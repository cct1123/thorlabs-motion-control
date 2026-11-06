"""Local Thorlabs-style GUI; all hardware communication uses our WinUSB/APT code."""

import argparse
import logging
import socket

from werkzeug.serving import ThreadedWSGIServer

from .gui_state import GuiController
from .gui_view import create_app


class LocalServer(ThreadedWSGIServer):
    """Reject occupied ports, including Windows SO_REUSEADDR double binds."""

    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


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
