"""GTK window tests. They need a display, so they only run with
M913_GUI_TESTS=1 (e.g. under gtk4-broadwayd with GDK_BACKEND=broadway)."""

import copy
import os
import tempfile
import time
import tomllib
import unittest
from pathlib import Path
from unittest import mock

RUN = os.environ.get("M913_GUI_TESTS") == "1"
if RUN:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, GLib

    from m913ctl import gui
    from tests.test_m913ctl import FakeHardware, fake_mouse

from m913ctl import protocol as P  # noqa: E402

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "config.toml"


def spin(seconds=0.3, until=None):
    ctx = GLib.MainContext.default()
    end = time.time() + seconds
    while time.time() < end:
        while ctx.pending():
            ctx.iteration(False)
        if until and until():
            return True
        time.sleep(0.01)
    return until() if until else True


@unittest.skipUnless(RUN, "set M913_GUI_TESTS=1 with a display")
class Window(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.tmp.name,
                                                "XDG_CONFIG_HOME": self.tmp.name})
        self.env.start()
        self.cfg = Path(self.tmp.name, "c.toml")
        self.cfg.write_text(EXAMPLE.read_text())
        self.app = Adw.Application(application_id="io.github.m913ctl.test")
        self.app.register(None)
        self.hw = FakeHardware()
        self.hw.mem[0x60:0xA0] = b"".join(P.field(1, 1, 0) for _ in range(16))
        self.fm = fake_mouse(self.hw)
        self.fm.__enter__()
        self.w = gui.Window(self.app, str(self.cfg))
        self.w.present()
        spin()

    def tearDown(self):
        self.w.saved = self.w.raw
        self.w.destroy()
        spin(0.1)
        self.fm.__exit__(None, None, None)
        self.env.stop()
        self.tmp.cleanup()

    def test_led_rate_dpi_edits(self):
        self.w.set_led("mode", "rainbow")
        self.w.set_rate(1)
        self.w.dpi_edit(add=True)
        spin()
        self.w.dpi_edit(0, value=1234)
        spin()
        r = self.w.raw
        self.assertEqual(r["led"]["mode"], "rainbow")
        self.assertEqual(r["report_rate"], 125)
        self.assertEqual(r["dpi"]["levels"], [1200, 1600, 3200, 6400])
        self.assertEqual(len(r["dpi"]["colors"]), 4)
        self.assertIn("•", self.w.get_title())

    def test_colour_dots_and_rainbow(self):
        self.w.set_led("mode", "static")
        spin()
        self.w.set_led("color", "00ffff")
        spin()
        self.assertEqual(self.w.raw["led"]["color"], "00ffff")
        row = self.w.groups["led"]
        found = []

        def walk(wd):
            if "m913-active" in wd.get_css_classes():
                found.append(wd.get_tooltip_text())
            c = wd.get_first_child()
            while c:
                walk(c)
                c = c.get_next_sibling()
        walk(row)
        self.assertEqual(found, ["#00ffff"])
        self.w.set_led("mode", "rainbow")
        spin()
        found.clear()
        walk(self.w.groups["led"])
        self.assertEqual(found, [])  # no colour row in rainbow

    def test_led_not_managed(self):
        self.w.set_led("mode", gui.NOT_MANAGED)
        spin()
        self.assertNotIn("led", self.w.raw)

    def test_bad_change_is_reverted(self):
        before = copy.deepcopy(self.w.raw)
        self.w.raw["buttons"]["button_1"] = "key:nope"
        self.w.changed(before)
        self.assertEqual(self.w.raw["buttons"]["button_1"], "back")

    def test_apply_writes_mouse_and_config(self):
        answers = []

        def present(dlg, parent):
            answers.append(dlg.get_heading())
            GLib.idle_add(lambda: (dlg.emit("response", "write"), False)[1])

        with mock.patch.object(Adw.AlertDialog, "present", present):
            self.w.set_led("brightness", 3)
            spin()
            self.w.apply()
            done = spin(10, until=lambda: not self.w.busy and self.w.raw == self.w.saved)
        self.assertTrue(done)
        self.assertTrue(answers and answers[0].startswith("Write "))
        saved = tomllib.loads(self.cfg.read_text())
        self.assertEqual(saved["led"]["brightness"], 3)
        self.assertEqual(bytes(self.hw.mem[0x5A:0x5C]), P.field(P.BRIGHTNESS[2]))
        self.assertTrue(list(Path(self.tmp.name, "m913ctl").glob("backup-*.json")))

    def test_backup_restore_roundtrip(self):
        import os
        from m913ctl import ops as O
        # start from a known mouse state, back it up, change it, restore it
        self.hw.mem[0x54:0x56] = P.field(0, 255, 0)[:2]  # some LED bytes
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.tmp.name}):
            with gui.D.Mouse(self.w.dev["node"]) as m:
                path = O.make_backup(m, self.w.dev, self.w.sensor)
            self.assertTrue(os.path.exists(path))
            baseline = bytes(self.hw.mem)
            self.hw.mem[0x54] = 0x11  # mutate the mouse
            regions = O.load_backup(path)
            with gui.D.Mouse(self.w.dev["node"]) as m:
                changed, bad = O.restore(m, regions)
            self.assertFalse(bad)
            self.assertEqual(bytes(self.hw.mem), baseline)
            self.assertEqual(len(O.list_backups()), 1)

    def test_read_mouse(self):
        self.hw.mem[0x54:0x5E] = P.field(0, 0, 255) + P.field(1) + P.field(255) + P.field(3)
        self.w.saved = self.w.raw
        self.w.read_mouse()
        spin(5, until=lambda: not self.w.busy)
        self.assertEqual(self.w.raw["led"], {"mode": "static", "color": "0000ff",
                                             "brightness": 10})
        # fake mouse has no command macros, so no [commands] survive the read
        self.assertNotIn("commands", self.w.raw)


if __name__ == "__main__":
    unittest.main()
