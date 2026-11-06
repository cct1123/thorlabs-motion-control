"""Regression checks for manual-motion ownership and expired browser leases."""

import threading

import pytest
from conftest import (
    HOMED,
    MOVING,
    POSITION_90,
    POSITION_REQUEST,
    POSITION_ZERO,
    STATUS_REQUEST,
    STOP_DONE,
    STOP_REQUEST,
)

from k10cr2 import MotionCancelled, MotionError

# Independent issue-48 single-step jog layout: channel, mode, step, minimum,
# acceleration, maximum, stop mode. A 90-degree step is 12,288,000 counts.
JOG_QUERY = bytes.fromhex("17 04 01 00 50 01")
JOG_SETTINGS = bytes.fromhex(
    "18 04 16 00 81 50 01 00 02 00 00 80 bb 00 00 00 00 00 de 05 00 00 55 d5 6f 00 02 00"
)
JOG_FORWARD = bytes.fromhex("6a 04 01 01 50 01")


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
    assert caught.value.cleanup.confirmed_stopped and mount.recovery_required
    assert fake.sent.count(STOP_REQUEST) == 1 and not fake.steps


def test_single_jog_rejects_acceleration_above_manual_limit_before_motion(mount_factory):
    # Raw acceleration 30,041 exceeds the 20 degree/s² limit (20 * 1,502).
    profile = JOG_SETTINGS[:18] + bytes.fromhex("59 75 00 00") + JOG_SETTINGS[22:]
    mount, fake = mount_factory([(STATUS_REQUEST, HOMED), (JOG_QUERY, profile)])
    with pytest.raises(ValueError, match="[Aa]cceleration"):
        mount.manual_motion(1, jog=True)
    assert JOG_FORWARD not in fake.sent and not fake.steps


@pytest.mark.parametrize("manual", [False, True])
def test_fixed_range_refuses_an_outside_counter_before_motion(mount_factory, manual):
    # 54,613,333 counts is approximately 400 degrees, outside Fixed Range.
    position400 = bytes.fromhex("12 04 06 00 81 50 01 00 55 55 41 03")
    steps = [(STATUS_REQUEST, HOMED)]
    if manual:
        steps.append((JOG_QUERY, JOG_SETTINGS))
    steps.append((POSITION_REQUEST, position400))
    mount, fake = mount_factory(steps)
    with pytest.raises(ValueError, match="outside"):
        if manual:
            mount.manual_motion(1, jog=True)
        else:
            mount.move_to(90, mode="fixed")
    assert JOG_FORWARD not in fake.sent and not fake.steps


def test_absolute_move_requires_home_after_exceeding_counter_bound(mount_factory):
    import struct

    # 43.5 revolutions: still a signed 32-bit counter, beyond the manual's 43 turns.
    position = bytes.fromhex("12 04 06 00 81 50") + struct.pack("<Hi", 1, 2_138_112_000)
    mount, fake = mount_factory([(STATUS_REQUEST, HOMED), (POSITION_REQUEST, position)])
    with pytest.raises(RuntimeError, match="43-turn"):
        mount.move_absolute(0)
    assert len(fake.sent) == 3 and not fake.steps  # Identity plus two read-only queries.
