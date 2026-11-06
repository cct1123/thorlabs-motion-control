import json
import math
import struct

import pytest
from conftest import FIXTURES, HOME_DONE, HOMED, IDLE, MOVE_DONE, POSITION_90, STOP_DONE

from k10cr2.protocol import (
    ControllerFault,
    FrameBuffer,
    ProtocolError,
    angle_to_counts,
    decode_identity,
    decode_position,
    decode_status,
    request,
)


@pytest.mark.parametrize(
    ("name", "literal"),
    [
        ("info", "05 00 00 00 50 01"),
        ("status", "29 04 01 00 50 01"),
        ("position", "11 04 01 00 50 01"),
        ("enable", "10 02 01 01 50 01"),
        ("home", "43 04 01 00 50 01"),
        ("stop", "65 04 01 02 50 01"),
    ],
)
def test_command_table(name, literal):
    assert request(name) == bytes.fromhex(literal)


def test_absolute_move_literal_and_signed_payload():
    assert request("move", counts=12_288_000) == bytes.fromhex(
        "53 04 06 00 d0 01 01 00 00 80 bb 00"
    )
    assert request("move", counts=-1) == bytes.fromhex("53 04 06 00 d0 01 01 00 ff ff ff ff")
    for value in (None, True, 1.5, 2**31, -(2**31) - 1):
        with pytest.raises(ValueError):
            request("move", counts=value)


@pytest.mark.parametrize(
    ("angle", "counts"), [(0, 0), (10, 1365333), (90, 12288000), (180, 24576000)]
)
def test_counts_conversion(angle, counts):
    assert angle_to_counts(angle) == counts


@pytest.mark.parametrize(
    "angle", [-1, 360, math.nan, math.inf, -math.inf, "90", True, 359.999999999]
)
def test_invalid_angles(angle):
    with pytest.raises(ValueError):
        angle_to_counts(angle)


def test_all_split_boundaries_and_coalesced_frames(identity_bytes):
    stream = identity_bytes + IDLE + HOME_DONE + MOVE_DONE + STOP_DONE + POSITION_90
    expected = [0x0006, 0x042A, 0x0444, 0x0464, 0x0466, 0x0412]
    for split in range(len(stream) + 1):
        parser = FrameBuffer()
        frames = []
        for chunk in (stream[:split], stream[split:]):
            parser.feed(chunk)
            while (frame := parser.pop()) is not None:
                frames.append(frame.message_id)
        assert frames == expected
        assert not parser.data


def frame(data):
    parser = FrameBuffer()
    parser.feed(data)
    return parser.pop()


def test_recorded_identity(identity_bytes):
    info = decode_identity(frame(identity_bytes))
    assert (info.model, info.serial, info.firmware, info.hardware_revision, info.channels) == (
        "K10CR2",
        "55551174",
        "2.0.1",
        4,
        1,
    )


def test_status_and_position_literals():
    status = decode_status(frame(HOMED))
    assert status.raw_bits == 0x80000400
    assert status.homed and status.enabled and not status.busy and not status.motion_error
    position = decode_position(frame(POSITION_90))
    assert position.counts == 12_288_000 and position.degrees == 90
    assert decode_position(frame(bytes.fromhex("12 04 06 00 81 50 01 00 ff ff ff ff"))).counts == -1
    status = decode_status(frame(bytes.fromhex("2a 04 06 00 81 50 01 00 f0 42 00 80")))
    assert status.moving and status.homing and status.motion_error and status.busy


@pytest.mark.parametrize(
    "data",
    [
        "2a 04 06 00 81 22 01 00 00 04 00 80",  # Wrong source.
        "2a 04 06 00 82 50 01 00 00 04 00 80",  # Wrong host.
        "2a 04 06 00 81 50 02 00 00 04 00 80",  # Wrong channel.
        "2a 04 05 00 81 50",  # Wrong known length.
        "44 04 02 00 01 50",  # Short home event has a channel too.
        "44 04 01 00 81 50",  # Home complete must be a short frame.
        "99 09 ff ff 81 50",  # Implausible unknown length.
    ],
)
def test_malformed_frames(data):
    with pytest.raises(ProtocolError):
        frame(bytes.fromhex(data))


def test_controller_fault_preserves_raw():
    raw = bytes.fromhex("80 00 01 00 01 50")
    with pytest.raises(ControllerFault) as exc:
        frame(raw)
    assert exc.value.raw == raw
    assert exc.value.message_id == 0x0080
    assert exc.value.code is None and exc.value.notes is None


def test_rich_fault_fields_and_every_split_boundary():
    # Issue 48 p56: message-ident uint16, code uint16, zero-terminated char[64].
    raw = bytes.fromhex("81 00 44 00 81 50 53 04 02 00") + b"Example fault\0".ljust(64, b"\0")
    for split in range(len(raw) + 1):
        parser = FrameBuffer()
        parser.feed(raw[:split])
        if split < len(raw):
            assert parser.pop() is None
        parser.feed(raw[split:])
        with pytest.raises(ControllerFault) as exc:
            parser.pop()
        assert exc.value.raw == raw
        assert exc.value.message_id == 0x0081
        assert exc.value.message_ident == 0x0453
        assert exc.value.code == 2 and exc.value.notes == "Example fault"
        assert "command=0x0453" in str(exc.value)


@pytest.mark.parametrize(
    "raw",
    [
        "80 00 00 00 81 50",  # Simple response must be short, even with zero length.
        "80 00 01 00 81 50 00",  # Not a long response.
        "81 00 00 00 01 50",  # Rich response must carry its 68-byte payload.
        "81 00 43 00 81 50",  # Wrong rich response length.
        "81 00 45 00 81 50",  # Wrong rich response length.
    ],
)
def test_invalid_fault_layout_is_protocol_error(raw):
    with pytest.raises(ProtocolError) as exc:
        frame(bytes.fromhex(raw))
    assert not isinstance(exc.value, ControllerFault)


def test_implemented_messages_have_manufacturer_catalogue_entries():
    from k10cr2.protocol import COMMANDS

    catalogue = json.loads((FIXTURES / "protocol-issue48-catalog.json").read_text())
    entries = catalogue["shared_index_entries"]
    assert catalogue["protocol"]["issue"] == "48" and len(entries) == 56
    by_id = {int(item["id"], 16): item for item in entries}
    assert len(by_id) == 56
    for command in COMMANDS.values():
        assert command.message_id in by_id
        if command.response_id is not None:
            assert command.response_id in by_id
    excluded = {
        key for key, value in by_id.items() if "explicitly excluded" in value["applicability"]
    }
    assert excluded == {0x04B3, 0x04B4, 0x04B5}
    assert not excluded.intersection(command.message_id for command in COMMANDS.values())


def test_saved_home_capture_decodes_without_losing_frames():
    capture = json.loads((FIXTURES / "bench-home-20261106.json").read_text())
    parser = FrameBuffer()
    frames = []
    for item in capture["apt_traffic"]:
        if item["direction"] == "in":
            parser.feed(bytes.fromhex(item["apt_hex"]))
            while (parsed := parser.pop()) is not None:
                frames.append(parsed)
    assert any(item.message_id == 0x0444 and not item.payload for item in frames)
    statuses = [decode_status(item) for item in frames if item.message_id == 0x042A]
    assert any(item.homing for item in statuses)
    assert statuses[-1].homed and not statuses[-1].busy
    assert not parser.data


def test_saved_raw_usb_capture_decodes():
    from k10cr2.transport import strip_ftdi_status

    capture = json.loads((FIXTURES / "bench-status-20261106.json").read_text())
    for transaction, expected in zip(capture["transactions"], (0x0006, 0x042A), strict=True):
        parser = FrameBuffer()
        for raw in transaction["raw_usb_reads"]:
            parser.feed(strip_ftdi_status(bytes.fromhex(raw)))
        assert parser.pop().message_id == expected
        assert parser.pop() is None


@pytest.mark.parametrize(
    ("fixture", "counts"), [("move-completed-90.hex", 12288000), ("move-completed-zero.hex", 0)]
)
def test_packaged_hardware_move_completion(fixture, counts):
    # Actual received frames from docs/bench/package-motion-20261106-143305.log.
    parsed = frame(bytes.fromhex((FIXTURES / fixture).read_text()))
    assert parsed.message_id == 0x0464 and len(parsed.payload) == 14
    channel, position, unused, bits = struct.unpack("<HiiI", parsed.payload)
    assert channel == 1 and position == counts and unused == 0
    assert bits == 0x80000400


@pytest.mark.parametrize(
    ("fixture", "counts"), [("profiled-stopped.hex", 2065104), ("ctrlc-stopped.hex", 9373739)]
)
def test_packaged_hardware_stop_completion(fixture, counts):
    # Actual 0466 replies from the same-owner stop and real console Ctrl+C tests.
    parsed = frame(bytes.fromhex((FIXTURES / fixture).read_text()))
    assert parsed.message_id == 0x0466 and len(parsed.payload) == 14
    channel, position, unused, bits = struct.unpack("<HiiI", parsed.payload)
    assert channel == 1 and position == counts and unused == 0
    assert bits == 0x80000400
