# Repository review after jog acceptance — 6 November 2026

**Fixed shutdown, held-input, refresh and migration flaws; consolidated server
ownership handling; checked the current GUI against the actual mount without
motion.** Communication remains our WinUSB/APT implementation only.

## Findings and fixes

| Finding | Resolution and evidence |
| --- | --- |
| Bench recorder closed HTTP and recording after a failed owner shutdown, leaving Stop inaccessible | Normal and bench entry points now use one `serve_gui()` loop. A no-hardware regression reproduced the old close-after-false behavior and verifies HTTP/recording stay open until owner shutdown succeeds. |
| Interrupting a shutdown wait/query could strand the closing flag or bypass recovery | Interrupted waits retain the active worker; interrupted shutdown queries retain UNKNOWN state. Both return control for Stop/retry without releasing an active owner. Dedicated tests verify the owner is retained and retry stops before close. |
| **Refresh status** actually reloaded all settings and could replace drafts | Canonical control `refresh-status` submits status/position refresh only. Settings **Refresh readbacks** remains the explicit all-group reload. The old `refresh-state` ID is removed, with no alias. Callback regression and actual-mount readback metadata verify the distinction. |
| Losing pointer capture had no release path | Release only the active pointer's hold on `lostpointercapture`; ignore another pointer/control and delayed events during a later keyboard hold. The actual JavaScript asset is tested in a simulated event environment. This path was not physically induced on the mount. |
| Declined panel feedback still said motion was active after the mount stopped | Feedback now describes the declined request as a past event; current state remains in the status strip. |
| A tab left open across the control-ID update repeatedly posted an obsolete callback layout, causing server errors | Observed on restart. Unknown callback outputs now receive HTTP 409 with reload guidance before dispatch. A regression verifies no USB access or error log. The migration guide requires reloading open tabs and explicit reconnect. No old-layout adapter was added. |
| Current command catalogue still called continuous/repeated jog offline-only | Updated it to the recorded small-move acceptance and retained the unverified window-switch, immediate-stop and physical-calibration limits. |

## Migration and pruning

The active library, CLI, GUI and notebook code use `move_absolute()` and the shared
Fixed/Total/Equivalent target resolver. An AST audit found no removed `move_to`,
`angle_to_counts`, step-command or old refresh-control interface in active source
or notebook code. Negative migration tests and dated evidence deliberately retain
old strings. Public motion packets, angle units and the WinUSB transport were not
changed by this review.

Removed duplicated server shutdown code and the misleading refresh route. The
GUI entry point owns the shared lifecycle; the bench observer adds recording and
guards without another communication backend or receive path. Current docs and
README distinguish status refresh from settings reload and explain restart
migration. Manufacturer PDFs remain in the canonical `docs/references` directory.
Historical reviews, logs, failed attempts, raw captures and manifests are retained.

## Validation

- **292 Python tests and 2 JavaScript event tests passed.** New tests cover recorder
  retry ownership, interrupted shutdown phases, status refresh routing, stale
  callbacks, and lost capture without duplicate/cross-pointer release. The first
  recorder/refresh/capture regressions failed against the old code before fixes.
- Ruff lint/format and both JavaScript asset syntax checks passed. The existing
  third-party Dash pytest-plugin deprecation warning remains.
- Offline lockfile check, wheel and source-distribution builds passed at code
  checkpoint **`f43c6cf`**, before this documentation checkpoint. Archive inspection
  found no cache/temp/environment/Git tree or duplicate `docs/hardware` tree.
- The extracted wheel served startup, layout, status, assets and the exact bundled
  user-guide hash, without importing USB. It used existing `.venv` dependencies;
  this was not a fresh dependency-installation test. Entry points and removed
  interface absence were checked in the packaged code.
- All existing bench-manifest entries were verified against both working files
  and committed Git bytes. Reference PDF hashes also match their provenance record.
  Exact counts, artifact hashes, commands and source hashes are in the structured
  [review results](repository-review-20261106.json).

Windows sandbox restrictions prevented access to pytest's private fixtures and
uv's existing cache. The same offline checks passed with the normal user token;
no network dependency update was performed. Environment failures are distinct
from the deliberately failing pre-fix regressions.

## Final live check

The reviewed GUI was restarted on `http://127.0.0.1:8050`, then connected explicitly
to **55551174**, firmware **2.0.1**. Actual UI actions checked:

1. Connect and live status/position.
2. Main **Refresh status**: settings revision and read timestamp stayed unchanged.
3. Settings **Refresh readbacks**: revision and timestamp advanced; all five raw
   groups matched the original pre-test baseline exactly.
4. Close the settings dialog and leave the normal GUI connected at **zero counts,
   homed, enabled, idle and error-free**, status **`0x80000400`**.

No Home, Move, Jog, Drive, Stop, Enable/Disable, device Apply or EEPROM action was
requested in this review. Shutdown checks used idle/disconnected owners. The
shutdown-interruption, capture-loss and stale-callback rejection regressions are
offline checks, not new physical fault-injection evidence. Prior [motion
acceptance](../bench/gui-migration-20261106-231335Z.md) and [held-jog acceptance](../bench/gui-jog-20261107-002109Z.md)
retain their own source checkpoints and scope.

- [Final live GUI](repository-review-20261106-gui.png)
- [Final raw state from the same GUI owner](repository-review-20261106-state.json)
- [Structured checks, package hashes and readback comparison](repository-review-20261106.json)
- [Review-artifact SHA256 manifest](repository-review-20261106-manifest.json)

## Cleanup and checkpoints

Removed only this review's inventoried `.tmp` build/extracted-wheel/test/scratch
outputs and generated Ruff cache, after checking absolute paths, tracked-file
exclusion and reparse links. `.venv`, reference PDFs, all bench evidence and
historical reviews were preserved. The structured results record the removed
file count and bytes.

Cleanup removed **30 generated files / 12,764,847 bytes** (about 12.2 MiB), with
no tracked files or reparse links inside the checked targets. A final inventory
found no project cache/temp files outside the preserved virtual environment.

| Commit | Stage |
| --- | --- |
| `9ca812d` | Shared shutdown lifecycle and interruption recovery |
| `21c75d9` | Status refresh migration, capture-loss release and feedback |
| `f43c6cf` | Live-discovered stale-tab rejection and reload guidance |

This report, current documentation, final live evidence and verified cleanup form
the final documentation checkpoint. Changes are committed by explicit paths and
pushed without rewriting history.

Independent angle/rate calibration, mechanical stop timing, OS window switching,
USB/browser-crash fault behavior, immediate jog stop, EEPROM power-cycle retention
and endurance remain outside this review's bench evidence.
