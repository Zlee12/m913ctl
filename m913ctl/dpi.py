"""DPI <-> sensor code tables.

The sensor is chosen by the top nibble of the USB bcdDevice:
0 = PMW3325, 1 = PMW3335, 2 = 3330.
"""

SENSORS = {0: "PMW3325", 1: "PMW3335", 2: "3330"}
SENSOR_IDS = {"3325": 0, "pmw3325": 0, "3335": 1, "pmw3335": 1}

# PMW3335: 100..16000 in steps of 100.  Note: mouse_m908 is off by one on
# 16 of these (6100, 6600, ... 13600); these are the right values.
_C3335 = [
    0, 2, 3, 4, 5, 6, 7, 9, 10, 11, 12, 13, 14, 16, 17, 18, 19, 20, 22, 23, 24, 25, 26, 27, 29, 30,
    31, 32, 33, 35, 37, 38, 39, 40, 41, 42, 44, 45, 46, 47, 48, 50, 51, 52, 53, 54, 56, 57, 58, 59,
    60, 62, 63, 64, 65, 66, 68, 69, 70, 71, 72, 74, 75, 76, 77, 78, 80, 81, 82, 83, 84, 86, 87, 88,
    89, 90, 92, 93, 94, 95, 96, 98, 99, 100, 101, 102, 104, 105, 106, 107, 108, 110, 111, 112, 113,
    114, 116, 117, 118, 119, 120, 122, 123, 124, 125, 126, 128, 129, 130, 131, 132, 134, 135, 136,
    137, 138, 140, 141, 142, 143, 144, 146, 147, 148, 149, 150, 152, 153, 154, 155, 156, 158, 159,
    160, 161, 162, 164, 165, 166, 167, 168, 170, 171, 172, 173, 174, 176, 177, 178, 179, 180, 181,
    182, 183, 184, 185, 186, 187, 188, 189,
]
PMW3335 = {100 * (i + 1): c for i, c in enumerate(_C3335)}

# PMW3325: 200..5000 in steps of 100; above that, 6000..10000 in steps of
# 1000 use code(dpi / 2) with the high-range flag set.
_C3325 = [
    4, 6, 8, 11, 13, 15, 18, 20, 22, 25, 27, 29, 32, 34, 36, 39, 41, 43, 46, 48, 50, 52, 55, 57, 59,
    62, 64, 66, 69, 71, 73, 76, 78, 80, 83, 85, 87, 90, 92, 94, 97, 99, 101, 104, 106, 108, 111,
    113, 115,
]
PMW3325 = {100 * (i + 2): c for i, c in enumerate(_C3325)}


class DpiError(ValueError):
    pass


def table(sensor: int) -> dict:
    """{dpi: (code, flag)} for a sensor id."""
    if sensor == 1:
        return {d: (c, 0) for d, c in PMW3335.items()}
    if sensor == 0:
        t = {d: (c, 0) for d, c in PMW3325.items()}
        for d in range(6000, 10001, 1000):
            t[d] = (PMW3325[d // 2], 1)
        return t
    # Sensor id 2 (3330) is not used by the M913, so it has no table here.
    raise DpiError(f"no DPI table for sensor id {sensor}; pass --sensor 3325 or 3335")


def encode(sensor: int, dpi: int):
    t = table(sensor)
    if dpi not in t:
        valid = sorted(t)
        raise DpiError(
            f"{dpi} DPI is not available on the {SENSORS.get(sensor, sensor)} sensor "
            f"({valid[0]}..{valid[-1]}; see `m913ctl dpi-values`)"
        )
    return t[dpi]


def decode(sensor: int, code: int, flag: int):
    for d, (c, f) in table(sensor).items():
        if c == code and f == flag:
            return d
    return None
