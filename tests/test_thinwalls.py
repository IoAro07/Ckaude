"""Walls drawn as one line, on a layer whose walls are drawn in pairs: taken as walls when they close a space."""

import ezdxf
import pytest
from helpers import Obj
from shapely.geometry import Point

from dwg2c4d import Config, convert
from dwg2c4d.config import LayerRules
from dwg2c4d.reader import read_items

AT = {"layer": "LINEE"}


def rect(msp, x0, y0, x1, y1):
    msp.add_lwpolyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], close=True, dxfattribs=AT)


def sheet(path, west="line", table=False, ltype=None):
    """A 10 x 6 m building on one layer: the south, north and east walls are 30 cm outlines, the west side has no wall of
    that kind: ``west`` "line": a single line along it (5.4 m); "pieces": the same line in four pieces of 1.35 m; None:
    nothing. ``ltype``: the linetype of the west line. ``table``: a free-standing single line, 2.5 m long, in the middle."""
    doc = ezdxf.new("R2018", setup=True)
    doc.units = 5
    doc.layers.add("LINEE")
    msp = doc.modelspace()
    rect(msp, 0, 0, 1000, 30)
    rect(msp, 0, 570, 1000, 600)
    rect(msp, 970, 30, 1000, 570)
    at = {**AT, **({"linetype": ltype} if ltype else {})}
    if west == "line":
        msp.add_line((30, 30), (30, 570), dxfattribs=at)
    elif west == "pieces":
        for k in range(4):
            msp.add_line((30, 30 + k * 135), (30, 30 + (k + 1) * 135), dxfattribs=at)
    if table:
        msp.add_line((400, 300), (650, 300), dxfattribs=AT)
    doc.saveas(path)
    return path


def library(**kw):
    """The settings of a drawing whose layers say nothing and whose walls are found by their shape."""
    return Config(units="cm", shape_openings=True, pair_layers=("LINEE",), layers=LayerRules({"wall": ["LINEE"]}), **kw)


def test_a_single_line_that_closes_the_building_is_a_thin_wall_and_the_room_has_a_floor(tmp_path):
    report = convert(sheet(tmp_path / "c.dxf"), tmp_path / "c.obj", library(area=(-200, -200, 1200, 800)))
    solid = report.plan.solid_walls
    assert solid.covers(Point(0.3, 1.0)) and solid.covers(Point(0.3, 5.0))  # along the line: a wall ...
    assert not solid.covers(Point(0.4, 3.0))  # ... 10 cm thick (the line is its axis), no thicker
    assert solid.covers(Point(0.0, 0.1)) and solid.covers(Point(0.3, 5.9)) and solid.covers(Point(0.3, 0.2))  # and joined to the walls at both ends
    assert len(report.rooms) == 1 and report.rooms[0]["area_m2"] == pytest.approx(9.35 * 5.4, abs=0.1)  # from the thin wall to the east wall, between the south and the north walls
    assert "Pavimento" in Obj(report.output).groups  # the floor under it (a single unnamed room: the one slab)
    assert any("linee singole" in w and "chiudono" in w for w in report.warnings)  # a guess: the report says so


def test_a_stub_of_wall_that_the_thin_wall_continues_is_kept_with_it(tmp_path):
    """The west side is a single line up to y = 4.5 m, then a 70 cm stub of wall drawn as two lines: alone it is a speck
    (dropped), but it is where the thin wall goes on, and a door in the gap beyond it needs a wall at its jamb."""
    doc = ezdxf.new("R2018", setup=True)
    doc.units = 5
    doc.layers.add("LINEE")
    msp = doc.modelspace()
    rect(msp, 0, 0, 1000, 30)
    rect(msp, 0, 570, 1000, 600)
    rect(msp, 970, 30, 1000, 570)
    msp.add_line((30, 30), (30, 450), dxfattribs=AT)
    for x in (25, 35):
        msp.add_line((x, 450), (x, 520), dxfattribs=AT)
    path = tmp_path / "stub.dxf"
    doc.saveas(path)
    report = convert(path, tmp_path / "stub.obj", library(area=(-200, -200, 1200, 800)))
    solid = report.plan.solid_walls
    assert solid.covers(Point(0.3, 2.0)) and solid.covers(Point(0.3, 4.85))  # the line, and the stub beyond its end


def test_without_that_line_the_building_is_open_and_has_no_room(tmp_path):
    report = convert(sheet(tmp_path / "o.dxf", west=None), tmp_path / "o.obj", library(area=(-200, -200, 1200, 800)))
    assert report.rooms == []


def test_a_line_that_closes_nothing_is_no_wall(tmp_path):
    report = convert(sheet(tmp_path / "t.dxf", table=True), tmp_path / "t.obj", library(area=(-200, -200, 1200, 800)))
    assert not report.plan.solid_walls.covers(Point(5.2, 3.0))  # the free-standing 2.5 m line in the big space
    assert report.plan.solid_walls.covers(Point(0.3, 3.0))  # while the line that closes the building is a wall
    assert len(report.rooms) == 1  # and the table line did not cut the space in two


def test_lines_shorter_than_two_metres_are_no_walls(tmp_path):
    report = convert(sheet(tmp_path / "s.dxf", west="pieces"), tmp_path / "s.obj", library(area=(-200, -200, 1200, 800)))
    assert not report.plan.solid_walls.covers(Point(0.3, 3.0)) and report.rooms == []


def test_a_dashed_line_is_no_wall(tmp_path):
    path = sheet(tmp_path / "x.dxf", ltype="DASHED")
    items = read_items(ezdxf.readfile(path), library()).items
    assert any(p.meta.get("broken") for it in items for p in it.prims)  # the reader knows the line is dashed
    report = convert(path, tmp_path / "x.obj", library(area=(-200, -200, 1200, 800)))
    assert not report.plan.solid_walls.covers(Point(0.3, 3.0)) and report.rooms == []


def test_continuous_lines_are_not_marked_broken(tmp_path):
    items = read_items(ezdxf.readfile(sheet(tmp_path / "k.dxf")), library()).items
    assert not any(p.meta.get("broken") for it in items for p in it.prims)


def test_on_a_layer_read_by_name_single_lines_stay_what_they_were(tmp_path):
    cfg = Config(units="cm", layers=LayerRules({"wall": ["LINEE"]}), area=(-200, -200, 1200, 800))  # no pair_layers: the names or the user chose the layer
    report = convert(sheet(tmp_path / "n.dxf"), tmp_path / "n.obj", cfg)
    assert report.plan.solid_walls.covers(Point(0.4, 3.0))  # a lone line there is the axis of a 30 cm wall (the centreline reading), not of a 10 cm one
    assert not any("linee singole" in w for w in report.warnings)
