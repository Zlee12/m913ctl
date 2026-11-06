"""Key names -> HID codes used in key slots and macros."""

import string

# HID keyboard usage page (0x07)
KEYS = {}
for i, ch in enumerate(string.ascii_lowercase):
    KEYS[ch] = 0x04 + i
for i, ch in enumerate("1234567890"):
    KEYS[ch] = 0x1E + i
KEYS.update({
    "enter": 0x28, "return": 0x28, "esc": 0x29, "escape": 0x29, "backspace": 0x2A,
    "tab": 0x2B, "space": 0x2C, "minus": 0x2D, "equal": 0x2E, "leftbrace": 0x2F,
    "rightbrace": 0x30, "backslash": 0x31, "semicolon": 0x33, "apostrophe": 0x34,
    "grave": 0x35, "comma": 0x36, "dot": 0x37, "period": 0x37, "slash": 0x38,
    "capslock": 0x39, "printscreen": 0x46, "print": 0x46, "scrolllock": 0x47,
    "pause": 0x48, "insert": 0x49, "home": 0x4A, "pageup": 0x4B, "delete": 0x4C,
    "end": 0x4D, "pagedown": 0x4E, "right": 0x4F, "left": 0x50, "down": 0x51,
    "up": 0x52, "numlock": 0x53, "kp_slash": 0x54, "kp_asterisk": 0x55,
    "kp_minus": 0x56, "kp_plus": 0x57, "kp_enter": 0x58, "kp_dot": 0x63,
    "102nd": 0x64, "menu": 0x65, "compose": 0x65,
})
for i in range(1, 13):
    KEYS[f"f{i}"] = 0x3A + i - 1
for i in range(13, 25):
    KEYS[f"f{i}"] = 0x68 + i - 13
for i in range(1, 10):
    KEYS[f"kp_{i}"] = 0x59 + i - 1
KEYS["kp_0"] = 0x62

# Modifier bits (HID boot report byte 0)
MODIFIERS = {
    "ctrl": 0x01, "lctrl": 0x01, "shift": 0x02, "lshift": 0x02, "alt": 0x04,
    "lalt": 0x04, "super": 0x08, "lsuper": 0x08, "meta": 0x08, "win": 0x08,
    "rctrl": 0x10, "rshift": 0x20, "ralt": 0x40, "altgr": 0x40, "rsuper": 0x80,
}

# Consumer page (0x0c) usages, 16 bit
MEDIA = {
    "play_pause": 0xCD, "next": 0xB5, "previous": 0xB6, "prev": 0xB6, "stop": 0xB7,
    "mute": 0xE2, "volume_up": 0xE9, "volume_down": 0xEA,
    "brightness_up": 0x6F, "brightness_down": 0x70,
    "mail": 0x18A, "calculator": 0x192, "file_browser": 0x194, "my_computer": 0x194,
    "www_search": 0x221, "www_home": 0x223, "www_back": 0x224, "www_forward": 0x225,
    "www_stop": 0x226, "www_refresh": 0x227, "www_favorites": 0x22A,
}

# Mouse button bits used in key slots / macros (event type 0x84 / 0x44)
MOUSE = {"mouse_left": 0x01, "mouse_right": 0x02, "mouse_middle": 0x04,
         "mouse_back": 0x08, "mouse_forward": 0x10}

EV_MOD_DOWN, EV_MOD_UP = 0x80, 0x40
EV_KEY_DOWN, EV_KEY_UP = 0x81, 0x41
EV_MEDIA_DOWN, EV_MEDIA_UP = 0x82, 0x42
EV_MOUSE_DOWN, EV_MOUSE_UP = 0x84, 0x44
DOWN_TO_UP = {0x80: 0x40, 0x81: 0x41, 0x82: 0x42, 0x84: 0x44}

# characters a "type" macro step understands (US layout)
SHIFTED = dict(zip('!@#$%^&*()_+{}|:"~<>?', "1234567890-=[]\\;'`,./"))
PLAIN_PUNCT = {"-": "minus", "=": "equal", "[": "leftbrace", "]": "rightbrace",
               "\\": "backslash", ";": "semicolon", "'": "apostrophe", "`": "grave",
               ",": "comma", ".": "dot", "/": "slash", " ": "space"}


class KeyError_(ValueError):
    pass


def _rev(d):
    out = {}
    for k, v in d.items():
        out.setdefault(v, k)
    return out


KEY_NAMES = _rev(KEYS)
MOD_NAMES = _rev(MODIFIERS)
MEDIA_NAMES = _rev(MEDIA)
MOUSE_NAMES = _rev(MOUSE)


def lookup(name: str):
    """Name -> (down_type, code). Raises KeyError_ for unknown names."""
    n = name.strip().lower()
    if n in MODIFIERS:
        return EV_MOD_DOWN, MODIFIERS[n]
    if n in KEYS:
        return EV_KEY_DOWN, KEYS[n]
    if n in MOUSE:
        return EV_MOUSE_DOWN, MOUSE[n]
    if n in MEDIA:
        return EV_MEDIA_DOWN, MEDIA[n]
    raise KeyError_(f"unknown key {name!r} (see `m913ctl keys`)")


def name_of(down_type: int, code: int):
    table = {EV_MOD_DOWN: MOD_NAMES, EV_KEY_DOWN: KEY_NAMES, EV_MEDIA_DOWN: MEDIA_NAMES,
             EV_MOUSE_DOWN: MOUSE_NAMES}.get(down_type)
    if table is None or code not in table:
        return None
    return table[code]


def type_text(text: str):
    """Text -> list of key names to tap, with shift handled. US layout."""
    steps = []
    for ch in text:
        if ch.isascii() and ch.isalpha():
            steps.append((ch.isupper(), ch.lower()))
        elif ch.isdigit():
            steps.append((False, ch))
        elif ch in PLAIN_PUNCT:
            steps.append((False, PLAIN_PUNCT[ch]))
        elif ch in SHIFTED:
            base = SHIFTED[ch]
            steps.append((True, base if base.isdigit() else PLAIN_PUNCT[base]))
        elif ch == "\n":
            steps.append((False, "enter"))
        else:
            raise KeyError_(f"cannot type {ch!r} (US layout ASCII only)")
    return steps
