# Changelog

All notable changes to m913ctl are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and the project uses
[semantic versioning](https://semver.org/).

## 0.4.0

- The GTK app now has a **Restore** button: roll the mouse back to any saved
  backup in one click, with the earliest one marked as the baseline.
- On first connect the app quietly saves a baseline backup of the mouse's
  current settings, so there is always something to restore to.

## 0.3.0

- Side-button commands are now stored entirely on the mouse as a typed macro
  (types the command and presses Enter), instead of sending F13–F24 for a
  Hyprland bind. Works on any Wayland or X11 desktop with nothing running.
- Config section renamed `[scripts]` → `[commands]` (`[scripts]` still accepted).
- Removed the background daemon, the systemd user service and the Hyprland
  bind generator.
- The config checker enforces the mouse's per-button macro limit (~31 typed
  characters) and reports a clear error for a command that won't fit.

## 0.2.0

- Added a terminal UI (`m913ctl tui`) and a GTK window (`m913ctl gui`) with a
  clickable drawing of the mouse.
- LED "rainbow" mode (formerly "neon"); working colour picker.

## 0.1.0

- First release: read, dump, backup, apply and restore the M913's onboard
  settings (buttons, key actions, combos, media keys, macros, DPI, report
  rate, lighting) over hidraw, with diff-only writes and read-back verify.
