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
Startup only shows the UI; click **Connect** for readback. If the mount is not
homed, use **Home** explicitly before positioning. Close other software that owns
the mount first.
To update, Stop active motion, Disconnect, and Close web UI. Restart the server
with the updated software, then reload every open GUI tab before reconnecting.
Stale callback layouts are rejected with a reload-required response.

The panel provides Move/profile editing, Home, Stop, held Drive/Jog controls,
Enable/Disable, Identify, serial/position/status and limit indicators. Settings
include display units, Fixed/Equivalent/Total rotation modes, forward/reverse/
quickest routing, velocity/jog/backlash and hardware-button parameters. **Connect**
reads all five device settings groups; **Refresh readbacks** reloads them and shows
the read time. **Refresh status** reads status and position without replacing
settings or settings drafts. Setting writes require fresh readback, validation and verification.
The GUI defaults to **Equivalent Angle**: reverse Jog/Drive can cross zero and the
display wraps through 360°. **Fixed Range** remains an explicit single-turn option;
**Total Angle** shows signed accumulated rotation. All modes retain the counted
±15,480° software guard at the manual's display/counting bound; it is not a mechanical stop.
One **Apply changes** action validates the complete form and applies edited device
settings, then publishes the session display/rotation/drive options. An unchanged
form sends no configuration request. Device-setting failure leaves session options
unchanged and reports that device changes may be partial. Persistence is opt-in;
an EEPROM write is reported as requested, not power-cycle proven. **Close**, × and
Escape discard unapplied settings edits. Dialog feedback stays visible beside the actions.

Unverified S-curve, power, trigger and factory-reset controls are visibly unavailable;
stage-specific settings remain locked as instructed by the manual. See the full
[GUI mapping, packet references and limitations](docs/gui-manual.md). The original
[manual and protocol PDFs](docs/references/README.md) are retained in the repository.

One worker owns USB; motions are not queued. Release, lost focus or an expired
browser heartbeat requests a stop for held controls. Software Stop is not hard
real time. A confirmed operator Stop on a healthy connection allows another command
without reconnecting, after any temporary velocity profile is restored and verified.
Query/motion faults still require explicit recovery; an unconfirmed stop
retains **physical state UNKNOWN**. **Close web UI** or console **Ctrl+C** waits
for the owner before releasing USB. Closing a tab alone leaves the server running;
finite moves continue, while held controls expire. All tabs share the controller.
The server binds only to loopback and rejects foreign-host/cross-origin writes.
Normal and recording servers share shutdown handling: a timed-out, interrupted or
failed shutdown keeps the controls available for Stop/retry.

Earlier [migrated-GUI](docs/bench/gui-migration-20261106-231335Z.md) and
[held-jog](docs/bench/gui-jog-20261107-002109Z.md) bench records cover small motions,
release/focus/Stop behavior and exact settings restoration on serial 55551174,
firmware 2.0.1. The latest cyclic-default and settings migration is validated offline.
Independent accuracy, full/multi-turn travel, ordinary OS window switching and fault
scenarios still need bench acceptance. Install the `gui` extra for GUI tests; core
library/CLI use does not require Dash.

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
An already-homed counter outside Fixed Range can move back to a target inside it.
In Fixed Range, manual controls permit inward recovery: finite jogs may approach or enter the
interval without crossing the opposite boundary; held Drive/continuous Jog stops at
the nearest boundary. The ±15,480° counted bound still applies to the current position.
CLI, GUI and library share the same modes: `fixed`, `total` (±15,480°) and
`equivalent` (0–360° phase resolved from a fresh counter). For equivalent routing,
choose `--direction quickest`, `forward` or `reverse`; use `--mode` to select the mode.
The GUI starts in `equivalent`; CLI and library defaults remain `fixed`.
The device's backlash settings remain in effect.

Home/move block until fresh status and position confirm completion. Homing must show
a new homing/not-homed transition. A move already at its target is reported as a
`no_op`. A short move can finish without a captured busy flag if the fresh position
counter changed to the requested target. Completion notification receipt is reported
separately from these status/counter checks. Loss of motor enable during Home/Move,
or loss of the home reference during an absolute Move, triggers same-owner Stop
cleanup and requires explicit recovery.

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
and a separate stop-cleanup result. A clean cooperative cancellation has
`MotionError.recovery_required == False`; the connection remains usable after a
confirmed stop and verified temporary-profile restoration. The GUI reports this
as an informational Stop. Cancellation observed before enabling/motion
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
node tests/hold.test.mjs
uv run --locked ruff check .
uv run --locked ruff format --check .
uv build --no-sources
```

The current [repository review](docs/reviews/repository-review-20261108.md) records
**354 Python and 2 JavaScript tests**, regression fixes, the single-script GUI
migration, offline packaging and generated-cache cleanup. See the
[validation record](docs/validation.md) for checkpoint-specific evidence and
[command reference](docs/commands.md) for protocol details. Tests use independent
literal frames, saved traffic and mocked USB; they do not operate the mount.

Runtime source is organized as follows:

- `gui.py`: GUI owner, layout, callbacks, local HTTP routes and server lifecycle.
- `gui_forms.py`: pure named-form conversion and session-option validation.
- `controller.py`: explicit-connect device API, motion ownership and recovery.
- `protocol.py`: APT packet/layout definitions, frame parsing and reply validation.
- `settings.py`: units, limits, target resolution and editable-setting validation.
- `transport.py`: lazy USB initialization, FTDI bridge setup and bounded transfers.
- `cli.py`: JSON command-line interface.

The [capability catalogue](docs/command-catalog.md) distinguishes implemented
commands, documented possibilities and unsupported K10CR2 mappings; the
[GUI contract](docs/gui-manual.md) describes settings and operator behavior.
Historical [6 November review](docs/reviews/repository-review-20261106.md) and
[bench records](docs/validation.md) retain their original source checkpoints.
Controller-reported counters and completion do not establish independent angular
accuracy. Physical speed calibration, reboot persistence, long sessions and
coexistence stability remain unverified.
