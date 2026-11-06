"""Execute tutorial cells with external access blocked or literal APT responses."""

import ast
import json
from collections import deque
from pathlib import Path

import pytest
from conftest import (
    HOME_DONE,
    HOME_REQUEST,
    HOMED,
    HOMING,
    INFO_REQUEST,
    MOVE_DONE,
    POSITION_90,
    POSITION_REQUEST,
    POSITION_ZERO,
    STATUS_REQUEST,
    STOP_DONE,
    STOP_REQUEST,
    ScriptedTransport,
)

from k10cr2 import MotionError
from k10cr2.transport import WinUSBTransport

NOTEBOOKS = Path(__file__).resolve().parents[1] / "examples" / "notebooks"
PATHS = sorted(NOTEBOOKS.glob("*.ipynb"))
MOVE_90 = bytes.fromhex("53 04 06 00 d0 01 01 00 00 80 bb 00")
MOVE_ZERO = bytes.fromhex("53 04 06 00 d0 01 01 00 00 00 00 00")


def execute_notebook(path, overrides=None, namespace=None):
    """Run ordinary Python cells; overrides emulate edits to tutorial configuration."""
    namespace = {} if namespace is None else namespace
    overrides = {} if overrides is None else overrides
    document = json.loads(path.read_text(encoding="utf-8"))
    assert (document["nbformat"], document["nbformat_minor"]) == (4, 5)
    for index, cell in enumerate(document["cells"]):
        if cell["cell_type"] != "code":
            continue
        assert cell["execution_count"] is None and cell["outputs"] == []
        tree = ast.parse("".join(cell["source"]), filename=f"{path.name}:cell-{index}")
        for statement in tree.body:
            if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
                target = statement.targets[0]
                if isinstance(target, ast.Name) and target.id in overrides:
                    statement.value = ast.parse(repr(overrides[target.id]), mode="eval").body
        exec(compile(ast.fix_missing_locations(tree), path.name, "exec"), namespace)
    return namespace


@pytest.fixture(autouse=True)
def block_external_access(monkeypatch):
    import platform
    import subprocess

    import usb.core

    # Python's Windows platform probe may run `ver`; cache it before blocking processes.
    platform.platform()

    def blocked(*args, **kwargs):
        pytest.fail("Notebook attempted external device access")

    monkeypatch.setattr(usb.core, "find", blocked)
    monkeypatch.setattr(subprocess, "run", blocked)
    monkeypatch.setattr(WinUSBTransport, "open", blocked)


@pytest.mark.parametrize("path", PATHS, ids=lambda path: path.stem)
def test_default_notebook_execution_has_no_external_access(path):
    execute_notebook(path)


def scripted_connections(monkeypatch, identity_bytes, sessions):
    transports = [ScriptedTransport([(INFO_REQUEST, identity_bytes), *steps]) for steps in sessions]
    pending = deque(transports)

    def open_transport(serial, deadline):
        assert serial == "55551174"
        assert pending, "Unexpected extra connection"
        return pending.popleft()

    monkeypatch.setattr(WinUSBTransport, "open", open_transport)
    return transports, pending


def assert_consumed(transports, pending):
    assert not pending
    assert all(transport.closed and not transport.steps for transport in transports)


QUERY_POSITION = [(STATUS_REQUEST, HOMED), (POSITION_REQUEST, POSITION_ZERO)]
STOP = [(STOP_REQUEST, STOP_DONE), *QUERY_POSITION]
MOVE_TO_90 = [
    *QUERY_POSITION,
    (MOVE_90, b""),
    (STATUS_REQUEST, MOVE_DONE + HOMED),
    (POSITION_REQUEST, POSITION_90),
]


@pytest.mark.parametrize(
    ("filename", "overrides", "sessions"),
    [
        ("03_connection.ipynb", {}, [[(INFO_REQUEST, None)], [(INFO_REQUEST, None)]]),
        ("04_status_and_position.ipynb", {}, [QUERY_POSITION, QUERY_POSITION * 5]),
        (
            "06_homing.ipynb",
            {"CONFIRM_HOME": "HOME 55551174"},
            [
                [(INFO_REQUEST, None), *QUERY_POSITION],
                [
                    *QUERY_POSITION,
                    (HOME_REQUEST, b""),
                    (STATUS_REQUEST, HOMING),
                    (STATUS_REQUEST, HOME_DONE + HOMED),
                    (POSITION_REQUEST, POSITION_ZERO),
                    (POSITION_REQUEST, POSITION_ZERO),
                ],
            ],
        ),
        (
            "07_absolute_motion.ipynb",
            {"CONFIRM_MOVE": "MOVE 55551174", "TARGET_DEG": 90.0},
            [[*QUERY_POSITION, *MOVE_TO_90]],
        ),
        (
            "08_stop_and_recovery.ipynb",
            {"CONFIRM_STOP": "STOP 55551174", "RECONNECT_AND_QUERY": True},
            [STOP, [(INFO_REQUEST, None), *QUERY_POSITION]],
        ),
        (
            "09_angle_sequence.ipynb",
            {"CONFIRM_SEQUENCE": "SEQUENCE 55551174", "TARGETS_DEG": [90.0, 0.0]},
            [
                [
                    (STATUS_REQUEST, HOMED),
                    *MOVE_TO_90,
                    (STATUS_REQUEST, HOMED),
                    (POSITION_REQUEST, POSITION_90),
                    (MOVE_ZERO, b""),
                    *QUERY_POSITION,
                ]
            ],
        ),
    ],
)
def test_opted_in_controller_examples(monkeypatch, identity_bytes, filename, overrides, sessions):
    import time

    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    sessions = [
        [(request, identity_bytes if reply is None else reply) for request, reply in steps]
        for steps in sessions
    ]
    transports, pending = scripted_connections(monkeypatch, identity_bytes, sessions)
    execute_notebook(
        NOTEBOOKS / filename,
        {"SERIAL": "55551174", "CONNECT_TO_DEVICE": True, **overrides},
    )
    assert_consumed(transports, pending)


def test_sequence_failure_closes_and_does_not_attempt_later_targets(monkeypatch, identity_bytes):
    steps = [
        (STATUS_REQUEST, HOMED),
        *QUERY_POSITION,
        (MOVE_90, b""),
        (STATUS_REQUEST, TimeoutError("Illustrated query failure")),
        *STOP,
    ]
    transports, pending = scripted_connections(monkeypatch, identity_bytes, [steps])
    namespace = {}
    with pytest.raises(MotionError) as caught:
        execute_notebook(
            NOTEBOOKS / "09_angle_sequence.ipynb",
            {
                "SERIAL": "55551174",
                "CONNECT_TO_DEVICE": True,
                "CONFIRM_SEQUENCE": "SEQUENCE 55551174",
                "TARGETS_DEG": [90.0, 0.0],
            },
            namespace,
        )
    assert caught.value.cleanup.confirmed_stopped
    assert namespace["sequence_results"] == []
    assert MOVE_ZERO not in transports[0].sent
    assert_consumed(transports, pending)
