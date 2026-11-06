import contextlib
import io
import json
import os
import socket
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from m913ctl import cli
from m913ctl import device as D
from m913ctl import dpi as dpimod
from m913ctl import model as M
from m913ctl import ops as O
from m913ctl import protocol as P

HERE = Path(__file__).parent
EXAMPLE = HERE.parent / "examples" / "config.toml"


def golden_packets():
    """SET_REPORT payloads mouse_m908 sends for examples/example_m913.ini
    (captured with tests/golden_harness, PID fa08)."""
    out = []
    for line in (HERE / "fixtures" / "m908_example_m913.log").read_text().splitlines():
        hdr, _, payload = line.partition(":")
        assert hdr.split() == ["21", "09", "0308", "0001"]
        out.append(bytes.fromhex(payload))
    return out


def golden_mem():
    mem = M.Mem()
    for p in golden_packets():
        mem.put((p[3] << 8) | p[4], p[6:6 + p[5]])
    return mem


def settings(toml_text):
    return M.validate(tomllib.loads(toml_text))


def apply_writes(mem, writes):
    for w in writes:
        mem.put(w.addr, w.data)


class Checksums(unittest.TestCase):
    def test_golden_packet_checksums(self):
        for p in golden_packets():
            self.assertEqual(len(p), 17)
            self.assertEqual(P.chk(p[:16]), p[16], p.hex(" "))

    def test_packet_builder_matches_golden(self):
        for p in golden_packets():
            addr = (p[3] << 8) | p[4]
            self.assertEqual(P.packet(p[1], addr, p[6:6 + p[5]]), p)

    def test_known_read_packets(self):
        # handshake packets as listed in mouse_m908's data tables
        self.assertEqual(P.packet(P.CMD_PREAMBLE).hex(), "0803" + "00" * 14 + "4a")
        self.assertEqual(P.preamble_packets()[1][-1], 0x43)
        self.assertEqual(P.read_packets(0x04, 2)[0][-1], 0x3F)
        self.assertEqual(P.read_packets(0x100, 10)[0][-1], 0x3A)

    def test_field(self):
        self.assertEqual(P.field(0x01, 0x08, 0x00), bytes([1, 8, 0, 0x4C]))
        self.assertEqual(sum(P.field(0xFF, 0xFF, 0xFF)) & 0xFF, 0x55)

    def test_response_parse(self):
        p = bytearray(P.packet(P.CMD_READ, 0x60, bytes(range(10))))
        self.assertEqual(P.parse_response(bytes(p))[2], 0x60)
        p[0] = 0x01  # another report (e.g. mouse movement)
        self.assertIsNone(P.parse_response(bytes(p)))


class Golden(unittest.TestCase):
    """Fields where mouse_m908 is right must come out byte-identical."""

    CFG = """
        report_rate = 500
        [dpi]
        levels = [100, 3000, 4000, 6000, 16000]
        [buttons]
        left = "left"
        right = "right"
        middle = "middle"
        fire = "left"
        button_1 = "back"
        button_2 = "forward"
        button_3 = "led_toggle"
        button_4 = "none"
        button_5 = "none"
        button_6 = "none"
        button_7 = "none"
        button_8 = "none"
        button_9 = "none"
        button_10 = "none"
        button_11 = "none"
        button_12 = "none"
    """

    def test_fields(self):
        g = golden_mem()
        for w in M.encode(settings(self.CFG), 1):
            self.assertEqual(w.data, g.get(w.addr, len(w.data)), w.label)

    def test_dpi_chunking_matches(self):
        w = [w for w in M.encode(settings(self.CFG), 1) if w.label == "dpi levels"][0]
        pk = P.write_packets(w.addr, w.data, w.chunk)
        self.assertEqual(pk, [p for p in golden_packets() if p[4] in (0x0C, 0x14, 0x1C)])

    def test_led_mode_and_speed(self):
        g = golden_mem()
        self.assertEqual(g.get(0x58, 2), P.field(3))      # rainbow
        self.assertEqual(g.get(0x5C, 2), P.field(3))      # speed 3
        led = M.encode(settings('[led]\nmode="neon"\ncolor="ff00ff"'), 1)
        self.assertEqual(led[0].data[:6], g.get(0x54, 6))
        self.assertEqual(led[1].data, g.get(0x5C, 2))


class Dpi(unittest.TestCase):
    def test_tables(self):
        t = dpimod.table(1)
        self.assertEqual(len(t), 160)
        self.assertEqual(t[16000], (189, 0))
        # differs from mouse_m908's 73
        self.assertEqual(t[6100], (72, 0))
        t = dpimod.table(0)
        self.assertEqual(t[5000], (115, 0))
        self.assertEqual(t[10000], (115, 1))
        self.assertEqual(t[6000], (69, 1))

    def test_unknown(self):
        with self.assertRaises(dpimod.DpiError):
            dpimod.encode(0, 12000)
        with self.assertRaises(dpimod.DpiError):
            dpimod.table(2)

    def test_decode_roundtrip(self):
        for s in (0, 1):
            for d, (c, f) in dpimod.table(s).items():
                self.assertEqual(dpimod.decode(s, c, f), d)

    def test_needs_sensor(self):
        with self.assertRaises(M.ConfigError):
            M.encode(settings("[dpi]\nlevels=[800]"), None)

    def test_xy_flag_mismatch(self):
        with self.assertRaises(M.ConfigError):
            M.encode(settings("[dpi]\nlevels=[[8000, 800]]"), 0)


class Actions(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(M.parse_action("back"), (1, 8, 0))
        self.assertEqual(M.parse_action("fire:2:50"), (4, 50, 2))
        self.assertEqual(M.parse_action("raw:0b ad 00"), (0x0B, 0xAD, 0))

    def test_combo_order(self):
        a = M.parse_action("combo:ctrl+shift+t")
        self.assertEqual(a.events, [(0x80, 1, 0), (0x80, 2, 0), (0x81, 0x17, 0),
                                    (0x40, 1, 0), (0x40, 2, 0), (0x41, 0x17, 0)])

    def test_media_is_16bit_le(self):
        a = M.parse_action("media:www_home")
        self.assertEqual(a.events, [(0x82, 0x23, 0x02), (0x42, 0x23, 0x02)])

    def test_keyslot_bytes(self):
        # same layout as mouse_m908's keyboard-key template
        b = M.keyslot_bytes([(0x81, 0x21, 0), (0x41, 0x21, 0)])
        self.assertEqual(b, bytes([2, 0x81, 0x21, 0, 0x41, 0x21, 0, 0x4F]))

    def test_errors(self):
        for bad in ("key:nope", "fire:9:9", "raw:05 00 00", "media:a", "combo:a+play_pause",
                    "launch_rockets"):
            with self.assertRaises(M.ConfigError, msg=bad):
                M.parse_action(bad)

    def test_too_many_combo_events(self):
        with self.assertRaises(M.ConfigError):
            settings('[buttons]\nbutton_1="combo:ctrl+shift+alt+super+a+b"')


class Macros(unittest.TestCase):
    def test_bytes_and_checksum(self):
        rb, ev = M.parse_macro("m", {"events": ["tap a"], "delay": 10})
        self.assertEqual(rb, 1)
        b = M.macro_bytes(ev)
        self.assertEqual(len(b), 0x20 + 10 + 4)
        self.assertEqual(b[0x1F], 2)
        self.assertEqual(b[0x20:0x2A], bytes([0x81, 4, 0, 0, 10, 0x41, 4, 0, 0, 10]))
        self.assertEqual(b[0x2A], P.chk(b[:0x2A]))
        self.assertEqual(b[-3:], b"\0\0\0")

    def test_delay_attaches_to_previous(self):
        _, ev = M.parse_macro("m", {"events": ["down a", "delay 500", "up a"]})
        self.assertEqual(ev, [(0x81, 4, 500), (0x41, 4, 20)])

    def test_type(self):
        _, ev = M.parse_macro("m", {"events": ["type Hi!"], "delay": 5})
        self.assertEqual([e[:2] for e in ev], [
            (0x80, 2), (0x81, 0x0B), (0x41, 0x0B), (0x40, 2),
            (0x81, 0x0C), (0x41, 0x0C),
            (0x80, 2), (0x81, 0x1E), (0x41, 0x1E), (0x40, 2)])

    def test_repeat(self):
        self.assertEqual(M.parse_macro("m", {"events": ["tap a"], "repeat": "hold"})[0], 0xFF)
        self.assertEqual(M.parse_macro("m", {"events": ["tap a"], "repeat": "until_key"})[0],
                         0xFE)

    def test_rejects(self):
        cases = [
            {"events": ["down a"]},                      # stuck key
            {"events": ["delay 10", "tap a"]},           # leading delay
            {"events": ["tap a", "delay 1"]},            # below minimum
            {"events": ["tap play_pause"]},              # media in macro
            {"events": ["tap a"] * 40},                  # 80 events
            {"events": ["tap a"], "repeat": 0},
            {"events": ["wiggle"]},
            {"events": ["tap a"], "speed": 3},
        ]
        for c in cases:
            with self.assertRaises(M.ConfigError, msg=c):
                M.parse_macro("m", c)

    def test_undefined_macro(self):
        with self.assertRaises(M.ConfigError):
            settings('[buttons]\nbutton_1="macro:nope"')


class Config(unittest.TestCase):
    def test_example_validates(self):
        s = O.settings_from_raw(tomllib.loads(EXAMPLE.read_text()))[0]
        M.encode(s, 1)
        M.encode(s, 0)

    def test_rejects(self):
        for bad in ('report_rate = 333', 'colour = 1', '[buttons]\nbutton_13 = "left"',
                    '[led]\nmode = "disco"', '[led]\nbrightness = 0',
                    '[dpi]\nlevels = [1,2,3,4,5,6]', '[dpi]\ncolors = ["red"]'):
            with self.assertRaises(M.ConfigError, msg=bad):
                settings(bad)

    def test_neon_is_an_alias_for_rainbow(self):
        self.assertEqual(settings('[led]\nmode = "neon"')["led"]["mode"], "rainbow")

    def test_led_off_only_touches_mode(self):
        w = M.encode(settings('[led]\nmode = "off"'), 1)
        self.assertEqual([(x.addr, x.data) for x in w], [(0x58, bytes([0, 0x55]))])

    def test_led_brightness_table(self):
        w = M.encode(settings('[led]\nmode="static"\ncolor="102030"\nbrightness=5'), 1)
        self.assertEqual(w[0].data, P.field(0x10, 0x20, 0x30) + P.field(1) + P.field(128))
        self.assertEqual(len(w), 1)  # static: no speed write

    def _blank_mem(self):
        mem = M.Mem()
        mem.put(0, bytes(0x2000))
        # every button record a valid "disabled" so unmanaged ones decode cleanly
        for i in range(P.N_BUTTONS):
            mem.put(P.ADDR_BUTTONS + 4 * i, P.field(0, 0, 0))
        return mem

    def test_roundtrip_dump(self):
        s = O.settings_from_raw(tomllib.loads(EXAMPLE.read_text()))[0]
        writes = M.encode(s, 1)
        mem = self._blank_mem()
        apply_writes(mem, writes)
        macros = {i: mem.get(P.ADDR_MACRO + P.MACRO_SIZE * i, P.MACRO_SIZE - 0x40)
                  for i in M.macro_slots_used(mem)}
        cfg, notes = M.decode(mem, 1, macros)
        self.assertEqual(notes, [])
        text = M.to_toml(cfg)
        s2 = M.validate(tomllib.loads(text))
        w2 = M.encode(s2, 1)
        mem2 = self._blank_mem()
        apply_writes(mem2, w2)
        # same bytes on the mouse, even though macro names differ
        for addr in list(range(0, 0xA0)) + list(range(0x100, 0x300)):
            self.assertEqual(mem.b.get(addr), mem2.b.get(addr), hex(addr))
        for i in macros:
            a = P.ADDR_MACRO + P.MACRO_SIZE * i
            self.assertEqual(mem.get(a, 0x20 + 5 * mem.b[a + 0x1F] + 4),
                             mem2.get(a, 0x20 + 5 * mem2.b[a + 0x1F] + 4))


class HidDescriptor(unittest.TestCase):
    def test_report_id(self):
        # Usage Page (vendor), Usage, Collection, Report ID 8, ...
        desc = bytes([0x06, 0x00, 0xFF, 0x09, 0x01, 0xA1, 0x01, 0x85, 0x08, 0x75, 0x08, 0xC0])
        self.assertTrue(D._has_report_id_8(desc))
        self.assertFalse(D._has_report_id_8(bytes([0x85, 0x01, 0x05, 0x08])))
        # a 0x85 byte inside another item's data must not count
        self.assertFalse(D._has_report_id_8(bytes([0x06, 0x85, 0x08])))

    def test_find(self):
        with tempfile.TemporaryDirectory() as t:
            def mk(n, pid, iface, desc):
                usb = Path(t, "devices", f"usb{n}")
                hid = usb / f"1-{n}:1.{iface}" / f"0003:25A7:{pid:04X}.000{n}"
                hid.mkdir(parents=True)
                (usb / "bcdDevice").write_text("1100\n")
                (hid / "uevent").write_text(f"HID_ID=0003:000025A7:0000{pid:04X}\n")
                (hid / "report_descriptor").write_bytes(desc)
                cls = Path(t, "class", f"hidraw{n}")
                cls.mkdir(parents=True)
                os.symlink(hid, cls / "device")
            mk(0, 0xFA07, 0, bytes([0x85, 0x01]))
            mk(1, 0xFA07, 1, bytes([0x85, 0x08]))
            mk(2, 0xFA08, 1, bytes([0x85, 0x08]))
            found = D.find(os.path.join(t, "class"))
            self.assertEqual([f["node"] for f in found], ["/dev/hidraw2", "/dev/hidraw1"])
            self.assertEqual(found[0]["bcd"], 0x1100)
            self.assertEqual(D.sensor_from_bcd(found[0]["bcd"]), 1)


class FakeHardware:
    """Answers feature reports the way the mouse does, over a socketpair."""

    def __init__(self, ack_writes=True):
        self.mem = bytearray(b"\xff" * 0x2000)
        self.mem[0:0xA0] = bytes(0xA0)
        self.ack = ack_writes
        self.ours, self.theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self.ours.setblocking(False)
        self.sent = []

    def ioctl(self, fd, req, buf):
        p = bytes(buf)
        self.sent.append(p)
        assert len(p) == 17 and P.chk(p[:16]) == p[16]
        cmd, addr, n = p[1], (p[3] << 8) | p[4], p[5]
        resp = bytearray(p)
        resp[0] = 0x09  # real M913 (cable) answers with input report 9
        if cmd == P.CMD_WRITE:
            self.mem[addr:addr + n] = p[6:6 + n]
            if not self.ack:
                return 0
        elif cmd == P.CMD_READ:
            resp[6:6 + n] = self.mem[addr:addr + n]
        elif cmd == P.CMD_BATTERY:
            resp[6] = 7
        resp[16] = P.chk(resp[:16])
        self.theirs.send(bytes(resp))
        return 0


@contextlib.contextmanager
def fake_mouse(hw):
    real_open = os.open

    def fake_open(path, flags, *a, **kw):
        if path == "/dev/hidraw9":
            return os.dup(hw.ours.fileno())
        return real_open(path, flags, *a, **kw)

    dev = {"node": "/dev/hidraw9", "pid": P.PID_WIRELESS, "iface": "1", "bcd": 0x1000}
    with mock.patch.object(D.os, "open", fake_open), \
            mock.patch.object(D.fcntl, "ioctl", hw.ioctl), \
            mock.patch.object(D, "find", lambda *a: [dev]):
        try:
            yield
        finally:
            hw.ours.close()
            hw.theirs.close()


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = cli.main(list(argv))
        except SystemExit as e:
            rc = e.code
    return rc, out.getvalue(), err.getvalue()


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.tmp.name,
                                                "XDG_CONFIG_HOME": self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def cfg(self, text):
        p = Path(self.tmp.name, "c.toml")
        p.write_text(text)
        return str(p)

    def test_apply_dump_restore(self):
        hw = FakeHardware()
        hw.mem[0x60:0xA0] = b"".join(P.field(1, 1, 0) for _ in range(16))
        before = bytes(hw.mem)
        with fake_mouse(hw):
            rc, out, err = run("apply", "--yes", str(EXAMPLE))
            self.assertEqual(rc, 0, err)
            self.assertIn("written and verified", out)
            backups = list(Path(self.tmp.name, "m913ctl").glob("backup-*.json"))
            self.assertEqual(len(backups), 1)
            # applying again is a no-op
            n = len(hw.sent)
            rc, out, _ = run("apply", "--yes", str(EXAMPLE))
            self.assertIn("nothing to write", out)
            self.assertFalse([p for p in hw.sent[n:] if p[1] == P.CMD_WRITE])
            rc, out, err = run("dump")
            self.assertEqual(rc, 0, err)
            dumped = tomllib.loads(out)
            self.assertEqual(dumped["buttons"]["button_3"], "combo:ctrl+c")
            self.assertEqual(dumped["buttons"]["button_7"], "media:play_pause")
            self.assertEqual(dumped["dpi"]["levels"], [800, 1600, 3200])
            self.assertEqual(dumped["led"]["mode"], "breathing")
            rc, out, err = run("restore", "--yes", str(backups[0]))
            self.assertEqual(rc, 0, err)
        self.assertEqual(bytes(hw.mem), before)

    def test_writes_go_out_in_expected_chunks(self):
        hw = FakeHardware()
        hw.mem[0x60:0xA0] = b"".join(P.field(1, 1, 0) for _ in range(16))
        with fake_mouse(hw):
            run("apply", "--yes", "--no-backup", self.cfg("[dpi]\nlevels=[800,1600,3200]"))
        writes = [p for p in hw.sent if p[1] == P.CMD_WRITE]
        self.assertEqual([(p[4], p[5]) for p in writes], [(0x02, 2), (0x0C, 8), (0x14, 4)])
        self.assertEqual(hw.sent[0][:7], bytes([8, 2, 0, 0, 0, 1, 1]))   # driver open
        self.assertEqual(hw.sent[-1][:7], bytes([8, 2, 0, 0, 0, 1, 0]))  # driver close

    def test_packet_layout(self):
        hw = FakeHardware()
        hw.mem[0x60:0xA0] = b"".join(P.field(1, 1, 0) for _ in range(16))
        hw.mem[0x100:0x300] = bytes(0x200)
        with fake_mouse(hw):
            rc, _, err = run("apply", "--yes", str(EXAMPLE))
            self.assertEqual(rc, 0, err)
            backup = next(Path(self.tmp.name, "m913ctl").glob("backup-*.json"))
            n = len(hw.sent)
            rc, _, err = run("restore", "--yes", str(backup))
            self.assertEqual(rc, 0, err)
        for p in [p for p in hw.sent if p[1] == P.CMD_WRITE]:
            a, n_ = (p[3] << 8) | p[4], p[5]
            if 0x60 <= a < 0xA0:  # button matrix: 8-byte packets on 8-byte bounds
                self.assertEqual((a % 8, n_), (0, 8), p.hex(" "))
            if 0x100 <= a < 0x300:  # key slots: never across a slot boundary
                self.assertEqual(a // 0x20, (a + n_ - 1) // 0x20, p.hex(" "))
            if a >= 0x300:
                base = 0x300 + (a - 0x300) // 0x180 * 0x180
                self.assertEqual((a - base) % 10, 0, p.hex(" "))
        # restore never writes the active DPI index
        self.assertFalse([p for p in hw.sent[n:] if p[1] == P.CMD_WRITE
                          and ((p[3] << 8) | p[4]) <= 4 < ((p[3] << 8) | p[4]) + p[5]])

    def test_dry_run_writes_nothing(self):
        hw = FakeHardware()
        hw.mem[0x60:0xA0] = b"".join(P.field(1, 1, 0) for _ in range(16))
        with fake_mouse(hw):
            rc, out, _ = run("apply", "-n", str(EXAMPLE))
        self.assertEqual(rc, 0)
        self.assertIn("dry run", out)
        self.assertFalse([p for p in hw.sent if p[1] == P.CMD_WRITE])

    def test_refuses_losing_left_click(self):
        hw = FakeHardware()
        hw.mem[0x60:0xA0] = b"".join(P.field(0, 0, 0) for _ in range(16))
        hw.mem[0x7C:0x80] = P.field(1, 1, 0)
        with fake_mouse(hw):
            rc, _, err = run("apply", "--yes", self.cfg('[buttons]\nleft = "back"'))
        self.assertNotEqual(rc, 0)
        self.assertIn("left click", err)
        self.assertFalse([p for p in hw.sent if p[1] == P.CMD_WRITE])

    def test_works_without_write_acks(self):
        hw = FakeHardware(ack_writes=False)
        hw.mem[0x60:0xA0] = b"".join(P.field(1, 1, 0) for _ in range(16))
        with fake_mouse(hw):
            rc, out, err = run("apply", "--yes", "--no-backup", self.cfg("report_rate = 250"))
        self.assertEqual(rc, 0, err)
        self.assertEqual(hw.mem[0:2], P.field(4))

    def test_info_battery(self):
        hw = FakeHardware()
        hw.mem[0:2] = P.field(1)
        hw.mem[2:6] = P.field(3) + bytes([1, 0x54])
        with fake_mouse(hw):
            rc, out, _ = run("info")
        self.assertEqual(rc, 0)
        self.assertIn("1000 Hz", out)
        self.assertIn("battery  70 %", out)
        self.assertIn("PMW3335", out)

    def test_timeout_message(self):
        hw = FakeHardware()
        hw.ioctl = lambda fd, req, buf: 0  # mouse asleep: nothing comes back
        with fake_mouse(hw), mock.patch.object(D.Mouse, "__init__",
                                               lambda s, n, timeout=1.0, log=None: (
                                                   setattr(s, "node", n),
                                                   setattr(s, "timeout", 0.05),
                                                   setattr(s, "log", None),
                                                   setattr(s, "fd", None),
                                                   setattr(s, "acks", None))[0]):
            rc, _, err = run("dump")
        self.assertNotEqual(rc, 0)
        self.assertIn("sleeps", err)

    def test_backup_is_json(self):
        hw = FakeHardware()
        with fake_mouse(hw):
            out_path = Path(self.tmp.name, "b.json")
            rc, _, err = run("backup", "-o", str(out_path))
        self.assertEqual(rc, 0, err)
        b = json.loads(out_path.read_text())
        self.assertEqual(b["format"], "m913ctl-backup")
        self.assertIn("0x0300", b["regions"])


if __name__ == "__main__":
    unittest.main()
