"""The site round the buildings of a site plan (siteground): the garden with its pool, paving, lawn and plants, the terrain,
the low walls; synthetic sheets, centimetres. The buildings are those of test_sitemode: a hip roof 12 x 8 m at the origin and
a gable roof 6 x 5 m at (18, 1) m."""

import ezdxf
import pytest

from dwg2c4d import Config, convert
from dwg2c4d.qa import summary_lines
from helpers import Obj
from test_sitemode import site_sheet


def hatch(msp, points, layer, color=None, pattern="SOLID", rgb=None):
    """A hatch over ``points`` (cm) on ``layer``; a solid one in ``rgb``, or a pattern in the ACI ``color``."""
    h = msp.add_hatch(color=color if color is not None else 256, dxfattribs={"layer": layer})
    if pattern != "SOLID":
        h.set_pattern_fill(pattern, scale=20)
    if rgb is not None:
        h.rgb = rgb
    h.paths.add_polyline_path(points)
    return h


def rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def garden_sheet(path, pool=True, panels=False, canopy=False, tree=False, lawn=True, in_elevation=False, **kw):
    """The site sheet with a garden: a lawn and a pool under the hip roof (south of it), and, if asked, a carport of blue panels on
    a layer that is no pool's, a canopy (a small roof of tiles with no building under it), a tree, a lawn hatch in an elevation."""
    site_sheet(path, elevation=in_elevation, **kw)
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()
    for name in ("Prato", "Piscina", "ALBERI", "Pensilina"):
        doc.layers.add(name)
    if lawn:
        hatch(msp, rect(-400, -700, 1000, -100), "Prato", rgb=(60, 160, 60))
    if pool:
        hatch(msp, rect(200, -1100, 700, -800), "Piscina", rgb=(90, 170, 230))
    if panels:
        hatch(msp, rect(1500, -600, 2100, -300), "Pensilina", rgb=(90, 170, 230))
    if canopy:
        hatch(msp, rect(2500, -300, 2700, -100), "Coppi Scuro", color=30, pattern="COPPI01")
    if tree:
        block = doc.blocks.new("ALBERO")
        block.add_circle((0, 0), 100)
        msp.add_blockref("ALBERO", (1000, -950), dxfattribs={"layer": "ALBERI"})
    if in_elevation:
        hatch(msp, rect(9100, 100, 9500, 400), "Prato", rgb=(60, 160, 60))
    doc.saveas(path)
    return path


def converted(tmp_path, cfg=None, **kw):
    path = garden_sheet(tmp_path / "t.dxf", **kw)
    report = convert(path, tmp_path / "t.obj", cfg or Config(auto=True, images=False, roof_auto=True, garden=True))
    return report, Obj(tmp_path / "t.obj")


def test_the_garden_round_the_buildings_is_built_and_reported(tmp_path):
    report, obj = converted(tmp_path)
    garden = report.garden
    assert garden is not None and len(garden.pools) == 1
    assert garden.pools[0].area == pytest.approx(15.0, abs=0.1)
    assert garden.area("lawn") == pytest.approx(84.0, abs=1.0)
    for group in ("Prato", "Vasca", "Acqua", "E1_Muri"):
        assert group in obj.groups
    assert any(line.startswith("  Giardino") for line in summary_lines(report))


def test_the_garden_can_be_left_out(tmp_path):
    report, obj = converted(tmp_path, Config(auto=True, images=False, roof_auto=True, garden=False))
    assert report.garden is None and "Prato" not in obj.groups and "E1_Muri" in obj.groups


def test_a_blue_hatch_on_a_layer_that_is_no_pool_is_no_water_when_the_sheet_has_a_pool_layer(tmp_path):
    report, obj = converted(tmp_path, panels=True)
    assert len(report.garden.pools) == 1 and report.garden.pools[0].area == pytest.approx(15.0, abs=0.1)  # the 18 m2 of panels are not water
    assert report.garden.area("paving") == pytest.approx(18.0, abs=0.5)  # they are ground: paving


def test_with_no_pool_layer_a_blue_hatch_is_still_water_by_its_colour(tmp_path):
    report, _ = converted(tmp_path, pool=False, panels=True)
    assert len(report.garden.pools) == 1 and report.garden.pools[0].area == pytest.approx(18.0, abs=0.5)


def test_the_hatches_of_a_roof_are_no_ground_even_when_no_building_stands_under_them(tmp_path):
    report, obj = converted(tmp_path, canopy=True)
    assert report.garden.area("paving") == pytest.approx(0.0, abs=0.1)  # the canopy (2 m2 of tiles) and the roofs of the buildings are no paving
    assert "Pavimentazione" not in obj.groups


def test_the_hatches_in_the_elevations_of_the_sheet_are_no_garden(tmp_path):
    report, _ = converted(tmp_path, in_elevation=True)
    assert report.garden.area("lawn") == pytest.approx(84.0, abs=1.0)  # the lawn drawn in the elevation (12 m2) is not counted


def test_the_plants_come_with_the_garden_and_can_be_left_out(tmp_path):
    report, obj = converted(tmp_path, tree=True)
    assert [o.kind for o in report.garden.objects] == ["tree"] and "Chioma_A01" in obj.groups
    bare, obj = converted(tmp_path, Config(auto=True, images=False, roof_auto=True, garden=True, garden_plants=False), tree=True)
    assert bare.garden.objects == [] and "Chioma_A01" not in obj.groups and "Prato" in obj.groups  # the ground stays


# --- the terrain: every building at the height of the ground under it ----------------------------------------------

from test_terrain import add_contours, slope_z  # noqa: E402  (the slope of test_terrain: 10 % up eastwards, contour lines running north-south)

TERRAIN = dict(auto=True, images=False, roof_auto=True, garden=True, terrain_auto=True)


def sloping(tmp_path, cfg=None, contours=True, **kw):
    """The garden sheet on the slope: the hip roof (floor of the zero) and the gable roof 18 m further east, 1.5 m higher."""
    path = garden_sheet(tmp_path / "t.dxf", **kw)
    if contours:
        doc = ezdxf.readfile(path)
        add_contours(doc.modelspace())
        doc.saveas(path)
    report = convert(path, tmp_path / "t.obj", cfg or Config(**TERRAIN))
    return report, Obj(tmp_path / "t.obj")


def test_every_building_stands_on_the_ground_under_it_the_biggest_one_is_the_zero(tmp_path):
    report, obj = sloping(tmp_path)
    hip, gable = report.site.buildings
    assert hip.base == pytest.approx(0.0) and gable.base == pytest.approx(slope_z(2100) - slope_z(600), abs=0.05)  # 1.5 m up
    assert obj.bbox("E1_Muri")[0][1] == pytest.approx(0.0, abs=0.01)
    assert obj.bbox("E2_Muri")[0][1] == pytest.approx(gable.base, abs=0.01)  # the walls of the second stand on its floor
    assert obj.bbox("E2_Pavimento")[0][1] == pytest.approx(gable.base - 0.2, abs=0.01)
    assert obj.bbox("E2_Tetto")[0][1] > gable.base  # the roof goes up with it
    assert report.terrain is not None and "Terreno" in obj.groups
    assert any(w.startswith("Terreno: ogni edificio sta su una piattaforma") and "edificio 2 a +1.50 m" in w for w in report.warnings)


def test_the_ground_is_flat_under_each_building_and_keeps_the_contour_lines_between_them(tmp_path):
    report, obj = sloping(tmp_path)
    ground = report.site.around.terrain
    assert float(ground.height(600.0 / 100.0, 400.0 / 100.0)) == pytest.approx(-0.2, abs=0.01)  # under the first building: its platform
    gable = report.site.buildings[1]
    assert float(ground.height(21.0, 3.5)) == pytest.approx(gable.base - 0.2, abs=0.01)  # under the second: its own
    assert float(ground.height(29.0, 3.5)) == pytest.approx(slope_z(2900) - slope_z(600), abs=0.1)  # beyond them the ground is the survey's: still rising


def test_the_pool_is_dug_at_the_height_of_its_own_ground(tmp_path):
    report, obj = sloping(tmp_path)
    top = obj.bbox("Vasca")[1][1]
    assert top == pytest.approx(slope_z(450) - slope_z(600), abs=0.06)  # the pool lies 1.5 m west of the first building's centre: 15 cm lower
    assert obj.bbox("Acqua")[1][1] == pytest.approx(top - 0.2, abs=0.01)  # the water 20 cm below the edge


def test_with_no_terrain_option_the_buildings_stand_on_the_flat_zero(tmp_path):
    report, obj = sloping(tmp_path, Config(auto=True, images=False, roof_auto=True, garden=True))
    assert report.terrain is None and "Terreno" not in obj.groups
    assert [b.base for b in report.site.buildings] == [0.0, 0.0] and obj.bbox("E2_Muri")[0][1] == pytest.approx(0.0, abs=0.01)


def test_a_sheet_with_no_contour_lines_has_no_terrain_and_no_remark(tmp_path):
    report, obj = sloping(tmp_path, contours=False)
    assert report.terrain is None and [b.base for b in report.site.buildings] == [0.0, 0.0]
    assert not any(w.startswith("Terreno") for w in report.warnings)


def test_the_floor_levels_are_in_the_report(tmp_path):
    report, _ = sloping(tmp_path)
    lines = summary_lines(report)
    assert any("Edificio 2" in line and "pavimento a +1.50 m" in line for line in lines)
    assert any(line.startswith("  Terreno") for line in lines)


def test_contour_lines_far_from_the_buildings_give_no_terrain_and_the_remark_says_so(tmp_path):
    path = garden_sheet(tmp_path / "t.dxf")
    doc = ezdxf.readfile(path)
    for k in range(26):  # the survey of another lot, 50 m to the north
        x = -2000 + 200 * k
        doc.modelspace().add_lwpolyline([(x, 5000), (x, 9000)], dxfattribs={"layer": "CURVE-LIV", "elevation": round(slope_z(x) * 100)})
    doc.saveas(path)
    report = convert(path, tmp_path / "t.obj", Config(**TERRAIN))
    assert report.terrain is None and [b.base for b in report.site.buildings] == [0.0, 0.0]
    assert any("Terreno: le curve di livello del layer CURVE-LIV stanno a" in w for w in report.warnings)


# --- the free-standing walls -----------------------------------------------------------------------------------------

STRIP = rect(-1000, -1100, 0, -1060)  # a wall 10 m long and 40 cm thick, west of the pool
ROUND_UP = 0.02  # m: the slabs of the model are snapped to a grid of a centimetre or so


def walled(tmp_path, cfg=None, rings=(STRIP,), texts=(), layer="Polilinea sup. calpestabile", **kw):
    """The garden sheet with closed outlines (``rings``, cm) on ``layer`` and written ``texts``: (text, x, y) in cm."""
    path = garden_sheet(tmp_path / "t.dxf", **kw)
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()
    doc.layers.add(layer)
    for ring in rings:
        msp.add_lwpolyline(ring, close=True, dxfattribs={"layer": layer})
    for text, x, y in texts:
        msp.add_text(text, height=20, dxfattribs={"layer": "TESTI", "insert": (x, y)})
    doc.saveas(path)
    report = convert(path, tmp_path / "t.obj", cfg or Config(auto=True, images=False, roof_auto=True, garden=True, fences=True))
    return report, Obj(tmp_path / "t.obj")


def test_a_long_thin_closed_outline_in_the_open_is_a_wall_as_high_as_the_text_beside_it_says(tmp_path):
    report, obj = walled(tmp_path, texts=[("Hmuro= 1,00 ml.", -600, -1030)])
    (wall,) = report.site.around.walls.walls
    assert (wall.height, wall.text) == (1.0, "Hmuro= 1,00 ml.") and wall.shape.area == pytest.approx(4.0, abs=0.01)
    assert "Muretti" in obj.groups and obj.bbox("Muretti")[1][1] == pytest.approx(1.0, abs=ROUND_UP)
    assert any(w.startswith("Muri liberi: altezze scritte") and "1 m da 'Hmuro= 1,00 ml.'" in w for w in report.warnings)


def test_a_wall_with_two_heights_written_at_its_ends_has_both(tmp_path):
    report, obj = walled(tmp_path, texts=[("Hmuro= 1,00 ml.", -950, -1030), ("Hmuro= 0,50 ml.", -50, -1030)])
    west, east = sorted(report.site.around.walls.walls, key=lambda w: w.shape.centroid.x)
    assert (west.height, east.height) == (1.0, 0.5)  # the nearest text decides, point by point
    assert west.shape.area + east.shape.area == pytest.approx(4.0, abs=0.01) and west.shape.distance(east.shape) < 0.01  # the wall is whole
    assert obj.bbox("Muretti")[1][1] == pytest.approx(1.0, abs=ROUND_UP)


def test_a_wall_no_text_reaches_has_the_default_height_and_the_report_says_it_is_a_guess(tmp_path):
    report, obj = walled(tmp_path)
    (wall,) = report.site.around.walls.walls
    assert (wall.height, wall.text) == (0.5, "") and obj.bbox("Muretti")[1][1] == pytest.approx(0.5, abs=ROUND_UP)
    assert any("senza nessuna altezza scritta" in w and "--altezza-muretti" in w for w in report.warnings)


def test_a_height_the_user_gives_beats_the_one_the_drawing_writes(tmp_path):
    cfg = Config(auto=True, images=False, roof_auto=True, garden=True, fences=True, low_wall_height=0.8)
    report, _ = walled(tmp_path, cfg, texts=[("Hmuro= 1,00 ml.", -600, -1030)])
    (wall,) = report.site.around.walls.walls
    assert (wall.height, wall.text) == (0.8, "")


def test_a_text_that_no_wall_is_near_is_reported_and_used_by_nothing(tmp_path):
    report, _ = walled(tmp_path, texts=[("Hmuro= 1,00 ml.", -600, -1030), ("Hmuro= 0,50 ml.", 1500, -1100)])
    assert [w.height for w in report.site.around.walls.walls] == [1.0]
    assert any("'Hmuro= 0,50 ml.' non ha nessun muro libero vicino" in w for w in report.warnings)


@pytest.mark.parametrize("ring", [rect(-1000, -1100, -800, -1060),  # 2 m long: a pillar, a step
                                  rect(-1000, -1100, 0, -900),  # 2 m wide: a surface
                                  rect(-1000, -1100, 0, -1098)],  # 2 cm: a line
                         ids=["short", "wide", "line"])
def test_outlines_that_are_no_thin_long_strips_are_no_walls(tmp_path, ring):
    report, obj = walled(tmp_path, rings=(ring,))
    assert report.site.around.walls is None and "Muretti" not in obj.groups and "Cordoli" not in obj.groups


def test_a_strip_under_a_roof_is_no_free_standing_wall(tmp_path):
    report, obj = walled(tmp_path, rings=(rect(100, 300, 1100, 340),))  # across the hip roof
    assert report.site.around.walls is None


def test_the_free_standing_walls_can_be_left_out_with_the_fences(tmp_path):
    report, obj = walled(tmp_path, Config(auto=True, images=False, roof_auto=True, garden=True, fences=False))
    assert report.site.around.walls is None and "Muretti" not in obj.groups


def test_a_thin_strip_is_a_curb_and_a_wall_stands_on_the_terrain(tmp_path):
    report, obj = walled(tmp_path, rings=(rect(-1000, -1100, 0, -1082),), texts=[])  # 18 cm
    (wall,) = report.site.around.walls.walls
    assert wall.curb and wall.height == 0.15 and "Cordoli" in obj.groups


def test_the_hatch_that_fills_a_wall_is_no_paving_and_a_hatch_on_the_layer_of_the_floors_is(tmp_path):
    path = garden_sheet(tmp_path / "t.dxf")
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()
    doc.layers.add("Pavimenti")
    hatch(msp, STRIP, "Pavimenti", rgb=(200, 120, 90))  # the brick of the wall
    hatch(msp, rect(1100, -700, 1600, -100), "Pavimenti", rgb=(200, 120, 90))  # 30 m2 of paving beside the lawn
    msp.add_lwpolyline(STRIP, close=True, dxfattribs={"layer": "Pavimenti"})  # (the outline of the wall is on that layer too)
    doc.saveas(path)
    report = convert(path, tmp_path / "t.obj", Config(auto=True, images=False, roof_auto=True, garden=True, fences=True))
    assert report.garden.area("paving") == pytest.approx(30.0, abs=0.5)  # the strip of the wall (4 m2) is not ground
    assert len(report.site.around.walls.walls) == 1
