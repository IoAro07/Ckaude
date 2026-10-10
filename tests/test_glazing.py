"""Ribbon windows drawn as nothing but thin glazing strips along the outside wall (no layer or block says "window")."""

import ezdxf
import numpy as np
import pytest
from shapely.geometry import Point, box
from helpers import Obj

from dwg2c4d import Config, convert
from dwg2c4d.config import LayerRules
from dwg2c4d.glazing import find_bays, find_strips
from dwg2c4d.reader import read_items

AT = {"layer": "LINEE"}
BAYS = ((62, 468), (532, 938))  # the glass of the two windows, in the south wall (cm)
SASH = 120  # cm
MULLION = 23  # cm


def rect(msp, x0, y0, x1, y1):
    msp.add_lwpolyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], close=True, dxfattribs=AT)


def sashes(msp, x0, x1, y, rings=True):
    """Three sashes between x0 and x1: two lines 5 cm apart each (and a 2 cm pane between them)."""
    for k in range(3):
        a = x0 + k * (SASH + MULLION)
        for yy in (y, y + 5):
            msp.add_line((a, yy), (a + SASH, yy), dxfattribs=AT)
        if rings:
            msp.add_lwpolyline([(a, y + 1.5), (a + SASH, y + 1.5), (a + SASH, y + 3.5), (a, y + 3.5)], close=True,
                               dxfattribs=AT)


def ribbon_sheet(path, partition_strip=False, door_leaf=False):
    """A 10 x 6 m building, all on one layer called LINEE. The south wall is 50 cm thick and drawn as the outside face
    line (y = 0), three piers (closed outlines) and, in each of the two bays between them, three sashes of glass
    just inside the inner face (y = 52..57); the other walls are 30 cm closed outlines."""
    doc = ezdxf.new("R2018", setup=True)
    doc.units = 5
    doc.layers.add("LINEE")
    doc.layers.add("TESTI")
    msp = doc.modelspace()
    msp.add_text("PIANTA PIANO TERRA", height=20, dxfattribs={"layer": "TESTI", "insert": (350, -120)})
    msp.add_line((0, 0), (1000, 0), dxfattribs=AT)  # the outside face of the south wall
    rect(msp, 0, 0, 60, 50)
    rect(msp, 470, 0, 530, 50)
    rect(msp, 480, 50, 520, 110)  # the middle pier has a pilaster (it is no free-standing speck)
    rect(msp, 940, 0, 1000, 50)
    rect(msp, 0, 0, 30, 600)
    rect(msp, 970, 0, 1000, 600)
    rect(msp, 0, 570, 1000, 600)
    for x0, x1 in BAYS:
        sashes(msp, x0, x1, 52)
    for x0 in (100, 450):  # two closed rooms inside, drawn as an outer and an inner outline (10 cm walls)
        rect(msp, x0, 200, x0 + 200, 400)
        rect(msp, x0 + 10, 210, x0 + 190, 390)
    if partition_strip:  # a glass partition in the room: two lines 3 cm apart, free at both ends
        for x in (800, 803):
            msp.add_line((x, 100), (x, 300), dxfattribs=AT)
    if door_leaf:  # a partition with a door gap at 250..340, the shut leaf in the gap: 4 cm thick, 90 cm long
        rect(msp, 700, 50, 730, 250)
        rect(msp, 700, 340, 730, 570)
        for x in (713, 717):
            msp.add_line((x, 250), (x, 340), dxfattribs=AT)
    doc.saveas(path)
    return path


def library(**kw):
    """The settings of a drawing whose layers say nothing and whose walls are found by their shape."""
    return Config(units="cm", shape_openings=True, pair_layers=("LINEE",), layers=LayerRules({"wall": ["LINEE"]}), **kw)


def windows(report):
    return [o for o in report.openings if o.kind == "window"]


@pytest.fixture(scope="module")
def sheet(tmp_path_factory):
    return ribbon_sheet(tmp_path_factory.mktemp("ribbon") / "nastro.dxf")


# --- the strips and the bays -------------------------------------------------------------------

def seg(*rows):
    return np.array(rows, float)


def test_two_thin_parallel_lines_are_a_strip_and_one_line_or_a_wide_pair_is_not():
    assert len(find_strips(seg((0, 0, 1.4, 0), (0, 0.05, 1.4, 0.05)))) == 1
    assert not find_strips(seg((0, 0, 1.4, 0)))
    assert not find_strips(seg((0, 0, 1.4, 0), (0, 0, 1.4, 0)))  # the same line drawn twice
    assert not find_strips(seg((0, 0, 1.4, 0), (0, 0.08, 1.4, 0.08)))  # a thin wall, not glass
    assert not find_strips(seg((0, 0, 4.0, 0), (0, 0.05, 4.0, 0.05)))  # too long for a sash
    assert not find_strips(seg((0, 0, 0.4, 0), (0, 0.05, 0.4, 0.05)))  # too short
    assert not find_strips(seg((0, 0, 1.4, 0), (0, 0.05, 1.4, 0.05), (0, 0.10, 1.4, 0.10)))  # three lines spread over 10 cm


def test_a_strip_may_be_turned_and_made_of_a_pane_and_its_frame():
    (strip,) = find_strips(seg((0, 0, 1.0, 1.0), (0.0354, -0.0354, 1.0354, 0.9646), (0, 0.01, 1.0, 1.01)))
    assert strip.a1 - strip.a0 == pytest.approx(1.414, abs=0.02)


def test_sashes_a_mullion_apart_are_one_bay_and_a_wider_gap_makes_two():
    row = [(a, 0, a + 1.2, 0) for a in (0, 1.43, 2.86)] + [(a, 0.05, a + 1.2, 0.05) for a in (0, 1.43, 2.86)]
    (bay,) = find_bays(find_strips(seg(*row)))
    assert bay.a1 - bay.a0 == pytest.approx(4.06, abs=0.01) and len(bay.mullions) == 2
    far = [(a, 0, a + 1.2, 0) for a in (0, 1.7)] + [(a, 0.05, a + 1.2, 0.05) for a in (0, 1.7)]
    assert len(find_bays(find_strips(seg(*far)))) == 2


def test_strips_not_on_one_line_are_not_one_bay():
    rows = [(0, 0, 1.2, 0), (0, 0.05, 1.2, 0.05), (1.4, 0.4, 2.6, 0.4), (1.4, 0.45, 2.6, 0.45)]
    assert len(find_bays(find_strips(seg(*rows)))) == 2


def test_the_reader_keeps_the_long_straight_pieces_only_when_asked_to_look_for_shapes(sheet):
    doc = ezdxf.readfile(sheet)
    assert len(read_items(doc, library()).thin) > 20
    assert len(read_items(doc, Config(units="cm", layers=LayerRules({"wall": ["LINEE"]}))).thin) == 0


# --- the windows -------------------------------------------------------------------------------

def test_the_glazing_strips_of_the_south_wall_are_two_windows(sheet, tmp_path):
    report = convert(sheet, tmp_path / "n.obj", library(fixtures="detailed"))
    south = windows(report)
    assert len(south) == report.windows == 2
    for op, (x0, x1) in zip(sorted(south, key=lambda o: o.center[0]), BAYS):
        assert op.axis == (1.0, 0.0) and op.thickness == pytest.approx(0.50, abs=0.01)
        assert (op.z0, op.z1) == (pytest.approx(0.9), pytest.approx(2.2))
        assert op.width == pytest.approx((x1 - x0) / 100 + 0.04, abs=0.03)  # the glass and a hand's width to the piers
        assert op.center[1] == pytest.approx(0.25, abs=0.01) and op.sashes == 3
        assert op.src["kind"] == "strisce di vetro" and any("a nastro" in n for n in op.notes)
    assert any("finestre a nastro" in w for w in report.warnings)


def test_the_wall_is_continuous_around_the_window_and_the_glass_makes_no_slab(sheet, tmp_path):
    report = convert(sheet, tmp_path / "w.obj", library())
    solid = report.plan.solid_walls
    for x in (1.5, 3.0, 4.2, 6.5, 8.0):  # over the sashes and the mullions: the wall is there (the opening is cut out of it)
        assert solid.covers(Point(x, 0.25))
    for x in (1.5, 3.0, 4.2):  # ... but not the 50 cm slab the glass lines made with the outside face line
        assert not solid.covers(Point(x, 0.55))
    obj = Obj(report.output)
    assert "Vetri_F01" in obj.groups or "Vetri" in obj.groups
    # no wall stands in the window between the sill and the lintel: look at the wall faces at x = 1.5, y = 0..0.5
    low, high = obj.bbox("Muri")
    assert low[1] == pytest.approx(0.0) and high[1] == pytest.approx(2.7)
    middle = [v for ids, _ in obj.groups["Muri"] for v in (obj.v[i] for i in ids)]
    cx, cz = report.origin_offset
    inside = [v for v in middle if abs(v[0] - (1.5 + cx)) < 0.9 and abs(v[2] + (0.25 + report.origin_offset[1])) < 0.2 and 1.0 < v[1] < 2.1]
    assert not [v for v in inside if abs(v[0] - (1.5 + cx)) < 0.3]


def test_glazing_in_the_middle_of_a_room_is_no_window(tmp_path):
    report = convert(ribbon_sheet(tmp_path / "p.dxf", partition_strip=True), tmp_path / "p.obj", library())
    assert report.windows == 2  # the two bays of the wall, not the glass partition


def test_the_shut_leaf_of_a_door_in_an_inner_wall_is_no_window(tmp_path):
    report = convert(ribbon_sheet(tmp_path / "d.dxf", door_leaf=True), tmp_path / "d.obj", library())
    assert report.windows == 2
    assert not any(abs(o.center[1] - 2.9) < 1.0 for o in windows(report))  # the leaf is at y = 2.5..3.4, in the wall at x = 7.15


def test_what_the_layers_call_windows_is_not_read_again_by_shape(sheet, tmp_path):
    doc = ezdxf.readfile(sheet)
    doc.layers.add("FINESTRE")
    doc.modelspace().add_lwpolyline([(300, 0), (400, 0), (400, 50), (300, 50)], close=True, dxfattribs={"layer": "FINESTRE"})
    path = tmp_path / "named.dxf"
    doc.saveas(path)
    cfg = Config(units="cm", layers=LayerRules({"wall": ["LINEE"]}), pair_layers=("LINEE",))  # shape_openings off: the names rule
    report = convert(path, tmp_path / "named.obj", cfg)
    assert [o.layer for o in windows(report)] == ["FINESTRE"]


def test_the_whole_sheet_with_the_analysis_finds_the_windows_too(sheet, tmp_path):
    report = convert(sheet, tmp_path / "a.obj", Config(auto=True))
    assert report.windows == 2
