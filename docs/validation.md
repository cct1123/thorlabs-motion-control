# Validation record

## Actual mount GUI controls: 6 November 2026, 15:14–15:22 CST

The requested GUI Home, small absolute Move, positive/negative single-step Jog,
momentary forward/reverse Drive, explicit Stop during motion, and all five settings
readback groups passed on serial **55551174**, firmware **2.0.1**, using only our
WinUSB/APT implementation. [Full results and raw evidence](bench/gui-controls-20261106-211627Z.md)
record exact counts, status, packets, timing scope and tested limits.

The first read-only attempt exposed a firmware exception: `GET_JOGPARAMS` (`0418`)
uses destination `80`, not `81`. A narrowly scoped parser allowance preserves
source, size and channel checks. The original failure is retained; the complete
actual frame is a regression fixture. After the correction, temporary low-speed
settings were applied and verified without EEPROM writes. The mount returned to
zero, all original raw settings were restored exactly, and a final GUI readback
confirmed every group matched. Final status was `0x80000400`; USB disconnected
cleanly. No Kinesis or SDK was loaded or used.

The updated suite passed **191 offline tests** and Ruff. These results establish
controller-level behavior on this unit; physical angular accuracy, certified stop
timing, continuous jog, held-jog repetition, EEPROM retention, untested actions and
unavailable mappings remain outside this bench check.

## Manufacturer GUI revision: 6 November 2026

The panel and settings now follow Figure 16 and chapter 7 of the manufacturer's
ETN064249-D02 manual. Unmodified manual and protocol PDFs with SHA256 provenance
are retained under [hardware](hardware/README.md). The current implementation
contract and unavailable mappings are in [gui-manual.md](gui-manual.md).

The full offline suite covers literal parameter packets, strict response layouts,
read/validate/write/readback/persist ordering, whole-draft validation before writes,
explicit enable/disable/identify, native jog, finite held-drive targets and profile
restore, rotation/display units, browser lease expiry and cross-tab ownership,
Stop at completion and during a query, unknown-state gating, shutdown, modal Stop
access, packaged manual fallback, and all prior tests. Ruff and package checks
are included. These tests do not validate K10CR2 firmware acceptance, physical
profile scales, resting/trigger electrical behavior, EEPROM retention, or timing.

A browser check using a simulated APT transport verified explicit connection/home,
parameter readbacks, a velocity edit with readback, native single-step jog, and held
drive followed by release and confirmed idle. The real GUI was opened disconnected.
No real USB device was accessed, enabled, configured or moved by these checks.
Earlier bench records below apply to the core controller, not these new GUI paths.

The final repository review passed **189 offline tests**, Ruff lint and formatting,
JavaScript syntax checking, lockfile validation, and wheel/source-distribution
builds. An isolated installed wheel serves the GUI assets and local manufacturer
manual without importing USB. One third-party Dash pytest-plugin deprecation
warning remains. Findings, staged commits, and software/hardware limits are in
[the repository review](review-2026-11-06.md).

## Initial reference GUI validation: 6 November 2026

The optional Dash GUI is adapted from
`C:\Users\HFNV\Dev\rotation_mount\src\rotation_mount\gui.py` and its stylesheet,
using this package's WinUSB controller rather than the reference Kinesis backend.
Automated offline coverage includes read-only startup/connect, worker ownership,
non-queued commands, fresh finite-step targets, bounds, cooperative Stop, progress,
failed-stop state, explicit recovery, shutdown ordering/timeouts, idempotent close,
Dash layout/assets/callbacks, and foreign-host/cross-origin request rejection.
The existing CLI and protocol tests remain included. This validation does not
establish hardware timing or physical GUI Stop behavior; prior bench records below
predate the GUI and its cooperative cancellation/progress additions.

A browser session using an injected simulated mount verified connect, explicit
home, live counted-position updates, Stop confirmation, reconnect gating, and
disconnect. The actual GUI was opened disconnected, without USB access. Wheel
and source distribution builds passed; the wheel contains the GUI entry point,
owner module, and packaged stylesheet. No real mount was moved by these GUI checks.

## Prior bench evidence: 6 November 2026

Unit: K10CR2 serial **55551174**, firmware **2.0.1**, hardware revision **4**.
Windows driver: WinUSB, `kinesis_k10cr2_rotary_stage.inf`, installed as `oem187.inf`.
Shared FTDI bus/serial files remained version 2.12.36.20 after the binding change.
Driver-store names are machine-local and should not be assumed on another computer.

Earlier helpers successfully performed identity, status, explicit homing, and
position queries. Recorded home completion was `44 04 01 00 01 50`; final status
was `0x80000400` (homed, enabled, stopped), with position counter zero.

Raw captures are kept in `tests/fixtures/bench-home-20261106.json` and
`tests/fixtures/bench-status-20261106.json`. `identity.hex` is an actual received frame
from the status capture. Tests parse those independently of synthetic controller
responses. Replaying an old capture is offline regression validation, not a new
physical test of this package.

## Packaged query check: 6 November 2026, 14:30 CST

The installed package successfully connected to serial 55551174 and queried identity,
status, and position. It returned firmware 2.0.1, hardware revision 4, status
`0x80000400`, and zero position counts. No enable, home, move, stop, firmware, or
persistent-setting APT command was sent during this check. UART bridge setup was
performed as documented.

Evidence: [query results](bench/package-query-20261106.json) and
[raw APT traffic](bench/package-query-20261106.log).

## Package checks

The command-table/query milestone passed **53 offline tests** before motion code was
added. Subsequent tests cover home/move/stop, fresh transitions and target counters,
partial writes, deadlines, Ctrl+C, cleanup failure, single ownership, and captured
hardware traffic. The motion/stop milestone passed **87 offline tests** on Python
3.12.7. The issue-48 audit added seven fault-layout/decoding and source-catalogue
test cases. That historical protocol-audit record reported **97 focused tests**
and **131 full-repository tests** with concurrent GUI/notebook additions. Current
review counts are recorded above. Ruff checks and the distribution build also
passed. The Dash pytest plugin emitted one third-party deprecation warning.

Locked uv installation and lockfile validation, pytest, Ruff lint/format checks,
and wheel/sdist build all passed. The built wheel was also installed into an isolated
uv environment; package import and CLI help passed without importing USB. No
QMI/Thorlabs SDK runtime is used. Tests mock the USB interface and do not actuate
hardware. Built artifacts are in `dist/` and are excluded from Git.

## Packaged home and movement: 6 November 2026, 14:33 CST

The explicitly authorized sequence completed on serial 55551174, firmware 2.0.1:

| Step | Controller position | Final status | Completion received | Duration |
| --- | --- | --- | --- | --- |
| Home | 0 counts / 0 degrees | 0x80000400 | 0444, short frame | 2.562 s |
| Absolute move to 90 | 12,288,000 counts / 90 degrees | 0x80000400 | 0464, 14-byte payload | 10.063 s |
| Absolute return to zero | 0 counts / 0 degrees | 0x80000400 | 0464, 14-byte payload | 11.594 s |

Separate position/status queries after reaching 90 degrees and after returning to
zero matched those results. Both readbacks reported homed, enabled, not moving or
homing, and no motion-error flag. Three motion commands were sent: one home and two
absolute moves; no retries or stop cleanup were required. No motion-profile,
persistent-setting, or firmware command was sent.

Evidence: [sequence results and package source hashes](bench/package-motion-20261106-143305.json)
and [timestamped APT traffic](bench/package-motion-20261106-143305.log).
The actual move-complete frames are also in `tests/fixtures/move-completed-90.hex`
and `move-completed-zero.hex`. These checks establish the requested controller
workflow. Physical angle accuracy and direction were not independently measured.

## Profiled stop and real Ctrl+C: 6 November 2026, 14:38-14:39 CST

Both tests began at zero on serial 55551174, firmware 2.0.1, with the channel already
enabled, homed, and stopped. Both moved toward 90 degrees and stopped partway through.

| Test | Stopped counts | Stopped position | Status after stop | Final position |
| --- | --- | --- | --- | --- |
| Explicit profiled stop | 2,065,104 | 15.1252734375 degrees | 0x80000400 | 0 counts / 0 degrees |
| Real console Ctrl+C | 9,373,739 | 68.6553149414 degrees | 0x80000400 | 0 counts / 0 degrees |

The explicit-stop test started a nonblocking move through the package's encoder and
the already-verified owner's USB transport, observed moving status, waited two
seconds, and called public `stop()`. This bench-only start uses the same connection
and one reader; no second process or public asynchronous-motion API was introduced.
The synchronous public movement call would otherwise hold the operation lock until
completion. Separate position/status queries followed the stop acknowledgement.

The Ctrl+C test called public `move_absolute(90)` and received an actual console
Ctrl+C through the terminal PTY. A no-hardware console probe first verified interrupt
delivery. The package raised `MotionError` with original `KeyboardInterrupt`, sent a
profiled stop through the active owner, received the acknowledgement, and recorded
confirmed controller-stopped cleanup. `recovery_required` was true as designed.
The bench runner explicitly closed/reconnected before fresh position/status queries
and the authorized return to zero. It did not replay the interrupted move to 90.
The terminal host reported exit code 1 after Ctrl+C, as in the no-hardware probe;
the Python runner completed cleanup, returned to zero, saved `success: true`, and
closed the USB handle. This was a library interruption test, not a CLI exit-code test.

Each capture contains exactly one `65 04 01 02 50 01` profiled-stop request and one
0466 reply with a 14-byte payload. Final independent controller queries after each
return reported zero, homed, enabled, not moving/homing, and no motion-error flag.
The profiled-stop API round trip was 0.012 seconds in this run; this is a software/
controller observation and does not measure mechanical deceleration or rest time.
No firmware, EEPROM, velocity, acceleration, or home-parameter write was sent.

Evidence:

- [Explicit profiled-stop results](bench/package-profiled-stop-20261106-143829.json)
  and [APT log](bench/package-profiled-stop-20261106-143829.log).
- [Real Ctrl+C results](bench/package-ctrlc-stop-20261106-143848.json)
  and [APT log](bench/package-ctrlc-stop-20261106-143848.log).
- Exact received stop frames: `tests/fixtures/profiled-stopped.hex` and
  `tests/fixtures/ctrlc-stopped.hex`.

## Protocol audit: 6 November 2026

Reviewed current Thorlabs protocol **DOC-103274 issue 48** (494 pages) and K10CR2
user guide **ETN064249-D02 Rev A, August 2024**. The full command/query catalogue,
source hashes, explicit device exclusions, and firmware applicability limits are
recorded in [command-catalog.md](command-catalog.md). Source discovery used the live
Thorlabs software and product pages rather than the older issue-44.1 search copy.

The implemented request layouts and normal completion formats required no change.
The fault path was incomplete: it now rejects malformed 0080/0081 headers/lengths
and decodes the documented rich response fields while retaining the raw bytes.
New synthetic tests cover every rich-response split boundary and malformed fault
layouts. All prior recorded normal/stop frames and offline motion tests still pass.
The 56 shared-family index entries and 3 K10CR1-only trigger entries are retained as
an independently sourced catalogue fixture. All seven existing request definitions
and their responses have entries, and explicitly excluded AV-mode IDs cannot be
advertised as implemented commands.

No operation was added to the public API, and no hardware command was sent for this
audit. Rich-fault behavior was verified offline, not by deliberately inducing a
hardware fault. Generic heartbeat/streaming applicability to K10CR2 and extended
sessions remain unresolved; this audit does not claim full-device API coverage.

## Remaining bench acceptance

The [live redesigned-GUI review](bench/gui-live-review-20261106.md) connected the
actual mount, loaded all five settings groups and exercised read-only user flows,
session display edits, validation and keyboard/dialog behavior. It records the
issues fixed, screenshots, final raw state and 198 passing offline tests. No motion
or device-setting write was performed in that review.

- Packaged identity/status/position queries: passed as recorded above.
- Packaged home, 90-degree move, position/status query, and return to zero: passed
  as recorded above. Independent physical direction/angle observations remain open.
- Check 10 and 180 degree targets and nonzero angles with an independent reference.
- Profiled stop and real Ctrl+C during motion, stop acknowledgement length, fresh
  query, and return to zero: passed as recorded above. Independent mechanical stop
  timing and physical observations were not measured by the software.
- Check enable from a disabled channel if that path is needed.

Execute further physical motion in a separately authorized bench step. Reboot persistence,
extended communication/acknowledgement behavior, mechanical angular accuracy, and
Windows coexistence/endurance stability remain unverified by offline checks.
