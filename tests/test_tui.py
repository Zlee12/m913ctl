import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

from m913ctl import model as M
from m913ctl import ops as O
from m913ctl import scripts as S
from m913ctl import tui

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "examples" / "config.toml"

try:
    import pyte  # noqa: F401
    from tests.ptyshot import Term
except ImportError:  # pragma: no cover
    Term = None


class Helpers(unittest.TestCase):
    def test_set_button_moves_between_tables(self):
        raw = {"buttons": {"button_1": "back"}}
        tui.set_button(raw, "button_1", "command", "preset:lock")
        self.assertEqual(raw, {"commands": {"button_1": "preset:lock"}})
        tui.set_button(raw, "button_1", "combo", "ctrl+c")
        self.assertEqual(raw, {"buttons": {"button_1": "combo:ctrl+c"}})
        tui.set_button(raw, "button_1", "keep", "")
        self.assertEqual(raw, {})

    def test_describe(self):
        raw = {"buttons": {"left": "left", "button_2": "key:a"},
               "commands": {"button_1": "preset:lock"}}
        self.assertEqual(tui.describe(raw, "button_1")[0], "command")
        self.assertEqual(tui.describe(raw, "button_2"), ("key", "key:a"))
        self.assertEqual(tui.describe(raw, "left"), ("mouse", "left"))
        self.assertEqual(tui.describe(raw, "fire")[0], "keep")

    def test_macro_text_roundtrip(self):
        spec = {"repeat": "hold", "delay": 30, "events": ["tap a", "delay 50", "type hi"]}
        self.assertEqual(tui.text_to_macro("m", tui.macro_to_text("m", spec)), spec)
        with self.assertRaises(M.ConfigError):
            tui.text_to_macro("m", "down a\n")

    def test_merge_keeps_commands_still_on_the_mouse(self):
        raw = {"commands": {"button_1": "echo hi", "button_2": "false"}}
        mouse = {"buttons": {"button_1": f"macro:{S.macro_name('button_1')}", "button_2": "back",
                             "left": "left"},
                 "macros": {S.macro_name("button_1"): {"events": ["type echo hi", "tap enter"]}}}
        new = tui.merge_from_mouse(raw, mouse)
        self.assertEqual(new["commands"], {"button_1": "echo hi"})
        self.assertNotIn("button_1", new["buttons"])
        self.assertNotIn("macros", new)  # the typed-command macro isn't shown as a macro
        self.assertEqual(new["buttons"]["button_2"], "back")


class Commands(unittest.TestCase):
    def test_split_makes_a_typed_macro(self):
        raw = {"commands": {"button_1": "preset:system_update", "button_12": "echo hi"},
               "buttons": {"left": "left"}, "hypr": {"format": "lua"}}
        mouse, info = S.split(raw)
        self.assertEqual(mouse["buttons"]["button_1"], "macro:cmd1")
        self.assertEqual(mouse["buttons"]["button_12"], "macro:cmd12")
        self.assertEqual(mouse["macros"]["cmd1"]["events"],
                         ["type sudo pacman -Syu", "tap enter"])
        self.assertEqual(info["button_12"], "echo hi")
        self.assertNotIn("commands", mouse)
        self.assertNotIn("hypr", mouse)  # old 0.2 section is ignored
        # the whole thing encodes to mouse writes
        M.encode(M.validate(mouse), 1)

    def test_scripts_is_accepted_as_an_alias(self):
        mouse, info = S.split({"scripts": {"button_3": "echo hi"}})
        self.assertEqual(mouse["buttons"]["button_3"], "macro:cmd3")

    def test_split_errors(self):
        for raw in ({"commands": {"left": "x"}}, {"commands": {"button_1": ""}},
                    {"commands": {"button_1": "x"}, "buttons": {"button_1": "back"}},
                    {"commands": {"button_1": "preset:nope"}},
                    {"commands": {"button_1": "a\nb"}},
                    {"commands": {"button_1": "cmd"}, "macros": {"cmd1": {"events": ["tap a"]}}}):
            with self.assertRaises(M.ConfigError, msg=raw):
                S.split(raw)

    def test_too_long_command_is_refused(self):
        with self.assertRaises(M.ConfigError) as cm:
            S.split({"commands": {"button_1": "x" * 40}})
        self.assertIn("too long", str(cm.exception))

    def test_presets_expand(self):
        self.assertEqual(S.expand_command("preset:system_update"), "sudo pacman -Syu")

    def test_example_commands_section(self):
        raw = tomllib.loads(EXAMPLE.read_text())
        s, info = O.settings_from_raw(raw)
        M.encode(s, 1)
        self.assertTrue(info)

    def test_save_raw_roundtrip(self):
        raw = tomllib.loads(EXAMPLE.read_text())
        raw["commands"]["button_1"] = 'echo "q" x'
        with tempfile.TemporaryDirectory() as t:
            p = Path(t, "sub", "c.toml")
            O.save_raw(p, raw)
            self.assertEqual(tomllib.loads(p.read_text()), raw)


@unittest.skipIf(Term is None, "pyte not installed")
class Screen(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Path(self.tmp.name, "c.toml")
        self.cfg.write_text(EXAMPLE.read_text())
        self.ed = Path(self.tmp.name, "ed.sh")
        self.ed.write_text("#!/bin/sh\n"
                           "printf 'repeat = 2\\ndelay = 9\\ntap a\\ntap b\\n' > \"$1\"\n")
        self.ed.chmod(0o755)
        env = {"EDITOR": str(self.ed), "VISUAL": str(self.ed),
               "XDG_CONFIG_HOME": self.tmp.name, "XDG_STATE_HOME": self.tmp.name,
               "PYTHONPATH": str(ROOT)}
        self.t = Term([sys.executable, "-m", "m913ctl", "--sensor", "3335", "tui", str(self.cfg)],
                      env=env)

    def tearDown(self):
        self.t.close()
        self.tmp.cleanup()

    def saved(self):
        return tomllib.loads(self.cfg.read_text())

    def test_buttons_page(self):
        txt = self.t.text()
        self.assertIn("1 Buttons", txt)
        self.assertIn("button_12", txt)
        self.assertIn("preset:system_update", self.cfg.read_text())

    def test_assign_command_and_save(self):
        # button_1 is the 5th row
        self.t.send("<down>", "<down>", "<down>", "<down>", "<enter>")
        self.assertIn("what should it do", self.t.text())
        self.t.send("<down>", "<enter>")  # Type a command
        self.assertIn("command to type", self.t.text())
        self.t.send("<enter>")  # first preset (system_update)
        self.t.send("s")
        cfg = self.saved()
        self.assertEqual(cfg["commands"]["button_1"], "preset:system_update")
        self.assertNotIn("button_1", cfg.get("buttons", {}))

    def test_bad_key_is_refused(self):
        self.t.send("<down>", "<down>", "<down>", "<down>", "<enter>")
        self.t.send("<down>", "<down>", "<enter>")  # Single key
        self.t.send("n", "o", "p", "e", "<enter>")
        self.assertIn("Not changed", self.t.text())
        self.t.send("<enter>", "s")
        self.assertEqual(self.saved()["buttons"]["button_1"], "back")

    def test_macro_via_editor(self):
        self.t.send("2", "n")
        self.t.send(*"spam", "<enter>", wait=0.6)
        self.t.send("s")
        self.assertEqual(self.saved()["macros"]["spam"],
                         {"repeat": 2, "delay": 9, "events": ["tap a", "tap b"]})

    def test_lighting(self):
        self.t.send("3")
        self.assertIn("breathing", self.t.text())
        self.t.send("<right>")  # breathing -> rainbow (no colour row)
        self.t.send("<down>", "<left>", "<left>")  # brightness 6 -> 4
        self.t.send("s")
        led = self.saved()["led"]
        self.assertEqual((led["mode"], led["brightness"]), ("rainbow", 4))

    def test_dpi(self):
        self.t.send("4")
        self.assertIn("1000 Hz", self.t.text())
        self.t.send("<down>", "<right>", "<right>")  # 800 -> 1000
        self.t.send("n")  # add a level
        self.t.send("s")
        d = self.saved()["dpi"]
        self.assertEqual(d["levels"][0], 1000)
        self.assertEqual(len(d["levels"]), 4)
        self.assertEqual(len(d["colors"]), 4)

    def test_quit_asks_when_dirty(self):
        self.t.send("3", "<right>", "q")
        self.assertIn("Quit without saving?", self.t.text())


if __name__ == "__main__":
    unittest.main()


class Picture(unittest.TestCase):
    def test_hit_testing(self):
        from m913ctl import mousepic as MP
        for name, sh in MP.SHAPES.items():
            self.assertEqual(MP.button_at(*sh["label"]), name)
        for name, (x, y, w, h, _) in MP.CELLS.items():
            self.assertEqual(MP.button_at(x + w / 2, y + h / 2), name)
        self.assertIsNone(MP.button_at(5, 5))
        self.assertEqual(len(MP.SHAPES), 16)
        # the fire key sits on top of the left click
        x, y, w, h = MP.bbox(MP.SHAPES["fire"]["poly"])
        self.assertEqual(MP.button_at(x + w - 2, y + h / 2), "fire")
        s, ox, oy = MP.fit(MP.WIDTH * 2, MP.HEIGHT * 2 + 100)
        x, y, w, h = MP.bbox(MP.SHAPES["button_12"]["poly"])
        self.assertEqual(MP.button_at(ox + (x + 1) * s, oy + (y + 1) * s, s, ox, oy), "button_12")

    def test_short(self):
        from m913ctl import mousepic as MP
        self.assertEqual(MP.short("combo:ctrl+c"), "ctrl+c")
        self.assertEqual(MP.short("screenshot — region screenshot to clipboard"), "screenshot")
        self.assertEqual(MP.short("my-long-command — x", 10), "my-long-c…")
