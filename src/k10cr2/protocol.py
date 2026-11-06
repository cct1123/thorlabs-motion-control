"""APT wire contract audited against DOC-103274 issue 48; see docs/commands.md."""

import math
import struct
from dataclasses import dataclass

COUNTS_PER_REVOLUTION = 49_152_000
COUNTS_PER_DEGREE = COUNTS_PER_REVOLUTION / 360
HOST = 0x01
DEVICE = 0x50
CHANNEL = 1


class ProtocolError(RuntimeError):
    """Malformed or misaddressed APT data."""


class ControllerFault(ProtocolError):
    """Controller-originated fault, including the complete response bytes."""

    def __init__(self, raw: bytes):
        self.raw = raw
        self.message_id = struct.unpack_from("<H", raw)[0]
        self.message_ident: int | None = None
        self.code: int | None = None
        self.notes: str | None = None
        details = ""
        if self.message_id == 0x0081:
            self.message_ident, self.code = struct.unpack_from("<HH", raw, 6)
            self.notes = raw[10:74].split(b"\0", 1)[0].decode("ascii", errors="replace")
            details = (
                f" (command={self.message_ident:#06x}, code={self.code:#06x}, notes={self.notes!r})"
            )
        # No documented code layout is inferred from HW_RESPONSE's header parameters.
        super().__init__(f"Controller fault{details}: {raw.hex(' ')}")


@dataclass(frozen=True)
class Command:
    message_id: int
    param1: int
    param2: int
    response_id: int | None
    response_size: int


COMMANDS = {
    "info": Command(0x0005, 0, 0, 0x0006, 84),
    "status": Command(0x0429, 1, 0, 0x042A, 6),
    "position": Command(0x0411, 1, 0, 0x0412, 6),
    "enable": Command(0x0210, 1, 1, None, 0),
    "home": Command(0x0443, 1, 0, 0x0444, 0),
    "move": Command(0x0453, 1, 0, 0x0464, 14),
    "stop": Command(0x0465, 1, 2, 0x0466, 14),
}
RESPONSE_SIZES = {c.response_id: c.response_size for c in COMMANDS.values() if c.response_id}
FAULT_RESPONSE_SIZES = {0x0080: 0, 0x0081: 68}


def request(name: str, *, counts: int | None = None) -> bytes:
    command = COMMANDS[name]
    if name == "move":
        if isinstance(counts, bool) or not isinstance(counts, int):
            raise ValueError("Target counts must be a signed 32-bit integer")
        if not -(2**31) <= counts < 2**31:
            raise ValueError("Target counts exceed signed 32-bit range")
        return struct.pack("<HHBBHi", command.message_id, 6, DEVICE | 0x80, HOST, CHANNEL, counts)
    if counts is not None:
        raise ValueError("Counts apply only to an absolute move")
    return struct.pack("<HBBBB", command.message_id, command.param1, command.param2, DEVICE, HOST)


def angle_to_counts(angle: float) -> int:
    if isinstance(angle, bool) or not isinstance(angle, (int, float)):
        raise ValueError("Angle must be a finite number in [0, 360) degrees")
    if not math.isfinite(angle) or not 0 <= angle < 360:
        raise ValueError("Angle must be a finite number in [0, 360) degrees")
    counts = round(angle * COUNTS_PER_REVOLUTION / 360)
    if counts >= COUNTS_PER_REVOLUTION:
        raise ValueError("Angle rounds to a full revolution; choose a smaller target")
    return counts


@dataclass(frozen=True)
class Frame:
    message_id: int
    param1: int
    param2: int
    payload: bytes
    raw: bytes


class FrameBuffer:
    """One persistent receive buffer; never flush a partial APT frame."""

    def __init__(self):
        self.data = bytearray()

    def feed(self, data: bytes) -> None:
        self.data.extend(data)

    def pop(self) -> Frame | None:
        if len(self.data) < 6:
            return None
        msg, p1, p2, dest, source = struct.unpack_from("<HBBBB", self.data)
        if dest not in (HOST, HOST | 0x80) or source != DEVICE:
            raise ProtocolError(f"Unexpected APT addresses: {bytes(self.data[:6]).hex(' ')}")
        length = (p1 | p2 << 8) if dest & 0x80 else 0
        if length > 1024:
            raise ProtocolError(f"APT payload exceeds 1024 bytes: {length}")
        expected = RESPONSE_SIZES.get(msg, FAULT_RESPONSE_SIZES.get(msg))
        if expected is not None and (length != expected or bool(dest & 0x80) != bool(expected)):
            raise ProtocolError(f"Unexpected length/header for reply {msg:#06x}: {length}")
        if len(self.data) < 6 + length:
            return None
        raw = bytes(self.data[: 6 + length])
        del self.data[: 6 + length]
        frame = Frame(msg, p1, p2, raw[6:], raw)
        if msg in (0x0080, 0x0081):
            raise ControllerFault(raw)
        if msg in RESPONSE_SIZES and msg != 0x0006:
            channel = struct.unpack_from("<H", frame.payload)[0] if length else p1
            if channel != CHANNEL:
                raise ProtocolError(f"Unexpected channel {channel} in reply {msg:#06x}")
            if not length and p2:
                raise ProtocolError("Unexpected home-complete parameter")
        return frame


@dataclass(frozen=True)
class Identity:
    model: str
    serial: str
    firmware: str
    hardware_revision: int
    modification_state: int
    channels: int


@dataclass(frozen=True)
class Status:
    raw_bits: int
    moving: bool
    homing: bool
    homed: bool
    enabled: bool
    motion_error: bool

    @property
    def busy(self) -> bool:
        return self.moving or self.homing


@dataclass(frozen=True)
class Position:
    counts: int
    degrees: float


def decode_identity(frame: Frame) -> Identity:
    if frame.message_id != 0x0006 or len(frame.payload) != 84:
        raise ProtocolError("Expected hardware-information response")
    data = frame.payload
    try:
        model = data[4:12].decode("ascii").rstrip("\0 ")
    except UnicodeDecodeError as exc:
        raise ProtocolError("Non-ASCII model number") from exc
    return Identity(
        model,
        str(struct.unpack_from("<I", data)[0]),
        f"{data[16]}.{data[15]}.{data[14]}",
        *struct.unpack_from("<HHH", data, 78),
    )


def decode_status(frame: Frame) -> Status:
    if frame.message_id != 0x042A or len(frame.payload) != 6:
        raise ProtocolError("Expected channel status response")
    bits = struct.unpack_from("<I", frame.payload, 2)[0]
    return Status(
        bits,
        bool(bits & 0xF0),
        bool(bits & 0x200),
        bool(bits & 0x400),
        bool(bits & 0x80000000),
        bool(bits & 0x4000),
    )


def decode_position(frame: Frame) -> Position:
    if frame.message_id != 0x0412 or len(frame.payload) != 6:
        raise ProtocolError("Expected position response")
    counts = struct.unpack_from("<i", frame.payload, 2)[0]
    return Position(counts, counts * 360 / COUNTS_PER_REVOLUTION)
