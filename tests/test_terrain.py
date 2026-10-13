"""The terrain from the contour lines of the sheet: reading them, the platform of the house, the slab, the garden laid on
it. Synthetic sheets (centimetres): the 10 x 6 m building of ``builders`` on a slope that rises 1 m every 10 m eastwards."""

import numpy as np
import pytest
from builders import building
from helpers import Obj
from shapely.geometry import Polygon, box

from dwg2c4d import Config, convert
from dwg2c4d.cli import build_parser, config_from_args
from dwg2c4d.sitefit import Placement
from dwg2c4d.terrain import (MIN_LEVELS, build_ground, lattice, read_contours, written_floor_level)

FLOOR = 100.5  # m a.s.l.: the finished floor of the house, written in the sheet
STEP = 0.2  # m between contour lines
X0, LEVELS = -2000, 26  # cm: the first contour line (20 m west of the house) and how many there are (every 2 m: 50 m)


def slope_z(x_cm):
    """The height (m a.s.l.) of the slope at x: 100 m at the west wall, 10 % up eastwards."""
    return 100.0 + 0.1 * x_cm / 100.0


def add_contours(msp, layer="CURVE-LIV", levels=LEVELS, kind="lwpolyline", skip=(), first=X0, every=200):
    """Straight contour lines running north-south, each at its own elevation (in cm, like the drawing)."""
    for k in range(levels):
        if k in skip:
            continue
        x = first + every * k
        z = round(slope_z(x) * 100.0)
        if kind == "lwpolyline":
            msp.add_lwpolyline([(x, -3000), (x, 600), (x, 3600)], dxfattribs={"layer": layer, "elevation": z})
        else:  # a 3D polyline: the elevation is in the z of its vertices
            msp.add_polyline3d([(x, -3000, z), (x, 600, z), (x, 3600, z)], dxfattribs={"layer": layer})


def sloping_sheet(tmp_path, text="+0.00 (100.50)", **kw):
    doc, msp = building()
    doc.layers.add(kw.get("layer", "CURVE-LIV"))
    add_contours(msp, **kw)
    if text:
        msp.add_text(text, height=20, dxfattribs={"insert": (1200, 100), "layer": "0"})
    path = tmp_path / "t.dxf"
    doc.saveas(path)
    return path


def run(tmp_path, path=None, **kw):
    kw.setdefault("terrain_auto", True)
    kw.setdefault("floors_to_outer_face", False)
    path = path or sloping_sheet(tmp_path)
    report = convert(path, tmp_path / "t.obj", Config(**kw))
    return report, Obj(tmp_path / "t.obj")


# --- the contour lines ---------------------------------------------------------------------------

def test_a_regular_set_of_levels_is_a_lattice_and_a_stray_line_far_from_it_is_dropped():
    levels = [291.4 + 0.2 * k for k in range(50)]
    grid = lattice(levels + [-1.0])  # -1.0 stands on the same grid of 0.2 by chance
    assert grid is not None and grid[1] == pytest.approx(0.2) and len(grid[0]) == 50
    assert grid[0][0] == pytest.approx(291.4)


def test_levels_with_no_regular_step_are_no_contour_lines():
    assert lattice([100.0, 100.37, 101.9, 102.04, 107.5, 109.13]) is None


def test_contour_lines_are_read_from_polylines_with_an_elevation_or_with_3d_vertices(tmp_path):
    for kind in ("lwpolyline", "polyline3d"):
        doc, msp = building()
        doc.layers.add("QUOTE_X")
        add_contours(msp, layer="QUOTE_X", kind=kind)
        found = read_contours(doc, Config(), 0.01, MIN_LEVELS)
        assert [c.layer for c in found] == ["QUOTE_X"], kind  # whatever the layer is called
        c = found[0]
        assert len(c.levels) == LEVELS and c.step == pytest.approx(STEP) and c.lines == LEVELS
        assert c.xyz[:, 2].min() == pytest.approx(98.0) and c.xyz[:, 2].max() == pytest.approx(103.0)


def test_a_layer_of_polylines_at_random_heights_is_not_a_set_of_contour_lines():
    doc, msp = building()
    rng = np.random.default_rng(3)
    for k in range(30):  # spot heights drawn as little polylines, at whatever height
        z = round(float(rng.uniform(9000, 11000)))
        msp.add_lwpolyline([(k * 100, 700), (k * 100 + 50, 700)], dxfattribs={"layer": "QUOTE", "elevation": z})
    assert read_contours(doc, Config(), 0.01, 3) == []


def test_ten_levels_are_needed_unless_the_user_asks():
    doc, msp = building()
    add_contours(msp, levels=6)
    assert read_contours(doc, Config(), 0.01, MIN_LEVELS) == []
    assert len(read_contours(doc, Config(), 0.01, 3)[0].levels) == 6


def test_a_missing_level_does_not_break_the_set():
    doc, msp = building()
    add_contours(msp, skip={5, 6, 14})
    c = read_contours(doc, Config(), 0.01, MIN_LEVELS)[0]
    assert len(c.levels) == LEVELS - 3 and c.step == pytest.approx(STEP)


# --- the finished floor ----------------------------------------------------------------------------

def test_the_floor_level_is_read_from_the_levels_the_sheet_writes():
    assert written_floor_level(["+0.00 (297.40) QUOTA PIANO"], 291.0, 301.0)[0] == pytest.approx(297.40)
    assert written_floor_level(["-2.40 (295.00)"], 291.0, 301.0)[0] == pytest.approx(297.40)  # the zero is 2.40 above it
    assert written_floor_level(["QUOTA PIANO 297,40"], 291.0, 301.0)[0] == pytest.approx(297.40)
    assert written_floor_level(["+/-0.00 = 297.40"], 291.0, 301.0)[0] == pytest.approx(297.40)


def test_the_floor_level_most_texts_agree_on_wins_and_a_height_in_another_datum_is_ignored():
    texts = ["+0.00 (297.40)", "-2.40 (295.00)", "+3.20 (300.60)", "+0.00 (12.30)"]  # the last one is in another datum
    value, how = written_floor_level(texts, 291.0, 301.0)
    assert value == pytest.approx(297.40) and how == "3 scritte"
    assert written_floor_level(["+0.00 (12.30)", "PIANTA"], 291.0, 301.0) is None


def test_a_level_that_other_texts_contradict_is_said():
    texts = ["+0.00 (297.40)", "+0.00 (297.40)", "QUOTA PIANO FINITO 297.38", "-0.40 (297.00)"]
    value, how = written_floor_level(texts, 291.0, 301.0)
    assert value == pytest.approx(297.40) and how == "3 scritte; altre scritte dicono 297.38 (1)"  # -0.40 (297.00) puts the zero at 297.40 too


# --- the placement ---------------------------------------------------------------------------------

def test_a_placement_and_its_inverse_and_the_chain_of_two():
    p = Placement(1, (10.0, 5.0))
    xy = np.array([[1.0, 2.0], [-3.0, 4.0]])
    assert p.to_plan(p.to_sheet(xy)) == pytest.approx(xy)
    q = Placement(2, (-4.0, 7.0))
    assert p.then(q).to_sheet(xy) == pytest.approx(q.to_sheet(p.to_sheet(xy)))


# --- the terrain of a conversion -------------------------------------------------------------------

def test_contour_lines_over_the_plan_make_a_terrain_with_the_house_on_a_platform(tmp_path):
    report, obj = run(tmp_path)
    t = report.plan.terrain
    assert t is not None and report.terrain["levels"] == LEVELS and report.terrain["floor"] == pytest.approx(FLOOR)
    assert "Terreno" in obj.groups
    assert t.height(500.0 / 100, 300.0 / 100) == pytest.approx(-0.2)  # under the house: the platform, below the floors
    assert t.height(-12.0, 3.0) == pytest.approx(slope_z(-1200) - FLOOR, abs=0.15)  # farther away: the slope itself
    assert t.height(25.0, 3.0) == pytest.approx(slope_z(2500) - FLOOR, abs=0.15)
    (_, wy0, _), _ = obj.bbox("Muri")
    assert wy0 == pytest.approx(-0.2)  # the walls stand on the platform, no gap, no sinking
    lo, hi = obj.bbox("Terreno")
    assert hi[1] == pytest.approx(slope_z(3000) - FLOOR, abs=0.3) and lo[1] < t.z.min() - 0.9  # 1 m of ground under it all


def test_the_terrain_is_a_closed_solid_with_faces_that_agree_with_their_normals(tmp_path):
    _, obj = run(tmp_path)
    assert obj.open_edges("Terreno") == 0
    assert obj.winding_matches_normals("Terreno")
    assert obj.volume("Terreno") > 0


def test_the_written_floor_level_and_the_report(tmp_path):
    report, _ = run(tmp_path)
    assert report.terrain["floor_source"] == "1 scritte"
    assert any(w.startswith("Terreno: sotto la casa") for w in report.warnings)
    from dwg2c4d.qa import summary_lines

    assert any(line.startswith("  Terreno") and "26 curve di livello" in line and "100.50" in line for line in summary_lines(report))


def test_without_a_written_floor_level_the_ground_under_the_house_is_taken_and_said(tmp_path):
    path = sloping_sheet(tmp_path, text="")
    report, _ = run(tmp_path, path)
    assert report.terrain["floor"] == pytest.approx(slope_z(500), abs=0.1)  # the middle of the house
    assert any("non ho trovato la quota del pavimento finito" in w for w in report.warnings)


FEW = {"levels": 6, "first": -500, "every": 400}  # six lines, 4 m apart, from 5 m west of the house to 15 m east of it


def test_no_terrain_when_the_user_says_no_or_the_sheet_has_few_contour_lines(tmp_path):
    report, obj = run(tmp_path, terrain_auto=False)
    assert report.terrain is None and "Terreno" not in obj.groups
    (_, wy0, _), _ = obj.bbox("Muri")
    assert wy0 == pytest.approx(0.0)  # the walls stand at zero as ever
    few = sloping_sheet(tmp_path, **FEW)
    report, obj = run(tmp_path, few)
    assert report.terrain is None and "Terreno" not in obj.groups and not any("Terreno" in w for w in report.warnings)


def test_asking_for_the_terrain_accepts_few_contour_lines(tmp_path):
    few = sloping_sheet(tmp_path, **FEW)
    report, obj = run(tmp_path, few, terrain=True, terrain_auto=False)
    assert report.terrain is not None and report.terrain["levels"] == 6 and "Terreno" in obj.groups


def test_asking_for_the_terrain_where_there_are_no_contour_lines_says_so(tmp_path):
    doc, _ = building()
    doc.saveas(tmp_path / "t.dxf")
    report = convert(tmp_path / "t.dxf", tmp_path / "t.obj", Config(terrain=True, floors_to_outer_face=False))
    assert report.terrain is None and any("non ho trovato curve di livello" in w for w in report.warnings)


def test_contour_lines_of_another_lot_far_from_the_house_make_no_terrain(tmp_path):
    doc, msp = building()
    for k in range(LEVELS):  # the same lines, 400 m away
        x = 40000 + X0 + 200 * k
        msp.add_lwpolyline([(x, -3000), (x, 3600)], dxfattribs={"layer": "CURVE-LIV", "elevation": round(slope_z(X0 + 200 * k) * 100)})
    doc.saveas(tmp_path / "t.dxf")
    report = convert(tmp_path / "t.dxf", tmp_path / "t.obj", Config(terrain_auto=True, floors_to_outer_face=False))
    assert report.terrain is None and any("non coprono il punto dove sta l'edificio" in w for w in report.warnings)
    assert Obj(tmp_path / "t.obj").bbox("Muri")[0][1] == pytest.approx(0.0)


def test_the_thickness_of_the_terrain_is_an_option(tmp_path):
    report, obj = run(tmp_path, terrain_thickness=2.5)
    t = report.plan.terrain
    assert t.bottom == pytest.approx(t.z.min() - 2.5) and obj.bbox("Terreno")[0][1] == pytest.approx(t.bottom)


def test_the_command_line_options_of_the_terrain():
    cfg = config_from_args(build_parser().parse_args(["x.dxf", "--terreno", "--spessore-terreno", "2"]))
    assert cfg.terrain and cfg.terrain_auto and cfg.terrain_thickness == 2.0
    cfg = config_from_args(build_parser().parse_args(["x.dxf", "--no-terreno"]))
    assert not cfg.terrain and not cfg.terrain_auto
    cfg = config_from_args(build_parser().parse_args(["x.dxf"]))
    assert not cfg.terrain and cfg.terrain_auto and cfg.terrain_thickness == 1.0
    with pytest.raises(SystemExit):
        build_parser().parse_args(["x.dxf", "--terreno", "--no-terreno"])


def test_the_ground_is_flat_on_the_platform_and_never_steeper_than_the_embankment_beyond_it():
    pts = np.array([[x, y, 100.0 + 0.9 * x] for x in np.arange(-30, 31, 2.0) for y in (-30.0, 30.0)])  # a steep slope, 90 %
    t = build_ground(pts, (-30, -30, 30, 30), box(-5, -5, 5, 5), Polygon(), 100.0, -0.2, 1.0)
    assert t.height(0.0, 0.0) == pytest.approx(-0.2) and t.height(4.0, 4.0) == pytest.approx(-0.2)
    rise = (t.height(9.0, 0.0) - t.height(5.0, 0.0)) / 4.0
    assert rise <= 0.5 + 1e-6  # the embankment keeps to 1:2 although the ground climbs at 0.9
