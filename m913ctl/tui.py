"""Terminal UI: `m913ctl tui`.

Edits the same config file as the command line (default
~/.config/m913ctl/config.toml) and writes it to the mouse on `a`.
Standard library only (curses).
"""

import copy
import curses
import os
import shlex
import subprocess
import tempfile

from . import device as D
from . import dpi as dpimod
from . import keys as K
from . import model as M
from . import ops as O
from . import protocol as P
from . import scripts as S
from .edit import (BUTTON_ORDER, COLORS, KINDS, SIMPLE_CHOICES, check, describe,  # noqa: F401
                   macro_to_text, merge_from_mouse, set_button, text_to_macro)

TABS = ["Buttons", "Macros", "Lighting", "DPI & rate"]


# -- curses UI ---------------------------------------------------------------------

class UI:
    def __init__(self, scr, path, device=None, sensor=None):
        self.scr = scr
        self.path = path
        self.device = device
        self.sensor_override = sensor
        self.raw = O.load_raw(path)
        self.saved = copy.deepcopy(self.raw)
        self.tab = 0
        self.sel = [0, 0, 0, 0]
        self.status = ""
        self.dev = None
        self.sensor = sensor
        self._detect()
        curses.curs_set(0)
        curses.use_default_colors()
        self.col = {}
        if curses.has_colors():
            for i, (name, fg) in enumerate([("accent", curses.COLOR_CYAN),
                                            ("warn", curses.COLOR_YELLOW),
                                            ("ok", curses.COLOR_GREEN),
                                            ("dim", curses.COLOR_BLUE)], start=1):
                curses.init_pair(i, fg, -1)
                self.col[name] = curses.color_pair(i)
        self.swatch_ok = curses.has_colors() and curses.can_change_color() and curses.COLORS >= 16

    def c(self, name):
        return self.col.get(name, 0)

    def _detect(self):
        try:
            self.dev, s = O.find_device(self.device, self.sensor_override)
            self.sensor = s
        except O.OpsError:
            self.dev = None

    # -- drawing --
    def put(self, y, x, text, attr=0):
        h, w = self.scr.getmaxyx()
        if 0 <= y < h and x < w:
            try:
                self.scr.addnstr(y, x, text, max(0, w - x - 1), attr)
            except curses.error:
                pass

    def draw(self):
        self.scr.erase()
        h, w = self.scr.getmaxyx()
        if self.dev:
            kind = "wired" if self.dev.get("pid") == P.PID_WIRED else "wireless"
            dev = f"{self.dev['node']} {kind} · {dpimod.SENSORS.get(self.sensor, 'sensor ?')}"
        else:
            dev = "no mouse found"
        dirty = " *" if self.raw != self.saved else ""
        self.put(0, 1, "m913ctl", curses.A_BOLD | self.c("accent"))
        shown = str(self.path).replace(os.path.expanduser("~"), "~", 1)
        self.put(0, 10, f"{dev}  ·  {shown}{dirty}")
        x = 1
        for i, t in enumerate(TABS):
            label = f" {i + 1} {t} "
            self.put(1, x, label, curses.A_REVERSE if i == self.tab else curses.A_BOLD)
            x += len(label) + 1
        body = [self.draw_buttons, self.draw_macros, self.draw_led, self.draw_dpi][self.tab]
        body(3, h - 3)
        err = check(self.raw, self.sensor)
        if err:
            self.put(h - 2, 1, "! " + err, self.c("warn") | curses.A_BOLD)
        elif self.status:
            self.put(h - 2, 1, self.status, self.c("ok"))
        help_ = {
            0: "↑↓ select  Enter edit  Del unmanage",
            1: "↑↓ select  Enter edit  n new  Del delete",
            2: "↑↓ select  ←→ change  Enter type a value",
            3: "↑↓ select  ←→ change  Enter type  n add level  Del remove level",
        }[self.tab]
        self.put(h - 1, 1, f"{help_}   │ Tab/1-4 pages  s save  a apply  r read mouse  q quit",
                 self.c("dim"))
        self.scr.refresh()

    def rows_header(self, y, cols):
        self.put(y, 1, cols, curses.A_UNDERLINE)

    def draw_list(self, y, height, rows, sel):
        top = max(0, sel - height + 1)
        for i, row in enumerate(rows[top:top + height]):
            idx = top + i
            attr = curses.A_REVERSE if idx == sel else 0
            self.put(y + i, 1, row.ljust(self.scr.getmaxyx()[1] - 3), attr)

    def draw_buttons(self, y, h):
        self.rows_header(y, f"{'button':<11} {'key':<5} {'kind':<7} action")
        rows = []
        for b in BUTTON_ORDER:
            kind, text = describe(self.raw, b)
            fkey = "type" if kind == "command" else ""
            rows.append(f"{b:<11} {fkey:<5} {kind:<7} {text}")
        self.draw_list(y + 1, h - y - 1, rows, self.sel[0])

    def macro_names(self):
        return list(self.raw.get("macros", {}))

    def draw_macros(self, y, h):
        names = self.macro_names()
        self.rows_header(y, f"{'macro':<16} {'repeat':<10} {'used by':<22} events")
        if not names:
            self.put(y + 2, 3, "No macros yet. Press n to make one.", self.c("dim"))
            return
        rows = []
        for n in names:
            spec = self.raw["macros"][n]
            used = [b for b, a in self.raw.get("buttons", {}).items() if a == f"macro:{n}"]
            ev = " · ".join(spec.get("events", []))
            rep = str(spec.get("repeat", 1))
            rows.append(f"{n:<16} {rep:<10} {', '.join(used) or '-':<22} {ev}")
        self.sel[1] = min(self.sel[1], len(rows) - 1)
        self.draw_list(y + 1, h - y - 1, rows, self.sel[1])

    def led(self):
        return self.raw.get("led", {})

    def led_rows(self):
        led = self.led()
        mode = led.get("mode", "(not managed)")
        rows = [("mode", mode)]
        mode = P.LED_MODE_ALIASES.get(mode, mode)
        if led and mode != "off":
            if mode != "rainbow":
                rows.append(("colour", led.get("color", "ff0000")))
            rows.append(("brightness", f"{led.get('brightness', 10)} / 10"))
            if mode in ("breathing", "rainbow"):
                rows.append(("speed", f"{led.get('speed', 3)}"))
        return rows

    def draw_led(self, y, h):
        rows = self.led_rows()
        self.sel[2] = min(self.sel[2], len(rows) - 1)
        self.draw_list(y + 1, h - y - 1, [f"{k:<12} {v}" for k, v in rows], self.sel[2])
        led = self.led()
        if self.swatch_ok and led.get("color") and led.get("mode", "off") != "off":
            r, g, b = (int(led["color"][i:i + 2], 16) for i in (0, 2, 4))
            try:
                curses.init_color(15, r * 1000 // 255, g * 1000 // 255, b * 1000 // 255)
                curses.init_pair(9, 15, 15)
                self.put(y + 2, 26, "      ", curses.color_pair(9))
            except curses.error:
                pass
        self.put(y + len(rows) + 2, 3, "Rainbow cycles through the colours by itself.",
                 self.c("dim"))

    def dpi_rows(self):
        rows = [("report rate", f"{self.raw['report_rate']} Hz" if "report_rate" in self.raw
                 else "(not managed)")]
        d = self.raw.get("dpi", {})
        levels = d.get("levels", [])
        colors = d.get("colors", [])
        for i, lv in enumerate(levels):
            c = colors[i] if i < len(colors) else "-"
            v = f"{lv[0]} x {lv[1]}" if isinstance(lv, list) else str(lv)
            rows.append((f"level {i + 1}", f"{v:<14} colour {c}"))
        return rows

    def draw_dpi(self, y, h):
        rows = self.dpi_rows()
        self.sel[3] = min(self.sel[3], len(rows) - 1)
        self.draw_list(y + 1, h - y - 1, [f"{k:<12} {v}" for k, v in rows], self.sel[3])
        if not self.raw.get("dpi"):
            self.put(y + len(rows) + 2, 3, "No DPI levels managed. Press n to add one.",
                     self.c("dim"))

    # -- dialogs --
    def prompt(self, title, initial=""):
        """One-line editor. Returns the text or None on Esc."""
        h, w = self.scr.getmaxyx()
        buf = list(initial)
        pos = len(buf)
        curses.curs_set(1)
        try:
            while True:
                self.draw()
                y = h // 2
                width = w - 4
                self.put(y - 1, 2, f" {title} ".ljust(width), curses.A_REVERSE | curses.A_BOLD)
                start = max(0, pos - width + 2)
                text = "".join(buf)[start:start + width - 1]
                self.put(y, 2, text.ljust(width), curses.A_REVERSE)
                self.put(y + 1, 2, " Enter ok · Esc cancel ".ljust(width), curses.A_REVERSE)
                self.scr.move(y, 2 + pos - start)
                ch = self.scr.get_wch()
                if ch in ("\n", "\r", curses.KEY_ENTER):
                    return "".join(buf)
                if ch == "\x1b":
                    return None
                if ch in (curses.KEY_BACKSPACE, "\x7f", "\b"):
                    if pos:
                        pos -= 1
                        del buf[pos]
                elif ch == curses.KEY_DC:
                    if pos < len(buf):
                        del buf[pos]
                elif ch == curses.KEY_LEFT:
                    pos = max(0, pos - 1)
                elif ch == curses.KEY_RIGHT:
                    pos = min(len(buf), pos + 1)
                elif ch in (curses.KEY_HOME, "\x01"):
                    pos = 0
                elif ch in (curses.KEY_END, "\x05"):
                    pos = len(buf)
                elif ch == "\x15":  # ^U
                    buf, pos = [], 0
                elif isinstance(ch, str) and ch.isprintable():
                    buf.insert(pos, ch)
                    pos += 1
        finally:
            curses.curs_set(0)

    def choose(self, title, options, current=0):
        """options = [(value, label)]; returns value or None."""
        sel = max(0, min(current, len(options) - 1))
        while True:
            self.draw()
            h, w = self.scr.getmaxyx()
            width = min(w - 4, max(len(title), *(len(lbl) for _, lbl in options)) + 6)
            height = min(len(options), h - 6)
            y0 = max(1, (h - height) // 2 - 1)
            x0 = max(1, (w - width) // 2)
            self.put(y0, x0, f" {title} ".ljust(width), curses.A_REVERSE | curses.A_BOLD)
            top = max(0, sel - height + 1)
            for i, (_, lbl) in enumerate(options[top:top + height]):
                idx = top + i
                mark = "›" if idx == sel else " "
                attr = curses.A_REVERSE if idx == sel else curses.A_NORMAL
                self.put(y0 + 1 + i, x0, f" {mark} {lbl}".ljust(width), attr)
            self.put(y0 + 1 + height, x0, " Enter choose · Esc cancel ".ljust(width),
                     curses.A_REVERSE)
            ch = self.scr.get_wch()
            if ch in (curses.KEY_UP, "k"):
                sel = (sel - 1) % len(options)
            elif ch in (curses.KEY_DOWN, "j"):
                sel = (sel + 1) % len(options)
            elif ch in ("\n", "\r", curses.KEY_ENTER):
                return options[sel][0]
            elif ch in ("\x1b", "q"):
                return None

    def message(self, title, lines, ask=False):
        """Scrollable text box. With ask=True returns True on y."""
        top = 0
        while True:
            self.scr.erase()
            h, w = self.scr.getmaxyx()
            self.put(0, 1, title, curses.A_BOLD | self.c("accent"))
            for i, ln in enumerate(lines[top:top + h - 3]):
                self.put(2 + i, 2, ln)
            foot = "y write · n cancel · ↑↓ scroll" if ask else "Enter close · ↑↓ scroll"
            self.put(h - 1, 1, foot, curses.A_REVERSE)
            self.scr.refresh()
            ch = self.scr.get_wch()
            if ch in (curses.KEY_DOWN, "j"):
                top = min(max(0, len(lines) - (h - 3)), top + 1)
            elif ch in (curses.KEY_UP, "k"):
                top = max(0, top - 1)
            elif ask and ch in ("y", "Y"):
                return True
            elif ch in ("n", "N", "\x1b", "q") or (not ask and ch in ("\n", "\r",
                                                                      curses.KEY_ENTER)):
                return False

    def editor(self, text):
        """Edit text in $VISUAL/$EDITOR. Returns new text or None if unchanged/failed."""
        ed = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "nano"
        with tempfile.NamedTemporaryFile("w+", suffix=".m913macro", delete=False) as f:
            f.write(text)
            tmp = f.name
        try:
            curses.def_prog_mode()
            curses.endwin()
            try:
                subprocess.run(shlex.split(ed) + [tmp])
            except OSError as e:
                self.status = f"could not start editor {ed!r}: {e}"
                return None
            finally:
                curses.reset_prog_mode()
                self.scr.refresh()
            with open(tmp) as f:
                return f.read()
        finally:
            os.unlink(tmp)

    # -- editing --
    def edit_button(self):
        b = BUTTON_ORDER[self.sel[0]]
        kind_now, _ = describe(self.raw, b)
        kinds = [k for k in KINDS if k[0] != "command" or b in S.COMMAND_BUTTONS]
        cur = next((i for i, k in enumerate(kinds) if k[0] == kind_now), 0)
        kind = self.choose(f"{b}: what should it do?", kinds, cur)
        if kind is None:
            return
        value = None
        if kind == "keep":
            value = ""
        elif kind == "mouse":
            value = self.choose(f"{b}: action", [(c, c) for c in SIMPLE_CHOICES])
        elif kind == "command":
            opts = [(f"preset:{n}", f"{n:<16} {d}") for n, (d, _) in S.PRESETS.items()]
            opts.append(("custom", "custom command…"))
            value = self.choose(f"{b}: command to type into the focused window", opts)
            if value == "custom":
                old = self.raw.get("commands", {}).get(b, "")
                value = self.prompt(f"{b}: command to type", "" if old.startswith("preset:")
                                    else old)
        elif kind == "key":
            value = self.prompt(f"{b}: key name (e.g. f13, a, enter, ctrl)")
        elif kind == "combo":
            value = self.prompt(f"{b}: combination (e.g. ctrl+shift+t)")
        elif kind == "media":
            value = self.choose(f"{b}: media key", [(m, m) for m in K.MEDIA])
        elif kind == "macro":
            names = self.macro_names()
            if not names:
                self.status = "no macros yet: make one on the Macros page (2, then n)"
                return
            value = self.choose(f"{b}: macro", [(n, n) for n in names])
        elif kind == "fire":
            value = self.prompt(f"{b}: rapid fire <times 0-3>:<speed 3-255>", "3:20")
        if value is None or (kind != "keep" and not value.strip()):
            return
        before = copy.deepcopy(self.raw)
        set_button(self.raw, b, kind, value.strip())
        err = check(self.raw, self.sensor)
        if err:
            self.raw = before
            self.status = ""
            self.message("Not changed", [err, "", "Nothing was changed."])
        else:
            self.status = f"{b} changed (not saved yet: s saves, a writes to the mouse)"

    def unmanage_button(self):
        b = BUTTON_ORDER[self.sel[0]]
        set_button(self.raw, b, "keep", "")
        self.status = f"{b} is no longer managed by the config"

    def edit_macro(self, name=None):
        new = name is None
        if new:
            name = self.prompt("new macro name (letters, digits, _ -)")
            if not name:
                return
            if name in self.raw.get("macros", {}):
                self.status = f"macro {name} exists"
                return
            spec = {"repeat": 1, "delay": 20, "events": ["type hello"]}
        else:
            spec = self.raw["macros"][name]
        text = macro_to_text(name, spec)
        while True:
            out = self.editor(text)
            if out is None:
                return
            try:
                spec = text_to_macro(name, out)
                break
            except M.ConfigError as e:
                if not self.message("Macro has an error", [str(e), "", "y edit again · n drop"],
                                    ask=True):
                    return
                text = out
        before = copy.deepcopy(self.raw)
        self.raw.setdefault("macros", {})[name] = spec
        err = check(self.raw, self.sensor)
        if err:
            self.raw = before
            self.message("Not changed", [err])
            return
        self.status = f"macro {name} {'added' if new else 'changed'}; assign it on the Buttons page"

    def delete_macro(self):
        names = self.macro_names()
        if not names:
            return
        n = names[self.sel[1]]
        users = [b for b, a in self.raw.get("buttons", {}).items() if a == f"macro:{n}"]
        if users:
            self.status = f"{n} is used by {', '.join(users)}; change those buttons first"
            return
        del self.raw["macros"][n]
        if not self.raw["macros"]:
            del self.raw["macros"]
        self.status = f"macro {n} deleted"

    def change_led(self, delta=0, typed=False):
        rows = self.led_rows()
        key = rows[self.sel[2]][0]
        led = dict(self.raw.get("led", {}))
        modes = list(P.LED_MODES)
        if key == "mode":
            cur = P.LED_MODE_ALIASES.get(led.get("mode"), led.get("mode"))
            if typed:
                v = self.choose("LED mode", [(m, m) for m in modes] +
                                [(None, "(not managed)")])
                if v is None and cur is None:
                    return
                if v is None:
                    self.raw.pop("led", None)
                    return
                led["mode"] = v
            else:
                i = modes.index(cur) if cur in modes else -1
                led["mode"] = modes[(i + delta) % len(modes)]
            led.setdefault("color", "ff0000")
            led.setdefault("brightness", 10)
        elif key == "colour":
            if typed:
                v = self.prompt("colour as hex rrggbb", led.get("color", ""))
                if not v:
                    return
                led["color"] = v.strip().lstrip("#").lower()
            else:
                cur = led.get("color", "ff0000")
                i = COLORS.index(cur) if cur in COLORS else -1
                led["color"] = COLORS[(i + delta) % len(COLORS)]
        elif key == "brightness":
            led["brightness"] = max(1, min(10, led.get("brightness", 10) + (delta or 0)))
        elif key == "speed":
            led["speed"] = max(1, min(10, led.get("speed", 3) + (delta or 0)))
        before = copy.deepcopy(self.raw)
        self.raw["led"] = led
        err = check(self.raw, self.sensor)
        if err:
            self.raw = before
            self.status = ""
            self.message("Not changed", [err])

    def change_dpi(self, delta=0, typed=False, add=False, remove=False):
        sensor = self.sensor if self.sensor is not None else 1
        steps = sorted(dpimod.table(sensor))
        before = copy.deepcopy(self.raw)
        d = self.raw.setdefault("dpi", {})
        levels = d.setdefault("levels", [])
        if add:
            if len(levels) >= P.DPI_LEVELS:
                self.status = f"at most {P.DPI_LEVELS} levels"
                return
            levels.append(levels[-1] * 2 if levels and isinstance(levels[-1], int)
                          and levels[-1] * 2 in steps else 800)
            if "colors" in d:
                d["colors"].append(COLORS[len(levels) % len(COLORS)])
            self.sel[3] = len(levels)
        else:
            row = self.sel[3]
            if row == 0:
                rates = sorted(P.RATES)
                cur = self.raw.get("report_rate")
                if remove:
                    self.raw.pop("report_rate", None)
                elif typed:
                    v = self.choose("report rate", [(r, f"{r} Hz") for r in rates] +
                                    [(None, "(not managed)")])
                    if v is None:
                        self.raw.pop("report_rate", None)
                    else:
                        self.raw["report_rate"] = v
                else:
                    i = rates.index(cur) if cur in rates else rates.index(1000)
                    self.raw["report_rate"] = rates[(i + delta) % len(rates)]
            else:
                i = row - 1
                if remove:
                    if len(levels) <= 1:
                        self.raw.pop("dpi", None)
                    else:
                        levels.pop(i)
                        if "colors" in d and i < len(d["colors"]):
                            d["colors"].pop(i)
                        self.sel[3] = max(1, row - 1)
                elif typed:
                    v = self.prompt(f"level {i + 1}: DPI, or X x Y (e.g. 1600 or 1600x800)",
                                    str(levels[i]).strip("[]").replace(", ", "x"))
                    if v is None:
                        self.raw = before
                        return
                    try:
                        parts = [int(p) for p in v.lower().replace(" ", "").split("x")]
                        levels[i] = parts[0] if len(parts) == 1 else parts[:2]
                    except ValueError:
                        self.raw = before
                        self.status = "not a number"
                        return
                    c = self.prompt(f"level {i + 1}: indicator colour hex (empty = none)",
                                    d.get("colors", [""] * (i + 1))[i]
                                    if i < len(d.get("colors", [])) else "")
                    if c:
                        cols = d.setdefault("colors", [])
                        while len(cols) < len(levels):
                            cols.append(COLORS[len(cols) % len(COLORS)])
                        cols[i] = c.strip().lstrip("#").lower()
                else:
                    cur = levels[i]
                    x = cur[0] if isinstance(cur, list) else cur
                    j = steps.index(x) if x in steps else 0
                    levels[i] = steps[max(0, min(len(steps) - 1, j + delta))]
        if self.raw.get("dpi") == {"levels": []}:
            self.raw.pop("dpi")
        err = check(self.raw, self.sensor)
        if err:
            self.raw = before
            self.status = ""
            self.message("Not changed", [err])

    # -- file / mouse --
    def save(self):
        try:
            O.save_raw(self.path, self.raw)
        except (OSError, O.OpsError) as e:
            self.message("Save failed", [str(e)])
            return False
        self.saved = copy.deepcopy(self.raw)
        self.status = f"saved {self.path}"
        return True

    def with_mouse(self, fn):
        self._detect()
        if not self.dev:
            self.message("No mouse", ["No Redragon M913 found. Plug in the cable or dongle."])
            return None
        try:
            with D.Mouse(self.dev["node"]) as m:
                return fn(m)
        except (D.DeviceError, O.OpsError) as e:
            self.message("Mouse error", [str(e)])
            return None

    def read_mouse(self):
        if self.raw != self.saved and not self.message(
                "Read from the mouse?", ["Unsaved changes on screen will be replaced",
                                         "by what the mouse holds now."], ask=True):
            return
        res = self.with_mouse(lambda m: O.read_config(m, self.sensor))
        if res is None:
            return
        cfg, notes = res
        self.raw = merge_from_mouse(self.raw, cfg)
        self.status = "read the mouse's active profile" + (f" ({len(notes)} note(s))"
                                                           if notes else "")
        if notes:
            self.message("Read with notes", notes)

    def apply(self):
        err = check(self.raw, self.sensor)
        if err:
            self.message("Can't apply", [err])
            return
        s, commands = O.settings_from_raw(self.raw)

        def go(m):
            todo = O.plan(m, s, self.sensor)
            if todo:
                lines = [f"{len(todo)} field(s) differ (current -> new):", ""]
                for w, cur in todo:
                    lines += [f"{w.label}", f"   {cur.hex(' ')}", f"-> {w.data.hex(' ')}", ""]
                if not O.left_click_survives(m, [w for w, _ in todo]):
                    lines.insert(0, "WARNING: no button would be left click after this!")
                lines.append("A backup is saved first; every field is read back after.")
                if not self.message("Write to the mouse?", lines, ask=True):
                    return "cancelled"
                path, bad = O.execute(m, self.dev, self.sensor, todo)
                if bad:
                    return ("FAILED to verify " + ", ".join(w.label for w, _ in bad)
                            + f" · backup: {path}")
                return f"written and verified · backup {os.path.basename(path)}"
            return "the mouse already matches"

        res = self.with_mouse(go)
        if res is None or res == "cancelled":
            return
        if not self.save():
            return
        msg = res
        if commands:
            msg += " · command buttons type into the focused window"
        self.status = msg

    # -- main loop --
    def nrows(self):
        return [len(BUTTON_ORDER), max(1, len(self.macro_names())), len(self.led_rows()),
                len(self.dpi_rows())][self.tab]

    def run(self):
        while True:
            self.draw()
            ch = self.scr.get_wch()
            n = self.nrows()
            if ch in ("q", "Q"):
                if self.raw == self.saved or self.message(
                        "Quit without saving?", ["Your changes are not saved."], ask=True):
                    return 0
            elif ch in ("\t", curses.KEY_BTAB):
                self.tab = (self.tab + (1 if ch == "\t" else -1)) % len(TABS)
            elif ch in ("1", "2", "3", "4"):
                self.tab = int(ch) - 1
            elif ch in (curses.KEY_UP, "k"):
                self.sel[self.tab] = (self.sel[self.tab] - 1) % n
            elif ch in (curses.KEY_DOWN, "j"):
                self.sel[self.tab] = (self.sel[self.tab] + 1) % n
            elif ch == "s":
                self.save()
            elif ch == "a":
                self.apply()
            elif ch == "r":
                self.read_mouse()
            elif ch == curses.KEY_RESIZE:
                pass
            elif self.tab == 0:
                if ch in ("\n", "\r", curses.KEY_ENTER):
                    self.edit_button()
                elif ch in (curses.KEY_DC, "x"):
                    self.unmanage_button()
            elif self.tab == 1:
                names = self.macro_names()
                if ch == "n":
                    self.edit_macro()
                elif ch in ("\n", "\r", curses.KEY_ENTER) and names:
                    self.edit_macro(names[self.sel[1]])
                elif ch in (curses.KEY_DC, "x"):
                    self.delete_macro()
            elif self.tab == 2:
                if ch in (curses.KEY_LEFT, "h"):
                    self.change_led(-1)
                elif ch in (curses.KEY_RIGHT, "l"):
                    self.change_led(+1)
                elif ch in ("\n", "\r", curses.KEY_ENTER):
                    self.change_led(typed=True)
            elif self.tab == 3:
                if ch in (curses.KEY_LEFT, "h"):
                    self.change_dpi(-1)
                elif ch in (curses.KEY_RIGHT, "l"):
                    self.change_dpi(+1)
                elif ch in ("\n", "\r", curses.KEY_ENTER):
                    self.change_dpi(typed=True)
                elif ch == "n":
                    self.change_dpi(add=True)
                elif ch in (curses.KEY_DC, "x"):
                    self.change_dpi(remove=True)


def run(path, device=None, sensor=None):
    os.environ.setdefault("ESCDELAY", "25")
    try:
        O.load_raw(path)
    except O.OpsError as e:
        print(f"m913ctl: {e}")
        return 1
    return curses.wrapper(lambda scr: UI(scr, path, device, sensor).run())
