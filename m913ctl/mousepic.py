"""Geometry of the M913 drawing used by the GTK window.

A line drawing of the M913 (no logo): the top view and the
left side with the thumb pad, both front up, drawn to the mouse's real
proportions (outline measured from a product photo, 92 x 122.5 mm), plus two
small tables that repeat the bindings in readable text. Pure Python, so the
hit testing is tested without GTK.
"""

WIDTH, HEIGHT = 800, 500
K = 0.72  # photo pixels -> drawing units


def T(x, y):
    """Top view: photo coordinates -> drawing coordinates."""
    return (round((x - 92) * K + 16, 1), round((y - 266) * K + 14, 1))


def S(x, y):
    """Side view: photo coordinates -> drawing coordinates."""
    return (round((x - 601) * K + 372, 1), round((y - 266) * K + 14, 1))


def _t(pts):
    return [T(*p) for p in pts]


def _s(pts):
    return [S(*p) for p in pts]


TOP_OUTLINE = _t([
    (280, 269), (221, 308), (192, 320), (190, 325), (158, 333), (150, 338), (151, 440),
    (117, 465), (100, 482), (93, 494), (92, 513), (111, 621), (128, 691), (188, 791),
    (209, 818), (232, 831), (293, 854), (323, 856), (396, 830), (422, 810), (476, 742),
    (523, 673), (530, 653), (529, 628), (514, 513), (508, 437), (499, 423), (472, 402),
    (466, 401), (469, 340), (404, 308), (352, 271)
])
SIDE_OUTLINE = _s([
    (698, 272), (687, 272), (685, 277), (673, 277), (625, 304), (613, 353), (612, 427),
    (605, 468), (601, 588), (606, 817), (616, 853), (619, 856), (653, 861), (671, 851),
    (706, 825), (737, 795), (766, 759), (799, 697), (811, 659), (820, 607), (820, 564),
    (823, 563), (821, 524), (819, 519), (812, 516), (813, 497), (802, 466), (796, 464),
    (780, 420), (781, 396), (777, 377), (769, 362), (741, 340), (713, 290)
])

# -- top view details ----------------------------------------------------------------
TOP_LINES = [
    _t([(190, 330), (200, 470), (215, 640), (236, 790)]),      # left shell seam
    _t([(430, 330), (418, 470), (404, 640), (386, 790)]),      # right shell seam
    _t([(151, 440), (160, 560), (176, 700)]),                  # thumb wing crease
    _t([(469, 402), (460, 560), (446, 700)]),                  # ring-finger wing crease
    _t([(232, 600), (262, 600)]), _t([(358, 600), (388, 600)]),  # button ends
]
TOP_DECOR = {
    "console": _t([(280, 292), (340, 292), (344, 440), (356, 455), (352, 565), (310, 622),
                   (268, 565), (264, 455), (276, 440)]),
    "dpi_up": _t([(286, 462), (334, 462), (344, 490), (330, 520), (290, 520), (276, 490)]),
    "dpi_down": _t([(290, 530), (330, 530), (338, 560), (318, 598), (302, 598), (282, 560)]),
    "led_1": _t([(262, 478), (272, 474), (272, 488), (262, 492)]),
    "led_2": _t([(262, 503), (272, 499), (272, 513), (262, 517)]),
    "led_3": _t([(262, 528), (272, 524), (272, 538), (262, 542)]),
    "led_4": _t([(262, 553), (272, 549), (272, 563), (262, 567)]),
    "strip_l": _t([(204, 762), (214, 756), (234, 832), (224, 838)]),
    "strip_r": _t([(476, 756), (486, 762), (468, 838), (458, 832)]),
    "side_keys": _t([(140, 480), (150, 476), (156, 660), (146, 664)]),
}

# -- side view details ---------------------------------------------------------------
SIDE_LINES = [
    _s([(700, 285), (760, 360), (805, 470), (810, 600), (780, 720), (720, 810), (655, 858)]),
    _s([(625, 420), (628, 600), (636, 760)]),
]
SIDE_DECOR = {
    "groove": _s([(620, 425), (640, 418), (652, 715), (634, 724)]),
    "wheel": _s([(752, 335), (770, 350), (782, 395), (774, 412), (760, 380)]),
    "top_btn_1": _s([(812, 458), (822, 462), (822, 500), (812, 498)]),
    "top_btn_2": _s([(815, 515), (824, 518), (824, 546), (815, 544)]),
    "strip": _s([(700, 836), (712, 828), (764, 762), (772, 772)]),
    "pad": _s([(655, 476), (802, 476), (802, 682), (655, 682)]),
}

# -- clickable shapes on the mouse: name -> {"poly", "label", "text", "r"} -------------
SHAPES = {
    "left": {"poly": _t([(192, 312), (282, 292), (276, 440), (264, 455), (262, 590),
                         (232, 600), (208, 470), (190, 330)]),
             "label": T(232, 420), "text": "L", "r": 0},
    "right": {"poly": _t([(428, 312), (338, 292), (344, 440), (356, 455), (358, 590),
                          (388, 600), (412, 470), (430, 330)]),
              "label": T(388, 420), "text": "R", "r": 0},
    "middle": {"poly": _t([(288, 318), (332, 318), (332, 432), (288, 432)]),
               "label": T(310, 375), "text": "", "r": 9},
    "fire": {"poly": _t([(152, 348), (184, 348), (184, 412), (152, 412)]),
             "label": T(168, 380), "text": "F", "r": 5},
}
_COLS = (683, 728, 773)
_ROWS = (505, 552, 600, 648)
for i in range(12):
    r, c = divmod(i, 3)
    cx, cy = _COLS[c], _ROWS[r]
    SHAPES[f"button_{i + 1}"] = {
        "poly": _s([(cx - 19, cy - 19), (cx + 19, cy - 19), (cx + 19, cy + 19),
                    (cx - 19, cy + 19)]),
        "label": S(cx, cy), "text": str(i + 1), "r": 5}

# -- the two tables: name -> (x, y, w, h, title) ----------------------------------------
PAD_TABLE = {"x": 560, "y": 104, "w": 74, "h": 44, "gap": 6}
CELLS = {}
for i in range(12):
    r, c = divmod(i, 3)
    t = PAD_TABLE
    CELLS[f"button_{i + 1}"] = (t["x"] + c * (t["w"] + t["gap"]), t["y"] + r * (t["h"] + t["gap"]),
                                t["w"], t["h"], str(i + 1))
MAIN_TABLE = {"x": 560, "y": 330, "w": 114, "h": 38, "gap": 6}
for j, (name, title) in enumerate([("left", "Left"), ("right", "Right"), ("middle", "Wheel"),
                                    ("fire", "Fire")]):
    r, c = divmod(j, 2)
    t = MAIN_TABLE
    CELLS[name] = (t["x"] + c * (t["w"] + t["gap"]), t["y"] + r * (t["h"] + t["gap"]),
                   t["w"], t["h"], title)


def _area(poly):
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]))) / 2


def _inside(poly, px, py):
    hit = False
    for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
        if (y1 > py) != (y2 > py) and px < x1 + (py - y1) * (x2 - x1) / (y2 - y1):
            hit = not hit
    return hit


def bbox(poly):
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    return min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)


def short(text, n=11):
    """Squeeze an action description into a cell-sized label."""
    t = text.split(" — ")[0]
    for pre in ("key:", "combo:", "media:", "macro:"):
        if t.startswith(pre):
            t = t[len(pre):]
    t = t.replace("_", " ")
    return t if len(t) <= n else t[:n - 1] + "…"


def button_at(x, y, scale=1.0, ox=0.0, oy=0.0):
    """Which button is at widget coordinates (x, y)? On the drawing the smallest
    shape wins (the fire key and the wheel beat the click panels); the tables
    are checked too."""
    px, py = (x - ox) / scale, (y - oy) / scale
    for name, (cx, cy, w, h, _) in CELLS.items():
        if cx <= px <= cx + w and cy <= py <= cy + h:
            return name
    hits = [(_area(s["poly"]), name) for name, s in SHAPES.items() if _inside(s["poly"], px, py)]
    return min(hits)[1] if hits else None


def fit(width, height):
    """Scale and offsets to draw the WIDTH x HEIGHT design centred in a widget."""
    s = min(width / WIDTH, height / HEIGHT)
    return s, (width - WIDTH * s) / 2, (height - HEIGHT * s) / 2
