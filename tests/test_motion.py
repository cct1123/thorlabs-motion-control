import pytest
from conftest import (
    ENABLE_REQUEST,
    HOME_DONE,
    HOME_REQUEST,
    HOMED,
    HOMING,
    IDLE,
    MOVE_DONE,
    MOVING,
    POSITION_90,
    POSITION_REQUEST,
    POSITION_ZERO,
    STATUS_REQUEST,
    STOP_DONE,
    STOP_REQUEST,
)

from k10cr2 import MotionError

MOVE_90 = bytes.fromhex("53 04 06 00 d0 01 01 00 00 80 bb 00")


def stop_steps():
    return [(STOP_REQUEST, STOP_DONE), (STATUS_REQUEST, HOMED), (POSITION_REQUEST, POSITION_ZERO)]


def test_home_transition_completion_and_fresh_status(mount_factory):
    mount, fake = mount_factory(
        [
            (STATUS_REQUEST, IDLE),
            (POSITION_REQUEST, POSITION_ZERO),
            (HOME_REQUEST, b""),
            (STATUS_REQUEST, HOMING),
            (STATUS_REQUEST, HOME_DONE + HOMED),
            (POSITION_REQUEST, POSITION_ZERO),
        ]
    )
    result = mount.home()
    assert result.status.homed and not result.status.busy and result.position.counts == 0
    assert result.completion_received and not fake.steps
    assert fake.sent.count(HOME_REQUEST) == 1
    assert ENABLE_REQUEST not in fake.sent  # Already enabled.


def test_home_uses_transition_if_end_notification_missing(mount_factory):
    mount, _ = mount_factory(
        [
            (STATUS_REQUEST, IDLE),
            (POSITION_REQUEST, POSITION_ZERO),
            (HOME_REQUEST, b""),
            (STATUS_REQUEST, HOMING),
            (STATUS_REQUEST, HOMED),
            (POSITION_REQUEST, POSITION_ZERO),
        ]
    )
    assert not mount.home().completion_received


def test_stale_home_event_and_initial_idle_are_not_completion(mount_factory):
    mount, fake = mount_factory(
        [
            (STATUS_REQUEST, HOME_DONE + HOMED),
            (POSITION_REQUEST, POSITION_ZERO),
            (HOME_REQUEST, b""),
            (STATUS_REQUEST, HOME_DONE + HOMED),
            (POSITION_REQUEST, POSITION_ZERO),
            (STATUS_REQUEST, HOMING),
            (STATUS_REQUEST, HOME_DONE + HOMED),
            (POSITION_REQUEST, POSITION_ZERO),
        ]
    )
    assert mount.home().status.homed
    assert not fake.steps  # Must reach the actual transition, not return on stale event.


def test_absolute_move_and_enable_helper(mount_factory):
    disabled_homed = bytes.fromhex("2a 04 06 00 81 50 01 00 00 04 00 00")
    mount, fake = mount_factory(
        [
            (STATUS_REQUEST, disabled_homed),
            (POSITION_REQUEST, POSITION_ZERO),
            (ENABLE_REQUEST, b""),
            (STATUS_REQUEST, HOMED),
            (MOVE_90, b""),
            (STATUS_REQUEST, MOVING),
            (STATUS_REQUEST, MOVE_DONE + HOMED),
            (POSITION_REQUEST, POSITION_90),
        ]
    )
    result = mount.move_absolute(90)
    assert result.position.degrees == 90 and result.completion_received
    assert not fake.steps and fake.sent.count(MOVE_90) == 1


def test_short_move_reaching_changed_target_without_busy_or_event(mount_factory):
    mount, _ = mount_factory(
        [
            (STATUS_REQUEST, HOMED),
            (POSITION_REQUEST, POSITION_ZERO),
            (MOVE_90, b""),
            (STATUS_REQUEST, HOMED),
            (POSITION_REQUEST, POSITION_90),
        ]
    )
    result = mount.move_absolute(90)
    assert result.position.degrees == 90 and not result.completion_received


def test_already_at_target_no_op(mount_factory):
    mount, fake = mount_factory([(STATUS_REQUEST, HOMED), (POSITION_REQUEST, POSITION_90)])
    assert mount.move_absolute(90).no_op
    assert MOVE_90 not in fake.sent and ENABLE_REQUEST not in fake.sent


def test_unhomed_or_busy_refuses_before_actuator_write(mount_factory):
    for status in (IDLE, MOVING):
        mount, fake = mount_factory([(STATUS_REQUEST, status)])
        with pytest.raises(RuntimeError):
            mount.move_absolute(90)
        assert MOVE_90 not in fake.sent and ENABLE_REQUEST not in fake.sent
        mount.close()


@pytest.mark.parametrize("angle", [-1, 360, float("nan"), float("inf"), True])
def test_invalid_target_no_queries_or_motion(mount_factory, angle):
    mount, fake = mount_factory([])
    with pytest.raises(ValueError):
        mount.move_absolute(angle)
    assert len(fake.sent) == 1


@pytest.mark.parametrize("operation", ["home", "move_absolute", "stop"])
def test_bad_motion_timeout_before_io(mount_factory, operation):
    mount, fake = mount_factory([])
    with pytest.raises(ValueError):
        getattr(mount, operation)(*([90] if operation == "move_absolute" else []), timeout=0)
    assert len(fake.sent) == 1


@pytest.mark.parametrize(
    "failure", [TimeoutError("lost reply"), KeyboardInterrupt(), OSError("disconnect")]
)
def test_motion_failure_stops_and_preserves_original(mount_factory, failure):
    mount, fake = mount_factory(
        [
            (STATUS_REQUEST, IDLE),
            (POSITION_REQUEST, POSITION_ZERO),
            (HOME_REQUEST, b""),
            (STATUS_REQUEST, failure),
            *stop_steps(),
        ]
    )
    with pytest.raises(MotionError) as caught:
        mount.home()
    assert caught.value.original is failure
    assert caught.value.cleanup.confirmed_stopped
    assert mount.recovery_required and not fake.steps
    with pytest.raises(RuntimeError, match="uncertain"):
        mount.home()


def test_partial_home_write_still_attempts_stop(mount_factory):
    mount, fake = mount_factory(
        [
            (STATUS_REQUEST, IDLE),
            (POSITION_REQUEST, POSITION_ZERO),
            (HOME_REQUEST, OSError("Partial write")),
            *stop_steps(),
        ]
    )
    with pytest.raises(MotionError) as caught:
        mount.home()
    assert caught.value.cleanup.confirmed_stopped
    assert fake.sent.count(HOME_REQUEST) == 1 and fake.sent.count(STOP_REQUEST) == 1


def test_failed_stop_reports_unknown_and_preserves_original(mount_factory):
    mount, _ = mount_factory(
        [
            (STATUS_REQUEST, IDLE),
            (POSITION_REQUEST, POSITION_ZERO),
            (HOME_REQUEST, b""),
            (STATUS_REQUEST, TimeoutError("query failed")),
            (STOP_REQUEST, OSError("USB gone")),
        ]
    )
    with pytest.raises(MotionError, match="UNKNOWN") as caught:
        mount.home()
    assert str(caught.value.original) == "query failed"
    assert "USB gone" in caught.value.cleanup.error
    assert not caught.value.cleanup.confirmed_stopped


def test_explicit_stop_waits_for_rest(mount_factory):
    mount, fake = mount_factory(
        [
            (STOP_REQUEST, STOP_DONE),
            (STATUS_REQUEST, MOVING),
            (STATUS_REQUEST, HOMED),
            (POSITION_REQUEST, POSITION_ZERO),
        ]
    )
    result = mount.stop()
    assert result.acknowledged and not result.status.busy and not fake.steps


def test_end_to_end_deadline_has_separate_cleanup_budget(mount_factory, monkeypatch):
    import k10cr2.controller as module

    clock = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        module.time, "sleep", lambda duration: clock.__setitem__(0, clock[0] + duration)
    )
    mount, fake = mount_factory(
        [
            (STATUS_REQUEST, IDLE),
            (POSITION_REQUEST, POSITION_ZERO),
            (HOME_REQUEST, b""),
            (STATUS_REQUEST, HOMING),
            *stop_steps(),
        ],
        cleanup_timeout=5,
    )
    with pytest.raises(MotionError) as caught:
        mount.home(timeout=0.01)
    assert isinstance(caught.value.original, TimeoutError)
    assert caught.value.cleanup.confirmed_stopped
    stop_index = fake.sent.index(STOP_REQUEST)
    assert fake.deadlines[stop_index] > fake.deadlines[stop_index - 1]


def test_wrong_target_does_not_complete_on_stale_move_event(mount_factory):
    mount, fake = mount_factory(
        [
            (STATUS_REQUEST, HOMED),
            (POSITION_REQUEST, POSITION_ZERO),
            (MOVE_90, b""),
            (STATUS_REQUEST, MOVE_DONE + HOMED),
            (POSITION_REQUEST, POSITION_ZERO),
            (STATUS_REQUEST, MOVING),
            (STATUS_REQUEST, HOMED),
            (POSITION_REQUEST, POSITION_90),
        ]
    )
    assert mount.move_absolute(90).position.counts == 12288000
    assert not fake.steps


def test_cooperative_stop_and_progress_use_the_motion_receive_path(mount_factory):
    import threading

    from k10cr2 import MotionCancelled

    cancel = threading.Event()
    progress = []
    mount, fake = mount_factory(
        [
            (STATUS_REQUEST, HOMED),
            (POSITION_REQUEST, POSITION_ZERO),
            (MOVE_90, b""),
            (STATUS_REQUEST, MOVING),
            (POSITION_REQUEST, POSITION_ZERO),
            *stop_steps(),
        ]
    )

    def report(status, position):
        progress.append((status, position))
        cancel.set()

    with pytest.raises(MotionError) as caught:
        mount.move_absolute(90, cancel=cancel, on_progress=report)
    assert isinstance(caught.value.original, MotionCancelled)
    assert caught.value.cleanup.confirmed_stopped
    assert progress[0][0].moving and progress[0][1].counts == 0
    assert fake.sent.count(STOP_REQUEST) == 1 and not fake.steps


def test_cancel_before_motion_does_not_enable_or_move(mount_factory):
    import threading

    from k10cr2 import MotionCancelled

    cancel = threading.Event()
    cancel.set()
    disabled_homed = bytes.fromhex("2a 04 06 00 81 50 01 00 00 04 00 00")
    mount, fake = mount_factory(
        [(STATUS_REQUEST, disabled_homed), (POSITION_REQUEST, POSITION_ZERO)]
    )
    with pytest.raises(MotionCancelled):
        mount.move_absolute(90, cancel=cancel)
    assert ENABLE_REQUEST not in fake.sent and MOVE_90 not in fake.sent
    assert STOP_REQUEST not in fake.sent and not mount.recovery_required


def test_progress_callback_failure_runs_stop_cleanup(mount_factory):
    mount, fake = mount_factory(
        [
            (STATUS_REQUEST, HOMED),
            (POSITION_REQUEST, POSITION_ZERO),
            (MOVE_90, b""),
            (STATUS_REQUEST, MOVING),
            (POSITION_REQUEST, POSITION_ZERO),
            *stop_steps(),
        ]
    )

    def report(status, position):
        raise RuntimeError("UI observer failed")

    with pytest.raises(MotionError) as caught:
        mount.move_absolute(90, on_progress=report)
    assert str(caught.value.original) == "UI observer failed"
    assert caught.value.cleanup.confirmed_stopped and not fake.steps
