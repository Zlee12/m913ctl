"""Talking to the M913 through Linux hidraw.

Requests go out as HID feature reports (HIDIOCSFEATURE), report ID 8, on the
mouse's second interface; answers come back as input reports with the same
report ID on that same hidraw node. The kernel driver stays bound, so the
mouse keeps working while we talk to it.
"""

import errno
import fcntl
import glob
import os
import select
import time

from . import protocol as P

_IOC_WRITE, _IOC_READ = 1, 2


def _hidiocsfeature(n):
    return ((_IOC_WRITE | _IOC_READ) << 30) | (n << 16) | (ord("H") << 8) | 0x06


class DeviceError(RuntimeError):
    pass


class Timeout(DeviceError):
    pass


def _read(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def _has_report_id_8(desc: bytes) -> bool:
    """Walk a HID report descriptor looking for Report ID (0x85) = 8."""
    i = 0
    while i < len(desc):
        b = desc[i]
        if b == 0xFE:  # long item
            if i + 1 >= len(desc):
                break
            i += 3 + desc[i + 1]
            continue
        size = (0, 1, 2, 4)[b & 3]
        if b & 0xFC == 0x84 and size == 1 and i + 1 < len(desc) and desc[i + 1] == P.REPORT_ID:
            return True
        i += 1 + size
    return False


def find(sysfs="/sys/class/hidraw"):
    """List candidate devices: [{'node', 'pid', 'bcd', 'iface'}], wired first."""
    out = []
    for d in sorted(glob.glob(os.path.join(sysfs, "hidraw*"))):
        dev = os.path.realpath(os.path.join(d, "device"))
        uevent = (_read(os.path.join(dev, "uevent")) or b"").decode(errors="replace")
        hid_id = next((ln.split("=", 1)[1] for ln in uevent.splitlines()
                       if ln.startswith("HID_ID=")), "")
        parts = hid_id.split(":")
        if len(parts) != 3:
            continue
        vid, pid = int(parts[1], 16), int(parts[2], 16)
        if vid != P.VID or pid not in P.PIDS:
            continue
        desc = _read(os.path.join(dev, "report_descriptor")) or b""
        iface_dir = os.path.dirname(dev)          # .../1-2:1.1
        usb_dir = os.path.dirname(iface_dir)       # .../1-2
        iface = os.path.basename(iface_dir).rsplit(".", 1)[-1]
        bcd = _read(os.path.join(usb_dir, "bcdDevice"))
        if not _has_report_id_8(desc):
            continue
        out.append({
            "node": "/dev/" + os.path.basename(d),
            "pid": pid,
            "iface": iface,
            "bcd": int(bcd.strip(), 16) if bcd else None,
        })
    out.sort(key=lambda x: (x["pid"] != P.PID_WIRED, x["iface"] != "1", x["node"]))
    return out


def sensor_from_bcd(bcd):
    return None if bcd is None else bcd >> 12


class Mouse:
    """One open hidraw node. Use as a context manager."""

    def __init__(self, node, timeout=1.0, log=None):
        self.node = node
        self.timeout = timeout
        self.log = log
        self.fd = None
        self.acks = None  # learnt on the first write: does the mouse answer writes?

    def __enter__(self):
        try:
            self.fd = os.open(self.node, os.O_RDWR | os.O_NONBLOCK)
        except PermissionError:
            raise DeviceError(
                f"no permission to open {self.node}. Install the udev rule "
                "(70-m913ctl.rules) and re-plug the mouse/dongle, or log out and in.")
        except FileNotFoundError:
            raise DeviceError(f"{self.node} not found")
        try:
            self.send(P.packet(P.CMD_DRV_STATUS, 0, b"\x01"))
            self._wait(P.CMD_DRV_STATUS, None, 0.3, required=False)
        except BaseException:
            os.close(self.fd)
            raise
        return self

    def __exit__(self, *exc):
        try:
            self.send(P.packet(P.CMD_DRV_STATUS, 0, b"\x00"))
            self._wait(P.CMD_DRV_STATUS, None, 0.3, required=False)
        except DeviceError:
            pass
        finally:
            os.close(self.fd)

    # -- low level --
    def send(self, pkt: bytes):
        if self.log:
            self.log(">> " + pkt.hex(" "))
        buf = bytearray(pkt)
        for attempt in range(3):  # retry 3x, 100 ms apart
            try:
                fcntl.ioctl(self.fd, _hidiocsfeature(len(buf)), buf)
                return
            except OSError as e:
                if e.errno in (errno.ENODEV, errno.ENOENT):
                    raise DeviceError("the mouse went away")
                if attempt == 2:
                    raise DeviceError(f"sending to the mouse failed: {e.strerror}")
                time.sleep(0.1)

    def _drain(self):
        while True:
            try:
                os.read(self.fd, 64)
            except BlockingIOError:
                return
            except OSError:
                return

    def _wait(self, cmd, addr, timeout, required=True):
        end = time.monotonic() + timeout
        while True:
            left = end - time.monotonic()
            if left <= 0:
                if required:
                    raise Timeout(
                        "the mouse didn't answer. A wireless M913 sleeps after a while: "
                        "move it or click, or plug in the cable, then try again.")
                return None
            r, _, _ = select.select([self.fd], [], [], left)
            if not r:
                continue
            try:
                pkt = os.read(self.fd, 64)
            except BlockingIOError:
                continue
            except OSError as e:
                raise DeviceError(f"reading from the mouse failed: {e.strerror}")
            resp = P.parse_response(pkt)
            if self.log:
                self.log("<< " + pkt.hex(" "))
            if resp is None:
                continue
            rcmd, status, raddr, _, data = resp
            if rcmd != cmd or (addr is not None and raddr != addr):
                continue
            if status != 0:
                raise DeviceError(f"the mouse rejected command {cmd:#04x} at {raddr:#06x} "
                                  f"(status {status:#04x})")
            return data

    # -- EEPROM --
    def read(self, addr: int, length: int) -> bytes:
        out = bytearray()
        for pkt in P.read_packets(addr, length):
            a = (pkt[3] << 8) | pkt[4]
            n = pkt[5]
            for attempt in range(2):
                self._drain()
                self.send(pkt)
                try:
                    data = self._wait(P.CMD_READ, a, self.timeout)
                    break
                except Timeout:
                    if attempt:
                        raise
                    # some firmware wants the opening handshake first
                    for p in P.preamble_packets():
                        self.send(p)
                        self._wait(p[1], None, 0.3, required=False)
            if len(data) < n:
                raise DeviceError(f"short read at {a:#06x}")
            out += data[:n]
        return bytes(out)

    def write(self, addr: int, data: bytes, chunk: int = P.MAX_CHUNK):
        for a, d in P.chunks(addr, data, chunk):
            self._drain()
            self.send(P.packet(P.CMD_WRITE, a, d))
            if self.acks is False:
                time.sleep(0.02)
                continue
            got = self._wait(P.CMD_WRITE, a, 0.5 if self.acks is None else self.timeout,
                             required=bool(self.acks))
            if self.acks is None:
                self.acks = got is not None

    def battery(self):
        """0-100 % or None. Only meaningful on the wireless dongle."""
        self._drain()
        self.send(P.packet(P.CMD_BATTERY))
        data = self._wait(P.CMD_BATTERY, None, 0.5, required=False)
        if not data:
            return None
        return min(data[0], 10) * 10
