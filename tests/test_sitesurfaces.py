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


def add_hatch(msp, x0, y0, x1, y1, color):
    h = msp.add_hatch(color=color)
    h.paths.add_polyline_path([(OX + x * 100, y * 100) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))], is_closed=True)
    return h


def add_word(msp, text, x, y):
    msp.add_text(text, height=40, dxfattribs={"insert": (OX + x * 100, y * 100)})


def site_sheet(path, turn=0, ground=True):
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
    for dx in (500, 560) if turn == 0 else (-300, -240):  # a drive from the road to the house wall, wherever the house stands now
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
    add_title(msp, "PLANIMETRIA GENERALE", OX + 100, -150)
    doc.saveas(path)
    return path


def converted(tmp_path, turn=0, **cfg):
    path = site_sheet(tmp_path / "s.dxf", turn=turn)
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
