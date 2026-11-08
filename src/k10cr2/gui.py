"""Local K10CR2 web UI: single USB owner, component layout, callbacks and server."""

import argparse
import logging
import socket
import threading
import time
from collections import deque
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from dash import Dash, Input, Output, State, ctx, dcc, html, no_update
from flask import abort, jsonify, request, send_file
from werkzeug.serving import ThreadedWSGIServer

from .controller import K10CR2, MotionCancelled, MotionError
from .protocol import COUNTS_PER_DEGREE, SETTINGS
from .settings import (
    UNIT_FACTORS,
    VELOCITY_SCALE,
    boolean,
    display_angle,
    encode_changes,
    finite,
    integer,
    resolve_target,
    to_display,
)

GROUP_FIELDS = {
    "velocity": ("velocity", "acceleration"),
    "jog": ("jog_step", "jog_mode", "jog_stop", "jog_velocity", "jog_acceleration"),
    "backlash": ("backlash",),
    "buttons": ("button_mode", "button_position1", "button_position2"),
}


DEVICE_FIELDS = tuple(field for fields in GROUP_FIELDS.values() for field in fields)


SESSION_SCALAR_FIELDS = ("rotation_mode", "rotation_direction", "units", "decimals")


DRIVE_FIELDS = tuple(f"drive{i}" for i in range(1, 5))


SESSION_FIELDS = (*SESSION_SCALAR_FIELDS, "high_power", *DRIVE_FIELDS)


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
            **{name: fields[name] for name in SESSION_SCALAR_FIELDS},
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
        **{name: ui[name] for name in SESSION_SCALAR_FIELDS},
        "high_power": ["yes"] if ui["high_power"] else [],
        **dict(zip(DRIVE_FIELDS, ui["drives"], strict=True)),
    }


MOTION_COMMANDS = {"home", "move"}
MANUAL_COMMANDS = {"jog-forward", "jog-reverse", "drive-forward", "drive-reverse"}
MOTION_COMMANDS |= MANUAL_COMMANDS


class GuiController:
    def __init__(self, factory=K10CR2):
        self._factory = factory
        self._condition = threading.Condition()
        self._mount = None
        self._cancel = threading.Event()
        self._closing = False
        self._hold_token = None
        self._hold_until = 0
        self._released_tokens = deque(maxlen=64)
        self._events = deque(maxlen=100)
        self._state = dict(
            connected=False,
            busy=None,
            recovery=False,
            identity={},
            status={},
            position=None,
            updated=None,
            error="",
            unknown=False,
            message="Connect a mount to begin.",
            settings={},
            settings_revision=0,
            settings_updated=None,
            ui=default_session(),
        )

    def apply_settings(self, fields, *, revision, persist=False):
        """Apply one form; publish session options only after device writes succeed."""
        with self._condition:
            if (
                self._state["busy"] not in {None, "refresh"}
                or self._closing
                or self._state["status"].get("moving")
                or self._state["status"].get("homing")
            ):
                return "Wait for the active operation before applying settings."
            if revision != self._state["settings_revision"]:
                return "Device readbacks changed. Review the refreshed settings and apply again."
            boolean(persist, "EEPROM persistence")
            candidate = session_draft(fields)
            changes = changed_settings(fields, self._state["settings"])
            if changes:
                return self.submit("configure", changes=changes, persist=persist, session=candidate)
            if candidate == self._state["ui"]:
                return "No settings changed."
            self._state["ui"] = candidate
            self._state["settings_revision"] += 1
            self._event("Settings applied (session only).")
            return "Settings applied (session only)."

    def hold(self, action, token, command=None, index=0):
        """A short browser lease, tied to one token; releases cannot cancel another tab."""
        if not isinstance(token, str) or not 1 <= len(token) <= 80:
            raise ValueError("Invalid hold token")
        with self._condition:
            if action == "release":
                self._released_tokens.append(token)
                if token == self._hold_token:
                    self._hold_until = 0
                return "Control released."
            if action == "heartbeat":
                if (
                    token != self._hold_token
                    or token in self._released_tokens
                    or time.monotonic() >= self._hold_until
                    or self._state["busy"] not in MANUAL_COMMANDS
                ):
                    return "Control is inactive."
                self._hold_until = time.monotonic() + 0.8
                return "Control renewed."
            if (
                action != "start"
                or command not in MANUAL_COMMANDS
                or token in self._released_tokens
            ):
                return "Control request rejected."
            if self._state["busy"] or self._closing:
                return "Another operation owns the mount; request was not queued."
            if not isinstance(index, int) or isinstance(index, bool) or index not in range(4):
                raise ValueError("Invalid drive division")
            self._hold_token = token
            self._hold_until = time.monotonic() + 0.8
            return self.submit(command, target=self._state["ui"]["drives"][index])

    def _held(self):
        with self._condition:
            return time.monotonic() < self._hold_until

    def snapshot(self):
        with self._condition:
            return deepcopy({**self._state, "closing": self._closing, "events": list(self._events)})

    def _event(self, message, level="info"):
        self._events.appendleft(
            dict(
                time=datetime.now().astimezone().isoformat(timespec="seconds"),
                message=message,
                level=level,
            )
        )

    def _readback(self, status, position):
        with self._condition:
            self._state.update(
                status=asdict(status), position=asdict(position), updated=time.time(), unknown=False
            )

    def submit(
        self, command, *, serial=None, target=None, changes=None, persist=False, session=None
    ):
        with self._condition:
            if self._closing:
                return "The GUI is shutting down."
            busy = self._state["busy"]
            if command == "stop" and busy and busy not in {"connect", "disconnect", "stop"}:
                self._cancel.set()
                self._state["message"] = "Stop requested; waiting for controller confirmation…"
                self._event("Stop requested")
                return self._state["message"]
            if busy:
                return f"{busy} is in progress; request was not queued."
            try:
                if command == "connect":
                    if self._mount is not None:
                        raise ValueError("Disconnect before connecting again.")
                    # Validate without constructing a controller or accessing USB.
                    if (
                        not isinstance(serial, str)
                        or len(serial) != 8
                        or not serial.isascii()
                        or not serial.isdigit()
                    ):
                        raise ValueError("Enter the mount's eight-digit serial.")
                elif self._mount is None:
                    raise ValueError("Connect a mount first.")
                elif command not in {
                    "disconnect",
                    "refresh",
                    "stop",
                    "enable",
                    "disable",
                    "identify",
                    "load-settings",
                    "configure",
                    *MOTION_COMMANDS,
                }:
                    raise ValueError("Unknown command.")
                elif self._state["unknown"] and command not in {"stop", "disconnect"}:
                    raise ValueError(
                        "Physical state UNKNOWN; use Stop, then disconnect and reconnect."
                    )
                elif self._state["recovery"] and command not in {"disconnect", "stop"}:
                    raise ValueError("Disconnect and explicitly reconnect before continuing.")
                if command in MOTION_COMMANDS:
                    status = self._state["status"]
                    if status.get("moving") or status.get("homing"):
                        raise ValueError("Mount is already moving; use Stop.")
                    if command != "home":
                        if not status.get("homed"):
                            raise ValueError("Home the mount explicitly before moving.")
                        if command in MANUAL_COMMANDS:
                            if not status.get("enabled"):
                                raise ValueError(
                                    "Enable the motor explicitly before manual control."
                                )
                            if command.startswith("jog-") and "jog" not in self._state["settings"]:
                                raise ValueError("Load Jog settings before jogging.")
                        else:
                            resolve_target(
                                target,
                                self._state["position"]["degrees"],
                                self._state["ui"]["rotation_mode"],
                                self._state["ui"]["rotation_direction"],
                            )
                if command == "configure" or (command == "move" and changes):
                    if not changes:
                        raise ValueError("No device settings changed.")
                    if session is not None:
                        session = validate_session(session)
                    for group, draft in changes.items():
                        if group not in self._state["settings"]:
                            raise ValueError(
                                f"Load {group} settings from the device before editing."
                            )
                        encode_changes(
                            group,
                            draft,
                            self._state["settings"][group],
                            (session or self._state["ui"])["high_power"],
                        )
            except (ValueError, TypeError) as exc:
                return str(exc)
            self._cancel.clear()
            self._state["busy"] = command
            if command != "refresh":
                self._state["message"] = f"{command} in progress…"
            thread = threading.Thread(
                target=self._run,
                args=(command, serial, target, changes, persist, session),
                name="k10cr2-gui-owner",
                daemon=False,
            )
            try:
                thread.start()
            except Exception:
                self._state["busy"] = None
                raise
            return f"{command} requested."

    def _disconnect(self):
        if self._mount is not None:
            self._mount.close()
        self._mount = None
        with self._condition:
            self._state.update(
                connected=False,
                recovery=False,
                identity={},
                status={},
                position=None,
                updated=None,
                unknown=False,
                settings={},
                settings_revision=self._state["settings_revision"] + 1,
                settings_updated=None,
            )

    def _publish_settings(self, actual, session=None):
        with self._condition:
            self._state["settings"].update(actual)
            self._state["settings_updated"] = time.time()
            self._state["settings_revision"] += 1
            if session is not None:
                self._state["ui"] = session

    def _configure(self, changes, *, persist=False, session=None):
        try:
            actual = self._mount.configure(
                changes, high_power=(session or self._state["ui"])["high_power"], persist=persist
            )
        except Exception as exc:
            if self._mount.recovery_required:
                exc.add_note(
                    "Device changes may be partially applied. Session options were not changed; "
                    "reconnect and refresh readbacks before retrying."
                )
            raise
        self._publish_settings(actual, session)

    def _run(self, command, serial, target, changes, persist, session):
        stop_handled = False
        cancelled_before_motion = False
        try:
            if command == "connect":
                self._mount = self._factory(serial)
                self._mount.connect()
                identity = self._mount.info()
                self._readback(self._mount.status(), self._mount.position())
                settings = self._mount.query_settings(list(SETTINGS))
                with self._condition:
                    self._state.update(
                        connected=True,
                        identity=asdict(identity),
                    )
                    self._publish_settings(settings)
            elif command == "disconnect":
                if self._state["unknown"]:
                    raise ValueError("Physical state UNKNOWN; use Stop before disconnecting.")
                status = self._mount.status() if not self._mount.recovery_required else None
                if status is not None and status.busy:
                    raise ValueError("Mount is moving; use Stop before disconnecting.")
                self._disconnect()
            elif command == "refresh":
                self._readback(self._mount.status(), self._mount.position())
            elif command == "stop":
                result = self._mount.stop()
                self._readback(result.status, result.position)
            elif command in {"enable", "disable"}:
                status = self._mount.set_enabled(command == "enable")
                self._readback(status, self._mount.position())
            elif command == "identify":
                self._mount.identify()
            elif command == "load-settings":
                self._readback(self._mount.status(), self._mount.position())
                settings = self._mount.query_settings(list(SETTINGS))
                self._publish_settings(settings)
            elif command == "configure":
                self._configure(changes, persist=persist, session=session)
            elif command in MANUAL_COMMANDS:
                result = self._mount.manual_motion(
                    1 if command.endswith("forward") else -1,
                    jog=command.startswith("jog-"),
                    speed=target,
                    mode=self._state["ui"]["rotation_mode"],
                    high_power=self._state["ui"]["high_power"],
                    cancel=self._cancel,
                    held=self._held,
                    on_progress=self._readback,
                )
                self._readback(result.status, result.position)
            else:
                if command == "move" and changes:
                    self._configure(changes)
                kwargs = dict(cancel=self._cancel, on_progress=self._readback)
                result = (
                    self._mount.home(**kwargs)
                    if command == "home"
                    else self._mount.move_absolute(
                        target,
                        mode=self._state["ui"]["rotation_mode"],
                        direction=self._state["ui"]["rotation_direction"],
                        **kwargs,
                    )
                )
                self._readback(result.status, result.position)
            with self._condition:
                if command != "refresh":
                    self._state.update(error="", message=f"{command} complete.")
                    if command == "identify":
                        self._state["message"] = (
                            "Identify command sent (LED has no acknowledgement)."
                        )
                    elif command == "configure" and persist:
                        self._state["message"] = (
                            "Settings readback verified; EEPROM save requested. "
                            "Power-cycle persistence is unverified."
                        )
                    self._event(self._state["message"])
        except Exception as exc:
            # MotionError means cleanup was already attempted; never replay it.
            stop_handled = isinstance(exc, MotionError)
            cancelled_before_motion = (
                isinstance(exc, MotionCancelled)
                and self._cancel.is_set()
                and self._mount is not None
                and not self._mount.recovery_required
            )
            if not cancelled_before_motion:
                self._failed(command, exc)
        finally:
            with self._condition:
                # Stop acceptance and owner release share one lock. A Stop accepted
                # before this boundary must be serviced before another owner starts.
                pending_stop = (
                    command not in {"connect", "disconnect", "stop"}
                    and self._cancel.is_set()
                    and not stop_handled
                )
                if not pending_stop:
                    self._finish_operation()
            if pending_stop:
                try:
                    result = self._mount.stop()
                    self._readback(result.status, result.position)
                    with self._condition:
                        if cancelled_before_motion:
                            self._state["error"] = ""
                        self._state["message"] = "Stop confirmed by controller."
                        self._event(self._state["message"])
                except Exception as exc:
                    self._failed("stop", exc)
                finally:
                    with self._condition:
                        self._finish_operation()

    def _finish_operation(self):
        """Publish owner release while holding the condition lock."""
        self._state.update(
            busy=None,
            recovery=self._mount is not None and self._mount.recovery_required,
        )
        self._condition.notify_all()

    def _failed(self, command, exc):
        if isinstance(exc, MotionError) and exc.cleanup.stop is not None:
            self._readback(exc.cleanup.stop.status, exc.cleanup.stop.position)
            if not exc.recovery_required and not self._mount.recovery_required:
                with self._condition:
                    self._state.update(error="", message="Stop confirmed by controller.")
                    self._event(self._state["message"])
                return
        message = f"{type(exc).__name__}: {exc}"
        if getattr(exc, "__notes__", None):
            message += " " + " ".join(exc.__notes__)
        if command == "connect":
            try:
                self._disconnect()
            except Exception as close_error:
                message += f"; close failed: {close_error}"
                with self._condition:
                    self._state["connected"] = True  # Keep Disconnect/Stop accessible.
        with self._condition:
            if self._mount is not None and self._mount.recovery_required:
                self._state["unknown"] = not (
                    isinstance(exc, MotionError) and exc.cleanup.confirmed_stopped
                )
            if self._state["unknown"]:
                message += "; physical state UNKNOWN. Use Stop before disconnecting."
            self._state.update(error=message, message="Operation failed; inspect Activity.")
            self._event(message, "error")

    def refresh(self):
        with self._condition:
            if (
                self._closing
                or not self._state["connected"]
                or self._state["busy"]
                or self._state["recovery"]
            ):
                return
            updated = self._state["updated"]
            if updated is not None and time.time() - updated < 0.5:
                return
            # Reserve under the same lock so a simultaneous command cannot be queued.
            self.submit("refresh")

    def shutdown(self, timeout=15):
        """Cancel through the active owner, wait, then release USB. Never race close."""
        with self._condition:
            if self._closing:
                return self._mount is None and self._state["busy"] is None
            self._closing = True
            self._cancel.set()
            try:
                finished = self._condition.wait_for(lambda: self._state["busy"] is None, timeout)
            except KeyboardInterrupt:
                self._closing = False
                self._state["error"] = (
                    "Shutdown interrupted; USB owner is still active. Retry Stop."
                )
                return False
            if not finished:
                self._closing = False
                self._state["error"] = "Shutdown timed out; USB owner is still active. Retry Stop."
                return False
            self._state["busy"] = "disconnect"
        try:
            # A mount may already have been moving when connected. An uncertain
            # connection also requires a stop attempt before the GUI releases it.
            if self._mount is not None:
                state = self._state
                # Front-panel movement can start after the last browser sample.
                fresh_status = self._mount.status() if not self._mount.recovery_required else None
                if (
                    state["unknown"]
                    or (fresh_status is not None and fresh_status.busy)
                    or (
                        fresh_status is None
                        and (state["status"].get("moving") or state["status"].get("homing"))
                    )
                ):
                    result = self._mount.stop()
                    self._readback(result.status, result.position)
            self._disconnect()
            with self._condition:
                self._state["message"] = "Web UI closed. You may close this tab."
            return True
        except (Exception, KeyboardInterrupt) as exc:
            with self._condition:
                self._closing = False
                self._state["error"] = (
                    f"Shutdown failed; physical state may be UNKNOWN: {type(exc).__name__}: {exc}"
                )
                self._state["unknown"] = True
                self._state["recovery"] = self._mount is not None and self._mount.recovery_required
            return False
        finally:
            with self._condition:
                self._state["busy"] = None
                self._condition.notify_all()


UNAVAILABLE = "Unavailable: K10CR2 command mapping is not verified. No command will be sent."


def field(label, name, *, value=None, options=None, disabled=False, **kwargs):
    kwargs.setdefault("step", "any")
    control = (
        dcc.Dropdown(
            id=name,
            options=options,
            value=value,
            clearable=False,
            disabled=disabled,
            placeholder="Unavailable" if disabled else "Select…",
        )
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
    defaults = default_session()
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
                    "Refresh readbacks",
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
                                        value=defaults["units"],
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
                                        value=defaults["rotation_mode"],
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
                                        value=defaults["rotation_direction"],
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
                                    for i, speed in enumerate(defaults["drives"], 1)
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
                                        "Device settings are read on connection. Select the "
                                        "persistence checkbox to save supported settings groups "
                                        "to the device."
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
                dcc.Checklist(
                    id="persist",
                    options=[
                        {"label": " Save changed settings to device (EEPROM)", "value": "yes"}
                    ],
                    value=[],
                ),
            ],
            className="apply-options",
        ),
        html.P(
            "Apply updates your edited settings. Saving to the device is optional.",
            className="hint",
        ),
        html.Div(
            [
                html.Button("Reset edits", id="reset-settings", className="button"),
                html.Button("Apply changes", id="apply-settings", className="button primary"),
                html.Button("Close", id="settings-close", className="button"),
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
    # Scroll only the settings body; feedback and actions stay visible.
    return [
        *result[:3],
        html.Div([tabs, *panels, *result[4:-1]], className="settings-body"),
        html.Div(
            [
                html.Div(id="settings-feedback", role="status", className="dialog-feedback"),
                result[-1],
            ],
            className="settings-footer",
        ),
    ]


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


def build_layout(serial=None):
    return html.Main(
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
                        "Refresh status", id="refresh-status", disabled=True, className="button"
                    ),
                    html.Button("Close web UI", id="close-gui", className="button"),
                ],
                className="connection-bar",
            ),
            html.Section(
                [
                    html.Div(
                        [
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
            # Carries immediate callback / held-control feedback to the single renderer.
            html.Div(id="request-result", style={"display": "none"}),
            html.Div(id="user-feedback", role="status", className="request-result"),
            html.P(
                "Counted position · Hold Drive/Jog to move; release to stop.",
                title="Position is not encoder measured. Loss of focus or an expired browser "
                "heartbeat requests Stop; USB latency still applies.",
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
                        "Unedited profiles stay unchanged; "
                        "clear both fields to use device settings.",
                        className="hint",
                    ),
                    html.Button(
                        "Move to position →",
                        id="move",
                        disabled=True,
                        className="button primary",
                    ),
                    html.Button("Apply profile", id="apply-move", className="button"),
                    html.Button("Reset", id="reset-move", className="button"),
                    html.Div(id="move-feedback", role="status", className="dialog-feedback"),
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


CONTROL_IDS = (
    "connect",
    "disconnect",
    "refresh-status",
    "home",
    "move",
    "stop",
    "enable",
    "disable",
    "identify",
    "load-settings",
    "jog-forward",
    "jog-reverse",
)


def visible_feedback(snapshot, request_message):
    """Keep validation feedback, but replace queued-request text with its actual outcome."""
    if request_message and not (
        request_message.endswith(" requested.") or request_message.startswith("Stop requested;")
    ):
        return request_message
    message = snapshot["message"]
    return {
        "connect complete.": "Connected. Position and device settings are ready.",
        "load-settings complete.": "Device readbacks updated.",
        "configure complete.": "Settings applied; device readback verified.",
        "disconnect complete.": "Disconnected.",
    }.get(message, message)


def controls(snapshot, target):
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
    disabled = {name: True for name in (*CONTROL_IDS, *MANUAL_COMMANDS)}
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
    for name in ("refresh-status", "load-settings"):
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
    disabled["move"] = not ready or not status.get("homed") or bool(error)
    for name in MANUAL_COMMANDS:
        # Keep the held control focusable while it owns motion. Disabling it
        # would blur it immediately and turn every keyboard hold into a release.
        holding = (
            busy == name
            and snapshot["connected"]
            and not snapshot["closing"]
            and not snapshot.get("unknown", False)
            and not snapshot["recovery"]
            and not status.get("motion_error")
            and not status.get("raw_bits", 0) & 0x40000000
        )
        disabled[name] = (
            not (ready or holding)
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
            if request.path == "/_dash-update-component":
                payload = request.get_json(silent=True)
                output = payload.get("output") if isinstance(payload, dict) else None
                if isinstance(output, str):
                    callback = app.callback_map.get(output)
                    if callback is None or any(
                        not isinstance(payload.get(kind), list)
                        or any(not isinstance(item, dict) for item in payload[kind])
                        or [(item.get("id"), item.get("property")) for item in payload[kind]]
                        != [(item["id"], item["property"]) for item in callback[kind]]
                        for kind in ("inputs", "state")
                    ):
                        return jsonify(
                            message="GUI layout changed. Reload this tab to continue."
                        ), 409

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
            / "docs/references/k10cr2-user-guide-etn064249-d02.pdf"
        )
        packaged = Path(__file__).parent / "manuals/k10cr2-user-guide-etn064249-d02.pdf"
        if packaged.is_file():
            path = packaged
        elif not path.is_file():
            abort(404, description="The bundled user guide is missing. Reinstall the package.")
        return send_file(path, mimetype="application/pdf")

    app.layout = build_layout(serial)

    buttons = (
        "connect",
        "disconnect",
        "refresh-status",
        "home",
        "move",
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
        "reset-settings",
        "reset-move",
        "apply-settings",
        "settings-close",
        "apply-move",
        "settings-dialog-stop",
        "move-dialog-stop",
    )

    @app.callback(
        Output("request-result", "children"),
        Output("panel", "data"),
        Output("settings-tab", "value"),
        inputs={
            "clicks": {name: Input(name, "n_clicks") for name in buttons},
            "serial_value": State("serial", "value"),
            "target": State("target", "value"),
            "move_profile": {
                "velocity": State("move_velocity", "value"),
                "acceleration": State("move_acceleration", "value"),
            },
            "fields": {name: State(name, "value") for name in DEVICE_FIELDS + SESSION_FIELDS},
            "revision": State("settings-revision", "data"),
            "persist": State("persist", "value"),
            "panel": State("panel", "data"),
        },
        prevent_initial_call=True,
    )
    def dispatch(clicks, serial_value, target, move_profile, fields, revision, persist, panel):
        name = ctx.triggered_id
        if name in {"move-open", "position-open", "settings-open", "jog-open", "drive-open"}:
            snapshot = owner.snapshot()
            if (
                snapshot["busy"] in MOTION_COMMANDS
                or snapshot["status"].get("moving")
                or snapshot["status"].get("homing")
            ):
                return (
                    "Panel request declined during motion. "
                    "Stop remains available on the main panel.",
                    None,
                    no_update,
                )
        if name in {"move-open", "position-open"}:
            return no_update, "move", no_update
        if name in {"settings-open", "jog-open", "drive-open"}:
            return no_update, "settings", "advanced" if name == "drive-open" else "moves"
        if name in {"cancel-move", "cancel-settings", "settings-close"}:
            return "", None, no_update
        if name in {"reset-settings", "reset-move"}:
            return "Edits reset to the last readback and applied session options.", panel, no_update
        try:
            if name == "apply-settings":
                message = owner.apply_settings(
                    fields, revision=revision, persist="yes" in (persist or [])
                )
            elif name in {"apply-move", "move"}:
                changes = None
                if name == "apply-move" or (
                    any(value is not None for value in move_profile.values())
                ):
                    draft = move_profile
                    current = to_display(owner.snapshot()["settings"]) if name == "move" else {}
                    if name == "apply-move" or any(
                        value is not None and value != current.get(key)
                        for key, value in draft.items()
                    ):
                        changes = {"velocity": draft}
                message = owner.submit(
                    "configure" if name == "apply-move" else "move",
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
                    if name == "refresh-status"
                    else "stop"
                    if name in {"settings-dialog-stop", "move-dialog-stop"}
                    else name
                )
                message = owner.submit(command, serial=serial_value, target=target)
        except (ValueError, TypeError, KeyError) as exc:
            message = str(exc)
        if name == "move" and message == "move requested.":
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
        *[Output(name, "value") for name in DEVICE_FIELDS + SESSION_FIELDS],
        Output("settings-revision", "data"),
        Output("persist", "value"),
        Input("refresh", "n_intervals"),
        Input("reset-settings", "n_clicks"),
        Input("cancel-settings", "n_clicks"),
        Input("settings-close", "n_clicks"),
        State("settings-revision", "data"),
    )
    def settings_values(_ticks, _reset, _cancel, _close, revision):
        snap = owner.snapshot()
        if revision == snap["settings_revision"] and ctx.triggered_id not in {
            "reset-settings",
            "cancel-settings",
            "settings-close",
        }:
            return [no_update] * (len(DEVICE_FIELDS + SESSION_FIELDS) + 2)
        values = form_values(snap)
        return [values.get(name) for name in DEVICE_FIELDS + SESSION_FIELDS] + [
            snap["settings_revision"],
            [],
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
        Output("user-feedback", "children"),
        Output("settings-feedback", "children"),
        Output("move-feedback", "children"),
        *[Output(name, "disabled") for name in CONTROL_IDS],
        *[
            Output(f"{direction}-{i}", "disabled")
            for direction in ("forward", "reverse")
            for i in range(4)
        ],
        Input("refresh", "n_intervals"),
        Input("target", "value"),
        Input("request-result", "children"),
    )
    def render(_ticks, target, request_message):
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
            if snap["busy"] in MOTION_COMMANDS or state.get("moving")
            else "Idle"
            if fresh
            else "Disconnected"
            if not snap["connected"]
            else "Unverified"
        )
        if snap["busy"] not in {None, "refresh", *MOTION_COMMANDS}:
            operation = {
                "connect": "Connecting…",
                "load-settings": "Reading settings…",
                "configure": "Applying settings…",
                "disconnect": "Disconnecting…",
            }.get(snap["busy"], operation)
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
            return f"{prefix}: {settings[key]:.6g}{suffix}" if key in settings else f"{prefix}: —"

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
                html.Dd(f"{home['offset'] / COUNTS_PER_DEGREE:.6f}"),
                html.Dt("Velocity / °/s"),
                html.Dd(f"{home['velocity'] / VELOCITY_SCALE:.6f}"),
            ]
            if home
            else [html.Dd("Load from device to read homing settings.")]
        )
        read_status = (
            "Read at " + time.strftime("%H:%M:%S", time.localtime(snap["settings_updated"]))
            if snap.get("settings_updated")
            else "Connect to read device settings."
        )
        feedback = visible_feedback(snap, request_message)
        return (
            display_angle(angle, ui["rotation_mode"], ui["units"], ui["decimals"]),
            {"deg": "°", "rad": " rad", "mrad": " mrad", "urad": " μrad", "counts": " cts"}[
                ui["units"]
            ],
            "S/N " + snap["identity"].get("serial", "—"),
            operation,
            label,
            "No readback" if age is None else f"Last counted readback: {age:.1f} s ago",
            "Home reference —"
            if not snap["connected"]
            else "Homing"
            if state.get("homing")
            else "Homed"
            if state.get("homed")
            else "Not Homed",
            "lamp homing" if state.get("homing") and fresh else lamp(state.get("homed")),
            lamp(bits & 1),
            lamp(bits & 2),
            summary("Vel", "velocity", "°/s"),
            summary("Acc", "acceleration", "°/s²"),
            summary("Jog Step", "jog_step", "°"),
            snap["error"],
            {} if snap["error"] else {"display": "none"},
            events,
            html.Dl(
                [
                    item
                    for label, value in (
                        ("Model", snap["identity"].get("model", "—")),
                        ("Firmware", snap["identity"].get("firmware", "—")),
                        ("Raw counts", position["counts"] if position else "—"),
                        ("Status bits", f"{bits:#010x}" if snap["status"] else "—"),
                    )
                    for item in (html.Dt(label), html.Dd(str(value)))
                ],
                className="device-facts",
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
            "" if snap["error"] else feedback,
            snap["error"] or feedback,
            snap["error"] or feedback,
            *[disabled[name] for name in CONTROL_IDS],
            *[
                disabled[f"drive-{direction}"]
                for direction in ("forward", "reverse")
                for _ in range(4)
            ],
        )

    return app


class LocalServer(ThreadedWSGIServer):
    """Reject occupied ports, including Windows SO_REUSEADDR double binds."""

    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def serve_gui(server, controller):
    """Keep Stop available until the USB owner confirms shutdown, then close HTTP."""
    try:
        while True:
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            if controller.shutdown():
                return True
            print(
                "Shutdown incomplete; controls remain available. " + controller.snapshot()["error"],
                flush=True,
            )
    finally:
        server.server_close()


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
    server = LocalServer("127.0.0.1", args.port, app.server)
    app.server.config["GUI_SHUTDOWN"] = server.shutdown
    print(f"K10CR2 GUI: http://127.0.0.1:{server.server_port}", flush=True)
    print(
        "Startup does not connect or move the mount. Ctrl+C stops and closes the GUI.", flush=True
    )
    serve_gui(server, controller)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
