"""Texts that were exploded into lines are read back as texts."""

import math

import pytest
from builders import PLAN_AREA, T, WIN_S, building
from helpers import Obj

from dwg2c4d import Config, convert

textpath = pytest.importorskip("matplotlib.textpath")
from matplotlib.font_manager import FontProperties  # noqa: E402

SOUTH_WIN_X = (WIN_S[0] + WIN_S[1]) / 2


def explode(msp, text, x, y, height=10.0, angle=0.0, layer="0", as_lines=False):
    """Draw ``text`` as outlines (what EXPLODE leaves): lower-left corner at (x, y), cap height in cm."""
    tp = textpath.TextPath((0, 0), text, size=height / 0.73, prop=FontProperties(family="DejaVu Sans"))
    ca, sa = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    for poly in tp.to_polygons():
        pts = [(x + px * ca - py * sa, y + px * sa + py * ca) for px, py in poly]
        if as_lines:
            for a, b in zip(pts, pts[1:]):
                msp.add_line(a, b, dxfattribs={"layer": layer})
        else:
            msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": layer})


def run(doc, tmp_path, **kw):
    path = tmp_path / "v.dxf"
    doc.saveas(path)
    return convert(path, tmp_path / "v.obj", Config(area=PLAN_AREA, **kw))


def window(rep):
    return next(o for o in rep.openings if o.kind == "window" and o.center[1] < 3.0)


def test_exploded_size_and_sill_are_read(tmp_path):
    doc, msp = building()
    explode(msp, "120", SOUTH_WIN_X - 20, -60)      # width over height, outside the south wall
    explode(msp, "150", SOUTH_WIN_X - 20, -78)
    explode(msp, "ht 95", SOUTH_WIN_X - 20, -105, height=5)
    rep = run(doc, tmp_path)
    win = window(rep)
    assert (win.z0, win.z1) == (pytest.approx(0.95), pytest.approx(2.45))
    assert win.src["height"] == "scritta"


@pytest.mark.parametrize("as_lines", [False, True])
def test_polylines_and_loose_lines_both_work(tmp_path, as_lines):
    doc, msp = building()
    explode(msp, "120x130", SOUTH_WIN_X - 30, -60, as_lines=as_lines)
    assert window(run(doc, tmp_path)).z1 - window(run(doc, tmp_path)).z0 == pytest.approx(1.3)


@pytest.mark.parametrize("angle", [0, 90, 270])
def test_rotated_text(tmp_path, angle):
    doc, msp = building()
    # place the rotated block so that it sits about 50 cm from the window, on the outside
    x, y = (SOUTH_WIN_X - 20, -60) if angle == 0 else ((SOUTH_WIN_X - 60, -30) if angle == 90 else (SOUTH_WIN_X + 60, -35))
    explode(msp, "120x140", x, y, angle=angle)
    win = window(run(doc, tmp_path))
    assert win.z1 - win.z0 == pytest.approx(1.4), angle


def test_room_name_and_height_from_exploded_text(tmp_path):
    doc, msp = building()
    explode(msp, "SOGGIORNO", 440, 300)
    explode(msp, "h 300", 440, 280)
    rep = run(doc, tmp_path)
    assert rep.rooms[0]["name"] == "Soggiorno" and rep.rooms[0]["height"] == 3.0
    assert rep.wall_height == 3.0


def test_a_name_of_two_words_is_one_name(tmp_path):
    doc, msp = building()
    explode(msp, "CAMERA", 380, 300)
    explode(msp, "DA LETTO", 380 + 6 * 8.5 + 7, 300)
    assert run(doc, tmp_path).rooms[0]["name"] == "Camera da letto"


def test_switched_off(tmp_path):
    doc, msp = building()
    explode(msp, "120x130", SOUTH_WIN_X - 30, -60)
    rep = run(doc, tmp_path, vector_text=False)
    assert rep.labels == 0


def test_plain_linework_on_layer_zero_is_not_text(tmp_path):
    doc, msp = building()
    for i in range(30):  # a hatch-like fan of long lines: none of it is a letter
        msp.add_line((100 + i * 20, 100), (150 + i * 20, 500), dxfattribs={"layer": "0"})
    rep = run(doc, tmp_path)
    assert rep.labels == 0 and rep.rooms[0]["name"].startswith("Locale")


def test_text_layers_option(tmp_path):
    doc, msp = building()
    doc.layers.add("XYZ-7")
    explode(msp, "120x130", SOUTH_WIN_X - 30, -60, layer="XYZ-7")
    assert run(doc, tmp_path).labels == 0
    rep = run(doc, tmp_path, text_layers=["xyz-*"])
    assert rep.labels == 1 and Obj(rep.output).verts  # found only when the layer is named
    assert T == 30
