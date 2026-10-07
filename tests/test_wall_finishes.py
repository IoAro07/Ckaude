"""Perimeter walls cut in two halves (outside / inside) and a closed underside."""

import ezdxf
import numpy as np
import pytest
from builders import D, DOOR, PLAN_AREA, T, W, building
from helpers import Obj
from test_conversion import build_sample

from dwg2c4d import Config, cli, convert

H = 2.70


def ring(tmp_path, t=30, partition=False, name="r"):
    """A 10 x 6 m building with walls of thickness t (cm), no symbols; optionally a 10 cm partition at x = 5 m."""
    doc = ezdxf.new("R2018", setup=True)
    doc.units = 5
    doc.layers.add("MURI")
    msp = doc.modelspace()
    for pts in ([(0, 0), (W, 0), (W, D), (0, D)], [(t, t), (W - t, t), (W - t, D - t), (t, D - t)]):
        msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": "MURI"})
    if partition:
        msp.add_lwpolyline([(500, t - 1), (510, t - 1), (510, D - t + 1), (500, D - t + 1)], close=True,
                           dxfattribs={"layer": "MURI"})
    path = tmp_path / f"{name}.dxf"
    doc.saveas(path)
    return path


def run(path, tmp_path, name="o", **kw):
    return convert(path, tmp_path / f"{name}.obj", Config(area=PLAN_AREA, floors_by_room=False, **kw))


def halves(tmp_path, **kw):
    return Obj(run(ring(tmp_path, **kw.pop("plan", {})), tmp_path, wall_finishes=True, **kw).output)


def test_two_closed_solids_replace_the_single_object(tmp_path):
    rep = run(ring(tmp_path), tmp_path, wall_finishes=True)
    text = rep.output.read_text()
    assert "\no Muri_esterno\n" in text and "\no Muri_interno\n" in text
    assert "\no Muri\n" not in text and "Muri_spessori" not in text
    obj = Obj(rep.output)
    assert obj.open_edges("Muri_esterno") == 0 and obj.open_edges("Muri_interno") == 0  # each is a closed solid


@pytest.mark.parametrize("t", [30, 20])
def test_each_half_is_half_of_the_thickness(tmp_path, t):
    obj = halves(tmp_path, plan={"t": t})
    h = t / 200.0  # half the thickness, metres
    outer_area = W / 100 * D / 100 - (W / 100 - 2 * h) * (D / 100 - 2 * h)  # the band along the outside
    ring_area = W / 100 * D / 100 - (W / 100 - 2 * t / 100) * (D / 100 - 2 * t / 100)
    assert obj.volume("Muri_esterno") == pytest.approx(outer_area * H, rel=1e-6)
    assert obj.volume("Muri_interno") == pytest.approx((ring_area - outer_area) * H, rel=1e-6)


def test_the_outer_half_lies_between_the_outside_and_the_middle_of_the_wall(tmp_path):
    obj = halves(tmp_path)
    (x0, _, z0), (x1, _, z1) = obj.bbox("Muri_esterno")
    assert (x0, x1, z0, z1) == pytest.approx((0.0, W / 100, -D / 100, 0.0), abs=1e-6)  # reaches the outside
    for ids, _ in obj.groups["Muri_esterno"]:
        for i in ids:  # no point of it is deeper than the middle of the wall (15 cm)
            x, y = obj.v[i][0], -obj.v[i][2]
            depth = min(x, W / 100 - x, y, D / 100 - y)
            assert depth <= 0.15 + 1e-6
    # the inner half starts at the middle and reaches the room
    for ids, _ in obj.groups["Muri_interno"]:
        for i in ids:
            x, y = obj.v[i][0], -obj.v[i][2]
            assert min(x, W / 100 - x, y, D / 100 - y) >= 0.15 - 1e-6


def test_the_two_halves_meet_on_the_middle_surface(tmp_path):
    obj = halves(tmp_path)
    for group, sign in (("Muri_esterno", 1), ("Muri_interno", -1)):
        faces = [(ids, nid) for ids, nid in obj.groups[group]
                 if all(abs(obj.v[i][0] - 0.15) < 1e-6 for i in ids) and abs(obj.n[nid][0]) > 0.99]
        assert faces, group  # a face on the plane x = 15 cm, west wall
        assert all(np.sign(obj.n[nid][0]) == sign for _, nid in faces)  # outer half looks inwards, inner half back


def test_walls_between_rooms_belong_to_the_inner_object(tmp_path):
    plain = halves(tmp_path)
    divided = halves(tmp_path, plan={"partition": True})
    assert divided.volume("Muri_esterno") == pytest.approx(plain.volume("Muri_esterno"), rel=1e-6)
    assert divided.volume("Muri_interno") == pytest.approx(plain.volume("Muri_interno") + 0.1 * 5.4 * H, rel=1e-3)


def test_a_door_cuts_both_halves(tmp_path):
    doc, _ = building()
    doc.saveas(tmp_path / "d.dxf")
    obj = Obj(run(tmp_path / "d.dxf", tmp_path, wall_finishes=True).output)
    for group, lo, hi in (("Muri_esterno", 0.0, 0.15), ("Muri_interno", 0.15, 0.30)):
        jambs = {round(float(obj.v[i][0]), 3) for ids, nid in obj.groups[group] if abs(obj.n[nid][0]) > 0.99
                 for i in ids if lo - 1e-6 <= -obj.v[i][2] <= hi + 1e-6 and obj.v[i][1] <= 2.2}
        assert DOOR[0] / 100 in jambs and DOOR[1] / 100 in jambs, group  # the reveal of the door in each half


def test_splitting_loses_no_volume(tmp_path):
    doc, _ = building()
    doc.saveas(tmp_path / "b.dxf")
    one = Obj(run(tmp_path / "b.dxf", tmp_path, "a").output)
    two = Obj(run(tmp_path / "b.dxf", tmp_path, "b", wall_finishes=True).output)
    assert two.volume("Muri_esterno") + two.volume("Muri_interno") == pytest.approx(one.volume("Muri"), rel=1e-6)


def test_walls_that_enclose_nothing_stay_in_one_object(tmp_path):
    doc = ezdxf.new("R2018", setup=True)
    doc.units = 5
    doc.layers.add("MURI")
    msp = doc.modelspace()
    for a, b in (((0, 0), (800, 0)), ((0, 30), (800, 30)), ((0, 0), (0, 30)), ((800, 0), (800, 30))):
        msp.add_line(a, b, dxfattribs={"layer": "MURI"})  # one straight wall: no space to be inside of
    doc.saveas(tmp_path / "w.dxf")
    rep = run(tmp_path / "w.dxf", tmp_path, wall_finishes=True)
    groups = set(Obj(rep.output).groups)
    assert "Muri" in groups and "Muri_esterno" not in groups


def test_the_underside_of_the_walls_is_closed(tmp_path):
    dxf = build_sample(tmp_path / "a.dxf", "polylines", with_openings=False)
    for finishes in (False, True):
        obj = Obj(convert(dxf, tmp_path / "o.obj", Config(wall_finishes=finishes)).output)
        assert obj.open_edges("Muri") == 0
        assert any(obj.n[nid][1] < -0.99 for _, nid in obj.groups["Muri"])


def test_the_command_line_halves_by_default_and_can_merge(tmp_path):
    area = "--area=" + ",".join(map(str, PLAN_AREA))
    path = ring(tmp_path, name="c")
    assert cli.main([str(path), "-o", str(tmp_path / "a.obj"), area, "--no-immagini"]) == 0
    assert "Muri_esterno" in Obj(tmp_path / "a.obj").groups
    assert cli.main([str(path), "-o", str(tmp_path / "b.obj"), area, "--no-immagini", "--muri-uniti"]) == 0
    b = Obj(tmp_path / "b.obj")
    assert "Muri" in b.groups and "Muri_esterno" not in b.groups


def test_json_and_mtl_have_the_two_materials(tmp_path):
    import json

    rep = run(ring(tmp_path), tmp_path, wall_finishes=True, c4d_json=True)
    model = json.loads(rep.json_path.read_text())
    mats = {o["name"]: o["material"] for o in model["objects"]}
    assert (mats["Muri_esterno"], mats["Muri_interno"]) == ("wall_outer", "wall_inner")
    assert {"wall_outer", "wall_inner"} <= set(model["materials"]) and "wall_edge" not in model["materials"]
    mtl = rep.output.with_suffix(".mtl").read_text()
    assert "newmtl Muri_esterno" in mtl and "newmtl Muri_interno" in mtl and "Muri_spessori" not in mtl
