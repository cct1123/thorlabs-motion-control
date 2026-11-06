"""Synchronous K10CR2 controller. One operation and receive path per instance."""

import math
import struct
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
    decode_identity,
    decode_position,
    decode_status,
    request,
)
from .settings import (
    ACCELERATION_SCALE,
    EDITABLE,
    MAX_ANGLE,
    SETTINGS,
    VELOCITY_SCALE,
    boolean,
    data_packet,
    encode_changes,
    finite,
    position_counts,
    resolve_target,
    short_packet,
)
from .transport import WinUSBTransport, remaining

_owners_lock = threading.Lock()
_owners: dict[str, "K10CR2"] = {}


def validate_timeout(value: float) -> float:
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
        self.query_timeout = validate_timeout(query_timeout)
        self.motion_timeout = validate_timeout(motion_timeout)
        self.cleanup_timeout = validate_timeout(cleanup_timeout)
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
        return time.monotonic() + validate_timeout(default if timeout is None else timeout)

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

    def _query(self, name, deadline, decode):
        deadline = min(deadline, time.monotonic() + self.query_timeout)
        try:
            self._transport.write(request(name), deadline)
            return decode(self._wait_for(COMMANDS[name].response_id, deadline))
        except BaseException:
            self._needs_recovery = True
            raise

    def _info(self, deadline):
        return self._query("info", deadline, decode_identity)

    def _status(self, deadline):
        return self._query("status", deadline, decode_status)

    def _position(self, deadline):
        return self._query("position", deadline, decode_position)

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

    def _setting(self, group, deadline):
        spec = SETTINGS[group]
        deadline = min(deadline, time.monotonic() + self.query_timeout)
        try:
            self._transport.write(short_packet(spec.request_id), deadline)
            frame = self._wait_for(spec.response_id, deadline)
            return dict(zip(spec.fields, struct.unpack(spec.format, frame.payload), strict=True))
        except BaseException:
            self._needs_recovery = True
            raise

    def _write_setting(self, group, values, deadline):
        spec = SETTINGS[group]
        try:
            payload = struct.pack(spec.format, *(values[field] for field in spec.fields))
            self._transport.write(data_packet(spec.set_id, payload), deadline)
            actual = self._setting(group, deadline)
            if actual != values:
                raise RuntimeError(f"{group} readback does not match requested settings")
            return actual
        except BaseException:
            self._needs_recovery = True
            raise

    def query_settings(self, groups=None):
        groups = list(SETTINGS) if groups is None else list(groups)
        if not groups or any(group not in SETTINGS for group in groups):
            raise ValueError("Unknown settings group")
        deadline = self._deadline(self.query_timeout * len(groups), self.query_timeout)
        with self._operation():
            return {group: self._setting(group, deadline) for group in groups}

    def configure(self, changes, *, high_power=False, persist=False):
        """Read, validate the whole draft, apply changed groups, and verify readback.

        EEPROM has no acknowledgement: persistence reports a request, not proof.
        Stage/axis, power, trigger, bow index and factory reset remain unavailable.
        """
        boolean(high_power, "High-power USB confirmation")
        boolean(persist, "EEPROM persistence")
        if not changes or any(group not in EDITABLE for group in changes):
            raise ValueError("Unknown, locked or unverified settings group")
        deadline = self._deadline(self.query_timeout * (3 * len(changes) + 1), self.query_timeout)
        with self._operation():
            state = self._status(deadline)
            self._check_motion_status(state)
            if state.busy:
                raise RuntimeError("Stop motion before changing settings")
            before = {group: self._setting(group, deadline) for group in changes}
            # No setting writes until EVERY group has passed validation.
            prepared = {
                group: encode_changes(group, draft, before[group], high_power)
                for group, draft in changes.items()
            }
            result = {}
            for group, values in prepared.items():
                result[group] = (
                    self._write_setting(group, values, deadline)
                    if values != before[group]
                    else before[group]
                )
            if persist:
                try:
                    for group in prepared:
                        self._transport.write(
                            data_packet(0x04B9, struct.pack("<HH", 1, SETTINGS[group].set_id)),
                            deadline,
                        )
                except BaseException:
                    self._needs_recovery = True
                    raise
            return result

    def set_enabled(self, enabled):
        if not isinstance(enabled, bool):
            raise ValueError("Enabled must be True or False")
        deadline = self._deadline(None, self.query_timeout)
        with self._operation():
            state = self._status(deadline)
            if state.busy:
                raise RuntimeError("Stop motion before enabling or disabling the motor")
            try:
                self._transport.write(short_packet(0x0210, 1, 1 if enabled else 2), deadline)
                state = self._status(deadline)
                if state.enabled != enabled:
                    raise RuntimeError("Motor enable readback did not match the request")
                return state
            except BaseException:
                self._needs_recovery = True
                raise

    def identify(self):
        deadline = self._deadline(None, self.query_timeout)
        with self._operation():
            try:
                self._transport.write(short_packet(0x0223, 0, 0), deadline)
            except BaseException:
                self._needs_recovery = True
                raise

    def manual_motion(
        self,
        direction,
        *,
        jog=False,
        speed=None,
        mode="fixed",
        high_power=False,
        cancel=None,
        held=None,
        on_progress=None,
        timeout=None,
    ):
        """Held drive or jog. Continuous motion uses a finite boundary target.

        A released or expired browser lease stops through this owner. A single jog
        finishes once, and repeats only while held. Temporary velocity changes are
        verified and restored. No receive path competes with this operation.
        """
        boolean(jog, "Jog selection")
        boolean(high_power, "High-power USB confirmation")
        if (
            not isinstance(direction, int)
            or isinstance(direction, bool)
            or direction not in {-1, 1}
        ):
            raise ValueError("Direction must be -1 or +1")
        if mode not in {"fixed", "total", "equivalent"}:
            raise ValueError("Invalid rotation mode")
        if not jog:
            finite(speed, "Drive velocity / °/s", 0.005, 20 if high_power else 10)
        deadline = self._deadline(timeout, self.motion_timeout)
        with self._operation():
            state = self._status(deadline)
            self._check_motion_status(state)
            if state.busy or not state.homed or not state.enabled:
                raise RuntimeError("Manual control requires an idle, enabled, homed mount")
            profile = self._setting("jog", deadline) if jog else None
            if profile is not None:
                if (
                    profile["mode"] not in {1, 2}
                    or profile["stop"] not in {1, 2}
                    or profile["step"] <= 0
                    or profile["maximum"] <= 0
                    or profile["acceleration"] <= 0
                ):
                    raise ValueError("Unrecognized or invalid jog settings readback")
                if profile["maximum"] > (20 if high_power else 10) * VELOCITY_SCALE:
                    raise ValueError("Jog speed exceeds the selected USB supply rating")
                if profile["acceleration"] > 20 * ACCELERATION_SCALE:
                    raise ValueError("Jog acceleration exceeds the manual's 20 degree/s² limit")
            continuous = not jog or profile["mode"] == 1
            stop_mode = profile["stop"] if jog else 2
            old_velocity = self._setting("velocity", deadline) if continuous else None
            lower, upper = (0, 360) if mode == "fixed" else (-MAX_ANGLE, MAX_ANGLE)
            restore = False
            try:
                if continuous:
                    maximum = profile["maximum"] if jog else round(speed * VELOCITY_SCALE)
                    finite(
                        maximum / VELOCITY_SCALE, "Velocity / °/s", 0.005, 20 if high_power else 10
                    )
                    acceleration = profile["acceleration"] if jog else old_velocity["acceleration"]
                    finite(acceleration / ACCELERATION_SCALE, "Acceleration / °/s²", 0.001, 20)
                    if cancel is not None and cancel.is_set():
                        raise MotionCancelled("Cancelled before manual motion")
                    velocity = {
                        **old_velocity,
                        "minimum": 0,
                        "maximum": maximum,
                        "acceleration": acceleration,
                    }
                    restore = True
                    self._write_setting("velocity", velocity, deadline)
                while True:
                    before = self._position(deadline)
                    if not position_counts(lower) <= before.counts <= position_counts(upper):
                        raise ValueError(
                            "Current counter is outside the selected range; home first"
                        )
                    target = (
                        position_counts(upper if direction > 0 else lower)
                        if continuous
                        else before.counts + direction * profile["step"]
                    )
                    if not position_counts(lower) <= target <= position_counts(upper):
                        raise ValueError("Jog would exceed the selected rotation range")
                    if before.counts == target:
                        return MotionResult(state, before, False, no_op=True)
                    if cancel is not None and cancel.is_set():
                        raise MotionCancelled("Cancelled before manual motion")
                    if continuous and held is not None and not held():
                        return MotionResult(state, before, False, no_op=True)
                    self._events.clear()
                    packet = (
                        request("move", counts=target)
                        if continuous
                        else short_packet(0x046A, 1, 1 if direction > 0 else 2)
                    )
                    try:
                        self._transport.write(packet, deadline)
                        while True:
                            if cancel is not None and cancel.is_set():
                                raise MotionCancelled("Stop requested")
                            if continuous and held is not None and not held():
                                return self._stop(
                                    deadline=self._deadline(None, self.cleanup_timeout),
                                    mode=stop_mode,
                                )
                            state = self._status(deadline)
                            self._check_motion_status(state)
                            if not state.homed or not state.enabled:
                                raise RuntimeError(
                                    "Manual motion lost home reference or motor enable"
                                )
                            position = self._position(deadline)
                            if on_progress is not None:
                                on_progress(state, position)
                            if cancel is not None and cancel.is_set():
                                raise MotionCancelled("Stop requested")
                            if not state.busy and position.counts == target:
                                if continuous or held is None or not held():
                                    return MotionResult(state, position, 0x0464 in self._events)
                                break
                            self._pause(deadline)
                    except BaseException as original:
                        self._needs_recovery = True
                        try:
                            cleanup = CleanupResult(
                                True, stop=self._stop(self._deadline(None, self.cleanup_timeout))
                            )
                        except BaseException as exc:
                            cleanup = CleanupResult(False, error=f"{type(exc).__name__}: {exc}")
                        failure = MotionError(original, cleanup)
                        if restore:
                            failure.add_note(
                                "Temporary manual-motion profile was not restored; "
                                "reload and inspect velocity settings after recovery."
                            )
                        raise failure from original
            finally:
                if restore and not self._needs_recovery:
                    self._write_setting(
                        "velocity",
                        old_velocity,
                        self._deadline(self.query_timeout * 2, self.query_timeout),
                    )

    def _pause(self, deadline):
        time.sleep(min(0.05, remaining(deadline)))
        remaining(deadline)

    def _check_motion_status(self, state):
        if state.motion_error or state.raw_bits & 0x40000000:
            raise RuntimeError(f"Controller reports a motion/error flag: {state.raw_bits:#010x}")

    def _stop(self, deadline, mode=2) -> StopResult:
        self._events.discard(0x0466)
        self._transport.write(short_packet(0x0465, 1, mode), deadline)
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
            if name == "move" and abs(before.degrees) > MAX_ANGLE:
                raise RuntimeError("Counted angle exceeds the manual's 43-turn bound; home first")
            if name == "move":
                angle, mode, direction = target
                if mode == "fixed" and not 0 <= before.degrees <= 360:
                    raise ValueError(
                        "Current counter is outside Fixed Range; home or use Total Angle"
                    )
                target = resolve_target(angle, before.degrees, mode, direction)
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
                            if cancel is not None and cancel.is_set():
                                raise MotionCancelled("Stop requested")
                            remaining(deadline)
                            return MotionResult(state, position, completion in self._events)
                    self._pause(deadline)
            except BaseException as original:
                self._needs_recovery = True
                try:
                    stopped = self._stop(self._deadline(None, self.cleanup_timeout))
                    cleanup = CleanupResult(True, stop=stopped)
                except BaseException as stop_error:
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
        mode: str = "fixed",
        direction: str = "quickest",
        timeout: float | None = None,
        cancel: threading.Event | None = None,
        on_progress: Callable[[Status, Position], None] | None = None,
    ) -> MotionResult:
        """Move in fixed [0, 360], total or equivalent-angle mode, in degrees.

        Equivalent routes use a fresh counted position while owning USB. Targets
        round to the nearest count (ties to even). Direction applies only to
        equivalent-angle routing. No motion is replayed automatically.
        """
        resolve_target(angle, 0, mode, direction)  # Validate before any I/O.
        return self._motion("move", (angle, mode, direction), timeout, cancel, on_progress)
