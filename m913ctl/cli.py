"""m913ctl command line."""

import argparse
import sys

from . import __version__
from . import device as D
from . import dpi as dpimod
from . import keys as K
from . import model as M
from . import ops as O
from . import protocol as P
from . import scripts as S


def die(msg, code=1):
    print(f"m913ctl: {msg}", file=sys.stderr)
    sys.exit(code)


def pick(args):
    try:
        return O.find_device(args.device, O.sensor_id(getattr(args, "sensor", None)))
    except O.OpsError as e:
        die(str(e))


def sensor_of(args, dev=None):
    if getattr(args, "sensor", None):
        return O.sensor_id(args.sensor)
    if dev and dev.get("bcd") is not None:
        s = D.sensor_from_bcd(dev["bcd"])
        if s in (0, 1):
            return s
    return None


def open_mouse(args, dev):
    log = (lambda s: print(s, file=sys.stderr)) if args.debug else None
    return D.Mouse(dev["node"], log=log)


def show_plan(todo):
    for w, cur in todo:
        print(f"  {w.label:<28} @{w.addr:#06x}  {cur.hex(' ') if cur is not None else '?'}")
        print(f"  {'':<28}  {'->':>7}  {w.data.hex(' ')}")


def confirm(args, what):
    if args.yes:
        return
    if not sys.stdin.isatty():
        die("refusing to write without a terminal; pass --yes")
    ans = input(f"{what} [y/N] ").strip().lower()
    if ans not in ("y", "yes"):
        die("nothing written", 0)


# -- commands ----------------------------------------------------------------------

def cmd_list(args):
    devs = D.find()
    if not devs:
        print("no M913 found")
        return 1
    for d in devs:
        kind = "wired" if d["pid"] == P.PID_WIRED else "wireless dongle"
        s = D.sensor_from_bcd(d["bcd"])
        sname = dpimod.SENSORS.get(s, "unknown")
        print(f"{d['node']}  {kind}  interface {d['iface']}  "
              f"bcdDevice {d['bcd']:04x}  sensor {sname}" if d["bcd"] is not None
              else f"{d['node']}  {kind}  interface {d['iface']}")
    return 0


def cmd_info(args):
    dev, sensor = pick(args)
    with open_mouse(args, dev) as m:
        rate = m.read(P.ADDR_RATE, 2)
        cnt = m.read(P.ADDR_DPI_COUNT, 4)
        bat = m.battery() if dev.get("pid") == P.PID_WIRELESS else None
    print(f"device   {dev['node']}")
    print(f"sensor   {dpimod.SENSORS.get(sensor, 'unknown')}")
    if M._ok(rate) and rate[0] in P.RATE_CODES:
        print(f"rate     {P.RATE_CODES[rate[0]]} Hz")
    if M._ok(cnt[:2]):
        print(f"dpi      {cnt[0]} levels, active level {cnt[2] + 1}")
    if bat is not None:
        print(f"battery  {bat} %")
    return 0


def cmd_dump(args):
    dev, sensor = pick(args)
    with open_mouse(args, dev) as m:
        cfg, notes = O.read_config(m, sensor)
    text = "# read from the M913's active profile by m913ctl " + __version__ + "\n"
    text += "".join(f"# note: {n}\n" for n in notes) + "\n" + M.to_toml(cfg)
    if args.output:
        with open(args.output, "w") as f:
            f.write(text)
        print(f"wrote {args.output}")
    else:
        sys.stdout.write(text)
    return 0


def cmd_backup(args):
    dev, sensor = pick(args)
    with open_mouse(args, dev) as m:
        b = O.full_backup(m, dev, sensor)
    print(f"saved {O.save_backup(b, args.output)}")
    return 0


def load_config(path):
    """-> (mouse settings, {button: command})"""
    try:
        raw = O.load_raw(path)
    except O.OpsError as e:
        die(str(e))
    if not raw and not __import__("os").path.exists(path):
        die(f"{path}: no such file (default config is {O.config_path()})")
    try:
        return O.settings_from_raw(raw)
    except M.ConfigError as e:
        die(f"{path}: {e}")


def cmd_check(args):
    s, commands = load_config(args.config)
    sensor = sensor_of(args)
    if sensor is None and "dpi_levels" in s:
        devs = D.find()
        sensor = sensor_of(args, devs[0]) if devs else None
    try:
        writes = M.encode(s, sensor)
    except M.ConfigError as e:
        die(f"{args.config}: {e}")
    print(f"{args.config}: OK, {len(writes)} field(s)")
    for w in writes:
        print(f"  {w.label:<28} @{w.addr:#06x}  {w.data.hex(' ')}")
    if commands:
        print("Typed into the focused window when the button is pressed:")
        for b, cmd in sorted(commands.items(), key=lambda x: int(x[0].split("_")[1])):
            print(f"  {b:<10} {cmd}")
    return 0


def cmd_apply(args):
    s, commands = load_config(args.config)
    dev, sensor = pick(args)
    try:
        writes = M.encode(s, sensor)
    except M.ConfigError as e:
        die(f"{args.config}: {e}")
    with open_mouse(args, dev) as m:
        todo = O.coalesce_buttons(m, O.diff(m, writes))
        if not todo:
            print("the mouse already matches the config; nothing to write")
        else:
            print(f"{len(todo)} field(s) differ (current -> new):")
            show_plan(todo)
            if not args.force and not O.left_click_survives(m, [w for w, _ in todo]):
                die("no button would be left click after this. "
                    "Re-run with --force if you mean it.")
            if args.dry_run:
                print("dry run: nothing written")
                return 0
            confirm(args, "Write these to the mouse?")
            if not args.no_backup:
                print("backing up the current settings first...")
            path, bad = O.execute(m, dev, sensor, todo, backup=not args.no_backup)
            if path:
                print(f"  {path}")
            if bad:
                for w, got in bad:
                    print(f"  verify FAILED {w.label} @{w.addr:#06x}: read {got.hex(' ')}",
                          file=sys.stderr)
                die("some fields did not read back as written; "
                    "`m913ctl restore` puts the backup back")
            print("written and verified")
    if commands and not args.dry_run:
        print("command buttons type into the focused window — open a terminal first")
    return 0


def cmd_restore(args):
    try:
        regions = O.load_backup(args.backup)
    except O.OpsError as e:
        die(str(e))
    dev, _ = pick(args)
    writes = O.restore_writes(regions)
    with open_mouse(args, dev) as m:
        todo = O.diff(m, writes)
        if not todo:
            print("the mouse already matches the backup")
            return 0
        print(f"{len(todo)} block(s) differ from the backup")
        if args.dry_run:
            show_plan(todo)
            return 0
        confirm(args, "Restore them?")
        bad = O.write_and_verify(m, [w for w, _ in todo])
    if bad:
        die(f"{len(bad)} block(s) did not verify")
    print("restored and verified")
    return 0


def cmd_presets(args):
    for name, (desc, cmd) in S.PRESETS.items():
        print(f"  preset:{name:<16} {desc:<28} {cmd}")
    print('use in [commands], e.g.  button_12 = "preset:system_update"')
    return 0


def cmd_tui(args):
    from . import tui
    return tui.run(args.config, device=args.device, sensor=O.sensor_id(args.sensor))


def cmd_gui(args):
    try:
        from . import gui
    except ImportError as e:
        die(f"the GTK window needs python-gobject and gtk4 ({e})")
    return gui.run(args.config, device=args.device, sensor=O.sensor_id(args.sensor))


def cmd_keys(args):
    print("keys:      " + " ".join(sorted(K.KEYS, key=lambda k: (len(k) > 1, k))))
    print("modifiers: " + " ".join(K.MODIFIERS))
    print("media:     " + " ".join(K.MEDIA))
    print("mouse:     " + " ".join(K.MOUSE) + "   (macros/combos only)")
    return 0


ACTIONS = """button actions ([buttons] values):
  left right middle back forward      mouse buttons
  none                                disabled
  dpi_loop dpi_up dpi_down            DPI switching
  double_click triple_click           click presets
  fire:<times 0-3>:<speed 3-255>      rapid fire
  rate_switch led_toggle profile_switch
  key:<key>                           one key, e.g. key:f13, key:ctrl
  combo:<k>+<k>...                    e.g. combo:ctrl+shift+t (max 10 events)
  media:<name>                        e.g. media:play_pause, media:volume_up
  macro:<name>                        a [macros.<name>] table
  raw:<3 hex bytes>                   anything else, e.g. raw:0b ad 00

buttons: """ + " ".join(P.BUTTONS) + """

macro events ([macros.<name>] events = [...]):
  down <key>  up <key>  tap <key>  combo <k>+<k>  type <text>  delay <ms>
  repeat = 1..253 | "hold" | "until_key";  delay = default ms between events (>= 3)

typed commands ([commands] button_N = "..."):
  stored on the mouse as a macro that types the command and presses Enter,
  into whatever window has focus (open a terminal first). ~31 plain chars max
  per button; `m913ctl presets` lists the presets.
"""


def cmd_actions(args):
    sys.stdout.write(ACTIONS)
    return 0


def cmd_dpi_values(args):
    sensor = sensor_of(args)
    if sensor is None:
        devs = D.find()
        sensor = sensor_of(args, devs[0]) if devs else None
    if sensor is None:
        die("sensor unknown; pass --sensor 3325 or 3335")
    print(f"{dpimod.SENSORS[sensor]}: " + " ".join(str(d) for d in sorted(dpimod.table(sensor))))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="m913ctl",
        description="Write button mappings, macros, DPI and LED settings into a "
                    "Redragon M913's onboard memory (active profile).")
    ap.add_argument("--version", action="version", version=f"m913ctl {__version__}")
    ap.add_argument("-d", "--device", help="hidraw node (default: auto-detect, wired first)")
    ap.add_argument("--sensor", choices=sorted(dpimod.SENSOR_IDS),
                    help="override the sensor detected from bcdDevice")
    ap.add_argument("--debug", action="store_true", help="print every packet")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="list connected M913 interfaces").set_defaults(fn=cmd_list)
    sub.add_parser("info", help="sensor, report rate, DPI level, battery").set_defaults(fn=cmd_info)
    p = sub.add_parser("dump", help="read the active profile as a config file")
    p.add_argument("-o", "--output")
    p.set_defaults(fn=cmd_dump)
    p = sub.add_parser("backup", help="save the raw memory to a JSON file")
    p.add_argument("-o", "--output", help=f"default: {O.state_dir()}/backup-<time>.json")
    p.set_defaults(fn=cmd_backup)
    default_cfg = str(O.config_path())
    p = sub.add_parser("check", help="validate a config without touching the mouse")
    p.add_argument("config", nargs="?", default=default_cfg)
    p.set_defaults(fn=cmd_check)
    p = sub.add_parser("apply", help="write a config to the mouse")
    p.add_argument("config", nargs="?", default=default_cfg)
    p.add_argument("-n", "--dry-run", action="store_true", help="show the differences only")
    p.add_argument("-y", "--yes", action="store_true", help="don't ask")
    p.add_argument("--no-backup", action="store_true", help="skip the automatic backup")
    p.add_argument("--force", action="store_true", help="allow a config without left click")
    p.set_defaults(fn=cmd_apply)
    p = sub.add_parser("restore", help="write a backup back to the mouse")
    p.add_argument("backup")
    p.add_argument("-n", "--dry-run", action="store_true")
    p.add_argument("-y", "--yes", action="store_true")
    p.set_defaults(fn=cmd_restore)
    p = sub.add_parser("tui", help="terminal UI")
    p.add_argument("config", nargs="?", default=default_cfg)
    p.set_defaults(fn=cmd_tui)
    p = sub.add_parser("gui", help="GTK window")
    p.add_argument("config", nargs="?", default=default_cfg)
    p.set_defaults(fn=cmd_gui)
    sub.add_parser("presets", help="list command presets").set_defaults(fn=cmd_presets)
    sub.add_parser("keys", help="list key names").set_defaults(fn=cmd_keys)
    sub.add_parser("actions", help="list button actions and macro syntax").set_defaults(
        fn=cmd_actions)
    sub.add_parser("dpi-values", help="list the DPI steps of the sensor").set_defaults(
        fn=cmd_dpi_values)

    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except (D.DeviceError, O.OpsError) as e:
        die(str(e))
    except KeyboardInterrupt:
        die("interrupted", 130)


if __name__ == "__main__":
    sys.exit(main())
