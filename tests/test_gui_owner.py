"""No-hardware regressions for the owner in the consolidated GUI module."""

import threading
import time

import pytest

pytest.importorskip("dash")

from k10cr2 import CleanupResult, MotionCancelled, MotionError, MotionResult, StopResult  # noqa: E402
from k10cr2.gui import DEVICE_FIELDS, GuiController, form_values  # noqa: E402
from k10cr2.protocol import Identity, Position, Status  # noqa: E402
from k10cr2.settings import encode_changes, position_counts  # noqa: E402


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

    def configure(self, changes, *, high_power=False, persist=False):
        self.record(f"configure:{high_power}:{persist}")
        current = self.query_settings(changes)
        return {
            group: encode_changes(group, draft, current[group], high_power)
            for group, draft in changes.items()
        }

    def home(self, **kwargs):
        return self.move_absolute(0, **kwargs)

    def move_absolute(self, angle, *, mode="fixed", direction="quickest", cancel, on_progress):
        self.record(f"move:{angle:g}")
        on_progress(self.status(), self.position())
        self.started.set()
        while not self.release.wait(0.01):
            if cancel.is_set():
                try:
                    cleanup = CleanupResult(True, stop=self.stop())
                except OSError as exc:
                    cleanup = CleanupResult(False, error=str(exc))
                self.recovery_required = not cleanup.confirmed_stopped
                raise MotionError(
                    MotionCancelled("Stop requested"),
                    cleanup,
                    recovery_required=self.recovery_required,
                )
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
    assert owner.snapshot()["ui"]["rotation_mode"] == "equivalent"
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
        assert "session only" in owner.apply_settings(
            {**form_values(owner.snapshot()), "decimals": 3},
            revision=owner.snapshot()["settings_revision"],
        )
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
        assert "Wait" in owner.apply_settings(
            {**form_values(owner.snapshot()), "decimals": 6},
            revision=owner.snapshot()["settings_revision"],
        )
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
def test_controller_rejects_invalid_targets_without_device_io(mount_factory, angle):
    mount, transport = mount_factory([])
    owner = GuiController()
    owner._mount = mount
    owner._state.update(connected=True, status={"homed": True, "enabled": True})
    before = list(transport.sent)
    assert owner.submit("move", target=angle) == "move requested."
    snapshot = wait_idle(owner)
    assert "angle" in snapshot["error"].lower()
    assert transport.sent == before and not snapshot["recovery"]


@pytest.mark.parametrize("moving", [False, True])
def test_controller_fresh_status_protects_motion_despite_stale_gui_flags(mount_factory, moving):
    from conftest import IDLE, MOVING, STATUS_REQUEST

    mount, transport = mount_factory([(STATUS_REQUEST, MOVING if moving else IDLE)])
    owner = GuiController()
    owner._mount = mount
    owner._state.update(connected=True, status={"homed": True, "enabled": True})
    assert owner.submit("move", target=90) == "move requested."
    snapshot = wait_idle(owner)
    assert ("already moving" if moving else "Home") in snapshot["error"]
    assert transport.sent[-1] == STATUS_REQUEST and not transport.steps


def test_removed_step_commands_are_rejected_without_io(gui_owner):
    owner, mount = gui_owner
    mount.homed = True
    connect(owner)
    before = list(mount.calls)
    for command in ("step-plus", "step-minus", "move-to"):
        assert "Unknown command" in owner.submit(command, target=1)
    assert mount.calls == before


def test_confirmed_stop_uses_active_owner_and_allows_next_move(gui_owner):
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
    assert not snap["recovery"] and not snap["unknown"] and not snap["error"]
    assert "Stop confirmed" in snap["message"]
    assert snap["events"][0]["level"] == "info"
    move_thread = next(ident for name, ident in mount.calls if name == "move:90")
    assert [ident for name, ident in mount.calls if name == "stop"] == [move_thread]
    mount.release.set()
    assert "requested" in owner.submit("move", target=1)
    assert wait_idle(owner)["position"]["degrees"] == 1
    names = [name for name, _ in mount.calls]
    assert names.count("connect") == 1 and "close" not in names


@pytest.mark.parametrize("fail_stop", [False, True])
def test_stop_before_motion_write_reports_the_actual_stop_outcome(gui_owner, fail_stop):
    owner, mount = gui_owner
    mount.homed = True
    mount.fail_stop = fail_stop
    connect(owner)
    entered, release = threading.Event(), threading.Event()

    def preflight(angle, *, cancel, **kwargs):
        mount.record("preflight")
        entered.set()
        assert release.wait(2)
        assert cancel.is_set()
        raise MotionCancelled("Cancelled before motion; no motion command sent")

    mount.move_absolute = preflight
    owner.submit("move", target=1)
    try:
        assert entered.wait(1)
        assert "Stop requested" in owner.submit("stop")
    finally:
        release.set()
    snapshot = wait_idle(owner)
    assert [name for name, _ in mount.calls].count("stop") == 1
    if fail_stop:
        assert snapshot["unknown"] and snapshot["recovery"] and "USB gone" in snapshot["error"]
    else:
        assert not snapshot["error"] and not snapshot["unknown"] and not snapshot["recovery"]
        assert snapshot["message"] == "Stop confirmed by controller."
        assert snapshot["events"][0]["level"] == "info"


@pytest.mark.parametrize("angle", [-90, 450])
def test_gui_accepts_fixed_target_from_outside_interval(gui_owner, angle):
    owner, mount = gui_owner
    owner._state["ui"]["rotation_mode"] = "fixed"
    mount.homed = True
    mount.angle = angle
    connect(owner)
    assert "requested" in owner.submit("move", target=90)
    assert wait_idle(owner)["position"]["degrees"] == 90


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


def test_one_apply_uses_candidate_supply_rating_then_publishes_session(gui_owner):
    owner, mount = gui_owner
    connect(owner)
    entered, release = threading.Event(), threading.Event()
    configure = mount.configure

    def blocked_configure(changes, **kwargs):
        entered.set()
        assert release.wait(2)
        return configure(changes, **kwargs)

    mount.configure = blocked_configure
    before = owner.snapshot()
    fields = {**form_values(before), "high_power": ["yes"], "velocity": 15, "units": "rad"}
    assert "requested" in owner.apply_settings(
        fields, revision=before["settings_revision"], persist=True
    )
    try:
        assert entered.wait(1)
        assert owner.snapshot()["ui"] == before["ui"]
    finally:
        release.set()
    after = wait_idle(owner)
    assert not after["error"] and after["ui"]["high_power"] and after["ui"]["units"] == "rad"
    assert after["settings"]["velocity"]["maximum"] == 109_936_635
    assert after["settings_revision"] == before["settings_revision"] + 1
    assert "configure:True:True" in [name for name, _ in mount.calls]
    assert "EEPROM save requested" in after["message"]


def test_failed_device_apply_keeps_old_session_and_reports_partial_application(gui_owner):
    owner, mount = gui_owner
    connect(owner)
    before = owner.snapshot()

    def partial_failure(*args, **kwargs):
        mount.recovery_required = True
        raise OSError("Readback failed after write")

    mount.configure = partial_failure
    fields = {**form_values(before), "backlash": 2, "units": "rad"}
    owner.apply_settings(fields, revision=before["settings_revision"])
    after = wait_idle(owner)
    assert after["recovery"] and after["unknown"]
    assert after["ui"] == before["ui"] and after["settings"] == before["settings"]
    assert after["settings_revision"] == before["settings_revision"]
    assert (
        "partially applied" in after["error"]
        and "Session options were not changed" in after["error"]
    )


@pytest.mark.parametrize("invalid", [{"decimals": -1}, {"units": "invalid"}, {"high_power": "yes"}])
def test_invalid_session_draft_cannot_write_device_settings(gui_owner, invalid):
    owner, mount = gui_owner
    connect(owner)
    before = owner.snapshot()
    calls = list(mount.calls)
    with pytest.raises(ValueError):
        owner.apply_settings(
            {**form_values(before), "backlash": 2, **invalid},
            revision=before["settings_revision"],
        )
    assert mount.calls == calls and owner.snapshot()["ui"] == before["ui"]


def test_invalid_device_draft_cannot_commit_session_options(gui_owner):
    owner, mount = gui_owner
    connect(owner)
    before = owner.snapshot()
    message = owner.apply_settings(
        {**form_values(before), "velocity": 15, "units": "rad"},
        revision=before["settings_revision"],
    )
    assert message == "configure requested."
    after = wait_idle(owner)
    assert "Velocity" in after["error"]
    assert after["ui"] == before["ui"] and after["settings"] == before["settings"]
    assert not after["recovery"]


def test_controller_validates_selected_drive_speed_before_usb_io(mount_factory):
    mount, transport = mount_factory([])
    owner = GuiController()
    owner._mount = mount
    owner._state.update(connected=True, status={"homed": True, "enabled": True})
    fields = {**dict.fromkeys(DEVICE_FIELDS), **form_values(owner.snapshot()), "drive1": 25}
    assert "session only" in owner.apply_settings(fields, revision=0)
    before = list(transport.sent)
    assert (
        owner.hold("start", "drive-speed-check", "drive-forward", 0) == "drive-forward requested."
    )
    snapshot = wait_idle(owner)
    assert "Drive velocity" in snapshot["error"]
    assert transport.sent == before and not snapshot["recovery"]
