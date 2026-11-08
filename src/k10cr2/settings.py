"""Documented APT setting layouts and GUI rotation rules; no hardware access.

Sources: ETN064249-D02 pp2,17-24; DOC-103274 issue48 pp44-45,77-88,111-113.
The generic wire layouts still name K10CR1. K10CR2 readbacks are required before
editing; docs/validation.md separates the tested unit/subset from unverified modes.
"""

import math

from .protocol import COUNTS_PER_DEGREE, COUNTS_PER_REVOLUTION

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


EDITABLE = {"velocity", "jog", "backlash", "buttons"}


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
    if mode not in {"fixed", "equivalent", "total"} or direction not in {
        "forward",
        "reverse",
        "quickest",
    }:
        raise ValueError("Invalid rotation mode or direction")
    if mode == "fixed":
        finite(angle, "Fixed-range angle", 0, 360)
    elif mode == "equivalent":
        finite(angle, "Equivalent angle", 0, 360)
        # Compare realizable counter phases, not floating-point degree phases.
        # Otherwise a repeated quantized target (e.g. 0.25° = 34,133 counts)
        # can select an extra full turn in one forced direction.
        current_counts = position_counts(current)
        target_phase = position_counts(angle) % COUNTS_PER_REVOLUTION
        delta = (target_phase - current_counts) % COUNTS_PER_REVOLUTION
        if direction == "reverse" and delta:
            delta -= COUNTS_PER_REVOLUTION
        elif direction == "quickest" and delta > COUNTS_PER_REVOLUTION // 2:
            delta -= COUNTS_PER_REVOLUTION
        target_counts = current_counts + delta
        if abs(target_counts) > position_counts(MAX_ANGLE):
            raise ValueError("Equivalent route exceeds the counted 43-turn bound; home first")
        return target_counts
    return position_counts(angle)


def manual_target(current_counts, direction, *, mode, step=None):
    """Resolve a bounded drive target or finite jog, entirely in integer counts."""
    if mode not in {"fixed", "total", "equivalent"}:
        raise ValueError("Invalid rotation mode")
    if isinstance(direction, bool) or direction not in {-1, 1}:
        raise ValueError("Direction must be -1 or +1")
    limit = position_counts(MAX_ANGLE)
    if abs(current_counts) > limit:
        raise RuntimeError("Counted angle exceeds the manual's 43-turn bound; home first")
    lower, upper = (0, COUNTS_PER_REVOLUTION) if mode == "fixed" else (-limit, limit)
    if current_counts < lower:
        if direction < 0:
            raise ValueError("Outside selected range; move forward toward the range")
        boundary = lower
    elif current_counts > upper:
        if direction > 0:
            raise ValueError("Outside selected range; move reverse toward the range")
        boundary = upper
    else:
        boundary = upper if direction > 0 else lower
    target = boundary if step is None else current_counts + direction * step
    if not min(current_counts, lower) <= target <= max(current_counts, upper):
        raise ValueError("Jog would exceed the selected rotation range")
    return target


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
