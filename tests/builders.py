"""Synthetic DXF plans with elevations and roof plans, in centimetres."""

from __future__ import annotations

import ezdxf

# Building: 10 x 6 m, 30 cm walls, wall layer MURI.
W, D, T = 1000, 600, 30
DOOR = (200, 290)      # x range of the door on the south wall
WIN_S = (500, 620)     # window on the south wall
WIN_N = (300, 420)     # window on the north wall
PLAN_AREA = (-200, -200, W + 200, D + 200)


def _rect(msp, x0, y0, x1, y1, layer):
    msp.add_lwpolyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], close=True, dxfattribs={"layer": layer})


def building(units: int = 5) -> tuple[ezdxf.document.Drawing, object]:
    doc = ezdxf.new("R2018", setup=True)
    doc.units = units
    for name in ("MURI", "PORTE", "FINESTRE", "Tetto", "Prospetto Sud", "Prospetto Nord"):
        doc.layers.add(name)
    msp = doc.modelspace()
    _rect(msp, 0, 0, W, D, "MURI")
    _rect(msp, T, T, W - T, D - T, "MURI")
    # symbols over the (continuous) walls
    _rect(msp, DOOR[0], 0, DOOR[1], T, "PORTE")
    _rect(msp, WIN_S[0], 0, WIN_S[1], T, "FINESTRE")
    _rect(msp, WIN_N[0], D - T, WIN_N[1], D, "FINESTRE")
    return doc, msp


def south_elevation(msp, ground_y=-2000, door_h=200, sill=100, win_h=140):
    """Below the plan, same x. Door bottom = floor level (ground_y)."""
    _rect(msp, DOOR[0], ground_y, DOOR[1], ground_y + door_h, "PORTE")
    _rect(msp, WIN_S[0], ground_y + sill, WIN_S[1], ground_y + sill + win_h, "FINESTRE")
    return (-100, ground_y - 100, W + 100, ground_y + 900)


def north_elevation(msp, floor_y=2500, sill=110, win_h=130):
    _rect(msp, WIN_N[0], floor_y + sill, WIN_N[1], floor_y + sill + win_h, "FINESTRE")
    return (-100, floor_y - 100, W + 100, floor_y + 900, floor_y)


def hip_roof(msp, dx=0, dy=0, overhang=50, layer="Tetto"):
    """Rectangular hip roof over the building: outline + ridge + 4 hips. Plan offset (dx, dy)."""
    x0, y0, x1, y1 = -overhang + dx, -overhang + dy, W + overhang + dx, D + overhang + dy
    half = (y1 - y0) / 2
    ym = (y0 + y1) / 2
    msp.add_lwpolyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], close=True, dxfattribs={"layer": layer})
    rx0, rx1 = x0 + half, x1 - half
    msp.add_line((rx0, ym), (rx1, ym), dxfattribs={"layer": layer})
    for corner, end in (((x0, y0), (rx0, ym)), ((x0, y1), (rx0, ym)),
                        ((x1, y0), (rx1, ym)), ((x1, y1), (rx1, ym))):
        msp.add_line(corner, end, dxfattribs={"layer": layer})
    return (rx0, rx1, ym, half)


def gable_roof(msp, dx=0, dy=0, overhang=50, layer="Tetto"):
    x0, y0, x1, y1 = -overhang + dx, -overhang + dy, W + overhang + dx, D + overhang + dy
    ym = (y0 + y1) / 2
    msp.add_lwpolyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], close=True, dxfattribs={"layer": layer})
    msp.add_line((x0, ym), (x1, ym), dxfattribs={"layer": layer})  # ridge reaches both gable ends
    return (x0, x1, ym, (y1 - y0) / 2)


def l_roof(doc_or_msp, layer="Tetto"):
    """Equal-pitch hip roof on an L (two 5 m wide wings) with a valley; metres * 100."""
    s = 100
    L = [(0, 0), (12, 0), (12, 5), (5, 5), (5, 12), (0, 12)]
    msp = doc_or_msp
    msp.add_lwpolyline([(x * s, y * s) for x, y in L], close=True, dxfattribs={"layer": layer})
    for a, b in (((2.5, 2.5), (9.5, 2.5)), ((2.5, 2.5), (2.5, 9.5)),
                 ((12, 0), (9.5, 2.5)), ((12, 5), (9.5, 2.5)),
                 ((0, 12), (2.5, 9.5)), ((5, 12), (2.5, 9.5)),
                 ((0, 0), (2.5, 2.5)), ((5, 5), (2.5, 2.5))):
        msp.add_line((a[0] * s, a[1] * s), (b[0] * s, b[1] * s), dxfattribs={"layer": layer})
