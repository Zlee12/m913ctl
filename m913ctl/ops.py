"""Operations shared by the command line, the TUI and the GTK window."""

import json
import os
import time
import tomllib
from pathlib import Path

from . import device as D
from . import dpi as dpimod
from . import model as M
from . import protocol as P
from . import scripts as S

LEFT_CLICK = P.field(*M.SIMPLE["left"])


class OpsError(RuntimeError):
    pass


def state_dir():
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return os.path.join(base, "m913ctl")


def config_path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "m913ctl" / "config.toml"


# -- config files ----------------------------------------------------------------

def load_raw(path):
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return {}
    except OSError as e:
        raise OpsError(f"{path}: {e.strerror}")
    except tomllib.TOMLDecodeError as e:
        raise OpsError(f"{path}: {e}")


def save_raw(path, raw: dict):
    """Write a config dict back as TOML (comments are not kept)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "# managed by m913ctl; `m913ctl actions` lists what goes where\n\n" + M.to_toml(raw)
    tomllib.loads(text)  # never write something we can't read back
    tmp = path.with_suffix(".toml.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def settings_from_raw(raw: dict):
    """Config dict -> (validated mouse settings, {button: command})."""
    mouse_raw, scripts = S.split(raw)
    return M.validate(mouse_raw), scripts


# -- device ------------------------------------------------------------------------

def find_device(node=None, sensor=None):
    """-> (dev dict, sensor id or None). sensor overrides detection."""
    devs = D.find()
    if node:
        hit = [d for d in devs if d["node"] == node]
        dev = hit[0] if hit else {"node": node, "pid": None, "bcd": None, "iface": "?"}
    else:
        if not devs:
            raise OpsError("no Redragon M913 found (25a7:fa07 / 25a7:fa08). "
                           "Is the dongle or cable plugged in?")
        dev = devs[0]
    if sensor is None and dev.get("bcd") is not None:
        s = D.sensor_from_bcd(dev["bcd"])
        sensor = s if s in (0, 1) else None
    return dev, sensor


def sensor_id(name):
    return dpimod.SENSOR_IDS[name.lower()] if name else None


# -- memory ------------------------------------------------------------------------

def read_settings(m):
    mem = M.Mem()
    for addr, n in M.regions_for_decode():
        mem.put(addr, m.read(addr, n))
    return mem


def read_macro_slot(m, slot, whole=False):
    base = P.ADDR_MACRO + P.MACRO_SIZE * slot
    if whole:
        return m.read(base, P.MACRO_SIZE)
    head = m.read(base, P.MACRO_HEADER)
    n = head[0x1F]
    if not 1 <= n <= 69:
        return head
    return head + m.read(base + P.MACRO_HEADER, 5 * n + 4)


def read_config(m, sensor):
    """What the mouse holds, as a config dict -> (cfg, notes)."""
    mem = read_settings(m)
    macros = {s: read_macro_slot(m, s) for s in M.macro_slots_used(mem)}
    return M.decode(mem, sensor, macros)


def full_backup(m, dev, sensor, whole_slots=()):
    """Settings, key slots and macros. Macro slots in `whole_slots` (the ones
    about to be overwritten) are saved in full so a restore is byte-exact."""
    regions = {}
    for addr, n in M.regions_for_decode():
        regions[addr] = m.read(addr, n)
    for slot in range(P.N_MACROS):
        regions[P.ADDR_MACRO + P.MACRO_SIZE * slot] = read_macro_slot(
            m, slot, slot in whole_slots)
    return {
        "format": "m913ctl-backup",
        "version": 1,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "pid": f"{dev['pid']:04x}" if dev.get("pid") else None,
        "bcd": f"{dev['bcd']:04x}" if dev.get("bcd") is not None else None,
        "sensor": sensor,
        "regions": {f"{a:#06x}": d.hex() for a, d in sorted(regions.items())},
    }


def save_backup(b, path=None):
    if path is None:
        os.makedirs(state_dir(), mode=0o700, exist_ok=True)
        path = os.path.join(state_dir(), time.strftime("backup-%Y%m%d-%H%M%S.json"))
    with open(path, "w") as f:
        json.dump(b, f, indent=1)
        f.write("\n")
    return path


def diff(m, writes):
    """Keep only the writes whose bytes differ from what the mouse holds."""
    todo = []
    for w in writes:
        cur = m.read(w.addr, len(w.data))
        if cur != w.data:
            todo.append((w, cur))
    return todo


def button_matrix_after(m, writes):
    mat = bytearray(m.read(P.ADDR_BUTTONS, 4 * P.N_BUTTONS))
    for w in writes:
        for i, v in enumerate(w.data):
            a = w.addr + i - P.ADDR_BUTTONS
            if 0 <= a < len(mat):
                mat[a] = v
    return bytes(mat)


def left_click_survives(m, writes):
    mat = button_matrix_after(m, writes)
    return any(mat[i:i + 4] == LEFT_CLICK for i in range(0, len(mat), 4))


def coalesce_buttons(m, todo):
    """Send button records as 8-byte packets
    aligned on 0x60, filling the neighbour record with what the mouse has."""
    end = P.ADDR_BUTTONS + 4 * P.N_BUTTONS
    btn = [w for w, _ in todo if P.ADDR_BUTTONS <= w.addr < end]
    if not btn:
        return todo
    out = [(w, c) for w, c in todo if not P.ADDR_BUTTONS <= w.addr < end]
    before = m.read(P.ADDR_BUTTONS, 4 * P.N_BUTTONS)
    after = button_matrix_after(m, btn)
    for k in range(0, len(after), 8):
        if after[k:k + 8] != before[k:k + 8]:
            names = [w.label for w in btn if k <= w.addr - P.ADDR_BUTTONS < k + 8]
            out.append((M.Write(" + ".join(names), P.ADDR_BUTTONS + k, after[k:k + 8], 8),
                        before[k:k + 8]))
    return out


def plan(m, settings, sensor):
    """Settings -> [(Write, current bytes)] that actually need writing."""
    return coalesce_buttons(m, diff(m, M.encode(settings, sensor)))


def write_and_verify(m, writes):
    for w in writes:
        m.write(w.addr, w.data, w.chunk)
    bad = []
    for w in writes:
        got = m.read(w.addr, len(w.data))
        if got != w.data:
            bad.append((w, got))
    return bad


def execute(m, dev, sensor, todo, backup=True):
    """Back up, write, verify. -> (backup path or None, [(Write, got)] failures)."""
    path = None
    if backup:
        slots = {(w.addr - P.ADDR_MACRO) // P.MACRO_SIZE for w, _ in todo
                 if w.addr >= P.ADDR_MACRO}
        path = save_backup(full_backup(m, dev, sensor, slots))
    return path, write_and_verify(m, [w for w, _ in todo])


# Fields restore puts back, written in the same packet layout as apply.
# Bytes m913ctl never writes (e.g. the active DPI index) are left alone.
SETTINGS_FIELDS = [
    ("report rate", P.ADDR_RATE, 2, P.MAX_CHUNK),
    ("dpi level count", P.ADDR_DPI_COUNT, 2, P.MAX_CHUNK),
    ("dpi levels", P.ADDR_DPI, 4 * P.DPI_LEVELS, 8),
    ("dpi colours", P.ADDR_DPI_COLOR, 4 * P.DPI_LEVELS, 8),
    ("led", P.ADDR_LED, 8, 8),
    ("led speed", P.ADDR_LED_SPEED, 2, P.MAX_CHUNK),
] + [(f"buttons {P.ADDR_BUTTONS + k:#06x}", P.ADDR_BUTTONS + k, 8, 8)
     for k in range(0, 4 * P.N_BUTTONS, 8)]


def restore_writes(regions):
    mem = M.Mem(dict(regions))
    writes = []
    for label, addr, n, chunk in SETTINGS_FIELDS:
        data = mem.get(addr, n)
        if data is not None:
            writes.append(M.Write(label, addr, data, chunk))
    for addr, data in regions:
        if P.ADDR_KEYSLOT <= addr < P.ADDR_MACRO:
            for off in range(0, len(data), P.KEYSLOT_SIZE):
                a = addr + off
                writes.append(M.Write(f"key slot {(a - P.ADDR_KEYSLOT) // P.KEYSLOT_SIZE}",
                                      a, data[off:off + P.KEYSLOT_SIZE]))
        elif addr >= P.ADDR_MACRO:
            writes.append(M.Write(f"macro slot {(addr - P.ADDR_MACRO) // P.MACRO_SIZE}",
                                  addr, data))
    return writes


def load_backup(path):
    try:
        with open(path) as f:
            b = json.load(f)
        if b.get("format") != "m913ctl-backup":
            raise ValueError("not an m913ctl backup")
        return [(int(a, 16), bytes.fromhex(h)) for a, h in b["regions"].items()]
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        raise OpsError(f"{path}: {e}")
