# Redragon M913 protocol notes

How m913ctl talks to the mouse. Where it overlaps, this matches the packets
[mouse_m908](https://github.com/dokutan/mouse_m908) sends. Confirmed on a
wired M913 (PMW3335, bcdDevice `1333`): answers on report 9, memory reads,
the LED fields (mode, brightness, off), key actions and a macro slot with an
empty name.

## Transport

| | |
|---|---|
| IDs | VID `25a7`; PID `fa07` = 2.4 GHz dongle, `fa08` = cable |
| Requests | HID **feature** report, ID `0x08`, 17 bytes; retried up to 3 times, 100 ms apart |
| Answers | **input** report ID `0x09` (confirmed on a wired M913), 17 bytes, interrupt EP `0x82`, interface 1; same layout, checksum over the `0x09` |
| Linux | `HIDIOCSFEATURE` + `read()` on the interface-1 hidraw node; the kernel driver stays bound |

Packet: `08 cmd status addr_hi addr_lo len data[10] chk`, with
`chk = (0x55 - sum(bytes 0..15)) & 0xff`. Requests have
status 0; answers repeat the header with data filled in.

| cmd | meaning |
|---|---|
| `0x01` | 4-byte challenge; not needed |
| `0x02` | "driver status": data[0] = 1 on open, 0 on close |
| `0x03` | harmless; m913ctl sends it only if a read gets no answer |
| `0x04` | battery: answer data[0] = 0..10 (×10 %) |
| `0x07` | write memory, ≤10 bytes per packet |
| `0x08` | read memory, ≤10 bytes per packet |
| `0x09` | purpose unknown, not used |
| `0x0a` | async notification from the mouse |

## Fields

Every field ends with a checksum byte that makes the field sum to `0x55`.
The memory belongs to the **active profile**; the profile button on the mouse
switches which one you are writing.

| addr | size | content |
|---|---|---|
| `0x00` | 2 | report rate code: 1 = 1000, 2 = 500, 4 = 250, 8 = 125 Hz |
| `0x02` | 2 | number of enabled DPI levels (1..5) |
| `0x04` | 2 | active DPI level (index) |
| `0x0c + 4i` | 4 | DPI level i: `x y flag chk` (written 8 bytes per packet) |
| `0x2c + 4i` | 4 | DPI level i colour: `r g b chk` |
| `0x54` | 8 | LED: `r g b chk mode ~mode bright ~bright` (`~x` = `0x55 - x`) |
| `0x5c` | 2 | LED speed (breathing/rainbow only) |
| `0x60 + 4i` | 4 | button i: `type b1 b2 chk` |
| `0x100 + 0x20i` | ≤32 | key action for button i |
| `0x300 + 0x180i` | ≤384 | macro slot i (16 slots) |

LED modes: 0 off (only `0x58 = 00 55` is written), 1 static, 2 breathing,
3 rainbow (`neon` is accepted as another name). Brightness levels 1..10 map
to `1 30 60 90 128 150 180 210 230 255`. The default speed is 3; its range
is not pinned down, m913ctl allows 1..10.

Button order: `button_1..6, right, left, button_7, button_8, middle, fire,
button_9..12`.

### Button records

| bytes | action |
|---|---|
| `00 00 00` | disabled |
| `01 01/02/04/08/10 00` | left / right / middle / back / forward |
| `02 01/02/03 00` | DPI loop / up / down |
| `04 speed times` | fire; double click `04 32 02`, triple `04 32 03` |
| `05 i 00` | key action in slot i (mouse_m908 always writes i = 0) |
| `06 i repeat` | macro slot i; repeat = count, `ff` = while held, `fe` = until a key |
| `07 00 00` / `08 00 00` / `09 00 00` | report-rate switch / LED on-off / profile switch |

### Key actions

`count, events[count × 3], chk` with events `type code_lo code_hi`:

| down | up | code |
|---|---|---|
| `80` | `40` | modifier bit (1 LCtrl, 2 LShift, 4 LAlt, 8 LGui, 0x10.. right side) |
| `81` | `41` | HID keyboard usage |
| `82` | `42` | consumer usage, 16-bit little endian |
| `84` | `44` | mouse button bit |

A combo is ordered: modifiers down, keys down, modifiers up, keys up. 10 events fit in a slot.

### Macros

```
[0]       name length in bytes
[1..30]   UTF-16LE name (15 chars)
[0x1f]    event count n
[0x20..]  n × (type code 00 delay_hi delay_lo)   delay ≥ 3 ms
[..]      chk, 00 00 00
```

`chk = 0x55 - sum(bytes 0 .. 0x20 + 5n)`, computed with the name area
empty. m913ctl leaves the name empty, so the checksum is right whichever way
the firmware checks it. A slot is `0x180` bytes, so `n` ≤ 69 events; m913ctl
caps at 64. There are 16 slots. A typed command (`[commands]`) is just a macro
that types the text and taps Enter, so it shares these limits — about 31 plain
characters per button, fewer with shifted characters.

## DPI codes

Sensor = `bcdDevice >> 12`: 0 = PMW3325, 1 = PMW3335. The tables are in
`m913ctl/dpi.py`. PMW3335: 100..16000 in
steps of 100. PMW3325: 200..5000 in steps of 100, then 6000..10000 in steps
of 1000 as `code(dpi / 2)` with `flag = 1` (the flag is taken from X).

## Where mouse_m908 is wrong for the M913

- 16 PMW3335 codes are one too high (6100, 6600, … 13600).
- Key actions always point at slot 0, so only one key binding works.
- Macros are not implemented for the M913.
- Media keys are sent as fake keyboard codes instead of consumer usages.
- LED writes also rewrite the report rate at `0x00`; breathing is written as
  mode 1; the colour checksum is a constant and brightness's isn't recomputed.
- DPI levels you didn't configure get filler values.
