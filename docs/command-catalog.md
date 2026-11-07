# K10CR2 commands and queries: documented capabilities and package coverage

The table includes the current controller and GUI mappings. Detailed settings
behavior and validation limits are documented in [gui-manual.md](gui-manual.md).

Reviewed 6 November 2026 against **DOC-103274 issue 48** and the **K10CR2(/M) User
Guide ETN064249-D02, Rev A, August 2024**. This is a capability/coverage catalogue,
not a claim that every shared-family command is supported by K10CR2 firmware 2.0.1.
The existing small package scope is retained.

## Reading the table

`Set/action` changes state or commands an action. `Request -> reply` is a query and
its response. `Event` is an asynchronous controller notification, not a query to
send. IDs are hexadecimal. Multi-byte fields are little-endian.

The protocol's applicability list on pages 12-13 still names **TST001, TST101,
KST101, and K10CR1**, not K10CR2. It contains 56 message IDs. The table below covers
all 56, including the AV-mode entries that the detailed definition explicitly
excludes. The K10CR2 manual confirms many corresponding capabilities, but does not
list their raw APT IDs. Consequently, unimplemented rows are candidates for a future
extension and must not be silently advertised as verified K10CR2 raw commands.

## Full shared-family command/query catalogue

| Function | Set/action | Request -> reply / event | K10CR2 evidence and current package | Issue 48 pages |
| --- | --- | --- | --- | --- |
| Identify / flash LED | 0223 | No reply | Implemented; command has no acknowledgement or optical readback | 51 |
| Motor enable/disable | 0210 | 0211 -> 0212 | Set enable/disable implemented; verify with status bit. Direct 0211 query absent; enable-from-disabled remains untested | 52-53 |
| Automatic status updates | Start 0011; stop 0012 | Unsolicited update messages | Shared index; not enabled or exposed by package | 57 |
| Hardware identity | - | 0005 -> 0006 | Implemented; firmware, model, serial and revision checked against captures | 59-60 |
| Position counter | 0410 | 0411 -> 0412 | Query implemented and bench-tested; setter absent | 73-74 |
| Encoder counter | 0409 | 040A -> 040B | Definition is for encoded stages. No independent encoder support established for this mount; not implemented | 75-76 |
| Move velocity/acceleration | 0413 | 0414 -> 0415 | Read/write and readback verification implemented; volatile writes/restoration bench-tested on the recorded unit | 77-78 |
| Jog parameters | 0416 | 0417 -> 0418 | Read/write implemented; continuous and single-step profile writes/readbacks bench-tested. Firmware host-zero reply exception retained | 79-80 |
| General move / backlash | 043A | 043B -> 043C | Read/write and readback verification implemented; volatile write/restoration bench-tested | 84 |
| Stored relative target | 0445 | 0446 -> 0447 | Shared index; not implemented | 85 |
| Stored absolute target | 0450 | 0451 -> 0452 | Not implemented; long-form 0453 carries the target directly | 86 |
| Homing parameters | 0440 | 0441 -> 0442 | Readback implemented and bench-tested; setter deliberately locked per manual p21 | 87-88 |
| Limit-switch parameters | 0423 | 0424 -> 0425 | Shared family; K10CR2 applicability of configurable limits not established; not implemented | 89-90 |
| Home | 0443 | Event 0444 | Implemented; short completion frame bench-tested | 91 |
| Relative move | 0448 | Event 0464 | Manual supports relative movement; not implemented | 92-94 |
| Absolute move | 0453 | Event 0464 | Long form implemented; 90-degree and zero moves/completions bench-tested | 94-96 |
| Jog | 046A | Event 0464 | Native single-step and held-repeat jog bench-tested. Host-bounded continuous jog uses 0453; release, control focus loss and Stop passed small-move checks in both modes | 97 |
| Continuous velocity move | 0457 | Stop with 0465 | Manual describes continuous motion; raw command not implemented | 98 |
| Stop | 0465 | Event 0466 | Profiled public Stop and Ctrl+C cleanup bench-tested. Jog-selected immediate stop is implemented but not bench-tested | 99-100 |
| LED/AV modes | 04B3 | 04B4 -> 04B5 | **Explicitly not applicable to K10CR1 or K10CR2**, despite shared index listing | 106-107 |
| Potentiometer / drive velocities | 04B0 | 04B1 -> 04B2 | Manual p23 describes drive-array velocities; exact K10CR2 raw mapping not verified; absent | 108-110 |
| Front-panel buttons | 04B6 | 04B7 -> 04B8 | Read/write implemented; readback bench-tested. Button writes and physical button behavior not bench-tested | 111-112 |
| Persist parameters to EEPROM | 04B9 | No direct reply | Explicit opt-in for selected mapped groups after volatile verification. No acknowledgement; power-cycle persistence unverified | 113 |
| Status bits | - | 0429 -> 042A | Implemented; raw bits, moving, homing, homed, enabled and motion-error fields bench-tested | 148 |

## Related definitions, restrictions and unresolved mappings

| Function | IDs | Applicability / handling |
| --- | --- | --- |
| Hardware triggering | Set 0500; query 0501 -> 0502 | Protocol p14 labels these K10CR1-only. K10CR2 manual pp23-24 confirms trigger capabilities, but raw ID/bit mapping is not established here. Not implemented |
| Simple fault event | 0080 | Short receive-only event, handled defensively; retain raw header without inventing an error-code layout (pp54-55) |
| Rich fault event | 0081 | Receive-only 68-byte payload: triggering message uint16, code uint16, notes char[64]. Strictly validated and decoded (p56) |
| Full status update | 0480 -> 0481 | Generic controller definition exists (pp134-136); not in the K10CR1 shared index. Package does not request or enable streaming |
| Unified status update | 0490 -> 0491 | Generic definition (pp137-147); do not substitute its device-specific fields for current 042A decoding |
| USB status acknowledgement | 0492 | Generic USB/server-alive definition warns about 50 unacknowledged updates (p147); not listed for K10CR1. K10CR2 applicability/long-session behavior unresolved; no speculative heartbeat added |
| Suspend/resume completion events | 046B / 046C | Generic definitions (pp149-150), absent from shared K10CR1 list; not implemented |
| Velocity-profile bow index | 04F4; query 04F5 -> 04F6 | Generic definition (pp101-103). Manual pp18-19 describes profiles, but K10CR2 raw mapping not verified; absent |
| Motor power parameters | 0426; query 0427 -> 0428 | Index restricts these to TST/TST101/KST101 families. K10CR2 manual p22 exposes resting power through Kinesis; that does not establish the same raw command mapping |
| K-Cube settings / servo loops | 0520 and related families | Other device groups have explicit applicability. Not imported into this K10CR2 package |

No firmware-programming command or automatic actuation is added by this catalogue.
The manual's hardware/GUI capabilities and the low-level protocol's applicability
tables are different kinds of evidence. The core requests and supported settings
are mapped separately; this is not a claim to implement every message in the 494-page protocol.

The [held-jog bench record](bench/gui-jog-20261107-002109Z.md) is the evidence for
the continuous/repeated coverage above. Ordinary OS window switching, lost pointer
capture, immediate jog stop, USB-loss behavior and independent mechanical stop
timing are not established by that run. Lost-capture release has offline event
coverage in the [subsequent review](reviews/repository-review-20261106.md).

## Audit result for the implemented subset

The requests and replies in [commands.md](commands.md) match issue 48: addresses,
short/long indicator, sizes, channel widths, signed target/position counts, firmware
byte order, and home/move/stop completion framing agree with the reference and the
captured unit. No change to motion packets or units was required.

The fault path needed hardening: 0080 must be short; 0081 must carry exactly 68
bytes. Those layouts now receive the same header/length validation as normal
responses. `ControllerFault` preserves `raw` and additionally exposes `message_id`,
`message_ident`, `code`, and `notes` where documented. Codes are kept numeric; their
device-specific meanings are not guessed. Synthetic malformed/fault tests are
distinct from the real normal-motion/stop captures.

The WinUSB bridge setup has the documented logical 115200/8N1/RTS-CTS settings.
Its UART-reset/latency/purge sequence is the tested bridge adaptation, not a literal
copy of the protocol p33 D2XX initialization example (which has separate pre/post
purge dwells). This audit does not alter the validated bridge sequence. Packet
status stripping and transport writes remain separate from the APT catalogue.

## Source record

- [Current protocol download page](https://www.thorlabs.com/software-pages/Motion_Control?tabName=Communications%20Protocol).
- [Protocol issue 48 PDF](https://media.thorlabs.com/globalassets/family-pages/sharedassets/a/ap/apt_communications_protocol.pdf?v=0812022816),
  SHA256 `911bdd8cc6a445f959a5fae3f44456ebf345c65d5103f3f7d5c5fd547d6e523f`.
- [K10CR2 user guide](https://media.thorlabs.com/globalassets/items/k/k1/k10/k10cr2_m/etn064249-d02.pdf?v=0116115646),
  SHA256 `e0c3a77232005cfca12381493ed8ea6907e0cfdc7fa406b8c7c02a6bd04dbeab`.
- `tests/fixtures/protocol-issue48-catalog.json` records all 56 shared-index entries
  and all 3 K10CR1-only trigger entries, their page numbers, explicit exclusions,
  source URLs and hashes. The implementation-reference QMI revision is recorded in
  [commands.md](commands.md); manufacturer documentation remains authoritative.

Read and visually checked complete protocol index pages 12-14, fault page 56 and
the AV-mode exclusion on page 106. Reviewed full implemented-message definition
pages and the manual's software-reference chapter. No hardware commands were sent
as part of this audit. Remaining firmware applicability and endurance limits are
listed above rather than described as verified support.
