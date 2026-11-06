# Controller and GUI migration

The current implementation uses one WinUSB/APT controller, one motion API and one
set of rotation rules across the library, CLI and web UI. No Kinesis/SDK backend,
legacy command adapter or automatic startup configuration is retained.

| Earlier development interface | Current interface |
| --- | --- |
| `mount.move_to(angle, mode=..., direction=...)` | `mount.move_absolute(angle, mode=..., direction=...)` |
| GUI `move-to` command/control ID | `move` |
| GUI `step-plus` / `step-minus`, `submit(..., step=...)` | Removed. Use native Jog with readback-verified jog settings. |
| `k10cr2.protocol.angle_to_counts(angle)` | `k10cr2.settings.resolve_target(angle, current_degrees, mode, direction)` for a preview in counts; ordinary code passes degrees to `move_absolute()` |
| `k10cr2.gui` re-exports of view helpers | `create_app`, `controls`, `CONTROL_IDS` live in `k10cr2.gui_view`; `k10cr2.gui` owns the server/entry point |
| Settings OK / implicit Apply | Explicit Apply session options, Apply device settings, and Close |

These interfaces have no compatibility aliases. Repository callers, tests and
tutorial notebooks are migrated together.

## Shared angle contract

- `fixed` (default): finite absolute target **0–360° inclusive**, from a counter
  already inside that interval. A 360° target is a full counted turn, distinct from
  zero. Nearest-count rounding can round a value just below 360° to that endpoint.
- `total`: finite signed target within **±15,480°** (the manual's 43-turn bound).
- `equivalent`: finite phase **0–360°**, resolved from a fresh counted position
  using `quickest`, `forward` or `reverse`. The display wraps; the counter retains
  complete turns. A 180° tie chooses forward.

The controller resolves routes while it owns USB. A GUI preview or earlier
readback is never used as a substitute for that fresh position. All motion
requires explicit intent and a home reference; imports, help and server startup
perform no USB access. Existing controller backlash parameters remain in effect.

```python
mount.move_absolute(90)  # Fixed Range, degrees
mount.move_absolute(10, mode="equivalent", direction="reverse")
```

```powershell
k10cr2 --serial 55551174 move 10 --mode equivalent --direction reverse
```

These examples cause physical motion when called on a connected mount. The CLI
options and library arguments have the same meaning as the GUI session rules.

## Explicit settings authorization

`high_power`, `persist` and manual-motion `jog` accept actual Python booleans only.
Values such as `"false"`, `0` or `1` are rejected before device I/O. EEPROM
persistence requires `persist=True`; speeds above 10°/s require a confirmed
≥900 mA supply and `high_power=True`. Hardware settings are read before editing,
validated as a complete draft, written only when changed and verified by readback.
Persisted retention still requires an independent power-cycle test.

The prior [motion bench record](bench/gui-controls-20261106-211627Z.md) and
[live UI review](bench/gui-live-review-20261106.md) preserve their original API/DOM
names and source hashes as historical evidence. New rotation modes and boundary
cases have offline tests; those records do not establish physical calibration or
bench validation of every mode.
