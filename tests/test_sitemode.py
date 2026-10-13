"""Site mode: a sheet with no plan of a building but a site plan that draws the roofs of its buildings (a hatch of tiles on
each slope, an arrow for each fall) becomes a model of volumes with their roofs; synthetic sheets, centimetres."""

import math
from types import SimpleNamespace

import ezdxf
import numpy as np
import pytest
from shapely.geometry import Polygon

from dwg2c4d import Config, convert
from dwg2c4d import siteelev as se
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


def add_roof_band(msp, x, floor, w, eave, ridge):
    """The roof of an elevation: a hatch of tiles from the eaves to the ridge, ``w`` cm wide at the eaves."""
    hatch = msp.add_hatch(dxfattribs={"layer": "Coppi Chiaro"})
    hatch.dxf.pattern_name = "COPPI01"
    hatch.dxf.solid_fill = 0
    hatch.paths.add_polyline_path([(x, floor + eave), (x + w, floor + eave), (x + w - 300, floor + ridge), (x + 300, floor + ridge)])


def site_sheet(path, buildings=None, arrows=True, elevation=False, roofs=(), pendenza=None, title="PLANIMETRIA GENERALE", lines=True):
    """A site plan (a hip roof 12 x 8 m and a gable roof 6 x 5 m, 15 m apart) and, if asked, elevations of the sheet: one with
    no roof (``elevation``), or one for each of ``roofs``: (the width of the roof cm, its eaves cm, its ridge cm), seen above the
    floor of the elevation (+0,00). ``pendenza``: a note on the slope written in the site plan."""
    doc, msp = new_sheet()
    for name in ("Coppi Scuro", "Coppi Chiaro", "E", "CONFINE", "CURVE-LIV", "TESTI"):
        doc.layers.add(name)
    if lines:
        add_site_lines(msp)
    for points, normal in (buildings if buildings is not None else hip_planes(0, 0) + gable_planes(1800, 100)):
        add_plane(msp, points, normal if arrows else None)
    if title:
        add_title(msp, title, -1500, -1600)
    if pendenza:
        msp.add_text(pendenza, height=25, dxfattribs={"layer": "TESTI", "insert": (-1400, 2600)})
    for k, band in enumerate(roofs if roofs else ((None,) if elevation else ())):
        x = 9000 + k * 2800
        add_elevation(msp, x, 0, w=1200, h=600)
        if band is not None:
            add_roof_band(msp, x, 0, *band)
        add_title(msp, f"PROSPETTO {k + 1}", x, -200)
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


# --- the heights from the elevations -----------------------------------------------------------

TAN = math.tan(math.radians(PITCH))
HIP_ROOFS = ((1200, 600, 600 + 400 * TAN * 100 / 100), (800, 600, 600 + 400 * TAN * 100 / 100))  # the hip roof 12 x 8 m seen from two sides: eaves 6 m, 4 m of run


def test_the_eaves_and_the_pitch_come_from_the_roofs_the_elevations_draw(tmp_path):
    report, obj = converted(tmp_path, Config(auto=True, images=False, origin="drawing"), roofs=HIP_ROOFS)
    hip, gable = report.site.buildings
    (body,) = hip.bodies
    assert (body.eave_height, body.eave_source) == (pytest.approx(6.0, abs=0.02), "prospetto")
    assert body.measured_pitch == pytest.approx(PITCH, abs=1.0) and body.pitch == pytest.approx(PITCH, abs=1.0)
    assert (report.site.pitch_source, report.site.pitch) == ("prospetto", pytest.approx(PITCH, abs=1.0))
    (_, y0, _), (_, y1, _) = obj.bbox("E1_Muri")
    assert (y0, y1) == pytest.approx((0.0, 6.0), abs=0.02)
    (_, _, _), (_, ry1, _) = obj.bbox("E1_Tetto")
    assert ry1 == pytest.approx(6.0 + 4.0 * TAN, abs=0.1)  # the ridge is where the elevations draw it
    assert any("misurati sui prospetti" in w and "PROSPETTO 1" in w for w in report.warnings)


def test_a_building_no_elevation_shows_keeps_the_default_and_the_report_says_so(tmp_path):
    report, obj = converted(tmp_path, roofs=HIP_ROOFS)
    gable = report.site.buildings[1]
    assert [(b.eave_height, b.eave_source) for b in gable.bodies] == [(3.0, "predefinita")]  # 30 m2: one storey of 3 m
    assert any("Altezze di gronda presunte per edificio 2" in w for w in report.warnings)
    assert report.site.buildings[0].bodies[0].eave_source == "prospetto"


def test_a_roof_one_elevation_draws_is_not_taken_for_the_building_of_the_same_length(tmp_path):
    only_one = ((1200, 600, 600 + 400 * TAN),)  # the 12 m side of the hip roof, seen once: the roof of the elevation could be any 12 m roof
    report, _ = converted(tmp_path, roofs=only_one, buildings=hip_planes(0, 0) + gable_planes(1800, 100, w=1200, d=500))
    assert all(b.eave_source == "predefinita" for building in report.site.buildings for b in building.bodies)


def test_the_pitch_written_in_the_sheet_beats_the_one_the_elevations_give(tmp_path):
    report, _ = converted(tmp_path, roofs=HIP_ROOFS, pendenza="Pendenza 30 %")
    assert report.site.pitch_source == "scritta" and report.site.pitch == pytest.approx(math.degrees(math.atan(0.3)), abs=0.05)
    assert report.site.buildings[0].bodies[0].pitch == pytest.approx(16.7, abs=0.1)
    assert report.site.buildings[0].bodies[0].eave_height == pytest.approx(6.0, abs=0.02)  # the heights are still the elevations'


def test_the_pitch_and_the_height_the_user_gives_beat_the_elevations(tmp_path):
    report, obj = converted(tmp_path, Config(auto=True, images=False, origin="drawing", roof_pitch=35.0, wall_height=4.0), roofs=HIP_ROOFS)
    body = report.site.buildings[0].bodies[0]
    assert (body.pitch, body.eave_height, body.eave_source) == (35.0, 4.0, "indicata") and report.site.pitch_source == "indicata"
    (_, _, _), (_, ry1, _) = obj.bbox("E1_Tetto")
    assert ry1 == pytest.approx(4.0 + 4.0 * math.tan(math.radians(35.0)), abs=0.1)


# --- a lean-to no elevation measures leans on the eaves of its neighbour -----------------------------

LEAN_TO = ([(0, -250), (1200, -250), (1200, 0), (0, 0)], (0, -1))  # 12 x 2.5 m against the south eaves of the hip roof, falling the same way


def test_a_lean_to_no_elevation_measures_meets_the_wall_under_the_eaves_it_leans_on(tmp_path):
    report, obj = converted(tmp_path, Config(auto=True, images=False, origin="drawing"), buildings=hip_planes(0, 0) + [LEAN_TO])
    (building,) = report.site.buildings
    hip, lean = building.bodies
    assert (hip.eave_height, hip.eave_source) == (6.0, "predefinita")  # 96 m2: two storeys
    expected = 6.0 - sm.LEAN_BELOW - 2.5 * math.tan(math.radians(25.0))  # 30 cm under the hip's eaves, less the rise of its own roof
    assert lean.eave_height == pytest.approx(expected, abs=0.05) and lean.eave_source == "addossato"
    assert any(w.startswith("Tetti addossati: edificio 1, corpo 2: gronda a 4.5") for w in report.warnings)
    (_, y0, _), (_, y1, _) = obj.bbox("E1_Tetto")
    assert y1 == pytest.approx(6.0 + 4.0 * math.tan(math.radians(25.0)), abs=0.15)  # the ridge of the hip is where it was


def test_a_lean_to_is_never_lower_at_its_eaves_than_the_minimum(tmp_path):
    small = ([(0, -250), (600, -250), (600, 0), (0, 0)], (0, -1))  # against the eaves of a gable roof of one storey (30 m2)
    report, _ = converted(tmp_path, buildings=gable_planes(0, 0, w=600, d=500) + [small])
    gable, lean = report.site.buildings[0].bodies
    assert gable.eave_height == 3.0
    assert lean.eave_source == "addossato" and lean.eave_height == pytest.approx(sm.LEAN_MIN)  # 3 m - 30 cm - 1.2 m is 1.5 m: too low for a door


def test_a_roof_that_touches_no_eave_keeps_the_height_of_its_storeys(tmp_path):
    apart = ([(0, -650), (1200, -650), (1200, -300), (0, -300)], (0, -1))  # 3 m from the eaves: no lean-to
    report, _ = converted(tmp_path, buildings=hip_planes(0, 0) + [apart])
    assert all(b.eave_source == "predefinita" for building in report.site.buildings for b in building.bodies)


def test_the_height_the_user_gives_is_the_one_of_a_lean_to_too(tmp_path):
    report, _ = converted(tmp_path, Config(auto=True, images=False, wall_height=4.0), buildings=hip_planes(0, 0) + [LEAN_TO])
    assert [(b.eave_height, b.eave_source) for b in report.site.buildings[0].bodies] == [(4.0, "indicata")] * 2


def test_a_lean_to_a_measured_roof_has_over_it_takes_its_eaves_from_the_measure(tmp_path):
    report, _ = converted(tmp_path, buildings=hip_planes(0, 0) + [LEAN_TO], roofs=HIP_ROOFS)
    hip, lean = report.site.buildings[0].bodies
    assert (hip.eave_height, hip.eave_source) == (pytest.approx(6.0, abs=0.02), "prospetto")
    assert lean.eave_source == "addossato" and lean.eave_height == pytest.approx(6.0 - sm.LEAN_BELOW - 2.5 * TAN, abs=0.1)


# --- the doors and windows of the elevations, put on the facades they show ----------------------

def two_bodies():
    """A hip roof 12 x 8 m (A) with a gable roof 10 x 8 m (B) against its west side, in metres: one building, two bodies."""
    shrink = lambda pts: Polygon(pts).buffer(-GAP / 200, join_style="mitre")  # noqa: E731
    hatches = [shrink([(x / 100, y / 100) for x, y in pts]) for pts, _ in hip_planes(0, 0)]
    hatches += [shrink([(x / 100, y / 100) for x, y in pts]) for pts, _ in gable_planes(-1000, 0, w=1000, d=800)]
    (building,) = sm.find_buildings(hatches, np.zeros((0, 4)), 0.5)
    assert len(building.bodies) == 2
    a, b = sorted(building.bodies, key=lambda body: -sm.body_outline(building, body).area)
    a.eave_height, b.eave_height = 6.0, 3.0
    for body in (a, b):
        body.pitch = 20.0
    return building, a, b


def shown(building, a, b, b_at):
    """The matched list for one elevation that draws A (12 m, x 100..112) and B (10 m) at ``b_at``: "left" or "right" of it."""
    band_b = se.RoofBand(1, 3.0, 4.0, 86.0, 96.0) if b_at == "left" else se.RoofBand(1, 3.0, 4.0, 116.0, 126.0)
    band_a = se.RoofBand(1, 6.0, 7.5, 100.0, 112.6)  # (the elevation draws the roof a little longer than the plan: the symbols go in proportion)
    return [(building, a, se.RoofLevel([band_a]), [band_a]), (building, b, se.RoofLevel([band_b]), [band_b])]


@pytest.mark.parametrize("b_at, side_y", [("left", 0.0), ("right", 8.0)])
def test_the_facade_is_the_side_from_which_the_neighbouring_roof_is_where_the_elevation_draws_it(b_at, side_y):
    building, a, b = two_bodies()
    facades, notes = se.find_facades(shown(building, a, b, b_at))
    on_a = [f for f in facades if f.body is a]
    assert len(on_a) == 1 and not notes
    assert on_a[0].side.a[1] == pytest.approx(side_y, abs=0.3) and on_a[0].side.b[1] == pytest.approx(side_y, abs=0.3)
    (_, ny), (ux, _) = on_a[0].side.normal, on_a[0].side.direction
    assert (round(ny), round(ux)) == ((-1, 1) if b_at == "left" else (1, -1))  # looks out to the south and runs west to east, or the reverse


def test_a_facade_two_sides_fit_equally_is_left_out_and_told():
    building, a, b = two_bodies()
    only_a = [m for m in shown(building, a, b, "left") if m[1] is a]
    facades, notes = se.find_facades(only_a)
    assert facades == [] and len(notes) == 1 and "non ho messo le sue aperture" in notes[0]


def read_with(*symbols, floor=0.0):
    return [se.ElevationRead(SimpleNamespace(id=1), SimpleNamespace(symbols=list(symbols)), floor)]


def window(x0, x1, y0, y1, kind="window", arched=False):
    return SimpleNamespace(kind=kind, x0=x0, x1=x1, y0=y0, y1=y1, arched=arched)


def test_a_symbol_goes_on_the_side_of_its_facade_as_far_along_it_as_the_elevation_draws_it_in_the_middle_of_the_wall():
    building, a, b = two_bodies()
    facades, _ = se.find_facades(shown(building, a, b, "left"))  # the south side, x from 0 to 12
    reads = read_with(window(103.0, 103.9, 2.5, 3.8), window(109.0, 110.0, 0.0, 2.1, kind="door"))
    openings, notes = se.place_openings(facades, reads, Config(), {1: "PROSPETTO 1"})
    win, door = sorted(openings, key=lambda o: o.center[0])
    assert (win.kind, door.kind) == ("window", "door")
    (side,) = [f.side for f in facades if f.body is a]
    along = lambda x: side.a[0] + (x - 100.0) / 12.6 * side.length  # noqa: E731  (the band of the elevation is 12.6 m, the side of the roof 12.1)
    assert win.center == pytest.approx((along(103.45), 0.04 + 0.5 + 0.2), abs=0.02) and win.width == pytest.approx(0.9)  # 0.5 m of eaves and half of a 0.4 m wall in
    assert (win.z0, win.z1) == pytest.approx((2.5, 3.8)) and (door.z0, door.z1) == pytest.approx((0.0, 2.1))
    assert door.center[0] == pytest.approx(along(109.5), abs=0.02) and len(door.leaves) == 1
    assert all(o.building == building.id and o.thickness == pytest.approx(0.4) and o.axis == pytest.approx((1.0, 0.0)) for o in openings)
    assert any("Aperture posizionate dai prospetti, senza pianta" in n for n in notes)


def test_a_symbol_under_no_roof_of_a_facade_or_above_the_eaves_is_not_put():
    building, a, b = two_bodies()
    facades, _ = se.find_facades(shown(building, a, b, "left"))
    reads = read_with(window(103.0, 104.0, 6.5, 7.4), window(150.0, 151.0, 2.0, 3.0))  # over the eaves of the farm; far from every roof
    openings, notes = se.place_openings(facades, reads, Config(), {})
    assert openings == [] and any("non stanno sotto nessun tetto" in n for n in notes)


def test_an_arched_window_is_cut_square_at_the_springing_line_and_a_door_wider_than_1_3_m_has_two_leaves():
    building, a, b = two_bodies()
    facades, _ = se.find_facades(shown(building, a, b, "left"))
    reads = read_with(window(103.0, 104.0, 2.0, 3.6, arched=True), window(106.0, 108.0, 0.0, 2.4, kind="door"))
    openings, _ = se.place_openings(facades, reads, Config(), {})
    arch, door = sorted(openings, key=lambda o: o.center[0])
    assert arch.z1 == pytest.approx(3.6 - 0.5) and len(door.leaves) == 2


def test_a_door_below_the_ground_floor_is_cut_at_zero_and_one_that_stays_no_higher_than_40_cm_is_not_put():
    building, a, b = two_bodies()
    facades, _ = se.find_facades(shown(building, a, b, "left"))
    reads = read_with(window(103.0, 104.2, -2.25, 0.45, kind="door"), window(106.0, 107.0, -1.0, 0.2, kind="door"))
    openings, notes = se.place_openings(facades, reads, Config(), {})
    (door,) = openings
    assert (door.z0, door.z1) == pytest.approx((0.0, 0.45)) and any("restano alte meno di 40 cm" in n for n in notes)


def test_the_openings_of_the_elevations_are_cut_in_the_walls_of_the_site_and_get_frames():
    building, a, b = two_bodies()
    cfg = Config(auto=True, images=False, fixtures="detailed", fixtures_per_opening=True)
    facades, _ = se.find_facades(shown(building, a, b, "left"))
    openings, _ = se.place_openings(facades, read_with(window(103.0, 103.9, 2.5, 3.8)), cfg, {})
    openings[0].id = "F01"
    with_hole = sm.building_mesh(building, "prospetto", cfg, openings)
    solid = sm.building_mesh(building, "prospetto", cfg, [])
    assert "Telai_F01" in with_hole.groups and "Vetri_F01" in with_hole.groups and "Telai_F01" not in solid.groups
    assert with_hole.face_count > solid.face_count  # the wall has a hole in it


def test_an_opening_of_a_high_volume_does_not_lift_the_walls_of_a_lower_one():
    building, a, b = two_bodies()
    cfg = Config(auto=True, images=False)
    facades, _ = se.find_facades(shown(building, a, b, "left"))
    openings, _ = se.place_openings(facades, read_with(window(103.0, 103.9, 4.0, 5.5)), cfg, {})  # high up in the 6 m volume
    assert len(openings) == 1
    mesh = sm.building_mesh(building, "prospetto", cfg, openings)
    west = [mesh.vertices[i] for ids, _ in mesh.groups["Muri"] for i in ids if mesh.vertices[i][0] < -1.0]  # the walls of the 3 m volume
    assert max(z for _, _, z in west) == pytest.approx(3.0)


def test_a_skylight_is_covered_by_its_slope_and_a_courtyard_stays_a_hole():
    def planes(hole):
        hip = [Polygon([(x / 100, y / 100) for x, y in pts]).buffer(-GAP / 200, join_style="mitre") for pts, _ in hip_planes(0, 0, w=2400, d=1600)]
        return [p.difference(hole) if p.contains(hole.centroid) else p for p in hip]

    sky = sm.find_buildings(planes(Polygon([(10.0, 2.0), (11.0, 2.0), (11.0, 3.0), (10.0, 3.0)])), np.zeros((0, 4)), 0.5)[0]
    assert not sky.outline.interiors if sky.outline.geom_type == "Polygon" else True
    assert all(not p.interiors for p in sm.polygons_of(sky.footprint))
    assert sm.union(p.shape for p in sky.planes).area == pytest.approx(sky.outline.area, abs=0.5)  # the planes cover it all: no bare hole
    yard = sm.find_buildings(planes(Polygon([(8.0, 3.0), (16.0, 3.0), (16.0, 6.0), (8.0, 6.0)])), np.zeros((0, 4)), 0.5)[0]
    assert sum(len(p.interiors) for p in sm.polygons_of(yard.outline)) == 1  # 24 m2: a courtyard


def test_a_body_with_a_side_as_long_as_one_of_the_bands_does_not_own_a_roof_the_elevations_explain_better_of_another():
    shape = lambda pts: Polygon(pts).buffer(-GAP / 200, join_style="mitre")  # noqa: E731
    farm = shape([(0, 0), (12, 0), (12, 8), (5, 8), (5, 13), (0, 13)])  # sides 12, 8, 7, 5, 5, 13
    shed = shape([(100, 0), (112, 0), (112, 8), (100, 8)])  # 12 x 8: two of the three sides the elevations draw
    buildings = sm.find_buildings([farm, shed], np.zeros((0, 4)), 0.5)
    bands = [se.RoofBand(view, 6.0, 7.5, 0.0, length) for view, length in ((1, 12.0), (2, 8.0), (3, 5.0))]
    matched = se.match_levels(buildings, [se.RoofLevel(bands)])
    assert [b.hatch_area > 100 for b, _, _, _ in matched] == [True]  # only the farm, which explains all three
    assert [body.eave_source for b in buildings for body in b.bodies] == ["prospetto", ""]


def test_a_roof_one_elevation_draws_is_the_building_s_if_no_other_has_a_side_that_long(tmp_path):
    report, _ = converted(tmp_path, roofs=((1200, 600, 600 + 400 * TAN),))  # the 12 m side of the hip roof: the gable roof (6 x 5 m) has none
    hip, gable = report.site.buildings
    assert (hip.bodies[0].eave_source, round(hip.bodies[0].eave_height, 1)) == ("prospetto", 6.0)
    assert gable.bodies[0].eave_source == "predefinita"
