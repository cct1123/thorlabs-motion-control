# K10CR2 Python API guide

The synchronous API is exported from `k10cr2`. Install and select the mount as
described in the [README](../README.md#setup). For guided examples, use the
[tutorial notebooks](../examples/notebooks/README.md).

## Connection and queries

```python
from k10cr2 import K10CR2

with K10CR2("55551174") as mount:
    identity = mount.info()
    status = mount.status()
    position = mount.position()
```

Supply the actual eight-digit serial. Construction alone does not access USB;
the context manager connects and closes. Explicit `connect()` / `close()` are
also available. Close releases USB and leaves motor enable/holding state unchanged.
Queries do not enable or move the motor.

Constructor timeout defaults, in seconds, are `query_timeout=3`,
`motion_timeout=120` and `cleanup_timeout=5`. Connection, identity/status/position
queries, Home, Move and Stop accept a per-call `timeout`. Values must be positive
and finite. Settings operations derive their deadline from `query_timeout` and
the number of groups involved.

One operation owns the connection at a time; commands are not queued. One owner
per serial is enforced within this process. Close other software or notebook
connections before using the mount; cross-process ownership is the operator's
responsibility.

## Motion and completion

The following calls **cause physical motion** when used on a connected mount:

```python
mount.home(timeout=120)
mount.move_absolute(90, timeout=120)
mount.move_absolute(10, mode="equivalent", direction="reverse")
mount.stop(timeout=5)
```

`move_absolute()` defaults to `mode="fixed"` and `direction="quickest"`.
Targets are in degrees and round to the nearest count, ties to even. The
[shared angle contract](migration.md#shared-angle-contract) defines Fixed,
Equivalent and Total modes, fresh-counter routing and counted travel bounds.
Existing controller backlash settings remain in effect.

Home and Move block until fresh status and position confirm completion. Home
requires a new homing/not-homed transition and exactly zero counts. Absolute Move
requires fresh idle/homed/enabled status and evidence of progress, with a residual
of at most one count (about 0.0000073°). Firmware 2.0.1 can stop one count from its
target; this tolerance does not establish mechanical accuracy. The actual counter
is retained rather than snapped to the target.

`MotionResult` contains `status`, `position`, `completion_received` and `no_op`.
A move already at its exact resolved target returns `no_op=True`. A short move
can finish without a captured busy flag if the fresh counter changed. Completion
notification receipt is reported separately from status/counter confirmation.
Loss of motor enable during Home/Move, or loss of the home reference during Move,
triggers same-owner Stop cleanup and requires explicit recovery.

## Settings

`query_settings()` reads velocity, jog, backlash, home and hardware buttons.
An optional iterable of group names selects a subset. `configure(changes,
high_power=False, persist=False)` reads fresh state, validates every selected
group before writing, and verifies changed groups by readback. A later USB failure
can leave earlier changes applied; there is no speculative rollback or replay.

Velocity, jog, backlash and button groups are editable. Home edits and unverified
mappings are unavailable. Speeds above 10°/s require a confirmed ≥900 mA supply
and `high_power=True`; the supported maximum is 20°/s and 20°/s². Confirmation
flags require actual Python booleans. EEPROM persistence is opt-in through
`persist=True` and reports a request, not power-cycle-proven retention. See the
[settings contract](gui-manual.md#settings-and-wire-support) for wire support,
validation and physical calibration limits.

## Cancellation and recovery

`home()` and `move_absolute()` accept an optional `threading.Event` as `cancel`,
plus `on_progress(status, position)`. Setting the event requests cooperative Stop
through the active owner. Do not call `stop()` from a competing thread or process
while that owner is executing motion. Console **Ctrl+C** also attempts a profiled
stop through the blocking Home/Move connection.

Progress callbacks run synchronously on the owner thread, must return promptly,
and must not call the controller. They add a position query to each motion poll;
observer failures trigger Stop cleanup.

After a motion command, cancellation raises `MotionError` with `MotionCancelled`
as its `original` cause and a separate `cleanup` record. Clean cancellation has
`recovery_required=False`; the connection remains usable after a confirmed stop
and verified temporary-profile restoration. Cancellation observed before
enabling/motion raises `MotionCancelled` directly.

A failed motion raises `MotionError`, preserving the original failure and cleanup
outcome. Cleanup has its own deadline, independent of the expired motion deadline.
Query timeouts and protocol failures require close and explicit reconnect before
normal operations resume, avoiding ambiguous delayed same-ID replies. A later
successful Stop does not clear a prior connection fault. Motion is never replayed
automatically. Reconnecting alone does not establish a new physical home reference;
query state and explicitly home when required.

A failed or unconfirmed stop reports **physical state UNKNOWN**. Timeouts propagate
to USB transfers, but enumeration, Windows driver calls, scheduling and resource
release cannot provide a hard real-time guarantee. A software stop acknowledgement
is controller evidence, not independent proof of mechanical rest or optical safety.
See the [validation record](validation.md) for the tested hardware scope.
