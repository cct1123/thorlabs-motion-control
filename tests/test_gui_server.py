"""No-hardware checks for the normal and recording GUI server lifecycle."""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("dash")


def test_bench_retains_server_and_recorder_until_owner_shutdown_succeeds(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "tools/bench/record_gui.py"
    spec = importlib.util.spec_from_file_location("record_gui", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls, events = [], []
    attempts = iter((False, True))

    class Owner:
        def shutdown(self):
            stopped = next(attempts)
            calls.append(("owner-shutdown", stopped))
            return stopped

        def snapshot(self):
            return {"error": "stop not confirmed"}

    class Server:
        def __init__(self, *args):
            self.runs = 0

        def serve_forever(self):
            calls.append("serve")
            self.runs += 1
            if self.runs == 1:
                raise KeyboardInterrupt

        def shutdown(self):
            pass

        def server_close(self):
            calls.append("server-close")

    from flask import Flask

    recorder = SimpleNamespace(
        base=Path("not-created"),
        write=lambda kind, **data: events.append((kind, data)),
        close=lambda: calls.append("recorder-close"),
    )
    monkeypatch.setattr(module, "Recorder", lambda base: recorder)
    monkeypatch.setattr(module, "BenchOwner", lambda *args, **kwargs: Owner())
    monkeypatch.setattr(module, "LocalServer", Server)
    monkeypatch.setattr(
        module, "create_app", lambda *args, **kwargs: SimpleNamespace(server=Flask("bench"))
    )
    monkeypatch.setattr(sys, "argv", [str(path), "--serial", "55551174"])
    assert module.main() == 0
    assert calls == [
        "serve",
        ("owner-shutdown", False),
        "serve",
        ("owner-shutdown", True),
        "server-close",
        "recorder-close",
    ]
    assert events[-1][0] == "session_end" and events[-1][1]["shutdown"]
