"""Wire format and memory map of the Redragon M913 (VID 25a7, PID fa07/fa08).

See PROTOCOL.md.
"""

VID = 0x25A7
PID_WIRELESS = 0xFA07  # 2.4 GHz dongle
PID_WIRED = 0xFA08
PIDS = (PID_WIRED, PID_WIRELESS)

REPORT_ID = 0x08         # requests (feature report)
ANSWER_IDS = (0x09, 0x08)  # the M913 answers with input report 9 (seen on hardware)
PACKET_LEN = 17
MAX_CHUNK = 10  # data bytes per packet

CMD_DRV_STATUS = 0x02  # data[0] = 1 when a driver opens the mouse, 0 on close
CMD_PREAMBLE = 0x03
CMD_HANDSHAKE = 0x01
CMD_BATTERY = 0x04
CMD_WRITE = 0x07
CMD_READ = 0x08

# --- memory map (active profile) -------------------------------------------
ADDR_RATE = 0x00          # [code, chk]
ADDR_DPI_COUNT = 0x02     # [n, chk]
ADDR_DPI_ACTIVE = 0x04    # [index, chk]  (read only for us)
ADDR_DPI = 0x0C           # 4 bytes per level: [x, y, flag, chk]
ADDR_DPI_COLOR = 0x2C     # 4 bytes per level: [r, g, b, chk]
ADDR_LED = 0x54           # [r, g, b, chk, mode, ~mode, bright, ~bright]
ADDR_LED_MODE = 0x58      # [mode, ~mode]  (the only write for "off")
ADDR_LED_SPEED = 0x5C     # [speed, ~speed]
ADDR_BUTTONS = 0x60       # 4 bytes per button: [type, b1, b2, chk]
ADDR_KEYSLOT = 0x100      # 0x20 bytes per button
KEYSLOT_SIZE = 0x20
ADDR_MACRO = 0x300        # 0x180 bytes per macro slot
MACRO_SIZE = 0x180

DPI_LEVELS = 5
N_BUTTONS = 16
N_MACROS = 16
KEYSLOT_MAX_EVENTS = (KEYSLOT_SIZE - 2) // 3      # 10
MACRO_HEADER = 0x20
MACRO_MAX_EVENTS = 64  # 69 would fit; stay below the edge of the slot
MACRO_MIN_DELAY = 3

# Order of the 16 button records at 0x60 (and of the key slots at 0x100).
BUTTONS = [
    "button_1", "button_2", "button_3", "button_4", "button_5", "button_6",
    "right", "left", "button_7", "button_8", "middle", "fire",
    "button_9", "button_10", "button_11", "button_12",
]

RATES = {1000: 1, 500: 2, 250: 4, 125: 8}
RATE_CODES = {v: k for k, v in RATES.items()}

LED_MODES = {"off": 0, "static": 1, "breathing": 2, "rainbow": 3}
LED_MODE_ALIASES = {"neon": "rainbow"}  # older name, still accepted
LED_MODE_NAMES = {v: k for k, v in LED_MODES.items()}
# brightness level 1..10 -> byte
BRIGHTNESS = [1, 30, 60, 90, 128, 150, 180, 210, 230, 255]


def chk(data) -> int:
    """Field checksum: the byte that makes a field sum to 0x55."""
    return (0x55 - sum(data)) & 0xFF


def field(*data) -> bytes:
    data = bytes(data)
    return data + bytes([chk(data)])


def packet(cmd: int, addr: int = 0, data: bytes = b"", length=None) -> bytes:
    """17-byte request: 08 cmd 00 addr_hi addr_lo len data[10] chk."""
    data = bytes(data)
    if len(data) > MAX_CHUNK:
        raise ValueError("at most 10 data bytes per packet")
    n = len(data) if length is None else length
    p = bytearray(PACKET_LEN)
    p[0] = REPORT_ID
    p[1] = cmd
    p[3] = (addr >> 8) & 0xFF
    p[4] = addr & 0xFF
    p[5] = n
    p[6:6 + len(data)] = data
    p[16] = chk(p[:16])
    return bytes(p)


def parse_response(p: bytes):
    """Return (cmd, status, addr, length, data[10]) or None if not an answer."""
    if len(p) < PACKET_LEN or p[0] not in ANSWER_IDS:
        return None
    # The answer's checksum is not checked: USB already guards the transfer.
    # Address and command are matched instead.
    # all 10 data bytes: some answers (battery) carry data with len = 0
    return p[1], p[2], (p[3] << 8) | p[4], p[5], bytes(p[6:16])


def chunks(addr: int, data: bytes, size: int = MAX_CHUNK):
    for off in range(0, len(data), size):
        yield addr + off, data[off:off + size]


def write_packets(addr: int, data: bytes, size: int = MAX_CHUNK):
    return [packet(CMD_WRITE, a, d) for a, d in chunks(addr, data, size)]


def read_packets(addr: int, length: int):
    out = []
    for off in range(0, length, MAX_CHUNK):
        out.append(packet(CMD_READ, addr + off, b"", min(MAX_CHUNK, length - off)))
    return out


def preamble_packets():
    """An opening handshake some firmware wants before reads.

    Only used as a fallback when a plain read gets no answer.
    """
    return [
        packet(CMD_PREAMBLE),
        packet(CMD_HANDSHAKE, 0, bytes([0x34, 0x2E, 0x4C, 0x57])),
    ]
