# Actual-mount GUI recording tools

These bench-only scripts use the production WinUSB/APT implementation. They are
not another backend and are not needed to run the normal GUI. Close the current
GUI's USB connection/server before starting another owner.

```powershell
.venv/Scripts/python.exe -B tools/bench/record_gui.py --serial 55551174 --port 8050
```

Open the printed URL. Startup does not access USB; all connection, setting and
motion actions come from the GUI. The script records timestamped GUI requests,
worker boundaries, fresh readbacks, raw APT chunks and source hashes under
`docs/bench/gui-migration-<UTC timestamp>`. Defaults add ±3° counted-travel and 2 s
manual-motion guards, recording any activation. Home is excluded from the travel
guard. For small held-jog checks, the recorded session used:

```powershell
.venv/Scripts/python.exe -B tools/bench/record_gui.py --serial 55551174 --label gui-jog --max-degrees 1 --manual-seconds 4
```

Labels select the evidence filename only. Guard bounds must be positive and no
greater than 3° / 5 s; metadata records the actual values. These guards do not replace
apparatus safety measures or guarantee a stop when communication fails. Close the
session through **Disconnect** and **Close web UI** after confirming idle state.

Capture `/api/status` from the same owner before any temporary setting changes.
It reads the GUI cache and does not create another USB reader. Keep the original
baseline and all raw logs. No EEPROM persistence is needed for these tests.

For exact cleanup after disconnect, the restoration tool accepts that captured
baseline. It requires fresh homed/idle zero, checks home/button groups unchanged,
and restores only velocity/jog/backlash using raw setter/readback values:

```powershell
.venv/Scripts/python.exe -B tools/bench/restore_raw_settings.py --baseline docs/bench/<baseline>.json --output docs/bench/<restoration>.json
```

This command writes device parameters: use only the baseline captured for the
same actual unit before the test. It sends no motion, enable or EEPROM command
and refuses to overwrite evidence files. Reconnect the normal GUI and compare
all five groups afterward. These tools were exercised in the
[migration acceptance record](../../docs/bench/gui-migration-20261106-231335Z.md).
The [held-jog record](../../docs/bench/gui-jog-20261107-002109Z.md) includes pointer
and focus release reasons, native repeated steps, explicit Stop and exact restoration.

Offline checks (no USB access):

```powershell
.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider
node tests/hold.test.mjs
```
