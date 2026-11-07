import threading
import time

import pytest

from k10cr2 import CleanupResult, MotionCancelled, MotionError, MotionResult, StopResult
from k10cr2.gui_state import GuiController
from k10cr2.protocol import Identity, Position, Status
from k10cr2.settings import position_counts


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
        return Position(position_counts(self.angle), self.angle)

    def query_settings(self, groups):
        self.record("settings")
        return {
            "velocity": dict(channel=1, minimum=0, acceleration=15020, maximum=73300775),
            "jog": dict(
                channel=1,
                mode=2,
                step=13653,
                minimum=0,
                acceleration=22530,
                maximum=109951163,
                stop=2,
            ),
            "backlash": dict(channel=1, distance=136533),
            "home": dict(channel=1, direction=2, switch=1, velocity=73300775, offset=546133),
            "buttons": dict(
                channel=1, mode=1, position1=136533, position2=682667, timeout1=500, timeout2=500
            ),
        }

    def stop(self):
        self.record("stop")
        if self.fail_stop:
            self.recovery_required = True
            raise OSError("USB gone")
        return StopResult(self.status(), self.position())

    def home(self, **kwargs):
        return self.move_absolute(0, **kwargs)

    def move_absolute(self, angle, *, mode="fixed", direction="quickest", cancel, on_progress):
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
    assert [name for name, _ in mount.calls] == [
        "connect",
        "info",
        "status",
        "position",
        "settings",
    ]
    assert set(owner.snapshot()["settings"]) == {"velocity", "jog", "backlash", "home", "buttons"}
    assert owner.snapshot()["settings_updated"] is not None
    assert "Home" in owner.submit("move", target=90)
    assert "Home" in owner.submit("jog-forward")
    assert len(mount.calls) == 5


def test_home_and_move_use_background_owner(gui_owner):
    owner, mount = gui_owner
    connect(owner)
    owner.submit("home")
    assert wait_idle(owner)["status"]["homed"]
    owner.submit("move", target=90)
    assert wait_idle(owner)["position"]["degrees"] == 90
    assert all(ident != threading.get_ident() for _, ident in mount.calls)


def test_session_options_do_not_race_or_block_on_background_readback(gui_owner):
    owner, mount = gui_owner
    connect(owner)
    entered, release = threading.Event(), threading.Event()
    original = mount.status

    def blocked_status():
        entered.set()
        assert release.wait(2)
        return original()

    mount.status = blocked_status
    owner.submit("refresh")
    try:
        assert entered.wait(1)
        before = list(mount.calls)
        assert "session only" in owner.set_display({"decimals": 3})
        assert owner.snapshot()["ui"]["decimals"] == 3
        assert mount.calls == before
    finally:
        release.set()
    wait_idle(owner)
    mount.homed = True
    owner.submit("home")
    wait_idle(owner)
    mount.started.clear()
    mount.release.clear()
    owner.submit("move", target=1)
    assert mount.started.wait(1)
    try:
        assert "Wait" in owner.set_display({"decimals": 6})
        assert owner.snapshot()["ui"]["decimals"] == 3
    finally:
        mount.release.set()


def test_failed_initial_settings_read_releases_connection_and_can_retry(gui_owner):
    owner, mount = gui_owner
    original = mount.query_settings

    def failed_read(groups):
        raise TimeoutError("No settings reply")

    mount.query_settings = failed_read
    owner.submit("connect", serial="55551174")
    snapshot = wait_idle(owner)
    assert not snapshot["connected"]
    assert snapshot["settings"] == {} and snapshot["settings_updated"] is None
    assert "No settings reply" in snapshot["error"]
    assert mount.calls[-1][0] == "close"
    mount.query_settings = original
    connect(owner)


@pytest.mark.parametrize("angle", [-1, 360.1, float("nan"), float("inf"), None, True])
def test_invalid_targets_never_reach_worker(gui_owner, angle):
    owner, mount = gui_owner
    mount.homed = True
    connect(owner)
    before = len(mount.calls)
    assert "angle" in owner.submit("move", target=angle).lower()
    assert len(mount.calls) == before


def test_removed_step_commands_are_rejected_without_io(gui_owner):
    owner, mount = gui_owner
    mount.homed = True
    connect(owner)
    before = list(mount.calls)
    for command in ("step-plus", "step-minus", "move-to"):
        assert "Unknown command" in owner.submit(command, target=1)
    assert mount.calls == before


def test_stop_uses_active_owner_and_requires_reconnect(gui_owner):
    owner, mount = gui_owner
    mount.homed = True
    connect(owner)
    mount.release.clear()
    owner.submit("move", target=90)
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
    owner.submit("move", target=90)
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
    owner.submit("move", target=90)
    assert wait_idle(owner)["message"] == "move complete."
    owner.submit("refresh")
    assert wait_idle(owner)["message"] == "move complete."
    mount.fail_query = True
    owner.submit("refresh")
    error = wait_idle(owner)["error"]
    assert "No status reply" in error
    owner.refresh()
    assert owner.snapshot()["error"] == error


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


def test_interrupting_shutdown_wait_preserves_the_active_owner(gui_owner, monkeypatch):
    owner, mount = gui_owner
    connect(owner)
    entered, release = threading.Event(), threading.Event()
    original = mount.status

    def blocked_status():
        entered.set()
        assert release.wait(2)
        return original()

    def interrupt_wait(*args):
        raise KeyboardInterrupt

    mount.status = blocked_status
    owner.submit("refresh")
    try:
        assert entered.wait(1)
        with monkeypatch.context() as patch:
            patch.setattr(owner._condition, "wait_for", interrupt_wait)
            assert not owner.shutdown()
        snapshot = owner.snapshot()
        assert snapshot["busy"] == "refresh" and not snapshot["closing"]
        assert "interrupted" in snapshot["error"]
        assert not any(name == "close" for name, _ in mount.calls)
    finally:
        release.set()
    wait_idle(owner)
    assert owner.shutdown()


def test_interrupting_shutdown_query_keeps_stop_and_retry_available(gui_owner):
    owner, mount = gui_owner
    connect(owner)
    original = mount.status

    def interrupted_status():
        mount.status = original
        raise KeyboardInterrupt

    mount.status = interrupted_status
    assert not owner.shutdown()
    snapshot = owner.snapshot()
    assert snapshot["connected"] and snapshot["unknown"] and not snapshot["closing"]
    assert "KeyboardInterrupt" in snapshot["error"]
    assert not any(name == "close" for name, _ in mount.calls)
    assert owner.shutdown()
    names = [name for name, _ in mount.calls]
    assert names.index("stop") < names.index("close")


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
    owner.submit("move", target=90)
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
