"""Walls with an outside and an inside material, and a closed underside."""

import numpy as np
import pytest
from builders import D, PLAN_AREA, T, W, WIN_S, building
from helpers import Obj
from test_conversion import build_sample

from dwg2c4d import Config, cli, convert


def run(tmp_path, doc=None, name="o", **kw):
    if doc is None:
        doc, _ = building()
    path = tmp_path / f"{name}.dxf"
    doc.saveas(path)
    return convert(path, tmp_path / f"{name}.obj", Config(area=PLAN_AREA, floors_by_room=False, **kw))


def face_vertices(obj, group):
    return [[obj.v[i] for i in ids] for ids, _ in obj.groups[group]]


def on_outer_boundary(p):
    """OBJ point (x, up, -y): on the outer face of the 10 x 6 m building?"""
    x, y = p[0], -p[2]
    return abs(x) < 1e-4 or abs(x - W / 100) < 1e-4 or abs(y) < 1e-4 or abs(y - D / 100) < 1e-4


def test_three_wall_objects_replace_the_single_one(tmp_path):
    rep = run(tmp_path, wall_finishes=True)
    text = rep.output.read_text()
    assert all(f"\no {g}\n" in text for g in ("Muri_esterno", "Muri_interno", "Muri_spessori"))
    assert "\no Muri\n" not in text  # (the test helper adds a "Muri" alias to what it reads; the file has none)


def test_exterior_material_covers_only_the_outer_faces(tmp_path):
    obj = Obj(run(tmp_path, wall_finishes=True).output)
    for face in face_vertices(obj, "Muri_esterno"):
        assert all(on_outer_boundary(p) for p in face)
        ys = {round(float(p[1]), 4) for p in face}
        assert len(face) >= 3 and ys != set()  # vertical faces: real quads or triangles
    # and the faces are walls seen from outside: their normal points away from the building centre
    centre = np.array([W / 200, 0.0, -D / 200])
    for ids, nid in obj.groups["Muri_esterno"]:
        mid = obj.v[list(ids)].mean(axis=0)
        assert np.dot(obj.n[nid], mid - centre) > 0


def test_interior_material_covers_the_room_side_faces(tmp_path):
    obj = Obj(run(tmp_path, wall_finishes=True).output)
    inner = lambda p: (abs(p[0] - T / 100) < 1e-4 or abs(p[0] - (W - T) / 100) < 1e-4
                       or abs(-p[2] - T / 100) < 1e-4 or abs(-p[2] - (D - T) / 100) < 1e-4)
    faces = face_vertices(obj, "Muri_interno")
    assert faces and all(all(inner(p) for p in f) for f in faces)
    centre = np.array([W / 200, 0.0, -D / 200])
    for ids, nid in obj.groups["Muri_interno"]:
        assert np.dot(obj.n[nid], obj.v[list(ids)].mean(axis=0) - centre) < 0  # looking into the room


def test_tops_jambs_and_underside_are_the_third_object(tmp_path):
    obj = Obj(run(tmp_path, wall_finishes=True).output)
    normals = [obj.n[nid] for _, nid in obj.groups["Muri_spessori"]]
    assert any(n[1] > 0.99 for n in normals) and any(n[1] < -0.99 for n in normals)  # top and bottom
    # the jambs of the south window (x = 5.00 and 6.20) look along the wall: they are here, not outside/inside
    jamb_x = {round(float(obj.v[i][0]), 3) for ids, nid in obj.groups["Muri_spessori"]
              if abs(obj.n[nid][0]) > 0.99 for i in ids}
    assert WIN_S[0] / 100 in jamb_x and WIN_S[1] / 100 in jamb_x
    for group in ("Muri_esterno", "Muri_interno"):
        assert all(abs(obj.n[nid][1]) < 1e-6 for _, nid in obj.groups[group])  # nothing horizontal there


def test_splitting_the_walls_loses_and_adds_no_surface(tmp_path):
    def area(obj, groups):
        total = 0.0
        for g in groups:
            for ids, _ in obj.groups[g]:
                p = obj.v[list(ids)]
                total += 0.5 * np.linalg.norm(sum(np.cross(p[k] - p[0], p[k + 1] - p[0]) for k in range(1, len(p) - 1)))
        return total

    merged = Obj(run(tmp_path, name="a").output)
    split = Obj(run(tmp_path, name="b", wall_finishes=True).output)
    parts = ["Muri_esterno", "Muri_interno", "Muri_spessori"]
    assert area(split, parts) == pytest.approx(area(merged, ["Muri"]), rel=1e-9)


def test_the_underside_of_the_walls_is_closed(tmp_path):
    """No openings: the walls are a closed solid, bottom included (before: open underneath)."""
    dxf = build_sample(tmp_path / "a.dxf", "polylines", with_openings=False)
    for finishes in (False, True):
        obj = Obj(convert(dxf, tmp_path / "o.obj", Config(wall_finishes=finishes)).output)
        assert obj.open_edges("Muri") == 0
        bottoms = [n for _, nid in obj.groups["Muri"] if (n := obj.n[nid])[1] < -0.99]
        assert bottoms


def test_the_command_line_splits_by_default_and_can_merge(tmp_path):
    doc, _ = building()
    doc.saveas(tmp_path / "c.dxf")
    area = "--area=" + ",".join(map(str, PLAN_AREA))
    assert cli.main([str(tmp_path / "c.dxf"), "-o", str(tmp_path / "a.obj"), area, "--no-immagini"]) == 0
    assert "Muri_esterno" in Obj(tmp_path / "a.obj").groups
    assert cli.main([str(tmp_path / "c.dxf"), "-o", str(tmp_path / "b.obj"), area, "--no-immagini",
                     "--muri-uniti"]) == 0
    b = Obj(tmp_path / "b.obj")
    assert "Muri" in b.groups and "Muri_esterno" not in b.groups


def test_json_and_mtl_know_the_three_materials(tmp_path):
    import json

    rep = run(tmp_path, wall_finishes=True, c4d_json=True)
    model = json.loads(rep.json_path.read_text())
    mats = {o["name"]: o["material"] for o in model["objects"]}
    assert (mats["Muri_esterno"], mats["Muri_interno"], mats["Muri_spessori"]) == ("wall_outer", "wall_inner", "wall_edge")
    assert {"wall_outer", "wall_inner", "wall_edge"} <= set(model["materials"])
    assert len({g["name"] for g in model["groups"] if g["name"] == "Murature"}) == 1  # one null for the three
    mtl = rep.output.with_suffix(".mtl").read_text()
    assert all(f"newmtl {m}" in mtl for m in ("Muri_esterno", "Muri_interno", "Muri_spessori"))
