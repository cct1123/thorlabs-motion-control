# GUI contract: K10CR2 manufacturer manual

Source of truth: [ETN064249-D02](hardware/K10CR2-user-guide-ETN064249-D02.pdf),
Rev A, August 2024, Figure 16 and printed pp14-24 (PDF pp17-27).
The GUI uses our own WinUSB/APT implementation exclusively. No Kinesis application,
SDK, pythonnet, or manufacturer runtime is used or invoked. Connecting never loads
an SDK startup profile or automatically homes, enables, configures, or moves a mount.

## Panel and dialogs

| Manual control | Behavior in this application | Printed pages |
| --- | --- | --- |
| Position / Move | Six decimal places initially; click readout or Move to open target/profile dialog. Degrees, radians, mrad, μrad or device counts are display options; motion entries are explicitly degrees | 14,16-17 |
| Travel / Vel / Acc / Jog Step | Continuous mechanical travel; parameters are read on explicit Connect, then refreshed on request | 16 |
| Home / Homed indicator | Explicit home; indicator flashes while homing and shows fresh homed state afterwards | 13,16 |
| Forward / Reverse Limit | Fresh raw status masks 0x1 / 0x2; unknown when disconnected/stale. A rotary home switch is not a mechanical travel stop | 16,27 |
| Enable / Disable | Exactly one is visible for known enabled state; output state is checked after the short command. Disable is refused during motion | 16 |
| Identify | Sends the LED identification command; reports that the request was sent, since no acknowledgement or optical feedback exists | 16 |
| Stop | Signals the active USB owner; fresh status/counter confirm cleanup. Remains accessible in dialogs. Failed stop retains UNKNOWN state and blocks other actions | 16 |
| Drive bar graph | Four session-configured speeds per direction; hold to move, release to stop. A temporary move profile is readback-verified and restored after normal stop | 16,23 |
| Jog arrows | Native single-step jog command, one step per click; holding repeats completed steps. Continuous jog uses the jog profile and stops on release; immediate/profiled stop choice is honored | 14,16,18 |
| Status strip | Idle, Moving, Homing, or Unverified, together with stage model and serial | 16 |
| Settings | Moves/Jogs, Rotation, Stage/Axis, Advanced, Defaults. Reset/Close/×/Escape discard unapplied edits; device groups and session options have separate Apply actions. Scrollable form, fixed feedback/actions and keyboard focus containment | 17-24 |

Held controls use pointer capture and support Space/Enter. Release, pointer cancel,
hidden tab, blur and page unload end the browser lease. Heartbeats run every 200 ms;
the owner regards a lease as expired after 800 ms. Expired leases cannot be renewed.
Release tokens are retained to reject a release-before-start race. This is software
ownership protection, not a real-time safety interlock: an in-flight USB transfer or
query can delay observation, and stop cleanup has its own deadline. Single-step jog
finishes the already-requested finite step when released; it does not start another.
Use Stop to interrupt that step.

Drive and continuous jog use an absolute finite boundary target instead of an
unbounded velocity packet. This enforces the selected counted interval without
depending on timely browser release for a boundary stop. Fixed Range is 0–360°;
Total Angle and Equivalent Angle retain counts within ±15,480°, the manual's 43-turn
display bound. Equivalent Angle wraps only the display and selects forward,
reverse or quickest routing from a fresh counter. Ties at 180° select forward.
The CLI, GUI and library all use `move_absolute(angle, mode=..., direction=...)`.
Fixed Range includes both 0° and 360°; Total and Equivalent use the same target
resolver and fresh-counter routing. The earlier `move_to()` API and unused
`step-plus`/`step-minus` GUI commands have been removed; no aliases are retained.

## Settings and wire support

The [actual-mount GUI check on 6 November 2026](bench/gui-controls-20261106-211627Z.md)
passed on serial 55551174, firmware 2.0.1: Home, small Move, single-step Jog in both
directions, held Drive/release in both directions, explicit Stop, and readback of
all five groups. Velocity/jog/backlash writes and exact restoration also passed.
Hardware-button writes, EEPROM persistence, other modes/fault scenarios, physical
profile calibration and the unavailable mappings are not established by that test.

`Connect` reads velocity, jog, backlash, home and button groups before publishing a
successful connection. `Refresh readbacks` (or the main Refresh button) reads them
again, together with fresh status and counted position. Settings show a read time;
position and status continue polling independently. A settings read failure during
Connect closes the connection and reports the error for explicit retry.
An edit requires a previous readback and a new read immediately before writing.
Every requested group is validated before the first setting write; only changed
groups are written, then compared with readback. A partial application is possible
if a later USB operation fails; there is no speculative rollback or replay. Query,
write and readback failures require explicit recovery. Fresh device data is used to
preserve unedited fields, including hardware-button hold timeouts.

| Group / action | APT messages | Reference / status |
| --- | --- | --- |
| Enable / disable | 0210; enabled status from 0429/042A | Protocol pp52-53; additional disable path is offline-tested |
| Identify | 0223 | Protocol p51; no acknowledgement |
| Velocity | 0413 / 0414 / 0415 | Protocol pp77-78 |
| Jog settings / step | 0416 / 0417 / 0418; 046A | Protocol pp79-80,97 |
| Backlash | 043A / 043B / 043C | Protocol p84 |
| Homing settings | Read 0441 / 0442 only | Protocol pp87-88; stage-specific editing locked per manual p21 |
| Hardware buttons | 04B6 / 04B7 / 04B8 | Protocol pp111-112 |
| Persist selected groups | 04B9 with each group's setter ID | Protocol p113; no direct acknowledgement or power-cycle verification |

The shared protocol applicability index still names K10CR1, not K10CR2. These
extensions use its documented layouts for capabilities confirmed by the K10CR2
manual; acceptance beyond the recorded unit and tested subset still needs bench
validation. Velocity
and acceleration use the protocol p44 K10CR1 scale factors 7,329,109 and 1,502.
Those factors are separately identified from the established K10CR2 position scale
of 409600/3 counts/degree. Offline fixtures verify encoding and conversion, not
physical velocity or acceleration. Speeds above 10°/s require explicit confirmation
of a ≥900 mA USB supply; the manual limits speed to 20°/s and acceleration to 20°/s².

EEPROM persistence is opt-in and limited to explicitly selected mapped groups.
After verified volatile readback, the GUI reports **EEPROM save requested**;
the packet has no acknowledgement and persistence is not claimed as proven.
Display units, rotation rules, USB rating and drive-array speeds are session values,
not device EEPROM or automatic startup settings. A failed temporary drive-profile
restore requires recovery. If motion cleanup itself fails, profile restoration is
skipped; inspect/reload the device parameters after explicit recovery.

Observed firmware exception: this unit's `GET_JOGPARAMS` reply (`0418`) uses host
destination byte `80`, whereas the generic protocol specifies `81`. Only that
message gains zero-host acceptance; device source, exact length and channel are
still validated. The bench record retains the original rejection and corrected
readback, and the saved actual reply is an offline regression fixture.

## Visible unavailable and locked controls

Per the user's explicit decision to retain WinUSB, these elements are displayed
with reasons and cannot emit a command:

- **S-curve / Bow Index:** documented GUI behavior on pp18-19, but 04F4/04F5/04F6
  is absent from the K10CR1 applicability index; K10CR2 mapping is not established.
- **Resting power:** manual p22 exposes rest power and fixes moving power internally.
  The protocol's 0426 family has different applicability; it is not assumed here.
- **Input/output triggers and their target fields:** manual pp23-24 defines the
  choices. 0500's device-dependent mode layouts do not establish K10CR2 encoding.
- **Factory reset:** manual p24 defines the effect at next power-up. Protocol p69
  explicitly limits 0686 and 04F3 to other controller families; neither is sent.
- **Stage/Axis fields and limit policy:** the manual p21 instructs not to alter
  stage-specific defaults. Readbacks and fixed explanatory fields remain visible.

Unavailable readbacks are blank, not presented as factory or current values. No
firmware, unverified command, driver replacement, Kinesis backend, or SDK fallback
is included. The original PDFs and source hashes are in [hardware/README.md](hardware/README.md).
