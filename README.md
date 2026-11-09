# K10CR2 controller

A synchronous Python library, command-line interface and local web GUI for one
Thorlabs K10CR2 rotation mount on Windows. Communication uses WinUSB, PyUSB,
libusb and an independent APT implementation. No Kinesis application, SDK,
pythonnet or manufacturer runtime is required.

Supports identity/status/position queries, Home, absolute Move, Jog, held Drive,
Stop and verified settings editing. The GUI follows the K10CR2 manufacturer's
panel and settings; unsupported controls are omitted.

## Setup

Install [uv](https://docs.astral.sh/uv/), then run from this repository:

```powershell
uv sync --locked --extra gui
uv run --locked --extra gui k10cr2-gui --serial 55551174
```

Replace `55551174` with the mount's actual eight-digit serial. This is the unit
used for the recorded bench tests. Python 3.12 is selected by `.python-version`;
`uv.lock` pins runtime and development dependencies. `--locked` rejects a stale
or missing lockfile. For library/CLI use, omit `--extra gui`; add `--no-dev` to
`uv sync` for a runtime-only environment.

The mount must already use the **WinUSB** Windows driver. Driver installation is
a separate operator step; this package does not install or replace drivers. The
tested USB bridge is VID `0403`, PID `FAF0`, bcdDevice `1000`, with 64-byte bulk
endpoints. Match the mount's **name and serial** because other Thorlabs devices
may share those IDs. WinUSB binding does not provide Kinesis/D2XX/COM access;
other serial adapters should retain their own driver bindings.

## Local web GUI

Open [http://127.0.0.1:8050](http://127.0.0.1:8050); add `--port 8051` to the launch
command if needed. Startup does not access USB. Close other software that owns
the mount, then click **Connect** to read identity, status and all five settings
groups. If the mount is not homed, use **Home** explicitly before positioning.

The panel provides Move/profile editing, Home, Stop, Drive/Jog, Enable/Disable,
Identify and status/limit indicators. Settings has Moves / Jogs, Rotation and
Advanced tabs. **Apply changes** validates the complete form, applies edited
device groups with readback verification, then publishes session options. A
failure can leave partial device changes and requires recovery. EEPROM saving
is opt-in and reported as requested, not power-cycle proven. Close/×/Escape
discard unapplied edits.

**Refresh status** reads status and position; **Refresh readbacks** reloads the
five settings groups. Display units, rotation rules, USB supply confirmation and
Drive speeds are session options. Homing settings are read-only; S-curve, power,
trigger and factory-reset controls are unavailable. See the
[GUI contract](docs/gui-manual.md) for the full behavior and wire mappings.

One worker owns USB and motions are not queued. Release, lost focus or an expired
browser heartbeat requests a stop for held controls. A confirmed operator Stop on
a healthy connection permits another command after any temporary profile is
restored and verified. Communication/controller faults require explicit recovery;
an unconfirmed stop retains **physical state UNKNOWN**.

All tabs share the controller. Closing a tab leaves the server running and finite
moves continue; held controls expire. **Close web UI** or console **Ctrl+C** waits
for the owner before releasing USB. Failed shutdown keeps controls available for
Stop/retry. To update: stop motion, disconnect, close the server, update/restart,
then **reload every open GUI tab** before reconnecting. Stale callback layouts
are rejected. The server binds only to loopback and rejects foreign-host and
cross-origin writes.

## Command-line use

Queries return JSON and do not enable or move the motor:

```powershell
uv run --locked k10cr2 --help
uv run --locked k10cr2 --serial 55551174 info
uv run --locked k10cr2 --serial 55551174 status
uv run --locked k10cr2 --serial 55551174 position
```

Add `--verbose` before the subcommand to log raw APT traffic to stderr. Queries
configure the USB-to-UART bridge and request data. Imports and CLI help do not
access USB.

These commands **cause physical motion**; prepare the mounted setup first:

```powershell
uv run --locked k10cr2 --serial 55551174 home --timeout 120
uv run --locked k10cr2 --serial 55551174 move 90 --timeout 120
uv run --locked k10cr2 --serial 55551174 stop --timeout 5
```

During a blocking Home/Move, use **Ctrl+C in that same process** to request Stop
through its active owner. The standalone `stop` command is for a mount with no
other active owner. One owner per serial is enforced within a process;
cross-process ownership remains the operator's responsibility.

## Rotation modes

CLI, GUI and library share the same angle contract. Absolute moves require a
fresh home reference; existing device backlash settings remain in effect.

| Mode | Target and behavior | Default for |
| --- | --- | --- |
| `fixed` | 0–360° inclusive; 360° is a complete counted turn, distinct from zero | CLI/library |
| `equivalent` | 0–360° phase, resolved from the fresh counter using quickest/forward/reverse routing; display wraps | GUI |
| `total` | Signed accumulated angle within ±15,480° | Explicit selection |

Use `move 10 --mode equivalent --direction reverse` for an equivalent-angle CLI
move. Equivalent and Total Jog/Drive can cross zero and full turns. Fixed Range
manual controls permit inward recovery from outside its interval. Every mode
retains the ±15,480° counted software guard; it is not a mechanical stop. See the
[shared angle contract](docs/migration.md#shared-angle-contract) for routing,
rounding and boundary details.

## Python library

```python
from k10cr2 import K10CR2

with K10CR2("55551174") as mount:
    print(mount.info())
    print(mount.status())
    print(mount.position())
```

Construction alone does not connect; the context manager connects and closes.
Motion is explicit through `home()`, `move_absolute()` and `stop()`. Settings are
read with `query_settings()` and edited with `configure()`. The
[Python API guide](docs/python-api.md) covers timeouts, completion, cancellation,
settings and recovery. The [tutorial notebooks](examples/notebooks/README.md)
provide nine independent examples with device access disabled by default.

## Validation and limits

The [8 November 2026 live GUI test](docs/bench/gui-live-20261108.md) exercised
settings, cyclic Jog, Drive/release, Stop and subsequent motion on serial
55551174, firmware 2.0.1, including ±12° sweeps with a 20°/s, 20°/s² profile.
It reproduced and retested callback-rendering and one-count Move-completion
fixes, then restored all five raw settings groups and exactly zero counts.
That checkpoint passed **363 Python tests and two JavaScript event tests**.

Controller counters and completion do not establish independent angular accuracy.
Physical speed/acceleration calibration, full/multi-turn travel, EEPROM retention,
native OS focus switching, hardware fault injection and endurance remain open.
Software Stop is not hard real time; its acknowledgement is controller evidence,
not independent proof of mechanical rest or optical safety.

## Development

```powershell
uv run --locked --extra gui pytest
node tests/hold.test.mjs
uv run --locked ruff check .
uv run --locked ruff format --check .
uv build --no-sources
```

Tests use literal frames, saved traffic and mocked USB; they do not operate the
mount. Install the `gui` extra for GUI tests. Runtime source lives in `src/k10cr2/`:
`controller.py` owns the device API and recovery, `protocol.py` the APT layouts,
`settings.py` units and validation, `transport.py` USB, `cli.py` the CLI, and
`gui.py` the GUI owner, forms, callbacks and server lifecycle.

## Documentation

| Guide | Contents |
| --- | --- |
| [GUI contract](docs/gui-manual.md) | Controls, settings, ownership, packet mappings and unsupported features |
| [Python API](docs/python-api.md) | Connection, motion completion, cancellation and recovery |
| [Tutorial notebooks](examples/notebooks/README.md) | Environment checks through a bounded angle sequence |
| [Migration guide](docs/migration.md) | Shared angle contract, removed interfaces and reference paths |
| [Command reference](docs/commands.md) | APT frames, units, status and USB bridge setup |
| [Capability catalogue](docs/command-catalog.md) | Implemented commands and unverified mappings |
| [Validation record](docs/validation.md) | Dated software and hardware checkpoints with evidence links |
| [Manufacturer references](docs/references/README.md) | Original PDFs and provenance |
| [Bench tools](tools/bench/README.md) | GUI recording and exact raw-settings restoration |

Dated reviews and bench captures retain their original software versions and scope.
