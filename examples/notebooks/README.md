# K10CR2 tutorial notebooks

Nine independent tutorials introduce the current Python API, from environment checks
to a bounded motion sequence. Start each notebook with a fresh kernel and run its
cells from top to bottom. No notebook depends on variables from another notebook.

## Launch

From the repository root in PowerShell:

```powershell
uv sync --locked
uv run --locked --with jupyterlab --with ipykernel python -m jupyterlab examples/notebooks
```

The `--with` packages supply notebook tooling without changing the project's runtime
dependencies or lockfile. Their versions are not pinned by this repository's lockfile.
Select **Python 3 (ipykernel)** and check the executable and package versions in
notebook 01. If `import k10cr2` fails, stop and select a kernel with this project
installed. Do not install packages into an unrelated kernel to hide the mismatch.

For an existing Jupyter installation, register the project environment as a kernel:

```powershell
uv run --locked --with ipykernel python -m ipykernel install --user --name k10cr2-tutorial --display-name "K10CR2 tutorial"
```

Then select **K10CR2 tutorial** in that installation. This optional command writes a
user kernelspec. Repeat it if the repository or environment is moved or recreated.

## Tutorials

| Notebook | Topics | Optional hardware action |
| --- | --- | --- |
| [01 - Required drivers](01_check_required_drivers.ipynb) | Kernel, packages, libusb backend, Windows PnP service/INF inspection | Read Windows device information |
| [02 - Device discovery](02_device_discovery.ipynb) | VID/PID candidates, USB descriptors, serial selection, resource release | Read USB descriptors |
| [03 - Connection](03_connection.ipynb) | Context manager, identity verification, explicit connect/close | Bridge setup and identity queries |
| [04 - Status and position](04_status_and_position.ipynb) | Status flags, raw bits, counter units, bounded polling | Status/position queries |
| [05 - Settings and units](05_settings_and_units.ipynb) | Software timeouts, logging, angle quantization, unsupported settings | None |
| [06 - Homing](06_homing.ipynb) | Preflight, explicit home, completion and cleanup records | Physical homing; may enable motor |
| [07 - Absolute motion](07_absolute_motion.ipynb) | Target preview, progress callback, no-op and completion results | Physical absolute move; may enable motor |
| [08 - Stop and recovery](08_stop_and_recovery.ipynb) | Failure records, interruption, standalone stop, explicit reconnect | Profiled stop and recovery queries |
| [09 - Angle sequence](09_angle_sequence.ipynb) | Full-list validation, sequential moves, partial results, stop on failure | Multiple physical moves; may enable motor |

## Hardware opt-in

As distributed, **Run All does not enumerate USB devices, inspect Windows PnP devices,
connect, or command motion**. Notebook 01 loads the libusb backend without enumerating.
Code cells have no stored outputs or execution counts.

- Discovery and Windows driver inspection each have their own default-off flag.
- Connection examples use an empty `SERIAL` and `CONNECT_TO_DEVICE = False`. Enter the
  actual eight-digit serial from discovery and verify it on the mount's label.
- Each actuator cell also requires an explicit confirmation string containing the
  selected serial. Review the target/path before entering it, then clear it after use.
  A rerun with the confirmation still set can repeat the command.
- Use one owner per device, including across notebook kernels and external programs.
  All connection examples close their handles within the same cell.

Only settings implemented by the public API are shown. Velocity, acceleration,
backlash, direction, persistent parameters, and firmware writes are unsupported.
The controller's existing motion settings determine the route; these notebooks do
not assume the shortest path. Closing a connection leaves holding state unchanged.

While a home/move is blocking, interrupt its own cell to let that owner attempt stop
cleanup. A queued cell or a second notebook is not a live stop control. Forced kernel
termination or a lost USB link cannot guarantee a stop. A failed/unconfirmed stop means
physical state is unknown; use the apparatus's physical safety procedure. Consult
[08](08_stop_and_recovery.ipynb) before recovering from a failed operation.

## Validation limits

Default execution is checked offline with external device access blocked. Guarded
controller examples are checked with scripted APT responses. These checks establish
notebook/API consistency and opt-in behavior, not new bench evidence. They do not
verify Windows driver visibility, physical travel, angle accuracy, or notebook
interrupt delivery on your apparatus. Existing hardware evidence is documented in
[the validation record](../../docs/validation.md); the protocol and unit contract is
in [the command reference](../../docs/commands.md).
