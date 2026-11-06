"""Local Dash interface for the WinUSB controller; startup never accesses USB."""

import argparse
import logging
import math
import threading
import time
from pathlib import Path
from urllib.parse import quote, urlsplit

from dash import Dash, Input, Output, State, ctx, dcc, html
from flask import abort, jsonify, request
from werkzeug.serving import make_server

from .gui_state import MOTIONS, GuiController
from .protocol import angle_to_counts

COMMANDS = (
    "connect",
    "disconnect",
    "refresh-state",
    "home",
    "move-to",
    "step-minus",
    "step-plus",
    "stop",
)


def dial_source(angle):
    ticks = "".join(
        f'<line x1="{120 + 91 * math.sin(math.radians(a)):.2f}" '
        f'y1="{120 - 91 * math.cos(math.radians(a)):.2f}" '
        f'x2="{120 + 99 * math.sin(math.radians(a)):.2f}" '
        f'y2="{120 - 99 * math.cos(math.radians(a)):.2f}" stroke="#94a3a9"/>'
        for a in range(0, 360, 15)
    )
    pointer = ""
    if angle is not None and math.isfinite(angle):
        radians = math.radians(angle)
        pointer = (
            f'<line x1="120" y1="120" x2="{120 + 72 * math.sin(radians):.2f}" '
            f'y2="{120 - 72 * math.cos(radians):.2f}" stroke="#136f72" '
            'stroke-width="5" stroke-linecap="round"/>'
        )
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 240">'
        '<circle cx="120" cy="120" r="99" fill="#f7f9f8" stroke="#d5dfdf"/>'
        f'{ticks}<circle cx="120" cy="120" r="79" fill="white" stroke="#e2e9e8"/>'
        f'{pointer}<circle cx="120" cy="120" r="7" fill="#24434c"/>'
        '<g text-anchor="middle" font-family="sans-serif" font-size="10" fill="#637780">'
        '<text x="120" y="12">0°</text><text x="228" y="124">90°</text>'
        '<text x="120" y="236">180°</text><text x="12" y="124">270°</text>'
        "</g></svg>"
    )
    return "data:image/svg+xml;charset=utf-8," + quote(svg)


def button(label, name, variant=""):
    return html.Button(
        label, id=name, n_clicks=0, disabled=name != "connect", className=f"button {variant}"
    )


def controls(snapshot, target, step):
    """Presentation gating; the worker independently validates every request."""
    connected, busy = snapshot["connected"], snapshot["busy"]
    status = snapshot["status"]
    idle = connected and not busy and not snapshot["closing"]
    ready = (
        idle
        and not snapshot["recovery"]
        and not snapshot["unknown"]
        and not status.get("moving")
        and not status.get("homing")
        and not status.get("motion_error")
        and not status.get("raw_bits", 0) & 0x40000000
    )
    disabled = {name: True for name in COMMANDS}
    disabled.update(
        connect=connected or bool(busy) or snapshot["closing"],
        disconnect=not idle
        or snapshot["unknown"]
        or bool(status.get("moving") or status.get("homing")),
        home=not ready,
        stop=not connected or (bool(busy) and busy not in MOTIONS) or snapshot["closing"],
    )
    disabled["refresh-state"] = not idle or snapshot["recovery"]
    error = ""
    try:
        angle_to_counts(target)
    except (ValueError, TypeError) as exc:
        error = str(exc)
    disabled["move-to"] = not ready or not status.get("homed") or bool(error)
    for name, sign in (("step-minus", -1), ("step-plus", 1)):
        try:
            if (
                isinstance(step, bool)
                or not isinstance(step, (int, float))
                or not math.isfinite(step)
                or step <= 0
            ):
                raise ValueError
            angle_to_counts(snapshot["position"]["degrees"] + sign * step)
            disabled[name] = not ready or not status.get("homed")
        except (ValueError, TypeError, KeyError):
            pass
    return disabled, error


def create_app(controller=None, *, serial=None):
    controller = controller if controller is not None else GuiController()
    app = Dash(
        __name__,
        title="K10CR2 · Rotation mount",
        update_title=None,
        assets_folder=str(Path(__file__).parent / "assets"),
    )
    app.server.config["GUI_CONTROLLER"] = controller

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
        return jsonify(controller.snapshot())

    app.layout = html.Main(
        [
            html.Header(
                [
                    html.Div(
                        [
                            html.Span("◉", className="brand-mark"),
                            html.Div(
                                [
                                    html.Div("INSTRUMENT CONTROL", className="eyebrow"),
                                    html.H1("Rotation mount"),
                                ]
                            ),
                        ],
                        className="brand",
                    ),
                    html.Div(
                        [
                            html.Span(id="connection-dot", className="status-dot"),
                            html.Span("Disconnected", id="connection-label"),
                        ],
                        className="connection-pill",
                    ),
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
                                placeholder="Eight-digit serial",
                                maxLength=8,
                                className="serial-input",
                                persistence=True,
                                persistence_type="local",
                            ),
                        ],
                        className="device-select",
                    ),
                    html.Div(
                        [
                            button("Connect", "connect", "primary"),
                            button("Disconnect", "disconnect", "quiet"),
                            button("Refresh status", "refresh-state", "quiet"),
                            html.Button(
                                "Close web UI",
                                id="close-gui",
                                n_clicks=0,
                                className="button quiet",
                                title="Stop active motion, release USB, and shut down this web UI",
                            ),
                        ],
                        className="device-actions",
                    ),
                ],
                className="connection-bar",
                **{"aria-label": "Device connection"},
            ),
            html.Section(
                [
                    html.Div(
                        [
                            html.Div("COUNTED POSITION", className="eyebrow"),
                            html.Div(
                                [html.Span("—", id="angle"), html.Span("°", className="degree")],
                                className="angle",
                            ),
                            html.Div(
                                "Connect a mount to begin.",
                                id="operation",
                                role="status",
                                className="position-status",
                            ),
                            html.Div(
                                "Position is counted, not encoder measured.", className="helper"
                            ),
                        ]
                    ),
                    html.Img(
                        id="dial",
                        src=dial_source(None),
                        className="dial",
                        alt="Counted angle; zero is at the top, 90 degrees at the right",
                    ),
                ],
                className="position-panel",
            ),
            html.Div(id="error", role="alert", className="error-box", style={"display": "none"}),
            html.Section(
                [
                    html.Div(
                        [html.H2("Angle control"), button("Stop", "stop", "stop-button")],
                        className="angle-toolbar",
                    ),
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.H3("Position", className="control-title"),
                                    html.Label("Target angle / °", htmlFor="target"),
                                    html.Div(
                                        [
                                            dcc.Input(
                                                id="target",
                                                type="number",
                                                value=0,
                                                min=0,
                                                max=359.99999,
                                                step="any",
                                                className="target-input",
                                            ),
                                            button("Move to angle →", "move-to", "primary"),
                                        ],
                                        className="target-row",
                                    ),
                                    html.Div(
                                        id="move-preview", className="move-preview", role="status"
                                    ),
                                ],
                                className="control-group",
                            ),
                            html.Div(
                                [
                                    html.H3("Step", className="control-title"),
                                    html.Label("Step angle / °", htmlFor="step"),
                                    html.Div(
                                        [
                                            button("− Step", "step-minus"),
                                            dcc.Input(
                                                id="step",
                                                type="number",
                                                value=1,
                                                min=0,
                                                step="any",
                                            ),
                                            button("+ Step", "step-plus"),
                                        ],
                                        className="step-row",
                                    ),
                                    html.P(
                                        "Finite steps use a fresh position. "
                                        "Targets stay within 0 ≤ θ < 360°; they do not wrap.",
                                        className="helper step-hint",
                                    ),
                                ],
                                className="control-group",
                            ),
                        ],
                        className="move-grid",
                    ),
                ],
                className="card",
            ),
            html.Details(
                [
                    html.Summary("Device actions & details"),
                    html.Div([button("Home mount", "home")], className="button-row"),
                    html.P(
                        "Home moves the mount and establishes zero. Connect only reads state. "
                        "Movement uses the controller's existing speed, direction, "
                        "and backlash settings.",
                        className="helper",
                    ),
                    html.Div(id="device-details", className="details"),
                ],
                className="card optional-panel",
            ),
            html.Details(
                [html.Summary("Activity"), html.Div(id="activity", className="activity")],
                className="activity-card",
            ),
            html.Div("Ready.", id="request-result", role="status", className="request-result"),
            html.Footer("K10CR2 · WinUSB · Local control", className="page-footer"),
            dcc.Interval(id="refresh", interval=500, n_intervals=0),
        ],
        className="page",
    )

    @app.callback(
        Output("request-result", "children"),
        [Input(name, "n_clicks") for name in (*COMMANDS, "close-gui")],
        State("serial", "value"),
        State("target", "value"),
        State("step", "value"),
        prevent_initial_call=True,
    )
    def command(*values):
        serial_value, target, step = values[-3:]
        name = ctx.triggered_id
        if name == "close-gui":
            shutdown = app.server.config.get("GUI_SHUTDOWN")
            if shutdown is None:
                return "Close this web UI from the application hosting it."

            def close():
                if controller.shutdown():
                    shutdown()

            threading.Thread(target=close, name="k10cr2-gui-shutdown", daemon=True).start()
            return "Closing web UI; waiting for the USB owner…"
        if name not in COMMANDS:
            return "Ready."
        return controller.submit(
            "refresh" if name == "refresh-state" else name,
            serial=serial_value,
            target=target,
            step=step,
        )

    @app.callback(
        Output("connection-label", "children"),
        Output("connection-dot", "className"),
        Output("angle", "children"),
        Output("dial", "src"),
        Output("operation", "children"),
        Output("device-details", "children"),
        Output("error", "children"),
        Output("error", "style"),
        Output("activity", "children"),
        Output("move-preview", "children"),
        Output("serial", "disabled"),
        Output("close-gui", "disabled"),
        *[Output(name, "disabled") for name in COMMANDS],
        Input("refresh", "n_intervals"),
        Input("target", "value"),
        Input("step", "value"),
    )
    def render(_ticks, target, step):
        snap = controller.snapshot()
        if ctx.triggered_id in {None, "refresh"}:
            controller.refresh()
        disabled, target_error = controls(snap, target, step)
        connected = snap["connected"]
        age = time.time() - snap["updated"] if snap["updated"] is not None else None
        fresh = connected and not snap["recovery"] and age is not None and age < 2
        angle = snap["position"]["degrees"] if snap["position"] is not None else None
        identity, status = snap["identity"], snap["status"]
        label = (
            "Reconnect required"
            if snap["recovery"]
            else "Connected"
            if fresh
            else "Status stale"
            if connected
            else "Disconnected"
        )
        operation = snap["message"]
        if snap["busy"] in MOTIONS:
            operation = snap["message"] + " · Last controller readback shown."
        elif connected and not fresh:
            operation += " · Last readback is unverified."
        details = [
            html.Div([html.Span(label), html.Strong(str(value))])
            for label, value in (
                ("Model", identity.get("model", "—")),
                ("Serial", identity.get("serial", "—")),
                ("Firmware", identity.get("firmware", "—")),
                ("Hardware revision", identity.get("hardware_revision", "—")),
                ("Raw counts", snap["position"]["counts"] if angle is not None else "—"),
                ("Homed", status.get("homed", "—")),
                ("Motor enabled", status.get("enabled", "—")),
                ("Status bits", f"{status['raw_bits']:#010x}" if status else "—"),
            )
        ]
        events = [
            html.Div(
                [html.Time(event["time"]), html.Span(event["message"])],
                className=f"event {event['level']}",
            )
            for event in snap["events"]
        ]
        return (
            label,
            "status-dot connected" if fresh else "status-dot",
            "—" if angle is None else f"{angle:.3f}",
            dial_source(angle),
            operation,
            details,
            snap["error"],
            {} if snap["error"] else {"display": "none"},
            events,
            target_error or "Absolute target · 0 ≤ θ < 360° · Home required",
            connected or bool(snap["busy"]) or snap["closing"],
            snap["closing"],
            *[disabled[name] for name in COMMANDS],
        )

    return app


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", help="Prefill an eight-digit serial; does not connect")
    parser.add_argument("--port", type=int, default=8050, help="Loopback port (default: 8050)")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("Port must be between 1 and 65535")
    if args.serial is not None and (
        len(args.serial) != 8 or not args.serial.isascii() or not args.serial.isdigit()
    ):
        parser.error("Serial must be eight ASCII digits")
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    controller = GuiController()
    app = create_app(controller, serial=args.serial)
    server = make_server("127.0.0.1", args.port, app.server, threaded=True)
    app.server.config["GUI_SHUTDOWN"] = server.shutdown
    print(f"K10CR2 GUI: http://127.0.0.1:{server.server_port}", flush=True)
    print(
        "Startup does not connect or move the mount. Ctrl+C stops and closes the GUI.", flush=True
    )
    try:
        while True:
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            if controller.shutdown():
                break
            print(
                "Shutdown incomplete; controls remain available. " + controller.snapshot()["error"],
                flush=True,
            )
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
