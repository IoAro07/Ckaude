import math

import ezdxf
import pytest

from builders import (D, DOOR, PLAN_AREA, W, WIN_N, WIN_S, building, gable_roof, hip_roof, l_roof,
                      north_elevation, south_elevation)
from helpers import Obj

from dwg2c4d import Config, cli, convert

H = 2.70


def run(tmp_path, doc, cfg=None, name="o"):
    path = tmp_path / f"{name}.dxf"
    doc.saveas(path)
    cfg = cfg or Config()
    if cfg.area is None:
        cfg.area = PLAN_AREA
    return convert(path, tmp_path / f"{name}.obj", cfg)


def glass_heights(obj, x_lo, x_hi):
    ys = [obj.v[i][1] for ids, _ in obj.groups["Vetri"] for i in ids if x_lo < obj.v[i][0] < x_hi]
    return min(ys), max(ys)


# --- door / window heights from an elevation ---------------------------------------------

def test_default_heights_without_elevation(tmp_path):
    doc, _ = building()
    obj = Obj(run(tmp_path, doc).output)
    assert glass_heights(obj, 4.9, 6.3) == (pytest.approx(0.9), pytest.approx(2.2))


def test_south_elevation_sets_window_and_door_heights(tmp_path):
    doc, msp = building()
    area = south_elevation(msp, ground_y=-2000, door_h=200, sill=100, win_h=140)
    rep = run(tmp_path, doc, Config(elevations=[area]))
    assert rep.elevations == [{"side": "south", "matched": 2, "total": 2, "zero_source": "porta"}]
    obj = Obj(rep.output)
    # south window: sill 1.00, head 2.40 (defaults would be 0.90 / 2.20)
    assert glass_heights(obj, 4.9, 6.3) == (pytest.approx(1.0), pytest.approx(2.4))
    # north window has no elevation: default
    assert glass_heights(obj, 2.9, 4.3) == (pytest.approx(0.9), pytest.approx(2.2))
    # entrance door: 2.00 m instead of 2.10 -> the lintel soffit is at 2.0
    soffit_y = {round(float(obj.v[i][1]), 3)
                for ids, _ in obj.groups["Muri"]
                if all(abs(obj.v[i][1] - obj.v[ids[0]][1]) < 1e-6 for i in ids)
                and min(obj.v[i][0] for i in ids) == pytest.approx(DOOR[0] / 100, abs=0.02)
                and max(obj.v[i][0] for i in ids) == pytest.approx(DOOR[1] / 100, abs=0.02)
                for i in ids}
    assert 2.0 in soffit_y and 2.1 not in soffit_y


def test_north_elevation_with_explicit_floor_level(tmp_path):
    doc, msp = building()
    area = north_elevation(msp, floor_y=2500, sill=110, win_h=130)  # 5th value = floor Y
    rep = run(tmp_path, doc, Config(elevations=[area]))
    assert rep.elevations[0]["side"] == "north"
    assert rep.elevations[0]["zero_source"] == "indicata"
    assert rep.elevations[0]["matched"] == 1
    obj = Obj(rep.output)
    assert glass_heights(obj, 2.9, 4.3) == (pytest.approx(1.1), pytest.approx(2.4))


def test_two_elevations_together(tmp_path):
    doc, msp = building()
    cfg = Config(elevations=[south_elevation(msp), north_elevation(msp)])
    rep = run(tmp_path, doc, cfg)
    assert [e["side"] for e in rep.elevations] == ["south", "north"]
    assert sum(e["matched"] for e in rep.elevations) == 3


def test_elevation_without_door_needs_the_floor_level(tmp_path):
    doc, msp = building()
    area = north_elevation(msp)[:4]  # no 5th value and no door in it
    rep = run(tmp_path, doc, Config(elevations=[area]))
    assert rep.elevations == []
    assert any("quota" in w and "pavimento" in w for w in rep.warnings)


def test_elevation_overlapping_the_plan_is_rejected(tmp_path):
    doc, msp = building()
    rep = run(tmp_path, doc, Config(elevations=[(0, 0, W, D, 0)]))
    assert rep.elevations == []
    assert any("si sovrappone alla pianta" in w for w in rep.warnings)


def test_elevation_symbols_that_match_nothing_are_reported(tmp_path):
    doc, msp = building()
    _rect = lambda *a: msp.add_lwpolyline([(a[0], a[1]), (a[2], a[1]), (a[2], a[3]), (a[0], a[3])],  # noqa: E731
                                          close=True, dxfattribs={"layer": "FINESTRE"})
    msp.add_lwpolyline([(200, -2000), (290, -2000), (290, -1800), (200, -1800)], close=True,
                       dxfattribs={"layer": "PORTE"})
    _rect(8000, -1900, 8100, -1760)  # a window far from anything in the plan
    rep = run(tmp_path, doc, Config(elevations=[(-100, -2100, 8200, -1100)]))
    assert rep.elevations[0]["matched"] == 1 and rep.elevations[0]["total"] == 2
    assert any("non corrispondono a nessuna apertura" in w for w in rep.warnings)


# --- roof ----------------------------------------------------------------------------------

def test_hip_roof_with_explicit_pitch(tmp_path):
    doc, msp = building()
    hip_roof(msp)
    rep = run(tmp_path, doc, Config(roof=True, roof_pitch=30))
    assert rep.roof["faces"] == 4
    assert rep.roof["pitch_source"] == "indicata"
    obj = Obj(rep.output)
    half = (D + 100) / 200  # half span incl. 50 cm overhang each side, metres
    lo, hi = obj.bbox("Tetto")
    assert hi[1] == pytest.approx(H + math.tan(math.radians(30)) * half, abs=1e-3)
    assert lo[1] == pytest.approx(H - 0.15, abs=1e-3)  # eaves level minus the thickness
    # covers the walls plus the overhang
    assert (lo[0], hi[0]) == (pytest.approx(-0.5, abs=1e-3), pytest.approx(10.5, abs=1e-3))
    assert (lo[2], hi[2]) == (pytest.approx(-6.5, abs=1e-3), pytest.approx(0.5, abs=1e-3))


@pytest.mark.parametrize("roof_fn", [hip_roof, gable_roof])
def test_roof_solid_is_closed_with_outward_normals(tmp_path, roof_fn):
    doc, msp = building()
    roof_fn(msp)
    obj = Obj(run(tmp_path, doc, Config(roof=True, roof_pitch=28)).output)
    assert obj.open_edges("Tetto") == 0
    assert obj.non_manifold_edges("Tetto") == 0
    assert obj.volume("Tetto") > 0
    assert obj.winding_matches_normals("Tetto")


def test_gable_roof_ridge_ends_sit_at_ridge_height(tmp_path):
    doc, msp = building()
    gable_roof(msp)
    rep = run(tmp_path, doc, Config(roof=True, roof_pitch=25))
    assert rep.roof["faces"] == 2
    obj = Obj(rep.output)
    half = (D + 100) / 200
    ridge = H + math.tan(math.radians(25)) * half
    lo, hi = obj.bbox("Tetto")
    assert hi[1] == pytest.approx(ridge, abs=1e-3)
    # the gable end edges: a vertex at the ridge height exists on the x=-0.5 end
    ends = [v for v in obj.v if abs(v[0] + 0.5) < 1e-6 and abs(v[1] - ridge) < 1e-6]
    assert ends


def test_roof_plan_drawn_elsewhere_is_centred_on_the_walls(tmp_path):
    doc, msp = building()
    hip_roof(msp, dx=3000, dy=4000)  # roof plan far from the building
    obj = Obj(run(tmp_path, doc, Config(roof=True, roof_pitch=30)).output)
    lo, hi = obj.bbox("Tetto")
    assert (lo[0], hi[0]) == (pytest.approx(-0.5, abs=1e-3), pytest.approx(10.5, abs=1e-3))


def test_roof_offset_option(tmp_path):
    doc, msp = building()
    hip_roof(msp, dx=3000, dy=0)
    cfg = Config(roof=True, roof_pitch=30, roof_offset=(-3000.0, 100.0))
    lo, hi = Obj(run(tmp_path, doc, cfg).output).bbox("Tetto")
    assert (lo[0], hi[0]) == (pytest.approx(-0.5, abs=1e-3), pytest.approx(10.5, abs=1e-3))
    assert lo[2] == pytest.approx(-6.5 - 1.0, abs=1e-3)  # shifted 1 m in y


def test_default_pitch_is_used_without_pitch_or_elevation(tmp_path):
    doc, msp = building()
    hip_roof(msp)
    rep = run(tmp_path, doc, Config(roof=True))
    assert rep.roof["pitch_source"] == "predefinita"
    assert rep.roof["pitch_deg"] == pytest.approx(25.0, abs=0.01)


def test_ridge_height_is_read_from_the_elevation(tmp_path):
    doc, msp = building()
    rx0, rx1, _, half = hip_roof(msp)
    area = south_elevation(msp, ground_y=-2000)
    # the ridge as the south elevation draws it: a horizontal line 4.3 m above the floor level,
    # spanning the same x as the ridge in the roof plan
    msp.add_line((rx0, -2000 + 430), (rx1, -2000 + 430), dxfattribs={"layer": "Prospetto Sud"})
    rep = run(tmp_path, doc, Config(roof=True, elevations=[area]))
    assert rep.roof["pitch_source"] == "prospetto"
    assert rep.roof["ridges_from_elevation"] == 1
    assert rep.roof["ridge_height"] == pytest.approx(4.30, abs=1e-3)
    expected_pitch = math.degrees(math.atan((4.30 - H) / (half / 100)))
    assert rep.roof["pitch_deg"] == pytest.approx(expected_pitch, abs=0.05)


def test_explicit_pitch_wins_over_the_elevation(tmp_path):
    doc, msp = building()
    rx0, rx1, _, half = hip_roof(msp)
    area = south_elevation(msp, ground_y=-2000)
    msp.add_line((rx0, -2000 + 430), (rx1, -2000 + 430), dxfattribs={"layer": "Prospetto Sud"})
    rep = run(tmp_path, doc, Config(roof=True, roof_pitch=20, elevations=[area]))
    assert rep.roof["pitch_source"] == "indicata"
    assert rep.roof["ridge_height"] == pytest.approx(H + math.tan(math.radians(20)) * half / 100, abs=1e-3)


def test_l_shaped_hip_roof_with_valley(tmp_path):
    doc = ezdxf.new("R2018", setup=True)
    doc.units = 5
    doc.layers.add("MURI")
    msp = doc.modelspace()
    L = [(0, 0), (1200, 0), (1200, 500), (500, 500), (500, 1200), (0, 1200)]
    msp.add_lwpolyline(L, close=True, dxfattribs={"layer": "MURI"})
    inner = [(30, 30), (1170, 30), (1170, 470), (470, 470), (470, 1170), (30, 1170)]
    msp.add_lwpolyline(inner, close=True, dxfattribs={"layer": "MURI"})
    l_roof(msp)
    path = tmp_path / "l.dxf"
    doc.saveas(path)
    cfg = Config(roof=True, roof_pitch=30, area=(-100, -100, 1300, 1300), roof_area=(-100, -100, 1300, 1300))
    rep = convert(path, tmp_path / "l.obj", cfg)
    obj = Obj(rep.output)
    ridge = H + math.tan(math.radians(30)) * 2.5
    assert obj.bbox("Tetto")[1][1] == pytest.approx(ridge, abs=1e-3)
    assert obj.open_edges("Tetto") == 0 and obj.non_manifold_edges("Tetto") == 0
    assert obj.volume("Tetto") > 0
    assert rep.roof["faces"] == 6  # front + back of each wing (4) and the two end hips


def test_roof_with_only_an_outline_is_one_sloping_face_with_a_warning(tmp_path):
    doc, msp = building()
    msp.add_lwpolyline([(-50, -50), (1050, -50), (1050, 650), (-50, 650)], close=True,
                       dxfattribs={"layer": "Tetto"})
    rep = run(tmp_path, doc, Config(roof=True, roof_pitch=20))
    assert rep.roof["faces"] == 1
    assert any("una sola falda" in w for w in rep.warnings)


def test_roof_requested_but_no_roof_layer(tmp_path):
    doc, _ = building()
    rep = run(tmp_path, doc, Config(roof=True))
    assert rep.roof is None and "Tetto" not in rep.groups
    assert any("nessuna linea trovata" in w for w in rep.warnings)


def test_no_roof_unless_requested(tmp_path):
    doc, msp = building()
    hip_roof(msp)
    assert "Tetto" not in run(tmp_path, doc).groups


def test_roof_layer_override(tmp_path):
    from dwg2c4d.config import LayerRules
    doc, msp = building()
    hip_roof(msp, layer="COPERTURA_X")
    cfg = Config(roof=True, roof_pitch=30)
    cfg.layers = LayerRules({"roof": ["copertura_x"]})
    assert "Tetto" in run(tmp_path, doc, cfg).groups


# --- CLI -----------------------------------------------------------------------------------

def test_cli_elevation_and_roof_options(tmp_path, capsys):
    doc, msp = building()
    area = south_elevation(msp)
    hip_roof(msp)
    path = tmp_path / "p.dxf"
    doc.saveas(path)
    out = tmp_path / "o.obj"
    arg = ",".join(str(v) for v in area)
    plan = ",".join(str(v) for v in PLAN_AREA)
    assert cli.main([str(path), "-o", str(out), f"--area={plan}", f"--prospetto={arg}", "--tetto",
                     "--pendenza", "30", "--spessore-tetto", "0.2"]) == 0
    text = capsys.readouterr().out
    assert "Prospetto sud" in text and "2 di 2" in text
    assert "Tetto" in text and "30 gradi (indicata)" in text
    obj = Obj(out)
    assert obj.bbox("Tetto")[0][1] == pytest.approx(H - 0.2, abs=1e-3)


def test_cli_repeated_prospetto_and_layer_tetto(tmp_path, capsys):
    doc, msp = building()
    a, b = south_elevation(msp), north_elevation(msp)
    hip_roof(msp, layer="XYZ")
    path = tmp_path / "p.dxf"
    doc.saveas(path)
    plan = ",".join(str(v) for v in PLAN_AREA)
    rc = cli.main([str(path), "-o", str(tmp_path / "o.obj"), f"--area={plan}",
                   f"--prospetto={','.join(map(str, a))}", f"--prospetto={','.join(map(str, b))}",
                   "--layer-tetto", "XYZ"])
    assert rc == 0
    text = capsys.readouterr().out
    assert "Prospetto sud" in text and "Prospetto nord" in text and "Tetto" in text


@pytest.mark.parametrize("args", [["--prospetto", "1,2,3"], ["--area-tetto", "1,2"],
                                  ["--sposta-tetto", "1"]])
def test_cli_bad_numbers_are_rejected(args):
    with pytest.raises(SystemExit):
        cli.main(["x.dxf", *args])


def test_cli_validation_errors(tmp_path, capsys):
    doc, _ = building()
    path = tmp_path / "p.dxf"
    doc.saveas(path)
    assert cli.main([str(path), "--tetto", "--pendenza", "0"]) == 1
    assert "pendenza" in capsys.readouterr().err
    assert cli.main([str(path), "--prospetto", "5,5,1,1"]) == 1
    assert "prospetto" in capsys.readouterr().err


def test_config_json_with_elevations_and_roof(tmp_path):
    import json
    f = tmp_path / "c.json"
    f.write_text(json.dumps({"roof": True, "roof_pitch": 22, "roof_area": [0, 0, 5, 5],
                             "elevations": [[0, -10, 5, -5, -9]], "layers": {"roof": ["TT*"]}}))
    cfg = Config.from_json(f)
    cfg.validate()
    assert cfg.roof and cfg.roof_pitch == 22 and cfg.roof_area == (0, 0, 5, 5)
    assert cfg.elevations == [(0.0, -10.0, 5.0, -5.0, -9.0)]
    assert cfg.layers.classify_layer("TT1") == "roof"


# --- regressions from the adversarial review -------------------------------------------------

def _rect(msp, x0, y0, x1, y1, layer):
    msp.add_lwpolyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], close=True, dxfattribs={"layer": layer})


@pytest.mark.parametrize("inset", [4, 8, 10])
def test_window_drawn_as_frame_plus_pane_uses_the_outer_rectangle(tmp_path, inset):
    doc, msp = building()
    gy = -2000
    _rect(msp, DOOR[0], gy, DOOR[1], gy + 200, "PORTE")
    _rect(msp, WIN_S[0], gy + 100, WIN_S[1], gy + 240, "FINESTRE")                      # frame
    _rect(msp, WIN_S[0] + inset, gy + 100 + inset, WIN_S[1] - inset, gy + 240 - inset, "FINESTRE")  # pane
    rep = run(tmp_path, doc, Config(elevations=[(-100, gy - 100, W + 100, gy + 900)]))
    assert rep.elevations[0]["total"] == 2  # one door, one window
    assert glass_heights(Obj(rep.output), 4.9, 6.3) == (pytest.approx(1.0), pytest.approx(2.4))


def test_elevation_window_does_not_attach_to_an_interior_door(tmp_path):
    """The plan has no window at that x but a vestibule door 65 cm inside the facade."""
    doc, msp = building()
    # remove the plan's south window symbol by drawing a fresh plan without it
    doc2 = ezdxf.new("R2018", setup=True)
    doc2.units = 5
    for name in ("MURI", "PORTE", "FINESTRE"):
        doc2.layers.add(name)
    m = doc2.modelspace()
    _rect(m, 0, 0, W, D, "MURI")
    _rect(m, 30, 30, W - 30, D - 30, "MURI")
    _rect(m, DOOR[0], 0, DOOR[1], 30, "PORTE")
    _rect(m, 30, 95, W - 30, 105, "MURI")          # vestibule partition 65 cm inside the facade
    _rect(m, WIN_S[0], 95, WIN_S[0] + 90, 105, "PORTE")  # door in it, same x as the elevation window
    gy = -2000
    _rect(m, DOOR[0], gy, DOOR[1], gy + 200, "PORTE")
    _rect(m, WIN_S[0], gy + 100, WIN_S[1], gy + 240, "FINESTRE")
    rep = run(tmp_path, doc2, Config(elevations=[(-100, gy - 100, W + 100, gy + 900)]))
    obj = Obj(rep.output)
    # the interior door keeps the default 2.10 m: a lintel soffit at 2.1 in its x range
    levels = {round(float(obj.v[i][1]), 3) for ids, _ in obj.groups["Muri"] for i in ids
              if 5.0 <= obj.v[i][0] <= 5.9 and abs(obj.v[i][2] + 1.0) < 0.06}
    assert 2.1 in levels and 1.4 not in levels
    assert any("non corrispondono" in w for w in rep.warnings)


def test_same_kind_window_still_matches_behind_a_projecting_balcony(tmp_path):
    doc, msp = building()
    _rect(msp, 400, -150, 700, 0, "MURI")  # balcony slab/parapet drawn on the wall layer, 1.5 m proud
    gy = -2000
    area = south_elevation(msp, ground_y=gy, sill=100, win_h=140)
    rep = run(tmp_path, doc, Config(elevations=[area]))
    assert glass_heights(Obj(rep.output), 4.9, 6.3) == (pytest.approx(1.0), pytest.approx(2.4))


def test_roof_with_a_wide_nested_outline_is_not_flattened(tmp_path):
    """Eave outline 1 m out, wall line 50 cm out, hips drawn from the inner one."""
    doc, msp = building()
    msp.add_lwpolyline([(-100, -100), (W + 100, -100), (W + 100, D + 100), (-100, D + 100)], close=True,
                       dxfattribs={"layer": "Tetto"})
    hip_roof(msp, overhang=50)
    rep = run(tmp_path, doc, Config(roof=True, roof_pitch=30))
    half = (D + 100) / 200
    assert rep.roof["faces"] == 4
    obj = Obj(rep.output)
    assert obj.bbox("Tetto")[1][1] == pytest.approx(H + math.tan(math.radians(30)) * half, abs=1e-3)
    assert obj.open_edges("Tetto") == 0 and obj.volume("Tetto") > 0


def test_roof_drawn_only_as_a_hatch_uses_its_outline(tmp_path):
    doc, msp = building()
    h = msp.add_hatch(color=1, dxfattribs={"layer": "Tetto"})
    h.paths.add_polyline_path([(-50, -50), (W + 50, -50), (W + 50, D + 50), (-50, D + 50)], is_closed=True)
    rep = run(tmp_path, doc, Config(roof=True, roof_pitch=20))
    assert rep.roof is not None and rep.roof["faces"] == 1


def test_roof_outline_corner_open_by_a_few_cm_is_closed(tmp_path):
    doc, msp = building()
    pts = [(-50, -50), (W + 50, -50), (W + 50, D + 50), (-50, D + 50)]
    for a, b in zip(pts, pts[1:] + pts[:1]):
        msp.add_line(a, b, dxfattribs={"layer": "Tetto"})
    # open the top-left corner by 6 cm
    for e in list(msp.query('LINE[layer=="Tetto"]')):
        if tuple(round(c) for c in e.dxf.end.xy) == (-50, 650):
            e.dxf.end = (-50, 644)
    rep = run(tmp_path, doc, Config(roof=True, roof_pitch=20))
    assert rep.roof is not None
    lo, hi = Obj(rep.output).bbox("Tetto")
    assert (hi[0] - lo[0], hi[2] - lo[2]) == (pytest.approx(11.0, abs=0.01), pytest.approx(7.0, abs=0.01))


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_non_finite_numbers_are_rejected(bad):
    for cfg in (Config(roof_pitch=bad), Config(elevations=[(0, -10, 5, -5, bad)]),
                Config(roof_offset=(bad, 0))):
        with pytest.raises(ValueError):
            cfg.validate()


@pytest.mark.parametrize("factor,tag", [(10, 4), (0.01, 6)])
def test_drawing_units_do_not_change_the_result(tmp_path, factor, tag):
    """The same building drawn in cm, mm and m: elevation, roof and offsets scale correctly."""
    from ezdxf.math import Matrix44

    def make(f, units):
        doc, msp = building()
        area = south_elevation(msp)
        hip_roof(msp, dx=3000, dy=4000)
        rx0, rx1, _, _ = (50, 950, 300, 350)
        msp.add_line((rx0 + 3000 - 50 + 350 - 350 + 50, 0), (0, 0))  # placeholder removed below
        msp.delete_entity(list(msp)[-1])
        if f != 1:
            m = Matrix44.scale(f, f, f)
            for e in msp:
                e.transform(m)
        doc.units = units
        path = tmp_path / f"u{units}.dxf"
        doc.saveas(path)
        sc = lambda v: tuple(c * f for c in v)  # noqa: E731
        plan = sc(PLAN_AREA)
        cfg = Config(area=plan, elevations=[sc(area)], roof=True, roof_pitch=30,
                     roof_offset=sc((-3000.0, -4000.0)))
        return convert(path, tmp_path / f"u{units}.obj", cfg)

    ref, other = make(1, 5), make(factor, tag)
    assert not other.unit_guessed
    a, b = Obj(ref.output), Obj(other.output)
    for g in ("Muri", "Vetri", "Tetto"):
        assert b.volume(g) == pytest.approx(a.volume(g), rel=1e-4)
        assert b.bbox(g)[0] == pytest.approx(a.bbox(g)[0], abs=1e-4)
        assert b.bbox(g)[1] == pytest.approx(a.bbox(g)[1], abs=1e-4)
    assert other.elevations == ref.elevations


# --- two roof volumes of different heights (the case that came out badly in Cinema 4D) -------

TWO_VOLUMES_OUTER = [(72435.2, 50401.1), (73215.2, 50401.1), (73215.2, 50701.1), (73999.2, 50701.1),
                     (73999.2, 49821.1), (73520.2, 49821.1), (73520.2, 49771.1), (72435.2, 49771.1)]
TWO_VOLUMES_INNER = [(72445.2, 50391.1), (73225.2, 50391.1), (73225.2, 50691.1), (73989.2, 50691.1),
                     (73989.2, 49831.1), (73510.2, 49831.1), (73510.2, 49781.1), (72445.2, 49781.1)]
TWO_VOLUMES_LINES = [
    ((73225.2, 50391.1), (73225.2, 50111.1)), ((73480.2, 49831.1), (73510.2, 49831.1)),
    ((73989.2, 50691.1), (73789.2, 50261.1)), ((73789.2, 50261.1), (73989.2, 49831.1)),
    ((73789.2, 50261.1), (73225.2, 50261.1)), ((72445.2, 50391.1), (72645.2, 50086.1)),
    ((72445.2, 49781.1), (72645.2, 50086.1)), ((72645.2, 50086.1), (73480.2, 50086.1)),
    ((73480.2, 50111.1), (73480.2, 49831.1)), ((73225.2, 50111.1), (73480.2, 50111.1)),
]


def two_volume_roof(tmp_path):
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    from dwg2c4d.reader import read_items
    from dwg2c4d.roof import build_roof

    doc = ezdxf.new("R2018", setup=True)
    doc.units = 5
    doc.layers.add("Tetto")
    msp = doc.modelspace()
    for ring in (TWO_VOLUMES_OUTER, TWO_VOLUMES_INNER):
        msp.add_lwpolyline(ring, close=True, dxfattribs={"layer": "Tetto"})
    for a, b in TWO_VOLUMES_LINES:
        msp.add_line(a, b, dxfattribs={"layer": "Tetto"})
    path = tmp_path / "roof.dxf"
    doc.saveas(path)
    items = read_items(ezdxf.readfile(path), Config(), area=None, unit="cm").items
    inner = Polygon([(x / 100, y / 100) for x, y in TWO_VOLUMES_INNER])
    # ridge heights above the eaves, as the elevation gives them (x extents in metres)
    hints = [(726.45, 735.10, 1.597), (732.25, 737.89, 2.183)]
    warnings: list[str] = []
    roof = build_roof(items, Config(), 0.01, inner.bounds, hints, warnings)
    return roof, inner, unary_union([inner]), warnings


def test_two_volume_roof_keeps_each_volume_to_its_own_ridge(tmp_path):
    roof, _, _, warnings = two_volume_roof(tmp_path)
    assert roof.faces == 6 and roof.ridges_from_elevation == 2 and roof.pitch_source == "prospetto"
    zs = [p[2] for t in roof.triangles for p in t]
    assert max(zs) == pytest.approx(2.183, abs=1e-3)
    # nothing on the lower volume (west of the taller one) is above the lower ridge
    west = [p[2] for t in roof.triangles for p in t if p[0] < 732.2]
    assert max(west) == pytest.approx(1.597, abs=1e-3)
    ridge_y = 500.861
    on_ridge = [p[2] for t in roof.triangles for p in t if abs(p[1] - ridge_y) < 1e-3 and 726.4 < p[0] < 734.9]
    # the ridge is at its elevation height at both ends (at the east end the taller volume's slope is
    # 30 cm lower, which shows as a second value there: a step, not a blend)
    assert max(on_ridge) == pytest.approx(1.597, abs=1e-3) and min(on_ridge) >= 1.29
    assert sum(1 for z in on_ridge if abs(z - 1.597) < 1e-3) >= 2


def test_two_volume_roof_has_a_wall_where_the_gable_stands_over_the_lower_roof(tmp_path):
    roof, _, _, _ = two_volume_roof(tmp_path)
    assert len(roof.steps) >= 1
    # every step wall is vertical: its points pair up on the same (x, y)
    for wall in roof.steps:
        xy = {(round(x, 4), round(y, 4)) for x, y, _ in wall}
        assert len(xy) <= 2 and len(wall) in (3, 4)


def test_two_volume_roof_top_covers_the_outline_exactly_once(tmp_path):
    roof, inner, _, _ = two_volume_roof(tmp_path)
    area = sum(abs((b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])) / 2
               for a, b, c in roof.triangles)
    assert area == pytest.approx(inner.area, rel=1e-6)


def test_two_volume_roof_solid_is_geometrically_closed(tmp_path):
    """Topological T-junctions (a vertex in the middle of a neighbour's edge) are allowed where a
    gable wall meets the outline; what must never happen is a gap: the signed volume is
    positive and every outward normal agrees with its winding."""
    doc, msp = building()
    for ring in (TWO_VOLUMES_OUTER, TWO_VOLUMES_INNER):
        msp.add_lwpolyline([(x - 72435 - 50, y - 49771 - 50) for x, y in ring], close=True,
                           dxfattribs={"layer": "Tetto"})
    for a, b in TWO_VOLUMES_LINES:
        msp.add_line((a[0] - 72485, a[1] - 49821), (b[0] - 72485, b[1] - 49821), dxfattribs={"layer": "Tetto"})
    rep = run(tmp_path, doc, Config(roof=True, roof_pitch=25))
    obj = Obj(rep.output)
    assert obj.volume("Tetto") > 0 and obj.winding_matches_normals("Tetto")
    assert obj.non_manifold_edges("Tetto") == 0


# --- facade elevations found by their layer name ---------------------------------------------

def outline(msp, area, layer):
    """The facade outline on its own layer: what the program can find without being told where."""
    x0, y0, x1, y1 = area[:4]
    msp.add_lwpolyline([(x0 + 100, y0 + 100), (x1 - 100, y0 + 100), (x1 - 100, y1 - 100), (x0 + 100, y1 - 100)],
                       close=True, dxfattribs={"layer": layer})


def test_a_prospetto_layer_below_the_plan_is_found_without_coordinates(tmp_path):
    doc, msp = building()
    area = south_elevation(msp, ground_y=-2000, door_h=200, sill=100, win_h=140)
    outline(msp, area, "Prospetto Sud")
    rep = run(tmp_path, doc)  # no Config(elevations=...)
    assert rep.elevations == [{"side": "south", "matched": 2, "total": 2, "zero_source": "porta",
                               "found_on": "Prospetto Sud"}]
    assert glass_heights(Obj(rep.output), 4.9, 6.3) == (pytest.approx(1.0), pytest.approx(2.4))


def test_north_elevation_above_the_plan_too(tmp_path):
    doc, msp = building()
    ground = 2500
    msp.add_lwpolyline([(300, ground + 110), (420, ground + 110), (420, ground + 240), (300, ground + 240)],
                       close=True, dxfattribs={"layer": "FINESTRE"})
    msp.add_lwpolyline([(0, ground), (W_ELEV, ground), (W_ELEV, ground + 800), (0, ground + 800)], close=True,
                       dxfattribs={"layer": "Prospetto Nord"})
    msp.add_lwpolyline([(100, ground), (200, ground), (200, ground + 210), (100, ground + 210)], close=True,
                       dxfattribs={"layer": "PORTE"})
    rep = run(tmp_path, doc)
    assert [e["side"] for e in rep.elevations] == ["north"] and rep.elevations[0]["found_on"] == "Prospetto Nord"


W_ELEV = 1000


def test_an_interior_elevation_without_openings_is_not_a_facade(tmp_path):
    doc, msp = building()
    msp.add_lwpolyline([(300, -900), (700, -900), (700, -600), (300, -600)], close=True,
                       dxfattribs={"layer": "Prospetto Cucina"})  # a kitchen wall: no doors or windows
    rep = run(tmp_path, doc)
    assert rep.elevations == []


def test_a_zone_beside_the_plan_is_not_a_facade(tmp_path):
    doc, msp = building()
    msp.add_lwpolyline([(1500, 100), (2500, 100), (2500, 500), (1500, 500)], close=True,
                       dxfattribs={"layer": "Prospetto Est"})
    msp.add_lwpolyline([(1600, 100), (1700, 100), (1700, 300), (1600, 300)], close=True, dxfattribs={"layer": "PORTE"})
    assert run(tmp_path, doc).elevations == []


def test_explicit_elevations_win_over_the_search(tmp_path):
    doc, msp = building()
    area = south_elevation(msp, ground_y=-2000, door_h=200, sill=100, win_h=140)
    outline(msp, area, "Prospetto Sud")
    rep = run(tmp_path, doc, Config(elevations=[area]))
    assert "found_on" not in rep.elevations[0]


def test_the_search_can_be_switched_off(tmp_path):
    doc, msp = building()
    area = south_elevation(msp, ground_y=-2000, door_h=200, sill=100, win_h=140)
    outline(msp, area, "Prospetto Sud")
    assert run(tmp_path, doc, Config(elevations_auto=False)).elevations == []


# --- the roof is built by itself when a roof layer has lines (command line) -------------------

def test_the_library_builds_no_roof_unless_asked(tmp_path):
    doc, msp = building()
    hip_roof(msp)
    assert "Tetto" not in run(tmp_path, doc).groups


def test_roof_auto_builds_it_from_the_roof_layer(tmp_path):
    doc, msp = building()
    hip_roof(msp)
    rep = run(tmp_path, doc, Config(roof_auto=True))
    assert "Tetto" in rep.groups and rep.roof["faces"] == 4 and rep.warnings == []


def test_roof_auto_without_a_roof_layer_says_nothing(tmp_path):
    doc, _ = building()
    rep = run(tmp_path, doc, Config(roof_auto=True))
    assert rep.roof is None and not any("Tetto" in w for w in rep.warnings)


def test_asking_for_a_roof_that_is_not_there_still_warns(tmp_path):
    doc, _ = building()
    rep = run(tmp_path, doc, Config(roof=True))
    assert any("Tetto: nessuna linea" in w for w in rep.warnings)


def test_a_roof_line_on_an_elevation_layer_is_not_a_roof_plan(tmp_path):
    doc, msp = building()
    doc.layers.add("Prospetto Tetto")
    hip_roof(msp, layer="Prospetto Tetto")
    rep = run(tmp_path, doc, Config(roof_auto=True))
    assert rep.roof is None


def test_command_line_builds_the_roof_by_default_and_can_skip_it(tmp_path, capsys):
    doc, msp = building()
    hip_roof(msp)
    path = tmp_path / "c.dxf"
    doc.saveas(path)
    area = "--area=" + ",".join(map(str, PLAN_AREA))
    assert cli.main([str(path), "-o", str(tmp_path / "a.obj"), area, "--no-immagini"]) == 0
    assert "Tetto              :" in capsys.readouterr().out and "Tetto" in Obj(tmp_path / "a.obj").groups
    assert cli.main([str(path), "-o", str(tmp_path / "b.obj"), area, "--no-immagini", "--no-tetto"]) == 0
    assert "Tetto" not in Obj(tmp_path / "b.obj").groups
