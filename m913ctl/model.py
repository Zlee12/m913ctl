"""Settings <-> bytes in the mouse's memory.

`encode(settings, sensor)` turns validated settings into a list of `Write`s.
`decode(mem, sensor)` turns memory read from the mouse back into a settings
dict with the same shape the config file uses, so `dump` output can be fed
straight back into `apply`.
"""

from collections import Counter
from dataclasses import dataclass

from . import dpi as dpimod
from . import keys as K
from . import protocol as P


@dataclass
class Write:
    label: str
    addr: int
    data: bytes
    chunk: int = P.MAX_CHUNK


class ConfigError(ValueError):
    pass


# --- button actions ----------------------------------------------------------

SIMPLE = {
    "none": (0x00, 0x00, 0x00), "disable": (0x00, 0x00, 0x00),
    "left": (0x01, 0x01, 0x00), "right": (0x01, 0x02, 0x00),
    "middle": (0x01, 0x04, 0x00), "back": (0x01, 0x08, 0x00),
    "forward": (0x01, 0x10, 0x00),
    "dpi_loop": (0x02, 0x01, 0x00), "dpi_up": (0x02, 0x02, 0x00),
    "dpi_down": (0x02, 0x03, 0x00),
    "double_click": (0x04, 0x32, 0x02), "triple_click": (0x04, 0x32, 0x03),
    "rate_switch": (0x07, 0x00, 0x00), "led_toggle": (0x08, 0x00, 0x00),
    "profile_switch": (0x09, 0x00, 0x00),
}
SIMPLE_NAMES = {}
for _n, _v in SIMPLE.items():
    SIMPLE_NAMES.setdefault(_v, _n)

REPEAT_HOLD = 0xFF
REPEAT_UNTIL_KEY = 0xFE


@dataclass
class KeySlot:
    events: list  # [(type, lo, hi)]


@dataclass
class MacroRef:
    name: str


def _key_events_tap(name):
    t, code = K.lookup(name)
    lo, hi = code & 0xFF, code >> 8
    return [(t, lo, hi), (K.DOWN_TO_UP[t], lo, hi)]


def parse_action(text: str):
    """Config string -> (type, b1, b2) | KeySlot | MacroRef."""
    try:
        return _parse_action(text)
    except K.KeyError_ as e:
        raise ConfigError(str(e)) from None


def _parse_action(text: str):
    s = text.strip()
    low = s.lower()
    if low in SIMPLE:
        return SIMPLE[low]
    kind, _, arg = s.partition(":")
    kind = kind.strip().lower()
    arg = arg.strip()
    if kind == "fire":
        parts = arg.split(":")
        try:
            times, speed = (int(parts[0]), int(parts[1])) if len(parts) == 2 else (3, 20)
        except ValueError:
            raise ConfigError(f"fire needs fire:<times 0-3>:<speed 3-255>, got {text!r}")
        if not (0 <= times <= 3 and 3 <= speed <= 255):
            raise ConfigError(f"fire times must be 0-3 and speed 3-255, got {text!r}")
        return (0x04, speed, times)
    if kind == "key":
        return KeySlot(_key_events_tap(arg))
    if kind == "media":
        if arg.lower() not in K.MEDIA:
            raise ConfigError(f"unknown media key {arg!r} (see `m913ctl keys`)")
        return KeySlot(_key_events_tap(arg))
    if kind == "combo":
        names = [n for n in arg.replace(" ", "").split("+") if n]
        if not names:
            raise ConfigError(f"empty combo in {text!r}")
        mods, plain = [], []
        for n in names:
            t, code = K.lookup(n)
            if t == K.EV_MEDIA_DOWN:
                raise ConfigError(f"media keys can't be part of a combo: {text!r}")
            (mods if t == K.EV_MOD_DOWN else plain).append((t, code))
        down = mods + plain
        ev = [(t, c, 0) for t, c in down]
        ev += [(K.DOWN_TO_UP[t], c, 0) for t, c in mods]
        ev += [(K.DOWN_TO_UP[t], c, 0) for t, c in reversed(plain)]
        return KeySlot(ev)
    if kind == "macro":
        if not arg:
            raise ConfigError("macro: needs a macro name")
        return MacroRef(arg)
    if kind == "raw":
        try:
            b = bytes.fromhex(arg)
        except ValueError:
            b = b""
        if len(b) != 3:
            raise ConfigError(f"raw needs 3 hex bytes, e.g. raw:01 08 00, got {text!r}")
        if b[0] in (0x05, 0x06):
            raise ConfigError("raw key-slot/macro records are not allowed; use key:/combo:/macro:")
        return tuple(b)
    raise ConfigError(f"unknown action {text!r} (see `m913ctl actions`)")


# --- macros ------------------------------------------------------------------

def parse_macro(name, spec):
    """[macros.<name>] table -> (repeat_byte, [(type, code, delay)])."""
    if not isinstance(spec, dict):
        raise ConfigError(f"macros.{name} must be a table")
    unknown = set(spec) - {"repeat", "delay", "events"}
    if unknown:
        raise ConfigError(f"macros.{name}: unknown keys {sorted(unknown)}")
    repeat = spec.get("repeat", 1)
    if repeat == "hold":
        rb = REPEAT_HOLD
    elif repeat == "until_key":
        rb = REPEAT_UNTIL_KEY
    elif isinstance(repeat, int) and not isinstance(repeat, bool) and 1 <= repeat <= 253:
        rb = repeat
    else:
        raise ConfigError(f'macros.{name}.repeat must be 1-253, "hold" or "until_key"')
    d = spec.get("delay", 20)
    if not isinstance(d, int) or isinstance(d, bool) or not (P.MACRO_MIN_DELAY <= d <= 0xFFFF):
        raise ConfigError(f"macros.{name}.delay must be {P.MACRO_MIN_DELAY}-65535 ms")
    lines = spec.get("events")
    if not isinstance(lines, list) or not lines:
        raise ConfigError(f"macros.{name}.events must be a non-empty list of strings")

    events = []  # [type, code, delay]
    held = set()

    def add(t, code):
        if t == K.EV_MEDIA_DOWN or t == K.EV_MEDIA_UP:
            raise ConfigError(f"macros.{name}: media keys are only supported as button actions")
        events.append([t, code, d])
        key = (t & 0x0F | 0x80, code)
        if t & 0x80:
            held.add(key)
        else:
            held.discard(key)

    def press(n, up=False, down=True):
        try:
            t, code = K.lookup(n)
        except K.KeyError_ as e:
            raise ConfigError(f"macros.{name}: {e}")
        if code > 0xFF:
            raise ConfigError(f"macros.{name}: media keys are only supported as button actions")
        if down:
            add(t, code)
        if up:
            add(K.DOWN_TO_UP[t], code)

    for raw in lines:
        if not isinstance(raw, str):
            raise ConfigError(f"macros.{name}: events must be strings")
        verb, _, rest = raw.strip().partition(" ")
        verb = verb.lower()
        rest = rest.strip()
        if verb == "delay":
            if not events:
                raise ConfigError(f"macros.{name}: a macro can't start with a delay")
            try:
                ms = int(rest)
            except ValueError:
                raise ConfigError(f"macros.{name}: bad delay {raw!r}")
            if not (P.MACRO_MIN_DELAY <= ms <= 0xFFFF):
                raise ConfigError(f"macros.{name}: delay must be {P.MACRO_MIN_DELAY}-65535 ms")
            events[-1][2] = ms
        elif verb == "down":
            press(rest)
        elif verb == "up":
            press(rest, up=True, down=False)
        elif verb == "tap":
            press(rest, up=True)
        elif verb == "combo":
            parts = [p for p in rest.replace(" ", "").split("+") if p]
            for p in parts:
                press(p)
            for p in reversed(parts):
                press(p, up=True, down=False)
        elif verb == "type":
            text = raw.strip()[5:]
            try:
                steps = K.type_text(text)
            except K.KeyError_ as e:
                raise ConfigError(f"macros.{name}: {e}")
            for shift, k in steps:
                if shift:
                    press("shift")
                press(k, up=True)
                if shift:
                    press("shift", up=True, down=False)
        else:
            raise ConfigError(
                f"macros.{name}: unknown event {raw!r} (down/up/tap/combo/type/delay)")
    if held:
        raise ConfigError(f"macros.{name}: keys left pressed at the end: "
                          + ", ".join(K.name_of(t, c) or hex(c) for t, c in sorted(held)))
    if len(events) > P.MACRO_MAX_EVENTS:
        raise ConfigError(f"macros.{name}: {len(events)} events, at most {P.MACRO_MAX_EVENTS}")
    return rb, [tuple(e) for e in events]


def macro_bytes(events) -> bytes:
    """Macro slot payload. The name area is left empty (see PROTOCOL.md)."""
    buf = bytearray(P.MACRO_HEADER)
    buf[0x1F] = len(events)
    for t, code, delay in events:
        buf += bytes([t, code, 0, delay >> 8, delay & 0xFF])
    return bytes(buf) + bytes([P.chk(buf), 0, 0, 0])


def keyslot_bytes(events) -> bytes:
    if len(events) > P.KEYSLOT_MAX_EVENTS:
        raise ConfigError(f"a key action holds at most {P.KEYSLOT_MAX_EVENTS} key events")
    body = bytes([len(events)]) + b"".join(bytes(e) for e in events)
    return body + bytes([P.chk(body)])


# --- config validation -------------------------------------------------------

TOP_KEYS = {"report_rate", "dpi", "led", "buttons", "macros"}


def _color(v, where):
    if isinstance(v, str):
        h = v.lstrip("#")
        if len(h) == 6:
            try:
                return tuple(bytes.fromhex(h))
            except ValueError:
                pass
    raise ConfigError(f"{where}: colours are hex strings like \"ff8800\"")


def validate(cfg: dict) -> dict:
    """Check a parsed TOML dict and return normalised settings."""
    unknown = set(cfg) - TOP_KEYS
    if unknown:
        raise ConfigError(f"unknown top-level keys: {sorted(unknown)}")
    out = {}
    if "report_rate" in cfg:
        if cfg["report_rate"] not in P.RATES:
            raise ConfigError(f"report_rate must be one of {sorted(P.RATES)}")
        out["report_rate"] = cfg["report_rate"]

    if "dpi" in cfg:
        d = cfg["dpi"]
        if not isinstance(d, dict) or set(d) - {"levels", "colors"}:
            raise ConfigError("[dpi] takes levels = [...] and optional colors = [...]")
        if "levels" in d:
            lv = d["levels"]
            if not isinstance(lv, list) or not 1 <= len(lv) <= P.DPI_LEVELS:
                raise ConfigError(f"dpi.levels needs 1-{P.DPI_LEVELS} entries")
            levels = []
            for x in lv:
                if isinstance(x, int) and not isinstance(x, bool):
                    levels.append((x, x))
                elif isinstance(x, list) and len(x) == 2 and all(isinstance(i, int) for i in x):
                    levels.append((x[0], x[1]))
                else:
                    raise ConfigError("dpi.levels entries are 1600 or [1600, 800] (x, y)")
            out["dpi_levels"] = levels
        if "colors" in d:
            cl = d["colors"]
            if not isinstance(cl, list) or not 1 <= len(cl) <= P.DPI_LEVELS:
                raise ConfigError(f"dpi.colors needs 1-{P.DPI_LEVELS} entries")
            out["dpi_colors"] = [_color(c, "dpi.colors") for c in cl]

    if "led" in cfg:
        led = cfg["led"]
        if not isinstance(led, dict) or set(led) - {"mode", "color", "brightness", "speed"}:
            raise ConfigError("[led] takes mode, color, brightness, speed")
        mode = P.LED_MODE_ALIASES.get(led.get("mode", "static"), led.get("mode", "static"))
        if mode not in P.LED_MODES:
            raise ConfigError(f"led.mode must be one of {list(P.LED_MODES)}")
        b = led.get("brightness", 10)
        if not isinstance(b, int) or not 1 <= b <= 10:
            raise ConfigError("led.brightness is 1-10")
        sp = led.get("speed", 3)
        if not isinstance(sp, int) or not 1 <= sp <= 10:
            raise ConfigError("led.speed is 1-10")
        out["led"] = {"mode": mode, "color": _color(led.get("color", "ff0000"), "led.color"),
                      "brightness": b, "speed": sp}

    macros = {}
    if "macros" in cfg:
        if not isinstance(cfg["macros"], dict):
            raise ConfigError("[macros.<name>] tables expected")
        if len(cfg["macros"]) > P.N_MACROS:
            raise ConfigError(f"at most {P.N_MACROS} macros")
        for name, spec in cfg["macros"].items():
            if not name or not all(c.isalnum() or c in "_-" for c in name):
                raise ConfigError(f"macro name {name!r}: use letters, digits, _ and -")
            macros[name] = parse_macro(name, spec)
    out["macros"] = macros

    buttons = {}
    if "buttons" in cfg:
        if not isinstance(cfg["buttons"], dict):
            raise ConfigError("[buttons] must be a table")
        for bname, action in cfg["buttons"].items():
            if bname not in P.BUTTONS:
                raise ConfigError(f"unknown button {bname!r}; buttons are {', '.join(P.BUTTONS)}")
            if not isinstance(action, str):
                raise ConfigError(f"buttons.{bname} must be a string")
            a = parse_action(action)
            if isinstance(a, MacroRef) and a.name not in macros:
                raise ConfigError(f"buttons.{bname}: no [macros.{a.name}] defined")
            if isinstance(a, KeySlot) and len(a.events) > P.KEYSLOT_MAX_EVENTS:
                raise ConfigError(f"buttons.{bname}: more than {P.KEYSLOT_MAX_EVENTS} key events")
            buttons[bname] = a
    out["buttons"] = buttons
    return out


# --- encoding ----------------------------------------------------------------

def encode(s: dict, sensor) -> list:
    w = []
    if "report_rate" in s:
        w.append(Write("report rate", P.ADDR_RATE, P.field(P.RATES[s["report_rate"]])))
    if "dpi_levels" in s:
        if sensor is None:
            raise ConfigError("sensor unknown; pass --sensor 3325 or 3335 to set DPI")
        lv = s["dpi_levels"]
        w.append(Write("dpi level count", P.ADDR_DPI_COUNT, P.field(len(lv))))
        data = b""
        for x, y in lv:
            try:
                cx, flag = dpimod.encode(sensor, x)
                cy, fy = dpimod.encode(sensor, y)
            except dpimod.DpiError as e:
                raise ConfigError(str(e))
            if fy != flag:
                raise ConfigError(f"dpi {x}x{y}: on this sensor X and Y must both be "
                                  "above or both at/below 5000")
            # the mouse takes the high-range flag from X only
            data += P.field(cx, cy, flag)
        w.append(Write("dpi levels", P.ADDR_DPI, data, 8))
    if "dpi_colors" in s:
        data = b"".join(P.field(*c) for c in s["dpi_colors"])
        w.append(Write("dpi colours", P.ADDR_DPI_COLOR, data, 8))
    if "led" in s:
        led = s["led"]
        mode = P.LED_MODES[led["mode"]]
        if mode == 0:
            w.append(Write("led mode", P.ADDR_LED_MODE, P.field(0)))
        else:
            bright = P.BRIGHTNESS[led["brightness"] - 1]
            data = P.field(*led["color"]) + P.field(mode) + P.field(bright)
            w.append(Write("led", P.ADDR_LED, data, 8))
            if mode in (2, 3):
                w.append(Write("led speed", P.ADDR_LED_SPEED, P.field(led["speed"])))

    slots = {name: i for i, name in enumerate(s.get("macros", {}))}
    for name, (_, events) in s.get("macros", {}).items():
        w.append(Write(f"macro {name} (slot {slots[name]})",
                       P.ADDR_MACRO + P.MACRO_SIZE * slots[name], macro_bytes(events)))
    for bname, a in s.get("buttons", {}).items():
        i = P.BUTTONS.index(bname)
        if isinstance(a, KeySlot):
            w.append(Write(f"{bname} key action", P.ADDR_KEYSLOT + P.KEYSLOT_SIZE * i,
                           keyslot_bytes(a.events)))
            rec = (0x05, i, 0x00)
        elif isinstance(a, MacroRef):
            rec = (0x06, slots[a.name], s["macros"][a.name][0])
        else:
            rec = a
        w.append(Write(bname, P.ADDR_BUTTONS + 4 * i, P.field(*rec)))
    return w


# --- decoding ----------------------------------------------------------------

class Mem:
    """Sparse view of the mouse's memory: mem[addr:addr+n] -> bytes or None."""

    def __init__(self, regions=None):
        self.b = {}
        for addr, data in (regions or {}).items():
            self.put(addr, data)

    def put(self, addr, data):
        for i, v in enumerate(data):
            self.b[addr + i] = v

    def get(self, addr, n):
        try:
            return bytes(self.b[addr + i] for i in range(n))
        except KeyError:
            return None


def _ok(f):
    return f is not None and P.chk(f[:-1]) == f[-1]


def _hex(rgb):
    return "".join(f"{v:02x}" for v in rgb)


def decode_keyslot(data):
    """Key slot bytes -> action string (or None if unrecognised)."""
    if not data or data[0] == 0 or data[0] > P.KEYSLOT_MAX_EVENTS:
        return None
    n = data[0]
    body = data[:1 + 3 * n + 1]
    if len(body) < 3 * n + 2 or P.chk(body[:-1]) != body[-1]:
        return None
    ev = [tuple(body[1 + 3 * k:4 + 3 * k]) for k in range(n)]
    if n == 2 and ev[0][0] in K.DOWN_TO_UP and ev[1] == (K.DOWN_TO_UP[ev[0][0]],) + ev[0][1:]:
        t, lo, hi = ev[0]
        name = K.name_of(t, lo | hi << 8)
        if name:
            return ("media:" if t == K.EV_MEDIA_DOWN else "key:") + name
    downs = [e for e in ev if e[0] & 0x80]
    if downs and all(e[2] == 0 for e in ev) and len(downs) * 2 == n:
        names = [K.name_of(t, c) for t, c, _ in downs]
        if all(names) and parse_action("combo:" + "+".join(names)).events == ev:
            return "combo:" + "+".join(names)
    return None


def decode_macro(data):
    """Macro slot bytes -> (events list of strings, delay) or None."""
    if data is None or len(data) < P.MACRO_HEADER:
        return None
    n = data[0x1F]
    if n == 0 or n > 69 or data[0] > 30:
        return None
    end = P.MACRO_HEADER + 5 * n
    if len(data) < end + 1:
        return None
    raw = [data[P.MACRO_HEADER + 5 * k:P.MACRO_HEADER + 5 * k + 5] for k in range(n)]
    delays = [(e[3] << 8) | e[4] for e in raw]
    common = Counter(delays).most_common(1)[0][0]
    lines = []
    for e, dl in zip(raw, delays):
        t, code = e[0], e[1]
        down = t & 0x80
        name = K.name_of((t & 0x0F) | 0x80, code)
        if name is None:
            return None
        lines.append(("down " if down else "up ") + name)
        if dl != common:
            lines.append(f"delay {dl}")
    return lines, common


def regions_for_decode():
    """(addr, length) ranges `decode` needs, excluding macro slots."""
    return [(0x00, 0xA0), (P.ADDR_KEYSLOT, P.KEYSLOT_SIZE * P.N_BUTTONS)]


def decode(mem: Mem, sensor, macro_data: dict):
    """Memory -> (settings dict shaped like the config file, list of notes)."""
    cfg, notes = {}, []
    f = mem.get(P.ADDR_RATE, 2)
    if _ok(f) and f[0] in P.RATE_CODES:
        cfg["report_rate"] = P.RATE_CODES[f[0]]
    f = mem.get(P.ADDR_DPI_COUNT, 2)
    if _ok(f) and 1 <= f[0] <= P.DPI_LEVELS:
        n = f[0]
        levels, colors = [], []
        for i in range(n):
            lf = mem.get(P.ADDR_DPI + 4 * i, 4)
            x = y = None
            if _ok(lf) and sensor is not None:
                try:
                    x = dpimod.decode(sensor, lf[0], lf[2])
                    y = dpimod.decode(sensor, lf[1], lf[2])
                except dpimod.DpiError:
                    pass
            if x is None or y is None:
                notes.append(f"dpi level {i + 1}: raw {lf.hex(' ') if lf else '?'} "
                             "(unknown code or sensor)")
                levels = None
                break
            levels.append(x if x == y else [x, y])
            cf = mem.get(P.ADDR_DPI_COLOR + 4 * i, 4)
            colors.append(_hex(cf[:3]) if _ok(cf) else None)
        cfg["dpi"] = {}
        if levels is not None:
            cfg["dpi"]["levels"] = levels
        if all(colors) and len(colors) == n:
            cfg["dpi"]["colors"] = colors
        if not cfg["dpi"]:
            del cfg["dpi"]
    led = mem.get(P.ADDR_LED, 10)
    if led and _ok(led[4:6]):
        mode = led[4]
        if mode in P.LED_MODE_NAMES:
            out = {"mode": P.LED_MODE_NAMES[mode]}
            if mode:
                if _ok(led[0:4]):
                    out["color"] = _hex(led[:3])
                if _ok(led[6:8]):
                    b = led[6]
                    out["brightness"] = min(range(10), key=lambda k: abs(P.BRIGHTNESS[k] - b)) + 1
                if mode in (2, 3) and _ok(led[8:10]):
                    out["speed"] = led[8]
            cfg["led"] = out
    buttons, macros = {}, {}
    for i, bname in enumerate(P.BUTTONS):
        rec = mem.get(P.ADDR_BUTTONS + 4 * i, 4)
        if not _ok(rec):
            notes.append(f"{bname}: unreadable record {rec.hex(' ') if rec else '?'}")
            continue
        t, b1, b2 = rec[:3]
        if t == 0x05:
            a = decode_keyslot(mem.get(P.ADDR_KEYSLOT + P.KEYSLOT_SIZE * b1, P.KEYSLOT_SIZE)
                               if b1 < P.N_BUTTONS else None)
            if a is None:
                notes.append(f"{bname}: key action in slot {b1} not understood")
                continue
            buttons[bname] = a
        elif t == 0x06:
            dec = decode_macro(macro_data.get(b1))
            if dec is None:
                notes.append(f"{bname}: macro slot {b1} not understood")
                continue
            lines, delay = dec
            mname = f"slot{b1}"
            spec = {"events": lines}
            if b2 == REPEAT_HOLD:
                spec["repeat"] = "hold"
            elif b2 == REPEAT_UNTIL_KEY:
                spec["repeat"] = "until_key"
            else:
                spec["repeat"] = b2
                if b2 == 0:
                    notes.append(f"{bname}: macro repeat byte is 0 (meaning unknown)")
            spec["delay"] = delay
            macros[mname] = spec
            buttons[bname] = f"macro:{mname}"
        elif (t, b1, b2) in SIMPLE_NAMES:
            buttons[bname] = SIMPLE_NAMES[(t, b1, b2)]
        elif t == 0x04:
            buttons[bname] = f"fire:{b2}:{b1}"
        else:
            buttons[bname] = f"raw:{t:02x} {b1:02x} {b2:02x}"
    if buttons:
        cfg["buttons"] = buttons
    if macros:
        cfg["macros"] = macros
    return cfg, notes


def macro_slots_used(mem: Mem):
    used = set()
    for i in range(P.N_BUTTONS):
        rec = mem.get(P.ADDR_BUTTONS + 4 * i, 4)
        if _ok(rec) and rec[0] == 0x06 and rec[1] < P.N_MACROS:
            used.add(rec[1])
    return sorted(used)


# --- TOML output (tiny writer for our own shapes) ---------------------------

_ESC = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\t": "\\t", "\r": "\\r"}


def _tv(v):
    if isinstance(v, str):
        out = "".join(_ESC.get(c, c if ord(c) >= 0x20 and c != "\x7f" else f"\\u{ord(c):04x}")
                      for c in v)
        return '"' + out + '"'
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, list):
        return "[" + ", ".join(_tv(x) for x in v) + "]"
    return str(v)


def to_toml(cfg: dict) -> str:
    lines = []
    for k, v in cfg.items():
        if not isinstance(v, dict):
            lines.append(f"{k} = {_tv(v)}")
    for k, v in cfg.items():
        if not isinstance(v, dict) or k == "macros":
            continue
        lines += ["", f"[{k}]"]
        for kk, vv in v.items():
            key = kk if kk.replace("_", "").isalnum() else _tv(kk)
            lines.append(f"{key} = {_tv(vv)}")
    for name, spec in cfg.get("macros", {}).items():
        lines += ["", f"[macros.{name}]"]
        for kk in ("repeat", "delay"):
            if kk in spec:
                lines.append(f"{kk} = {_tv(spec[kk])}")
        lines.append("events = [")
        lines += [f"  {_tv(e)}," for e in spec["events"]]
        lines.append("]")
    return "\n".join(lines).lstrip("\n") + "\n"
