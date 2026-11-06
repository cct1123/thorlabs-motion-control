"""Thin JSON CLI; parsing/help never open USB devices."""

import argparse
import json
import logging
import sys
from dataclasses import asdict

from .controller import K10CR2, MotionError
from .protocol import angle_to_counts


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Control one K10CR2 over WinUSB")
    parser.add_argument("--serial", required=True, help="Exact eight-digit serial")
    parser.add_argument("--verbose", action="store_true", help="Log raw APT traffic to stderr")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("info", "status", "position"):
        commands.add_parser(name)
    for name in ("home", "move", "stop"):
        command = commands.add_parser(name)
        command.add_argument("--timeout", type=float, default=None)
        if name == "move":
            command.add_argument("angle", type=float, help="Absolute angle in [0, 360) degrees")
    args = parser.parse_args(argv)
    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    try:
        if args.command == "move":
            angle_to_counts(args.angle)
        if args.command in ("home", "move", "stop") and args.timeout is not None:
            from .controller import _valid_timeout

            _valid_timeout(args.timeout)
        with K10CR2(args.serial) as mount:
            if args.command == "move":
                result = mount.move_absolute(args.angle, timeout=args.timeout)
            elif args.command in ("home", "stop"):
                result = getattr(mount, args.command)(timeout=args.timeout)
            else:
                result = getattr(mount, args.command)()
        print(json.dumps(asdict(result), indent=2))
        return 0
    except MotionError as exc:
        print(f"k10cr2: {exc}", file=sys.stderr)
        return 130 if isinstance(exc.original, KeyboardInterrupt) else 1
    except KeyboardInterrupt:
        print("k10cr2: interrupted", file=sys.stderr)
        return 130
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"k10cr2: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
