"""Documented APT setting layouts and GUI rotation rules; no hardware access.

Sources: ETN064249-D02 pp2,17-24; DOC-103274 issue48 pp44-45,77-88,111-113.
The generic wire layouts still name K10CR1. K10CR2 readbacks are required before
editing, and remain software-tested rather than bench-verified in this release.
"""

import math
import struct
from dataclasses import dataclass

from .protocol import COUNTS_PER_DEGREE, DEVICE, HOST

VELOCITY_SCALE = 7_329_109
ACCELERATION_SCALE = 1_502
MAX_ANGLE = 15_480
UNIT_FACTORS = {
    "deg": 1,
    "rad": math.pi / 180,
    "mrad": math.pi / 180 * 1e3,
    "urad": math.pi / 180 * 1e6,
    "counts": COUNTS_PER_DEGREE,
}


@dataclass(frozen=True)
class Setting:
    set_id: int
    request_id: int
    response_id: int
    format: str
    fields: tuple[str, ...]

    @property
    def size(self):
        return struct.calcsize(self.format)


SETTINGS = {
    "velocity": Setting(
        0x0413, 0x0414, 0x0415, "<HIII", ("channel", "minimum", "acceleration", "maximum")
    ),
    "jog": Setting(
        0x0416,
        0x0417,
        0x0418,
        "<HHIIIIH",
        ("channel", "mode", "step", "minimum", "acceleration", "maximum", "stop"),
    ),
    "backlash": Setting(0x043A, 0x043B, 0x043C, "<Hi", ("channel", "distance")),
    "home": Setting(
        0x0440, 0x0441, 0x0442, "<HHHIi", ("channel", "direction", "switch", "velocity", "offset")
    ),
    "buttons": Setting(
        0x04B6,
        0x04B7,
        0x04B8,
        "<HHiiHH",
        ("channel", "mode", "position1", "position2", "timeout1", "timeout2"),
    ),
}
EDITABLE = {"velocity", "jog", "backlash", "buttons"}


def short_packet(message_id, p1=1, p2=0):
    return struct.pack("<HBBBB", message_id, p1, p2, DEVICE, HOST)


def data_packet(message_id, payload):
    return struct.pack("<HHBB", message_id, len(payload), DEVICE | 0x80, HOST) + payload


def finite(value, label, low, high):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not low <= value <= high
    ):
        raise ValueError(f"{label} must be a finite number from {low:g} to {high:g}")
    return value


def integer(value, label, choices):
    if isinstance(value, bool) or value not in choices:
        raise ValueError(f"Invalid {label}")
    return int(value)


def boolean(value, label):
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be True or False")
    return value


def position_counts(angle):
    return round(finite(angle, "Total angle / degrees", -MAX_ANGLE, MAX_ANGLE) * COUNTS_PER_DEGREE)


def resolve_target(angle, current, mode="fixed", direction="quickest"):
    integer_mode = mode in {"fixed", "equivalent", "total"}
    if not integer_mode or direction not in {"forward", "reverse", "quickest"}:
        raise ValueError("Invalid rotation mode or direction")
    if mode == "fixed":
        finite(angle, "Fixed-range angle", 0, 360)
    elif mode == "equivalent":
        finite(angle, "Equivalent angle", 0, 360)
        phase = current % 360
        delta = (angle % 360 - phase) % 360
        if direction == "reverse" and delta:
            delta -= 360
        elif direction == "quickest" and delta > 180:
            delta -= 360
        angle = current + delta
    return position_counts(angle)


def display_angle(angle, mode, units="deg", decimals=6):
    if angle is None:
        return "—"
    if mode == "equivalent":
        angle %= 360
    factor = UNIT_FACTORS[units]
    value = angle * factor
    if mode == "equivalent" and round(value, decimals) >= 360 * factor:
        value = 0
    return f"{value:.{decimals}f}"


def encode_changes(group, draft, current, high_power=False):
    """Merge only editable fields into a fresh raw readback; preserve reserved values."""
    speed_limit = 20 if high_power else 10
    result = dict(current)
    if group == "velocity":
        result.update(
            minimum=0,
            maximum=round(
                finite(draft["velocity"], "Velocity / °/s", 0.005, speed_limit) * VELOCITY_SCALE
            ),
            acceleration=round(
                finite(draft["acceleration"], "Acceleration / °/s²", 0.001, 20) * ACCELERATION_SCALE
            ),
        )
    elif group == "jog":
        result.update(
            mode=integer(draft["jog_mode"], "jog mode", {1, 2}),
            stop=integer(draft["jog_stop"], "jog stop mode", {1, 2}),
            minimum=0,
            step=round(
                finite(draft["jog_step"], "Jog step / °", 1 / COUNTS_PER_DEGREE, 360)
                * COUNTS_PER_DEGREE
            ),
            maximum=round(
                finite(draft["jog_velocity"], "Jog velocity / °/s", 0.005, speed_limit)
                * VELOCITY_SCALE
            ),
            acceleration=round(
                finite(draft["jog_acceleration"], "Jog acceleration / °/s²", 0.001, 20)
                * ACCELERATION_SCALE
            ),
        )
    elif group == "backlash":
        result["distance"] = round(
            finite(draft["backlash"], "Backlash / °", 0, 360) * COUNTS_PER_DEGREE
        )
    elif group == "buttons":
        result.update(
            mode=integer(draft["button_mode"], "button mode", {1, 2}),
            position1=position_counts(draft["button_position1"]),
            position2=position_counts(draft["button_position2"]),
        )
    else:
        raise ValueError("This setting is locked or its K10CR2 mapping is unverified")
    return result


def to_display(raw):
    result = {}
    if "velocity" in raw:
        result.update(
            velocity=raw["velocity"]["maximum"] / VELOCITY_SCALE,
            acceleration=raw["velocity"]["acceleration"] / ACCELERATION_SCALE,
        )
    if "jog" in raw:
        jog = raw["jog"]
        result.update(
            jog_mode=jog["mode"],
            jog_stop=jog["stop"],
            jog_step=jog["step"] / COUNTS_PER_DEGREE,
            jog_velocity=jog["maximum"] / VELOCITY_SCALE,
            jog_acceleration=jog["acceleration"] / ACCELERATION_SCALE,
        )
    if "backlash" in raw:
        result["backlash"] = raw["backlash"]["distance"] / COUNTS_PER_DEGREE
    if "buttons" in raw:
        buttons = raw["buttons"]
        result.update(
            button_mode=buttons["mode"],
            button_position1=buttons["position1"] / COUNTS_PER_DEGREE,
            button_position2=buttons["position2"] / COUNTS_PER_DEGREE,
        )
    return {
        key: round(value, 8) if isinstance(value, float) else value for key, value in result.items()
    }
