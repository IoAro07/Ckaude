"""Reading a drawing whose layers say nothing: the unit, the views of the sheet, the walls and the doors by their shape."""

import math

import ezdxf
import pytest
from shapely.geometry import LineString, Point, box
from shapely.ops import unary_union

from dwg2c4d import Config, convert
from dwg2c4d.autodetect import (Soup, _title_kind, analyze, candidate_layers, find_views, infer_unit, scan,
                                wall_metrics)
from dwg2c4d.cli import main
from dwg2c4d.config import LayerRules
from dwg2c4d.openings import Symbol, _is_swing
from dwg2c4d.reader import Prim
from dwg2c4d.walls import drop_specks, faces_from_pairs

import numpy as np


def messy_sheet(path, declared_units=4, elevation=True):
    """A 10 x 6 m plan in centimetres whose walls are pairs of parallel lines on the layer 'LINEE' (the same layer as
    the furniture, with open ends where the door and the window are), a door drawn as a swing arc and nothing else, a
    wardrobe door's arc in the middle of a room, and, 30 m below, an elevation. The header says millimetres."""
    doc = ezdxf.new("R2018", setup=True)
    doc.units = declared_units
    doc.layers.add("LINEE")
    doc.layers.add("TESTI")
    doc.layers.add("QUOTE")
    msp = doc.modelspace()
    at = {"layer": "LINEE"}

    def pair(a, b, c, d):  # two lines from (a, b) to (c, d), 30 cm apart, as a wall of the given direction
        if abs(b - d) < 1e-9:  # horizontal at y = b
            for y in (b, b + 30):
                msp.add_line((a, y), (c, y), dxfattribs=at)
        else:
            for x in (a, a + 30):
                msp.add_line((x, b), (x, d), dxfattribs=at)

    for x0, x1 in ((0, 200), (290, 500), (620, 1000)):  # south wall: a door gap (200-290), a window gap (500-620)
        pair(x0, 0, x1, 0)
    pair(0, 570, 1000, 570)
    pair(0, 0, 0, 600)
    pair(970, 0, 970, 600)
    pair(800, 30, 800, 300)  # a partition with a doorway above it
    # the door: the arc turns about the hinge (200, 30) from the shut leaf to the open one
    msp.add_arc((200, 30), 90, 0, 90, dxfattribs=at)
    msp.add_line((200, 30), (200, 120), dxfattribs=at)
    # furniture on the same layer: a sofa, a stool, a wardrobe whose door sweeps 58 cm in the middle of the room
    msp.add_lwpolyline([(100, 200), (300, 200), (300, 290), (100, 290)], close=True, dxfattribs=at)
    msp.add_lwpolyline([(600, 400), (640, 400), (640, 440), (600, 440)], close=True, dxfattribs=at)
    msp.add_arc((700, 300), 58, 0, 90, dxfattribs=at)
    msp.add_text("PIANTA PIANO TERRA", height=20, dxfattribs={"layer": "TESTI", "insert": (350, -120)})
    for p1, p2 in (((0, 0), (200, 0)), ((290, 0), (500, 0)), ((620, 0), (1000, 0)), ((0, 0), (0, 300)),
                   ((0, 300), (0, 600)), ((970, 0), (970, 300)), ((970, 300), (970, 600))):
        msp.add_linear_dim(base=(-100, -60) if p1[1] == p2[1] else (-60, 0), p1=p1, p2=p2,
                           dxfattribs={"layer": "QUOTE"}).render()
    if elevation:
        for pts in ([(0, -3500), (1000, -3500), (1000, -3000), (0, -3000)],
                    [(200, -3500), (290, -3500), (290, -3290), (200, -3290)],
                    [(500, -3400), (620, -3400), (620, -3260), (500, -3260)]):
            msp.add_lwpolyline(pts, close=True, dxfattribs=at)
        msp.add_text("PROSPETTO SUD", height=20, dxfattribs={"layer": "TESTI", "insert": (350, -3620)})
    doc.saveas(path)
    return path


@pytest.fixture(scope="module")
def sheet(tmp_path_factory):
    return messy_sheet(tmp_path_factory.mktemp("messy") / "foglio.dxf")


# --- parallel line pairs ----------------------------------------------------------------------

def test_two_parallel_lines_with_open_ends_make_a_wall():
    lines = [LineString([(0, 0), (5, 0)]), LineString([(0, 0.3), (5, 0.3)]),
             LineString([(5.5, 0), (5.5, 4)]), LineString([(5.8, 0), (5.8, 4)])]
    faces = faces_from_pairs(lines, 0.6)
    assert unary_union(faces).area == pytest.approx(5 * 0.3 + 4 * 0.3)


def test_lines_too_far_too_close_not_facing_or_not_parallel_make_no_wall():
    assert faces_from_pairs([LineString([(0, 0), (5, 0)]), LineString([(0, 1.5), (5, 1.5)])], 0.6) == []  # too far
    assert faces_from_pairs([LineString([(0, 0), (5, 0)]), LineString([(0, 0.01), (5, 0.01)])], 0.6) == []  # one line twice
    assert faces_from_pairs([LineString([(0, 0), (2, 0)]), LineString([(3, 0.3), (5, 0.3)])], 0.6) == []  # no overlap
    assert faces_from_pairs([LineString([(0, 0), (5, 0)]), LineString([(0, 0.3), (5, 1.3)])], 0.6) == []  # not parallel


def test_a_tilted_wall_is_found_too():
    a = math.radians(30)
    u, n = (math.cos(a), math.sin(a)), (-math.sin(a), math.cos(a))
    p = lambda s, t: (s * u[0] + t * n[0], s * u[1] + t * n[1])  # noqa: E731
    faces = faces_from_pairs([LineString([p(0, 0), p(6, 0)]), LineString([p(0, 0.25), p(6, 0.25)])], 0.6)
    assert unary_union(faces).area == pytest.approx(6 * 0.25, rel=1e-6)


def test_small_lone_bodies_are_not_walls_but_attached_ones_stay():
    wall = box(0, 0, 6, 0.3)
    stool = box(2, 2, 2.4, 2.4)
    leaning = box(1, 0.3, 1.4, 0.7)  # touches the wall: part of it
    kept = drop_specks(unary_union([wall, stool, leaning]))
    assert kept.area == pytest.approx(wall.area + leaning.area)
    assert drop_specks(box(0, 0, 0.5, 0.5)).area == pytest.approx(0.25)  # a single body is never dropped


# --- the unit ---------------------------------------------------------------------------------

def _soup(declared, dims=(), arcs=(), extent=2000.0):
    seg = np.array([[0.0, 0.0, extent, 0.0], [0.0, extent / 2, extent, extent / 2]])
    return Soup(declared, ["0"], seg, np.zeros(2, dtype=int), np.array(arcs, dtype=float).reshape(-1, 5),
                np.zeros(len(arcs), dtype=int), np.zeros((0, 3)), np.zeros((0, 4)),
                dims=[{"layer": "0", "x": 0, "y": 0, "value": v} for v in dims])


def test_dimension_values_decide_the_unit_not_the_header():
    guess = infer_unit(_soup("mm", dims=[90, 120, 250, 310, 480, 600]))
    assert guess.unit == "cm" and guess.differs and guess.declared == "mm"
    assert infer_unit(_soup("m", dims=[900, 1200, 2500, 3100, 4800, 6000], extent=12000)).unit == "mm"
    assert not infer_unit(_soup("cm", dims=[90, 120, 250, 310, 480, 600])).differs


def test_a_big_hall_dimensioned_overall_only_keeps_its_centimetre_header():
    # 60 x 30 m in centimetres, five overall dimensions of 20 to 60 m and nothing else: the median (40 m) is unusual for a
    # plan, but millimetres (4 m) is no reason to overrule a header that says centimetres
    guess = infer_unit(_soup("cm", dims=[4000, 6000, 3000, 2000, 5000], extent=6000.0))
    assert guess.unit == "cm" and not guess.differs
    # ... while a header that is wrong still loses to dimensions that fit only the other unit
    assert infer_unit(_soup("m", dims=[90, 120, 250, 310, 480, 600], extent=2000.0)).unit == "cm"


def test_a_tie_between_units_goes_to_the_header_else_to_centimetres():
    # a plan 14 m wide in centimetres with nothing to measure: metres and centimetres tie, and metres used to win
    assert infer_unit(_soup(None, extent=1400.0)).unit == "cm"
    assert infer_unit(_soup("m", extent=1400.0)).unit == "m"  # the header breaks the tie
    assert infer_unit(_soup("mm", extent=1400.0)).unit == "mm"


def test_a_sheet_read_in_a_unit_a_hundred_times_too_big_is_not_cut_in_millions_of_cells():
    import time

    from dwg2c4d.autodetect import _grid_scale

    rng = np.random.default_rng(1)
    n = 5000  # the lines of a plan 14 x 9 m in centimetres...
    x, y = rng.uniform(40, 1360, n), rng.uniform(40, 860, n)
    seg = np.column_stack([x, y, x + rng.uniform(20, 200, n), y])
    soup = Soup(None, ["0"], seg, np.zeros(n, dtype=int), np.zeros((0, 5)), np.zeros(0, dtype=int), np.zeros((0, 3)),
                np.zeros((0, 4)))
    assert _grid_scale(soup, 0.01) == 0.01 and _grid_scale(soup, 1.0) < 0.5  # ... asked at one metre a unit
    t0 = time.time()
    find_views(soup, 0.01)
    right = time.time() - t0
    t0 = time.time()
    assert find_views(soup, 1.0)
    assert time.time() - t0 < 25 * right + 5.0  # a hundred times too fine took 20 times as long and many GB: seconds, not minutes


def test_door_arcs_decide_the_unit():
    arcs = [(0, 0, 90, 0, 90)] * 4  # four 90 degree arcs of radius 90: doors in centimetres
    assert infer_unit(_soup("m", arcs=arcs, extent=1500)).unit == "cm"


# --- the views of the sheet -------------------------------------------------------------------

def test_titles_are_whole_words():
    assert _title_kind("PIANTA PIANO TERRA") == "plan"
    assert _title_kind("Prospetto Sud") == "elevation"
    assert _title_kind("SEZIONE A-A") == "section"
    assert _title_kind("PLANIMETRIA COPERTURA") == "roof"
    assert _title_kind("PLANIMETRIA GENERALE") == "site"
    assert _title_kind("TETTOIA IN ACCIAIO") is None  # not a roof
    assert _title_kind("Mq 12,50") is None


def test_the_plan_and_the_elevation_of_a_sheet_are_told_apart(sheet):
    doc = ezdxf.readfile(sheet)
    a = analyze(doc)
    assert a.unit.unit == "cm" and a.unit.differs  # the header says millimetres
    assert sorted(v.kind for v in a.views) == ["elevation", "plan"]
    assert a.plan is not None and a.plan.kind == "plan" and a.plan.bbox[1] > -1000  # not the elevation below


def test_a_view_can_be_asked_for(sheet):
    a = analyze(ezdxf.readfile(sheet), None, 2)
    assert a.plan is not None and a.plan.id == 2


# --- the walls by their shape -----------------------------------------------------------------

def test_the_layer_that_has_the_shape_of_the_walls_is_found(sheet):
    doc = ezdxf.readfile(sheet)
    a = analyze(doc)
    assert candidate_layers(a, LayerRules()) == ["LINEE"]


def test_wall_metrics_prefer_closed_thin_bodies_to_a_block():
    ring = box(0, 0, 8, 6).difference(box(0.3, 0.3, 7.7, 5.7))
    block = box(0, 0, 8, 6)
    assert wall_metrics(ring, 60.0)["rooms"] == 1 and wall_metrics(ring, 60.0)["score"] > 3.0
    assert wall_metrics(block, 60.0)["score"] < 1.0


def test_a_ring_with_the_gaps_of_a_door_and_a_window_still_closes_a_room():
    ring = box(0, 0, 8, 6).difference(box(0.3, 0.3, 7.7, 5.7)).difference(box(3, -1, 4, 1)).difference(box(7, 2, 9, 3))
    assert wall_metrics(ring, 60.0)["rooms"] == 1


def test_the_whole_sheet_converts_without_any_layer_name(sheet, tmp_path):
    report = convert(sheet, tmp_path / "m.obj", Config(auto=True, images=True))
    assert report.unit == "cm" and not report.unit_guessed
    assert report.analysis is not None and report.analysis.plan.kind == "plan"
    # outer ring 1000 x 600 with 30 cm walls, minus the door (90 cm) and the window (120 cm) gaps, plus the partition
    assert report.wall_area_m2 == pytest.approx(9.42, abs=0.7)
    assert report.size_m[0] == pytest.approx(10.0, abs=0.2) and report.size_m[1] == pytest.approx(6.0, abs=0.2)
    assert report.doors == 1 and report.windows == 1  # the gap in the south wall is a window: the outside is behind it
    assert any("LINEE" in w for w in report.warnings)  # the report says which layer was taken for the walls
    assert (tmp_path / "m_viste.png").exists()


def test_a_sheet_in_centimetres_whose_header_says_feet_converts_as_one_in_centimetres(tmp_path):
    path = messy_sheet(tmp_path / "ft.dxf", declared_units=2)  # the dimensions (2 to 10 m) are 600 m and more in feet
    report = convert(path, tmp_path / "ft.obj", Config(auto=True))
    assert report.unit == "cm" and report.size_m[0] == pytest.approx(10.0, abs=0.2) and report.size_m[1] == pytest.approx(6.0, abs=0.2)
    assert any("uso 'cm'" in w for w in report.warnings)


def test_without_the_analysis_the_layer_name_is_all_there_is(sheet, tmp_path):
    from dwg2c4d.dwgfile import ConversionError

    with pytest.raises(ConversionError):
        convert(sheet, tmp_path / "n.obj", Config(units="cm"))  # no wall layer by name: nothing to build


def test_what_the_user_says_beats_the_analysis(sheet, tmp_path):
    cfg = Config(auto=True, units="cm", layers=LayerRules({"wall": ["LINEE"]}))
    report = convert(sheet, tmp_path / "u.obj", cfg)
    assert not any("ha la forma dei muri" in w for w in report.warnings)  # nothing was added to what was asked


# --- doors by the shape of the swing ----------------------------------------------------------

def _arc_symbol(cx, cy, r=0.9, span=90.0, mid=None):
    mid = mid or (cx + r * math.cos(math.radians(span / 2)), cy + r * math.sin(math.radians(span / 2)))
    prim = Prim(LineString([(cx + r, cy), (cx, cy + r)]), "line", {"arc": (cx, cy, r, span, mid), "shape_door": True})
    return Symbol(prim.geom, [prim], None, "L", by_shape=True)


def test_an_arc_that_turns_about_a_wall_end_is_a_swing():
    walls = box(-3, -0.3, 0, 0)  # a wall ending at the hinge (0, 0)
    assert _is_swing(_arc_symbol(0, 0), walls)


def test_an_arc_far_from_every_wall_is_not_a_door():
    assert not _is_swing(_arc_symbol(3, 3), box(-3, -0.3, 0, 0))


def test_the_arc_of_a_rounded_wall_corner_is_not_a_door():
    corner = Point(0, 0).buffer(0.9).difference(Point(0, 0).buffer(0.6))  # the wall is the arc's own ring
    assert not _is_swing(_arc_symbol(0, 0, r=0.75), corner)


# --- the command line -------------------------------------------------------------------------

def test_the_command_line_reads_the_sheet_and_draws_the_views(sheet, tmp_path):
    out = tmp_path / "c.obj"
    assert main([str(sheet), "-o", str(out)]) == 0
    assert out.exists() and (tmp_path / "c_viste.png").exists()


def test_the_command_line_can_leave_the_analysis_out(sheet, tmp_path):
    out = tmp_path / "d.obj"
    assert main([str(sheet), "-o", str(out), "--no-analisi", "--unita", "cm"]) != 0  # no layer says what a wall is
    assert not (tmp_path / "d_viste.png").exists()


def test_the_command_line_takes_a_view_number(sheet, tmp_path):
    out = tmp_path / "e.obj"
    assert main([str(sheet), "-o", str(out), "--vista", "1"]) == 0
    assert (tmp_path / "e_viste.png").exists()
    assert main([str(sheet), "-o", str(out), "--vista", "0"]) == 1  # the views are numbered from 1


def test_a_failing_analysis_never_stops_the_conversion(sheet, tmp_path, monkeypatch):
    import dwg2c4d.pipeline as pipeline

    def broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(pipeline, "analyze", broken)
    cfg = Config(auto=True, units="cm", layers=LayerRules({"wall": ["LINEE"]}))
    report = convert(sheet, tmp_path / "f.obj", cfg)
    assert report.wall_area_m2 > 5.0 and any("Analisi del foglio non riuscita" in w for w in report.warnings)


# --- what the review of the first version found ---------------------------------------------------

def _opened(path, cfg=None):
    """convert() as the command line calls it: the analysis on."""
    import tempfile
    from pathlib import Path

    out = Path(tempfile.mkdtemp()) / "o.obj"
    return convert(path, out, cfg or Config(auto=True))


def _titled_building(tmp_path, wall_layers=("MURI",), title="PIANTA PIANO TERRA", units=5):
    """The 10 x 6 m building of the other tests, a door and a window on their layers, and a title under it."""
    from builders import building

    doc, msp = building(units)
    for name in wall_layers:
        if not doc.layers.has_entry(name):
            doc.layers.add(name)
    msp.add_text(title, height=20, dxfattribs={"layer": "TESTI", "insert": (350, -150)})
    path = tmp_path / "b.dxf"
    doc.saveas(path)
    return doc, path


@pytest.mark.parametrize("names", [("MURI_ESTERNI", "MURI_INTERNI"), ("Muri esterni", "Tramezzi"),
                                   ("A-WALL-EXTERIOR", "A-WALL-INT")])
def test_walls_whose_names_say_outside_are_still_walls(tmp_path, names):
    import ezdxf as _ezdxf

    from builders import W, D, T, DOOR, WIN_S, _rect

    doc = _ezdxf.new("R2018", setup=True)
    doc.units = 5
    for name in (*names, "PORTE", "FINESTRE"):
        doc.layers.add(name)
    msp = doc.modelspace()
    _rect(msp, 0, 0, W, D, names[0])  # the outer faces on the "outside" layer
    _rect(msp, T, T, W - T, D - T, names[1])
    _rect(msp, DOOR[0], 0, DOOR[1], T, "PORTE")
    _rect(msp, WIN_S[0], 0, WIN_S[1], T, "FINESTRE")
    msp.add_text("PIANTA PIANO TERRA", height=20, dxfattribs={"insert": (350, -150)})
    path = tmp_path / "e.dxf"
    doc.saveas(path)
    on, off = _opened(path), _opened(path, Config())
    assert on.size_m == pytest.approx(off.size_m) and 9.5 < on.size_m[0] < 11.0
    assert (on.doors, on.windows) == (off.doors, off.windows) == (1, 1)


def test_a_group_of_walls_without_openings_between_two_plans_does_not_loop_forever(tmp_path):
    import ezdxf as _ezdxf

    from builders import W, D, T, DOOR, WIN_S, _rect

    doc = _ezdxf.new("R2018", setup=True)
    doc.units = 5
    for name in ("MURI", "PORTE", "FINESTRE"):
        doc.layers.add(name)
    msp = doc.modelspace()
    for k, dx in enumerate((0, 1700, 3400)):
        _rect(msp, dx, 0, dx + W, D, "MURI")
        _rect(msp, dx + T, T, dx + W - T, D - T, "MURI")
        if k != 1:  # the middle one has neither door nor window
            _rect(msp, dx + DOOR[0], 0, dx + DOOR[1], T, "PORTE")
            _rect(msp, dx + WIN_S[0], 0, dx + WIN_S[1], T, "FINESTRE")
    path = tmp_path / "three.dxf"
    doc.saveas(path)
    report = _opened(path, Config())
    assert report.doors == 2 and report.windows == 2


def test_a_cadastral_plan_is_the_site_not_the_building(tmp_path):
    doc, path = _titled_building(tmp_path)
    doc = ezdxf.readfile(path)
    doc.layers.add("CATASTO")
    msp = doc.modelspace()
    msp.add_lwpolyline([(5000, -6000), (11000, -6000), (11000, -2000), (5000, -2000)], close=True,
                       dxfattribs={"layer": "CATASTO"})
    msp.add_lwpolyline([(5100, -5900), (10900, -5900), (10900, -2100), (5100, -2100)], close=True,
                       dxfattribs={"layer": "CATASTO"})
    msp.add_text("PLANIMETRIA CATASTALE", height=20, dxfattribs={"layer": "TESTI", "insert": (7000, -6300)})
    doc.saveas(path)
    a = analyze(ezdxf.readfile(path))
    assert a.plan is not None and a.plan.bbox[0] < 2000  # the building, not the 60 x 40 m parcel
    assert sorted(v.kind for v in a.views) == ["plan", "site"]
    report = _opened(path)
    assert 9.5 < report.size_m[0] < 11.0 and (report.doors, report.windows) == (1, 2)


def test_three_overall_dimensions_do_not_beat_a_right_header():
    # a 8 x 6 m plan in centimetres: its overall dimensions (800, 800, 600) would fit "mm" as 0.8 m...
    guess = infer_unit(_soup("cm", dims=[800, 800, 600], extent=800))
    assert guess.unit == "cm" and not guess.differs


def test_a_header_in_feet_or_inches_is_not_overridden_where_nothing_speaks_against_it():
    for declared, extent in (("ft", 40.0), ("in", 600.0)):  # a 12 m and a 15 m sheet
        guess = infer_unit(_soup(declared, extent=extent))
        assert guess.unit == declared and not guess.differs
    # a plan really drawn in feet: its doors are 3 ft wide and its dimensions are 10 to 30 ft
    feet = infer_unit(_soup("ft", dims=[12, 18, 25, 10, 30], arcs=[(0, 0, 3, 0, 90)] * 4, extent=60.0))
    assert feet.unit == "ft" and not feet.differs


@pytest.mark.parametrize("declared", ["ft", "in"])
def test_a_centimetre_sheet_with_a_header_in_feet_or_inches_is_read_in_centimetres(declared):
    # converters that do not know the unit often write feet: 90 cm door arcs, 15 cm letters, dimensions of 2 to 5 m
    arcs = [(0, 0, 90, 0, 90)] * 5
    soup = _soup(declared, dims=[200, 350, 500, 420, 280], arcs=arcs, extent=3000.0)
    soup.texts = [{"layer": "T", "text": "x", "x": 0, "y": 0, "height": 15.0}] * 10
    guess = infer_unit(soup)
    assert guess.unit == "cm" and guess.differs and guess.declared == declared


def test_a_header_in_feet_is_not_enough_to_read_the_sheet_at_the_wrong_scale():
    from dwg2c4d.autodetect import apply_analysis

    doc = ezdxf.new("R2018", setup=True)
    doc.units = 2  # feet, on a sheet of centimetres: the whole of the sheet fragmented at 30 cm a unit
    msp = doc.modelspace()
    from test_views import add_elevation, add_plan, add_title

    add_plan(msp, 0, 0, w=1800, d=1100)
    add_title(msp, "PIANTA PIANO TERRA", 0, -250)
    add_elevation(msp, 3000, 0, w=1800)
    add_title(msp, "PROSPETTO SUD", 3000, 850)
    a = analyze(doc)
    assert a.unit.unit == "cm" and a.unit.differs
    assert a.plan is not None and a.plan.kind == "plan" and min(a.plan.size_m(a.unit.scale)) > 10
    cfg, _ = apply_analysis(doc, Config(), a)
    assert cfg.units == "cm" and cfg.area is not None and cfg.area[2] - cfg.area[0] < 2100


def test_a_plan_too_small_to_be_one_is_not_cropped_to(tmp_path):
    from dwg2c4d.autodetect import Analysis, UnitGuess, View, apply_analysis

    doc = ezdxf.new("R2018", setup=True)
    soup = scan(doc)
    sliver = View(2, (0, 0, 460, 60), 80, kind="plan", doors=3)  # 4.6 x 0.6 m in centimetres: the frame of a title
    a = Analysis(soup, UnitGuess("cm", "cm", {}, []), [View(1, (0, 0, 5000, 3000), 900), sliver], sliver)
    cfg, notes = apply_analysis(doc, Config(), a)
    assert cfg.area is None and any("misura solo 4.6 x 0.6 m" in n for n in notes)
    assert apply_analysis(doc, Config(view=2), a)[0].area is not None  # ... unless it is the view the user asked for
    sliver.bbox = (0, 0, 1000, 700)  # a 10 x 7 m plan is cropped to as before
    assert apply_analysis(doc, Config(), a)[0].area is not None


def test_a_missing_header_is_filled_in_from_the_arcs_and_dimensions(tmp_path):
    # no $INSUNITS: a 4 x 3 m room in millimetres with door arcs and dimensions
    doc = ezdxf.new("R2018", setup=True)
    doc.header["$INSUNITS"] = 0
    msp = doc.modelspace()
    for pts in ([(0, 0), (4000, 0), (4000, 3000), (0, 3000)],):
        msp.add_lwpolyline(pts, close=True)
    for x in (500, 1500, 2500):
        msp.add_arc((x, 0), 800, 0, 90)
    for p1, p2 in (((0, 0), (800, 0)), ((800, 0), (2000, 0)), ((0, 0), (0, 1500)), ((0, 1500), (0, 3000))):
        msp.add_linear_dim(base=(0, -300), p1=p1, p2=p2).render()
    path = tmp_path / "noheader.dxf"
    doc.saveas(path)
    a = analyze(ezdxf.readfile(path))
    assert a.unit.unit == "mm" and a.unit.declared is None and a.unit.margin >= 2.0


def test_the_unit_the_user_gave_is_never_guessed(sheet):
    a = analyze(ezdxf.readfile(sheet), None, None, "m")
    assert a.unit.unit == "m" and a.unit.evidence == ["indicata da te"]


def test_a_curved_piece_of_furniture_is_no_door_where_the_layers_name_the_doors(tmp_path):
    doc, path = _titled_building(tmp_path)
    doc = ezdxf.readfile(path)
    doc.layers.add("SANITARI")
    # a quarter-round shower in the south-west corner, hinged on the wall: it sweeps 90 degrees with a radius of 90
    doc.modelspace().add_arc((30, 30), 90, 0, 90, dxfattribs={"layer": "SANITARI"})
    doc.saveas(path)
    on, off = _opened(path), _opened(path, Config())
    assert on.doors == off.doors == 1


def test_a_gap_in_the_outside_wall_stays_a_doorway_where_the_layers_name_the_openings(tmp_path):
    from builders import W, D, T, _rect

    import ezdxf as _ezdxf

    doc = _ezdxf.new("R2018", setup=True)
    doc.units = 5
    for name in ("MURI", "PORTE"):
        doc.layers.add(name)
    msp = doc.modelspace()
    for y0, y1 in ((0, T), (D - T, D)):  # walls with a 1.2 m gap in the south one, no symbol
        pass
    at = {"layer": "MURI"}
    for x0, x1 in ((0, 400), (520, W)):
        msp.add_line((x0, 0), (x1, 0), dxfattribs=at)
        msp.add_line((x0, T), (x1, T), dxfattribs=at)
    for a, b in (((0, D), (W, D)), ((0, D - T), (W, D - T)), ((0, 0), (0, D)), ((T, 0), (T, D)),
                 ((W, 0), (W, D)), ((W - T, 0), (W - T, D))):
        msp.add_line(a, b, dxfattribs=at)
    _rect(msp, 100, 0, 190, T, "PORTE")  # a door symbol exists: the names say what the openings are
    path = tmp_path / "gap.dxf"
    doc.saveas(path)
    on, off = _opened(path), _opened(path, Config())
    assert (on.windows, on.passages) == (off.windows, off.passages) and on.windows == 0


def test_a_sheet_with_a_lot_of_scattered_drawing_is_not_quadratic():
    import time

    rng = np.random.default_rng(1)
    n = 60000
    p = rng.uniform(0, 400000, (n, 2))
    seg = np.hstack([p, p + rng.uniform(-50, 50, (n, 2))])
    soup = Soup("cm", ["0"], seg, np.zeros(n, dtype=int), np.zeros((0, 5)), np.zeros(0, dtype=int), np.zeros((0, 3)),
                np.zeros((0, 4)))
    t0 = time.time()
    views = find_views(soup, 0.01)
    assert time.time() - t0 < 10.0 and isinstance(views, list)


def test_old_style_polylines_solids_and_mirrored_arcs_are_seen_by_the_scan(tmp_path):
    doc = ezdxf.new("R12")
    msp = doc.modelspace()
    msp.add_polyline2d([(0, 0), (500, 0), (500, 300), (0, 300)], close=True)
    msp.add_solid([(1000, 0), (1100, 0), (1000, 100), (1100, 100)])
    arc = msp.add_arc((100, 0), 90, 0, 90)
    path = tmp_path / "r12.dxf"
    doc.saveas(path)
    soup = scan(ezdxf.readfile(path))
    assert len(soup.seg) == 8 and len(soup.arcs) == 1

    doc = ezdxf.new("R2018")
    doc.modelspace().add_arc((100, 50), 90, 10, 100, dxfattribs={"extrusion": (0, 0, -1)})
    doc.modelspace().add_lwpolyline([(0, 0), (200, 0)], dxfattribs={"extrusion": (0, 0, -1)})
    path = tmp_path / "mirror.dxf"
    doc.saveas(path)
    soup = scan(ezdxf.readfile(path))
    assert soup.arcs[0, 0] == pytest.approx(-100) and soup.seg[0, 0] == pytest.approx(0) and soup.seg[0, 2] == pytest.approx(-200)
    assert soup.arcs[0, 3] == pytest.approx(80) and soup.arcs[0, 4] == pytest.approx(90)


def test_a_sheet_of_several_drawings_side_by_side_is_not_in_the_wrong_unit_because_it_is_big():
    # numbers in centimetres, header millimetres, 400 m of sheet (three tavole in a row): dimensions and the height of
    # the texts (15) say centimetres, the size of the sheet must not veto it
    texts = [{"layer": "T", "text": "x", "x": 0, "y": 0, "height": 15.0}] * 20
    soup = _soup("mm", dims=[262, 300, 180, 410], extent=41000.0)
    soup.texts = texts
    guess = infer_unit(soup)
    assert guess.unit == "cm" and guess.differs and any("testi" in e for e in guess.evidence)


def test_the_text_height_alone_tells_centimetres_from_millimetres():
    soup = _soup(None, extent=3000.0)
    soup.texts = [{"layer": "T", "text": "x", "x": 0, "y": 0, "height": 25.0}] * 10  # 25 cm letters, not 2.5 cm
    assert infer_unit(soup).unit == "cm"
    soup.texts = [{"layer": "T", "text": "x", "x": 0, "y": 0, "height": 250.0}] * 10  # 250 mm letters
    assert infer_unit(_soup(None, extent=30000.0)).unit in ("cm", "mm")  # no texts here: nothing to decide on
    soup2 = _soup(None, extent=30000.0)
    soup2.texts = soup.texts
    assert infer_unit(soup2).unit == "mm"
