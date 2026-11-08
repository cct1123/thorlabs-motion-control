"""Named settings drafts shared by the Dash form and its single-operation owner."""

from .settings import UNIT_FACTORS, finite, integer, resolve_target, to_display

GROUP_FIELDS = {
    "velocity": ("velocity", "acceleration"),
    "jog": ("jog_step", "jog_mode", "jog_stop", "jog_velocity", "jog_acceleration"),
    "backlash": ("backlash",),
    "buttons": ("button_mode", "button_position1", "button_position2"),
}
DEVICE_FIELDS = tuple(field for fields in GROUP_FIELDS.values() for field in fields)
DISPLAY_FIELDS = ("rotation_mode", "rotation_direction", "units", "decimals")
DRIVE_FIELDS = tuple(f"drive{i}" for i in range(1, 5))
HOST_FIELDS = (*DISPLAY_FIELDS, "high_power", *DRIVE_FIELDS)


def default_session():
    return dict(
        rotation_mode="equivalent",
        rotation_direction="quickest",
        units="deg",
        decimals=6,
        high_power=False,
        drives=[2.5, 5, 7.5, 10],
    )


def validate_session(options):
    """Validate the full candidate before either device writes or publication."""
    candidate = dict(options)
    resolve_target(0, 0, candidate["rotation_mode"], candidate["rotation_direction"])
    if candidate["units"] not in UNIT_FACTORS:
        raise ValueError("Unknown display units")
    candidate["decimals"] = integer(candidate["decimals"], "decimal places", set(range(10)))
    if not isinstance(candidate["high_power"], bool) or len(candidate["drives"]) != 4:
        raise ValueError("Invalid drive options")
    candidate["drives"] = [
        finite(speed, "Drive velocity / °/s", 0.005, 20 if candidate["high_power"] else 10)
        for speed in candidate["drives"]
    ]
    return candidate


def session_draft(fields):
    confirmation = fields["high_power"]
    if not isinstance(confirmation, list) or any(value != "yes" for value in confirmation):
        raise ValueError("Invalid USB supply confirmation")
    return validate_session(
        {
            **{name: fields[name] for name in DISPLAY_FIELDS},
            "high_power": "yes" in confirmation,
            "drives": [fields[name] for name in DRIVE_FIELDS],
        }
    )


def changed_settings(fields, raw):
    current = to_display(raw)
    return {
        group: {key: fields[key] for key in keys}
        for group, keys in GROUP_FIELDS.items()
        if any(fields[key] != current.get(key) for key in keys)
    }


def form_values(snapshot):
    ui = snapshot["ui"]
    return {
        **to_display(snapshot["settings"]),
        **{name: ui[name] for name in DISPLAY_FIELDS},
        "high_power": ["yes"] if ui["high_power"] else [],
        **dict(zip(DRIVE_FIELDS, ui["drives"], strict=True)),
    }
