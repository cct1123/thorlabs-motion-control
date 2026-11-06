"""Thorlabs Figure 16 layout and manual chapter 7 controls, using our APT owner."""

import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from dash import Dash, Input, Output, State, ctx, dcc, html, no_update
from flask import abort, jsonify, redirect, request, send_file

from .gui_state import MANUAL, MOTIONS, GuiController
from .settings import UNIT_FACTORS, VELOCITY_SCALE, display_angle, resolve_target, to_display

COMMANDS = (
    "connect",
    "disconnect",
    "refresh-state",
    "home",
    "move-to",
    "stop",
    "enable",
    "disable",
    "identify",
    "load-settings",
    "jog-forward",
    "jog-reverse",
)
DEVICE_FIELDS = (
    "velocity",
    "acceleration",
    "jog_step",
    "jog_mode",
    "jog_stop",
    "jog_velocity",
    "jog_acceleration",
    "backlash",
    "button_mode",
    "button_position1",
    "button_position2",
)
HOST_FIELDS = (
    "rotation_mode",
    "rotation_direction",
    "units",
    "decimals",
    "high_power",
    "drive1",
    "drive2",
    "drive3",
    "drive4",
)
GROUP_FIELDS = {
    "velocity": DEVICE_FIELDS[:2],
    "jog": DEVICE_FIELDS[2:7],
    "backlash": ("backlash",),
    "buttons": DEVICE_FIELDS[8:],
}
UNAVAILABLE = "Unavailable: K10CR2 command mapping is not verified. No command will be sent."
MANUAL_URL = (
    "https://media.thorlabs.com/globalassets/items/k/k1/k10/k10cr2_m/etn064249-d02.pdf?v=0116115646"
)


def field(label, name, *, value=None, options=None, disabled=False, **kwargs):
    kwargs.setdefault("step", "any")
    control = (
        dcc.Dropdown(id=name, options=options, value=value, clearable=False, disabled=disabled)
        if options is not None
        else dcc.Input(id=name, type="number", value=value, disabled=disabled, **kwargs)
    )
    return html.Div([html.Label(label, htmlFor=name), control], className="field")


def group(title, children, note=None):
    return html.Fieldset(
        [html.Legend(title), *children, html.P(note, className="hint") if note else None]
    )


def action(label, name, glyph, *, disabled=True, cls="", **kwargs):
    return html.Button(
        [html.Span(glyph, className="glyph", **{"aria-hidden": "true"}), html.Span(label)],
        id=name,
        n_clicks=0,
        disabled=disabled,
        className=f"tool {cls}",
        title=label,
        **kwargs,
    )


def settings_panel():
    result = [
        html.Div(
            [
                html.H2("Actuator settings"),
                html.Button(
                    "Stop", id="settings-dialog-stop", disabled=True, className="button dialog-stop"
                ),
                html.Button(
                    "×", id="cancel-settings", title="Cancel and close settings", className="close"
                ),
            ],
            className="dialog-title",
        ),
        html.P("K10CR2 Rotation Stage · Current device settings", className="settings-device"),
        html.Div(
            [
                html.Button(
                    "Load from device",
                    id="load-settings",
                    n_clicks=0,
                    disabled=True,
                    className="button",
                ),
                html.Span(id="settings-read-status", className="hint"),
            ],
            className="read-toolbar",
        ),
        dcc.Tabs(
            id="settings-tab",
            value="moves",
            children=[
                dcc.Tab(
                    label="Moves / Jogs",
                    value="moves",
                    children=html.Div(
                        [
                            group(
                                "Position display",
                                [
                                    field(
                                        "Units",
                                        "units",
                                        value="deg",
                                        options=[
                                            {"label": label, "value": value}
                                            for label, value in (
                                                ("Degrees / °", "deg"),
                                                ("Radians / rad", "rad"),
                                                ("Milliradians / mrad", "mrad"),
                                                ("Microradians / μrad", "urad"),
                                                ("Device counts", "counts"),
                                            )
                                        ],
                                    ),
                                    field(
                                        "Decimal places", "decimals", value=6, min=0, max=9, step=1
                                    ),
                                    html.Div(id="display-factor", className="hint"),
                                ],
                                "Display units affect the readout. Motion inputs remain "
                                "in degrees.",
                            ),
                            group(
                                "Jog",
                                [
                                    field("Step size / °", "jog_step"),
                                    field(
                                        "Mode",
                                        "jog_mode",
                                        options=[
                                            {"label": "Single step", "value": 2},
                                            {"label": "Continuous", "value": 1},
                                        ],
                                    ),
                                    field(
                                        "Stop",
                                        "jog_stop",
                                        options=[
                                            {"label": "Profiled", "value": 2},
                                            {"label": "Immediate", "value": 1},
                                        ],
                                    ),
                                    field("Maximum velocity / °/s", "jog_velocity"),
                                    field("Acceleration / °/s²", "jog_acceleration"),
                                ],
                                "One step per click; hold to repeat. Continuous mode "
                                "stops on release.",
                            ),
                            group(
                                "Velocity",
                                [
                                    field("Maximum velocity / °/s", "velocity"),
                                    field("Acceleration / °/s²", "acceleration"),
                                    html.P("Minimum velocity: 0 (locked)", className="hint"),
                                ],
                            ),
                            group(
                                "Backlash",
                                [field("Distance / °", "backlash")],
                                "Zero disables backlash compensation.",
                            ),
                            group(
                                "Velocity profile",
                                [
                                    field(
                                        "Profile",
                                        "profile",
                                        options=[
                                            {"label": "Trapezoidal", "value": 0},
                                            {"label": "S-curve", "value": 1},
                                        ],
                                        disabled=True,
                                    ),
                                    field("Bow index / 1–18", "bow-index", disabled=True),
                                ],
                                UNAVAILABLE,
                            ),
                        ],
                        className="settings-grid",
                    ),
                ),
                dcc.Tab(
                    label="Rotation",
                    value="rotation",
                    children=html.Div(
                        [
                            group(
                                "Rotation",
                                [
                                    field(
                                        "Rotation mode",
                                        "rotation_mode",
                                        value="fixed",
                                        options=[
                                            {"label": "Fixed range · 0 to 360°", "value": "fixed"},
                                            {
                                                "label": "Equivalent angle · wraps display",
                                                "value": "equivalent",
                                            },
                                            {
                                                "label": "Total angle · accumulated rotation",
                                                "value": "total",
                                            },
                                        ],
                                    ),
                                    field(
                                        "Rotation direction (equivalent angle)",
                                        "rotation_direction",
                                        value="quickest",
                                        options=[
                                            {"label": label, "value": value}
                                            for label, value in (
                                                ("Rotate quickest", "quickest"),
                                                ("Rotate forwards", "forward"),
                                                ("Rotate reverse", "reverse"),
                                            )
                                        ],
                                    ),
                                ],
                                "These are host control/display rules. The counted range "
                                "is limited to "
                                "±15,480°; home before exceeding it (manual p20).",
                            ),
                        ],
                        className="settings-grid",
                    ),
                ),
                dcc.Tab(
                    label="Stage / Axis",
                    value="stage",
                    children=html.Div(
                        [
                            group(
                                "Homing settings",
                                [
                                    html.Dl(id="homing-readback", className="readback"),
                                ],
                                "Locked: the manual p21 instructs that stage-specific parameters "
                                "should not be altered after defaults are established.",
                            ),
                            group(
                                "Hardware limit switches",
                                [
                                    html.P("Reverse home datum · switch makes on contact."),
                                    html.P(
                                        "The rotary stage has one home switch; it can "
                                        "rotate through the datum.",
                                        className="hint",
                                    ),
                                    field(
                                        "Forward limit",
                                        "forward-limit-setting",
                                        disabled=True,
                                        options=[{"label": "Switch makes", "value": 2}],
                                    ),
                                    field(
                                        "Reverse limit",
                                        "reverse-limit-setting",
                                        disabled=True,
                                        options=[{"label": "Switch makes", "value": 2}],
                                    ),
                                ],
                                "Stage parameters remain unchanged; status lamps show "
                                "reported bits.",
                            ),
                            group(
                                "Limit switch software policy",
                                [
                                    field(
                                        "Policy",
                                        "limit-policy",
                                        disabled=True,
                                        options=[
                                            {"label": label, "value": i}
                                            for i, label in enumerate(
                                                (
                                                    "Complete moves only",
                                                    "Clip move to limit",
                                                    "Ignore",
                                                )
                                            )
                                        ],
                                    )
                                ],
                                "Locked stage configuration. Fixed-range host controls "
                                "enforce 0–360° targets.",
                            ),
                        ],
                        className="settings-grid",
                    ),
                ),
                dcc.Tab(
                    label="Advanced",
                    value="advanced",
                    children=html.Div(
                        [
                            group(
                                "Power",
                                [
                                    field(
                                        "Resting power / %",
                                        "rest-power",
                                        disabled=True,
                                        min=1,
                                        max=100,
                                    )
                                ],
                                UNAVAILABLE
                                + " Moving power is automatic and is not adjustable (manual p22).",
                            ),
                            group(
                                "Button control",
                                [
                                    field(
                                        "Mode",
                                        "button_mode",
                                        options=[
                                            {"label": "Move By (Jog)", "value": 1},
                                            {"label": "Move To", "value": 2},
                                        ],
                                    ),
                                    field("Left / Top button position / °", "button_position1"),
                                    field("Right / Lower button position / °", "button_position2"),
                                ],
                                "Changes hardware-button behavior. Hold both physical "
                                "buttons for ~2 s to home.",
                            ),
                            group(
                                "Drive array velocities",
                                [
                                    field(
                                        f"Velocity {i} / °/s",
                                        f"drive{i}",
                                        value=speed,
                                        min=0.005,
                                        max=20,
                                    )
                                    for i, speed in enumerate((2.5, 5, 7.5, 10), 1)
                                ],
                                "Four speeds in both directions. Host session only; not "
                                "saved to device EEPROM.",
                            ),
                            group(
                                "Hardware triggering",
                                [
                                    field(
                                        "Input triggers",
                                        "trigger-input",
                                        disabled=True,
                                        options=[
                                            {"label": label, "value": i}
                                            for i, label in enumerate(
                                                (
                                                    "Disabled",
                                                    "Relative move (Fall)",
                                                    "Relative move (Rise)",
                                                    "Absolute move (Fall)",
                                                    "Absolute move (Rise)",
                                                    "Home move (Fall)",
                                                    "Home move (Rise)",
                                                )
                                            )
                                        ],
                                    ),
                                    field(
                                        "Output triggers",
                                        "trigger-output",
                                        disabled=True,
                                        options=[
                                            {"label": label, "value": i}
                                            for i, label in enumerate(
                                                (
                                                    "Disabled",
                                                    "In motion (Low)",
                                                    "In motion (High)",
                                                    "Move completed (Low)",
                                                    "Move completed (High)",
                                                    "At maximum velocity (Low)",
                                                    "At maximum velocity (High)",
                                                )
                                            )
                                        ],
                                    ),
                                    field(
                                        "Move relative distance / °",
                                        "trigger-relative",
                                        disabled=True,
                                    ),
                                    field(
                                        "Move absolute position / °",
                                        "trigger-absolute",
                                        disabled=True,
                                    ),
                                ],
                                UNAVAILABLE,
                            ),
                        ],
                        className="settings-grid",
                    ),
                ),
                dcc.Tab(
                    label="Defaults",
                    value="defaults",
                    children=html.Div(
                        [
                            group(
                                "Reset stage to factory defaults",
                                [
                                    html.P(
                                        "The manufacturer GUI can reset persisted device "
                                        "parameters. "
                                        "Defaults take effect at the next power-up (manual p24)."
                                    ),
                                    html.Button(
                                        "Reset Stage To Factory Defaults",
                                        disabled=True,
                                        className="button",
                                        title=UNAVAILABLE,
                                    ),
                                ],
                                UNAVAILABLE + " No reset packet is guessed or sent.",
                            ),
                            group(
                                "Startup settings",
                                [
                                    html.P(
                                        "This application reads existing device state. "
                                        "It does not load an SDK profile "
                                        "or automatically overwrite startup settings. "
                                        "Use the explicit persistence "
                                        "checkbox to request EEPROM storage for "
                                        "supported settings groups."
                                    )
                                ],
                            ),
                        ],
                        className="settings-grid",
                    ),
                ),
            ],
        ),
        dcc.Checklist(
            id="high_power",
            options=[
                {
                    "label": " USB supply is rated ≥900 mA (permits speeds above 10 °/s)",
                    "value": "yes",
                }
            ],
            value=[],
        ),
        html.Div(
            [
                html.Div(
                    [
                        html.Label("Apply device groups"),
                        dcc.Checklist(
                            id="apply-groups",
                            options=[
                                {"label": label, "value": value}
                                for label, value in (
                                    (" Velocity", "velocity"),
                                    (" Jog", "jog"),
                                    (" Backlash", "backlash"),
                                    (" Hardware buttons", "buttons"),
                                )
                            ],
                            value=[],
                            inline=True,
                        ),
                    ]
                ),
                dcc.Checklist(
                    id="persist",
                    options=[
                        {"label": " Persist selected settings to hardware (EEPROM)", "value": "yes"}
                    ],
                    value=[],
                ),
            ],
            className="apply-options",
        ),
        html.P(
            "Load readbacks before editing device settings. New command paths require bench "
            "validation. Unavailable controls do not access hardware.",
            className="hint",
        ),
        html.Div(
            [
                html.A("Manual · chapter 7 ↗", href="/manual#page=18", target="_blank"),
                html.Button(
                    "Apply display / rotation / drive", id="apply-display", className="button"
                ),
                html.Button("Reset edits", id="reset-settings", className="button"),
                html.Button(
                    "Apply device settings", id="apply-settings", className="button primary"
                ),
                html.Button("OK", id="settings-ok", className="button"),
            ],
            className="dialog-actions",
        ),
    ]
    # Mount all fields once: switching tabs must not remove callback State inputs.
    tabs = result[3]
    panels = []
    for tab in tabs.children:
        content = tab.children
        tab.children = None
        panels.append(
            html.Div(
                content,
                id=f"settings-page-{tab.value}",
                style={} if tab.value == "moves" else {"display": "none"},
            )
        )
    result[4:4] = panels
    return result


def controls(snapshot, target, _step=None):
    status = snapshot["status"]
    busy = snapshot["busy"]
    idle = snapshot["connected"] and not busy and not snapshot["closing"]
    ready = (
        idle
        and not snapshot.get("unknown", False)
        and not snapshot["recovery"]
        and not status.get("moving")
        and not status.get("homing")
        and not status.get("motion_error")
        and not status.get("raw_bits", 0) & 0x40000000
    )
    disabled = {name: True for name in (*COMMANDS, *MANUAL)}
    disabled.update(
        connect=snapshot["connected"] or bool(busy) or snapshot["closing"],
        disconnect=not idle
        or snapshot.get("unknown", False)
        or status.get("moving", False)
        or status.get("homing", False),
        home=not ready,
        enable=not ready or status.get("enabled", False),
        disable=not ready or not status.get("enabled", False),
        identify=not ready,
    )
    disabled["stop"] = (
        not snapshot["connected"]
        or snapshot["closing"]
        or busy in {"connect", "disconnect", "stop"}
    )
    for name in ("refresh-state", "load-settings"):
        disabled[name] = not ready
    error = ""
    ui = snapshot["ui"]
    try:
        resolve_target(
            target,
            snapshot["position"]["degrees"] if snapshot["position"] else 0,
            ui["rotation_mode"],
            ui["rotation_direction"],
        )
    except (ValueError, TypeError) as exc:
        error = str(exc)
    disabled["move-to"] = not ready or not status.get("homed") or bool(error)
    for name in MANUAL:
        disabled[name] = (
            not ready
            or not status.get("homed")
            or not status.get("enabled")
            or (name.startswith("jog-") and "jog" not in snapshot["settings"])
        )
    return disabled, error


def create_app(controller=None, *, serial=None):
    owner = controller if controller is not None else GuiController()
    app = Dash(
        __name__,
        title="Cage Rotator · K10CR2",
        update_title=None,
        assets_folder=str(Path(__file__).parent / "assets"),
    )
    app.server.config["GUI_CONTROLLER"] = owner

    @app.server.before_request
    def local_requests_only():
        if urlsplit(request.host_url).hostname not in {"127.0.0.1", "localhost"}:
            abort(403)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            if request.headers.get("Sec-Fetch-Site") == "cross-site":
                abort(403)
            origin = request.headers.get("Origin")
            if origin and origin != request.host_url.rstrip("/"):
                abort(403)

    @app.server.get("/api/status")
    def api_status():
        return jsonify(owner.snapshot())

    @app.server.post("/api/hold")
    def api_hold():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            abort(400)
        try:
            message = owner.hold(
                payload.get("action"),
                payload.get("token"),
                payload.get("command"),
                payload.get("index", 0),
            )
            return jsonify(message=message)
        except (ValueError, TypeError) as exc:
            return jsonify(message=str(exc)), 400

    @app.server.get("/manual")
    def manual():
        path = (
            Path(__file__).resolve().parents[2]
            / "docs/hardware/K10CR2-user-guide-ETN064249-D02.pdf"
        )
        packaged = Path(__file__).parent / "manuals/K10CR2-user-guide-ETN064249-D02.pdf"
        if packaged.is_file():
            path = packaged
        elif not path.is_file():
            return redirect(MANUAL_URL)
        return send_file(path, mimetype="application/pdf")

    def drive(direction):
        return html.Div(
            [
                html.Button(
                    html.Span(className="drive-bar"),
                    id=f"{direction}-{i}",
                    disabled=True,
                    className=f"drive-division division-{i}",
                    title=f"Hold to drive {direction} · velocity {i + 1}",
                    **{
                        "data-hold": f"drive-{direction}",
                        "data-index": str(i),
                        "aria-label": f"Drive {direction}, speed {i + 1}",
                    },
                )
                for i in (range(3, -1, -1) if direction == "forward" else range(4))
            ],
            className="drive-stack",
        )

    app.layout = html.Main(
        [
            dcc.Store(id="panel", data=None),
            dcc.Store(id="settings-revision", data=-1),
            html.Header(
                [
                    html.Div(
                        [
                            html.Div("INSTRUMENT CONTROL", className="eyebrow"),
                            html.H1("Rotation mount"),
                        ]
                    ),
                    html.A("User guide ↗", href="/manual", target="_blank"),
                ],
                className="page-header",
            ),
            html.Section(
                [
                    html.Div(
                        [
                            html.Label("K10CR2 serial", htmlFor="serial"),
                            dcc.Input(
                                id="serial",
                                type="text",
                                value=serial or "",
                                maxLength=8,
                                placeholder="Eight-digit serial",
                                className="serial-input",
                            ),
                        ]
                    ),
                    html.Button("Connect", id="connect", className="button primary"),
                    html.Button("Disconnect", id="disconnect", disabled=True, className="button"),
                    html.Button(
                        "Refresh status", id="refresh-state", disabled=True, className="button"
                    ),
                    html.Button("Close web UI", id="close-gui", className="button"),
                ],
                className="connection-bar",
            ),
            html.Section(
                [
                    html.Div(
                        [
                            html.A(
                                "i",
                                href="/manual#page=18",
                                target="_blank",
                                className="info-icon",
                                title="Open the manufacturer manual",
                            ),
                            html.Strong("Cage Rotator"),
                            html.Span("S/N —", id="instrument-serial"),
                        ],
                        className="instrument-title",
                    ),
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.Button(
                                        [
                                            html.Span("—", id="angle"),
                                            html.Span("°", id="angle-unit"),
                                        ],
                                        id="position-open",
                                        className="position-display",
                                        title="Open Move settings",
                                        **{"aria-label": "Position; open Move settings"},
                                    ),
                                    html.Div(
                                        [
                                            html.Div(
                                                [
                                                    html.Div("Travel: 360° continuous"),
                                                    html.Div("Vel: —", id="velocity-summary"),
                                                    html.Div("Acc: —", id="acceleration-summary"),
                                                    html.Div("Jog Step: —", id="jog-summary"),
                                                ],
                                                className="parameters",
                                            ),
                                            action(
                                                "Settings",
                                                "settings-open",
                                                "⚙",
                                                disabled=False,
                                                cls="settings-tool",
                                            ),
                                            action("Home", "home", "⌂"),
                                            action("Stop", "stop", "STOP", cls="stop-tool"),
                                        ],
                                        className="under-position",
                                    ),
                                ],
                                className="readout-area",
                            ),
                            action("Move ▾", "move-open", "➜", disabled=False, cls="move-tool"),
                            html.Div(
                                [
                                    drive("forward"),
                                    drive("reverse"),
                                    html.Button("Drive ▾", id="drive-open", className="tool-label"),
                                ],
                                className="drive-area",
                            ),
                            html.Div(
                                [
                                    action(
                                        "",
                                        "jog-forward",
                                        "❯❯",
                                        cls="jog-tool forward",
                                        **{"data-hold": "jog-forward", "aria-label": "Jog forward"},
                                    ),
                                    action(
                                        "",
                                        "jog-reverse",
                                        "❯❯",
                                        cls="jog-tool reverse",
                                        **{"data-hold": "jog-reverse", "aria-label": "Jog reverse"},
                                    ),
                                    html.Button("Jog ▾", id="jog-open", className="tool-label"),
                                ],
                                className="jog-area",
                            ),
                            html.Div(
                                [
                                    html.Div(
                                        [
                                            html.Span("Not Homed", id="homed-caption"),
                                            html.Span(id="homed-lamp", className="lamp unknown"),
                                        ],
                                        className="lamp-row",
                                    ),
                                    html.Div(
                                        [
                                            html.Span("Forward Limit"),
                                            html.Span(id="forward-lamp", className="lamp unknown"),
                                        ],
                                        className="lamp-row",
                                    ),
                                    html.Div(
                                        [
                                            html.Span("Reverse Limit"),
                                            html.Span(id="reverse-lamp", className="lamp unknown"),
                                        ],
                                        className="lamp-row",
                                    ),
                                    action("Enable", "enable", "▣"),
                                    action("Disable", "disable", "▣"),
                                    action("Identify", "identify", "●", cls="identify-tool"),
                                ],
                                className="indicators",
                            ),
                        ],
                        className="instrument-controls",
                    ),
                    html.Div(
                        [
                            html.Span("Disconnected", id="operation"),
                            html.Span("Actuator: K10CR2 Rotation Stage"),
                        ],
                        className="instrument-status",
                    ),
                ],
                className="instrument",
            ),
            html.Div(
                [html.Span(id="connection-label"), html.Span(id="readback-age")],
                className="readback-meta",
            ),
            html.Div(id="error", role="alert", className="error-box", style={"display": "none"}),
            html.Div(
                "Ready. Connect reads state; Home and motion require explicit actions.",
                id="request-result",
                role="status",
                className="request-result",
            ),
            html.P(
                "Counted position, not encoder measured. Hold controls require a live "
                "browser heartbeat; "
                "release, loss of focus, or heartbeat expiry requests Stop. USB latency "
                "still applies.",
                className="hint control-note",
            ),
            html.Details(
                [
                    html.Summary("Activity & device details"),
                    html.Div(id="device-details"),
                    html.Div(id="activity", className="activity"),
                ],
                className="activity-panel",
            ),
            html.Footer(
                "Our WinUSB / APT communication · No Kinesis or SDK · "
                "GUI source: Thorlabs ETN064249-D02, chapter 7",
                className="page-footer",
            ),
            html.Div(id="backdrop", className="backdrop", style={"display": "none"}),
            html.Section(
                [
                    html.Div(
                        [
                            html.H2("Move settings"),
                            html.Button(
                                "Stop",
                                id="move-dialog-stop",
                                disabled=True,
                                className="button dialog-stop",
                            ),
                            html.Button(
                                "×",
                                id="cancel-move",
                                className="close",
                                title="Close Move settings",
                            ),
                        ],
                        className="dialog-title",
                    ),
                    field("Absolute position / °", "target", value=0),
                    field("Maximum velocity / °/s", "move_velocity"),
                    field("Acceleration / °/s²", "move_acceleration"),
                    html.Div(id="move-preview", className="hint"),
                    html.P(
                        "The selected rotation mode and direction determine the route. "
                        "Blank profile fields preserve device settings; "
                        "load readbacks before editing.",
                        className="hint",
                    ),
                    html.Button(
                        "Move to position →",
                        id="move-to",
                        disabled=True,
                        className="button primary",
                    ),
                    html.Button("Apply profile", id="apply-move", className="button"),
                    html.Button("Reset", id="reset-move", className="button"),
                ],
                id="move-dialog",
                role="dialog",
                **{"aria-modal": "true", "aria-label": "Move settings"},
                className="dialog move-dialog",
                style={"display": "none"},
            ),
            html.Section(
                settings_panel(),
                id="settings-dialog",
                role="dialog",
                **{"aria-modal": "true", "aria-label": "Actuator settings"},
                className="dialog settings-dialog",
                style={"display": "none"},
            ),
            dcc.Interval(id="refresh", interval=300, n_intervals=0),
        ],
        className="page",
    )

    buttons = (
        "connect",
        "disconnect",
        "refresh-state",
        "home",
        "move-to",
        "stop",
        "enable",
        "disable",
        "identify",
        "load-settings",
        "close-gui",
        "move-open",
        "position-open",
        "settings-open",
        "drive-open",
        "jog-open",
        "cancel-move",
        "cancel-settings",
        "apply-settings",
        "settings-ok",
        "apply-display",
        "apply-move",
        "settings-dialog-stop",
        "move-dialog-stop",
    )

    @app.callback(
        Output("request-result", "children"),
        Output("panel", "data"),
        Output("settings-tab", "value"),
        [Input(name, "n_clicks") for name in buttons],
        State("serial", "value"),
        State("target", "value"),
        State("move_velocity", "value"),
        State("move_acceleration", "value"),
        *[State(name, "value") for name in DEVICE_FIELDS + HOST_FIELDS],
        State("apply-groups", "value"),
        State("persist", "value"),
        State("panel", "data"),
        prevent_initial_call=True,
    )
    def dispatch(*values):
        values = values[len(buttons) :]
        serial_value, target, move_velocity, move_acceleration = values[:4]
        fields = dict(zip(DEVICE_FIELDS + HOST_FIELDS, values[4:-3], strict=True))
        groups, persist, panel = values[-3:]
        name = ctx.triggered_id
        if name in {"move-open", "position-open", "settings-open", "jog-open", "drive-open"}:
            snapshot = owner.snapshot()
            if (
                snapshot["busy"] in MOTIONS
                or snapshot["status"].get("moving")
                or snapshot["status"].get("homing")
            ):
                return (
                    "Motion is active. Stop remains available on the main panel.",
                    None,
                    no_update,
                )
        if name in {"move-open", "position-open"}:
            return no_update, "move", no_update
        if name in {"settings-open", "jog-open", "drive-open"}:
            return no_update, "settings", "advanced" if name == "drive-open" else "moves"
        if name in {"cancel-move", "cancel-settings"}:
            return no_update, None, no_update
        try:
            if name == "apply-display" or (name == "settings-ok" and not groups):
                ui = {key: fields[key] for key in HOST_FIELDS[:4]}
                ui.update(
                    high_power="yes" in fields["high_power"],
                    drives=[fields[f"drive{i}"] for i in range(1, 5)],
                )
                message = owner.set_display(ui)
            elif name in {"apply-settings", "settings-ok"}:
                changes = {
                    group: {key: fields[key] for key in GROUP_FIELDS[group]}
                    for group in groups
                    if group in GROUP_FIELDS
                }
                message = owner.submit("configure", changes=changes, persist="yes" in persist)
            elif name == "apply-move" or (
                name == "move-to" and (move_velocity is not None or move_acceleration is not None)
            ):
                changes = {
                    "velocity": {"velocity": move_velocity, "acceleration": move_acceleration}
                }
                message = owner.submit(
                    "configure" if name == "apply-move" else "move-to",
                    target=target,
                    changes=changes,
                )
            elif name == "close-gui":
                shutdown = app.server.config.get("GUI_SHUTDOWN")
                if shutdown is None:
                    message = "Close this UI from the application hosting it."
                else:

                    def close():
                        if owner.shutdown():
                            shutdown()

                    threading.Thread(target=close, name="k10cr2-shutdown", daemon=True).start()
                    message = "Closing web UI; waiting for the USB owner. Then close this tab."
            else:
                command = (
                    "refresh"
                    if name == "refresh-state"
                    else "stop"
                    if name in {"settings-dialog-stop", "move-dialog-stop"}
                    else name
                )
                message = owner.submit(command, serial=serial_value, target=target)
        except (ValueError, TypeError, KeyError) as exc:
            message = str(exc)
        if name == "move-to" and message == "move-to requested.":
            panel = None  # Keep the main Stop control immediately accessible.
        return message, panel, no_update

    @app.callback(
        Output("backdrop", "style"),
        Output("move-dialog", "style"),
        Output("settings-dialog", "style"),
        Input("panel", "data"),
    )
    def panels(panel):
        hidden = {"display": "none"}
        return (
            {} if panel else hidden,
            {} if panel == "move" else hidden,
            {} if panel == "settings" else hidden,
        )

    @app.callback(
        *[
            Output(f"settings-page-{name}", "style")
            for name in ("moves", "rotation", "stage", "advanced", "defaults")
        ],
        Input("settings-tab", "value"),
    )
    def settings_tabs(selected):
        return [
            {} if name == selected else {"display": "none"}
            for name in ("moves", "rotation", "stage", "advanced", "defaults")
        ]

    @app.callback(
        Output("move_velocity", "value"),
        Output("move_acceleration", "value"),
        Input("reset-move", "n_clicks"),
        Input("position-open", "n_clicks"),
        Input("move-open", "n_clicks"),
    )
    def move_values(*_):
        values = to_display(owner.snapshot()["settings"])
        return values.get("velocity"), values.get("acceleration")

    @app.callback(
        *[Output(name, "value") for name in DEVICE_FIELDS + HOST_FIELDS],
        Output("settings-revision", "data"),
        Input("refresh", "n_intervals"),
        Input("reset-settings", "n_clicks"),
        Input("cancel-settings", "n_clicks"),
        State("settings-revision", "data"),
    )
    def settings_values(_ticks, _reset, _cancel, revision):
        snap = owner.snapshot()
        if revision == snap["settings_revision"] and ctx.triggered_id not in {
            "reset-settings",
            "cancel-settings",
        }:
            return [no_update] * (len(DEVICE_FIELDS + HOST_FIELDS) + 1)
        values = to_display(snap["settings"])
        ui = snap["ui"]
        values.update(
            ui,
            high_power=["yes"] if ui["high_power"] else [],
            **{f"drive{i}": speed for i, speed in enumerate(ui["drives"], 1)},
        )
        return [values.get(name) for name in DEVICE_FIELDS + HOST_FIELDS] + [
            snap["settings_revision"]
        ]

    @app.callback(
        Output("angle", "children"),
        Output("angle-unit", "children"),
        Output("instrument-serial", "children"),
        Output("operation", "children"),
        Output("connection-label", "children"),
        Output("readback-age", "children"),
        Output("homed-caption", "children"),
        Output("homed-lamp", "className"),
        Output("forward-lamp", "className"),
        Output("reverse-lamp", "className"),
        Output("velocity-summary", "children"),
        Output("acceleration-summary", "children"),
        Output("jog-summary", "children"),
        Output("error", "children"),
        Output("error", "style"),
        Output("activity", "children"),
        Output("device-details", "children"),
        Output("move-preview", "children"),
        Output("serial", "disabled"),
        Output("enable", "style"),
        Output("disable", "style"),
        Output("settings-read-status", "children"),
        Output("homing-readback", "children"),
        Output("display-factor", "children"),
        Output("settings-dialog-stop", "disabled"),
        Output("move-dialog-stop", "disabled"),
        *[Output(name, "disabled") for name in COMMANDS],
        *[
            Output(f"{direction}-{i}", "disabled")
            for direction in ("forward", "reverse")
            for i in range(4)
        ],
        Input("refresh", "n_intervals"),
        Input("target", "value"),
    )
    def render(_ticks, target):
        snap = owner.snapshot()
        owner.refresh()
        disabled, target_error = controls(snap, target)
        state, ui = snap["status"], snap["ui"]
        position = snap["position"]
        angle = position["degrees"] if position else None
        age = time.time() - snap["updated"] if snap["updated"] is not None else None
        fresh = age is not None and age < 2 and not snap["recovery"]
        bits = state.get("raw_bits", 0)
        operation = (
            "Homing"
            if snap["busy"] == "home" or state.get("homing")
            else "Moving"
            if snap["busy"] in MOTIONS or state.get("moving")
            else "Idle"
            if fresh
            else "Disconnected"
            if not snap["connected"]
            else "Unverified"
        )
        label = (
            "Physical state UNKNOWN"
            if snap["unknown"]
            else "Reconnect required"
            if snap["recovery"]
            else "Connected"
            if fresh
            else "Disconnected"
            if not snap["connected"]
            else "Readback stale"
        )

        def lamp(active):
            return "lamp unknown" if not fresh else "lamp on" if active else "lamp off"

        settings = to_display(snap["settings"])

        def summary(prefix, key, suffix):
            return f"{prefix}: {settings[key]:.4g}{suffix}" if key in settings else f"{prefix}: —"

        events = [
            html.Div(
                [html.Time(event["time"]), html.Span(event["message"])],
                className=f"event {event['level']}",
            )
            for event in snap["events"]
        ]
        home = snap["settings"].get("home")
        home_details = (
            [
                html.Dt("Direction / switch"),
                html.Dd(f"{home['direction']} / {home['switch']}"),
                html.Dt("Zero offset / °"),
                html.Dd(f"{home['offset'] / (409600 / 3):.6f}"),
                html.Dt("Velocity / °/s"),
                html.Dd(f"{home['velocity'] / VELOCITY_SCALE:.6f}"),
            ]
            if home
            else [html.Dd("Load from device to read homing settings.")]
        )
        read_status = (
            "Readbacks loaded. Device edits require an explicit group selection."
            if snap["settings"]
            else "No settings readbacks loaded."
        )
        return (
            display_angle(angle, ui["rotation_mode"], ui["units"], ui["decimals"]),
            {"deg": "°", "rad": " rad", "mrad": " mrad", "urad": " μrad", "counts": " cts"}[
                ui["units"]
            ],
            "S/N " + snap["identity"].get("serial", "—"),
            operation,
            label,
            "No readback" if age is None else f"Last counted readback: {age:.1f} s ago",
            "Homing" if state.get("homing") else "Homed" if state.get("homed") else "Not Homed",
            "lamp homing" if state.get("homing") and fresh else lamp(state.get("homed")),
            lamp(bits & 1),
            lamp(bits & 2),
            summary("Vel", "velocity", "°/s"),
            summary("Acc", "acceleration", "°/s²"),
            summary("Jog Step", "jog_step", "°"),
            snap["error"],
            {} if snap["error"] else {"display": "none"},
            events,
            html.Pre(
                str(
                    {
                        **snap["identity"],
                        "status_bits": f"{bits:#010x}",
                        "raw_counts": position["counts"] if position else None,
                    }
                )
            ),
            target_error
            or f"Rotation: {ui['rotation_mode']} · Direction: {ui['rotation_direction']}",
            snap["connected"] or bool(snap["busy"]),
            {"display": "none"} if state.get("enabled") else {},
            {} if state.get("enabled") else {"display": "none"},
            read_status,
            home_details,
            f"Factor: {UNIT_FACTORS[ui['units']]:.12g} per degree (applied display units).",
            disabled["stop"],
            disabled["stop"],
            *[disabled[name] for name in COMMANDS],
            *[
                disabled[f"drive-{direction}"]
                for direction in ("forward", "reverse")
                for _ in range(4)
            ],
        )

    return app
