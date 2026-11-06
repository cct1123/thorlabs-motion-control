# Protocol and bench-record review: 6 November 2026

This is a historical checkpoint. Reference PDFs were subsequently deduplicated
and renamed; [the migration map](../migration.md#reference-paths) locates their
current copies without changing the recorded hashes or historical JSON.

The committed controller/protocol checkpoint **`eda8ede0a4bf42f2e70ca49e217bebfbdeef5cf8`**
passes 142 offline tests, Ruff lint/format checks, and the offline uv lockfile check.
This review independently checked the saved bench traffic and protocol catalogue.
It performed no USB discovery, device connection, motor command, driver change, or
firmware operation. Concurrent settings/jog/GUI work is outside this checkpoint.

## Commit checkpoints

| Commit | Scope |
| --- | --- |
| `2a2cefe` | Initial package, command/fault audit, GUI, notebooks, manufacturer references, raw bench captures and validation records |
| `61c5e0d` | Completion cancellation, observer-exit cleanup, identity failure recovery and interrupted USB enumeration cleanup |
| `eda8ede` | GUI Stop acceptance, unconfirmed-stop disconnect protection and fresh shutdown status |
| `53ae8ad` | Lifecycle review findings, packaging checks and validation limits |

The lifecycle findings and their regression evidence are recorded in
[the repository review](../review-2026-11-06.md). This supplemental pass found no
additional defect in the original seven-command table or saved bench records.

## Protocol audit checks

- All **56 shared-family IDs and 3 related trigger IDs** in the independent
  catalogue fixture appear in [the capability table](../command-catalog.md).
- Both saved copies of issue 48 match SHA256
  `911bdd8cc6a445f959a5fae3f44456ebf345c65d5103f3f7d5c5fd547d6e523f`.
- Both saved K10CR2 manual copies match SHA256
  `e0c3a77232005cfca12381493ed8ea6907e0cfdc7fa406b8c7c02a6bd04dbeab`.
- The literal request, signed-count, header/address/channel, fragmented receive,
  normal completion and malformed/rich-fault regressions pass. Rich faults remain
  synthetic evidence; no fault was deliberately induced on the mount.
- The K10CR1/shared-family applicability limits, explicit AV-mode exclusions,
  unverified trigger mapping and unresolved long-session acknowledgement behavior
  remain stated in the catalogue. This does not certify every APT message for K10CR2.

## Saved bench-record checks

All five packaged JSON/log pairs were parsed offline. Receive chunks were joined
and decoded independently using the six-byte APT header and little-endian fields.
Every stream ends at a complete frame. Addresses, known payload sizes, channels,
identity (serial 55551174, model K10CR2, firmware 2.0.1), and final controller
position/status agree with the saved JSON summaries.

| Historical record | Traffic and recorded result |
| --- | --- |
| Query, 14:30 | Identity/status/position requests only; zero counts, status `0x80000400` |
| Home/90/zero, 14:33 | One home, two absolute moves; completion counts 12,288,000 then zero |
| Watched repeat, 14:35 | Same home/90/zero sequence and completion counts |
| Profiled stop, 14:38 | Exactly one stop request/reply; 2,065,104 stopped counts match the fixture; return to zero |
| Real Ctrl+C, 14:38-14:39 | Exactly one stop request/reply; 9,373,739 stopped counts match the fixture; confirmed cleanup, recovery flag, explicit reconnect and return to zero |

The four motion/stop JSON records contain identical six-file source-hash sets.
Those hashes describe the earlier bench source, not this reviewed checkpoint.
The controller, protocol, transport and package exports have changed since the
bench captures; the CLI and module launcher hashes also differ, but match after
normalizing the final newline. Original records and raw logs were left intact.
Replaying these captures cannot establish bench acceptance of later fixes.

The [machine-readable result](protocol-evidence-20261106.json) contains message
counts, completion positions, source-hash comparisons and SHA256 values for 21
audit/bench/fixture files. Hashes refer to exact `git archive` bytes before Windows
checkout line-ending conversion. Reference PDF hashes are recorded separately.

## Offline validation and limits

Validation used an isolated `git archive eda8ede` snapshot, Python 3.12.7 and the
existing project environment, with `PYTHONPATH` pointing to that snapshot's `src`:

- `python -m pytest -q -p no:cacheprovider`: **142 passed**, one Dash plugin
  deprecation warning.
- `ruff check --no-cache .`: passed, Ruff 0.17.4.
- `ruff format --check --no-cache .`: all 32 files formatted.
- `uv lock --check --offline`: passed, 32 packages resolved.

These are software and historical-record checks. Independent physical angle,
direction and stopping-time measurements, enable-from-disabled, extended-session
behavior and acceptance of the new lifecycle/settings/jog paths still require
separate bench validation. See [validation.md](../validation.md).
