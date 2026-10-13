"""The surfaces of the site: the ground round the building, read from the site plan that draws a copy of the plan. Synthetic
sheets (centimetres): the plan of a 14 x 9 m house, and a site plan of a 62 x 48 m lot with a copy of it and some ground."""

import json
import math

import pytest
from helpers import Obj
from ezdxf.math import Matrix44
from shapely.geometry import box
from test_views import add_plan, add_title, new_sheet

from dwg2c4d import Config, convert
from dwg2c4d.model import building_footprint
from dwg2c4d.sitesurfaces import faces_of

OX = 16000  # cm: the site plan is drawn this far east of the plan
LOT = [(0, 0), (6200, 0), (6200, 3100), (4300, 4800), (700, 4800), (0, 2600)]  # an irregular lot, 62 x 48 m (cm), from its south-west corner
HOUSE = (2500, 1500)  # where the copy of the plan stands in the lot (its south-west corner)
SHIFT = (OX / 100 + HOUSE[0] / 100, HOUSE[1] / 100)  # m: the plan's origin is this far from the lot's corner, in the site plan


def to_plan(x, y):
    """A point of the lot (m, from its corner) in the frame of the plan (the copy is not turned)."""
    return x - SHIFT[0] + OX / 100, y - SHIFT[1]


def add_room(msp, x0, y0, x1, y1, gap=0, layer="0", closed=True):
    """The four sides of a rectangle (m, from the corner of the lot) as separate lines, ``gap`` cm short at each end."""
    sides = [((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)), ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))]
    for (ax, ay), (bx, by) in sides:
        a, b = (OX + ax * 100, ay * 100), (OX + bx * 100, by * 100)
        length = math.hypot(b[0] - a[0], b[1] - a[1])
        ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
        msp.add_line((a[0] + ux * gap, a[1] + uy * gap), (b[0] - ux * gap, b[1] - uy * gap), dxfattribs={"layer": layer})


def add_pair(msp, x0, x1, y0, y1):
    """Two long lines (m, from the corner of the lot) at ``y0`` and ``y1``, from ``x0`` to ``x1``: a strip of curb or wall."""
    for y in (y0, y1):
        msp.add_line((OX + x0 * 100, y * 100), (OX + x1 * 100, y * 100))


def add_hatch(msp, x0, y0, x1, y1, color, layer="0"):
    h = msp.add_hatch(color=color, dxfattribs={"layer": layer})
    h.paths.add_polyline_path([(OX + x * 100, y * 100) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))], is_closed=True)
    return h


def add_word(msp, text, x, y):
    msp.add_text(text, height=40, dxfattribs={"insert": (OX + x * 100, y * 100)})


def site_sheet(path, turn=0, ground=True, bands=0, band_width=3.0, band_layers=("0",)):
    """The plan (view A) and the site plan (view B): the lot, a copy of the plan (turned ``turn`` quarters about its corner)
    and, when ``ground``: a lawn drawn with gaps at its corners and written PRATO, a paving that is a grey hatch, a road
    between two long lines written STRADA, a room with nothing to say what it is, and a tree (a small circle) in the lawn."""
    doc, msp = new_sheet()
    add_plan(msp, 0, 0)
    add_title(msp, "PIANTA PIANO TERRA", 100, -150)
    msp.add_lwpolyline([(x + OX, y) for x, y in LOT], close=True)
    hx, hy = OX + HOUSE[0], HOUSE[1]
    before = len(msp)
    add_plan(msp, hx, hy)
    turned = Matrix44.chain(Matrix44.translate(-hx, -hy, 0), Matrix44.z_rotate(math.radians(90 * turn)), Matrix44.translate(hx, hy, 0))
    for e in list(msp)[before:]:
        e.transform(turned)
    for dx in (500, 700) if turn == 0 else (-300, -100):  # a drive from the road to the house wall, wherever the house stands now
        msp.add_line((hx + dx, 900), (hx + dx, hy))
    if ground:
        add_room(msp, 8, 30, 19, 44, gap=10)  # the lawn: the corners are open by 10 cm
        add_word(msp, "PRATO", 11, 36)
        msp.add_circle((OX + 1300, 3700), 100)  # a tree: an object, not the edge of a surface
        add_room(msp, 44, 12, 58, 28)  # the paving: a hatch inside it, and its outline
        add_hatch(msp, 44, 12, 58, 28, 8)
        add_room(msp, 3, 12, 14, 24)  # a room of the lot that nothing names
        for y in (300, 900):  # the road: two long lines from side to side of the lot
            msp.add_line((OX, y), (OX + 6200, y))
        add_word(msp, "STRADA", 20, 6)
        add_pair(msp, 25, 37, 42.0, 42.2)  # a curb: two lines 20 cm apart, 12 m long
        add_pair(msp, 25, 35, 36.0, 36.45)  # a wall: 45 cm thick, 10 m long
        add_pair(msp, 40, 47, 38.0, 38.2)  # 7 m, 20 cm: a curb, with a height written beside it
        add_word(msp, "MURO DI CONTENIMENTO H= 120 cm", 43, 39)
        add_pair(msp, 50, 53, 5.0, 5.2)  # 3 m: the joint of a paving, no curb
    for k in range(bands):  # a flight of steps: green hatches ``band_width`` m long and 40 cm deep, 50 cm apart, in a row
        add_hatch(msp, 44, 32 + 0.5 * k, 44 + band_width, 32.4 + 0.5 * k, 3, band_layers[k % len(band_layers)])
    add_title(msp, "PLANIMETRIA GENERALE", OX + 100, -150)
    doc.saveas(path)
    return path


def converted(tmp_path, turn=0, bands=0, band_width=3.0, band_layers=("0",), **cfg):
    path = site_sheet(tmp_path / "s.dxf", turn=turn, bands=bands, band_width=band_width, band_layers=band_layers)
    config = dict(auto=True, site_surfaces=True, images=False, floors_to_outer_face=False)
    config.update(cfg)
    return convert(path, tmp_path / "s.obj", Config(**config))


def surface(report, kind, at):
    """The surface of this kind that holds the point ``at`` (a point of the lot, m)."""
    from shapely.geometry import Point

    x, y = to_plan(*at)
    found = [s for s in report.site_surfaces.surfaces if s.kind == kind and s.geom.contains(Point(x, y))]
    assert found, f"no {kind} at {at}: {[(s.id, s.geom.bounds) for s in report.site_surfaces.surfaces]}"
    return found[0]


# --- faces of a net of lines ---------------------------------------------------------------------

def test_a_region_the_lines_close_is_a_face_even_with_gaps_in_the_corners():
    from shapely.geometry import LineString

    lines = [LineString([(0, 0), (9.9, 0)]), LineString([(10, 0.1), (10, 9.9)]), LineString([(9.9, 10), (0.1, 10)]),
             LineString([(0, 9.9), (0, 0.1)])]
    faces = faces_of(lines, box(-5, -5, 15, 15))
    assert len(faces) == 1 and faces[0].area == pytest.approx(100, rel=0.03) and faces[0].contains(box(1, 1, 9, 9))


def test_a_region_the_lines_leave_open_is_the_outside_and_no_face():
    from shapely.geometry import LineString

    lines = [LineString([(0, 0), (10, 0)]), LineString([(10, 0), (10, 10)]), LineString([(10, 10), (0, 10)])]  # one side missing
    assert faces_of(lines, box(-5, -5, 15, 15)) == []


# --- the surfaces of a sheet ---------------------------------------------------------------------

def test_the_site_plan_that_draws_a_copy_of_the_plan_gives_surfaces_in_the_frame_of_the_plan(tmp_path):
    report = converted(tmp_path)
    site = report.site_surfaces
    assert site is not None and len(site.surfaces) >= 4
    lawn = surface(report, "prato", (13, 37))
    assert lawn.geom.bounds == pytest.approx((*to_plan(8, 30), *to_plan(19, 44)), abs=0.4)  # the plan's frame, not the site plan's
    assert lawn.source == "linee" and "PRATO" in lawn.why
    road = surface(report, "strada", (30, 6))
    assert road.geom.bounds[3] - road.geom.bounds[1] == pytest.approx(6.0, abs=0.4) and "STRADA" in road.why


def test_a_hatch_is_a_surface_of_its_own_and_the_net_does_not_cover_it_twice(tmp_path):
    report = converted(tmp_path)
    paving = surface(report, "pavimentazione", (50, 20))
    assert paving.source == "campitura" and paving.geom.area == pytest.approx(14 * 16, rel=0.02)
    here = [s for s in report.site_surfaces.surfaces if s.geom.intersection(paving.geom).area > 1.0]
    assert here == [paving]


def test_what_the_drawing_does_not_name_is_paving_and_the_report_says_so(tmp_path):
    report = converted(tmp_path)
    room = surface(report, "pavimentazione", (8, 18))
    assert room.why == "predefinita" and room.geom.area == pytest.approx(11 * 12, rel=0.03)
    assert any("trattate come pavimentazione" in w for w in report.warnings)


def test_a_tree_is_no_edge_of_a_surface_and_leaves_no_hole_in_the_lawn(tmp_path):
    report = converted(tmp_path)
    lawn = surface(report, "prato", (13, 37))
    assert not lawn.geom.interiors if hasattr(lawn.geom, "interiors") else all(not p.interiors for p in lawn.geom.geoms)
    assert lawn.geom.area == pytest.approx(11 * 14, rel=0.03)


def test_the_building_and_its_rooms_are_no_surface(tmp_path):
    report = converted(tmp_path)
    house = building_footprint(report.plan.solid_walls)
    for s in report.site_surfaces.surfaces:
        assert s.geom.intersection(house).area < 0.5, s.id


def test_a_copy_of_the_plan_turned_by_a_quarter_still_places_the_surfaces_on_the_plan(tmp_path):
    report = converted(tmp_path, turn=1)
    assert report.site_surfaces is not None
    house = building_footprint(report.plan.solid_walls)
    assert all(s.geom.intersection(house).area < 0.5 for s in report.site_surfaces.surfaces)
    kinds = {s.kind for s in report.site_surfaces.surfaces}
    assert {"prato", "strada", "pavimentazione"} <= kinds
    lawn = next(s for s in report.site_surfaces.surfaces if s.kind == "prato")
    assert lawn.geom.area == pytest.approx(11 * 14, rel=0.03)


def test_every_surface_is_a_group_of_its_own_with_the_material_of_its_kind(tmp_path):
    report = converted(tmp_path)
    obj = Obj(tmp_path / "s.obj")
    names = [s.id for s in report.site_surfaces.surfaces]
    assert len(set(names)) == len(names) and all(n.startswith("Sito_") for n in names)
    assert all(n in obj.groups for n in names)
    lawn = surface(report, "prato", (13, 37))
    assert lawn.id == "Sito_Prato_01"
    mtl = (tmp_path / "s.mtl").read_text()
    assert "newmtl Sito_Prato" in mtl and "newmtl Sito_Strada" in mtl and "newmtl Sito_Pavimentazione" in mtl
    text = (tmp_path / "s.obj").read_text().splitlines()
    at = text.index("o Sito_Prato_01")
    assert text[at + 1] == "usemtl Sito_Prato"


def test_surfaces_are_closed_slabs_at_the_level_of_their_kind(tmp_path):
    report = converted(tmp_path)
    obj = Obj(tmp_path / "s.obj")
    for kind, top in (("prato", -0.05), ("pavimentazione", 0.0), ("strada", -0.02)):
        s = surface(report, kind, {"prato": (13, 37), "pavimentazione": (50, 20), "strada": (30, 6)}[kind])
        lo, hi = obj.bbox(s.id)
        assert hi[1] == pytest.approx(top, abs=1e-6) and lo[1] == pytest.approx(top - 0.20, abs=1e-6)
        assert obj.open_edges(s.id) == 0 and obj.winding_matches_normals(s.id)


def test_the_json_for_cinema_4d_puts_the_surfaces_in_a_null_of_their_own(tmp_path):
    report = converted(tmp_path, c4d_json=True)
    data = json.loads((tmp_path / "s_model.json").read_text())
    objects = {o["name"]: o for o in data["objects"]}
    assert objects["Sito_Prato_01"]["group"] == "Superfici_sito" and objects["Sito_Prato_01"]["material"] == "site_lawn"
    assert objects[surface(report, "strada", (30, 6)).id]["material"] == "site_road"
    assert {"site_lawn", "site_road", "site_paving"} <= set(data["materials"])
    assert any(g["name"] == "Superfici_sito" for g in data["groups"])


def test_the_option_that_turns_them_off_leaves_the_model_as_it_was(tmp_path):
    report = converted(tmp_path, site_surfaces=False)
    assert report.site_surfaces is None
    assert not any(g.startswith("Sito_") for g in Obj(tmp_path / "s.obj").groups)
    assert not any("Superfici del sito" in w for w in report.warnings)


def test_a_sheet_without_a_site_plan_gets_no_surfaces(tmp_path):
    doc, msp = new_sheet()
    add_plan(msp, 0, 0)
    add_title(msp, "PIANTA PIANO TERRA", 100, -150)
    doc.saveas(tmp_path / "p.dxf")
    report = convert(tmp_path / "p.dxf", tmp_path / "p.obj", Config(auto=True, site_surfaces=True, images=False))
    assert report.site_surfaces is None and not any("Superfici del sito" in w for w in report.warnings)


def test_a_failure_in_the_surfaces_is_never_the_end_of_the_conversion(tmp_path, monkeypatch):
    import dwg2c4d.sitesurfaces as ss

    def boom(*args, **kwargs):
        raise RuntimeError("che sfortuna")

    monkeypatch.setattr(ss, "make_surfaces", boom)
    report = converted(tmp_path)
    assert report.site_surfaces is None and any("Superfici del sito non costruite" in w and "che sfortuna" in w for w in report.warnings)
    assert report.wall_pieces > 0


def test_the_report_line_counts_the_surfaces_by_kind(tmp_path):
    report = converted(tmp_path)
    from dwg2c4d.qa import summary_lines

    line = next(l for l in summary_lines(report) if "Superfici del sito" in l)
    assert "superfici dal disegno del sito" in line and "prato" in line and "strada" in line


# --- the curbs and the walls ----------------------------------------------------------------------

def wall_at(report, x, y):
    from shapely.geometry import Point

    px, py = to_plan(x, y)
    found = [w for w in report.site_surfaces.walls if w.geom.contains(Point(px, py))]
    assert found, f"no curb or wall at {(x, y)}: {[(w.id, w.geom.bounds) for w in report.site_surfaces.walls]}"
    return found[0]


def test_two_long_lines_a_few_centimetres_apart_are_a_curb_and_a_thick_pair_is_a_wall(tmp_path):
    report = converted(tmp_path)
    curb = wall_at(report, 31, 42.1)
    assert curb.kind == "cordolo" and curb.id.startswith("Sito_Cordolo_") and curb.height == pytest.approx(0.15)
    assert curb.axis.length == pytest.approx(12.0, abs=0.3) and curb.thickness == pytest.approx(0.2, abs=0.03)
    wall = wall_at(report, 30, 36.2)
    assert wall.kind == "muro" and wall.id == "Sito_Muro_01" and wall.height == pytest.approx(0.5) and "spessore 45 cm" in wall.why


def test_a_height_written_beside_a_strip_makes_it_a_wall_of_that_height(tmp_path):
    report = converted(tmp_path)
    wall = wall_at(report, 43, 38.1)
    assert wall.kind == "muro" and wall.height == pytest.approx(1.2) and wall.height_source == "testo"
    assert "H= 120 cm" in wall.why


def test_a_short_pair_of_lines_and_the_walls_of_the_house_are_no_curbs(tmp_path):
    report = converted(tmp_path)
    from shapely.geometry import Point

    px, py = to_plan(51.5, 5.1)
    assert not any(w.geom.contains(Point(px, py)) for w in report.site_surfaces.walls)  # 3 m only
    house = building_footprint(report.plan.solid_walls)
    assert all(w.geom.intersection(house).area < 0.3 for w in report.site_surfaces.walls)
    assert len(report.site_surfaces.walls) == 3


def test_the_heights_the_user_gives_to_the_low_walls_are_the_ones_of_the_site_walls(tmp_path):
    report = converted(tmp_path, low_wall_height=0.8, curb_height=0.1)
    assert wall_at(report, 30, 36.2).height == pytest.approx(0.8) and wall_at(report, 31, 42.1).height == pytest.approx(0.1)


def test_curbs_and_walls_are_closed_slabs_of_their_own_from_zero_to_their_height(tmp_path):
    report = converted(tmp_path)
    obj = Obj(tmp_path / "s.obj")
    for w in report.site_surfaces.walls:
        lo, hi = obj.bbox(w.id)
        assert lo[1] == pytest.approx(0.0, abs=1e-6) and hi[1] == pytest.approx(w.height, abs=1e-6), w.id
        assert obj.open_edges(w.id) == 0 and obj.winding_matches_normals(w.id)
    mtl = (tmp_path / "s.mtl").read_text()
    assert "newmtl Sito_Muro" in mtl and "newmtl Sito_Cordolo" in mtl


def test_the_json_for_cinema_4d_has_the_walls_and_curbs_in_the_null_of_the_site(tmp_path):
    report = converted(tmp_path, c4d_json=True)
    objects = {o["name"]: o for o in json.loads((tmp_path / "s_model.json").read_text())["objects"]}
    assert objects["Sito_Muro_01"]["material"] == "site_wall" and objects["Sito_Muro_01"]["group"] == "Superfici_sito"
    assert objects[wall_at(report, 31, 42.1).id]["material"] == "site_curb"


def test_the_report_counts_the_walls_and_the_curbs_and_says_what_it_guessed(tmp_path):
    report = converted(tmp_path)
    from dwg2c4d.qa import summary_lines

    line = next(l for l in summary_lines(report) if "Muri del sito" in l)
    assert "2 muri" in line and "1 cordoli" in line
    assert any("Muri del sito:" in w and "Sito_Cordolo_NN" in w and "--altezza-muretti" in w for w in report.warnings)


def test_a_wall_is_as_high_as_the_ground_is_higher_on_one_side_of_it_than_on_the_other():
    import numpy as np
    from shapely.geometry import LineString, Polygon

    from dwg2c4d.mesh import Mesh
    from dwg2c4d.sitecurbs import SiteWall, add_site_walls
    from dwg2c4d.terrain import Terrain

    xs, ys = np.meshgrid(np.arange(-5.0, 16.0), np.arange(-5.0, 16.0))
    ground = Terrain(-5.0, -5.0, 1.0, 0.5 * xs, 0.0, -1.0, Polygon(), Polygon())  # rises 0.5 m per metre eastwards
    axis = LineString([(5.0, 0.0), (5.0, 10.0)])  # a wall that runs north: the ground differs by 1.0 m across it
    free = SiteWall("Sito_Muro_01", "muro", axis.buffer(0.2, cap_style="flat"), axis, 0.4, "0", 0.5)
    written = SiteWall("Sito_Muro_02", "muro", axis.buffer(0.2, cap_style="flat"), axis, 0.4, "0", 0.9, "testo")
    add_site_walls(Mesh(), [free, written], ground)
    assert free.height == pytest.approx(1.0) and free.height_source == "dislivello"
    assert written.height == pytest.approx(0.9) and written.height_source == "testo"  # what is written beats the ground
    flat = SiteWall("Sito_Muro_03", "muro", axis.buffer(0.2, cap_style="flat"), axis, 0.4, "0", 0.5)
    add_site_walls(Mesh(), [flat], Terrain(-5.0, -5.0, 1.0, 0.0 * xs, 0.0, -1.0, Polygon(), Polygon()))
    assert flat.height == 0.5 and flat.height_source == "predefinita"  # no step: the default


def test_the_terrain_clipping_falls_back_to_the_plain_intersection_when_geos_gives_up(monkeypatch):
    import shapely
    from shapely.geometry import box

    from dwg2c4d import terrainmesh

    def give_up(*args, **kwargs):
        raise shapely.errors.GEOSException("Invalid number of points in LinearRing found 3 - must be 0 or >= 4")

    monkeypatch.setattr(terrainmesh.shapely, "clip_by_rect", give_up)
    got = terrainmesh._clipped(box(0, 0, 4, 4), (1, 1, 6, 3))
    assert got.equals(box(1, 1, 4, 3))


def test_a_flat_model_has_a_slab_of_ground_under_the_surfaces_that_closes_the_gaps_between_them(tmp_path):
    report = converted(tmp_path, c4d_json=True)
    obj = Obj(tmp_path / "s.obj")
    lo, hi = obj.bbox("Sito_Terreno_00")
    assert hi[1] == pytest.approx(-0.20) and lo[1] == pytest.approx(-0.20 - 1.0)  # below the lowest surface, the terrain's thickness
    base = report.site_surfaces.base
    assert all(base.buffer(0.01).contains(s.geom) for s in report.site_surfaces.surfaces)
    assert obj.open_edges("Sito_Terreno_00") == 0 and obj.winding_matches_normals("Sito_Terreno_00")
    objects = {o["name"]: o for o in json.loads((tmp_path / "s_model.json").read_text())["objects"]}
    assert objects["Sito_Terreno_00"]["material"] == "site_soil"


# --- steps drawn as hatched bands ---------------------------------------------------------------

def test_a_row_of_long_thin_hatches_is_a_flight_of_steps_not_a_lawn(tmp_path):
    report = converted(tmp_path, bands=5)
    site = report.site_surfaces
    assert len(site.stairs) == 1 and site.stairs[0].id == "Sito_Gradinata_01" and site.stairs[0].count == 5
    assert not any(s.source == "campitura" and s.geom.intersection(site.stairs[0].geom).area > 0.5 for s in site.surfaces)  # no lawn from their green
    foot, top = site.stairs[0].bands[0].centroid, site.stairs[0].bands[-1].centroid
    assert foot.y > top.y  # the top is the end nearer to the house (the bands run away from it, towards the north)
    assert any("Gradinate:" in w and "Sito_Gradinata_NN" in w for w in report.warnings)


def test_each_band_is_one_riser_higher_than_the_one_before_and_a_prism_of_its_own(tmp_path):
    report = converted(tmp_path, bands=5)
    obj = Obj(tmp_path / "s.obj")
    lo, hi = obj.bbox("Sito_Gradinata_01")
    assert hi[1] == pytest.approx(5 * 0.17) and lo[1] == pytest.approx(-0.20)
    heights = sorted({round(obj.v[i][1], 4) for f, _ in obj.faces("Sito_Gradinata_01") for i in f if obj.v[i][1] > 0})
    assert heights == [round(0.17 * k, 4) for k in range(1, 6)]
    assert obj.open_edges("Sito_Gradinata_01") == 0 and obj.winding_matches_normals("Sito_Gradinata_01")
    assert "newmtl Sito_Gradinata" in (tmp_path / "s.mtl").read_text()


def test_two_bands_are_no_flight_and_squares_in_a_row_are_no_steps(tmp_path):
    assert converted(tmp_path, bands=2).site_surfaces.stairs == []  # a row of two: a deck, a table
    assert converted(tmp_path, bands=5, band_width=0.5).site_surfaces.stairs == []  # 50 x 40 cm: tiles, not treads


def test_hatches_that_are_not_all_alike_are_no_flight_the_joints_of_a_paving_are_not_steps(tmp_path):
    assert converted(tmp_path, bands=6, band_layers=("0", "GIUNTI")).site_surfaces.stairs == []  # two layers, one band in two: no row


def test_the_json_has_the_flight_in_the_null_of_the_site(tmp_path):
    converted(tmp_path, bands=5, c4d_json=True)
    objects = {o["name"]: o for o in json.loads((tmp_path / "s_model.json").read_text())["objects"]}
    assert objects["Sito_Gradinata_01"]["group"] == "Superfici_sito" and objects["Sito_Gradinata_01"]["material"] == "site_steps"


# --- the terrain under the surfaces ------------------------------------------------------------------

WIDE_LOT = [(0, 0), (12000, 0), (12000, 3100), (10000, 4800), (700, 4800), (0, 2600)]  # a lot 120 m wide: far from the house


def terrain_sheet(path):
    """The plan and the site plan of a wide lot with the contour lines of a slope (every 20 cm, rising 5 % eastwards) drawn in it
    and a lawn 55 m east of the house, written PRATO."""
    doc, msp = new_sheet()
    doc.layers.add("CURVE-LIV")
    add_plan(msp, 0, 0)
    add_title(msp, "PIANTA PIANO TERRA", 100, -150)
    msp.add_lwpolyline([(x + OX, y) for x, y in WIDE_LOT], close=True)
    hx, hy = OX + HOUSE[0], HOUSE[1]
    add_plan(msp, hx, hy)
    for dx in (500, 700):
        msp.add_line((hx + dx, 0), (hx + dx, hy))
    for k in range(-14, 24):
        c = 0.2 * k  # the height above the floor of this contour line: x = 25 m + c / 0.05
        x = OX + HOUSE[0] + c / 0.05 * 100
        if OX <= x <= OX + 12000:
            msp.add_lwpolyline([(x, 0), (x, 4800)], dxfattribs={"layer": "CURVE-LIV", "elevation": round((100.0 + c) * 100.0)})
    add_room(msp, 80, 20, 95, 40)
    add_word(msp, "PRATO", 85, 30)
    msp.add_text("+0.00 (100.00)", height=20, dxfattribs={"insert": (OX + 1600, 1000)})
    add_title(msp, "PLANIMETRIA GENERALE", OX + 100, -150)
    doc.saveas(path)
    return path


def test_the_terrain_reaches_the_surfaces_however_far_they_are_from_the_house(tmp_path):
    report = convert(terrain_sheet(tmp_path / "t.dxf"), tmp_path / "t.obj",
                     Config(auto=True, site_surfaces=True, terrain_auto=True, images=False, floors_to_outer_face=False))
    ground = report.plan.terrain
    assert ground is not None
    lawn = next(s for s in report.site_surfaces.surfaces if s.kind == "prato")
    x0, y0, x1, y1 = lawn.geom.bounds
    assert x1 - x0 == pytest.approx(15.0, abs=0.5) and x0 - report.plan.solid_walls.bounds[2] > 40.0
    wx0, wy0, wx1, wy1 = ground.window
    assert wx0 <= x0 and x1 <= wx1 and wy0 <= y0 and y1 <= wy1  # the grid of the terrain covers the lawn
    lo, hi = Obj(tmp_path / "t.obj").bbox(lawn.id)
    assert hi[0] - lo[0] == pytest.approx(15.0, abs=0.5) and hi[2] - lo[2] == pytest.approx(20.0, abs=0.5)  # none of it cut off


def test_the_contour_lines_of_the_site_plan_do_not_cut_the_surfaces_in_bands(tmp_path):
    report = convert(terrain_sheet(tmp_path / "t.dxf"), tmp_path / "t.obj",
                     Config(auto=True, site_surfaces=True, terrain_auto=True, images=False, floors_to_outer_face=False))
    lawn = next(s for s in report.site_surfaces.surfaces if s.kind == "prato")
    assert lawn.geom.area == pytest.approx(15 * 20, rel=0.03)  # one lawn, not a band between two contour lines
    assert report.site_surfaces.walls == []  # and two contour lines 4 m apart are no curb
    paths = [s for s in report.site_surfaces.surfaces if s.kind != "prato"]
    assert all(not s.geom.intersects(lawn.geom.buffer(-0.5)) for s in paths)


def test_a_surface_on_a_terrain_follows_the_ground_and_stands_a_fixed_distance_from_it(tmp_path):
    import numpy as np

    from dwg2c4d.terrainmesh import EMBED

    report = convert(terrain_sheet(tmp_path / "t.dxf"), tmp_path / "t.obj",
                     Config(auto=True, site_surfaces=True, terrain_auto=True, images=False, floors_to_outer_face=False))
    ground, obj = report.plan.terrain, Obj(tmp_path / "t.obj")
    lawn = next(s for s in report.site_surfaces.surfaces if s.kind == "prato")
    p = obj.v[sorted({i for f, _ in obj.faces(lawn.id) for i in f})]
    above = p[:, 1] - ground.height(p[:, 0] - report.origin_offset[0], -p[:, 2] - report.origin_offset[1])
    assert set(np.round(above, 3)) == {round(lawn.top - ground.level, 3), round(-EMBED, 3)}  # the top, and the underside in the ground
    assert p[:, 1].max() - p[:, 1].min() > 0.5  # it is not flat: the ground rises under it
    assert obj.open_edges(lawn.id) == 0 and obj.winding_matches_normals(lawn.id)
    assert "Sito_Terreno_00" not in obj.groups  # a terrain is the ground itself: no slab under the surfaces


def test_a_surface_farther_than_the_reach_from_the_house_is_left_out():
    from shapely.geometry import LineString, Polygon

    from dwg2c4d.config import LayerRules
    from dwg2c4d.sitesurfaces import REACH, SiteDrawing, make_surfaces

    drawing = SiteDrawing(box(-50, -50, 600, 100))
    for b in (box(20, 0, 40, 20), box(10 + REACH + 50, 0, 30 + REACH + 50, 20)):  # one near the house, one beyond the reach
        drawing.lines.append(("0", LineString(list(b.exterior.coords)), False))
    surfaces, _ = make_surfaces(drawing, box(0, 0, 10, 10), Polygon(), LayerRules())
    assert len(surfaces) == 1 and surfaces[0].geom.bounds == pytest.approx((20, 0, 40, 20), abs=0.4)


def test_the_picture_to_check_the_site_is_written_with_the_images_and_only_then(tmp_path):
    report = converted(tmp_path, images=True)
    assert report.site_overlay_path == tmp_path / "s_controllo_sito.png"
    assert report.site_overlay_path.read_bytes()[:4] == b"\x89PNG" and report.site_overlay_path.stat().st_size > 5000
    assert "s_controllo_sito.png" in report.report_path.read_text(encoding="utf-8")
    other = tmp_path / "x"
    other.mkdir()
    assert converted(other).site_overlay_path is None  # no images: no picture
