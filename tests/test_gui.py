import os
import subprocess
import sys

import pytest

pytest.importorskip("dash")

from k10cr2.gui import (  # noqa: E402
    CONTROL_IDS,
    GuiController,  # noqa: E402
    controls,
    create_app,
    form_values,  # noqa: E402
)


def test_layout_assets_and_status_never_open_usb():
    def unexpected(serial):
        raise AssertionError("Startup attempted to construct a mount")

    controller = GuiController(unexpected)
    app = create_app(controller, serial="55551174")
    client = app.server.test_client()
    for path in ("/", "/_dash-layout", "/_dash-dependencies", "/assets/rotation.css"):
        assert client.get(path, base_url="http://127.0.0.1").status_code == 200
    status = client.get("/api/status", base_url="http://127.0.0.1").json
    assert not status["connected"] and status["busy"] is None
    disabled = controls(status)
    assert not disabled["connect"]
    assert all(disabled[name] for name in CONTROL_IDS if name != "connect")


def post_callback(app, key, values, changed):
    meta = app.callback_map[key]
    output = meta["output"]

    def spec(out):
        return {"id": out.component_id, "property": out.component_property}

    outputs = [spec(out) for out in output] if isinstance(output, list) else spec(output)

    def inputs(items):
        return [{**item, "value": values.get(item["id"])} for item in items]

    payload = {
        "output": key,
        "outputs": outputs,
        "inputs": inputs(meta["inputs"]),
        "changedPropIds": [changed],
    }
    # Match Dash's browser request, which omits State when there are no fields.
    if meta["state"]:
        payload["state"] = inputs(meta["state"])
    return app.server.test_client().post(
        "/_dash-update-component",
        base_url="http://127.0.0.1",
        json=payload,
    )


def test_dash_initial_render_and_command_roundtrip_without_usb():
    controller = GuiController(lambda serial: pytest.fail("Unexpected USB access"))
    app = create_app(controller)
    render_key = next(key for key in app.callback_map if "angle.children" in key)
    response = post_callback(app, render_key, {"refresh": 0, "target": 90}, "refresh.n_intervals")
    assert response.status_code == 200
    rendered = response.json["response"]
    assert rendered["angle"]["children"] == "—"
    assert rendered["stop"]["disabled"]
    command_key = next(key for key in app.callback_map if "request-result.children" in key)
    response = post_callback(app, command_key, {"serial": "bad", "target": 90}, "connect.n_clicks")
    assert response.status_code == 200
    assert "eight-digit" in response.json["response"]["request-result"]["children"]


@pytest.mark.parametrize(
    "velocity,acceleration,changes",
    [
        (10.00132144, 10, None),
        (None, None, None),
        (None, 10, None),
        (0.5, 1, {"velocity": {"velocity": 0.5, "acceleration": 1}}),
        (10.00132144, 1, {"velocity": {"velocity": 10.00132144, "acceleration": 1}}),
    ],
)
def test_move_only_applies_an_edited_profile(monkeypatch, velocity, acceleration, changes):
    owner = GuiController(lambda serial: pytest.fail("Unexpected USB access"))
    owner._state["settings"] = {
        "velocity": dict(channel=1, minimum=0, maximum=73300775, acceleration=15020)
    }
    calls = []

    def submit(command, **kwargs):
        calls.append((command, kwargs))
        return "move requested."

    monkeypatch.setattr(owner, "submit", submit)
    app = create_app(owner)
    key = next(key for key in app.callback_map if "request-result.children" in key)
    response = post_callback(
        app,
        key,
        {
            "target": 0,
            "move_velocity": velocity,
            "move_acceleration": acceleration,
            "persist": [],
            "panel": "move",
        },
        "move.n_clicks",
    )
    assert response.status_code == 200
    assert calls == [("move", dict(target=0, changes=changes))]
    assert response.json["response"]["panel"]["data"] is None


@pytest.mark.parametrize(
    "edits,groups",
    [
        ({}, set()),
        ({"velocity": 0.5, "acceleration": 1}, {"velocity"}),
        ({"backlash": 2}, {"backlash"}),
        ({"jog_step": 0.5, "button_mode": 2}, {"jog", "buttons"}),
    ],
)
@pytest.mark.parametrize("persist", [False, True])
def test_settings_apply_detects_edits_without_group_choices(monkeypatch, edits, groups, persist):
    owner = GuiController(lambda serial: pytest.fail("Unexpected USB access"))
    owner._state["settings"] = {
        # This unedited readback exceeds the session's 10 degree/s validation
        # threshold and must not prevent changes to unrelated device settings.
        "velocity": dict(channel=1, minimum=0, maximum=73300775, acceleration=15020),
        "jog": dict(
            channel=1, mode=2, step=13653, minimum=0, acceleration=1502, maximum=7329109, stop=2
        ),
        "backlash": dict(channel=1, distance=136533),
        "buttons": dict(
            channel=1, mode=1, position1=136533, position2=682667, timeout1=500, timeout2=500
        ),
    }
    calls = []
    monkeypatch.setattr(
        owner, "submit", lambda command, **kwargs: calls.append((command, kwargs)) or "requested"
    )
    app = create_app(owner)
    key = next(key for key in app.callback_map if "request-result.children" in key)
    fields = {**form_values(owner.snapshot()), **edits}
    response = post_callback(
        app,
        key,
        {
            **fields,
            "settings-revision": 0,
            "persist": ["yes"] if persist else [],
            "panel": "settings",
        },
        "apply-settings.n_clicks",
    )
    assert response.status_code == 200
    if groups:
        command, arguments = calls[0]
        assert len(calls) == 1 and command == "configure"
        assert set(arguments["changes"]) == groups
        assert arguments["persist"] == persist
        for draft in arguments["changes"].values():
            assert all(value == fields[field] for field, value in draft.items())
    else:
        assert not calls
        assert response.json["response"]["request-result"]["children"] == "No settings changed."


def test_settings_apply_does_not_treat_a_stale_form_as_new_edits(monkeypatch):
    owner = GuiController(lambda serial: pytest.fail("Unexpected USB access"))
    owner._state["settings_revision"] = 2
    monkeypatch.setattr(owner, "submit", lambda *args, **kwargs: pytest.fail("Stale form applied"))
    app = create_app(owner)
    key = next(key for key in app.callback_map if "request-result.children" in key)
    response = post_callback(
        app, key, {"settings-revision": 1, "panel": "settings"}, "apply-settings.n_clicks"
    )
    assert response.status_code == 200
    assert (
        "Review the refreshed settings" in response.json["response"]["request-result"]["children"]
    )


def test_manual_is_served_from_the_repository():
    response = create_app().server.test_client().get("/manual", base_url="http://127.0.0.1")
    assert response.status_code == 200 and response.data.startswith(b"%PDF")


def test_status_refresh_does_not_reload_or_replace_settings(monkeypatch):
    owner = GuiController(lambda serial: pytest.fail("Unexpected USB access"))
    calls = []
    monkeypatch.setattr(owner, "submit", lambda command, **kwargs: calls.append(command) or "ok")
    app = create_app(owner)
    key = next(key for key in app.callback_map if "request-result.children" in key)
    button = next(name for name in CONTROL_IDS if name.startswith("refresh-"))
    response = post_callback(app, key, {"panel": None}, f"{button}.n_clicks")
    assert response.status_code == 200
    assert calls == ["refresh"]


@pytest.mark.parametrize("index", [1.0, True, "1", -1, 4, None])
def test_invalid_drive_index_returns_validation_error_before_start(index):
    owner = GuiController(lambda serial: pytest.fail("Unexpected USB access"))
    response = (
        create_app(owner)
        .server.test_client()
        .post(
            "/api/hold",
            base_url="http://127.0.0.1",
            json={
                "action": "start",
                "token": "invalid-index",
                "command": "drive-forward",
                "index": index,
            },
        )
    )
    assert response.status_code == 400
    assert "drive division" in response.json["message"]
    assert owner.snapshot()["busy"] is None


def test_all_callback_components_exist_and_settings_fields_remain_mounted():
    app = create_app()
    ids = []

    def visit(component):
        if isinstance(component, (list, tuple)):
            for child in component:
                visit(child)
        elif hasattr(component, "to_plotly_json"):
            if getattr(component, "id", None):
                ids.append(component.id)
            visit(getattr(component, "children", None))

    visit(app.layout)
    assert len(ids) == len(set(ids))
    assert {
        "homing-readback",
        "settings-page-moves",
        "settings-page-rotation",
        "settings-page-advanced",
    } <= set(ids)
    assert not {
        "profile",
        "bow-index",
        "rest-power",
        "trigger-input",
        "trigger-output",
        "limit-policy",
        "settings-page-stage",
        "settings-page-defaults",
    } & set(ids)
    for meta in app.callback_map.values():
        for field in meta["inputs"] + meta["state"]:
            assert field["id"] in ids
        outputs = meta["output"] if isinstance(meta["output"], list) else [meta["output"]]
        assert all(out.component_id in ids for out in outputs)


def test_session_only_apply_does_not_access_usb():
    owner = GuiController(lambda serial: pytest.fail("Unexpected USB access"))
    app = create_app(owner)
    key = next(key for key in app.callback_map if "request-result.children" in key)
    values = dict(
        rotation_mode="equivalent",
        rotation_direction="reverse",
        units="rad",
        decimals=5,
        high_power=[],
        drive1=2.5,
        drive2=5,
        drive3=7.5,
        drive4=10,
        **{"settings-revision": 0, "persist": [], "panel": "settings"},
    )
    response = post_callback(app, key, values, "apply-settings.n_clicks")
    assert response.status_code == 200
    assert owner.snapshot()["ui"]["units"] == "rad"
    assert not owner.snapshot()["connected"]


@pytest.mark.parametrize("button", ["settings-close", "cancel-settings"])
def test_closing_settings_discards_drafts_without_applying(button):
    owner = GuiController(lambda serial: pytest.fail("Unexpected USB access"))
    app = create_app(owner)
    key = next(key for key in app.callback_map if "request-result.children" in key)
    response = post_callback(
        app,
        key,
        {
            "panel": "settings",
            "units": "rad",
            "decimals": 3,
            "persist": ["yes"],
        },
        f"{button}.n_clicks",
    )
    assert response.status_code == 200
    assert response.json["response"]["panel"]["data"] is None
    assert owner.snapshot()["ui"]["units"] == "deg"
    reset_key = next(key for key in app.callback_map if "settings-revision.data" in key)
    response = post_callback(app, reset_key, {"settings-revision": 0}, f"{button}.n_clicks")
    assert response.status_code == 200
    assert response.json["response"]["units"]["value"] == "deg"
    assert response.json["response"]["decimals"]["value"] == 6
    assert response.json["response"]["persist"]["value"] == []


def test_completed_feedback_replaces_request_and_reaches_both_dialogs():
    from k10cr2.gui import visible_feedback

    owner = GuiController(lambda serial: pytest.fail("Unexpected USB access"))
    owner._state["message"] = "load-settings complete."
    assert (
        visible_feedback(owner.snapshot(), "load-settings requested.")
        == "Device readbacks updated."
    )
    validation = "Decimal places must be an integer from 0 to 9"
    assert visible_feedback(owner.snapshot(), validation) == validation
    app = create_app(owner)
    key = next(key for key in app.callback_map if "angle.children" in key)
    response = post_callback(app, key, {"request-result": validation}, "request-result.children")
    rendered = response.json["response"]
    assert rendered["settings-feedback"]["children"] == validation
    assert rendered["move-feedback"]["children"] == validation


@pytest.mark.parametrize("button,panel", [("reset-settings", "settings"), ("reset-move", "move")])
def test_reset_replaces_stale_validation_without_closing_or_accessing_usb(button, panel):
    owner = GuiController(lambda serial: pytest.fail("Unexpected USB access"))
    app = create_app(owner)
    key = next(key for key in app.callback_map if "request-result.children" in key)
    response = post_callback(app, key, {"panel": panel}, f"{button}.n_clicks")
    rendered = response.json["response"]
    assert rendered["panel"]["data"] == panel
    assert rendered["request-result"]["children"].startswith("Edits reset")


def test_local_server_rejects_foreign_hosts_and_cross_origin_commands():
    app = create_app()
    client = app.server.test_client()
    assert client.get("/", base_url="http://attacker.example").status_code == 403
    for headers in ({"Origin": "http://attacker.example"}, {"Sec-Fetch-Site": "cross-site"}):
        assert (
            client.post(
                "/_dash-update-component", base_url="http://127.0.0.1", headers=headers, json={}
            ).status_code
            == 403
        )


def test_stale_gui_callback_requires_reload_without_dispatch_or_server_error(caplog):
    owner = GuiController(lambda serial: pytest.fail("Stale GUI attempted USB access"))
    app = create_app(owner)
    render_key = next(key for key in app.callback_map if "angle.children" in key)
    stale_key = render_key.replace("refresh-status", "refresh-state")
    assert stale_key != render_key
    response = app.server.test_client().post(
        "/_dash-update-component",
        base_url="http://127.0.0.1",
        json={"output": stale_key, "changedPropIds": ["refresh.n_intervals"]},
    )
    assert response.status_code == 409
    assert "Reload this tab" in response.json["message"]
    assert not owner.snapshot()["connected"] and owner.snapshot()["busy"] is None
    assert not [record for record in caplog.records if record.levelname == "ERROR"]


def test_gui_help_and_import_do_not_import_usb():
    code = (
        "import sys; from k10cr2.gui import main; "
        "assert 'usb.core' not in sys.modules; "
        "\ntry: main(['--help'])\nexcept SystemExit as e: assert e.code == 0\n"
        "assert 'usb.core' not in sys.modules"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert result.returncode == 0, result.stderr


def test_old_callback_inputs_require_reload_even_with_the_same_output():
    owner = GuiController(lambda serial: pytest.fail("Stale GUI attempted USB access"))
    app = create_app(owner)
    key = next(key for key in app.callback_map if "request-result.children" in key)
    response = app.server.test_client().post(
        "/_dash-update-component",
        base_url="http://127.0.0.1",
        json={
            "output": key,
            "inputs": [{"id": "apply-display", "property": "n_clicks", "value": 1}],
            "state": [],
            "changedPropIds": ["apply-display.n_clicks"],
        },
    )
    assert response.status_code == 409 and "Reload this tab" in response.json["message"]
    assert not owner.snapshot()["connected"] and owner.snapshot()["busy"] is None


def test_server_refuses_to_share_an_occupied_port():
    import socket

    from k10cr2.gui import LocalServer

    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        with pytest.raises(SystemExit):
            LocalServer("127.0.0.1", occupied.getsockname()[1], create_app().server)


def test_unknown_physical_state_keeps_stop_available_and_blocks_disconnect():
    snapshot = GuiController().snapshot()
    snapshot.update(
        connected=True,
        recovery=True,
        unknown=True,
        status={"homed": True},
        position={"degrees": 0},
    )
    disabled = controls(snapshot)
    assert not disabled["stop"]
    assert all(disabled[name] for name in CONTROL_IDS if name != "stop")
