# K10CR2 controller

A small synchronous Python library and CLI for one Thorlabs K10CR2 rotation mount on
Windows, using our WinUSB/PyUSB/libusb/APT communication. Includes a flat web UI,
identity/status/position queries, Home/Move/Jog/Drive/Stop and verified settings
readback/editing. No Kinesis application, SDK, pythonnet or manufacturer runtime
is used.

## Local graphical interface

The GUI follows the Thorlabs K10CR2 panel (Figure 16) and settings in the
[manufacturer manual](docs/references/k10cr2-user-guide-etn064249-d02.pdf).
All communication is our own WinUSB/APT implementation; no Kinesis or SDK is used.
The [migration guide](docs/migration.md) records the consolidated API, removed
development interfaces and shared angle contract.

```powershell
uv sync --locked --extra gui
uv run --locked --extra gui k10cr2-gui --serial 55551174
```

Open **http://127.0.0.1:8050**; use `--port 8051` if that port is occupied.
Startup only shows the UI; click **Connect** for readback and **Home** explicitly
before positioning. Close other software that owns the mount first.

The panel provides Move/profile editing, Home, Stop, held Drive/Jog controls,
Enable/Disable, Identify, serial/position/status and limit indicators. Settings
include display units, Fixed/Equivalent/Total rotation modes, forward/reverse/
quickest routing, velocity/jog/backlash and hardware-button parameters. **Connect**
reads all five device settings groups; **Refresh readbacks** reloads them and shows
the read time. Setting writes require fresh readback, validation and verification.
Persistence is opt-in; an EEPROM write is
reported as requested, not power-cycle proven. Session display and drive options
have a separate **Apply session options** button. **Close**, × and Escape discard
unapplied settings edits. Dialog feedback stays visible beside the actions.

Unverified S-curve, power, trigger and factory-reset controls are visibly unavailable;
stage-specific settings remain locked as instructed by the manual. See the full
[GUI mapping, packet references and limitations](docs/gui-manual.md). The original
[manual and protocol PDFs](docs/references/README.md) are retained in the repository.

One worker owns USB; motions are not queued. Release, lost focus or an expired
browser heartbeat requests a stop for held controls. Software Stop is not hard
real time. Query/motion faults require explicit recovery; an unconfirmed stop
retains **physical state UNKNOWN**. **Close web UI** or console **Ctrl+C** waits
for the owner before releasing USB. Closing a tab alone leaves the server running;
finite moves continue, while held controls expire. All tabs share the controller.
The server binds only to loopback and rejects foreign-host/cross-origin writes.

The [migrated-GUI hardware test](docs/bench/gui-migration-20261106-231335Z.md) passed
small Fixed/Total/Equivalent moves, all three Equivalent directions, repeated-phase
no-ops, Jog, Drive/release, Stop and all five settings readbacks on serial 55551174,
firmware 2.0.1. Two defects were fixed and retested; original raw settings were
restored exactly. The [earlier test](docs/bench/gui-controls-20261106-211627Z.md)
also covers Home. Independent accuracy, full/multi-turn travel and fault scenarios
remain unverified. Install the `gui` extra for GUI tests; core library/CLI use does
not require Dash.

## Setup

Install [uv](https://docs.astral.sh/uv/). From this repository:

```powershell
uv sync --locked
uv run --locked k10cr2 --help
```

Python 3.12 is selected by `.python-version`; validation used 3.12.7. `uv.lock` pins
runtime and development dependencies. `--locked` rejects a missing/stale lockfile.
For runtime-only installation use `uv sync --locked --no-dev`.

The mount must already use the **WinUSB** Windows driver. Driver installation is a
separate operator step; this package does not install or replace drivers. The tested
USB bridge is VID `0403`, PID `FAF0`, bcdDevice `1000`, with 64-byte bulk endpoints.
Match the mount's **name and serial**, because another Thorlabs device may share the
USB IDs. A WinUSB binding does not provide Kinesis/D2XX/COM access to that device.
Other serial adapters should retain their own driver bindings.

## Query the mount

Supply the actual eight-digit serial; `55551174` is the unit tested in this project.

```powershell
uv run --locked k10cr2 --serial 55551174 info
uv run --locked k10cr2 --serial 55551174 status
uv run --locked k10cr2 --serial 55551174 position
```

Results are JSON. Add `--verbose` before the subcommand to log raw APT traffic to
stderr. Queries configure the USB-to-UART bridge and request data; they do not enable
or move the motor. Imports and CLI help do not access USB.

## Home, move, stop

These commands **cause physical motion**. Run them only with the mounted setup ready.

```powershell
uv run --locked k10cr2 --serial 55551174 home --timeout 120
uv run --locked k10cr2 --serial 55551174 move 90 --timeout 120
uv run --locked k10cr2 --serial 55551174 stop --timeout 5
```

An absolute move requires a fresh homed status. Fixed Range accepts finite targets
in `0 <= angle <= 360` degrees, rounded to the nearest count (ties to even).
The 360° endpoint is a complete counted turn; targets do not silently wrap.
CLI, GUI and library share the same modes: `fixed`, `total` (±15,480°) and
`equivalent` (0–360° phase resolved from a fresh counter). For equivalent routing,
choose `--direction quickest`, `forward` or `reverse`; use `--mode` to select the mode.
The device's backlash settings remain in effect.

Home/move block until fresh status and position confirm completion. Homing must show
a new homing/not-homed transition. A move already at its target is reported as a
`no_op`. A short move can finish without a captured busy flag if the fresh position
counter changed to the requested target. Completion notification receipt is reported
separately from these status/counter checks.

While a blocking home/move owns the connection, **Ctrl+C** attempts a profiled stop
through that same connection. Do not start another CLI or application to interrupt it.
The standalone `stop` command is for a mount with no other active owner. There is one
owner per serial within this process; cross-process ownership is the operator's
responsibility. Close other control software before using this package.

## Python library

```python
from k10cr2 import K10CR2

with K10CR2("55551174") as mount:
    print(mount.info())
    print(mount.status())
    print(mount.position())
```

Explicit motion uses `mount.home()`, `mount.move_absolute(90)`, and `mount.stop()`.
Each accepts a positive finite `timeout` in seconds. Constructor defaults are
`query_timeout=3`, `motion_timeout=120`, and `cleanup_timeout=5`.
Construction alone does not connect; `connect()` / `close()` are also available.
Close releases the USB handle and leaves motor enable/holding state unchanged.

`query_settings()` reads velocity, jog, backlash, home and hardware buttons.
`configure(changes, high_power=False, persist=False)` validates all selected groups
before writing and verifies each changed group. Home edits and unverified mappings
remain unavailable. Confirmation flags require actual booleans; see the
[settings contract](docs/gui-manual.md#settings-and-wire-support).

`home()` and `move_absolute()` also accept an optional `threading.Event` as
`cancel`, plus `on_progress(status, position)`. Setting the event requests
cooperative cancellation through the active owner. After a motion command,
cancellation raises `MotionError` with `MotionCancelled` as its original cause
and a separate stop-cleanup result. Cancellation observed before enabling/motion
raises `MotionCancelled` directly. Progress callbacks run synchronously on the
owner thread, must return promptly, and must not call the controller. They add a
position query to each motion poll; observer failures trigger stop cleanup.

Query timeouts or protocol failures require close and explicit reconnect before
normal operations resume, avoiding ambiguous delayed same-ID replies. A failed
motion raises `MotionError`, retaining `original` and `cleanup` records. Cleanup has
its own deadline; it is not cut off by an expired motion deadline. A failed or
unconfirmed stop reports **physical state unknown**. Motion is never replayed
automatically. Reconnecting alone does not establish a new physical home reference;
query state and explicitly home when required.

Timeouts bound software operations and propagate to USB transfers. USB enumeration,
Windows driver calls, scheduling, and resource release cannot provide a hard real-time
guarantee. A software stop acknowledgement is controller evidence, not independent
proof of mechanical rest or optical safety.

## Development and evidence

```powershell
uv run --locked --extra gui pytest
uv run --locked ruff check .
uv run --locked ruff format --check .
uv build --no-sources
```

See [command reference](docs/commands.md) and [validation record](docs/validation.md).
The [holistic review](docs/reviews/holistic-review-20261106.md) records resolved
flaws, migration, cleanup, package checks and final live readback. Older dated
reviews describe their historical checkpoints.
The [full capability/coverage catalogue](docs/command-catalog.md) distinguishes
implemented commands, additional documented queries/settings, explicit exclusions,
and unresolved K10CR2 applicability. Current settings and manual-motion extensions
are mapped separately in [the GUI contract](docs/gui-manual.md).
The tests use independent literal frames, saved device traffic, scripted controller
responses, and mocked USB calls. They do not move real hardware.

Packaged identity/status/position queries, homing, a move to 90 degrees, and return
to zero passed on serial 55551174, firmware 2.0.1. Profiled stop and real console
Ctrl+C during motion also passed, with stop acknowledgements, position/status
queries, and successful returns to zero. The controller reported fresh completion
messages, expected counts, and stopped/homed status. Additional angles and independent
direction/angle observations still need bench acceptance.
A position counter agreement does not establish physical angular accuracy. Reboot
persistence, long sessions, and coexistence stability remain unverified.
