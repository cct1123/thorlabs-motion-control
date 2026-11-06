from collections import deque
from pathlib import Path

import pytest

from k10cr2 import K10CR2
from k10cr2.transport import WinUSBTransport, remaining

FIXTURES = Path(__file__).parent / "fixtures"
INFO_REQUEST = bytes.fromhex("05 00 00 00 50 01")
STATUS_REQUEST = bytes.fromhex("29 04 01 00 50 01")
POSITION_REQUEST = bytes.fromhex("11 04 01 00 50 01")
HOME_REQUEST = bytes.fromhex("43 04 01 00 50 01")
STOP_REQUEST = bytes.fromhex("65 04 01 02 50 01")
ENABLE_REQUEST = bytes.fromhex("10 02 01 01 50 01")
IDLE = bytes.fromhex("2a 04 06 00 81 50 01 00 00 00 00 80")
HOMED = bytes.fromhex("2a 04 06 00 81 50 01 00 00 04 00 80")
HOMING = bytes.fromhex("2a 04 06 00 81 50 01 00 00 02 00 80")
MOVING = bytes.fromhex("2a 04 06 00 81 50 01 00 10 04 00 80")
POSITION_ZERO = bytes.fromhex("12 04 06 00 81 50 01 00 00 00 00 00")
POSITION_90 = bytes.fromhex("12 04 06 00 81 50 01 00 00 80 bb 00")
HOME_DONE = bytes.fromhex("44 04 01 00 01 50")
MOVE_DONE = bytes.fromhex("64 04 0e 00 81 50 01 00 00 80 bb 00 00 00 00 00 00 04 00 80")
STOP_DONE = bytes.fromhex("66 04 0e 00 81 50 01 00 00 00 00 00 00 00 00 00 00 04 00 80")


class ScriptedTransport:
    def __init__(self, steps):
        self.steps = deque(steps)
        self.chunks = deque()
        self.sent = []
        self.closed = False
        self.deadlines = []

    def write(self, data, deadline):
        remaining(deadline)
        self.sent.append(data)
        self.deadlines.append(deadline)
        assert self.steps, f"Unexpected write {data.hex(' ')}"
        expected, response = self.steps.popleft()
        assert data == expected
        if isinstance(response, BaseException):
            raise response
        self.chunks.extend(response if isinstance(response, list) else [response])

    def read(self, deadline):
        remaining(deadline)
        assert self.chunks, "Read without a scripted response"
        chunk = self.chunks.popleft()
        if isinstance(chunk, BaseException):
            raise chunk
        return chunk

    def close(self):
        self.closed = True


@pytest.fixture
def identity_bytes():
    return bytes.fromhex((FIXTURES / "identity.hex").read_text())


@pytest.fixture
def mount_factory(monkeypatch, identity_bytes):
    mounts = []

    def build(steps, *, identity=identity_bytes, connect=True, **kwargs):
        fake = ScriptedTransport([(INFO_REQUEST, identity), *steps])
        monkeypatch.setattr(WinUSBTransport, "open", lambda serial, deadline: fake)
        mount = K10CR2("55551174", **kwargs)
        mounts.append(mount)
        if connect:
            mount.connect()
        return mount, fake

    yield build
    for mount in mounts:
        mount.close()
