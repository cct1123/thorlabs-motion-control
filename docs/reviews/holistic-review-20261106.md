# Holistic review and migration — 6 November 2026

The controller, CLI, GUI, notebooks, packaging and current documentation now share
one WinUSB/APT implementation and one motion contract. No Kinesis/SDK backend or
legacy motion adapter remains. The [migration guide](../migration.md) lists the
removed interfaces, angle-boundary change and canonical reference paths.

## Findings and resolutions

| Finding | Resolution |
| --- | --- |
| Firmware 2.0.1 jog readback used host destination `80` | Narrow `0418` parser allowance; exact source, length and channel checks remain. Actual reply retained as a regression fixture. |
| Git normalization would invalidate the bench SHA256 manifest | Bench artifacts preserve original bytes, including CRLF. All 13 committed artifact hashes verify. |
| Truthy strings/integers could authorize high-power or EEPROM behavior | `high_power`, `persist` and manual `jog` require actual booleans before I/O. Invalid forms have no-write regressions. |
| Fractional drive index could produce HTTP 500 | Drive indices must be integers 0–3; invalid requests return HTTP 400 without starting an operation. |
| Library/CLI and GUI used different move APIs and angle ranges | One `move_absolute()` API supports fixed, total and equivalent modes. All three interfaces share Fixed Range 0–360° inclusive and fresh-counter routing. Callers and notebooks migrated together. |
| Obsolete step routes and view re-exports remained | Removed step commands, their `step` parameter, the older `move_to()` API, duplicate conversion helper and incidental GUI view exports. No compatibility aliases. |
| Names still described earlier review/extension scaffolding | GUI `move` command/control, `CONTROL_IDS`, `MOTION_COMMANDS`, `MANUAL_COMMANDS`, `test_gui_workflows.py`, and `test_manual_motion.py` describe current responsibilities. Timeout validation no longer imports a private helper across modules. |
| Completed requests and validation feedback were misleading/hidden | Worker outcomes replace queued messages; both dialogs display feedback. Close/×/Escape discard drafts, Reset replaces validation, focus stays in the modal. Scrollable settings preserve visible actions. |
| Previous EEPROM/group selection survived closing the form | Close, Reset and new readbacks clear those selections. Verified through the real web UI without clicking Apply. |
| Duplicate manual trees and a remote-manual fallback weakened packaging | One SHA256-verified PDF pair under `docs/references/`; the wheel bundles and locally serves the user guide. Missing bundled documentation produces 404, never a remote redirect. |
| Docs claimed settings writes and multi-turn targets were unsupported | README, command coverage, GUI contract and notebook text now describe current capabilities and distinguish unverified mappings. Historical records remain explicitly historical. |
| A legacy step-based GUI process was still serving port 8050 | Confirmed it was disconnected; closed it through its own UI. Started the migrated application on the standard port. |
| Live migration check exposed a renamed Move presentation class | Restored `move-tool` while retaining command ID `move`; visually verified the arrow and responsive layout on the actual running UI. |

Removed redundant UI help/footer text and unused presentation rules. Historical
bench captures, source hashes and reference PDFs are retained; an old source
snapshot or generated bench helper is not installed as a second implementation.

## Validation

- **228 offline tests passed**, including guarded notebook execution, literal APT
  frames, cancellation/Stop ownership, invalid-flag no-write checks, complete
  settings-draft validation, canonical modes/boundaries and installed-manual serving.
- Ruff lint and format checks passed; both browser asset scripts passed Node's
  syntax check. Imports/help/startup remain free of USB access.
- Offline uv lockfile check passed. Wheel and source distribution built offline.
  Archive inspection confirmed no `.tmp`, `.venv`, `__pycache__`, Git metadata or
  duplicate hardware-reference tree. The wheel contains all GUI assets and the
  exact user-guide PDF hash.
- An isolated wheel installation passed startup/status, local manual/hash and
  asset checks. Removed motion and GUI helper interfaces were absent.
- All 13 bench manifest hashes were checked against committed Git bytes, not only
  the Windows working-copy representation.

The Windows sandbox could not access pytest's private temporary fixture directory;
the same fake-hardware suite passed under the normal user token. The installed
manual test also exposed an unclosed test-response file handle; the test now closes
that response before deleting its temporary PDF.

## Final actual-mount check

The final current application was connected through its web UI at
`http://127.0.0.1:8050` to serial **55551174**, firmware **2.0.1**. Position/status
polling and all five settings readbacks succeeded. Counted position was **0**, with
status **`0x80000400`**: homed, enabled and idle. All raw settings groups exactly
match the pre-test baseline. The current GUI remains open and connected.

Settings opening/closing, discarded write selections and the canonical Move
dialog/control were checked through user interactions. No Home, Move, Jog, Drive,
enable/disable, device Apply or EEPROM command was sent during this final review.

- [Final live GUI screenshot](holistic-gui-20261106.jpg)
- [Final raw state from the same GUI owner](holistic-mount-state-20261106.json)
- [Earlier actual-motion bench record](../bench/gui-controls-20261106-211627Z.md)

The earlier record remains the evidence for actual Home/small Move/Jog/Drive/Stop
and volatile settings restoration. The API migration's additional rotation modes
and 360° boundary have offline coverage; they were not newly exercised physically.
Independent angle/rate calibration, certified Stop timing, EEPROM power-cycle
retention, unavailable mappings and long-session endurance remain unverified.

## Cleanup and checkpoints

Removed six explicitly inventoried generated targets: `.tmp`, `dist`,
`.pytest_cache`, `.ruff_cache`, `src/k10cr2/__pycache__`, and `tests/__pycache__`.
They contained **795 files / 154,940,569 bytes** (about 148 MiB): old build/install
copies, review snapshots, extracted manual pages, one-off preview/capture scripts,
temporary patches/screenshots and caches. No target contained tracked files or
reparse links. Resolved paths were checked to stay inside this repository before
deletion. `.venv`, all committed bench evidence, manuals and the editor were
preserved. The current GUI runs with bytecode writes disabled; only `.venv` remains
as an ignored project artifact.

| Commit | Stage |
| --- | --- |
| `504cf88` | Firmware correction and byte-preserved bench evidence |
| `9d149c0` | Flat GUI and live readback/interaction fixes |
| `17df72c` | Explicit confirmation flags and drive-index validation |
| `f8769f6` | Canonical motion API, caller/notebook migration and removed legacy paths |
| `6465991` | Reference deduplication, naming, packaging and current docs |
| `e75b6cb` | Live-discovered Move presentation-class correction |

This report, final live evidence and documentation index updates form the last
documentation checkpoint. Commits are staged by explicit paths; no blanket staging
or history rewrite is used.
