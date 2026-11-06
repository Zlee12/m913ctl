"""Put a shell command on a side button, stored entirely on the mouse.

The mouse can't run programs, but it can store a macro that *types* text. A
`[commands]` entry becomes a macro that types the command and presses Enter,
so pressing the button types e.g. `sudo pacman -Syu` into whatever window has
focus (open a terminal first). It lives on the mouse; nothing runs on the PC
and it works on any desktop.

One macro slot holds ~31 plain characters (fewer with capitals or symbols);
`m913ctl` refuses a command too long to fit. The mouse has 16 slots, shared
with ordinary macros.
"""

from . import keys as K
from . import protocol as P
from .model import ConfigError

# which buttons can hold a command
COMMAND_BUTTONS = {f"button_{n}" for n in range(1, 13)}

# name -> (description, command text). Short commands you would type in a
# terminal — long ones don't fit on a button (see the module docstring).
PRESETS = {
    "system_update": ("update the system (Arch)", "sudo pacman -Syu"),
    "lock": ("lock the session", "loginctl lock-session"),
    "suspend": ("suspend the computer", "systemctl suspend"),
    "clear": ("clear the terminal", "clear"),
    "exit": ("close the terminal", "exit"),
}


def expand_command(cmd: str) -> str:
    s = cmd.strip()
    if s.startswith("preset:"):
        name = s[7:].strip()
        if name not in PRESETS:
            raise ConfigError(f"unknown preset {name!r}; presets: {', '.join(PRESETS)}")
        s = PRESETS[name][1]
    return s


def macro_name(button: str) -> str:
    return "cmd" + button.split("_")[1]


def split(raw: dict):
    """Config dict -> (config dict with the commands turned into macros,
    {button: command text}). Each command button becomes a macro that types
    the command and presses Enter."""
    raw = dict(raw)
    cmds = raw.pop("commands", None)
    if cmds is None:
        cmds = raw.pop("scripts", None) or {}  # 0.2 name
    raw.pop("hypr", None)  # from 0.2 (Hyprland binds); gone
    if not isinstance(cmds, dict):
        raise ConfigError("[commands] must be a table of button = \"shell command\"")
    buttons = dict(raw.get("buttons", {}) or {})
    macros = dict(raw.get("macros", {}) or {})
    info = {}
    for b, cmd in cmds.items():
        if b not in COMMAND_BUTTONS:
            raise ConfigError(f"commands.{b}: only button_1 .. button_12 can hold a command")
        if not isinstance(cmd, str) or not cmd.strip():
            raise ConfigError(f"commands.{b} must be a non-empty command string")
        if "\n" in cmd:
            raise ConfigError(f"commands.{b}: a command is a single line")
        if b in buttons:
            raise ConfigError(f"{b} is in both [buttons] and [commands]; pick one")
        text = expand_command(cmd)
        try:
            steps = K.type_text(text)
        except K.KeyError_ as e:
            raise ConfigError(f"commands.{b}: {e}")
        events = sum(4 if shift else 2 for shift, _ in steps) + 2  # + Enter
        if events > P.MACRO_MAX_EVENTS:
            raise ConfigError(
                f"commands.{b}: {text!r} is too long to store on one button "
                f"({events} key events, limit {P.MACRO_MAX_EVENTS}; ~31 plain characters, "
                "fewer with capitals or symbols). Shorten it or use a script in your shell.")
        name = macro_name(b)
        if name in macros:
            raise ConfigError(f"commands.{b} needs macro slot {name}, which a [macros] "
                              "table already uses; rename that macro")
        macros[name] = {"events": [f"type {text}", "tap enter"]}
        buttons[b] = f"macro:{name}"
        info[b] = text
    if info:
        raw["buttons"] = buttons
        raw["macros"] = macros
    return raw, info
