import math

import ezdxf
import pytest
from builders import D, DOOR, PLAN_AREA, T, W, WIN_N, WIN_S, building
from helpers import Obj

from dwg2c4d import Config, convert
from dwg2c4d.fixtures import CASING_W, LEAF_T

# The south wall of tests/builders.building() is y in [0, 30]; its mid-plane is y = 15 (cm).
MID = T / 2


def door_plan(tmp_path, hinge="left", double=False, leaf_arc=True):
    """A building whose south wall has a door drawn as swing arcs (like a real plan symbol)."""
    doc = ezdxf.new("R2018", setup=True)
    doc.units = 5
    for name in ("MURI", "PORTE", "FINESTRE"):
        doc.layers.add(name)
    msp = doc.modelspace()
    for pts in ([(0, 0), (W, 0), (W, D), (0, D)], [(T, T), (W - T, T), (W - T, D - T), (T, D - T)]):
        msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": "MURI"})
    x0, x1 = DOOR  # 200..290
    if leaf_arc and not double:
        if hinge == "left":
            msp.add_arc((x0, MID), x1 - x0, 0, 90, dxfattribs={"layer": "PORTE"})
            msp.add_line((x0, MID), (x0, MID + (x1 - x0)), dxfattribs={"layer": "PORTE"})
        else:
            msp.add_arc((x1, MID), x1 - x0, 90, 180, dxfattribs={"layer": "PORTE"})
            msp.add_line((x1, MID), (x1, MID + (x1 - x0)), dxfattribs={"layer": "PORTE"})
        msp.add_line((x0, 0), (x0, T), dxfattribs={"layer": "PORTE"})   # jambs: make it one symbol
        msp.add_line((x1, 0), (x1, T), dxfattribs={"layer": "PORTE"})
    elif double:
        x0, x1 = 200, 380  # 1.8 m double door
        half = (x1 - x0) / 2
        msp.add_arc((x0, MID), half, 0, 90, dxfattribs={"layer": "PORTE"})
        msp.add_arc((x1, MID), half, 90, 180, dxfattribs={"layer": "PORTE"})
        msp.add_line((x0, 0), (x0, T), dxfattribs={"layer": "PORTE"})
        msp.add_line((x1, 0), (x1, T), dxfattribs={"layer": "PORTE"})
        msp.add_line((x0, MID), (x1, MID), dxfattribs={"layer": "PORTE"})
    else:  # no arc at all
        msp.add_lwpolyline([(x0, 0), (x1, 0), (x1, T), (x0, T)], close=True, dxfattribs={"layer": "PORTE"})
    path = tmp_path / "d.dxf"
    doc.saveas(path)
    return path


def run(path, tmp_path, **kw):
    cfg = Config(area=PLAN_AREA, fixtures="detailed", **kw)
    return convert(path, tmp_path / "o.obj", cfg)


def group_x_range(obj, group, xlo=1.5, xhi=4.2):
    xs = [obj.v[i][0] for ids, _ in obj.groups[group] for i in ids if xlo < obj.v[i][0] < xhi]
    return min(xs), max(xs)


# --- doors -----------------------------------------------------------------------------------

def test_hinge_on_the_left_puts_the_handle_on_the_right(tmp_path):
    rep = run(door_plan(tmp_path, "left"), tmp_path)
    door = rep.openings[0]
    assert door.kind == "door" and door.src["leaves"] == "arco"
    assert door.leaves == [{"hinge": -1, "width": pytest.approx(0.9, abs=0.02), "swing": 1}]
    obj = Obj(rep.output)
    lo, hi = group_x_range(obj, "Maniglie")
    assert lo > 2.5 and hi < 2.91  # near the free (right) edge of a door that spans x 2.00..2.90


def test_hinge_on_the_right_puts_the_handle_on_the_left(tmp_path):
    rep = run(door_plan(tmp_path, "right"), tmp_path)
    assert rep.openings[0].leaves[0]["hinge"] == 1
    lo, hi = group_x_range(Obj(rep.output), "Maniglie")
    assert lo > 1.99 and hi < 2.4  # near the left edge


def test_the_lever_points_to_the_hinge(tmp_path):
    left = Obj(run(door_plan(tmp_path, "left"), tmp_path).output)
    lo, hi = group_x_range(left, "Maniglie")
    # plate centre is ~5 cm from the free edge (x = 2.82); the lever extends towards smaller x (the hinge)
    assert lo < 2.82 - 0.10 and hi < 2.85


def test_handles_and_keyhole_are_on_both_faces_of_the_leaf(tmp_path):
    obj = Obj(run(door_plan(tmp_path, "left"), tmp_path).output)
    zs = {round(float(-obj.v[i][2]), 3) for ids, _ in obj.groups["Maniglie"] for i in ids}  # CAD y
    mid_y = MID / 100
    assert any(z > mid_y + LEAF_T / 2 for z in zs) and any(z < mid_y - LEAF_T / 2 for z in zs)
    # keyhole plate below the handle: Maniglie spans from about 0.90 to 1.08 m
    hs = [obj.v[i][1] for ids, _ in obj.groups["Maniglie"] for i in ids]
    assert min(hs) == pytest.approx(0.90, abs=0.015) and max(hs) == pytest.approx(1.08, abs=0.015)


def test_door_without_an_arc_defaults_to_left_hinge_and_says_so(tmp_path):
    rep = run(door_plan(tmp_path, leaf_arc=False), tmp_path)
    door = rep.openings[0]
    assert door.src["leaves"] == "default" and door.leaves[0]["hinge"] == -1
    assert any("cerniera non deducibile" in n for n in door.notes)


def test_double_door_has_two_leaves_and_two_handles_near_the_meeting_stile(tmp_path):
    rep = run(door_plan(tmp_path, double=True), tmp_path)
    door = rep.openings[0]
    assert sorted(l["hinge"] for l in door.leaves) == [-1, 1]
    obj = Obj(rep.output)
    leaf_x = sorted({round(float(obj.v[i][0]), 2) for ids, _ in obj.groups["Ante"] for i in ids})
    assert leaf_x[0] < 2.1 and leaf_x[-1] > 3.7 and any(abs(x - 2.9) < 0.02 for x in leaf_x)  # gap in the middle
    handle_x = [obj.v[i][0] for ids, _ in obj.groups["Maniglie"] for i in ids]
    centre = 2.9
    assert any(abs(x - centre) < 0.12 for x in handle_x)
    assert min(handle_x) > 2.0 and max(handle_x) < 3.8


def test_door_has_lining_and_casing_on_both_faces(tmp_path):
    obj = Obj(run(door_plan(tmp_path, "left"), tmp_path).output)
    zs = [-obj.v[i][2] for ids, _ in obj.groups["Telai"] for i in ids]
    assert min(zs) < -0.0 + 1e-6 and min(zs) == pytest.approx(-0.012, abs=1e-3)  # casing proud of the south face
    assert max(zs) == pytest.approx(T / 100 + 0.012, abs=1e-3)  # and of the north face
    xs = [obj.v[i][0] for ids, _ in obj.groups["Telai"] for i in ids]
    assert min(xs) == pytest.approx(2.0 - CASING_W, abs=1e-3)


# --- windows ---------------------------------------------------------------------------------

def window_plan(tmp_path, dividers=(), width=120, with_hinge_arc=False):
    doc, msp = building()
    # replace the symbols of builders.building(): keep walls only
    for e in list(msp.query("LWPOLYLINE[layer=='FINESTRE']")) + list(msp.query("LWPOLYLINE[layer=='PORTE']")):
        msp.delete_entity(e)
    x0 = 400
    x1 = x0 + width
    for off in (0, MID, T):
        msp.add_line((x0, off), (x1, off), dxfattribs={"layer": "FINESTRE"})
    for x in (x0, x0 + 5, x1 - 5, x1):  # jambs: double lines at both ends
        msp.add_line((x, 0), (x, T), dxfattribs={"layer": "FINESTRE"})
    for d in dividers:  # cut marks
        msp.add_line((x0 + d, 0), (x0 + d, T), dxfattribs={"layer": "FINESTRE"})
    path = tmp_path / "w.dxf"
    doc.saveas(path)
    return path


@pytest.mark.parametrize("dividers,sashes", [((), 1), ((60,), 2), ((40, 80), 3)])
def test_number_of_sashes_comes_from_the_cut_lines(tmp_path, dividers, sashes):
    rep = run(window_plan(tmp_path, dividers), tmp_path)
    win = rep.openings[0]
    assert win.kind == "window" and win.sashes == sashes
    obj = Obj(rep.output)
    assert len(obj.groups["Vetri"]) == 6 * sashes  # one glass box per sash (6 faces each)


def test_double_line_cut_marks_count_once_and_jambs_never(tmp_path):
    rep = run(window_plan(tmp_path, dividers=(58, 62)), tmp_path)  # a double line 4 cm apart in the middle
    assert rep.openings[0].sashes == 2
    assert [round(d, 2) for d in rep.openings[0].dividers] == [0.0]


def test_a_short_connector_between_sliding_panels_is_a_cut_mark(tmp_path):
    doc, msp = building()
    for e in list(msp.query("LWPOLYLINE[layer=='FINESTRE']")) + list(msp.query("LWPOLYLINE[layer=='PORTE']")):
        msp.delete_entity(e)
    # two overlapping glass lines at different depths, joined by 3 cm marks (as in the real plan)
    msp.add_line((400, 0), (520, 0), dxfattribs={"layer": "FINESTRE"})
    msp.add_line((400, 30), (520, 30), dxfattribs={"layer": "FINESTRE"})
    msp.add_line((405, 18), (462, 18), dxfattribs={"layer": "FINESTRE"})
    msp.add_line((458, 12), (515, 12), dxfattribs={"layer": "FINESTRE"})
    msp.add_line((462, 18), (462, 21), dxfattribs={"layer": "FINESTRE"})
    msp.add_line((458, 12), (458, 15), dxfattribs={"layer": "FINESTRE"})
    for x in (400, 405, 515, 520):
        msp.add_line((x, 0), (x, 30), dxfattribs={"layer": "FINESTRE"})
    msp.add_line((400, 0), (400, 30), dxfattribs={"layer": "FINESTRE"})
    doc.saveas(tmp_path / "s.dxf")
    rep = run(tmp_path / "s.dxf", tmp_path)
    assert rep.openings[0].sashes == 2


def test_wide_single_sash_is_flagged(tmp_path):
    rep = run(window_plan(tmp_path, width=220), tmp_path)
    assert rep.openings[0].sashes == 1
    assert any("anta unica larga" in n for n in rep.openings[0].notes)


def test_window_parts_and_sill(tmp_path):
    rep = run(window_plan(tmp_path, (60,)), tmp_path)
    obj = Obj(rep.output)
    assert {"Telai", "Vetri", "Maniglie"} <= set(obj.groups)
    # frame spans the opening (4.00..5.20) and the sill ledge sticks out 3 cm each side
    lo, hi = group_x_range(obj, "Telai", 3.5, 5.6)
    assert lo == pytest.approx(4.0 - CASING_W, abs=1e-3) and hi == pytest.approx(5.2 + CASING_W, abs=1e-3)
    sill_top = min(obj.v[i][1] for ids, _ in obj.groups["Telai"] for i in ids if 3.9 < obj.v[i][0] < 5.3)
    assert sill_top == pytest.approx(0.90 - 0.03, abs=1e-3)  # the ledge's underside, 3 cm under the sill


def test_window_glass_is_inside_the_opening_and_thin(tmp_path):
    obj = Obj(run(window_plan(tmp_path, (60,)), tmp_path).output)
    lo, hi = obj.bbox("Vetri")
    assert 4.0 < lo[0] and hi[0] < 5.2 and 0.9 < lo[1] and hi[1] < 2.2
    assert hi[2] - lo[2] == pytest.approx(0.006, abs=1e-4)


# --- general -----------------------------------------------------------------------------------

def test_simple_mode_is_unchanged_and_has_no_fixture_groups(tmp_path):
    cfg = Config(area=PLAN_AREA)  # fixtures="simple"
    rep = convert(window_plan(tmp_path, (60,)), tmp_path / "s.obj", cfg)
    assert not ({"Telai", "Ante", "Maniglie"} & set(rep.groups))
    assert Obj(rep.output).volume("Vetri") == pytest.approx(1.2 * 0.02 * 1.3, rel=0.01)


def test_fixture_meshes_are_valid_and_json_maps_them_to_materials(tmp_path):
    path = door_plan(tmp_path, "left")
    cfg = Config(area=PLAN_AREA, fixtures="detailed", c4d_json=True)
    rep = convert(path, tmp_path / "o.obj", cfg)
    obj = Obj(rep.output)
    for g in ("Telai", "Ante", "Maniglie"):
        assert obj.winding_matches_normals(g) and obj.volume(g) > 0
    import json
    model = json.loads(rep.json_path.read_text())
    by_name = {o["name"]: o for o in model["objects"]}
    assert by_name["Telai"]["group"] == "Infissi" and by_name["Telai"]["material"] == "frame"
    assert by_name["Ante"]["material"] == "door_leaf" and by_name["Maniglie"]["material"] == "metal"
    assert {"frame", "door_leaf", "metal"} <= set(model["materials"])


def test_rotated_plan_keeps_handles_on_the_same_side(tmp_path):
    from ezdxf.math import Matrix44

    path = door_plan(tmp_path, "left")
    doc = ezdxf.readfile(path)
    for e in doc.modelspace():
        e.transform(Matrix44.z_rotate(math.radians(35)))
    doc.saveas(tmp_path / "rot.dxf")
    a = run(path, tmp_path)
    b = convert(tmp_path / "rot.dxf", tmp_path / "r.obj", Config(area=(-2000, -2000, 2000, 2000), fixtures="detailed"))
    assert b.openings[0].leaves[0]["hinge"] == a.openings[0].leaves[0]["hinge"] == -1
    assert Obj(b.output).volume("Maniglie") == pytest.approx(Obj(a.output).volume("Maniglie"), rel=1e-3)
