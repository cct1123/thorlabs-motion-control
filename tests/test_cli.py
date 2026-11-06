import os
import subprocess
import sys

from k10cr2.cli import main


def test_import_and_help_do_not_import_usb():
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    code = (
        "import sys; import k10cr2; from k10cr2.cli import main; "
        "assert 'usb.core' not in sys.modules; "
        "\ntry: main(['--help'])\nexcept SystemExit as e: assert e.code == 0\n"
        "assert 'usb.core' not in sys.modules"
    )
    result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_cli_query_json_without_hardware(monkeypatch, capsys):
    from k10cr2.protocol import Status

    class Mount:
        def __init__(self, serial):
            assert serial == "55551174"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def status(self):
            return Status(0x80000400, False, False, True, True, False)

    monkeypatch.setattr("k10cr2.cli.K10CR2", Mount)
    assert main(["--serial", "55551174", "status"]) == 0
    assert '"homed": true' in capsys.readouterr().out


def test_invalid_motion_arguments_do_not_construct_controller(monkeypatch, capsys):
    def unexpected(*args, **kwargs):
        raise AssertionError("Invalid CLI arguments reached connection setup")

    monkeypatch.setattr("k10cr2.cli.K10CR2", unexpected)
    assert main(["--serial", "55551174", "move", "nan"]) == 1
    assert main(["--serial", "55551174", "home", "--timeout", "0"]) == 1
    assert "finite" in capsys.readouterr().err


def test_cli_passes_the_shared_rotation_options(monkeypatch, capsys):
    from k10cr2 import MotionResult, Position, Status

    calls = []

    class Mount:
        def __init__(self, serial):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def move_absolute(self, angle, **kwargs):
            calls.append((angle, kwargs))
            return MotionResult(
                Status(0x80000400, False, False, True, True, False), Position(0, 0), False
            )

    monkeypatch.setattr("k10cr2.cli.K10CR2", Mount)
    assert (
        main(
            ["--serial", "55551174", "move", "10", "--mode", "equivalent", "--direction", "reverse"]
        )
        == 0
    )
    assert calls == [(10.0, dict(mode="equivalent", direction="reverse", timeout=None))]
    assert '"position"' in capsys.readouterr().out
