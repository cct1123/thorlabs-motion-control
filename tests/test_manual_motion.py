"""Regression checks for manual-motion ownership and expired browser leases."""

import struct
import threading

import pytest
from conftest import (
    HOMED,
    MOVE_DONE,
    MOVING,
    POSITION_90,
    POSITION_REQUEST,
    POSITION_ZERO,
    STATUS_REQUEST,
    STOP_DONE,
    STOP_REQUEST,
)

from k10cr2 import MotionCancelled, MotionError
from k10cr2.gui_forms import default_session
from k10cr2.settings import display_angle

# Independent issue-48 single-step jog layout: channel, mode, step, minimum,
# acceleration, maximum, stop mode. A 90-degree step is 12,288,000 counts.
JOG_QUERY = bytes.fromhex("17 04 01 00 50 01")
JOG_SETTINGS = bytes.fromhex(
    "18 04 16 00 81 50 01 00 02 00 00 80 bb 00 00 00 00 00 de 05 00 00 55 d5 6f 00 02 00"
)
JOG_FORWARD = bytes.fromhex("6a 04 01 01 50 01")
VEL_QUERY = bytes.fromhex("14 04 01 00 50 01")
# Independent 2 degree/s original and 1 degree/s temporary profiles, both at
# 1 degree/s squared acceleration; these are not built with production encoders.
OLD_VELOCITY = struct.pack("<Hiii", 1, 0, 1502, 14_658_218)
TEMP_VELOCITY = struct.pack("<Hiii", 1, 0, 1502, 7_329_109)
VEL_REPLY = bytes.fromhex("15 04 0e 00 81 50") + OLD_VELOCITY
VEL_SET = bytes.fromhex("13 04 0e 00 d0 01") + OLD_VELOCITY
TEMP_REPLY = bytes.fromhex("15 04 0e 00 81 50") + TEMP_VELOCITY
TEMP_SET = bytes.fromhex("13 04 0e 00 d0 01") + TEMP_VELOCITY
CONTINUOUS_JOG = bytes.fromhex("18 04 16 00 81 50") + struct.pack(
    "<HHiiiiH", 1, 1, 12_288_000, 0, 1502, 7_329_109, 2
)


def test_release_during_between_step_readback_prevents_an_extra_jog(mount_factory, monkeypatch):
    mount, fake = mount_factory(
        [
            (STATUS_REQUEST, HOMED),
            (JOG_QUERY, JOG_SETTINGS),
            (POSITION_REQUEST, POSITION_ZERO),
            (JOG_FORWARD, b""),
            (STATUS_REQUEST, HOMED),
            (POSITION_REQUEST, MOVE_DONE + POSITION_90),
            (POSITION_REQUEST, POSITION_90),
        ]
    )
    held = [True]
    reads = [0]
    position = mount._position

    def release_between_steps(deadline):
        result = position(deadline)
        reads[0] += 1
        if reads[0] == 3:
            held[0] = False
        return result

    monkeypatch.setattr(mount, "_position", release_between_steps)
    result = mount.manual_motion(1, jog=True, held=lambda: held[0])
    assert result.position.counts == 12_288_000 and result.completion_received
    assert not result.no_op and not mount.recovery_required and not fake.steps
    assert fake.sent.count(JOG_FORWARD) == 1 and STOP_REQUEST not in fake.sent


@pytest.mark.parametrize("option", ["jog", "high_power"])
@pytest.mark.parametrize("flag", ["false", 0, 1, None])
def test_manual_mode_and_supply_confirmation_require_boolean_before_io(mount_factory, option, flag):
    mount, fake = mount_factory([])
    before = list(fake.sent)
    with pytest.raises(ValueError, match="True or False"):
        mount.manual_motion(1, speed=1, **{option: flag})
    assert fake.sent == before


@pytest.mark.parametrize("failure", ["cancel", "exit"])
def test_manual_observer_failure_at_completion_still_stops(mount_factory, failure):
    cancel = threading.Event()
    mount, fake = mount_factory(
        [
            (STATUS_REQUEST, HOMED),
            (JOG_QUERY, JOG_SETTINGS),
            (POSITION_REQUEST, POSITION_ZERO),
            (JOG_FORWARD, b""),
            (STATUS_REQUEST, HOMED if failure == "cancel" else MOVING),
            (POSITION_REQUEST, POSITION_90 if failure == "cancel" else POSITION_ZERO),
            (STOP_REQUEST, STOP_DONE),
            (STATUS_REQUEST, HOMED),
            (POSITION_REQUEST, POSITION_90),
        ]
    )

    def observer(*_):
        if failure == "exit":
            raise SystemExit("observer exited")
        cancel.set()

    with pytest.raises(MotionError) as caught:
        mount.manual_motion(1, jog=True, cancel=cancel, on_progress=observer)
    assert isinstance(caught.value.original, MotionCancelled if failure == "cancel" else SystemExit)
    assert caught.value.cleanup.confirmed_stopped
    assert caught.value.recovery_required == mount.recovery_required == (failure != "cancel")
    assert fake.sent.count(STOP_REQUEST) == 1 and not fake.steps


def test_single_jog_rejects_acceleration_above_manual_limit_before_motion(mount_factory):
    # Raw acceleration 30,041 exceeds the 20 degree/sÂ² limit (20 * 1,502).
    profile = JOG_SETTINGS[:18] + bytes.fromhex("59 75 00 00") + JOG_SETTINGS[22:]
    mount, fake = mount_factory([(STATUS_REQUEST, HOMED), (JOG_QUERY, profile)])
    with pytest.raises(ValueError, match="[Aa]cceleration"):
        mount.manual_motion(1, jog=True)
    assert JOG_FORWARD not in fake.sent and not fake.steps


@pytest.mark.parametrize("counts,direction", [(-12_288_000, -1), (61_440_000, 1)])
def test_fixed_range_refuses_outward_jog_from_outside(mount_factory, counts, direction):
    position = bytes.fromhex("12 04 06 00 81 50") + struct.pack("<Hi", 1, counts)
    mount, fake = mount_factory(
        [(STATUS_REQUEST, HOMED), (JOG_QUERY, JOG_SETTINGS), (POSITION_REQUEST, position)]
    )
    with pytest.raises(ValueError, match="toward the range"):
        mount.manual_motion(direction, jog=True)
    assert not fake.steps  # No actuator write was scripted.


@pytest.mark.parametrize(
    "counts,direction,target",
    [
        (-24_576_000, 1, -12_288_000),  # Approach from below without reaching zero yet.
        (-12_288_000, 1, 0),
        (73_728_000, -1, 61_440_000),  # Approach from above without reaching 360 yet.
        (61_440_000, -1, 49_152_000),
    ],
)
def test_fixed_jog_can_approach_or_enter_the_interval(mount_factory, counts, direction, target):
    position = bytes.fromhex("12 04 06 00 81 50") + struct.pack("<Hi", 1, counts)
    target_position = bytes.fromhex("12 04 06 00 81 50") + struct.pack("<Hi", 1, target)
    jog = bytes.fromhex("6a 04 01 01 50 01" if direction > 0 else "6a 04 01 02 50 01")
    mount, fake = mount_factory(
        [
            (STATUS_REQUEST, HOMED),
            (JOG_QUERY, JOG_SETTINGS),
            (POSITION_REQUEST, position),
            (jog, b""),
            (STATUS_REQUEST, HOMED),
            (POSITION_REQUEST, target_position),
        ]
    )
    assert mount.manual_motion(direction, jog=True).position.counts == target
    assert not fake.steps


@pytest.mark.parametrize("counts,direction", [(0, -1), (49_152_000, 1), (-1, 1), (49_152_001, -1)])
def test_fixed_jog_cannot_cross_the_opposite_boundary(mount_factory, counts, direction):
    # A 720-degree step cannot overshoot the interval even during inward recovery.
    profile = JOG_SETTINGS[:10] + struct.pack("<i", 98_304_000) + JOG_SETTINGS[14:]
    position = bytes.fromhex("12 04 06 00 81 50") + struct.pack("<Hi", 1, counts)
    mount, fake = mount_factory(
        [(STATUS_REQUEST, HOMED), (JOG_QUERY, profile), (POSITION_REQUEST, position)]
    )
    with pytest.raises(ValueError, match="exceed"):
        mount.manual_motion(direction, jog=True)
    assert not fake.steps


@pytest.mark.parametrize("manual", [False, True])
def test_motion_requires_home_after_exceeding_counter_bound(mount_factory, manual):
    # 43.5 revolutions: still a signed 32-bit counter, beyond the manual's 43 turns.
    position = bytes.fromhex("12 04 06 00 81 50") + struct.pack("<Hi", 1, 2_138_112_000)
    steps = [(STATUS_REQUEST, HOMED)]
    if manual:
        steps.append((JOG_QUERY, JOG_SETTINGS))
    steps.append((POSITION_REQUEST, position))
    mount, fake = mount_factory(steps)
    with pytest.raises(RuntimeError, match="43-turn"):
        if manual:
            mount.manual_motion(-1, jog=True, mode="total")
        else:
            mount.move_absolute(0)
    assert not fake.steps  # Only read-only queries were scripted.


@pytest.mark.parametrize("jog", [False, True])
@pytest.mark.parametrize("outcome", ["ok", "restore-write", "restore-mismatch", "stop-failed"])
def test_cancel_restores_continuous_profile_before_allowing_more_motion(
    mount_factory, jog, outcome
):
    cancel = threading.Event()
    move360 = bytes.fromhex("53 04 06 00 d0 01 01 00 00 00 ee 02")
    move90 = bytes.fromhex("53 04 06 00 d0 01 01 00 00 80 bb 00")
    cleanup = (
        [(STOP_REQUEST, OSError("Stop failed"))]
        if outcome == "stop-failed"
        else [
            (STOP_REQUEST, STOP_DONE),
            (STATUS_REQUEST, HOMED),
            (POSITION_REQUEST, POSITION_ZERO),
        ]
    )
    restore = {
        "ok": [(VEL_SET, b""), (VEL_QUERY, VEL_REPLY)],
        "restore-write": [(VEL_SET, OSError("Restore failed"))],
        "restore-mismatch": [(VEL_SET, b""), (VEL_QUERY, TEMP_REPLY)],
        "stop-failed": [],
    }[outcome]
    mount, fake = mount_factory(
        [
            (STATUS_REQUEST, HOMED),
            *([(JOG_QUERY, CONTINUOUS_JOG)] if jog else []),
            (VEL_QUERY, VEL_REPLY),
            (TEMP_SET, b""),
            (VEL_QUERY, TEMP_REPLY),
            (POSITION_REQUEST, POSITION_ZERO),
            (move360, b""),
            (STATUS_REQUEST, MOVING),
            (POSITION_REQUEST, POSITION_ZERO),
            *cleanup,
            *restore,
            *(
                [
                    (STATUS_REQUEST, HOMED),
                    (POSITION_REQUEST, POSITION_ZERO),
                    (move90, b""),
                    (STATUS_REQUEST, HOMED),
                    (POSITION_REQUEST, POSITION_90),
                ]
                if outcome == "ok"
                else []
            ),
        ]
    )
    with pytest.raises(MotionError) as caught:
        mount.manual_motion(
            1,
            jog=jog,
            speed=1,
            held=lambda: True,
            cancel=cancel,
            on_progress=lambda *_: cancel.set(),
        )
    assert isinstance(caught.value.original, MotionCancelled)
    assert caught.value.cleanup.confirmed_stopped == (outcome != "stop-failed")
    assert caught.value.recovery_required == mount.recovery_required == (outcome != "ok")
    if outcome == "ok":
        assert mount.move_absolute(90).position.degrees == 90
        assert fake.sent.index(STOP_REQUEST) < fake.sent.index(VEL_SET) < fake.sent.index(move90)
    else:
        assert "profile" in " ".join(caught.value.__notes__)
        with pytest.raises(RuntimeError, match="uncertain"):
            mount.move_absolute(90)
        if outcome == "stop-failed":
            assert VEL_SET not in fake.sent
    assert fake.sent.count(STOP_REQUEST) == 1 and not fake.steps


@pytest.mark.parametrize("jog", [False, True])
@pytest.mark.parametrize(
    "counts,direction,target",
    [(-12_288_000, 1, 0), (61_440_000, -1, 49_152_000)],
)
def test_held_recovery_stops_at_nearest_fixed_boundary(
    mount_factory, jog, counts, direction, target
):
    position = bytes.fromhex("12 04 06 00 81 50") + struct.pack("<Hi", 1, counts)
    target_position = bytes.fromhex("12 04 06 00 81 50") + struct.pack("<Hi", 1, target)
    move = bytes.fromhex("53 04 06 00 d0 01") + struct.pack("<Hi", 1, target)
    mount, fake = mount_factory(
        [
            (STATUS_REQUEST, HOMED),
            *([(JOG_QUERY, CONTINUOUS_JOG)] if jog else []),
            (VEL_QUERY, VEL_REPLY),
            (TEMP_SET, b""),
            (VEL_QUERY, TEMP_REPLY),
            (POSITION_REQUEST, position),
            (move, b""),
            (STATUS_REQUEST, HOMED),
            (POSITION_REQUEST, target_position),
            (VEL_SET, b""),
            (VEL_QUERY, VEL_REPLY),
        ]
    )
    result = mount.manual_motion(direction, jog=jog, speed=1, held=lambda: True)
    assert result.position.counts == target
    assert not mount.recovery_required and not fake.steps


@pytest.mark.parametrize(
    "counts,direction,target", [(0, -1, -12_288_000), (49_152_000, 1, 61_440_000)]
)
def test_default_gui_jog_crosses_zero_and_full_turn(mount_factory, counts, direction, target):
    position = bytes.fromhex("12 04 06 00 81 50") + struct.pack("<Hi", 1, counts)
    target_position = bytes.fromhex("12 04 06 00 81 50") + struct.pack("<Hi", 1, target)
    jog = bytes.fromhex("6a 04 01 02 50 01" if direction < 0 else "6a 04 01 01 50 01")
    mode = default_session()["rotation_mode"]
    mount, fake = mount_factory(
        [
            (STATUS_REQUEST, HOMED),
            (JOG_QUERY, JOG_SETTINGS),
            (POSITION_REQUEST, position),
            (jog, b""),
            (STATUS_REQUEST, HOMED),
            (POSITION_REQUEST, target_position),
        ]
    )
    result = mount.manual_motion(direction, jog=True, mode=mode)
    assert mode == "equivalent" and result.position.counts == target
    assert display_angle(result.position.degrees, mode) == (
        "270.000000" if direction < 0 else "90.000000"
    )
    assert not fake.steps


def test_default_reverse_drive_from_zero_is_bounded_and_stops_on_release(mount_factory):
    # -15,480 degrees = -2,113,536,000 counts, still inside signed int32.
    boundary = bytes.fromhex("53 04 06 00 d0 01") + struct.pack("<Hi", 1, -2_113_536_000)
    held = [True]
    mount, fake = mount_factory(
        [
            (STATUS_REQUEST, HOMED),
            (VEL_QUERY, VEL_REPLY),
            (TEMP_SET, b""),
            (VEL_QUERY, TEMP_REPLY),
            (POSITION_REQUEST, POSITION_ZERO),
            (boundary, b""),
            (STATUS_REQUEST, MOVING),
            (POSITION_REQUEST, POSITION_ZERO),
            (STOP_REQUEST, STOP_DONE),
            (STATUS_REQUEST, HOMED),
            (POSITION_REQUEST, POSITION_ZERO),
            (VEL_SET, b""),
            (VEL_QUERY, VEL_REPLY),
        ]
    )
    result = mount.manual_motion(
        -1,
        speed=1,
        mode=default_session()["rotation_mode"],
        held=lambda: held[0],
        on_progress=lambda *_: held.__setitem__(0, False),
    )
    assert result.acknowledged and not mount.recovery_required and not fake.steps
