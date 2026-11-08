# GUI contract: K10CR2 manufacturer manual

Source of truth: [ETN064249-D02](references/k10cr2-user-guide-etn064249-d02.pdf),
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
| Enable / Disable | Exactly one is visible for known enabled state; output state is checked after the short command. Enable is blocked on motion/general fault flags, including a fault in its readback. Idle Disable remains available through the library even on a fault; Disable is refused during motion | 16 |
| Identify | Sends the LED identification command; reports that the request was sent, since no acknowledgement or optical feedback exists | 16 |
| Stop | Signals the active USB owner; fresh status/counter confirm cleanup. A clean cancellation permits another command after verified temporary-profile restoration. Remains accessible in dialogs. Failed stop retains UNKNOWN state and blocks other actions | 16 |
| Drive bar graph | Four session-configured speeds per direction; hold to move, release to stop. A temporary move profile is readback-verified and restored after normal stop | 16,23 |
| Jog arrows | Native single-step jog command, one step per click; holding repeats completed steps. Continuous jog uses the jog profile and stops on release; immediate/profiled stop choice is honored | 14,16,18 |
| Status strip | Idle, Moving, Homing, or Unverified, together with stage model and serial | 16 |
| Settings | Moves/Jogs, Rotation and Advanced. Advanced includes hardware buttons, Drive speeds and read-only homing settings. One Apply changes action applies edits; Reset/Close/×/Escape discard unapplied edits. Scrollable form, fixed feedback/actions and keyboard focus containment | 17-24 |

Held controls use pointer capture and support Space/Enter. Release, pointer cancel,
loss of the active pointer's capture,
focus leaving the held button, hidden tab, window blur and page unload end the
browser lease. The active control stays focusable during motion; the owner rejects
additional starts while busy. Heartbeats run every 200 ms;
the owner regards a lease as expired after 800 ms. Expired leases cannot be renewed.
Release tokens are retained to reject a release-before-start race. This is software
ownership protection, not a real-time safety interlock: an in-flight USB transfer or
query can delay observation, and stop cleanup has its own deadline. Single-step jog
finishes the already-requested finite step when released; it does not start another.
Use Stop to interrupt that step.

Home/Move polling rejects loss of motor enable immediately; absolute Move also
rejects loss of the home reference. Both use same-owner Stop cleanup and require
explicit recovery. An operator Stop accepted before a motion command is written
still receives same-owner confirmation; successful cleanup is informational and
does not leave a cancellation error banner. Failed confirmation retains UNKNOWN.

Absolute Move completes only after fresh idle/homed/enabled readback and evidence
of progress, with at most one count of target residual (about 0.0000073°). The
actual counter is displayed without snapping it to the target. Firmware 2.0.1 was
observed stopping at −1 count after a maximum-profile move to zero; requiring exact
equality left the GUI waiting despite idle status. Home still requires exact zero.
This software completion tolerance does not measure mechanical angular accuracy.

The normal GUI and bench recorder share server shutdown handling. Failed shutdown,
timeout or an interrupted wait keeps the current owner and controls available for
Stop/retry. Shutdown query interruptions retain an unknown-state indication until
an explicit Stop/retry confirms the state. See the [repository review](reviews/repository-review-20261106.md)
for offline failure-path tests and the current read-only live check.

The [actual-mount held-jog record](bench/gui-jog-20261107-002109Z.md) verifies
continuous and repeated finite jog release, focus moving to another control,
navigation away, and Stop with small counted moves. It includes raw traffic and
exact settings restoration. Native OS window switching was not reproduced; it is
not included in that acceptance claim.

Drive and continuous jog use an absolute finite boundary target instead of an
unbounded velocity packet. This enforces the selected counted interval without
depending on timely browser release for a boundary stop. Fixed Range is 0–360°;
Total Angle and Equivalent Angle retain counts within ±15,480°, the manual's 43-turn
display bound. Equivalent Angle wraps only the display and selects forward,
reverse or quickest routing from a fresh counter. Ties at 180° select forward.
The CLI, GUI and library all use `move_absolute(angle, mode=..., direction=...)`.
Fixed Range includes both 0° and 360°; Total and Equivalent use the same target
resolver and fresh-counter routing. An already-homed counter outside Fixed Range
may return to an absolute target inside it. Manual controls reject outward travel;
finite jogs may approach or enter the interval without crossing its opposite boundary,
and held Drive/continuous Jog stops at the nearest boundary. The current counter
must still be within ±15,480° in every mode. The earlier `move_to()` API and unused
`step-plus`/`step-minus` GUI commands have been removed; no aliases are retained.

The GUI starts in Equivalent Angle, permitting Jog/Drive across zero and full
revolutions in either direction. Negative counted positions wrap into 0–360° for
display. Total Angle also permits crossing zero but shows the signed accumulated
angle. Fixed Range remains an explicit bounded mode; CLI/library defaults stay fixed.

## Settings and wire support

The controller performs device numeric validation and fresh homed/enabled/busy
checks; the GUI does not repeat them against cached values. Invalid target/Drive
values are rejected before device I/O; setting drafts may require fresh readback
before validation but cannot write until every requested group passes. Errors are
reported by the owner worker, and failed device validation leaves session options
unchanged. GUI-only checks cover display formatting, request identity, owner/lease
lifecycle and recovery/UNKNOWN state.

The [migrated-GUI actual-mount test](bench/gui-migration-20261106-231335Z.md) also
verified small Fixed/Total/Equivalent routes and repeated quantized phase targets.
Equivalent routing now compares integer counter phases; repeating the reached
phase never adds a turn merely because its degree display is rounded. Loaded,
unedited Move profiles are preserved; explicit profile edits/Apply retain normal
write validation. The test found and retested both fixes and restored all raw
settings exactly.

The [actual-mount GUI check on 6 November 2026](bench/gui-controls-20261106-211627Z.md)
passed on serial 55551174, firmware 2.0.1: Home, small Move, single-step Jog in both
directions, held Drive/release in both directions, explicit Stop, and readback of
all five groups. Velocity/jog/backlash writes and exact restoration also passed.
Hardware-button writes, EEPROM persistence, other modes/fault scenarios, physical
profile calibration and the unavailable mappings are not established by that test.

`Connect` reads velocity, jog, backlash, home and button groups before publishing a
successful connection. `Refresh readbacks` reloads all five groups; the main
`Refresh status` button reads status and position only. Settings show a read time;
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
| Persist changed settings | 04B9 with each changed group's setter ID | Protocol p113; no direct acknowledgement or power-cycle verification |

The shared protocol applicability index still names K10CR1, not K10CR2. These
extensions use its documented layouts for capabilities confirmed by the K10CR2
manual; acceptance beyond the recorded unit and tested subset still needs bench
validation. Velocity
and acceleration use the protocol p44 K10CR1 scale factors 7,329,109 and 1,502.
Those factors are separately identified from the established K10CR2 position scale
of 409600/3 counts/degree. Offline fixtures verify encoding and conversion, not
physical velocity or acceleration. Speeds above 10°/s require explicit confirmation
of a ≥900 mA USB supply; the manual limits speed to 20°/s and acceleration to 20°/s².

Apply changes automatically includes only edited groups; no group selection
is required. Unedited readbacks are left alone, and applying without edits sends no
configuration request. Validation and fresh device readback still precede all writes.
The same action validates session display/rotation/drive options, including USB
supply confirmation, before requesting device changes. Session options are published
only after successful device application; a partial device failure retains the old
session and requires recovery/readback before retrying. Session-only edits work
without a device connection and do not issue USB commands.
EEPROM persistence is opt-in and limited to those changed mapped groups.
Closing/resetting the form or loading a new readback clears the persistence checkbox
so an earlier EEPROM selection cannot carry into the next edit.
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

## Unsupported features

Per the user's explicit decision to retain WinUSB, these unsupported controls are
not displayed and cannot emit a command:

- **S-curve / Bow Index:** documented GUI behavior on pp18-19, but 04F4/04F5/04F6
  is absent from the K10CR1 applicability index; K10CR2 mapping is not established.
- **Resting power:** manual p22 exposes rest power and fixes moving power internally.
  The protocol's 0426 family has different applicability; it is not assumed here.
- **Input/output triggers and their target fields:** manual pp23-24 defines the
  choices. 0500's device-dependent mode layouts do not establish K10CR2 encoding.
- **Factory reset:** manual p24 defines the effect at next power-up. Protocol p69
  explicitly limits 0686 and 04F3 to other controller families; neither is sent.
- **Stage/Axis fields and limit policy:** the manual p21 instructs not to alter
  stage-specific defaults. Only homing readbacks and the reported limit indicators
  remain visible; the placeholder Stage/Axis and Defaults tabs are removed.

Unavailable readbacks are blank, not presented as factory or current values. No
firmware, unverified command, driver replacement, Kinesis backend, or SDK fallback
is included. The original PDFs and source hashes are in [references/README.md](references/README.md).
