"""Thin JSON CLI; parsing/help never open USB devices."""

import argparse
import json
import logging
import sys
from dataclasses import asdict

from .controller import K10CR2, MotionError
from .settings import resolve_target


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
            command.add_argument("angle", type=float, help="Target in degrees (fixed range: 0–360)")
            command.add_argument(
                "--mode", choices=("fixed", "total", "equivalent"), default="fixed"
            )
            command.add_argument(
                "--direction",
                choices=("quickest", "forward", "reverse"),
                default="quickest",
                help="Route for equivalent-angle mode (default: quickest)",
            )
    args = parser.parse_args(argv)
    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    try:
        if args.command == "move":
            resolve_target(args.angle, 0, args.mode, args.direction)
        if args.command in ("home", "move", "stop") and args.timeout is not None:
            from .controller import validate_timeout

            validate_timeout(args.timeout)
        with K10CR2(args.serial) as mount:
            if args.command == "move":
                result = mount.move_absolute(
                    args.angle, mode=args.mode, direction=args.direction, timeout=args.timeout
                )
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
