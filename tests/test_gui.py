import os
import subprocess
import sys

import pytest

pytest.importorskip("dash")

from k10cr2.gui import COMMANDS, controls, create_app  # noqa: E402
from k10cr2.gui_state import GuiController  # noqa: E402


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
    disabled, _ = controls(status, 90, 1)
    assert not disabled["connect"]
    assert all(disabled[name] for name in COMMANDS if name != "connect")


def test_dash_initial_render_and_command_roundtrip_without_usb():
    controller = GuiController(lambda serial: pytest.fail("Unexpected USB access"))
    app = create_app(controller)
    client = app.server.test_client()
    render_key = next(key for key in app.callback_map if key != "request-result.children")
    outputs = [
        {"id": out.component_id, "property": out.component_property}
        for out in app.callback_map[render_key]["output"]
    ]
    response = client.post(
        "/_dash-update-component",
        base_url="http://127.0.0.1",
        json={
            "output": render_key,
            "outputs": outputs,
            "inputs": [
                {"id": "refresh", "property": "n_intervals", "value": 0},
                {"id": "target", "property": "value", "value": 90},
                {"id": "step", "property": "value", "value": 1},
            ],
            "state": [],
            "changedPropIds": ["refresh.n_intervals"],
        },
    )
    assert response.status_code == 200
    rendered = response.json["response"]
    assert rendered["angle"]["children"] == "—"
    assert rendered["stop"]["disabled"]
    response = client.post(
        "/_dash-update-component",
        base_url="http://127.0.0.1",
        json={
            "output": "request-result.children",
            "outputs": {"id": "request-result", "property": "children"},
            "inputs": [
                {"id": name, "property": "n_clicks", "value": int(name == "connect")}
                for name in (*COMMANDS, "close-gui")
            ],
            "state": [
                {"id": "serial", "property": "value", "value": "bad"},
                {"id": "target", "property": "value", "value": 90},
                {"id": "step", "property": "value", "value": 1},
            ],
            "changedPropIds": ["connect.n_clicks"],
        },
    )
    assert response.status_code == 200
    assert "eight-digit" in response.json["response"]["request-result"]["children"]


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
