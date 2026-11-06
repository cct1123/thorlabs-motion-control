import pytest
from conftest import HOME_DONE, HOMED, POSITION_90, POSITION_REQUEST, STATUS_REQUEST

from k10cr2 import K10CR2


def test_queries_with_fragmentation_and_interleaved_event(mount_factory):
    mount, fake = mount_factory(
        [
            (STATUS_REQUEST, [HOME_DONE[:2], HOME_DONE[2:] + HOMED[:7], HOMED[7:]]),
            (POSITION_REQUEST, [POSITION_90[:1], POSITION_90[1:]]),
        ]
    )
    assert mount.status().homed
    assert mount.position().degrees == 90
    mount.close()
    assert fake.closed and not fake.steps
    assert all(packet[:2] not in (b"\x10\x02", b"\x43\x04", b"\x53\x04") for packet in fake.sent)


def test_wrong_identity_closes_without_actuation(mount_factory, identity_bytes):
    wrong = identity_bytes.replace(b"K10CR2", b"K10CR1")
    mount, fake = mount_factory([], identity=wrong, connect=False)
    with pytest.raises(RuntimeError, match="Wrong device"):
        mount.connect()
    assert fake.closed and len(fake.sent) == 1


def test_wrong_serial_closes(mount_factory, identity_bytes):
    wrong = identity_bytes[:6] + bytes.fromhex("c7 a4 4f 03") + identity_bytes[10:]
    mount, fake = mount_factory([], identity=wrong, connect=False)
    with pytest.raises(RuntimeError, match="Wrong device"):
        mount.connect()
    assert fake.closed


def test_query_failure_requires_explicit_reconnect(mount_factory):
    mount, fake = mount_factory([(STATUS_REQUEST, TimeoutError("No reply"))])
    with pytest.raises(TimeoutError):
        mount.status()
    assert mount.recovery_required
    with pytest.raises(RuntimeError, match="uncertain"):
        mount.status()
    assert len(fake.sent) == 2
    mount.close()


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), True, "3"])
def test_bad_timeout_before_io(mount_factory, value):
    mount, fake = mount_factory([])
    with pytest.raises(ValueError):
        mount.status(timeout=value)
    assert len(fake.sent) == 1
    with pytest.raises(ValueError):
        K10CR2("55551174", query_timeout=value)


@pytest.mark.parametrize("serial", [None, "", "5555", "abcdefgh", "５５５５１１７４", 55551174])
def test_explicit_serial(serial):
    with pytest.raises(ValueError):
        K10CR2(serial)


def test_concurrent_operation_rejected(mount_factory):
    mount, fake = mount_factory([])
    with mount._operation():
        with pytest.raises(RuntimeError, match="Another operation"):
            mount.status()
    assert len(fake.sent) == 1


def test_second_controller_cannot_own_same_serial(mount_factory):
    mount, fake = mount_factory([])
    other = K10CR2("55551174")
    with pytest.raises(RuntimeError, match="Another controller"):
        other.connect()
    other.close()
    with pytest.raises(RuntimeError, match="Another controller"):
        other.connect()
    assert len(fake.sent) == 1
    mount.close()


def test_identity_decode_failure_requires_reconnect(mount_factory, identity_bytes):
    from conftest import INFO_REQUEST

    from k10cr2 import ProtocolError

    malformed = identity_bytes[:10] + b"\xff" + identity_bytes[11:]
    mount, fake = mount_factory([(INFO_REQUEST, malformed)])
    with pytest.raises(ProtocolError, match="Non-ASCII"):
        mount.info()
    assert mount.recovery_required
    with pytest.raises(RuntimeError, match="uncertain"):
        mount.status()
    assert not fake.steps
