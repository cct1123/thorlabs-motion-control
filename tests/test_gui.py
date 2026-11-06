import os
import subprocess
import sys

import pytest

pytest.importorskip("dash")

from k10cr2.gui_state import GuiController  # noqa: E402
from k10cr2.gui_view import CONTROL_IDS, controls, create_app  # noqa: E402


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
    disabled, _ = controls(status, 90)
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

    return app.server.test_client().post(
        "/_dash-update-component",
        base_url="http://127.0.0.1",
        json={
            "output": key,
            "outputs": outputs,
            "inputs": inputs(meta["inputs"]),
            "state": inputs(meta["state"]),
            "changedPropIds": [changed],
        },
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


def test_manual_is_served_from_the_repository():
    response = create_app().server.test_client().get("/manual", base_url="http://127.0.0.1")
    assert response.status_code == 200 and response.data.startswith(b"%PDF")


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
    for meta in app.callback_map.values():
        for field in meta["inputs"] + meta["state"]:
            assert field["id"] in ids
        outputs = meta["output"] if isinstance(meta["output"], list) else [meta["output"]]
        assert all(out.component_id in ids for out in outputs)


def test_settings_tabs_and_display_apply_do_not_access_usb():
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
        **{"apply-groups": [], "persist": [], "panel": "settings"},
    )
    response = post_callback(app, key, values, "apply-display.n_clicks")
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
            "apply-groups": ["velocity"],
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
    assert response.json["response"]["apply-groups"]["value"] == []
    assert response.json["response"]["persist"]["value"] == []


def test_completed_feedback_replaces_request_and_reaches_both_dialogs():
    from k10cr2.gui_view import visible_feedback

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
    disabled, _ = controls(snapshot, 90)
    assert not disabled["stop"]
    assert all(disabled[name] for name in CONTROL_IDS if name != "stop")
