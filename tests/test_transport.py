from types import SimpleNamespace

import pytest

from k10cr2.transport import WinUSBTransport, strip_ftdi_status


def test_ftdi_packet_headers_not_transfer_headers():
    first = bytes.fromhex("11 60") + bytes(range(62))
    second = bytes.fromhex("11 60 44 04 01 00 01 50")
    assert strip_ftdi_status(first + second) == bytes(range(62)) + second[2:]
    assert strip_ftdi_status(bytes.fromhex("11 60")) == b""
    assert strip_ftdi_status(b"") == b""
    for data in (b"\x11", bytes.fromhex("11 62 00")):
        with pytest.raises(OSError):
            strip_ftdi_status(data)


def test_partial_write_not_replayed():
    class USB:
        writes = 0

        def write(self, *args, **kwargs):
            self.writes += 1
            return 2

    usb = USB()
    transport = WinUSBTransport(usb, 0x81, 2, None, None)
    import time

    with pytest.raises(OSError, match="Partial"):
        transport.write(bytes.fromhex("43 04 01 00 50 01"), time.monotonic() + 1)
    assert usb.writes == 1


def test_deadline_propagates_and_late_write_is_not_success(monkeypatch):
    import k10cr2.transport as module

    clock = [10.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])

    class USB:
        def write(self, endpoint, data, timeout):
            assert timeout == 250
            clock[0] = 10.251
            return len(data)

    transport = WinUSBTransport(USB(), 0x81, 2, None, None)
    with pytest.raises(TimeoutError):
        transport.write(b"123456", 10.25)


def test_close_disposes_even_when_release_fails():
    disposed = []

    def release(*args):
        raise OSError("Release failed")

    util = SimpleNamespace(
        release_interface=release, dispose_resources=lambda d: disposed.append(d)
    )
    transport = WinUSBTransport("device", 0x81, 2, None, util)
    with pytest.raises(OSError):
        transport.close()
    transport.close()
    assert disposed == ["device"]


@pytest.mark.parametrize("failure", [None, "setup", "endpoints"])
def test_open_exact_serial_initializes_only_uart_and_closes(monkeypatch, failure):
    import time

    import libusb_package
    import usb.core
    import usb.util

    calls = []

    class Configuration:
        bNumInterfaces = 1

        def __getitem__(self, index):
            assert index == (0, 0)
            return [
                SimpleNamespace(
                    bEndpointAddress=2 if failure == "endpoints" else 0x81,
                    bmAttributes=2,
                    wMaxPacketSize=64,
                ),
                SimpleNamespace(bEndpointAddress=2, bmAttributes=2, wMaxPacketSize=64),
            ]

    class Device:
        bcdDevice = 0x1000

        def __init__(self, serial):
            self.serial_number = serial

        def get_active_configuration(self):
            return Configuration()

        def ctrl_transfer(self, kind, request, value, index, data, timeout):
            assert kind == 0x40 and data == b"" and 0 < timeout <= 1000
            calls.append((self.serial_number, request, value, index))
            if failure == "setup":
                raise OSError("Original setup failure")

    wrong, target = Device("87654321"), Device("12345678")
    monkeypatch.setattr(libusb_package, "get_libusb1_backend", lambda: object())
    monkeypatch.setattr(usb.core, "find", lambda **kwargs: [wrong, target])
    claims, released, disposed = [], [], []
    monkeypatch.setattr(usb.util, "claim_interface", lambda d, i: claims.append((d, i)))

    def release(device, interface):
        released.append((device, interface))
        if failure == "setup":
            raise OSError("Cleanup release failure")

    monkeypatch.setattr(usb.util, "release_interface", release)
    monkeypatch.setattr(usb.util, "dispose_resources", lambda d: disposed.append(d))
    if failure:
        with pytest.raises(
            OSError, match="Original setup" if failure == "setup" else "one input"
        ) as caught:
            WinUSBTransport.open("12345678", time.monotonic() + 3)
        assert disposed == [wrong, target]
        if failure == "setup":
            assert "Cleanup release failure" in str(caught.value.__notes__)
        else:
            assert not claims and not calls
        return
    transport = WinUSBTransport.open("12345678", time.monotonic() + 3)
    assert claims == [(target, 0)]
    assert all(item[0] == "12345678" for item in calls)
    assert [(r, v, i) for _, r, v, i in calls] == [
        (0, 0, 1),
        (3, 0x001A, 0),
        (4, 8, 1),
        (0, 1, 1),
        (0, 2, 1),
        (9, 2, 1),
        (2, 0, 0x0101),
        (1, 0x0202, 1),
    ]
    transport.close()
    assert released == [(target, 0)] and disposed == [wrong, target]


@pytest.mark.parametrize("failure", ["deadline", "interrupt"])
def test_enumeration_failure_disposes_current_and_previous_matches(monkeypatch, failure):
    import libusb_package
    import usb.core
    import usb.util

    import k10cr2.transport as module

    clock = [0.0]
    target = SimpleNamespace(serial_number="12345678")
    current = SimpleNamespace(serial_number="87654321")

    def candidates(**kwargs):
        yield target
        if failure == "deadline":
            clock[0] = 2.0
            yield current
        else:
            raise KeyboardInterrupt

    disposed = []
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(libusb_package, "get_libusb1_backend", lambda: object())
    monkeypatch.setattr(usb.core, "find", candidates)
    monkeypatch.setattr(usb.util, "dispose_resources", lambda d: disposed.append(d))
    with pytest.raises(TimeoutError if failure == "deadline" else KeyboardInterrupt):
        WinUSBTransport.open("12345678", 1.0)
    assert disposed == ([target, current] if failure == "deadline" else [target])
