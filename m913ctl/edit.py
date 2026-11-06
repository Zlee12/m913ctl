"""Config editing helpers shared by the TUI and the GTK window."""

from . import model as M
from . import ops as O
from . import scripts as S

# physical-ish order for the list
BUTTON_ORDER = ["left", "right", "middle", "fire"] + [f"button_{i}" for i in range(1, 13)]
SIMPLE_CHOICES = ["left", "right", "middle", "back", "forward", "none", "dpi_loop", "dpi_up",
                  "dpi_down", "double_click", "triple_click", "rate_switch", "led_toggle",
                  "profile_switch"]
KINDS = [
    ("mouse", "Mouse / DPI / special action"),
    ("command", "Type a command (into the focused window)"),
    ("key", "Single key"),
    ("combo", "Key combination"),
    ("media", "Media key"),
    ("macro", "Macro"),
    ("fire", "Rapid fire"),
    ("keep", "Not managed (leave the mouse as it is)"),
]
COLORS = ["ff0000", "ff8000", "ffff00", "00ff00", "00ffff", "0000ff", "8000ff", "ff00ff",
          "ffffff"]


def describe(raw, b):
    """(kind, text) for a button in the config dict."""
    cmds = raw.get("commands", {})
    if b in cmds:
        cmd = cmds[b]
        if cmd.startswith("preset:"):
            name = cmd[7:]
            return "command", f"{name} — {S.PRESETS.get(name, ('?',))[0]}"
        return "command", cmd
    a = raw.get("buttons", {}).get(b)
    if a is None:
        return "keep", "(not managed)"
    kind = a.split(":", 1)[0] if ":" in a else "mouse"
    return kind, a


def set_button(raw, b, kind, value):
    raw.setdefault("buttons", {})
    raw.setdefault("commands", {})
    raw["buttons"].pop(b, None)
    raw["commands"].pop(b, None)
    if kind == "command":
        raw["commands"][b] = value
    elif kind == "mouse":
        raw["buttons"][b] = value
    elif kind != "keep":
        raw["buttons"][b] = f"{kind}:{value}"
    for t in ("buttons", "commands"):
        if not raw[t]:
            del raw[t]


def check(raw, sensor):
    """None if the config is fine, else the error text."""
    try:
        s, _ = O.settings_from_raw(raw)
        M.encode(s, sensor if sensor is not None else 1)
    except M.ConfigError as e:
        return str(e)
    return None


def macro_to_text(name, spec):
    lines = [f"# macro {name}",
             "# repeat = 1..253 | hold | until_key ;  delay = default ms after each event",
             "# events, one per line: down <key> | up <key> | tap <key> | combo a+b |",
             "#   type <text> | delay <ms>     (`m913ctl keys` lists key names)",
             f"repeat = {spec.get('repeat', 1)}",
             f"delay = {spec.get('delay', 20)}", ""]
    lines += spec.get("events", [])
    return "\n".join(lines) + "\n"


def text_to_macro(name, text):
    spec, events = {}, []
    for ln in text.splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        key, eq, val = s.partition("=")
        if eq and key.strip() in ("repeat", "delay") and " " not in key.strip():
            v = val.strip().strip('"')
            spec[key.strip()] = int(v) if v.isdigit() else v
        else:
            events.append(s)
    spec["events"] = events
    M.parse_macro(name, spec)  # raises ConfigError
    return spec


def merge_from_mouse(raw, cfg):
    """Mouse settings replace the config's, but [commands] entries are kept for
    buttons whose macro on the mouse still matches the command's typed macro."""
    new = {k: v for k, v in cfg.items()}
    cmds = {}
    for b, cmd in raw.get("commands", {}).items():
        if new.get("buttons", {}).get(b) == f"macro:{S.macro_name(b)}":
            cmds[b] = cmd
            del new["buttons"][b]
            new.get("macros", {}).pop(S.macro_name(b), None)
    if cmds:
        new["commands"] = cmds
    if new.get("macros") == {}:
        new.pop("macros")
    return new
