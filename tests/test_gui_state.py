import threading
import time

import pytest

from k10cr2 import CleanupResult, MotionCancelled, MotionError, MotionResult, StopResult
from k10cr2.gui_state import GuiController
from k10cr2.protocol import Identity, Position, Status, angle_to_counts


class FakeMount:
    """No USB: calls are recorded with their owner thread for concurrency assertions."""

    def __init__(self):
        self.calls = []
        self.recovery_required = False
        self.homed = False
        self.angle = 0
        self.started = threading.Event()
        self.release = threading.Event()
        self.release.set()
        self.fail_stop = False
        self.fail_query = False

    def record(self, name):
        self.calls.append((name, threading.get_ident()))

    def connect(self):
        self.record("connect")

    def close(self):
        self.record("close")

    def info(self):
        self.record("info")
        return Identity("K10CR2", "55551174", "2.0.1", 4, 0, 1)

    def status(self):
        self.record("status")
        if self.fail_query:
            self.recovery_required = True
            raise TimeoutError("No status reply")
        return Status(
            0x80000400 if self.homed else 0x80000000, False, False, self.homed, True, False
        )

    def position(self):
        self.record("position")
        return Position(angle_to_counts(self.angle), self.angle)

    def stop(self):
        self.record("stop")
        if self.fail_stop:
            self.recovery_required = True
            raise OSError("USB gone")
        return StopResult(self.status(), self.position())

    def home(self, **kwargs):
        return self.move_absolute(0, **kwargs)

    def move_to(self, angle, *, mode="fixed", direction="quickest", **kwargs):
        return self.move_absolute(angle, **kwargs)

    def move_absolute(self, angle, *, cancel, on_progress):
        self.record(f"move:{angle:g}")
        on_progress(self.status(), self.position())
        self.started.set()
        while not self.release.wait(0.01):
            if cancel.is_set():
                self.recovery_required = True
                try:
                    cleanup = CleanupResult(True, stop=self.stop())
                except OSError as exc:
                    cleanup = CleanupResult(False, error=str(exc))
                raise MotionError(MotionCancelled("Stop requested"), cleanup)
        self.homed = True
        self.angle = angle
        return MotionResult(self.status(), self.position(), True)


def wait_idle(controller):
    deadline = time.monotonic() + 3
    while controller.snapshot()["busy"] is not None:
        assert time.monotonic() < deadline, controller.snapshot()
        time.sleep(0.005)
    return controller.snapshot()


@pytest.fixture
def gui_owner():
    mount = FakeMount()

    def factory(serial):
        assert serial == "55551174"
        return mount

    owner = GuiController(factory)
    yield owner, mount
    mount.release.set()
    mount.fail_stop = False
    mount.fail_query = False
    wait_idle(owner)
    assert owner.shutdown()


def connect(owner):
    owner.submit("connect", serial="55551174")
    assert wait_idle(owner)["connected"]


def test_startup_and_connect_are_read_only(gui_owner):
    owner, mount = gui_owner
    owner.refresh()
    assert not mount.calls
    assert "eight-digit" in owner.submit("connect", serial="123")
    assert not mount.calls
    connect(owner)
    assert [name for name, _ in mount.calls] == ["connect", "info", "status", "position"]
    assert "Home" in owner.submit("move-to", target=90)
    assert "Home" in owner.submit("step-plus", step=1)
    assert len(mount.calls) == 4


def test_home_and_move_use_background_owner(gui_owner):
    owner, mount = gui_owner
    connect(owner)
    owner.submit("home")
    assert wait_idle(owner)["status"]["homed"]
    owner.submit("move-to", target=90)
    assert wait_idle(owner)["position"]["degrees"] == 90
    assert all(ident != threading.get_ident() for _, ident in mount.calls)


@pytest.mark.parametrize("angle", [-1, 360.1, float("nan"), float("inf"), None, True])
def test_invalid_targets_never_reach_worker(gui_owner, angle):
    owner, mount = gui_owner
    mount.homed = True
    connect(owner)
    before = len(mount.calls)
    assert "angle" in owner.submit("move-to", target=angle).lower()
    assert len(mount.calls) == before


def test_step_uses_fresh_position_and_does_not_wrap(gui_owner):
    owner, mount = gui_owner
    mount.homed = True
    connect(owner)
    mount.angle = 20  # Position changed after the last UI snapshot.
    owner.submit("step-plus", step=1)
    assert wait_idle(owner)["position"]["degrees"] == 21
    mount.angle = 359.5
    owner.submit("step-plus", step=1)
    assert "Angle" in wait_idle(owner)["error"]
    assert not any(name == "move:360.5" for name, _ in mount.calls)


def test_stop_uses_active_owner_and_requires_reconnect(gui_owner):
    owner, mount = gui_owner
    mount.homed = True
    connect(owner)
    mount.release.clear()
    owner.submit("move-to", target=90)
    assert mount.started.wait(1)
    assert "not queued" in owner.submit("home")
    assert "not queued" in owner.submit("disconnect")
    assert "Stop requested" in owner.submit("stop")
    snap = wait_idle(owner)
    assert snap["recovery"] and not snap["unknown"]
    assert "stop confirmed" in snap["error"]
    move_thread = next(ident for name, ident in mount.calls if name == "move:90")
    assert [ident for name, ident in mount.calls if name == "stop"] == [move_thread]
    before = len(mount.calls)
    owner.refresh()
    assert "reconnect" in owner.submit("home")
    assert len(mount.calls) == before


def test_failed_stop_reports_unknown_and_allows_explicit_retry(gui_owner):
    owner, mount = gui_owner
    mount.homed = True
    connect(owner)
    mount.fail_stop = True
    mount.release.clear()
    owner.submit("move-to", target=90)
    assert mount.started.wait(1)
    owner.submit("stop")
    snap = wait_idle(owner)
    assert snap["unknown"] and "UNKNOWN" in snap["error"]
    assert not owner.shutdown()
    assert not any(name == "close" for name, _ in mount.calls)
    assert not owner.snapshot()["closing"]
    mount.fail_stop = False
    owner.submit("stop")
    snap = wait_idle(owner)
    assert snap["recovery"] and not snap["unknown"]
    assert owner.shutdown()


def test_shutdown_cancels_before_releasing_usb_and_is_idempotent(gui_owner):
    owner, mount = gui_owner
    connect(owner)
    mount.release.clear()
    owner.submit("home")
    assert mount.started.wait(1)
    assert owner.shutdown()
    names = [name for name, _ in mount.calls]
    assert names.count("stop") == 1
    assert names.index("stop") < names.index("close")
    assert owner.shutdown()
    assert [name for name, _ in mount.calls].count("close") == 1
    assert "shutting down" in owner.submit("connect", serial="55551174")


def test_query_failure_stops_polling_until_reconnect(gui_owner):
    owner, mount = gui_owner
    connect(owner)
    mount.fail_query = True
    owner.submit("refresh")
    assert wait_idle(owner)["recovery"]
    before = len(mount.calls)
    owner.refresh()
    assert len(mount.calls) == before
    assert "reconnect" in owner.submit("home")


def test_background_refresh_preserves_command_result_and_error(gui_owner):
    owner, mount = gui_owner
    mount.homed = True
    connect(owner)
    owner.submit("move-to", target=90)
    assert wait_idle(owner)["message"] == "move-to complete."
    owner.submit("refresh")
    assert wait_idle(owner)["message"] == "move-to complete."
    mount.angle = 359.5
    owner.submit("step-plus", step=1)
    error = wait_idle(owner)["error"]
    assert "Angle" in error
    owner.submit("refresh")
    assert wait_idle(owner)["error"] == error


def test_shutdown_timeout_does_not_close_an_active_owner(gui_owner):
    owner, mount = gui_owner
    connect(owner)
    # Hold a read query, which is bounded by the real controller's USB deadline.
    entered, release = threading.Event(), threading.Event()
    original = mount.status

    def blocked_status():
        entered.set()
        assert release.wait(2)
        return original()

    mount.status = blocked_status
    owner.submit("refresh")
    assert entered.wait(1)
    try:
        assert not owner.shutdown(timeout=0.01)
        assert not any(name == "close" for name, _ in mount.calls)
    finally:
        release.set()
    wait_idle(owner)
    assert owner.shutdown()


@pytest.mark.parametrize("fail_stop", [False, True])
def test_stop_at_final_readback_is_not_lost(gui_owner, fail_stop):
    owner, mount = gui_owner
    mount.homed = True
    mount.fail_stop = fail_stop
    connect(owner)
    entered, release = threading.Event(), threading.Event()
    original = owner._readback

    def final_readback(status, position):
        if position.degrees == 90 and not entered.is_set():
            entered.set()
            assert release.wait(2)
        original(status, position)

    owner._readback = final_readback
    owner.submit("move-to", target=90)
    try:
        assert entered.wait(1)
        assert "Stop requested" in owner.submit("stop")
    finally:
        release.set()
    snap = wait_idle(owner)
    names = [name for name, _ in mount.calls]
    assert names.count("stop") == 1
    assert snap["unknown"] == fail_stop
    if fail_stop:
        assert "UNKNOWN" in snap["error"]
    else:
        assert "Stop confirmed" in snap["message"]


def test_disconnect_cannot_abandon_an_unconfirmed_stop(gui_owner):
    owner, mount = gui_owner
    connect(owner)
    mount.fail_stop = True
    owner.submit("stop")
    assert wait_idle(owner)["unknown"]
    owner.submit("disconnect")
    snap = wait_idle(owner)
    assert snap["connected"] and snap["unknown"]
    assert not any(name == "close" for name, _ in mount.calls)
    assert "Stop" in snap["error"]
    mount.fail_stop = False
    owner.submit("stop")
    assert not wait_idle(owner)["unknown"]
    owner.submit("disconnect")
    assert not wait_idle(owner)["connected"]


def test_shutdown_checks_fresh_status_before_releasing(gui_owner):
    owner, mount = gui_owner
    connect(owner)
    # Movement began after the last UI sample (e.g. a front-panel action).
    idle_status = mount.status
    moving = [True]

    def fresh_status():
        if moving[0]:
            mount.record("status:moving")
            return Status(0x80000010, True, False, False, True, False)
        return idle_status()

    stop = mount.stop

    def stop_and_settle():
        moving[0] = False
        return stop()

    mount.status, mount.stop = fresh_status, stop_and_settle
    assert owner.shutdown()
    names = [name for name, _ in mount.calls]
    assert names.index("status:moving") < names.index("stop") < names.index("close")


def test_stop_accepted_during_readback_is_serviced_by_that_owner(gui_owner):
    owner, mount = gui_owner
    connect(owner)
    entered, release = threading.Event(), threading.Event()
    original = mount.status

    def blocked():
        entered.set()
        assert release.wait(2)
        return original()

    mount.status = blocked
    owner.submit("refresh")
    try:
        assert entered.wait(1)
        assert "Stop requested" in owner.submit("stop")
        assert not any(name == "stop" for name, _ in mount.calls)
    finally:
        release.set()
    snapshot = wait_idle(owner)
    assert "Stop confirmed" in snapshot["message"]
    calls = mount.calls[-5:]
    assert len({ident for _, ident in calls}) == 1
    assert [name for name, _ in mount.calls].count("stop") == 1
