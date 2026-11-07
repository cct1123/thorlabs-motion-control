"""Record explicit real-mount GUI actions through the production WinUSB/APT owner.

Startup is read-only with respect to USB: the operator must click Connect. This
bench-only observer adds counted travel/manual-duration guards, not another reader.
It never initiates a move or applies a profile. Do not use it as a safety interlock.
"""

import argparse
import hashlib
import json
import logging
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from flask import g, request

from k10cr2.gui import LocalServer, serve_gui
from k10cr2.gui_state import MANUAL_COMMANDS, MOTION_COMMANDS, GuiController
from k10cr2.gui_view import create_app

ROOT = Path(__file__).resolve().parents[2]


class Recorder:
    def __init__(self, base):
        self.base = base
        self.lock = threading.Lock()
        self.started = time.monotonic()
        self.events = base.with_suffix(".jsonl").open("x", encoding="utf-8", buffering=1)
        self.traffic = base.with_suffix(".log").open("x", encoding="utf-8", buffering=1)

    def write(self, kind, **data):
        row = dict(
            utc=datetime.now(UTC).isoformat(timespec="milliseconds"),
            elapsed_s=round(time.monotonic() - self.started, 6),
            kind=kind,
            **data,
        )
        with self.lock:
            self.events.write(json.dumps(row, ensure_ascii=False) + "\n")
            if kind == "traffic":
                self.traffic.write(f"{row['utc']} +{row['elapsed_s']:.6f}s {data['message']}\n")

    def close(self):
        self.events.close()
        self.traffic.close()


class TrafficHandler(logging.Handler):
    def __init__(self, recorder):
        super().__init__()
        self.recorder = recorder

    def emit(self, record):
        self.recorder.write("traffic", message=record.getMessage())


class BenchOwner(GuiController):
    def __init__(self, recorder, *, max_degrees=3, manual_seconds=2):
        super().__init__()
        self.recorder = recorder
        self.max_degrees = max_degrees
        self.manual_seconds = manual_seconds
        self.motion_started = None
        self.guard_fired = False

    def _run(self, command, *args):
        self.motion_started = time.monotonic() if command in MOTION_COMMANDS else None
        self.guard_fired = False
        self.recorder.write(
            "operation_start", command=command, arguments=args, state=self.snapshot()
        )
        try:
            super()._run(command, *args)
        finally:
            self.recorder.write("operation_end", command=command, state=self.snapshot())
            self.motion_started = None

    def _readback(self, status, position):
        super()._readback(status, position)
        self.recorder.write("readback", status=vars(status), position=vars(position))
        command = self.snapshot()["busy"]
        manual_overdue = (
            command in MANUAL_COMMANDS
            and self.motion_started is not None
            and time.monotonic() - self.motion_started > self.manual_seconds
        )
        outside = (
            command in MOTION_COMMANDS
            and command != "home"
            and abs(position.degrees) > self.max_degrees
        )
        if (manual_overdue or outside) and not self.guard_fired:
            self.guard_fired = True
            self.recorder.write(
                "guard", command=command, manual_overdue=manual_overdue, outside=outside
            )
            self.submit("stop")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--port", type=int, default=8050)
    parser.add_argument("--label", choices=("gui-migration", "gui-jog"), default="gui-migration")
    parser.add_argument("--max-degrees", type=float, default=3)
    parser.add_argument("--manual-seconds", type=float, default=2)
    args = parser.parse_args()
    if len(args.serial) != 8 or not args.serial.isascii() or not args.serial.isdigit():
        parser.error("Serial must be eight ASCII digits")
    if not 1 <= args.port <= 65535:
        parser.error("Port must be 1–65535")
    if not 0 < args.max_degrees <= 3 or not 0 < args.manual_seconds <= 5:
        parser.error("Bench bounds require 0 < degrees <= 3 and 0 < seconds <= 5")
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%SZ")
    recorder = Recorder(ROOT / "docs/bench" / f"{args.label}-{stamp}")
    owner = BenchOwner(recorder, max_degrees=args.max_degrees, manual_seconds=args.manual_seconds)
    app = create_app(owner, serial=args.serial)
    server = LocalServer("127.0.0.1", args.port, app.server)
    app.server.config["GUI_SHUTDOWN"] = server.shutdown
    handler = TrafficHandler(recorder)
    logger = logging.getLogger("k10cr2.transport")
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    @app.server.before_request
    def capture_request():
        if request.method != "POST":
            return
        payload = request.get_json(silent=True) or {}
        if request.path == "/api/hold" or (
            request.path == "/_dash-update-component"
            and "request-result.children" in payload.get("output", "")
        ):
            g.bench_request = payload
            recorder.write("gui_request", path=request.path, payload=payload)

    @app.server.after_request
    def capture_response(response):
        if hasattr(g, "bench_request"):
            recorder.write(
                "gui_response",
                path=request.path,
                status=response.status_code,
                payload=response.get_json(silent=True),
            )
        return response

    sources = [*ROOT.glob("src/k10cr2/*.py"), *ROOT.glob("src/k10cr2/assets/*"), Path(__file__)]
    recorder.write(
        "session",
        serial=args.serial,
        communication="our WinUSB/PyUSB/libusb/APT only",
        source_hashes={
            str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources
        },
        guards=dict(
            counted_degrees=[-args.max_degrees, args.max_degrees],
            manual_seconds=args.manual_seconds,
        ),
    )
    print(
        json.dumps(dict(url=f"http://127.0.0.1:{args.port}", record=str(recorder.base))), flush=True
    )
    print("Click Connect. No USB access, motion or setting write occurs at startup.", flush=True)
    stopped = False
    try:
        stopped = serve_gui(server, owner)
    finally:
        recorder.write(
            "session_end",
            shutdown=stopped,
            state=owner.snapshot(),
            forbidden_modules=[
                n
                for n in sys.modules
                if n.split(".")[0] in {"clr", "pythonnet", "Thorlabs", "Kinesis"}
            ],
        )
        logger.removeHandler(handler)
        recorder.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
