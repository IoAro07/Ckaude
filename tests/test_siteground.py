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
        hatch(msp, rect(2800, -500, 3000, -300), "Coppi Scuro", color=30, pattern="COPPI01")
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
