# Repository review: 8 November 2026

The controller and single-script GUI migration were reviewed together, including
protocol framing, USB ownership, cancellation, settings application, packaged
startup, bench-tool imports, notebooks and operator documentation. This review
used offline/scripted checks only. No USB enumeration, connection, motion,
device-setting write or EEPROM command was performed.

## Findings and fixes

| Finding | Resulting behavior | Regression evidence |
| --- | --- | --- |
| Explicit Enable ignored a controller fault before writing, and accepted a faulted enable readback | Motion/general faults block Enable; a fault after writing requires recovery. Idle Disable remains permitted on a fault | Three rejection cases and an explicit-disable case in `tests/test_settings.py` |
| Home/Move polling did not reject lost motor enable promptly; absolute Move could continue waiting after losing its reference | Same-owner Stop cleanup starts immediately; the original failure remains attached and reconnect is required | Home/Move enable loss and absolute-Move reference loss in `tests/test_motion.py` |
| Stop accepted before the motion write left a cancellation error after successful confirmation | Clean preflight cancellation is informational; failed confirmation still retains UNKNOWN and recovery | Confirmed/failed variants in `tests/test_gui_owner.py` |

Each defect was reproduced with a failing scripted regression before its fix.
Fresh status, one receive owner, bounded motion, independent cleanup deadlines,
fault latching and write/readback verification remain enforced.

## Migration and pruning

- `k10cr2.gui` owns the GUI controller, components, callbacks, local routes and
  server lifecycle. `gui_state.py` and `gui_view.py` are deleted; the interim
  `gui_layout.py` was folded into the same script. No forwarding modules remain.
- `gui_forms.py` holds pure conversion/validation helpers. Metadata consistently
  uses `SESSION_FIELDS` and `SESSION_SCALAR_FIELDS`; owner regressions are named
  `test_gui_owner.py`. Tests and the bench recorder use canonical imports.
- `protocol.py` owns settings wire definitions and packet helpers. Reply sizes
  derive from those layouts; independent literal-frame tests retain a separate
  check on the bytes. `settings.py` owns units, validation and target resolution.
- The GUI defaults to Equivalent Angle. Reverse Jog/Drive can cross zero and
  forward motion can cross full turns. Fixed Range remains explicitly bounded;
  CLI/library defaults remain fixed. The 43-turn guard is a software counting
  protection, not a mechanical travel stop.
- One Apply changes action validates the complete draft, applies edited device
  groups with readback verification, then publishes session options. Candidate
  USB confirmation participates in validation. Failed application preserves the
  old session and reports possible partial device changes. EEPROM remains opt-in.
- Healthy confirmed cancellation permits another command after verified temporary
  profile restoration. Communication/controller faults and failed restoration
  still require recovery; a later Stop cannot erase a prior connection fault.

Legacy identifiers remain only in migration documentation, dated evidence and
negative tests that confirm rejection. Historical hardware captures and source
hashes were preserved rather than rewritten as evidence for this checkpoint.

## Validation

- Full suite: **354 Python tests passed**. The only warning is the installed Dash
  testing plugin's pytest deprecation warning; dependency files were not modified.
- Browser hold-event suite: **2 JavaScript tests passed**.
- Repository-wide Ruff lint/format and Git whitespace checks passed.
- Core, protocol, CLI and notebook checkpoint: **268 tests passed** before stage 1.
- Offline source/wheel builds passed. The wheel contains byte-identical copies of
  all nine current runtime modules, GUI assets and the manufacturer manual. Both
  archives exclude the removed GUI modules.
- An extracted-wheel smoke check outside the checkout passed core/CLI startup
  with Dash blocked, GUI startup/routes/assets/manual and a session-only Apply
  callback. USB and manufacturer-runtime imports were blocked throughout.
- Local links passed across ten active Markdown documents.
- Five generated roots were removed: `.ruff_cache`, the source/test/bench-tool
  `__pycache__` directories, and the owned `.tmp` package-smoke directory. No
  generated cache/temp roots remain outside the preserved `.venv`.
- All 78 protected files (65 bench files, three reference files and ten notebook
  files including their README) remain byte-identical. The nine notebooks, original
  manuals, captures, `.venv`, and fresh `dist/` deliverables were preserved.

The two source commits are `6b7d6df` (controller/protocol/recovery) and `350dfe6`
(single-script GUI, settings, naming and Stop feedback). Documentation and cleanup
are recorded as the final stage; the Git history identifies that commit.

## Remaining physical limits

The checks establish software behavior, not bench acceptance of the new default,
unified Apply, new fault guards or same-connection Stop recovery. Physical angle,
velocity and acceleration calibration, full/multi-turn travel, persistence after
power cycling, long sessions, OS focus switching and hardware fault injection
remain outside this review. Earlier bench records retain their own dated scope.
