"""GTK4 / libadwaita window: `m913ctl gui`.

Edits the same config file as the command line and the TUI. Needs
python-gobject, gtk4 and libadwaita.
"""

import copy
import os
import sys
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_foreign("cairo")  # python-cairo, for drawing the mouse
import cairo  # noqa: E402
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import device as D  # noqa: E402
from . import dpi as dpimod  # noqa: E402
from . import keys as K  # noqa: E402
from . import model as M  # noqa: E402
from . import ops as O  # noqa: E402
from . import protocol as P  # noqa: E402
from . import mousepic as MP  # noqa: E402
from . import scripts as S  # noqa: E402
from .edit import (BUTTON_ORDER, COLORS, KINDS, SIMPLE_CHOICES, check, describe,  # noqa: E402
                   merge_from_mouse, set_button)

APP_ID = "io.github.m913ctl"
NOT_MANAGED = "not managed"
PRETTY = {"left": "Left click", "right": "Right click", "middle": "Middle / wheel",
          "fire": "Fire button"}
PRETTY.update({f"button_{i}": f"Side button {i}" for i in range(1, 13)})


def rgba(hexstr):
    c = Gdk.RGBA()
    c.parse("#" + hexstr)
    return c


def to_hex(c):
    return "".join(f"{round(v * 255):02x}" for v in (c.red, c.green, c.blue))


def combo(title, items, selected=0):
    row = Adw.ComboRow(title=title)
    row.set_model(Gtk.StringList.new(items))
    row.set_selected(max(0, selected))
    return row


def worker(fn, done):
    """Run fn() off the UI thread, then done(result, error) on it."""
    def run():
        try:
            res, err = fn(), None
        except (D.DeviceError, O.OpsError, M.ConfigError, OSError) as e:
            res, err = None, e
        GLib.idle_add(lambda: (done(res, err), False)[1])
    threading.Thread(target=run, daemon=True).start()


KIND_RGB = {"command": (0.21, 0.52, 0.89), "key": (0.15, 0.64, 0.41),
            "combo": (0.15, 0.64, 0.41), "media": (0.15, 0.64, 0.41),
            "macro": (0.57, 0.25, 0.68), "fire": (0.90, 0.55, 0.10)}
LEGEND = [("command", "types a command"), ("key", "keys / media"), ("macro", "macro"),
          ("mouse", "mouse action"), ("keep", "not managed")]


class MousePicture(Gtk.DrawingArea):
    """Clickable drawing of the M913: top view + thumb pad."""

    def __init__(self, win):
        # the preferences column is ~600 px wide: size the height to match the
        # drawing's aspect ratio so there is no empty band above and below
        super().__init__(hexpand=True, content_height=int(MP.HEIGHT * 0.72), has_tooltip=True)
        self.win = win
        self.hover = None
        self.set_draw_func(self.draw)
        click = Gtk.GestureClick()
        click.connect("released", self.on_click)
        self.add_controller(click)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self.on_motion)
        motion.connect("leave", lambda *_: self.set_hover(None))
        self.add_controller(motion)
        self.connect("query-tooltip", self.on_tooltip)

    def at(self, x, y):
        return MP.button_at(x, y, *MP.fit(self.get_width(), self.get_height()))

    def set_hover(self, name):
        if name != self.hover:
            self.hover = name
            self.set_cursor_from_name("pointer" if name else None)
            self.queue_draw()

    def on_motion(self, _c, x, y):
        self.set_hover(self.at(x, y))

    def on_click(self, _g, _n, x, y):
        b = self.at(x, y)
        if b:
            self.win.edit_button(b)

    def on_tooltip(self, _w, x, y, _kb, tip):
        b = self.at(x, y)
        if not b:
            return False
        kind, text = describe(self.win.raw, b)
        tip.set_text(f"{PRETTY[b]}: {text}")
        return True

    # -- drawing --
    def draw(self, _area, cr, width, height):
        s, ox, oy = MP.fit(width, height)
        cr.translate(ox, oy)
        cr.scale(s, s)
        fg = self.get_color()
        self.fg = (fg.red, fg.green, fg.blue)
        self.draw_top(cr)
        self.draw_side(cr)
        self.draw_tables(cr)
        self.draw_legend(cr)

    def rgba(self, cr, rgb, a):
        cr.set_source_rgba(rgb[0], rgb[1], rgb[2], a)

    def rounded(self, cr, x, y, w, h, r):
        r = min(r, w / 2, h / 2)
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -1.5708, 0)
        cr.arc(x + w - r, y + h - r, r, 0, 1.5708)
        cr.arc(x + r, y + h - r, r, 1.5708, 3.1416)
        cr.arc(x + r, y + r, r, 3.1416, 4.7124)
        cr.close_path()

    def path(self, cr, cmds):
        cr.new_path()
        for c in cmds:
            if c[0] == "M":
                cr.move_to(*c[1:])
            elif c[0] == "L":
                cr.line_to(*c[1:])
            elif c[0] == "C":
                cr.curve_to(*c[1:])
            else:
                cr.close_path()

    def poly(self, cr, pts, r=0):
        if r:
            x, y, w, h = MP.bbox(pts)
            self.rounded(cr, x, y, w, h, r)
            return
        cr.new_path()
        cr.move_to(*pts[0])
        for p in pts[1:]:
            cr.line_to(*p)
        cr.close_path()

    def shell(self, cr, body, lines):
        """Mouse body: drop shadow, shaded fill, a darker rim, crease lines."""
        cr.save()
        cr.translate(3, 7)
        self.path(cr, body)
        cr.set_source_rgba(0, 0, 0, 0.10)
        cr.fill()
        cr.restore()
        self.path(cr, body)
        x1, y1, x2, y2 = cr.path_extents()
        g = cairo.LinearGradient(x1, y1, x2, y2)
        g.add_color_stop_rgba(0, *self.fg, 0.04)
        g.add_color_stop_rgba(0.55, *self.fg, 0.09)
        g.add_color_stop_rgba(1, *self.fg, 0.15)
        cr.set_source(g)
        cr.fill_preserve()
        self.rgba(cr, self.fg, 0.6)
        cr.set_line_width(2)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        cr.stroke()
        cr.set_line_width(1)
        for ln in lines:
            self.path(cr, ln)
            self.rgba(cr, self.fg, 0.22)
            cr.stroke()

    def gloss(self, cr):
        """Light sheen over the current path (keeps it)."""
        x1, y1, x2, y2 = cr.path_extents()
        g = cairo.LinearGradient(0, y1, 0, y2)
        g.add_color_stop_rgba(0, 1, 1, 1, 0.28)
        g.add_color_stop_rgba(0.5, 1, 1, 1, 0.0)
        g.add_color_stop_rgba(1, 0, 0, 0, 0.06)
        cr.set_source(g)
        cr.fill_preserve()

    def fill_for(self, cr, b, alpha=0.38):
        kind, _ = describe(self.win.raw, b)
        if kind in KIND_RGB:
            self.rgba(cr, KIND_RGB[kind], alpha)
        elif kind == "keep":
            self.rgba(cr, self.fg, 0.04)
        else:
            self.rgba(cr, self.fg, 0.14)
        return kind

    def outline(self, cr, b, kind):
        if b == self.hover:
            self.rgba(cr, KIND_RGB["command"], 1)
            cr.set_line_width(3)
        else:
            self.rgba(cr, self.fg, 0.55)
            cr.set_line_width(1.2)
        cr.set_dash([4, 3] if kind == "keep" and b != self.hover else [])
        cr.stroke()
        cr.set_dash([])

    def text(self, cr, s, cx, cy, size=13, bold=True, alpha=0.9):
        cr.select_font_face("Sans", 0, 1 if bold else 0)
        cr.set_font_size(size)
        ext = cr.text_extents(s)
        cr.move_to(cx - ext.width / 2 - ext.x_bearing, cy - ext.height / 2 - ext.y_bearing)
        self.rgba(cr, self.fg, alpha)
        cr.show_text(s)

    def button(self, cr, b, sub=True, sub_dy=16, size=13, raised=False):
        sh = MP.SHAPES[b]
        if raised:  # keycaps cast a small shadow
            cr.save()
            cr.translate(0, 2.5)
            self.poly(cr, sh["poly"], sh["r"])
            cr.set_source_rgba(0, 0, 0, 0.18)
            cr.fill()
            cr.restore()
        self.poly(cr, sh["poly"], sh["r"])
        kind = self.fill_for(cr, b)
        cr.fill_preserve()
        self.gloss(cr)
        if b == self.hover:
            cr.save()
            self.rgba(cr, KIND_RGB["command"], 0.25)
            cr.set_line_width(8)
            cr.stroke_preserve()
            cr.restore()
        self.outline(cr, b, kind)
        lx, ly = sh["label"]
        if sh["text"]:
            self.text(cr, sh["text"], lx, ly - (6 if sub and kind != "keep" else 0), size=size)
        if sub and kind != "keep":
            self.text(cr, MP.short(describe(self.win.raw, b)[1]), lx, ly - 6 + sub_dy,
                      size=8.5, bold=False, alpha=0.8)

    def decor(self, cr, pts, rgb=None, alpha=0.22, stroke=False):
        self.poly(cr, pts)
        self.rgba(cr, rgb or self.fg, alpha)
        if stroke:
            cr.fill_preserve()
            self.rgba(cr, self.fg, 0.45)
            cr.set_line_width(1)
            cr.stroke()
        else:
            cr.fill()

    def lines(self, cr, lines, alpha=0.25):
        cr.set_line_width(1.1)
        for pts in lines:
            cr.new_path()
            cr.move_to(*pts[0])
            for p in pts[1:]:
                cr.line_to(*p)
            self.rgba(cr, self.fg, alpha)
            cr.stroke()

    def draw_top(self, cr):
        self.shell(cr, [("M", *MP.TOP_OUTLINE[0])] + [("L", *p) for p in MP.TOP_OUTLINE[1:]]
                   + [("Z",)], [])
        self.lines(cr, MP.TOP_LINES)
        d = MP.TOP_DECOR
        self.decor(cr, d["side_keys"], alpha=0.25, stroke=True)
        self.decor(cr, d["console"], alpha=0.12, stroke=True)
        for b in ("left", "right"):
            self.button(cr, b, sub=False, size=15)
        for name in ("dpi_up", "dpi_down"):
            self.decor(cr, d[name], alpha=0.22, stroke=True)
        x, y, w, h = MP.bbox(d["dpi_down"])
        self.text(cr, "DPI", x + w / 2, y + h + 8, size=7.5, bold=False, alpha=0.6)
        for name in ("led_1", "led_2", "led_3", "led_4", "strip_l", "strip_r"):
            self.decor(cr, d[name], KIND_RGB["fire"], 0.6)
        # wheel with treads
        sh = MP.SHAPES["middle"]
        x, y, w, h = MP.bbox(sh["poly"])
        self.poly(cr, sh["poly"], sh["r"])
        self.rgba(cr, self.fg, 0.30)
        cr.fill_preserve()
        kind = self.fill_for(cr, "middle", 0.5)
        cr.fill_preserve()
        self.gloss(cr)
        self.outline(cr, "middle", kind)
        self.rgba(cr, self.fg, 0.35)
        for i in range(int((h - 10) // 7)):
            cr.rectangle(x + 5, y + 6 + i * 7, w - 10, 2)
        cr.fill()
        self.button(cr, "fire", sub=False, size=10, raised=True)
        self.text(cr, "Top  ·  front ↑", 174, 462, size=11, bold=False, alpha=0.6)

    def draw_side(self, cr):
        self.shell(cr, [("M", *MP.SIDE_OUTLINE[0])] + [("L", *p) for p in MP.SIDE_OUTLINE[1:]]
                   + [("Z",)], [])
        self.lines(cr, MP.SIDE_LINES)
        d = MP.SIDE_DECOR
        self.decor(cr, d["groove"], alpha=0.30)
        self.decor(cr, d["wheel"], alpha=0.40)
        self.decor(cr, d["top_btn_1"], alpha=0.30, stroke=True)
        self.decor(cr, d["top_btn_2"], alpha=0.30, stroke=True)
        self.decor(cr, d["strip"], KIND_RGB["fire"], 0.6)
        x, y, w, h = MP.bbox(d["pad"])
        self.rounded(cr, x, y, w, h, 12)
        self.rgba(cr, self.fg, 0.10)
        cr.fill_preserve()
        self.rgba(cr, self.fg, 0.35)
        cr.set_line_width(1)
        cr.stroke()
        for i in range(12):
            self.button(cr, f"button_{i + 1}", sub=False, size=11, raised=True)
        self.text(cr, "Left side  ·  front ↑", 452, 462, size=11, bold=False, alpha=0.6)

    def draw_tables(self, cr):
        t = MP.PAD_TABLE
        self.text(cr, "Thumb pad", t["x"] + 1.5 * t["w"] + t["gap"], t["y"] - 14, size=11,
                  bold=True, alpha=0.75)
        t2 = MP.MAIN_TABLE
        self.text(cr, "Main buttons", t2["x"] + t2["w"] + t2["gap"] / 2, t2["y"] - 14, size=11,
                  bold=True, alpha=0.75)
        for b, (x, y, w, h, title) in MP.CELLS.items():
            self.rounded(cr, x, y, w, h, 7)
            kind = self.fill_for(cr, b, 0.30)
            cr.fill_preserve()
            if b == self.hover:
                cr.save()
                self.rgba(cr, KIND_RGB["command"], 0.25)
                cr.set_line_width(6)
                cr.stroke_preserve()
                cr.restore()
            self.outline(cr, b, kind)
            self.text(cr, title, x + w / 2, y + 13, size=11)
            label = "—" if kind == "keep" else MP.short(describe(self.win.raw, b)[1],
                                                         10 if w < 100 else 16)
            self.text(cr, label, x + w / 2, y + h - 12, size=9, bold=False, alpha=0.8)

    def draw_legend(self, cr):
        x = 150
        y = MP.HEIGHT - 6
        cr.select_font_face("Sans", 0, 0)
        cr.set_font_size(10)
        for kind, label in LEGEND:
            self.rounded(cr, x, y - 9, 12, 10, 3)
            if kind in KIND_RGB:
                self.rgba(cr, KIND_RGB[kind], 0.5)
            else:
                self.rgba(cr, self.fg, 0.16 if kind == "mouse" else 0.04)
            cr.fill_preserve()
            self.rgba(cr, self.fg, 0.5)
            cr.set_line_width(1)
            cr.set_dash([3, 2] if kind == "keep" else [])
            cr.stroke()
            cr.set_dash([])
            cr.move_to(x + 16, y)
            self.rgba(cr, self.fg, 0.7)
            cr.show_text(label)
            x += 26 + cr.text_extents(label).x_advance + 14


class Window(Adw.ApplicationWindow):
    def __init__(self, app, path, device=None, sensor=None):
        super().__init__(application=app, title="M913")
        self.set_default_size(760, 720)
        self.path = path
        self.node = device
        self.sensor_override = sensor
        self.raw = O.load_raw(path)
        self.saved = copy.deepcopy(self.raw)
        self.dev, self.sensor = None, sensor
        self.busy = False
        self.loading = False
        self.detect()

        self.toasts = Adw.ToastOverlay()
        view = Adw.ToolbarView()
        self.toasts.set_child(view)
        self.set_content(self.toasts)

        header = Adw.HeaderBar()
        self.stack = Adw.ViewStack()
        switcher = Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        header.set_title_widget(switcher)
        self.read_btn = Gtk.Button(label="Read mouse")
        self.read_btn.set_tooltip_text("Load what the mouse holds now")
        self.read_btn.connect("clicked", lambda *_: self.read_mouse())
        header.pack_start(self.read_btn)
        self.restore_btn = Gtk.Button(icon_name="edit-undo-symbolic")
        self.restore_btn.set_tooltip_text("Restore the mouse from a backup")
        self.restore_btn.connect("clicked", lambda *_: self.restore_dialog())
        header.pack_start(self.restore_btn)
        self.apply_btn = Gtk.Button(label="Apply")
        self.apply_btn.add_css_class("suggested-action")
        self.apply_btn.set_tooltip_text("Save and write to the mouse")
        self.apply_btn.connect("clicked", lambda *_: self.apply())
        header.pack_end(self.apply_btn)
        self.save_btn = Gtk.Button(icon_name="document-save-symbolic")
        self.save_btn.set_tooltip_text(f"Save {self.path}")
        self.save_btn.connect("clicked", lambda *_: self.save())
        header.pack_end(self.save_btn)
        view.add_top_bar(header)

        self.banner = Adw.Banner()
        view.add_top_bar(self.banner)
        self.devbar = Gtk.Label(xalign=0, ellipsize=3)  # Pango.EllipsizeMode.END
        self.devbar.add_css_class("dim-label")
        self.devbar.set_margin_start(12)
        self.devbar.set_margin_bottom(6)
        view.add_bottom_bar(self.devbar)
        view.set_content(self.stack)

        self.buttons_page = Adw.PreferencesPage()
        self.macros_page = Adw.PreferencesPage()
        self.led_page = Adw.PreferencesPage()
        self.dpi_page = Adw.PreferencesPage()
        for page, name, title, icon in [
                (self.buttons_page, "buttons", "Buttons", "input-mouse-symbolic"),
                (self.macros_page, "macros", "Macros", "media-playlist-repeat-symbolic"),
                (self.led_page, "led", "Lighting", "display-brightness-symbolic"),
                (self.dpi_page, "dpi", "DPI", "power-profile-performance-symbolic")]:
            self.stack.add_titled_with_icon(page, name, title, icon)
        self.groups = {}
        self.connect("close-request", self.on_close)
        self.refresh()
        GLib.idle_add(self.ensure_baseline_backup)

    # -- state --
    def detect(self):
        try:
            self.dev, self.sensor = O.find_device(self.node, self.sensor_override)
        except O.OpsError:
            self.dev = None

    def toast(self, text, timeout=4):
        t = Adw.Toast(title=GLib.markup_escape_text(text))
        t.set_timeout(timeout)
        self.toasts.add_toast(t)

    def changed(self, before=None, rebuild=True):
        err = check(self.raw, self.sensor)
        if err and before is not None:
            self.raw = before
            self.toast(f"Not changed: {err}", 6)
            rebuild = True
        self.refresh(rebuild)

    def refresh(self, rebuild=True):
        self.loading = True
        try:
            err = check(self.raw, self.sensor)
            self.banner.set_title(err or "")
            self.banner.set_revealed(bool(err))
            dirty = self.raw != self.saved
            self.set_title("M913" + (" •" if dirty else ""))
            self.save_btn.set_sensitive(dirty and not err)
            self.apply_btn.set_sensitive(not err and not self.busy)
            self.read_btn.set_sensitive(not self.busy)
            self.restore_btn.set_sensitive(not self.busy)
            if self.dev:
                kind = "wired" if self.dev.get("pid") == P.PID_WIRED else "wireless"
                dev = (f"{self.dev['node']} · {kind} · "
                       f"{dpimod.SENSORS.get(self.sensor, 'sensor ?')}")
            else:
                dev = "no mouse connected"
            shown = str(self.path).replace(os.path.expanduser("~"), "~", 1)
            self.devbar.set_label(f"{dev}   ·   {shown}{'  (unsaved)' if dirty else ''}")
            if not rebuild:
                return
            self.build_buttons()
            self.build_macros()
            self.build_led()
            self.build_dpi()
        finally:
            self.loading = False

    def regroup(self, page, key, title, description=None, suffix=None):
        old = self.groups.pop(key, None)
        if old is not None:
            page.remove(old)
        g = Adw.PreferencesGroup(title=title)
        if description:
            g.set_description(description)
        if suffix:
            g.set_header_suffix(suffix)
        page.add(g)
        self.groups[key] = g
        return g

    # -- Buttons --
    def build_buttons(self):
        pic = self.regroup(self.buttons_page, "b-pic", "Layout",
                           "Click a button on the mouse to change what it does.")
        self.picture = MousePicture(self)
        frame = Gtk.Frame(child=self.picture)
        frame.add_css_class("card")
        pic.add(frame)
        main = self.regroup(self.buttons_page, "b-main", "Main buttons")
        side = self.regroup(self.buttons_page, "b-side", "Side keypad",
                            "A side button can also type a command into the focused window; "
                            "it is stored on the mouse as a typed macro.")
        for b in BUTTON_ORDER:
            kind, text = describe(self.raw, b)
            row = Adw.ActionRow(title=PRETTY[b], subtitle=GLib.markup_escape_text(text))
            row.set_activatable(True)
            row.set_subtitle_lines(1)
            tag = Gtk.Label(label=kind if kind != "keep" else "")
            tag.add_css_class("dim-label")
            if kind == "command":
                tag.set_label("types")
                tag.add_css_class("accent")
            row.add_suffix(tag)
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            row.connect("activated", lambda _r, b=b: self.edit_button(b))
            (main if b in PRETTY and not b.startswith("button_") else side).add(row)

    def edit_button(self, b):
        kinds = [k for k in KINDS if k[0] != "command" or b in S.COMMAND_BUTTONS]
        kind_now, _ = describe(self.raw, b)
        dlg = Adw.AlertDialog(heading=PRETTY[b], body="What should this button do?")
        box = Adw.PreferencesGroup()
        kind_row = combo("Does", [lbl for _, lbl in kinds],
                         next((i for i, k in enumerate(kinds) if k[0] == kind_now), 0))
        box.add(kind_row)
        choice_row = Adw.ComboRow(title="Action")
        box.add(choice_row)
        entry_row = Adw.EntryRow(title="Value")
        box.add(entry_row)
        hint = Gtk.Label(xalign=0, wrap=True)
        hint.add_css_class("dim-label")
        hint.add_css_class("caption")
        hint.set_margin_top(8)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.append(box)
        outer.append(hint)
        dlg.set_extra_child(outer)

        cur_btn = self.raw.get("buttons", {}).get(b, "")
        cur_cmd = self.raw.get("commands", {}).get(b, "")
        state = {"choices": [], "kind": None, "busy": False}

        def choices_for(kind):
            if kind == "mouse":
                return [(c, c) for c in SIMPLE_CHOICES]
            if kind == "media":
                return [(m, m.replace("_", " ")) for m in K.MEDIA]
            if kind == "macro":
                return [(n, n) for n in self.raw.get("macros", {})]
            if kind == "command":
                return ([(f"preset:{n}", f"{n} — {d}") for n, (d, _) in S.PRESETS.items()]
                        + [("custom", "Custom command…")])
            return []

        def update(*_):
            if state["busy"]:
                return
            state["busy"] = True
            try:
                _update()
            finally:
                state["busy"] = False

        def _update():
            kind = kinds[kind_row.get_selected()][0]
            ch = choices_for(kind)
            if kind != state["kind"]:  # only rebuild the list when the kind changes
                state["kind"] = kind
                state["choices"] = ch
                choice_row.set_visible(bool(ch))
                if ch:
                    choice_row.set_model(Gtk.StringList.new([lbl for _, lbl in ch]))
                    cur = cur_cmd if kind == "command" else (
                        cur_btn.split(":", 1)[1] if ":" in cur_btn else cur_btn)
                    idx = next((i for i, (v, _) in enumerate(ch) if v == cur), None)
                    if kind == "command" and idx is None and cur_cmd:
                        idx = len(ch) - 1
                    choice_row.set_selected(idx or 0)
            sel = ch[choice_row.get_selected()][0] if ch else None
            need_entry = kind in ("key", "combo", "fire") or sel == "custom"
            entry_row.set_visible(need_entry)
            titles = {"key": "Key name (f13, a, enter, ctrl…)",
                      "combo": "Combination (ctrl+shift+t)",
                      "fire": "Times:speed (0-3 : 3-255)",
                      "command": "Command to type (e.g. sudo pacman -Syu)"}
            entry_row.set_title(titles.get(kind, "Value"))
            if need_entry and not entry_row.get_text():
                if kind == "command" and cur_cmd and not cur_cmd.startswith("preset:"):
                    entry_row.set_text(cur_cmd)
                elif kind == "fire":
                    entry_row.set_text(cur_btn[5:] if cur_btn.startswith("fire:") else "3:20")
                elif cur_btn.startswith(kind + ":"):
                    entry_row.set_text(cur_btn.split(":", 1)[1])
            hint.set_label({
                "command": "Typed into whatever window has focus when you press the button "
                           "(open a terminal first). Stored on the mouse; ~31 characters max.",
                "macro": "Make macros on the Macros page." if not ch else "",
                "keep": "The config won't touch this button; the mouse keeps what it has.",
            }.get(kind, ""))
            dlg.set_response_enabled("save", kind != "macro" or bool(ch))

        dlg.add_response("cancel", "Cancel")
        dlg.add_response("save", "Set")
        dlg.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("save")
        dlg.set_close_response("cancel")
        kind_row.connect("notify::selected", lambda *_: (entry_row.set_text(""), update()))
        choice_row.connect("notify::selected", lambda *_: update())
        update()

        def response(_d, resp):
            if resp != "save":
                return
            kind = kinds[kind_row.get_selected()][0]
            ch = state["choices"]
            sel = ch[choice_row.get_selected()][0] if ch else None
            value = entry_row.get_text().strip() if (sel in (None, "custom")) else sel
            if kind == "keep":
                value = ""
            elif not value:
                self.toast("Nothing set: the value was empty")
                return
            before = copy.deepcopy(self.raw)
            set_button(self.raw, b, kind, value)
            self.changed(before)

        dlg.connect("response", response)
        dlg.present(self)

    # -- Macros --
    def build_macros(self):
        add = Gtk.Button(icon_name="list-add-symbolic", valign=Gtk.Align.CENTER)
        add.add_css_class("flat")
        add.set_tooltip_text("New macro")
        add.connect("clicked", lambda *_: self.edit_macro(None))
        g = self.regroup(self.macros_page, "macros", "Macros",
                         "Stored in the mouse; assign one to a button on the Buttons page.", add)
        macros = self.raw.get("macros", {})
        if not macros:
            g.add(Adw.ActionRow(title="No macros yet", subtitle="Press + to make one"))
        for n, spec in macros.items():
            used = [PRETTY[b] for b, a in self.raw.get("buttons", {}).items()
                    if a == f"macro:{n}"]
            row = Adw.ActionRow(title=n, subtitle=GLib.markup_escape_text(
                " · ".join(spec.get("events", []))))
            row.set_subtitle_lines(2)
            row.set_activatable(True)
            row.connect("activated", lambda _r, n=n: self.edit_macro(n))
            lab = Gtk.Label(label=", ".join(used) or "unused")
            lab.add_css_class("dim-label")
            row.add_suffix(lab)
            rm = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER)
            rm.add_css_class("flat")
            rm.set_tooltip_text("Delete")
            rm.set_sensitive(not used)
            rm.connect("clicked", lambda _b, n=n: self.delete_macro(n))
            row.add_suffix(rm)
            g.add(row)

    def edit_macro(self, name):
        spec = self.raw.get("macros", {}).get(name, {"repeat": 1, "delay": 20,
                                                     "events": ["type hello"]})
        dlg = Adw.AlertDialog(heading=f"Macro {name}" if name else "New macro")
        grp = Adw.PreferencesGroup()
        name_row = Adw.EntryRow(title="Name (letters, digits, _ -)")
        if name is None:
            grp.add(name_row)
        rep = combo("Repeat", ["once", "2 times", "3 times", "5 times", "10 times",
                               "while held", "until a key is pressed"])
        rep_values = [1, 2, 3, 5, 10, "hold", "until_key"]
        r = spec.get("repeat", 1)
        rep.set_selected(rep_values.index(r) if r in rep_values else 0)
        grp.add(rep)
        delay = Adw.SpinRow.new_with_range(P.MACRO_MIN_DELAY, 5000, 1)
        delay.set_title("Delay after each event (ms)")
        delay.set_value(spec.get("delay", 20))
        grp.add(delay)
        tv = Gtk.TextView(monospace=True, wrap_mode=Gtk.WrapMode.NONE)
        tv.get_buffer().set_text("\n".join(spec.get("events", [])))
        tv.set_top_margin(6)
        tv.set_left_margin(6)
        sw = Gtk.ScrolledWindow(min_content_height=180)
        sw.set_child(tv)
        frame = Gtk.Frame(child=sw)
        frame.set_margin_top(12)
        help_ = Gtk.Label(xalign=0, wrap=True, label=(
            "One per line: tap a · down shift · up shift · combo ctrl+c · type Hello · "
            "delay 100 · tap mouse_left"))
        help_.add_css_class("dim-label")
        help_.add_css_class("caption")
        help_.set_margin_top(6)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for w in (grp, frame, help_):
            box.append(w)
        dlg.set_extra_child(box)
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("save", "Save")
        dlg.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_close_response("cancel")

        def response(_d, resp):
            if resp != "save":
                return
            n = name or name_row.get_text().strip()
            if not n:
                self.toast("A macro needs a name")
                return
            if name is None and n in self.raw.get("macros", {}):
                self.toast(f"Macro {n} already exists")
                return
            buf = tv.get_buffer()
            text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
            new = {"repeat": rep_values[rep.get_selected()], "delay": int(delay.get_value()),
                   "events": [ln.strip() for ln in text.splitlines() if ln.strip()]}
            before = copy.deepcopy(self.raw)
            self.raw.setdefault("macros", {})[n] = new
            self.changed(before)

        dlg.connect("response", response)
        dlg.present(self)

    def delete_macro(self, n):
        before = copy.deepcopy(self.raw)
        del self.raw["macros"][n]
        if not self.raw["macros"]:
            del self.raw["macros"]
        self.changed(before)

    # -- Lighting --
    def build_led(self):
        g = self.regroup(self.led_page, "led", "Lighting")
        led = self.raw.get("led", {})
        modes = [NOT_MANAGED] + list(P.LED_MODES)
        mode = led.get("mode", NOT_MANAGED) if led else NOT_MANAGED
        mode = P.LED_MODE_ALIASES.get(mode, mode)
        mrow = combo("Mode", modes, modes.index(mode) if mode in modes else 0)
        mrow.connect("notify::selected", lambda r, *_: self.set_led(
            "mode", modes[r.get_selected()]))
        g.add(mrow)
        if mode in (NOT_MANAGED, "off"):
            return
        if mode == "rainbow":
            g.set_description("Rainbow cycles through the colours by itself, so it has no "
                              "colour setting.")
        else:
            g.add(self.colour_row("Colour", led.get("color", "ff0000"),
                                  lambda hx: self.set_led("color", hx)))
        brow = Adw.SpinRow.new_with_range(1, 10, 1)
        brow.set_title("Brightness")
        brow.set_value(led.get("brightness", 10))
        brow.connect("notify::value", lambda r, *_: self.set_led("brightness",
                                                                 int(r.get_value())))
        g.add(brow)
        if mode in ("breathing", "rainbow"):
            srow = Adw.SpinRow.new_with_range(1, 10, 1)
            srow.set_title("Speed")
            srow.set_value(led.get("speed", 3))
            srow.connect("notify::value", lambda r, *_: self.set_led("speed",
                                                                     int(r.get_value())))
            g.add(srow)

    def colour_row(self, title, current, on_pick):
        """A row of preset colour dots plus a "Custom colour…" button.
        The dot matching the current colour gets a ring."""
        row = Adw.ActionRow(title=title, subtitle=f"#{current}")
        box = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
        for hx in COLORS:
            sw = Gtk.Button(valign=Gtk.Align.CENTER)
            for c in ("circular", "m913-swatch", f"m913-c{hx}"):
                sw.add_css_class(c)
            if hx == current:
                sw.add_css_class("m913-active")
            sw.set_tooltip_text(f"#{hx}")
            sw.connect("clicked", lambda _b, hx=hx: on_pick(hx))
            box.append(sw)
        custom = Gtk.Button(label="Custom colour…", valign=Gtk.Align.CENTER)
        custom.set_tooltip_text("Pick any colour")
        if current not in COLORS:
            custom.add_css_class("m913-active-btn")

        def pick(_b):
            dialog = Gtk.ColorDialog(with_alpha=False, title=f"{title}: custom colour")

            def done(d, res):
                try:
                    c = d.choose_rgba_finish(res)
                except GLib.Error:
                    return  # cancelled
                on_pick(to_hex(c))

            dialog.choose_rgba(self, rgba(current), None, done)

        custom.connect("clicked", pick)
        box.append(custom)
        row.add_suffix(box)
        return row

    def set_led(self, key, value):
        if self.loading:
            return
        before = copy.deepcopy(self.raw)
        if key == "mode" and value == NOT_MANAGED:
            self.raw.pop("led", None)
        else:
            led = dict(self.raw.get("led", {}))
            led.setdefault("color", "ff0000")
            led.setdefault("brightness", 10)
            led[key] = value
            led.setdefault("mode", "static")
            self.raw["led"] = led
        full = key in ("mode", "color")
        GLib.idle_add(lambda: (self.changed(before, full), False)[1])

    # -- DPI --
    def build_dpi(self):
        g = self.regroup(self.dpi_page, "rate", "Report rate")
        rates = [NOT_MANAGED] + [f"{r} Hz" for r in sorted(P.RATES)]
        cur = self.raw.get("report_rate")
        rrow = combo("Polling rate", rates, rates.index(f"{cur} Hz") if cur else 0)
        rrow.connect("notify::selected", lambda r, *_: self.set_rate(r.get_selected()))
        g.add(rrow)

        add = Gtk.Button(icon_name="list-add-symbolic", valign=Gtk.Align.CENTER)
        add.add_css_class("flat")
        add.set_tooltip_text("Add a level")
        d = self.raw.get("dpi", {})
        levels = d.get("levels", [])
        add.set_sensitive(len(levels) < P.DPI_LEVELS)
        add.connect("clicked", lambda *_: self.dpi_edit(add=True))
        sensor = self.sensor if self.sensor is not None else 1
        steps = sorted(dpimod.table(sensor))
        lg = self.regroup(self.dpi_page, "levels", "DPI levels",
                          f"The DPI button cycles through these. "
                          f"{dpimod.SENSORS.get(sensor)}: {steps[0]}–{steps[-1]}.", add)
        if not levels:
            lg.add(Adw.ActionRow(title="Not managed", subtitle="Press + to set DPI levels"))
        colors = d.get("colors", [])
        for i, lv in enumerate(levels):
            x = lv[0] if isinstance(lv, list) else lv
            row = Adw.SpinRow.new_with_range(steps[0], steps[-1], 100)
            row.set_title(f"Level {i + 1}")
            if isinstance(lv, list):
                row.set_subtitle(f"Y = {lv[1]}")
            row.set_value(x)
            row.connect("notify::value", lambda r, *_, i=i: self.dpi_edit(
                i, value=int(r.get_value())))
            cb = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False),
                                       valign=Gtk.Align.CENTER)
            cb.set_rgba(rgba(colors[i] if i < len(colors) else COLORS[i % len(COLORS)]))
            cb.set_tooltip_text("Indicator colour")
            cb.connect("notify::rgba", lambda b, *_, i=i: self.dpi_edit(
                i, color=to_hex(b.get_rgba())))
            row.add_suffix(cb)
            rm = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER)
            rm.add_css_class("flat")
            rm.connect("clicked", lambda _b, i=i: self.dpi_edit(i, remove=True))
            row.add_suffix(rm)
            lg.add(row)

    def set_rate(self, idx):
        if self.loading:
            return
        before = copy.deepcopy(self.raw)
        if idx == 0:
            self.raw.pop("report_rate", None)
        else:
            self.raw["report_rate"] = sorted(P.RATES)[idx - 1]
        GLib.idle_add(lambda: (self.changed(before), False)[1])

    def dpi_edit(self, i=None, value=None, color=None, remove=False, add=False):
        if self.loading:
            return
        before = copy.deepcopy(self.raw)
        d = self.raw.setdefault("dpi", {})
        levels = d.setdefault("levels", [])
        steps = sorted(dpimod.table(self.sensor if self.sensor is not None else 1))
        if add:
            nxt = levels[-1] * 2 if levels and isinstance(levels[-1], int) else 800
            levels.append(min(steps, key=lambda s: abs(s - nxt)))
            cols = d.setdefault("colors", [])
            while len(cols) < len(levels):
                cols.append(COLORS[len(cols) % len(COLORS)])
        elif remove:
            levels.pop(i)
            if i < len(d.get("colors", [])):
                d["colors"].pop(i)
        elif value is not None:
            v = min(steps, key=lambda s: abs(s - value))
            lv = levels[i]
            levels[i] = [v, lv[1]] if isinstance(lv, list) else v
        elif color is not None:
            cols = d.setdefault("colors", [])
            while len(cols) < len(levels):
                cols.append(COLORS[len(cols) % len(COLORS)])
            cols[i] = color
        if not levels:
            self.raw.pop("dpi", None)
        elif not d.get("colors"):
            d.pop("colors", None)
        full = add or remove
        GLib.idle_add(lambda: (self.changed(before, full), False)[1])

    # -- file / mouse --
    def save(self):
        try:
            O.save_raw(self.path, self.raw)
        except (OSError, O.OpsError) as e:
            self.toast(f"Save failed: {e}", 8)
            return False
        self.saved = copy.deepcopy(self.raw)
        self.refresh()
        return True

    def set_busy(self, busy):
        self.busy = busy
        self.refresh()

    def need_mouse(self):
        self.detect()
        if not self.dev:
            self.toast("No M913 found: plug in the cable or the dongle")
            return False
        return True

    def read_mouse(self):
        if not self.need_mouse():
            return

        def go():
            with D.Mouse(self.dev["node"]) as m:
                return O.read_config(m, self.sensor)

        def done(res, err):
            self.set_busy(False)
            if err:
                self.toast(str(err), 8)
                return
            cfg, notes = res
            self.raw = merge_from_mouse(self.raw, cfg)
            self.refresh()
            self.toast("Loaded the mouse's active profile" +
                       (f" ({len(notes)} parts not understood)" if notes else ""))

        if self.raw != self.saved:
            dlg = Adw.AlertDialog(heading="Replace unsaved changes?",
                                  body="What the mouse holds now replaces what's on screen.")
            dlg.add_response("cancel", "Cancel")
            dlg.add_response("read", "Read mouse")
            dlg.set_response_appearance("read", Adw.ResponseAppearance.DESTRUCTIVE)
            dlg.connect("response", lambda _d, r: r == "read" and (
                self.set_busy(True), worker(go, done)))
            dlg.present(self)
            return
        self.set_busy(True)
        worker(go, done)

    # -- backup / restore --
    def ensure_baseline_backup(self):
        """On first run with a mouse and no backups yet, save one quietly as the
        baseline — the closest thing to the mouse's original state."""
        if self.dev is None or O.list_backups():
            return False
        dev, sensor = self.dev, self.sensor

        def go():
            with D.Mouse(dev["node"]) as m:
                return O.make_backup(m, dev, sensor)

        def done(path, err):
            if err or not path:
                return  # best-effort; a manual Read/Apply will back up anyway
            self.toast("Saved a baseline backup of your mouse's current settings")
        worker(go, done)
        return False

    def restore_dialog(self):
        dlg = Adw.AlertDialog(
            heading="Restore the mouse",
            body="Write a saved backup back onto the mouse. The earliest backup "
                 "is your baseline — the closest thing to its original state. "
                 "(The mouse doesn't expose Redragon's factory defaults, so there "
                 "is no true factory reset.)")
        group = Adw.PreferencesGroup()
        backups = O.list_backups()
        if not backups:
            group.add(Adw.ActionRow(title="No backups yet",
                                    subtitle="One is saved automatically the first time "
                                             "you read or apply."))
        for i, b in enumerate(reversed(backups)):  # newest first
            baseline = b is backups[0]
            row = Adw.ActionRow(title="Baseline (first backup)" if baseline else b["when"],
                                subtitle=b["name"] + ("  ·  closest to original" if baseline
                                                      else ""))
            btn = Gtk.Button(label="Restore", valign=Gtk.Align.CENTER)
            btn.add_css_class("destructive-action" if baseline else "flat")
            btn.connect("clicked", lambda _b, p=b["path"], n=b["name"]:
                        (dlg.close(), self.confirm_restore(p, n)))
            row.add_suffix(btn)
            row.set_activatable_widget(btn)
            group.add(row)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.append(group)
        from_file = Gtk.Button(label="Restore from a file…")
        from_file.add_css_class("flat")
        from_file.connect("clicked", lambda *_: (dlg.close(), self.restore_from_file()))
        box.append(from_file)
        dlg.set_extra_child(box)
        dlg.add_response("cancel", "Close")
        dlg.present(self)

    def restore_from_file(self):
        dialog = Gtk.FileDialog(title="Choose a backup")
        f = Gtk.FileFilter()
        f.set_name("m913ctl backup (*.json)")
        f.add_pattern("*.json")
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(f)
        dialog.set_filters(store)

        def done(d, res):
            try:
                gf = d.open_finish(res)
            except GLib.Error:
                return
            self.confirm_restore(gf.get_path(), gf.get_basename())
        dialog.open(self, None, done)

    def confirm_restore(self, path, name):
        try:
            regions = O.load_backup(path)
        except O.OpsError as e:
            self.toast(str(e), 8)
            return
        if not self.need_mouse():
            return
        dlg = Adw.AlertDialog(
            heading="Restore this backup to the mouse?",
            body=f"{name}\n\nThis overwrites the mouse's current buttons, macros, DPI "
                 "and lighting with the saved state, then reads them back to confirm.")
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("restore", "Restore")
        dlg.set_response_appearance("restore", Adw.ResponseAppearance.DESTRUCTIVE)
        dev = self.dev

        def go():
            with D.Mouse(dev["node"]) as m:
                return O.restore(m, regions)

        def done(res, err):
            self.set_busy(False)
            if err:
                self.toast(str(err), 8)
                return
            changed, bad = res
            if bad:
                self.toast("Some blocks did not verify: "
                           + ", ".join(w.label for w, _ in bad), 0)
            elif changed:
                self.toast(f"Restored {name} ({changed} block(s))")
            else:
                self.toast("The mouse already matches that backup")

        dlg.connect("response", lambda _d, r: r == "restore" and (
            self.set_busy(True), worker(go, done)))
        dlg.present(self)

    def apply(self):
        err = check(self.raw, self.sensor)
        if err or not self.need_mouse():
            return
        settings, commands = O.settings_from_raw(self.raw)
        dev, sensor = self.dev, self.sensor

        def plan():
            with D.Mouse(dev["node"]) as m:
                todo = O.plan(m, settings, sensor)
                return todo, (O.left_click_survives(m, [w for w, _ in todo]) if todo else True)

        def planned(res, err):
            if err:
                self.set_busy(False)
                self.toast(str(err), 8)
                return
            todo, left_ok = res
            if not todo:
                self.set_busy(False)
                self.finish("The mouse already matches", commands)
                return
            body = "\n".join(f"• {w.label}" for w, _ in todo)
            if not left_ok:
                body = "⚠ No button would be left click after this!\n\n" + body
            dlg = Adw.AlertDialog(heading=f"Write {len(todo)} change(s) to the mouse?",
                                  body=body)
            dlg.set_body_use_markup(False)
            dlg.add_response("cancel", "Cancel")
            dlg.add_response("write", "Write")
            dlg.set_response_appearance("write", Adw.ResponseAppearance.DESTRUCTIVE
                                        if not left_ok else Adw.ResponseAppearance.SUGGESTED)
            dlg.set_close_response("cancel")

            def resp(_d, r):
                if r != "write":
                    self.set_busy(False)
                    return
                worker(lambda: write(todo), written)

            dlg.connect("response", resp)
            dlg.present(self)

        def write(todo):
            with D.Mouse(dev["node"]) as m:
                return O.execute(m, dev, sensor, todo)

        def written(res, err):
            self.set_busy(False)
            if err:
                self.toast(str(err), 8)
                return
            path, bad = res
            if bad:
                self.toast("Some fields did not verify: " + ", ".join(w.label for w, _ in bad)
                           + f". Backup: {path}", 0)
                return
            self.finish("Written and verified", commands)

        self.set_busy(True)
        worker(plan, planned)

    def finish(self, msg, commands):
        if not self.save():
            return
        if commands:
            msg += " · command buttons type into the focused window"
        self.toast(msg)

    def on_close(self, *_):
        if self.raw == self.saved:
            return False
        dlg = Adw.AlertDialog(heading="Discard unsaved changes?")
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("discard", "Discard")
        dlg.add_response("save", "Save")
        dlg.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)

        def resp(_d, r):
            if r == "save" and self.save():
                self.destroy()
            elif r == "discard":
                self.saved = copy.deepcopy(self.raw)
                self.destroy()

        dlg.connect("response", resp)
        dlg.present(self)
        return True


CSS = (".m913-swatch { min-width: 20px; min-height: 20px; padding: 0;"
       " box-shadow: inset 0 0 0 1px alpha(currentColor, 0.25); }\n"
       ".m913-active { box-shadow: 0 0 0 2px @window_bg_color, 0 0 0 4px @accent_color; }\n"
       ".m913-active-btn { box-shadow: inset 0 0 0 2px @accent_color; }\n") + "".join(
    f".m913-c{hx} {{ background: #{hx}; }}\n" for hx in COLORS)


def run(path, device=None, sensor=None):
    try:
        O.load_raw(path)
    except O.OpsError as e:
        print(f"m913ctl: {e}", file=sys.stderr)
        return 1
    app = Adw.Application(application_id=APP_ID)

    def activate(a):
        css = Gtk.CssProvider()
        if hasattr(css, "load_from_string"):  # GTK >= 4.12
            css.load_from_string(CSS)
        else:
            css.load_from_data(CSS, -1)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        win = Window(a, path, device, sensor)
        win.present()

    app.connect("activate", activate)
    return app.run([sys.argv[0]])
