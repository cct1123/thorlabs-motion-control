# Live web UI review — 6 November 2026

Reviewed the redesigned Dash UI at `http://127.0.0.1:8051` using the actual
K10CR2, serial **55551174**, firmware **2.0.1**, hardware revision **4**.
All connection and control interactions used the web UI. Communication used our
WinUSB/APT implementation exclusively. No Kinesis or SDK was invoked.

This review sent no motion, enable/disable, device-setting or EEPROM commands.
Session-only display precision was exercised and returned to six decimal places.
The GUI remains open and connected, displaying **0.000000° counted position**,
homed, enabled and idle. The retained `/api/status` snapshot is a read-only export
from the same GUI owner, not a second USB connection.

## Reproduced issues and fixes

| Observed issue | Change and verification |
| --- | --- |
| Connect left parameter summaries empty until an extra load action | Connect reads all five settings groups; actual values populate immediately. Startup still does not connect. |
| Completed Connect/Refresh still displayed “requested” | Feedback follows the worker outcome; verified “Device readbacks updated.” after Refresh. |
| Feedback and errors were behind the settings dialog | Added feedback inside both dialogs; an invalid decimal count visibly reports the 0–9 requirement. |
| Settings OK did not close; Escape did nothing | Replaced ambiguous OK with Close. Close, × and Escape discard drafts and restore focus to the opener. Verified an unapplied decimal edit was discarded. |
| Display Apply could collide with background read polling | Host-only session edits can proceed during a background read. Verified 6 → 3 → 6 decimals while connected. Motion still blocks session changes. |
| Long settings form hid actions and feedback | Scroll the form body while keeping the title/read toolbar and footer visible; verified lower velocity/backlash fields at the normal browser size. |
| Keyboard navigation could leave the modal | Focus opens on the heading; Shift+Tab wraps to Close and Tab wraps to Stop. Escape returns focus to Settings/Move. |
| Move Stop stretched across the title | Limited full-width button styling to Move's action buttons; visually verified the compact header. |
| Reset retained the previous validation error | Reset restores values and replaces feedback; verified invalid decimal count 10 → Reset → 6. |
| Disabled dropdowns displayed “Select…” | Unverified/locked dropdowns now display “Unavailable”; all disabled mappings remain unable to send commands. |
| Repeated help links, implementation footer and raw dictionary clutter | Kept one User guide link; shortened control help and session actions; replaced the dictionary with compact device facts. |

Also exercised all five settings tabs, main Refresh, Move open/reset/Escape, and
an invalid −1° fixed-range target. The invalid target disabled Move and displayed
its range error; no move was submitted. Device Apply/profile Apply and motion
controls were not clicked in this review.

## Device readback

| Parameter | Displayed value |
| --- | --- |
| Counted position | 0 counts / 0.000000° |
| Status bits | `0x80000400` |
| Move velocity / acceleration | 10.00132144°/s / 10°/s² |
| Jog step / velocity / acceleration | 0.09999756° / 15.00198223°/s / 15°/s² |
| Jog mode / stop mode | Single step / Profiled |
| Backlash | 0.99999756° |
| Home direction / switch | 2 / 1 |
| Home offset / velocity | 3.999998° / 10.001321°/s |
| Hardware buttons | Move By (Jog); 0.99999756° and 5.00000244° |

All five raw groups match the [saved pre-test baseline](gui-controls-20261106-211627Z-baseline.json)
exactly. Displayed rates use the documented protocol conversion constants; this
review does not establish independent physical velocity or angular accuracy.
Settings show their last read time; counted position/status continue polling.

## Evidence and checks

- [Connected panel](gui-live-review-20261106-main.jpg)
- [Settings and jog readback](gui-live-review-20261106-settings.jpg)
- [Scrolled velocity and backlash readback](gui-live-review-20261106-profile.jpg)
- [Final raw GUI state](gui-live-review-20261106-state.json)
- Offline suite: **198 passed**. Ruff: **all checks passed**. New dialog JavaScript:
  Node syntax check passed. Regression checks cover read-only Connect, settings
  read failure/retry, polling versus session edits, close/discard, reset and feedback.
- Final server log contains startup lines only; browser errors observed during
  intentional server restarts preceded the final reload and connection.

The earlier [actual-motion bench record](gui-controls-20261106-211627Z.md) remains
the evidence for Home, small Move, Jog, Drive, Stop and settings write/restoration.
This UI review does not repeat those motion tests or extend their physical claims.
