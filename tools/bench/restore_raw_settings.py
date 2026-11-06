"""Restore only velocity/jog/backlash from a captured baseline at homed, idle zero.

Bench cleanup, using our WinUSB/APT controller only. No motion or EEPROM packet.
Raw restoration preserves the exact pre-test values without rounded display units.
"""

import argparse
import json
import logging
import sys
import time
from dataclasses import asdict
from pathlib import Path

from k10cr2 import K10CR2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    baseline = json.loads(args.baseline.read_text(encoding="utf-8-sig"))
    target = baseline["settings"]
    serial = baseline["identity"]["serial"]
    if not baseline["connected"] or baseline["position"]["counts"] != 0:
        raise ValueError("Baseline must capture a connected mount at zero")
    if any(values["channel"] != 1 for values in target.values()):
        raise ValueError("Only channel 1 is allowed")
    if args.output.exists() or args.output.with_suffix(".log").exists():
        raise FileExistsError("Do not overwrite bench records")
    formatter = logging.Formatter("%(asctime)sZ %(message)s")
    formatter.converter = time.gmtime
    handler = logging.FileHandler(args.output.with_suffix(".log"), encoding="utf-8", mode="x")
    handler.setFormatter(formatter)
    logger = logging.getLogger("k10cr2.transport")
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    result = dict(serial=serial, baseline=str(args.baseline), motion=False, eeprom=False)
    try:
        with K10CR2(serial) as mount:
            status, position = mount.status(), mount.position()
            if status.busy or not status.homed or position.counts != 0:
                raise RuntimeError("Restoration requires fresh homed, idle zero")
            result["before"] = mount.query_settings()
            for group in ("home", "buttons"):
                if result["before"][group] != target[group]:
                    raise RuntimeError(f"Unexpected {group} change; refusing restoration")
            # One sequential owner, using the same verified APT setter/readback path.
            with mount._operation():
                deadline = mount._deadline(mount.query_timeout * 6, mount.query_timeout)
                for group in ("velocity", "jog", "backlash"):
                    if result["before"][group] != target[group]:
                        mount._write_setting(group, target[group], deadline)
            result["after"] = mount.query_settings()
            result["status"], result["position"] = asdict(mount.status()), asdict(mount.position())
            result["exact_match"] = result["after"] == target
            if not result["exact_match"] or result["position"]["counts"] != 0:
                raise RuntimeError("Final baseline/zero comparison failed")
    except BaseException as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        result["forbidden_modules"] = [
            n for n in sys.modules if n.split(".")[0] in {"clr", "pythonnet", "Thorlabs", "Kinesis"}
        ]
        args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        logger.removeHandler(handler)
        handler.close()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
