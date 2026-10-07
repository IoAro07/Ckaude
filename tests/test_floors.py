"""Floors per room, skirting boards, partitions as their own object."""

import json

import pytest
from builders import D, DOOR, PLAN_AREA, T, W, building
from helpers import Obj

from dwg2c4d import Config, convert
from dwg2c4d.config import LayerRules
from dwg2c4d.export import to_model_dict


def mtext(msp, text, x, y):
    msp.add_mtext(text, dxfattribs={"insert": (x, y), "char_height": 10, "layer": "Quote e Testi",
                                    "attachment_point": 5})


def divided(tmp_path, names=True):
    """The building cut in two rooms by a partition at x = 500 (10 cm thick), a door on the south wall."""
    doc, msp = building()
    doc.layers.add("Quote e Testi")
    msp.add_lwpolyline([(500, T - 1), (510, T - 1), (510, D - T + 1), (500, D - T + 1)], close=True,
                       dxfattribs={"layer": "MURI"})
    if names:
        mtext(msp, "SALA", 250, 300)
        mtext(msp, "CUCINA", 750, 300)
    path = tmp_path / "f.dxf"
    doc.saveas(path)
    return path


def run(path, tmp_path, name="o", **kw):
    return convert(path, tmp_path / f"{name}.obj", Config(area=PLAN_AREA, **kw))


def floor_groups(obj):
    return sorted(g for g in obj.groups if g.startswith("Pavimento"))


# --- floors ----------------------------------------------------------------------------------

def test_one_floor_object_per_room_named_after_it(tmp_path):
    rep = run(divided(tmp_path), tmp_path)
    obj = Obj(rep.output)
    assert floor_groups(obj) == ["Pavimento_Cucina", "Pavimento_Sala"]
    lo, hi = obj.bbox("Pavimento_Sala")
    assert hi[0] <= 5.0 + 1e-6 and lo[0] >= 0.3 - 1e-6  # inside its own room
    assert obj.volume("Pavimento_Sala") == pytest.approx(4.7 * 5.4 * 0.20 + 0.9 * 0.3 * 0.2, rel=0.01)  # + door threshold


def test_the_floor_runs_under_the_door_threshold(tmp_path):
    obj = Obj(run(divided(tmp_path), tmp_path).output)
    lo, _ = obj.bbox("Pavimento_Sala")
    # the door (x 2.0..2.9) cuts the wall (y 0..0.3): the floor reaches the outer face there
    south = [v for ids, _ in obj.groups["Pavimento_Sala"] for v in (obj.v[i] for i in ids) if 1.99 < v[0] < 2.91]
    assert max(v[2] for v in south) == pytest.approx(0.0, abs=3e-3) and lo[2] < -5.0  # OBJ z = -y


def test_rooms_without_a_name_are_numbered(tmp_path):
    names = floor_groups(Obj(run(divided(tmp_path, names=False), tmp_path).output))
    assert names == ["Pavimento_Locale_01", "Pavimento_Locale_02"]


def test_a_single_unnamed_room_keeps_one_slab_over_the_whole_footprint(tmp_path):
    doc, _ = building()
    doc.saveas(tmp_path / "s.dxf")
    obj = Obj(run(tmp_path / "s.dxf", tmp_path).output)
    assert floor_groups(obj) == ["Pavimento"]


def test_by_room_can_be_switched_off(tmp_path):
    obj = Obj(run(divided(tmp_path), tmp_path, floors_by_room=False).output)
    assert floor_groups(obj) == ["Pavimento"]


def test_floor_layer_shapes_are_the_floors(tmp_path):
    doc, msp = building()
    doc.layers.add("21 Pavimenti")
    doc.layers.add("Quote e Testi")
    msp.add_lwpolyline([(30, 30), (500, 30), (500, 570), (30, 570)], close=True, dxfattribs={"layer": "21 Pavimenti"})
    msp.add_lwpolyline([(500, 30), (970, 30), (970, 570), (500, 570)], close=True, dxfattribs={"layer": "21 Pavimenti"})
    mtext(msp, "SOGGIORNO", 250, 300)
    doc.saveas(tmp_path / "l.dxf")
    obj = Obj(run(tmp_path / "l.dxf", tmp_path).output)
    first, second = floor_groups(obj)
    assert first.startswith("Pavimento_Locale_0") and second == "Pavimento_Soggiorno"
    assert obj.volume("Pavimento_Soggiorno") == pytest.approx(4.7 * 5.4 * 0.2, rel=0.001)


def test_a_floor_shape_inside_another_is_cut_out_of_it(tmp_path):
    doc, msp = building()
    doc.layers.add("Pavimenti")
    msp.add_lwpolyline([(30, 30), (970, 30), (970, 570), (30, 570)], close=True, dxfattribs={"layer": "Pavimenti"})
    msp.add_lwpolyline([(300, 200), (600, 200), (600, 400), (300, 400)], close=True, dxfattribs={"layer": "Pavimenti"})
    doc.saveas(tmp_path / "n.dxf")
    obj = Obj(run(tmp_path / "n.dxf", tmp_path).output)
    total = sum(obj.volume(g) for g in floor_groups(obj))
    assert total == pytest.approx(9.4 * 5.4 * 0.2, rel=0.001)  # no overlap: no double volume


def test_layer_rules_know_floors_skirting_and_partitions():
    rules = LayerRules()
    assert rules.classify_layer("21 Pavimenti") == "floor"
    assert rules.classify_layer("20 Battiscopa") == "skirting"
    assert rules.classify_layer("19 Fondelli") == "wall" and rules.is_partition("19 Fondelli")
    assert not rules.is_partition("18 Muri") and rules.classify_layer("Pavimento_esterno") == "floor"


# --- skirting --------------------------------------------------------------------------------

def test_skirting_strip_stands_on_the_room_side_of_the_wall(tmp_path):
    doc, msp = building()
    doc.layers.add("20 Battiscopa")
    msp.add_lwpolyline([(30, 40), (30, 560)], dxfattribs={"layer": "20 Battiscopa"})  # on the west wall's face
    doc.saveas(tmp_path / "b.dxf")
    obj = Obj(run(tmp_path / "b.dxf", tmp_path).output)
    lo, hi = obj.bbox("Battiscopa")
    assert (lo[1], hi[1]) == (pytest.approx(0.0), pytest.approx(0.08))
    assert lo[0] == pytest.approx(0.30, abs=1e-6) and hi[0] == pytest.approx(0.312, abs=1e-6)  # into the room
    assert lo[2] == pytest.approx(-5.6) and hi[2] == pytest.approx(-0.4)


def test_skirting_stops_at_the_door(tmp_path):
    doc, msp = building()
    doc.layers.add("Battiscopa")
    msp.add_lwpolyline([(30, 30), (970, 30)], dxfattribs={"layer": "Battiscopa"})  # along the south wall
    doc.saveas(tmp_path / "b.dxf")
    obj = Obj(run(tmp_path / "b.dxf", tmp_path).output)
    xs = sorted({round(float(obj.v[i][0]), 3) for ids, _ in obj.groups["Battiscopa"] for i in ids})
    assert not any(DOOR[0] / 100 + 0.02 < x < DOOR[1] / 100 - 0.02 for x in xs)
    assert min(xs) == pytest.approx(0.3) and max(xs) == pytest.approx(9.7)


def test_skirting_height_and_thickness_options(tmp_path):
    doc, msp = building()
    doc.layers.add("Battiscopa")
    msp.add_lwpolyline([(30, 100), (30, 400)], dxfattribs={"layer": "Battiscopa"})
    doc.saveas(tmp_path / "b.dxf")
    obj = Obj(run(tmp_path / "b.dxf", tmp_path, skirting_height=0.12, skirting_thickness=0.02).output)
    lo, hi = obj.bbox("Battiscopa")
    assert hi[1] == pytest.approx(0.12) and hi[0] - lo[0] == pytest.approx(0.02)


# --- partitions ------------------------------------------------------------------------------

def partitions_plan(tmp_path):
    doc, msp = building()
    doc.layers.add("19 Fondelli")
    msp.add_lwpolyline([(500, T - 1), (510, T - 1), (510, 250), (500, 250)], close=True,
                       dxfattribs={"layer": "19 Fondelli"})
    msp.add_lwpolyline([(500, 330), (510, 330), (510, D - T + 1), (500, D - T + 1)], close=True,
                       dxfattribs={"layer": "19 Fondelli"})
    path = tmp_path / "t.dxf"
    doc.saveas(path)
    return path


def test_partition_layers_become_their_own_object(tmp_path):
    path = partitions_plan(tmp_path)
    split = Obj(run(path, tmp_path).output)
    joined = Obj(run(path, tmp_path, "o2", partitions_apart=False).output)
    assert "Tramezzi" in split.groups and "Tramezzi" not in joined.groups
    # same walls, only the grouping differs (the 80 cm passage between them is a doorway: lintel included)
    assert split.volume("Muri") + split.volume("Tramezzi") == pytest.approx(joined.volume("Muri"), rel=0.01)
    lo, hi = split.bbox("Tramezzi")
    assert lo[0] == pytest.approx(5.0, abs=0.02) and hi[0] == pytest.approx(5.1, abs=0.02)


def test_a_passage_between_partitions_belongs_to_the_partitions(tmp_path):
    split = Obj(run(partitions_plan(tmp_path), tmp_path).output)
    # the lintel's underside (2.10 m) over the 80 cm gap (y = 2.5..3.3) is in the partition object
    under = [v for ids, _ in split.groups["Tramezzi"] for v in (split.v[i] for i in ids)
             if abs(v[1] - 2.1) < 1e-6 and 2.4 < -v[2] < 3.4]
    assert under
    assert not [v for ids, _ in split.groups["Muri"] for v in (split.v[i] for i in ids)
                if abs(v[1] - 2.1) < 1e-6 and 4.9 < v[0] < 5.2 and 2.4 < -v[2] < 3.4]


def test_exports_know_the_new_groups(tmp_path):
    rep = run(divided(tmp_path), tmp_path, c4d_json=True)
    model = json.loads(rep.json_path.read_text())
    by_name = {o["name"]: o for o in model["objects"]}
    assert by_name["Pavimento_Sala"]["group"] == "Pavimenti" and by_name["Pavimento_Sala"]["material"] == "floor"
    mtl = rep.output.with_suffix(".mtl").read_text()
    assert "newmtl Pavimento_Sala" in mtl and "Kd 0.6 0.56 0.52" in mtl
    assert "floor" in model["materials"] and W == 1000
    doc, msp = building()
    doc.layers.add("Battiscopa")
    msp.add_lwpolyline([(30, 100), (30, 400)], dxfattribs={"layer": "Battiscopa"})
    doc.saveas(tmp_path / "k.dxf")
    model = to_model_dict(run(tmp_path / "k.dxf", tmp_path, "k").mesh, "k")
    assert {o["name"]: o["material"] for o in model["objects"]}["Battiscopa"] == "skirting"
