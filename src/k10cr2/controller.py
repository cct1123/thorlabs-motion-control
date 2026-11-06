"""Synchronous K10CR2 controller. One operation and receive path per instance."""

import math
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass

from .protocol import (
    COMMANDS,
    FrameBuffer,
    Identity,
    Position,
    Status,
    angle_to_counts,
    decode_identity,
    decode_position,
    decode_status,
    request,
)
from .transport import WinUSBTransport, remaining

_owners_lock = threading.Lock()
_owners: dict[str, "K10CR2"] = {}


def _valid_timeout(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Timeout must be finite and positive")
    if not math.isfinite(value) or value <= 0:
        raise ValueError("Timeout must be finite and positive")
    return float(value)


@dataclass(frozen=True)
class MotionResult:
    status: Status
    position: Position
    completion_received: bool
    no_op: bool = False


@dataclass(frozen=True)
class StopResult:
    status: Status
    position: Position
    acknowledged: bool = True


@dataclass(frozen=True)
class CleanupResult:
    confirmed_stopped: bool
    stop: StopResult | None = None
    error: str | None = None


class MotionError(RuntimeError):
    """Original motion failure and the separate stop-cleanup outcome."""

    def __init__(self, original: BaseException, cleanup: CleanupResult):
        self.original, self.cleanup = original, cleanup
        outcome = (
            "stop confirmed by controller"
            if cleanup.confirmed_stopped
            else (f"stop unconfirmed; physical state UNKNOWN ({cleanup.error})")
        )
        super().__init__(
            f"{type(original).__name__}: {original}; {outcome}. "
            "Close and explicitly reconnect before another motion."
        )


class MotionCancelled(RuntimeError):
    """The motion owner received a cooperative stop request."""


class K10CR2:
    def __init__(
        self,
        serial: str,
        *,
        query_timeout: float = 3,
        motion_timeout: float = 120,
        cleanup_timeout: float = 5,
    ):
        if (
            not isinstance(serial, str)
            or len(serial) != 8
            or not serial.isascii()
            or not serial.isdigit()
        ):
            raise ValueError("Serial must be an explicit eight-digit string")
        self.serial = serial
        self.query_timeout = _valid_timeout(query_timeout)
        self.motion_timeout = _valid_timeout(motion_timeout)
        self.cleanup_timeout = _valid_timeout(cleanup_timeout)
        self._transport = None
        self._buffer = FrameBuffer()
        self._lock = threading.Lock()
        self._events = set()
        self._needs_recovery = False

    @property
    def recovery_required(self) -> bool:
        return self._needs_recovery

    @contextmanager
    def _operation(self, *, connected=True, allow_recovery=False):
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("Another operation owns this mount; use its Ctrl+C/stop cleanup")
        try:
            if connected and self._transport is None:
                raise RuntimeError("Mount is not connected")
            if self._needs_recovery and not allow_recovery:
                raise RuntimeError("Connection state is uncertain; close and explicitly reconnect")
            yield
        finally:
            self._lock.release()

    def _deadline(self, timeout, default) -> float:
        return time.monotonic() + _valid_timeout(default if timeout is None else timeout)

    def connect(self, *, timeout: float | None = None):
        deadline = self._deadline(timeout, self.query_timeout)
        with self._operation(connected=False, allow_recovery=True):
            if self._transport is not None:
                raise RuntimeError("Mount is already connected")
            with _owners_lock:
                if self.serial in _owners:
                    raise RuntimeError("Another controller in this process owns this USB serial")
                _owners[self.serial] = self
            self._buffer = FrameBuffer()
            self._events.clear()
            self._needs_recovery = False
            try:
                self._transport = WinUSBTransport.open(self.serial, deadline)
                identity = self._info(deadline)
                if identity.model not in ("K10CR2", "K10CR2/M") or identity.serial != self.serial:
                    raise RuntimeError(f"Wrong device: {identity.model}, serial {identity.serial}")
                if identity.channels != 1:
                    raise RuntimeError("Expected a single-channel K10CR2")
            except BaseException as exc:
                try:
                    self._close()
                except Exception as close_error:
                    exc.add_note(f"USB cleanup also failed: {close_error}")
                raise
        return self

    def _close(self):
        transport, self._transport = self._transport, None
        try:
            if transport is not None:
                transport.close()
        finally:
            with _owners_lock:
                if _owners.get(self.serial) is self:
                    del _owners[self.serial]

    def close(self) -> None:
        with self._operation(connected=False, allow_recovery=True):
            self._close()

    def __enter__(self):
        return self.connect()

    def __exit__(self, exc_type, exc, tb):
        try:
            self.close()
        except Exception as close_error:
            if exc is None:
                raise
            exc.add_note(f"USB close also failed: {close_error}")

    def _wait_for(self, expected: int, deadline: float):
        while True:
            remaining(deadline)
            frame = self._buffer.pop()
            if frame is None:
                self._buffer.feed(self._transport.read(deadline))
                continue
            if frame.message_id in (0x0444, 0x0464, 0x0466):
                self._events.add(frame.message_id)
            if frame.message_id == expected:
                remaining(deadline)
                return frame

    def _query(self, name, deadline):
        deadline = min(deadline, time.monotonic() + self.query_timeout)
        try:
            self._transport.write(request(name), deadline)
            return self._wait_for(COMMANDS[name].response_id, deadline)
        except BaseException:
            self._needs_recovery = True
            raise

    def _info(self, deadline):
        return decode_identity(self._query("info", deadline))

    def _status(self, deadline):
        return decode_status(self._query("status", deadline))

    def _position(self, deadline):
        return decode_position(self._query("position", deadline))

    def info(self, *, timeout: float | None = None) -> Identity:
        deadline = self._deadline(timeout, self.query_timeout)
        with self._operation():
            return self._info(deadline)

    def status(self, *, timeout: float | None = None) -> Status:
        deadline = self._deadline(timeout, self.query_timeout)
        with self._operation():
            return self._status(deadline)

    def position(self, *, timeout: float | None = None) -> Position:
        deadline = self._deadline(timeout, self.query_timeout)
        with self._operation():
            return self._position(deadline)

    def _pause(self, deadline):
        time.sleep(min(0.05, remaining(deadline)))
        remaining(deadline)

    def _check_motion_status(self, state):
        if state.motion_error or state.raw_bits & 0x40000000:
            raise RuntimeError(f"Controller reports a motion/error flag: {state.raw_bits:#010x}")

    def _stop(self, deadline) -> StopResult:
        self._events.discard(0x0466)
        self._transport.write(request("stop"), deadline)
        self._wait_for(0x0466, deadline)
        while True:
            state = self._status(deadline)
            if not state.busy:
                position = self._position(deadline)
                remaining(deadline)
                return StopResult(state, position)
            self._pause(deadline)

    def stop(self, *, timeout: float | None = None) -> StopResult:
        """Profiled stop, acknowledged by the controller and checked with fresh status.

        During blocking home/move, Ctrl+C triggers stop on that active owner.
        A second thread/process must not compete for the same USB receive path.
        """
        deadline = self._deadline(timeout, self.cleanup_timeout)
        with self._operation(allow_recovery=True):
            try:
                return self._stop(deadline)
            except BaseException:
                self._needs_recovery = True
                raise

    def _motion(self, name, target, timeout, cancel=None, on_progress=None) -> MotionResult:
        deadline = self._deadline(timeout, self.motion_timeout)
        with self._operation():
            state = self._status(deadline)
            self._check_motion_status(state)
            if state.busy:
                raise RuntimeError("Mount is already moving or homing")
            if name == "move" and not state.homed:
                raise RuntimeError("Home the mount explicitly before an absolute move")
            before = self._position(deadline)
            if cancel is not None and cancel.is_set():
                raise MotionCancelled("Cancelled before motion; no motion command sent")
            if name == "move" and before.counts == target:
                return MotionResult(state, before, False, no_op=True)
            # Consume events seen in preflight but never treat them as new motion evidence.
            self._events.clear()
            if not state.enabled:
                try:
                    self._transport.write(request("enable"), deadline)
                    state = self._status(deadline)
                    self._check_motion_status(state)
                    if not state.enabled or state.busy:
                        raise RuntimeError("Channel did not become enabled and idle")
                except BaseException:
                    self._needs_recovery = True
                    raise
            try:
                if cancel is not None and cancel.is_set():
                    raise MotionCancelled("Stop requested")
                # A partial/ambiguous write may already start motion; stop on ANY failure here.
                self._transport.write(
                    request(name, counts=target) if name == "move" else request(name), deadline
                )
                transitioned = False
                completion = COMMANDS[name].response_id
                expected = 0 if name == "home" else target
                while True:
                    if cancel is not None and cancel.is_set():
                        raise MotionCancelled("Stop requested")
                    state = self._status(deadline)
                    self._check_motion_status(state)
                    position = self._position(deadline) if on_progress is not None else None
                    if on_progress is not None:
                        on_progress(state, position)
                    transitioned |= state.busy or (name == "home" and not state.homed)
                    if not state.busy and state.homed:
                        if position is None:
                            position = self._position(deadline)
                        # For a nonzero move, a fresh changed counter reaching target also
                        # proves progress even if a short move escaped the busy polls.
                        progressed = transitioned or (
                            name == "move" and before.counts != position.counts
                        )
                        if progressed and position.counts == expected:
                            remaining(deadline)
                            return MotionResult(state, position, completion in self._events)
                    self._pause(deadline)
            except (Exception, KeyboardInterrupt) as original:
                self._needs_recovery = True
                try:
                    stopped = self._stop(self._deadline(None, self.cleanup_timeout))
                    cleanup = CleanupResult(True, stop=stopped)
                except (Exception, KeyboardInterrupt) as stop_error:
                    cleanup = CleanupResult(
                        False, error=f"{type(stop_error).__name__}: {stop_error}"
                    )
                raise MotionError(original, cleanup) from original

    def home(
        self,
        *,
        timeout: float | None = None,
        cancel: threading.Event | None = None,
        on_progress: Callable[[Status, Position], None] | None = None,
    ) -> MotionResult:
        """Home once using the existing controller parameters; never auto-replay."""
        return self._motion("home", None, timeout, cancel, on_progress)

    def move_absolute(
        self,
        angle: float,
        *,
        timeout: float | None = None,
        cancel: threading.Event | None = None,
        on_progress: Callable[[Status, Position], None] | None = None,
    ) -> MotionResult:
        """Move to [0, 360) degrees, rounded to nearest count (ties to even)."""
        target = angle_to_counts(angle)  # Validate before preflight or any other I/O.
        return self._motion("move", target, timeout, cancel, on_progress)
