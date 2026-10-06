import json

import pytest
from builders import PLAN_AREA, building, hip_roof, south_elevation
from helpers import Obj

from dwg2c4d import Config, cli, convert
from dwg2c4d.sample import build_sample


def far_building(tmp_path, dx=72000.0, dy=48000.0):
    """The sample plan, but placed ~720 m from the origin like a real survey-based drawing."""
    import ezdxf
    from ezdxf.math import Matrix44

    doc = ezdxf.readfile(build_sample(tmp_path / "s.dxf", "lines"))
    m = Matrix44.translate(dx, dy, 0)
    for e in doc.modelspace():
        e.transform(m)
    doc.saveas(tmp_path / "far.dxf")
    return tmp_path / "far.dxf"


def test_origin_default_in_the_api_is_the_drawing_coordinates(tmp_path):
    rep = convert(far_building(tmp_path), tmp_path / "o.obj")
    lo, hi = Obj(rep.output).bbox("Muri")
    assert lo[0] == pytest.approx(720.0, abs=0.01) and rep.origin_offset == (0.0, 0.0)


def test_origin_center_puts_the_walls_around_zero(tmp_path):
    rep = convert(far_building(tmp_path), tmp_path / "o.obj", Config(origin="center"))
    obj = Obj(rep.output)
    lo, hi = obj.bbox("Muri")
    assert lo[0] == pytest.approx(-4.0, abs=0.01) and hi[0] == pytest.approx(4.0, abs=0.01)
    assert lo[2] == pytest.approx(-3.0, abs=0.01) and hi[2] == pytest.approx(3.0, abs=0.01)
    assert lo[1] == pytest.approx(0.0) and hi[1] == pytest.approx(2.7)  # heights untouched
    assert rep.origin_offset == pytest.approx((-724.0, -483.0), abs=0.01)  # centre of the walls
    assert obj.winding_matches_normals()


def test_origin_min_and_cli_default(tmp_path, capsys):
    far = far_building(tmp_path)
    rep = convert(far, tmp_path / "m.obj", Config(origin="min"))
    lo, _ = Obj(rep.output).bbox("Muri")
    assert (lo[0], lo[2]) == (pytest.approx(0.0, abs=0.01), pytest.approx(-6.0, abs=0.01))
    # the command line recentres by default and says so
    assert cli.main([str(far), "-o", str(tmp_path / "c.obj")]) == 0
    assert "Origine" in capsys.readouterr().out
    lo, hi = Obj(tmp_path / "c.obj").bbox("Muri")
    assert abs(lo[0] + hi[0]) < 0.02


def test_origin_option_validation():
    with pytest.raises(ValueError):
        Config(origin="nowhere").validate()


def test_origin_does_not_move_elevations_or_roof(tmp_path):
    """Everything is computed in drawing coordinates and shifted at the end."""
    doc, msp = building()
    area = south_elevation(msp)
    hip_roof(msp)
    doc.saveas(tmp_path / "p.dxf")
    a = convert(tmp_path / "p.dxf", tmp_path / "a.obj",
                Config(area=PLAN_AREA, elevations=[area], roof=True, roof_pitch=30))
    b = convert(tmp_path / "p.dxf", tmp_path / "b.obj",
                Config(area=PLAN_AREA, elevations=[area], roof=True, roof_pitch=30, origin="center"))
    oa, ob = Obj(a.output), Obj(b.output)
    for g in ("Muri", "Vetri", "Tetto"):
        assert ob.volume(g) == pytest.approx(oa.volume(g), rel=1e-6)
        assert ob.bbox(g)[1][1] == pytest.approx(oa.bbox(g)[1][1])


# --- Cinema 4D model.json ----------------------------------------------------------------

def make_json(tmp_path, **kw):
    doc, msp = building()
    area = south_elevation(msp)
    hip_roof(msp)
    doc.saveas(tmp_path / "p.dxf")
    cfg = Config(area=PLAN_AREA, elevations=[area], roof=True, roof_pitch=30, c4d_json=True, **kw)
    rep = convert(tmp_path / "p.dxf", tmp_path / "casa.obj", cfg)
    return rep, json.loads(rep.json_path.read_text())


def test_json_is_not_written_unless_requested(tmp_path):
    rep = convert(build_sample(tmp_path / "s.dxf", "lines"), tmp_path / "o.obj")
    assert rep.json_path is None and not (tmp_path / "o_model.json").exists()


def test_json_structure_matches_what_the_importer_reads(tmp_path):
    rep, model = make_json(tmp_path)
    assert rep.json_path.name == "casa_model.json"
    assert model["name"] == "casa" and model["units"] == "cm"
    group_names = {g["name"] for g in model["groups"]}
    assert {"Murature", "Infissi", "Tetto", "Pavimenti"} <= group_names
    assert all(g["parent"] == "casa" for g in model["groups"])
    assert len({g["name"] for g in model["groups"]}) == len(model["groups"])
    assert set(model["materials"]) == {"wall", "glass", "floor", "roof"} | (
        {"column"} if any(o["material"] == "column" for o in model["objects"]) else set())
    for mat in model["materials"].values():
        assert set(mat) == {"name", "color", "rough"} and len(mat["color"]) == 3
    for o in model["objects"]:
        assert o["group"] in group_names and o["material"] in model["materials"]
        assert len(o["points"]) % 3 == 0 and len(o["polys"]) % 4 == 0 and o["polys"]
        n = len(o["points"]) // 3
        assert all(0 <= i < n for i in o["polys"])
        assert set(o["polys"]) == set(range(n))  # every point is used, none orphaned


def test_json_is_in_cm_with_cad_axes_and_matches_the_obj(tmp_path):
    rep, model = make_json(tmp_path, origin="center")
    walls = next(o for o in model["objects"] if o["name"] == "Muri")
    xs, ys, zs = walls["points"][0::3], walls["points"][1::3], walls["points"][2::3]
    assert (min(xs), max(xs)) == (pytest.approx(-500.0, abs=0.1), pytest.approx(500.0, abs=0.1))
    assert (min(ys), max(ys)) == (pytest.approx(-300.0, abs=0.1), pytest.approx(300.0, abs=0.1))
    assert (min(zs), max(zs)) == (pytest.approx(0.0), pytest.approx(270.0))  # z is up, in cm
    roof = next(o for o in model["objects"] if o["name"] == "Tetto")
    assert max(roof["points"][2::3]) > 270.0
    assert model["origin_offset_m"] == pytest.approx(list(rep.origin_offset), abs=1e-3)


def test_json_triangles_repeat_the_last_vertex_and_quads_are_unique(tmp_path):
    _, model = make_json(tmp_path)
    tri = quad = 0
    for o in model["objects"]:
        for k in range(0, len(o["polys"]), 4):
            a, b, c, d = o["polys"][k:k + 4]
            if c == d:
                tri += 1
                assert len({a, b, c}) == 3
            else:
                quad += 1
                assert len({a, b, c, d}) == 4
    assert tri > 0 and quad > 0


def test_json_face_count_equals_the_mesh(tmp_path):
    rep, model = make_json(tmp_path)
    assert sum(len(o["polys"]) // 4 for o in model["objects"]) == rep.faces


def test_cli_writes_the_json(tmp_path, capsys):
    path = build_sample(tmp_path / "s.dxf", "lines")
    assert cli.main([str(path), "-o", str(tmp_path / "x.obj"), "--json-c4d"]) == 0
    assert (tmp_path / "x_model.json").exists()
    assert "x_model.json" in capsys.readouterr().out
