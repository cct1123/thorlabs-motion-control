"""Operator-facing regressions in the manual-style control panel."""

from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("dash")

from k10cr2.gui_state import GuiController  # noqa: E402
from k10cr2.gui_view import controls, create_app  # noqa: E402


def test_expired_manual_lease_cannot_be_revived_by_a_late_heartbeat(monkeypatch):
    import k10cr2.gui_state as module

    owner = GuiController()
    owner._hold_token = "expired-lease"
    owner._hold_until = 10
    owner._state["busy"] = "drive-forward"
    monkeypatch.setattr(module.time, "monotonic", lambda: 11)
    assert "inactive" in owner.hold("heartbeat", "expired-lease")
    assert not owner._held()


def test_unknown_state_blocks_motion_even_without_a_recovery_flag():
    snapshot = GuiController().snapshot()
    snapshot.update(
        connected=True,
        unknown=True,
        recovery=False,
        status={"homed": True, "enabled": True},
        position={"degrees": 0},
    )
    disabled, _ = controls(snapshot, 90)
    assert not disabled["stop"]
    assert disabled["disconnect"]
    assert disabled["home"] and disabled["move-to"] and disabled["disable"]


def test_worker_rejects_motion_while_physical_state_is_unknown():
    owner = GuiController()
    owner._mount = SimpleNamespace(recovery_required=False)
    owner._state.update(
        connected=True,
        unknown=True,
        recovery=False,
        status={"homed": True, "enabled": True},
        position={"degrees": 0},
    )
    assert "UNKNOWN" in owner.submit("move-to", target=90)
    assert owner.snapshot()["busy"] is None


def test_move_request_closes_dialog_so_stop_is_accessible(monkeypatch):
    import k10cr2.gui_view as view

    calls = []

    def submit(command, **kwargs):
        calls.append(command)
        return f"{command} requested."

    app = create_app(SimpleNamespace(submit=submit))
    callback = next(
        item
        for item in app.callback_map.values()
        if any(
            output.component_id == "panel"
            for output in (item["output"] if isinstance(item["output"], list) else [item["output"]])
        )
    )
    state = {
        "serial": "55551174",
        "target": 90,
        "apply-groups": [],
        "persist": [],
        "panel": "move",
    }
    args = [0] * len(callback["inputs"]) + [state.get(item["id"]) for item in callback["state"]]
    monkeypatch.setattr(view, "ctx", SimpleNamespace(triggered_id="move-to"))
    message, panel, _ = callback["callback"].__wrapped__(*args)
    assert calls == ["move-to"] and message == "move-to requested."
    assert panel is None


def test_manual_link_remains_usable_outside_the_source_checkout(monkeypatch):
    import k10cr2.gui_view as view

    # An installed wheel has no repository-level docs/hardware directory.
    installed = Path(__file__).resolve().parents[1] / ".tmp/review-installed-layout"
    monkeypatch.setattr(view, "__file__", str(installed / "site-packages/k10cr2/gui_view.py"))
    app = create_app()
    response = app.server.test_client().get("/manual", base_url="http://127.0.0.1")
    assert response.status_code in {200, 302}
    if response.status_code == 302:
        assert response.location.startswith("https://media.thorlabs.com/")
        assert "etn064249-d02.pdf" in response.location


@pytest.mark.parametrize(
    "button", ["move-open", "position-open", "settings-open", "drive-open", "jog-open"]
)
def test_opening_another_dialog_cannot_obscure_stop_during_motion(monkeypatch, button):
    import k10cr2.gui_view as view

    snapshot = GuiController().snapshot()
    snapshot.update(connected=True, busy="move-to")
    app = create_app(SimpleNamespace(snapshot=lambda: snapshot))
    callback = next(
        item for key, item in app.callback_map.items() if "request-result.children" in key
    )
    args = [0] * len(callback["inputs"]) + [None] * len(callback["state"])
    monkeypatch.setattr(view, "ctx", SimpleNamespace(triggered_id=button))
    _, panel, _ = callback["callback"].__wrapped__(*args)
    assert panel is None or panel is view.no_update
