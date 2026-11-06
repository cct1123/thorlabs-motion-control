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
    "18 04 16 00 81 50 01 00 02 00 00 80 bb 00 00 00 00 00 01 00 00 00 01 00 00 00 02 00"
)
JOG_FORWARD = bytes.fromhex("6a 04 01 01 50 01")


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
