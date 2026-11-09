# K10CR2 rotation control

Control a Thorlabs K10CR2 rotation mount on Windows using a browser or simple
commands. Kinesis is not required.

## Before you start

- Install [uv](https://docs.astral.sh/uv/getting-started/installation/).
- Connect the mount by USB. Its Windows driver must be **WinUSB**; this project
  does not install the driver.
- Close Kinesis and any other program connected to the mount.
- Make sure the mount can turn freely without pulling cables or hitting anything.

## Setup

Download or clone this repository, then open its folder in PowerShell. Run:

```powershell
uv sync --extra gui
```

## Open the controls

```powershell
uv run --extra gui k10cr2-gui
```

Open [http://127.0.0.1:8050](http://127.0.0.1:8050) in your browser.

1. Enter the eight-digit serial number printed on your mount.
2. Click **Connect**. This reads the mount without moving it.
3. If it is not homed, click **Home** to find its zero position. **This moves the mount.**
4. Click **Move** to enter an angle in degrees and start a move.
5. Use **Stop** to interrupt motion.

When finished, stop motion, click **Disconnect**, then **Close web UI**.
Closing the browser tab alone does not stop a move or close the program.

## Use commands instead

First disconnect and close the web controls. Replace `55551174` below with your
mount's serial number.

Read the current position without moving:

```powershell
uv run k10cr2 --serial 55551174 position
```

Find zero, then move to 10 degrees. **Both commands move the mount.**

```powershell
uv run k10cr2 --serial 55551174 home
uv run k10cr2 --serial 55551174 move 10
```

To interrupt a running command, press **Ctrl+C in the same PowerShell window**.
Software Stop cannot guarantee a stop if the USB connection fails.

For command help:

```powershell
uv run k10cr2 --help
```

## Learn more

- [GUI controls and settings](docs/gui-manual.md)
- [Python examples](examples/notebooks/README.md) and [API guide](docs/python-api.md)
- [Rotation modes](docs/migration.md#shared-angle-contract)
- [Test results and known limits](docs/validation.md)
- [Technical command reference](docs/commands.md)
