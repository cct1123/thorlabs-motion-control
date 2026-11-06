"""Single-operation GUI owner. Web callbacks never perform USB I/O."""

import threading
import time
from collections import deque
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime

from .controller import K10CR2, MotionError
from .settings import SETTINGS, encode_changes, finite, resolve_target

MOTION_COMMANDS = {"home", "move"}
MANUAL_COMMANDS = {"jog-forward", "jog-reverse", "drive-forward", "drive-reverse"}
MOTION_COMMANDS |= MANUAL_COMMANDS


class GuiController:
    def __init__(self, factory=K10CR2):
        self._factory = factory
        self._condition = threading.Condition()
        self._mount = None
        self._cancel = threading.Event()
        self._closing = False
        self._hold_token = None
        self._hold_until = 0
        self._released_tokens = deque(maxlen=64)
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
            settings={},
            settings_revision=0,
            settings_updated=None,
            ui=dict(
                rotation_mode="fixed",
                rotation_direction="quickest",
                units="deg",
                decimals=6,
                high_power=False,
                drives=[2.5, 5, 7.5, 10],
            ),
        )

    def set_display(self, values):
        with self._condition:
            if (
                self._state["busy"] not in {None, "refresh"}
                or self._closing
                or self._state["status"].get("moving")
                or self._state["status"].get("homing")
            ):
                return "Wait for the active operation before changing display/control options."
            candidate = {**self._state["ui"], **values}
            resolve_target(0, 0, candidate["rotation_mode"], candidate["rotation_direction"])
            if candidate["units"] not in {"deg", "rad", "mrad", "urad", "counts"}:
                raise ValueError("Unknown display units")
            if isinstance(candidate["decimals"], bool) or candidate["decimals"] not in range(10):
                raise ValueError("Decimal places must be an integer from 0 to 9")
            candidate["decimals"] = int(candidate["decimals"])
            if not isinstance(candidate["high_power"], bool) or len(candidate["drives"]) != 4:
                raise ValueError("Invalid drive options")
            for speed in candidate["drives"]:
                finite(speed, "Drive velocity / °/s", 0.005, 20 if candidate["high_power"] else 10)
            self._state["ui"] = candidate
            self._event("Display, rotation and drive options applied for this server session.")
            return "Display, rotation and drive options applied (session only)."

    def hold(self, action, token, command=None, index=0):
        """A short browser lease, tied to one token; releases cannot cancel another tab."""
        if not isinstance(token, str) or not 1 <= len(token) <= 80:
            raise ValueError("Invalid hold token")
        with self._condition:
            if action == "release":
                self._released_tokens.append(token)
                if token == self._hold_token:
                    self._hold_until = 0
                return "Control released."
            if action == "heartbeat":
                if (
                    token != self._hold_token
                    or token in self._released_tokens
                    or time.monotonic() >= self._hold_until
                    or self._state["busy"] not in MANUAL_COMMANDS
                ):
                    return "Control is inactive."
                self._hold_until = time.monotonic() + 0.8
                return "Control renewed."
            if (
                action != "start"
                or command not in MANUAL_COMMANDS
                or token in self._released_tokens
            ):
                return "Control request rejected."
            if self._state["busy"] or self._closing:
                return "Another operation owns the mount; request was not queued."
            if not isinstance(index, int) or isinstance(index, bool) or index not in range(4):
                raise ValueError("Invalid drive division")
            self._hold_token = token
            self._hold_until = time.monotonic() + 0.8
            return self.submit(command, target=self._state["ui"]["drives"][index])

    def _held(self):
        with self._condition:
            return time.monotonic() < self._hold_until

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

    def submit(self, command, *, serial=None, target=None, changes=None, persist=False):
        with self._condition:
            if self._closing:
                return "The GUI is shutting down."
            busy = self._state["busy"]
            if command == "stop" and busy and busy not in {"connect", "disconnect", "stop"}:
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
                elif command not in {
                    "disconnect",
                    "refresh",
                    "stop",
                    "enable",
                    "disable",
                    "identify",
                    "load-settings",
                    "configure",
                    *MOTION_COMMANDS,
                }:
                    raise ValueError("Unknown command.")
                elif self._state["unknown"] and command not in {"stop", "disconnect"}:
                    raise ValueError(
                        "Physical state UNKNOWN; use Stop, then disconnect and reconnect."
                    )
                elif self._state["recovery"] and command not in {"disconnect", "stop"}:
                    raise ValueError("Disconnect and explicitly reconnect before continuing.")
                if command in MOTION_COMMANDS:
                    status = self._state["status"]
                    if status.get("moving") or status.get("homing"):
                        raise ValueError("Mount is already moving; use Stop.")
                    if command != "home":
                        if not status.get("homed"):
                            raise ValueError("Home the mount explicitly before moving.")
                        if command in MANUAL_COMMANDS:
                            if not status.get("enabled"):
                                raise ValueError(
                                    "Enable the motor explicitly before manual control."
                                )
                            if command.startswith("jog-") and "jog" not in self._state["settings"]:
                                raise ValueError("Load Jog settings before jogging.")
                        else:
                            resolve_target(
                                target,
                                self._state["position"]["degrees"],
                                self._state["ui"]["rotation_mode"],
                                self._state["ui"]["rotation_direction"],
                            )
                if command == "configure" or (command == "move" and changes):
                    if not changes:
                        raise ValueError("Select at least one settings group to apply.")
                    for group, draft in changes.items():
                        if group not in self._state["settings"]:
                            raise ValueError(
                                f"Load {group} settings from the device before editing."
                            )
                        encode_changes(
                            group,
                            draft,
                            self._state["settings"][group],
                            self._state["ui"]["high_power"],
                        )
            except (ValueError, TypeError) as exc:
                return str(exc)
            self._cancel.clear()
            self._state["busy"] = command
            if command != "refresh":
                self._state["message"] = f"{command} in progress…"
            thread = threading.Thread(
                target=self._run,
                args=(command, serial, target, changes, persist),
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
                settings={},
                settings_revision=self._state["settings_revision"] + 1,
                settings_updated=None,
            )

    def _run(self, command, serial, target, changes, persist):
        stop_handled = False
        try:
            if command == "connect":
                self._mount = self._factory(serial)
                self._mount.connect()
                identity = self._mount.info()
                self._readback(self._mount.status(), self._mount.position())
                settings = self._mount.query_settings(list(SETTINGS))
                with self._condition:
                    self._state.update(
                        connected=True,
                        identity=asdict(identity),
                        settings=settings,
                        settings_updated=time.time(),
                    )
                    self._state["settings_revision"] += 1
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
            elif command in {"enable", "disable"}:
                status = self._mount.set_enabled(command == "enable")
                self._readback(status, self._mount.position())
            elif command == "identify":
                self._mount.identify()
            elif command == "load-settings":
                self._readback(self._mount.status(), self._mount.position())
                settings = self._mount.query_settings(list(SETTINGS))
                with self._condition:
                    self._state["settings"] = settings
                    self._state["settings_updated"] = time.time()
                    self._state["settings_revision"] += 1
            elif command == "configure":
                actual = self._mount.configure(
                    changes, high_power=self._state["ui"]["high_power"], persist=persist
                )
                with self._condition:
                    self._state["settings"].update(actual)
                    self._state["settings_updated"] = time.time()
                    self._state["settings_revision"] += 1
            elif command in MANUAL_COMMANDS:
                result = self._mount.manual_motion(
                    1 if command.endswith("forward") else -1,
                    jog=command.startswith("jog-"),
                    speed=target,
                    mode=self._state["ui"]["rotation_mode"],
                    high_power=self._state["ui"]["high_power"],
                    cancel=self._cancel,
                    held=self._held,
                    on_progress=self._readback,
                )
                self._readback(result.status, result.position)
            else:
                if command == "move" and changes:
                    actual = self._mount.configure(
                        changes, high_power=self._state["ui"]["high_power"]
                    )
                    with self._condition:
                        self._state["settings"].update(actual)
                        self._state["settings_updated"] = time.time()
                        self._state["settings_revision"] += 1
                kwargs = dict(cancel=self._cancel, on_progress=self._readback)
                result = (
                    self._mount.home(**kwargs)
                    if command == "home"
                    else self._mount.move_absolute(
                        target,
                        mode=self._state["ui"]["rotation_mode"],
                        direction=self._state["ui"]["rotation_direction"],
                        **kwargs,
                    )
                )
                self._readback(result.status, result.position)
            with self._condition:
                if command != "refresh":
                    self._state.update(error="", message=f"{command} complete.")
                    if command == "identify":
                        self._state["message"] = (
                            "Identify command sent (LED has no acknowledgement)."
                        )
                    elif command == "configure" and persist:
                        self._state["message"] = (
                            "Settings readback verified; EEPROM save requested. "
                            "Power-cycle persistence is unverified."
                        )
                    self._event(self._state["message"])
        except Exception as exc:
            # MotionError means cleanup was already attempted; never replay it.
            stop_handled = isinstance(exc, MotionError)
            self._failed(command, exc)
        finally:
            with self._condition:
                # Stop acceptance and owner release share one lock. A Stop accepted
                # before this boundary must be serviced before another owner starts.
                pending_stop = (
                    command not in {"connect", "disconnect", "stop"}
                    and self._cancel.is_set()
                    and not stop_handled
                )
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
        if getattr(exc, "__notes__", None):
            message += " " + " ".join(exc.__notes__)
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
