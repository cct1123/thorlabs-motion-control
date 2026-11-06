# K10CR2 command reference

The core wire table below and the additional settings/action mappings in
[gui-manual.md](gui-manual.md) describe the current implementation. The complete
capability table, including deliberately unavailable commands, is in
[command-catalog.md](command-catalog.md).

Audited 6 November 2026 against protocol issue 48 and the current K10CR2 manual.
The full documented capability/coverage table is in [command-catalog.md](command-catalog.md).
Requests are binary APT bytes, with no text terminator.
Host address is `01`, device is `50`, channel is `1`. Fields with more than one byte
are little-endian. Reply payload sizes below exclude the six-byte header.

## Sources

- [Thorlabs Host-Controller Communications Protocol, DOC-103274, issue 48](https://media.thorlabs.com/globalassets/family-pages/sharedassets/a/ap/apt_communications_protocol.pdf?v=0812022816):
  authoritative message definitions and binary layouts. Page numbers below are the
  document's printed pages.
- [K10CR2(/M) User Guide ETN064249-D02, Rev A, August 2024](https://media.thorlabs.com/globalassets/items/k/k1/k10/k10cr2_m/etn064249-d02.pdf?v=0116115646):
  device capabilities and operating behavior. The generic protocol's index still
  names K10CR1; capability and raw-message applicability are distinguished in the catalogue.
- [Thorlabs archived product sheet covering K10CR2 and K10CR1/M](https://www.thorlabs.com/catalogpages/Obsolete/2024/K10CR1_M.pdf):
  the **Resolution Calculation** section explicitly names K10CR2 and specifies 200
  full steps, 2048 microsteps per step, and 120:1 gearing. Despite the archive filename,
  this supports the K10CR2 scale independently of QMI and the protocol's K10CR1 table.
- [QMI K10CRx reference at revision 7b02875043985abd6366d902c1cf32af240a6881](https://github.com/QuTech-Delft/QMI/blob/7b02875043985abd6366d902c1cf32af240a6881/qmi/instruments/thorlabs/k10crx.py),
  together with `k10cr2.py` and `apt_packets.py` at the same revision: secondary
  reference for addressing, units, and status interpretation. QMI is not imported;
  this package implements the small required protocol subset independently.

## APT commands

| Operation | ID | Exact request | Reply/event | Payload | Source pages |
| --- | --- | --- | --- | --- | --- |
| Identity | 0005 | `05 00 00 00 50 01` | 0006 | 84 bytes | 59-60 |
| Status | 0429 | `29 04 01 00 50 01` | 042A | channel uint16 + bits uint32 | 148 |
| Position | 0411 | `11 04 01 00 50 01` | 0412 | channel uint16 + counts int32 | 73-74 |
| Enable, internal helper | 0210 | `10 02 01 01 50 01` | no direct reply; query enabled bit | none | 52-53 |
| Home | 0443 | `43 04 01 00 50 01` | 0444 | none; channel is header byte 2 | 91 |
| Absolute move | 0453 | `53 04 06 00 D0 01 01 00` + target int32 | 0464 | documented 14 bytes | 94-96 |
| Profiled stop | 0465 | `65 04 01 02 50 01` | 0466 | documented 14 bytes | 99-100 |

`D0 = 50 | 80` marks a long request. In short commands, channel/enable/stop parameters
occupy individual header bytes. In long payloads the channel is a 16-bit word.
An absolute move to 90 degrees is the independent reference frame
`53 04 06 00 D0 01 01 00 00 80 BB 00` (12,288,000 counts).

The parser validates source/destination, known response size, and channel. The
[GUI bench check](bench/gui-controls-20261106-211627Z.md) found firmware 2.0.1 sends
`GET_JOGPARAMS` (`0418`) to host destination `80`; that specific reply accepts `80`
in addition to `81`, with unchanged source/size/channel checks. Other messages
retain the generic host addressing. Home
completion is a **short** frame; movement/stopping completions are long frames.
Long completion data begins with the channel word. Interleaved frames are consumed
without losing partial data. Controller responses `0080` and `0081` are exposed as
faults with the full raw response. Their layouts are validated: 0080 is short;
0081 has a 68-byte payload. Rich responses expose the originating command ID,
numeric code and notes without guessing undocumented code meanings.
Unknown bounded frames can be skipped while
waiting for the requested response; they do not prove completion.

The 14-byte absolute-move completion payload was confirmed by packaged moves to 90
degrees and back to zero on serial 55551174, firmware 2.0.1. The 14-byte stop
completion payload was also confirmed by explicit profiled stop and real Ctrl+C
cleanup during motion. Do not silently accept another length if a
device differs; retain the raw fault and resolve the model/firmware difference.
Request-driven status queries are used; automatic status streaming is not enabled.
Long-session acknowledgement behavior has not been validated on this mount.

## Units and status

The manufacturer-derived scale is `200 * 2048 * 120 = 49,152,000` counts per output
revolution, or exactly `409600 / 3` counts per degree. Avoid rounding the conversion
constant to the protocol table's displayed integer 136533. The `position` reply is
a signed step counter, not an independently measured optical angle. Motion target
resolution uses the shared [rotation contract](migration.md#shared-angle-contract).
Velocity and acceleration use the protocol's K10CR1 constants 7,329,109 and 1,502;
their physical calibration on K10CR2 remains unverified. Settings change only on
explicit Apply/configure; Drive temporarily changes and restores a verified profile.

| Field | Mask | Meaning when set |
| --- | --- | --- |
| moving | 000000F0 | moving or jogging in either direction |
| homing | 00000200 | homing in progress |
| homed | 00000400 | home reference established |
| motion_error | 00004000 | motion/position error flag |
| enabled | 80000000 | motor output enabled |

`Status.busy` includes moving/jogging **or homing**. The general error bit `40000000`
also prevents starting/continuing a motion. Raw bits are retained; no claim about
unsupported sensors or other controllers' flags is inferred from a zero bit.
Sources: issue 48 status descriptions and status-bit command definition.

## USB bridge setup (not APT)

The tested bridge uses `bcdDevice=1000`, interface 0, two 64-byte bulk endpoints,
115200 baud, 8N1, RTS/CTS. The setup matches the successful bench helpers and the
libftdi vendor-request convention. No USB device reset, `set_configuration`, driver
installation, EEPROM write, or stage-parameter write is performed by bridge setup.

| Vendor request | Value | Index | Purpose |
| --- | --- | --- | --- |
| 00 | 0000 | 0001 | reset UART only |
| 03 | 001A | 0000 | 115200 baud |
| 04 | 0008 | 0001 | 8N1 |
| 00 | 0001 | 0001 | purge RX |
| 00 | 0002 | 0001 | purge TX |
| 09 | 0002 | 0001 | 2 ms latency |
| 02 | 0000 | 0101 | RTS/CTS |
| 01 | 0202 | 0001 | assert RTS |

Each received USB packet starts with two FTDI status bytes. Strip them per packet,
not once per transfer; handle status-only packets and reject UART error flags.
Source reference: [libftdi transport implementation](https://github.com/lipro/libftdi/blob/master/src/ftdi.c).

## Validation levels

All rows have offline literal-frame checks. Saved real traffic independently covers
identity, status, packaged home/home-complete, and moves/position at 90 degrees and
zero. Home and both moves returned completion messages. The exact received move
completion frames are regression fixtures. Enable was sent during an earlier helper
home with an already-enabled motor. Public profiled stop and public move interrupted
by real console Ctrl+C passed during motion; both 0466 stop replies are regression
fixtures. Standalone enable from disabled remains untested. Firmware commands are
not implemented. Supported settings groups can request EEPROM persistence, which
has no acknowledgement and is not power-cycle verified; see the GUI contract.
