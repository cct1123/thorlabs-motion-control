"""WinUSB -> FTDI UART -> APT bytes. USB modules load only on explicit open."""

import logging
import math
import time

log = logging.getLogger(__name__)


def remaining(deadline: float) -> float:
    value = deadline - time.monotonic()
    if value <= 0:
        raise TimeoutError("Operation deadline expired")
    return value


def timeout_ms(deadline: float, cap: int = 1000) -> int:
    return max(1, min(cap, math.ceil(remaining(deadline) * 1000)))


def strip_ftdi_status(data: bytes, packet_size: int = 64) -> bytes:
    result = bytearray()
    for offset in range(0, len(data), packet_size):
        packet = data[offset : offset + packet_size]
        if len(packet) < 2:
            raise OSError("Truncated FTDI packet status header")
        if packet[1] & 0x1E:
            raise OSError(f"FTDI UART error status: {packet[1]:#04x}")
        result.extend(packet[2:])
    return bytes(result)


class WinUSBTransport:
    def __init__(self, device, endpoint_in: int, endpoint_out: int, usb_core, usb_util):
        self.device = device
        self.endpoint_in, self.endpoint_out = endpoint_in, endpoint_out
        self._core, self._util = usb_core, usb_util
        self._closed = False

    @classmethod
    def open(cls, serial: str, deadline: float):
        import libusb_package
        import usb.core
        import usb.util

        remaining(deadline)
        backend = libusb_package.get_libusb1_backend()
        if backend is None:
            raise OSError("libusb backend unavailable")
        matches = []
        for candidate in usb.core.find(
            find_all=True, idVendor=0x0403, idProduct=0xFAF0, backend=backend
        ):
            remaining(deadline)
            try:
                if candidate.serial_number == serial:
                    matches.append(candidate)
                else:
                    usb.util.dispose_resources(candidate)
            except (usb.core.USBError, ValueError):
                usb.util.dispose_resources(candidate)
        if len(matches) != 1:
            for candidate in matches:
                usb.util.dispose_resources(candidate)
            raise OSError(
                f"Expected one accessible K10CR2 USB serial {serial}; found {len(matches)}. "
                "Check the connection, WinUSB binding, and other device owners."
            )
        device = matches[0]
        claimed = False
        try:
            remaining(deadline)
            if device.bcdDevice != 0x1000:
                raise OSError("Unsupported USB bridge revision; tested bcdDevice is 0x1000")
            cfg = device.get_active_configuration()
            if cfg.bNumInterfaces != 1:
                raise OSError("Expected one USB interface")
            endpoints = list(cfg[(0, 0)])
            if len(endpoints) != 2 or any(
                usb.util.endpoint_type(e.bmAttributes) != 2 for e in endpoints
            ):
                raise OSError("Expected two bulk endpoints")
            by_direction = {usb.util.endpoint_direction(e.bEndpointAddress): e for e in endpoints}
            if set(by_direction) != {usb.util.ENDPOINT_IN, usb.util.ENDPOINT_OUT}:
                raise OSError("Expected one input and one output bulk endpoint")
            ep_in, ep_out = by_direction[usb.util.ENDPOINT_IN], by_direction[usb.util.ENDPOINT_OUT]
            if ep_in.wMaxPacketSize != 64 or ep_out.wMaxPacketSize != 64:
                raise OSError("Expected 64-byte USB packets")
            usb.util.claim_interface(device, 0)
            claimed = True
            # UART reset, baud, 8N1, buffer purge, latency, RTS/CTS, assert RTS.
            # These are bridge control transfers, not motor or firmware commands.
            setup = [
                (0, 0, 1),
                (3, 0x001A, 0),
                (4, 8, 1),
                (0, 1, 1),
                (0, 2, 1),
                (9, 2, 1),
                (2, 0, 0x0101),
                (1, 0x0202, 1),
            ]
            for request, value, index in setup:
                device.ctrl_transfer(0x40, request, value, index, b"", timeout=timeout_ms(deadline))
                remaining(deadline)
            time.sleep(min(0.05, remaining(deadline)))
            remaining(deadline)
            return cls(device, ep_in.bEndpointAddress, ep_out.bEndpointAddress, usb.core, usb.util)
        except BaseException as original:
            try:
                if claimed:
                    usb.util.release_interface(device, 0)
            except Exception as cleanup_error:
                original.add_note(f"USB interface cleanup also failed: {cleanup_error}")
            try:
                usb.util.dispose_resources(device)
            except Exception as cleanup_error:
                original.add_note(f"USB resource disposal also failed: {cleanup_error}")
            raise

    def write(self, data: bytes, deadline: float) -> None:
        if self._closed:
            raise OSError("USB transport is closed")
        log.debug("APT TX %s", data.hex(" "))
        count = self.device.write(self.endpoint_out, data, timeout=timeout_ms(deadline))
        if count != len(data):
            raise OSError(f"Partial APT write ({count}/{len(data)} bytes); command not retried")
        remaining(deadline)

    def read(self, deadline: float) -> bytes:
        if self._closed:
            raise OSError("USB transport is closed")
        try:
            raw = bytes(self.device.read(self.endpoint_in, 512, timeout=timeout_ms(deadline, 100)))
        except self._core.USBTimeoutError:
            remaining(deadline)
            return b""
        remaining(deadline)
        data = strip_ftdi_status(raw)
        if data:
            log.debug("APT RX %s", data.hex(" "))
        return data

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            try:
                self._util.release_interface(self.device, 0)
            finally:
                self._util.dispose_resources(self.device)
