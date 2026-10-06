"""Synthetic sample plans (DXF), used by the tests and by examples/make_sample.py."""

from __future__ import annotations

from pathlib import Path

import ezdxf
from shapely.geometry import LineString, box
from shapely.ops import unary_union

# Plan in centimetres. An asymmetric 'L'-ish flat so a mirrored import is obvious:
#   - entrance door on the bottom wall, near the left corner
#   - two windows: top wall (left part) and right wall
#   - a partition with a door, plus a short return wall to the right
OUTER = (0, 0, 800, 600)
THICK = 30
WALL_RECTS = [
    box(0, 0, 800, THICK),                  # bottom
    box(0, 600 - THICK, 800, 600),          # top
    box(0, 0, THICK, 600),                  # left
    box(800 - THICK, 0, 800, 600),          # right
    box(395, THICK, 405, 400),              # partition (10 thick), vertical
    box(405, 395, 800 - THICK, 405),        # return wall, horizontal
]
# (name, x, y, direction, width). For 'h' doors (x, y) is the hinge on the wall centre
# line and the opening runs along +x; for 'v' doors (x, y) is the bottom end of the opening.
DOORS = [
    ("entrata", 100, THICK / 2, "h", 90),
    ("interna", 400, 150, "v", 80),
]
WINDOWS = [
    (200, 600 - THICK / 2, "h", 120),
    (800 - THICK / 2, 200, "v", 120),
]


def _door_block(doc, name: str, width: float) -> None:
    blk = doc.blocks.new(name)
    blk.add_line((0, 0), (0, width), dxfattribs={"layer": "0"})            # leaf, open at 90 deg
    blk.add_arc((0, 0), width, 0, 90, dxfattribs={"layer": "0"})           # swing


def _opening_rect(x, y, direction, width, thick=THICK):
    """Opening footprint, used to erase wall lines. 'h': runs along +x from (x, y) with the
    wall centre line at y; 'v': runs along +y from (x, y) with the wall centre line at x."""
    if direction == "h":
        return box(x, y - thick / 2, x + width, y + thick / 2)
    return box(x - thick / 2, y, x + thick / 2, y + width)


def build_sample(path: str | Path, style: str = "lines", units: int | None = 5,
                 with_openings: bool = True, gaps: bool = True) -> Path:
    """Write the sample DXF.

    style:
      "lines"     double-line walls as LINE entities, openings left open (no jambs)
      "jambs"     like "lines" but the wall ends at each opening are drawn
      "polylines" walls as one closed LWPOLYLINE per wall piece
      "hatch"     walls as a solid HATCH
      "centerline" walls as single centre lines
    units: DXF $INSUNITS tag written to the file. Coordinates are always in centimetres
        numerically, so any tag other than 5 (cm) or None is deliberately "wrong"; tests use
        it to check how the tag is honoured.
    with_openings: False draws closed walls with no doors/windows.
    gaps: True leaves a gap in the wall drawing at every door/window (the lines stop at
        the opening); False draws the walls continuous, with the symbols on top.
    """
    doc = ezdxf.new("R2018", setup=True)
    doc.units = units or 0  # 0 = unspecified (ezdxf's default would be metres)
    for name in ("MURI", "PORTE", "FINESTRE"):
        doc.layers.add(name)
    msp = doc.modelspace()
    union = unary_union(WALL_RECTS)

    doors, windows = (DOORS, WINDOWS) if with_openings else ([], [])
    cuts = []
    for _name, x, y, d, w in doors:
        cuts.append(_opening_rect(x, y, d, w) if d == "h" else _opening_rect(x, y, d, w, thick=10))
    for x, y, d, w in windows:
        cuts.append(_opening_rect(x, y, d, w))
    openings = unary_union(cuts) if cuts and gaps else box(0, 0, 0, 0)

    if style == "lines":
        lines = union.boundary.difference(openings)
        for ls in getattr(lines, "geoms", [lines]):
            if isinstance(ls, LineString):
                coords = list(ls.coords)
                for a, b in zip(coords, coords[1:]):
                    msp.add_line(a, b, dxfattribs={"layer": "MURI"})
    elif style == "jambs":  # like "lines", but the wall ends at each opening are drawn too
        solid = union.difference(openings)
        for ls in getattr(solid.boundary, "geoms", [solid.boundary]):
            coords = list(ls.coords)
            for a, b in zip(coords, coords[1:]):
                msp.add_line(a, b, dxfattribs={"layer": "MURI"})
    elif style == "polylines":
        solid = union.difference(openings)
        for poly in getattr(solid, "geoms", [solid]):
            msp.add_lwpolyline(list(poly.exterior.coords)[:-1], close=True, dxfattribs={"layer": "MURI"})
            for hole in poly.interiors:
                msp.add_lwpolyline(list(hole.coords)[:-1], close=True, dxfattribs={"layer": "MURI"})
    elif style == "hatch":
        solid = union.difference(openings)
        for poly in getattr(solid, "geoms", [solid]):
            h = msp.add_hatch(color=7, dxfattribs={"layer": "MURI"})
            h.paths.add_polyline_path(list(poly.exterior.coords)[:-1], is_closed=True)
            for hole in poly.interiors:
                h.paths.add_polyline_path(list(hole.coords)[:-1], is_closed=True)
    elif style == "centerline":
        msp.add_lwpolyline([(15, 15), (785, 15), (785, 585), (15, 585)], close=True,
                           dxfattribs={"layer": "MURI"})
        msp.add_line((400, 15), (400, 400), dxfattribs={"layer": "MURI"})
        msp.add_line((400, 400), (785, 400), dxfattribs={"layer": "MURI"})
    else:
        raise ValueError(style)

    # Doors: one block per width, inserted at the hinge and rotated to follow the wall.
    for _name, x, y, d, w in doors:
        bname = f"PORTA{int(w)}"
        if bname not in doc.blocks:
            _door_block(doc, bname, w)
        if d == "h":  # opening along +x, leaf swings towards +y
            msp.add_blockref(bname, (x, y), dxfattribs={"layer": "PORTE", "rotation": 0})
        else:  # opening along +y, hinge at its top end, leaf swings towards +x
            msp.add_blockref(bname, (x, y + w), dxfattribs={"layer": "PORTE", "rotation": -90})

    # Windows: three parallel lines across the wall (sill / glass / sill).
    for x, y, d, w in windows:
        if d == "h":
            x0, x1 = x, x + w
            for off in (-THICK / 2, 0, THICK / 2):
                msp.add_line((x0, y + off), (x1, y + off), dxfattribs={"layer": "FINESTRE"})
            msp.add_line((x0, y - THICK / 2), (x0, y + THICK / 2), dxfattribs={"layer": "FINESTRE"})
            msp.add_line((x1, y - THICK / 2), (x1, y + THICK / 2), dxfattribs={"layer": "FINESTRE"})
        else:
            y0, y1 = y, y + w
            for off in (-THICK / 2, 0, THICK / 2):
                msp.add_line((x + off, y0), (x + off, y1), dxfattribs={"layer": "FINESTRE"})
            msp.add_line((x - THICK / 2, y0), (x + THICK / 2, y0), dxfattribs={"layer": "FINESTRE"})
            msp.add_line((x - THICK / 2, y1), (x + THICK / 2, y1), dxfattribs={"layer": "FINESTRE"})

    # Things that must be ignored.
    doc.layers.add("QUOTE")
    msp.add_text("Soggiorno", dxfattribs={"layer": "QUOTE", "height": 20, "insert": (200, 300)})
    msp.add_line((0, -50), (800, -50), dxfattribs={"layer": "QUOTE"})

    path = Path(path)
    doc.saveas(path)
    return path
