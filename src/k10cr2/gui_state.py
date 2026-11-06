"""Single-operation GUI owner. Web callbacks never perform USB I/O."""

import math
import threading
import time
from collections import deque
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime

from .controller import K10CR2, MotionError
from .protocol import angle_to_counts

MOTIONS = {"home", "move-to", "step-minus", "step-plus"}


class GuiController:
    def __init__(self, factory=K10CR2):
        self._factory = factory
        self._condition = threading.Condition()
        self._mount = None
        self._cancel = threading.Event()
        self._closing = False
        self._events = deque(maxlen=100)
        self._state = dict(
            connected=False,
            busy=None,
            recovery=False,
            identity={},
            status={},
            position=None,
            updated=None,
            error="",
            unknown=False,
            message="Connect a mount to begin.",
        )

    def snapshot(self):
        with self._condition:
            return deepcopy({**self._state, "closing": self._closing, "events": list(self._events)})

    def _event(self, message, level="info"):
        self._events.appendleft(
            dict(
                time=datetime.now().astimezone().isoformat(timespec="seconds"),
                message=message,
                level=level,
            )
        )

    def _readback(self, status, position):
        with self._condition:
            self._state.update(
                status=asdict(status), position=asdict(position), updated=time.time(), unknown=False
            )

    def submit(self, command, *, serial=None, target=None, step=None):
        with self._condition:
            if self._closing:
                return "The GUI is shutting down."
            busy = self._state["busy"]
            if command == "stop" and busy in MOTIONS:
                self._cancel.set()
                self._state["message"] = "Stop requested; waiting for controller confirmation…"
                self._event("Stop requested")
                return self._state["message"]
            if busy:
                return f"{busy} is in progress; request was not queued."
            try:
                if command == "connect":
                    if self._mount is not None:
                        raise ValueError("Disconnect before connecting again.")
                    # Validate without constructing a controller or accessing USB.
                    if (
                        not isinstance(serial, str)
                        or len(serial) != 8
                        or not serial.isascii()
                        or not serial.isdigit()
                    ):
                        raise ValueError("Enter the mount's eight-digit serial.")
                elif self._mount is None:
                    raise ValueError("Connect a mount first.")
                elif command not in {"disconnect", "refresh", "stop", *MOTIONS}:
                    raise ValueError("Unknown command.")
                elif self._state["recovery"] and command not in {"disconnect", "stop"}:
                    raise ValueError("Disconnect and explicitly reconnect before continuing.")
                if command in MOTIONS:
                    status = self._state["status"]
                    if status.get("moving") or status.get("homing"):
                        raise ValueError("Mount is already moving; use Stop.")
                    if command != "home":
                        if not status.get("homed"):
                            raise ValueError("Home the mount explicitly before moving.")
                        if command.startswith("step-"):
                            if (
                                isinstance(step, bool)
                                or not isinstance(step, (int, float))
                                or not math.isfinite(step)
                                or step <= 0
                            ):
                                raise ValueError("Enter a finite, positive step.")
                            # This is a preview only; the worker re-reads position before stepping.
                            target = self._state["position"]["degrees"] + (
                                step if command == "step-plus" else -step
                            )
                        angle_to_counts(target)
            except (ValueError, TypeError) as exc:
                return str(exc)
            self._cancel.clear()
            self._state["busy"] = command
            if command != "refresh":
                self._state["message"] = f"{command} in progress…"
            thread = threading.Thread(
                target=self._run,
                args=(command, serial, target, step),
                name="k10cr2-gui-owner",
                daemon=False,
            )
            try:
                thread.start()
            except Exception:
                self._state["busy"] = None
                raise
            return f"{command} requested."

    def _disconnect(self):
        if self._mount is not None:
            self._mount.close()
        self._mount = None
        with self._condition:
            self._state.update(
                connected=False,
                recovery=False,
                identity={},
                status={},
                position=None,
                updated=None,
                unknown=False,
            )

    def _run(self, command, serial, target, step):
        stop_handled = False
        try:
            if command == "connect":
                self._mount = self._factory(serial)
                self._mount.connect()
                identity = self._mount.info()
                self._readback(self._mount.status(), self._mount.position())
                with self._condition:
                    self._state.update(connected=True, identity=asdict(identity))
            elif command == "disconnect":
                if self._state["unknown"]:
                    raise ValueError("Physical state UNKNOWN; use Stop before disconnecting.")
                status = self._mount.status() if not self._mount.recovery_required else None
                if status is not None and status.busy:
                    raise ValueError("Mount is moving; use Stop before disconnecting.")
                self._disconnect()
            elif command == "refresh":
                self._readback(self._mount.status(), self._mount.position())
            elif command == "stop":
                result = self._mount.stop()
                self._readback(result.status, result.position)
            else:
                if command.startswith("step-"):
                    position = self._mount.position()
                    target = position.degrees + (step if command == "step-plus" else -step)
                    angle_to_counts(target)
                kwargs = dict(cancel=self._cancel, on_progress=self._readback)
                result = (
                    self._mount.home(**kwargs)
                    if command == "home"
                    else self._mount.move_absolute(target, **kwargs)
                )
                self._readback(result.status, result.position)
            with self._condition:
                if command != "refresh":
                    self._state.update(error="", message=f"{command} complete.")
                    self._event(self._state["message"])
        except Exception as exc:
            # MotionError means cleanup was already attempted; never replay it.
            stop_handled = isinstance(exc, MotionError)
            self._failed(command, exc)
        finally:
            with self._condition:
                # Stop acceptance and owner release share one lock. A Stop accepted
                # before this boundary must be serviced before another owner starts.
                pending_stop = command in MOTIONS and self._cancel.is_set() and not stop_handled
                if not pending_stop:
                    self._finish_operation()
            if pending_stop:
                try:
                    result = self._mount.stop()
                    self._readback(result.status, result.position)
                    with self._condition:
                        self._state["message"] = "Stop confirmed by controller."
                        self._event(self._state["message"])
                except Exception as exc:
                    self._failed("stop", exc)
                finally:
                    with self._condition:
                        self._finish_operation()

    def _finish_operation(self):
        """Publish owner release while holding the condition lock."""
        self._state.update(
            busy=None,
            recovery=self._mount is not None and self._mount.recovery_required,
        )
        self._condition.notify_all()

    def _failed(self, command, exc):
        if isinstance(exc, MotionError) and exc.cleanup.stop is not None:
            self._readback(exc.cleanup.stop.status, exc.cleanup.stop.position)
        message = f"{type(exc).__name__}: {exc}"
        if command == "connect":
            try:
                self._disconnect()
            except Exception as close_error:
                message += f"; close failed: {close_error}"
                with self._condition:
                    self._state["connected"] = True  # Keep Disconnect/Stop accessible.
        with self._condition:
            if self._mount is not None and self._mount.recovery_required:
                self._state["unknown"] = not (
                    isinstance(exc, MotionError) and exc.cleanup.confirmed_stopped
                )
            if self._state["unknown"]:
                message += "; physical state UNKNOWN. Use Stop before disconnecting."
            self._state.update(error=message, message="Operation failed; inspect Activity.")
            self._event(message, "error")

    def refresh(self):
        with self._condition:
            if (
                self._closing
                or not self._state["connected"]
                or self._state["busy"]
                or self._state["recovery"]
            ):
                return
            updated = self._state["updated"]
            if updated is not None and time.time() - updated < 0.5:
                return
            # Reserve under the same lock so a simultaneous command cannot be queued.
            self.submit("refresh")

    def shutdown(self, timeout=15):
        """Cancel through the active owner, wait, then release USB. Never race close."""
        with self._condition:
            if self._closing:
                return self._mount is None and self._state["busy"] is None
            self._closing = True
            self._cancel.set()
            if not self._condition.wait_for(lambda: self._state["busy"] is None, timeout):
                self._closing = False
                self._state["error"] = "Shutdown timed out; USB owner is still active. Retry Stop."
                return False
            self._state["busy"] = "disconnect"
        try:
            # A mount may already have been moving when connected. An uncertain
            # connection also requires a stop attempt before the GUI releases it.
            if self._mount is not None:
                state = self._state
                # Front-panel movement can start after the last browser sample.
                fresh_status = self._mount.status() if not self._mount.recovery_required else None
                if (
                    state["unknown"]
                    or (fresh_status is not None and fresh_status.busy)
                    or (
                        fresh_status is None
                        and (state["status"].get("moving") or state["status"].get("homing"))
                    )
                ):
                    result = self._mount.stop()
                    self._readback(result.status, result.position)
            self._disconnect()
            with self._condition:
                self._state["message"] = "Web UI closed. You may close this tab."
            return True
        except Exception as exc:
            with self._condition:
                self._closing = False
                self._state["error"] = f"Shutdown failed; physical state may be UNKNOWN: {exc}"
                self._state["unknown"] = True
                self._state["recovery"] = self._mount is not None and self._mount.recovery_required
            return False
        finally:
            with self._condition:
                self._state["busy"] = None
                self._condition.notify_all()
