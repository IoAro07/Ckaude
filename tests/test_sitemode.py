"""Site mode: a sheet with no plan of a building but a site plan that draws the roofs of its buildings (a hatch of tiles on
each slope, an arrow for each fall) becomes a model of volumes with their roofs; synthetic sheets, centimetres."""

import math

import ezdxf
import pytest
from shapely.geometry import Polygon

from dwg2c4d import Config, convert
from dwg2c4d import sitemode as sm
from dwg2c4d.dwgfile import ConversionError
from helpers import Obj
from test_views import add_elevation, add_title, new_sheet

GAP = 8  # cm: the strip left between two hatches at a ridge or a hip
PITCH = 20.0


def hip_planes(x, y, w=1200, d=800):
    """The four planes of a hip roof of w x d cm with its south-west corner at (x, y): (polygon, outward normal)."""
    h = d / 2
    return [
        ([(x, y), (x + w, y), (x + w - h, y + h), (x + h, y + h)], (0, -1)),  # south
        ([(x, y + d), (x + h, y + h), (x + w - h, y + h), (x + w, y + d)], (0, 1)),  # north
        ([(x, y), (x + h, y + h), (x, y + d)], (-1, 0)),  # west hip
        ([(x + w, y), (x + w, y + d), (x + w - h, y + h)], (1, 0)),  # east hip
    ]


def gable_planes(x, y, w=600, d=500):
    """The two planes of a gable roof, the ridge along x."""
    h = d / 2
    return [([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], (0, -1)), ([(x, y + h), (x + w, y + h), (x + w, y + d), (x, y + d)], (0, 1))]


def add_plane(msp, points, normal=None, layer="Coppi Scuro", pattern="COPPI01"):
    """One slope: a hatch of tiles, shrunk by half the strip, and (with ``normal``) the arrow that shows how it falls."""
    shape = Polygon(points).buffer(-GAP / 2, join_style="mitre")
    hatch = msp.add_hatch(dxfattribs={"layer": layer})
    hatch.dxf.pattern_name = pattern
    hatch.dxf.solid_fill = 0
    hatch.paths.add_polyline_path(list(shape.exterior.coords)[:-1])
    if normal is None:
        return
    c = shape.representative_point()
    nx, ny = normal
    tip = (c.x + nx * 65, c.y + ny * 65)
    tail = (c.x - nx * 65, c.y - ny * 65)
    msp.add_line(tail, tip, dxfattribs={"layer": "E"})
    for side in (-1, 1):  # the barbs lean back from the tip by 30 degrees
        a = math.atan2(-ny, -nx) + side * math.radians(30)
        msp.add_line(tip, (tip[0] + 40 * math.cos(a), tip[1] + 40 * math.sin(a)), dxfattribs={"layer": "E"})


def add_site_lines(msp, x0=-1500, y0=-1200, x1=4500, y1=2800):
    """What a site plan holds besides the roofs: contour lines (polylines of short pieces, running every way)."""
    for k in range(26):
        pts = [(x0 + i * 200, y0 + k * 160 + 90 * math.sin(i / 6 + k) + (i * k) % 70) for i in range(int((x1 - x0) / 200) + 1)]
        msp.add_lwpolyline(pts, dxfattribs={"layer": "CURVE-LIV"})
    for k in range(10):  # ... and a boundary of a few straight pieces
        msp.add_line((x0 + k * 600, y0), (x0 + k * 600 + 700, y0 + 3600 + 150 * k), dxfattribs={"layer": "CONFINE"})


def site_sheet(path, buildings=None, arrows=True, elevation=False, title="PLANIMETRIA GENERALE", lines=True):
    """A site plan (a hip roof 12 x 8 m and a gable roof 6 x 5 m, 15 m apart) and, if asked, an elevation of the sheet."""
    doc, msp = new_sheet()
    for name in ("Coppi Scuro", "Coppi Chiaro", "E", "CONFINE", "CURVE-LIV"):
        doc.layers.add(name)
    if lines:
        add_site_lines(msp)
    for points, normal in (buildings if buildings is not None else hip_planes(0, 0) + gable_planes(1800, 100)):
        add_plane(msp, points, normal if arrows else None)
    if title:
        add_title(msp, title, -1500, -1600)
    if elevation:
        add_elevation(msp, 9000, 0, w=1200, h=600)
        add_title(msp, "PROSPETTO SUD", 9000, -200)
    doc.saveas(path)
    return path


def converted(tmp_path, cfg=None, **kw):
    path = site_sheet(tmp_path / "t.dxf", **kw)
    report = convert(path, tmp_path / "t.obj", cfg or Config(auto=True, images=False, roof_auto=True))
    return report, Obj(tmp_path / "t.obj")


# --- the buildings ---------------------------------------------------------------------------

def test_a_site_plan_with_roofs_and_no_plan_becomes_buildings(tmp_path):
    report, obj = converted(tmp_path)
    assert report.site is not None and len(report.site.buildings) == 2
    assert [b.size for b in report.site.buildings] == [pytest.approx((12.0, 8.0), abs=0.2), pytest.approx((6.0, 5.0), abs=0.2)]
    for group in ("E1_Muri", "E1_Tetto", "E1_Pavimento", "E2_Muri", "E2_Tetto"):
        assert group in obj.groups
    assert any(w.startswith("Modalita' sito") for w in report.warnings)


def test_every_building_stands_where_its_roof_is_drawn_and_the_walls_are_the_roof_less_the_eaves(tmp_path):
    report, obj = converted(tmp_path, Config(auto=True, images=False, origin="drawing"))
    (rx0, _, rz0), (rx1, _, rz1) = obj.bbox("E1_Tetto")  # OBJ: x, height, -y
    assert (rx0, rx1) == pytest.approx((0.0, 12.0), abs=0.1) and (rz0, rz1) == pytest.approx((-8.0, 0.0), abs=0.1)
    (wx0, _, wz0), (wx1, _, wz1) = obj.bbox("E1_Muri")
    assert (wx0, wx1) == pytest.approx((0.5, 11.5), abs=0.1) and (wz0, wz1) == pytest.approx((-7.5, -0.5), abs=0.1)  # 0.5 m of eaves
    assert report.site.buildings[0].overhang == pytest.approx(0.5)


def test_a_building_is_a_closed_volume_with_its_roof_over_the_walls(tmp_path):
    report, obj = converted(tmp_path, Config(auto=True, images=False, origin="drawing", wall_height=3.0))
    (_, y0, _), (_, y1, _) = obj.bbox("E1_Muri")
    assert (y0, y1) == pytest.approx((0.0, 3.0), abs=0.01)  # the eaves at the wall height
    (_, fy0, _), (_, fy1, _) = obj.bbox("E1_Pavimento")
    assert (fy0, fy1) == pytest.approx((-0.2, 0.0), abs=0.01)  # the floor slab under them closes the volume
    (_, ry0, _), (_, ry1, _) = obj.bbox("E1_Tetto")
    assert ry1 == pytest.approx(3.0 + math.tan(math.radians(25.0)) * 4.0, abs=0.15)  # the hip roof of 8 m: 4 m of run, the default 25 degrees
    assert obj.winding_matches_normals("E1_Muri") and obj.open_edges("E1_Muri") == 0


def test_the_slope_arrows_say_how_each_plane_falls(tmp_path):
    report, _ = converted(tmp_path)
    planes = report.site.buildings[0].planes
    assert all(p.how == "freccia" for p in planes)
    falls = sorted((round(p.fall[0]), round(p.fall[1])) for p in planes)
    assert falls == [(-1, 0), (0, -1), (0, 1), (1, 0)]


def test_a_plane_without_an_arrow_falls_from_its_longest_edge_on_the_outline(tmp_path):
    report, _ = converted(tmp_path, arrows=False)
    hip = report.site.buildings[0]
    assert all(p.how == "gronda" for p in hip.planes)
    assert sorted((round(p.fall[0]), round(p.fall[1])) for p in hip.planes) == [(-1, 0), (0, -1), (0, 1), (1, 0)]
    assert any("senza freccia" in w for w in report.warnings)


# --- the old behaviour -----------------------------------------------------------------------

def test_without_site_mode_the_sheet_gives_the_old_error(tmp_path):
    path = site_sheet(tmp_path / "t.dxf", elevation=True)
    with pytest.raises(ConversionError, match="nessuna e' la pianta di un edificio"):
        convert(path, tmp_path / "t.obj", Config(auto=True, images=False, site="no"))


def test_a_sheet_with_no_roofs_keeps_the_old_error(tmp_path):
    path = site_sheet(tmp_path / "t.dxf", buildings=[], elevation=True)
    with pytest.raises(ConversionError, match="nessuna e' la pianta di un edificio"):
        convert(path, tmp_path / "t.obj", Config(auto=True, images=False))


def test_asking_for_the_site_mode_on_a_sheet_with_no_roofs_says_so(tmp_path):
    path = site_sheet(tmp_path / "t.dxf", buildings=[], elevation=True)
    with pytest.raises(ConversionError, match="--sito"):
        convert(path, tmp_path / "t.obj", Config(auto=True, images=False, site="yes"))
