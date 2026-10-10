"""The floor that covers what no room's floor does (open plans, rooms the walls do not close)."""

import ezdxf
import pytest
from builders import building
from helpers import Obj
from shapely.geometry import Point, box

from dwg2c4d import Config, convert
from dwg2c4d.floors import building_outline


def open_plan(tmp_path):
    """A 10 x 6 m building whose south wall has a 3 m opening (x 400..700): the main space is no closed room. In the
    north-east corner a closed kitchen (10 cm partitions) with its name written in it."""
    doc = ezdxf.new("R2018", setup=True)
    doc.units = 5
    for name in ("MURI", "Quote e Testi"):
        doc.layers.add(name)
    msp = doc.modelspace()
    for x0, y0, x1, y1 in ((0, 0, 400, 30), (700, 0, 1000, 30), (0, 570, 1000, 600), (0, 0, 30, 600), (970, 0, 1000, 600),
                           (700, 300, 710, 570), (700, 300, 970, 310)):
        msp.add_lwpolyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], close=True, dxfattribs={"layer": "MURI"})
    msp.add_mtext("CUCINA", dxfattribs={"insert": (840, 440), "char_height": 10, "layer": "Quote e Testi",
                                        "attachment_point": 5})
    doc.saveas(tmp_path / "open.dxf")
    return tmp_path / "open.dxf"


def run(path, tmp_path, name="o"):
    return convert(path, tmp_path / f"{name}.obj", Config(area=(-200, -200, 1200, 800), floors_to_outer_face=False))


def floor_groups(obj):
    return sorted(g for g in obj.groups if g.startswith("Pavimento"))


def test_what_no_room_covers_gets_a_floor_of_its_own(tmp_path):
    rep = run(open_plan(tmp_path), tmp_path)
    shapes = dict(rep.plan.floors)
    assert set(shapes) == {"Cucina", "Pavimento"}
    rest = shapes["Pavimento"]
    assert rest.covers(Point(2.0, 3.0)) and rest.covers(Point(5.5, 0.15)) and rest.covers(Point(5.5, 5.0))  # the open space
    assert not rest.covers(Point(8.5, 4.4))  # the kitchen has its own floor
    assert not rest.covers(Point(5.5, -0.5)) and not rest.covers(Point(-0.5, 3.0))  # nothing outside the building
    assert shapes["Cucina"].area == pytest.approx(2.6 * 2.6, abs=0.01)  # the room's own floor: 7.1 x 2.6 minus the walls
    assert shapes["Cucina"].intersection(rest).area == pytest.approx(0.0, abs=1e-4)
    assert floor_groups(Obj(rep.output)) == ["Pavimento", "Pavimento_Cucina"]
    assert any(w.startswith("Pavimento:") and "Controlla" in w for w in rep.warnings)  # a guess: the report says so


def test_the_floor_of_a_room_is_the_same_with_or_without_the_one_that_covers_the_rest(tmp_path, monkeypatch):
    with_rest = dict(run(open_plan(tmp_path), tmp_path).plan.floors)
    monkeypatch.setattr("dwg2c4d.pipeline.floor_left_over", lambda solid, found: found)
    alone = dict(run(open_plan(tmp_path), tmp_path, "o2").plan.floors)
    assert set(alone) == {"Cucina"} and alone["Cucina"].equals(with_rest["Cucina"])


def test_a_lone_wall_has_no_outline_and_a_wall_with_a_wide_gap_has_one():
    assert building_outline(box(0, 0, 8, 0.3)).is_empty  # a wall holds no space
    ring = box(0, 0, 10, 6).difference(box(0.3, 0.3, 9.7, 5.7)).difference(box(3, -1, 6, 1))  # a 3 m gap in the south wall
    assert building_outline(ring).area == pytest.approx(60.0, abs=0.5)  # the gap is bridged: the whole building
    wide = box(0, 0, 10, 6).difference(box(0.3, 0.3, 9.7, 5.7)).difference(box(2, -1, 8, 1))  # 6 m: open space
    assert building_outline(wide).area < 20.0


def test_the_floor_layer_of_the_drawing_is_all_the_floor_there_is(tmp_path):
    doc, msp = building()
    doc.layers.add("21 Pavimenti")
    msp.add_lwpolyline([(30, 30), (500, 30), (500, 570), (30, 570)], close=True, dxfattribs={"layer": "21 Pavimenti"})
    doc.saveas(tmp_path / "l.dxf")
    rep = run(tmp_path / "l.dxf", tmp_path)
    assert [n for n, _ in rep.plan.floors] == ["Locale_01"]  # what the layer does not cover stays bare: the user said so


def test_a_building_with_no_closed_room_at_all_still_gets_a_floor(tmp_path):
    doc = ezdxf.new("R2018", setup=True)
    doc.units = 5
    doc.layers.add("MURI")
    msp = doc.modelspace()
    for x0, y0, x1, y1 in ((0, 0, 400, 30), (700, 0, 1000, 30), (0, 570, 1000, 600), (0, 0, 30, 600), (970, 0, 1000, 600)):
        msp.add_lwpolyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], close=True, dxfattribs={"layer": "MURI"})
    doc.saveas(tmp_path / "bare.dxf")
    rep = run(tmp_path / "bare.dxf", tmp_path)
    assert [n for n, _ in rep.plan.floors] == ["Pavimento"] and rep.plan.floors[0][1].covers(Point(2.0, 3.0))
    assert not any("non generati" in w for w in rep.warnings)
