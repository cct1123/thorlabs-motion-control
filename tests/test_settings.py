import math

import pytest
from conftest import HOMED, POSITION_REQUEST, POSITION_ZERO, STATUS_REQUEST

from k10cr2.protocol import FrameBuffer, ProtocolError
from k10cr2.settings import display_angle, resolve_target

# Literal frames, independent of the implementation encoder.
VEL_QUERY = bytes.fromhex("14 04 01 00 50 01")
VEL_REPLY = bytes.fromhex("15 04 0e 00 81 50 01 00 00 00 00 00 ac 3a 00 00 52 55 5e 04")
VEL_SET = bytes.fromhex("13 04 0e 00 d0 01 01 00 00 00 00 00 ac 3a 00 00 52 55 5e 04")
VEL_ONE = bytes.fromhex("15 04 0e 00 81 50 01 00 00 00 00 00 de 05 00 00 55 d5 6f 00")
VEL_ONE_SET = bytes.fromhex("13 04 0e 00 d0 01 01 00 00 00 00 00 de 05 00 00 55 d5 6f 00")


def test_query_velocity_literal_layout(mount_factory):
    mount, fake = mount_factory([(VEL_QUERY, VEL_REPLY)])
    values = mount.query_settings(["velocity"])["velocity"]
    assert values == dict(channel=1, minimum=0, acceleration=15020, maximum=73291090)
    assert not fake.steps


def test_configure_reads_back_and_persists_only_requested_group(mount_factory):
    eeprom_velocity = bytes.fromhex("b9 04 04 00 d0 01 01 00 13 04")
    mount, fake = mount_factory(
        [
            (STATUS_REQUEST, HOMED),
            (VEL_QUERY, VEL_ONE),
            (VEL_SET, b""),
            (VEL_QUERY, VEL_REPLY),
            (eeprom_velocity, b""),
        ]
    )
    actual = mount.configure({"velocity": dict(velocity=10, acceleration=10)}, persist=True)
    assert actual["velocity"]["maximum"] == 73291090
    assert not fake.steps


def test_invalid_complete_draft_has_no_setting_writes(mount_factory):
    mount, fake = mount_factory([(STATUS_REQUEST, HOMED), (VEL_QUERY, VEL_REPLY)])
    with pytest.raises(ValueError):
        mount.configure({"velocity": dict(velocity=20, acceleration=10)})
    assert VEL_SET not in fake.sent
    assert not fake.steps


def test_all_groups_validate_before_first_write(mount_factory):
    jog_query = bytes.fromhex("17 04 01 00 50 01")
    jog_reply = bytes.fromhex(
        "18 04 16 00 81 50 01 00 02 00 00 80 bb 00 00 00 00 00 de 05 00 00 55 d5 6f 00 02 00"
    )
    mount, fake = mount_factory(
        [(STATUS_REQUEST, HOMED), (VEL_QUERY, VEL_REPLY), (jog_query, jog_reply)]
    )
    with pytest.raises(ValueError):
        mount.configure(
            {
                "velocity": dict(velocity=8, acceleration=8),
                "jog": dict(
                    jog_step=5, jog_mode=2, jog_stop=2, jog_velocity=100, jog_acceleration=10
                ),
            }
        )
    assert fake.sent == [bytes.fromhex("05 00 00 00 50 01"), STATUS_REQUEST, VEL_QUERY, jog_query]


def test_readback_mismatch_requires_explicit_recovery(mount_factory):
    mount, fake = mount_factory(
        [
            (STATUS_REQUEST, HOMED),
            (VEL_QUERY, VEL_ONE),
            (VEL_SET, b""),
            (VEL_QUERY, VEL_ONE),
        ]
    )
    with pytest.raises(RuntimeError, match="readback"):
        mount.configure({"velocity": dict(velocity=10, acceleration=10)})
    assert mount.recovery_required and not fake.steps


@pytest.mark.parametrize("group", ["power", "trigger", "defaults", "home", "bow"])
def test_unverified_or_locked_settings_never_query_or_write(mount_factory, group):
    mount, fake = mount_factory([])
    with pytest.raises(ValueError):
        mount.configure({group: {}})
    assert len(fake.sent) == 1


def test_new_response_sizes_and_channel_are_checked():
    for raw in (
        VEL_REPLY[:2] + b"\x0c\x00" + VEL_REPLY[4:],
        VEL_REPLY[:6] + b"\x02\x00" + VEL_REPLY[8:],
    ):
        parser = FrameBuffer()
        parser.feed(raw)
        with pytest.raises(ProtocolError):
            parser.pop()


@pytest.mark.parametrize(
    ("target", "current", "mode", "direction", "expected"),
    [
        (360, 0, "fixed", "quickest", 360),
        (730, 0, "total", "quickest", 730),
        (10, 350, "equivalent", "quickest", 370),
        (10, 350, "equivalent", "reverse", 10),
        (350, 10, "equivalent", "quickest", -10),
        (350, 10, "equivalent", "forward", 350),
    ],
)
def test_manual_rotation_modes(target, current, mode, direction, expected):
    assert resolve_target(target, current, mode, direction) == round(expected * 409600 / 3)


@pytest.mark.parametrize("target", [15481, -15481, float("nan"), float("inf"), True])
def test_total_counter_guard(target):
    with pytest.raises(ValueError):
        resolve_target(target, 0, "total")


def test_display_units_and_equivalent_wrap():
    assert display_angle(730, "equivalent") == "10.000000"
    assert display_angle(180, "total", "rad", 6) == f"{math.pi:.6f}"
    assert display_angle(359.99999999, "equivalent", "deg", 6) == "0.000000"


def test_identify_and_explicit_disable_use_documented_short_commands(mount_factory):
    disabled = bytes.fromhex("2a 04 06 00 81 50 01 00 00 04 00 00")
    identify = bytes.fromhex("23 02 00 00 50 01")
    disable = bytes.fromhex("10 02 01 02 50 01")
    mount, fake = mount_factory(
        [(identify, b""), (STATUS_REQUEST, HOMED), (disable, b""), (STATUS_REQUEST, disabled)]
    )
    mount.identify()
    assert not mount.set_enabled(False).enabled
    assert not fake.steps


def test_single_jog_literal_command_and_completion(mount_factory):
    jog_query = bytes.fromhex("17 04 01 00 50 01")
    jog_reply = bytes.fromhex(
        "18 04 16 00 81 50 01 00 02 00 00 80 bb 00 00 00 00 00 de 05 00 00 55 d5 6f 00 02 00"
    )
    jog_forward = bytes.fromhex("6a 04 01 01 50 01")
    position90 = bytes.fromhex("12 04 06 00 81 50 01 00 00 80 bb 00")
    mount, fake = mount_factory(
        [
            (STATUS_REQUEST, HOMED),
            (jog_query, jog_reply),
            (POSITION_REQUEST, POSITION_ZERO),
            (jog_forward, b""),
            (STATUS_REQUEST, HOMED),
            (POSITION_REQUEST, position90),
        ]
    )
    assert mount.manual_motion(1, jog=True).position.degrees == 90
    assert not fake.steps


def test_released_drive_stops_before_restoring_temporary_velocity(mount_factory):
    from conftest import MOVING, STOP_DONE, STOP_REQUEST

    to360 = bytes.fromhex("53 04 06 00 d0 01 01 00 00 00 ee 02")
    restore = VEL_SET
    mount, fake = mount_factory(
        [
            (STATUS_REQUEST, HOMED),
            (VEL_QUERY, VEL_REPLY),
            (VEL_ONE_SET, b""),
            (VEL_QUERY, VEL_ONE),
            (POSITION_REQUEST, POSITION_ZERO),
            (to360, b""),
            (STATUS_REQUEST, MOVING),
            (POSITION_REQUEST, POSITION_ZERO),
            (STOP_REQUEST, STOP_DONE),
            (STATUS_REQUEST, HOMED),
            (POSITION_REQUEST, POSITION_ZERO),
            (restore, b""),
            (VEL_QUERY, VEL_REPLY),
        ]
    )
    held = [True]
    # Use a 1°/s profile whose acceleration is inherited from the old profile (10°/s²).
    # Override the old literal to a 1°/s² acceleration for the full temporary profile.
    from collections import deque

    old = VEL_REPLY[:12] + bytes.fromhex("de 05 00 00") + VEL_REPLY[16:]
    restore_one_accel = VEL_SET[:12] + bytes.fromhex("de 05 00 00") + VEL_SET[16:]
    steps = list(fake.steps)
    steps[1] = (VEL_QUERY, old)
    steps[-2:] = [(restore_one_accel, b""), (VEL_QUERY, old)]
    fake.steps = deque(steps)
    result = mount.manual_motion(
        1, speed=1, held=lambda: held[0], on_progress=lambda *_: held.__setitem__(0, False)
    )
    assert result.acknowledged and not mount.recovery_required and not fake.steps
    assert fake.sent.index(STOP_REQUEST) < fake.sent.index(restore_one_accel)
