"""The garden and the low walls laid on the terrain, the pool dug in it, the JSON of Cinema 4D. Synthetic sheets (centimetres):
the 10 x 6 m building on the slope of ``test_terrain`` (10 % up eastwards), the garden of ``test_garden`` to the south."""

import json

import numpy as np
import pytest
from builders import building
from helpers import Obj
from test_garden import GREEN, GREY, block, garden_plan, hatch, rect
from test_terrain import FLOOR, add_contours

from dwg2c4d import Config, convert
from dwg2c4d.terrainmesh import EMBED


def sloping_garden(tmp_path, with_pool=False, with_objects=True, **cfg):
    doc, msp = garden_plan(tmp_path, with_pool=with_pool, with_objects=with_objects)
    hatch(msp, rect(1220, -500, 1500, 0), GREEN)  # 20 cm of nothing between this and the paving: soil
    doc.layers.add("CURVE-LIV")
    add_contours(msp)
    msp.add_text("+0.00 (100.50)", height=20, dxfattribs={"insert": (1200, 300), "layer": "0"})
    path = tmp_path / "g.dxf"
    doc.saveas(path)
    cfg = {"garden": True, "terrain_auto": True, "floors_to_outer_face": False, **cfg}
    report = convert(path, tmp_path / "g.obj", Config(**cfg))
    return report, Obj(tmp_path / "g.obj")


def world(obj, ids):
    """The model points (x, y of the plan, z up) of the vertices ``ids`` of an OBJ (whose axes are x, z up as y, -y as z)."""
    p = obj.v[list(ids)]
    return p[:, 0], -p[:, 2], p[:, 1]


def offsets_above_ground(report, obj, group):
    """For each vertex of a group: how much higher it is than the ground of the terrain under it."""
    ids = sorted({i for f, _ in obj.faces(group) for i in f})
    x, y, z = world(obj, ids)
    return z - report.plan.terrain.height(x, y)


def test_the_garden_surfaces_follow_the_ground_and_stay_a_fixed_distance_from_it(tmp_path):
    report, obj = sloping_garden(tmp_path)
    t = report.plan.terrain
    assert t is not None and "Prato" in obj.groups and "Pavimentazione" in obj.groups
    for group, top in (("Prato", 0.15), ("Pavimentazione", 0.20)):  # the lawn 5 cm below the paving, the paving at the floor
        off = offsets_above_ground(report, obj, group)
        assert set(np.round(off, 4)) == {round(top, 4), round(-EMBED, 4)}, group  # the top, and the underside in the ground
    x, y, z = world(obj, sorted({i for f, _ in obj.faces("Prato") for i in f}))
    assert z.max() - z.min() > 0.5  # it is not flat: the ground rises under it


def test_on_the_platform_the_garden_is_flat_at_the_height_the_flat_model_gives_it(tmp_path):
    report, obj = sloping_garden(tmp_path)
    ids = sorted({i for f, _ in obj.faces("Pavimentazione") for i in f})
    x, y, z = world(obj, ids)
    near = (x > 6.0) & (x < 9.0) & (y > -1.5)  # inside the 2 m strip of the platform
    assert near.any() and z[near].max() == pytest.approx(0.0, abs=1e-6)


def test_the_soil_of_the_garden_is_the_terrain_itself(tmp_path):
    (tmp_path / "with").mkdir()
    (tmp_path / "bare").mkdir()
    report, obj = sloping_garden(tmp_path / "with")
    assert report.garden.area("soil") > 0.5  # the gap between the pieces is soil in the garden...
    bare = sloping_garden(tmp_path / "bare", garden=False)[1]  # ... but with a terrain nothing of it is made: it is the terrain
    assert len(obj.faces("Terreno")) == len(bare.faces("Terreno")) and obj.open_edges("Terreno") == 0


def test_plants_and_furniture_stand_on_the_ground_they_are_in(tmp_path):
    report, obj = sloping_garden(tmp_path)
    t = report.plan.terrain
    tree = next(o for o in report.garden.objects if o.kind == "tree")
    lawn_top = t.height(tree.cx, tree.cy) + 0.15
    assert tree.z0 == pytest.approx(lawn_top)
    assert min(obj.v[i][1] for f, _ in obj.faces("Tronco_" + tree.id) for i in f) == pytest.approx(lawn_top)


def test_the_garden_without_a_terrain_is_what_it_was(tmp_path):
    report, obj = sloping_garden(tmp_path, terrain_auto=False)
    assert report.plan.terrain is None
    lo, hi = obj.bbox("Prato")
    assert (lo[1], hi[1]) == pytest.approx((-0.25, -0.05))  # flat: the lawn 5 cm under the floor, 20 cm thick
    assert "Terreno" in obj.groups and obj.bbox("Terreno")[1][1] == pytest.approx(-0.05)  # the soil between the pieces
    assert obj.bbox("Muri")[0][1] == pytest.approx(0.0)


def in_top(obj, group, x, y):
    """Is a face of ``group`` that looks up over the point (x, y) of the plan?"""
    from shapely.geometry import Point, Polygon

    for ids, n in obj.faces(group):
        if obj.n[n][1] > 0.5:
            px, py, _ = world(obj, ids)
            if Polygon(list(zip(px, py))).buffer(1e-9).contains(Point(x, y)):
                return True
    return False


def test_a_pool_is_dug_flat_in_the_ground_and_the_terrain_has_a_hole_for_it(tmp_path):
    report, obj = sloping_garden(tmp_path, with_pool=True)
    assert report.garden.pools and "Vasca" in obj.groups and "Acqua" in obj.groups
    x, y = report.garden.pools[0].centroid.x, report.garden.pools[0].centroid.y
    assert not in_top(obj, "Terreno", x, y)  # but over the pool the top of the terrain is left out
    assert in_top(obj, "Terreno", 0.0, 8.0)  # and elsewhere it is there
    lo, hi = obj.bbox("Vasca")
    assert hi[1] == pytest.approx(0.0)  # the pool keeps the height of the house: the ground round it is flat


def test_a_curb_follows_the_ground_and_a_low_wall_too(tmp_path):
    doc, msp = building()
    for e in list(msp.query("LWPOLYLINE")):
        msp.delete_entity(e)
    h = msp.add_hatch(dxfattribs={"layer": "MURI"})  # the walls marked with a hatch
    h.paths.add_polyline_path([(0, 0), (1000, 0), (1000, 600), (0, 600)], is_closed=True)
    h.paths.add_polyline_path([(30, 30), (970, 30), (970, 570), (30, 570)], is_closed=True)
    msp.add_lwpolyline([(-200, -800), (1200, -800), (1200, -790), (-200, -790)], close=True, dxfattribs={"layer": "MURI"})  # a curb
    msp.add_lwpolyline([(-200, 1500), (1200, 1500), (1200, 1530), (-200, 1530)], close=True, dxfattribs={"layer": "MURI"})  # a low wall
    doc.layers.add("CURVE-LIV")
    add_contours(msp)
    msp.add_text("+0.00 (100.50)", height=20, dxfattribs={"insert": (1200, 300), "layer": "0"})
    doc.saveas(tmp_path / "k.dxf")
    report = convert(tmp_path / "k.dxf", tmp_path / "k.obj", Config(area=(-400, -1000, 1400, 2200), walls_guessed=True, terrain_auto=True,
                                                                    low_wall_height=0.6, curb_height=0.2))
    obj = Obj(tmp_path / "k.obj")
    assert report.plan.terrain is not None and {"Cordoli", "Muretti"} <= set(obj.groups)
    for group, height in (("Cordoli", 0.2), ("Muretti", 0.6)):
        off = offsets_above_ground(report, obj, group)
        assert set(np.round(off, 4)) == {round(height + 0.2, 4), round(-EMBED, 4)}, group  # the flat model's zero is 20 cm over the platform
        x, y, z = world(obj, sorted({i for f, _ in obj.faces(group) for i in f}))
        assert z.max() - z.min() > 1.0  # it climbs the slope
    assert obj.bbox("Muri")[1][1] == pytest.approx(2.7)  # the walls of the house keep theirs


def test_the_json_for_cinema_4d_puts_the_terrain_in_its_own_group_with_its_own_material(tmp_path):
    report = sloping_garden(tmp_path, c4d_json=True)[0]
    model = json.loads((tmp_path / "g_model.json").read_text())
    ground = next(o for o in model["objects"] if o["name"] == "Terreno")
    assert ground["group"] == "Terreno" and ground["material"] == "terrain"
    assert "terrain" in model["materials"] and "soil" not in model["materials"]
    assert any(g["name"] == "Terreno" for g in model["groups"])
    flat = sloping_garden(tmp_path, c4d_json=True, terrain_auto=False)[0]
    model = json.loads((tmp_path / "g_model.json").read_text())
    soil = next(o for o in model["objects"] if o["name"] == "Terreno")
    assert soil["group"] == "Giardino" and soil["material"] == "soil"
