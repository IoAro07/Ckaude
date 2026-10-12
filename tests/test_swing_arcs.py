"""Doors drawn as nothing but a swing arc (and the leaf lines): on the wall layer, or inside a block of layer 0."""

import ezdxf
import pytest
from shapely.geometry import Point

from dwg2c4d import Config, convert
from dwg2c4d.config import LayerRules
from dwg2c4d.reader import read_items

AT = {"layer": "LINEE"}
GAP = (250, 340)  # cm: the door opening in the partition at x = 500..510
RADIUS = 90  # cm: the leaf


def rect(msp, x0, y0, x1, y1, **at):
    msp.add_lwpolyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], close=True, dxfattribs=at or AT)


def swing_sheet(path, leaf="both", block=False):
    """A 10 x 6 m building of 30 cm outlines with a 10 cm partition (two lines) at x = 500..510 and a 90 cm door gap in it
    (y 250..340). The door is drawn with its hinge at the south jamb: a quarter-circle arc swept to the east, and the
    leaf in its two positions (``leaf`` "both": the shut one in the gap and the open one; "open": only the open one;
    "none"). ``block``: the symbol is a block of layer 0 inserted on a layer that says nothing."""
    doc = ezdxf.new("R2018", setup=True)
    doc.units = 5
    doc.layers.add("LINEE")
    doc.layers.add("BLOCCHI")
    msp = doc.modelspace()
    rect(msp, 0, 0, 1000, 600)
    rect(msp, 30, 30, 970, 570)
    for x in (500, 510):
        msp.add_line((x, 30), (x, GAP[0]), dxfattribs=AT)
        msp.add_line((x, GAP[1]), (x, 570), dxfattribs=AT)
    hinge = (505, GAP[0])
    if block:
        sym = doc.blocks.new("SIMBOLO_7")
        target, at = sym, {"layer": "0"}
    else:
        target, at = msp, AT
    target.add_arc(hinge, RADIUS, 0, 90, dxfattribs=at)
    if leaf in ("both", "open"):
        target.add_line(hinge, (hinge[0] + RADIUS, hinge[1]), dxfattribs=at)
    if leaf == "both":
        target.add_line(hinge, (hinge[0], hinge[1] + RADIUS), dxfattribs=at)
    if block:
        msp.add_blockref("SIMBOLO_7", (0, 0), dxfattribs={"layer": "BLOCCHI"})
    doc.saveas(path)
    return path


def library(**kw):
    """The settings of a drawing whose layers say nothing and whose walls are found by their shape."""
    return Config(units="cm", shape_openings=True, pair_layers=("LINEE",), layers=LayerRules({"wall": ["LINEE"]}), **kw)


def doors(report):
    return [o for o in report.openings if o.kind == "door"]


def test_a_door_symbol_on_the_wall_layer_is_a_door_and_makes_no_wall_of_its_own(tmp_path):
    report = convert(swing_sheet(tmp_path / "s.dxf"), tmp_path / "s.obj", library())
    (door,) = doors(report)
    assert door.src["kind"] == "forma" and door.width == pytest.approx(0.90, abs=0.03)
    assert door.center[0] == pytest.approx(5.05, abs=0.03) and door.center[1] == pytest.approx(2.95, abs=0.03)
    solid = report.plan.solid_walls
    assert not solid.covers(Point(5.5, 2.9))  # the quarter disc the leaf sweeps is air, not a 2.7 m high wall
    assert solid.covers(Point(5.05, 1.5)) and solid.covers(Point(5.05, 4.5))  # the partition is there on both sides of the door


@pytest.mark.parametrize("leaf", ["open", "none"])
def test_a_swing_arc_with_one_leaf_or_none_is_still_a_door(tmp_path, leaf):
    report = convert(swing_sheet(tmp_path / f"{leaf}.dxf", leaf=leaf), tmp_path / f"{leaf}.obj", library())
    assert len(doors(report)) == 1 and not report.plan.solid_walls.covers(Point(5.5, 2.9))


def test_a_rounded_wall_corner_is_still_a_wall(tmp_path):
    """The arc of a round corner has the same radius and span as a swing, but no leaf line joins its centre to its ends."""
    doc = ezdxf.new("R2018", setup=True)
    doc.units = 5
    doc.layers.add("LINEE")
    msp = doc.modelspace()
    rect(msp, 0, 0, 1000, 600)
    rect(msp, 30, 30, 970, 570)
    msp.add_arc((1000, 600), 100, 180, 270, dxfattribs=AT)  # an outer rounded corner, 100 cm, outside the building
    path = tmp_path / "corner.dxf"
    doc.saveas(path)
    walls = read_items(ezdxf.readfile(path), library()).items
    assert any(p.meta.get("arc") for it in walls if it.category == "wall" for p in it.prims)  # the arc stays among the wall lines


def test_a_door_symbol_in_a_block_of_layer_0_is_read(tmp_path):
    report = convert(swing_sheet(tmp_path / "b.dxf", block=True), tmp_path / "b.obj", library())
    (door,) = doors(report)
    assert door.src["kind"] == "forma" and door.center[0] == pytest.approx(5.05, abs=0.03)
    assert not report.plan.solid_walls.covers(Point(5.5, 2.9))
