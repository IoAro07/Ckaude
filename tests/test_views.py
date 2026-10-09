"""The views of a sheet: where each drawing is, what it is called and what it is, on synthetic tavole (frames, titles above
and below, long ground lines, crop marks, a plan on its lot) made with ezdxf. Numbers are centimetres."""

import math
import time

import ezdxf
import numpy as np
import pytest
from ezdxf.math import Matrix44

from dwg2c4d.autodetect import Soup, View, analyze, choose_plan, find_views, scan

S = 0.01  # metres per drawing unit


def new_sheet():
    doc = ezdxf.new("R2018", setup=True)
    doc.units = 5  # centimetres
    return doc, doc.modelspace()


def add_plan(msp, x, y, w=1400, d=900, doors=5, furniture=6, door_gap=250):
    """A plan with its south-west corner at (x, y): double-line walls, a partition, swing doors, furniture, and the
    names and areas of the rooms."""
    for off in (0, 30):
        msp.add_lwpolyline([(x + off, y + off), (x + w - off, y + off), (x + w - off, y + d - off), (x + off, y + d - off)],
                           close=True)
    for off in (0, 30):
        msp.add_line((x + 600 + off, y), (x + 600 + off, y + d))
    for k in range(doors):
        cx = x + 120 + k * door_gap
        msp.add_arc((cx, y + 30), 90, 0, 90)
        msp.add_line((cx, y + 30), (cx, y + 120))
    for k in range(furniture):
        msp.add_lwpolyline([(x + 100 + k * 200, y + 400), (x + 180 + k * 200, y + 400), (x + 180 + k * 200, y + 480),
                            (x + 100 + k * 200, y + 480)], close=True)
    for k, name in enumerate(("CAMERA", "BAGNO", "CUCINA", "SOGGIORNO")):
        msp.add_text(f"{name} MQ {10 + k},5", height=20, dxfattribs={"insert": (x + 100 + k * 300, y + 700)})


def add_elevation(msp, x, y, w=1400, h=600, windows=5, ground=True, marks=True):
    """A facade standing on the ground line y: the wall, a roof line, windows and (``marks``) two level marks."""
    if ground:
        msp.add_line((x - 200, y), (x + w + 200, y))
    msp.add_lwpolyline([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], close=True)
    msp.add_line((x - 50, y + h), (x + w / 2, y + h + 150))
    msp.add_line((x + w + 50, y + h), (x + w / 2, y + h + 150))
    step = (w - 200) / windows
    for k in range(windows):
        wx = x + 100 + k * step
        msp.add_lwpolyline([(wx, y + 250), (wx + 120, y + 250), (wx + 120, y + 390), (wx, y + 390)], close=True)
        msp.add_line((wx + 60, y + 250), (wx + 60, y + 390))
    if marks:
        msp.add_text("+0,00", height=15, dxfattribs={"insert": (x + w + 60, y)})
        msp.add_text("+3,20", height=15, dxfattribs={"insert": (x + w + 60, y + 320)})


def add_title(msp, text, x, y, h=35):
    """A title in a little frame (a box a bit wider than the text) whose lower left corner is (x, y)."""
    msp.add_text(text, height=h, dxfattribs={"insert": (x + 10, y + 12)})
    w = len(text) * h * 0.7 + 20
    msp.add_lwpolyline([(x, y), (x + w, y), (x + w, y + h + 25), (x, y + h + 25)], close=True)


def views_of(doc):
    return find_views(scan(doc), S)


def box_m(v):
    return tuple(c * S for c in v.bbox)


def view_at(views, x_m, y_m):
    """The view whose box holds the point (metres); the smallest one when there are several."""
    here = [v for v in views if v.contains(x_m / S, y_m / S)]
    assert here, f"no view at {x_m}, {y_m}: {[box_m(v) for v in views]}"
    return min(here, key=lambda v: (v.bbox[2] - v.bbox[0]) * (v.bbox[3] - v.bbox[1]))


# --- the border of the sheet -------------------------------------------------------------------

def test_the_border_of_the_sheet_is_no_view_and_swallows_no_drawing():
    doc, msp = new_sheet()
    add_plan(msp, 0, 0)
    add_elevation(msp, 2400, 100)
    msp.add_lwpolyline([(-600, -600), (4600, -600), (4600, 1900), (-600, 1900)], close=True)  # 52 x 25 m, 6 m clear
    views = views_of(doc)
    assert sorted(v.kind for v in views) == ["elevation", "plan"]
    assert all(v.size_m(S)[0] < 20 and v.size_m(S)[1] < 12 for v in views)


def test_a_border_close_to_the_drawings_does_not_join_them():
    doc, msp = new_sheet()
    add_plan(msp, 0, 0)
    add_elevation(msp, 2600, 100)
    msp.add_lwpolyline([(-100, -100), (4800, -100), (4800, 1000), (-100, 1000)], close=True)  # a metre from the plan
    views = views_of(doc)
    assert sorted(v.kind for v in views) == ["elevation", "plan"]
    plan = next(v for v in views if v.kind == "plan")
    assert plan.size_m(S)[0] < 17 and plan.size_m(S)[1] < 12  # the border does not make it the size of the sheet


def test_a_frame_with_a_table_around_the_views_is_not_a_view():
    doc, msp = new_sheet()
    add_plan(msp, 0, 0)
    for x in (-500, 1900):  # two nested frames, as the border and the margin of a tavola often are
        msp.add_lwpolyline([(x - 100, -700), (x + 2500, -700), (x + 2500, 1800), (x - 100, 1800)], close=True)
    add_elevation(msp, 2600, 100, w=1000)
    views = views_of(doc)
    assert all(v.size_m(S)[0] < 20 for v in views)
    assert {v.kind for v in views} == {"plan", "elevation"}


# --- long straight lines -----------------------------------------------------------------------

def test_a_long_ground_line_under_two_elevations_does_not_join_them():
    doc, msp = new_sheet()
    add_elevation(msp, 0, 0, ground=False)
    add_elevation(msp, 2800, 0, ground=False)
    msp.add_line((-200, 0), (4400, 0))  # one ground line, 46 m, under both
    views = views_of(doc)
    assert len(views) == 2 and all(v.size_m(S)[0] < 20 for v in views)


def test_a_section_mark_running_between_two_plans_does_not_join_them():
    doc, msp = new_sheet()
    add_plan(msp, 0, 0)
    add_plan(msp, 2200, 0)
    msp.add_line((-300, 450), (3900, 450), dxfattribs={"color": 1})  # the trace of a section through both
    views = views_of(doc)
    assert len(views) == 2


def test_a_ground_line_keeps_the_bays_of_one_facade_together():
    doc, msp = new_sheet()
    for x in (0, 800, 1600):  # three blocks of one building, 4 m apart: only the ground line holds them
        add_elevation(msp, x, 0, w=400, windows=2, ground=False, marks=False)
    msp.add_line((-200, 0), (2200, 0))
    msp.add_text("+0,00", height=15, dxfattribs={"insert": (2250, 0)})
    msp.add_text("+3,20", height=15, dxfattribs={"insert": (2250, 320)})
    views = views_of(doc)
    assert len(views) == 1 and views[0].size_m(S)[0] > 22


def test_a_tail_of_the_ground_line_does_not_stretch_the_view():
    doc, msp = new_sheet()
    add_elevation(msp, 0, 0)
    msp.add_line((-200, 0), (-5000, 0))  # the ground goes on for 48 m to the left
    (v,) = views_of(doc)
    assert v.size_m(S)[0] < 24


# --- stray marks -------------------------------------------------------------------------------

def test_crop_marks_in_the_corners_do_not_stretch_a_view():
    doc, msp = new_sheet()
    add_plan(msp, 0, 0)
    for cx, cy, sx, sy in ((-600, -600, 1, 1), (2000, -600, -1, 1), (-600, 1500, 1, -1), (2000, 1500, -1, -1)):
        msp.add_line((cx, cy), (cx + 120 * sx, cy))
        msp.add_line((cx, cy), (cx, cy + 120 * sy))
    views = views_of(doc)
    assert len(views) == 1
    x0, y0, x1, y1 = box_m(views[0])
    assert x0 > -1 and y0 > -1 and x1 < 15 and y1 < 10


def test_a_chain_of_tiny_marks_does_not_walk_away_from_a_view():
    doc, msp = new_sheet()
    add_plan(msp, 0, 0)
    for k in range(12):  # a mark every 2.5 m, the first one 2.2 m from the plan: each is near the next one only
        x = 1400 + 220 + k * 250
        msp.add_lwpolyline([(x, 400), (x + 80, 400), (x + 80, 480)])
    views = views_of(doc)
    assert len(views) == 1 and box_m(views[0])[2] < 14 + 3.5 + 1


def test_a_small_note_next_to_a_plan_is_a_part_of_it_and_a_far_one_is_not():
    doc, msp = new_sheet()
    add_plan(msp, 0, 0)
    for k in range(4):  # a north arrow 1.5 m from the east wall
        msp.add_line((1560 + k * 20, 400), (1560 + k * 20, 520))
    msp.add_lwpolyline([(1560, 520), (1620, 600), (1580, 520)])
    for k in range(4):  # the same, 9 m away
        msp.add_line((2400 + k * 20, 400), (2400 + k * 20, 520))
    views = views_of(doc)
    assert len(views) == 1 and 16 < box_m(views[0])[2] < 17.5


# --- titles ------------------------------------------------------------------------------------

def test_the_frame_of_a_title_a_metre_under_a_plan_is_no_part_of_it_and_names_the_elevation_below():
    doc, msp = new_sheet()
    add_plan(msp, 0, 1000)
    add_elevation(msp, 0, 0, marks=False)
    add_title(msp, "PROSPETTO FRONTALE", 0, 880)  # 0.6 m under the plan, 1.8 m above the roof of the elevation
    views = views_of(doc)
    assert len(views) == 2
    plan, elevation = (next(v for v in views if v.kind == k) for k in ("plan", "elevation"))
    assert box_m(plan)[1] == pytest.approx(10.0, abs=0.6)  # the plan stops at its own wall
    assert elevation.titles == ["PROSPETTO FRONTALE"] and not plan.titles
    assert elevation.kind_from == "titolo"


def test_a_title_that_says_prospetto_never_names_the_plan():
    doc, msp = new_sheet()
    add_plan(msp, 0, 600)
    add_elevation(msp, 2400, 0)  # the elevation is far; the title is much nearer to the plan
    add_title(msp, "PROSPETTO SUD", 100, 450)
    views = views_of(doc)
    plan = next(v for v in views if v.kind == "plan")
    assert plan.titles == []


def _two_rows(above: bool, doc_msp=None):
    """Two facades one above the other, 3.5 m apart, and a title in the gap that is nearer to the facade it does not
    name (1.1 m against 1.8 m); three clear titles, 1.5 m above (or below) their own facades and 30 m from the rest, show
    the habit of the sheet."""
    doc, msp = doc_msp or new_sheet()
    for k, x in enumerate((0, 2200, 4400)):
        add_elevation(msp, x, 4000, w=1000)
        add_title(msp, f"PROSPETTO {k + 1}", x, 4000 + 600 + 150 + 150 if above else 4000 - 150 - 60)
    add_elevation(msp, 0, 1150, w=1000)  # the upper facade of the pair
    add_elevation(msp, 0, 50, w=1000)  # the lower one: its roof reaches y = 800
    # above: the title names the lower facade (it sits above it) but lies nearer the upper one
    add_title(msp, "PROSPETTO 4", 100, 980 if above else 910)
    return doc


def test_a_frame_close_to_the_view_it_names_is_a_part_of_it_and_a_far_one_is_not():
    doc, msp = new_sheet()
    add_elevation(msp, 0, 0, marks=False)
    add_title(msp, "PROSPETTO SUD", 0, -200)  # 1.4 m under the ground line: the tab of the drawing
    add_elevation(msp, 3000, 0, marks=False)
    add_title(msp, "PROSPETTO NORD", 3000, -450)  # 4.4 m under it
    near, far = views_of(doc)
    assert near.titles == ["PROSPETTO SUD"] and box_m(near)[1] <= -1.9
    assert far.titles == ["PROSPETTO NORD"] and box_m(far)[1] >= -0.6


@pytest.mark.parametrize("above", [True, False])
def test_where_two_views_are_as_near_the_habit_of_the_sheet_decides(above):
    views = views_of(_two_rows(above))
    named = {v.titles[0]: v for v in views if v.titles}
    assert set(named) == {"PROSPETTO 1", "PROSPETTO 2", "PROSPETTO 3", "PROSPETTO 4"}
    y0 = box_m(named["PROSPETTO 4"])[1]
    assert (y0 < 5) if above else (y0 > 5)  # above: it names the lower facade of the pair; below: the upper one


def test_a_title_inside_a_view_names_that_view():
    doc, msp = new_sheet()
    add_plan(msp, 0, 0)
    add_elevation(msp, 0, 1300)
    msp.add_text("PIANTA PIANO TERRA", height=35, dxfattribs={"insert": (300, 300)})  # in the middle of the plan
    views = views_of(doc)
    assert next(v for v in views if v.kind == "plan").titles == ["PIANTA PIANO TERRA"]


def test_a_title_written_twice_counts_once_and_a_caption_with_a_number_is_a_title():
    doc, msp = new_sheet()
    add_elevation(msp, 0, 0, marks=False)
    for _ in range(2):
        msp.add_text("SEZIONE B-B", height=35, dxfattribs={"insert": (0, -300)})
    caption = "SEZIONE - A Superficie sezione: 30,58 mq Volume: 30,58 x 7,20 m = 220,18 mc S.U.C. Piano Primo: 220,18 / 3,50"
    msp.add_text(caption, height=20, dxfattribs={"insert": (0, 900)})
    msp.add_text("PIANTA DEL PIANO TERRA CON LE QUOTE DEGLI SCARICHI E DEI POZZETTI DI ISPEZIONE E DELLE ACQUE", height=20,
                 dxfattribs={"insert": (0, 1200)})  # a note, not a title
    from dwg2c4d.autodetect import _title_texts

    titles = _title_texts(scan(doc))
    assert sorted(t["text"][:10] for t in titles) == ["SEZIONE - ", "SEZIONE B-"]


# --- what a view is ----------------------------------------------------------------------------

def test_a_plan_is_known_by_its_doors_and_the_areas_of_its_rooms():
    doc, msp = new_sheet()
    add_plan(msp, 0, 0)
    (v,) = views_of(doc)
    assert v.kind == "plan" and v.kind_from.startswith("contenuto") and v.doors == 5 and v.areas == 4


def test_a_plan_turned_at_any_angle_is_still_a_plan_and_no_site():
    doc, msp = new_sheet()
    before = len(msp)
    add_plan(msp, 0, 0)
    turn = Matrix44.z_rotate(math.radians(31))
    for e in list(msp)[before:]:
        e.transform(turn)
    (v,) = views_of(doc)
    assert v.kind == "plan" and v.ortho > 0.8


def test_an_elevation_is_wide_and_low_with_level_marks_and_a_section_names_its_rooms():
    doc, msp = new_sheet()
    add_elevation(msp, 0, 0)
    add_elevation(msp, 2400, 0, marks=False)
    for k, name in enumerate(("CAMERA", "BAGNO", "CUCINA", "SOGGIORNO")):  # the rooms written in the drawing
        msp.add_text(name, height=20, dxfattribs={"insert": (2500 + k * 300, 100)})
    views = views_of(doc)
    assert [v.kind for v in views] == ["elevation", "section"]
    assert views[0].levels == 2 and views[1].rooms == 4


def test_a_facade_turned_on_its_side_is_still_an_elevation():
    doc, msp = new_sheet()
    before = len(msp)
    add_elevation(msp, 0, 0)
    turn = Matrix44.z_rotate(math.radians(90))
    for e in list(msp)[before:]:
        e.transform(turn)
    (v,) = views_of(doc)
    assert v.kind == "elevation" and v.size_m(S)[1] > v.size_m(S)[0]


def test_roof_tiles_make_a_roof_plan():
    doc, msp = new_sheet()
    for k in range(6):
        for j in range(2):
            x, y = k * 330, j * 330
            msp.add_lwpolyline([(x, y), (x + 320, y), (x + 320, y + 320), (x, y + 320)], close=True)
            hatch = msp.add_hatch()
            hatch.dxf.pattern_name = "COPPI01"
            hatch.dxf.solid_fill = 0
            hatch.paths.add_polyline_path([(x + 10, y + 10), (x + 310, y + 10), (x + 310, y + 310), (x + 10, y + 310)])
    (v,) = views_of(doc)
    assert v.kind == "roof" and v.tiles == 12 and v.doors == 0


def _site(msp, plan_at=(2700, 2000)):
    """A hillside: 25 contour lines of short straight pieces, 3 m apart, held together by a track across them, and (unless
    ``plan_at`` is None) a house with six doors on it."""
    t = np.linspace(0, math.pi / 2, 60)
    for k in range(25):
        r = 3000 + k * 300
        msp.add_lwpolyline([(r * math.cos(a) - 3000, r * math.sin(a) - 3000) for a in t])
    msp.add_lwpolyline([(-2500 + k * 450, -2500 + k * 450) for k in range(18)])
    if plan_at:
        add_plan(msp, *plan_at, doors=6)


def test_contour_lines_make_a_site_and_the_house_on_it_is_a_view_inside_it():
    doc, msp = new_sheet()
    _site(msp)
    views = views_of(doc)
    site = next(v for v in views if v.kind == "site")
    house = next(v for v in views if v.parent == site.id)
    assert house.kind == "plan" and house.doors == 6
    x0, y0, x1, y1 = box_m(house)
    assert x0 < 28 and x1 > 39 and y0 < 22 and y1 > 22 and x1 - x0 < 25 and y1 - y0 < 20  # about the doors, not the hill
    assert site.size_m(S)[0] > 60
    a = analyze(doc)
    assert a.plan.bbox == house.bbox  # the plan to convert is the house, not the hillside


def test_contour_lines_alone_are_a_site():
    doc, msp = new_sheet()
    _site(msp, None)
    (v,) = views_of(doc)
    assert v.kind == "site" and v.kind_from.startswith("contenuto") and v.ortho < 0.4


def test_a_long_hall_with_all_its_doors_at_one_end_is_one_plan_not_a_plan_on_a_lot():
    doc, msp = new_sheet()
    add_plan(msp, 0, 0, w=4000, doors=5, furniture=19, door_gap=150)
    for k in range(13):  # a window every 3 m on the two long walls
        for y in (0, 870):
            msp.add_lwpolyline([(150 + k * 300, y), (270 + k * 300, y), (270 + k * 300, y + 30), (150 + k * 300, y + 30)], close=True)
    (v,) = views_of(doc)
    assert v.kind == "plan" and v.parent is None and v.size_m(S)[0] > 39


def test_a_wide_and_low_drawing_with_no_marks_is_a_facade_and_a_squarish_one_is_not_told():
    doc, msp = new_sheet()
    add_elevation(msp, 0, 0, w=2000, marks=False)
    for k in range(5):  # a squarish drawing with nothing that says what it is
        msp.add_lwpolyline([(3000 + k * 150, 0), (3000 + k * 150, 1000), (3050 + k * 150, 1000)])
    msp.add_lwpolyline([(3000, 0), (3800, 0), (3800, 1000), (3000, 1000)], close=True)
    views = views_of(doc)
    assert [v.kind for v in views] == ["elevation", "?"]


def test_the_boundary_of_a_lot_is_a_view_of_its_own_and_a_lone_long_line_is_not():
    doc, msp = new_sheet()
    msp.add_lwpolyline([(0, 0), (6000, 0), (6000, 4000), (0, 4000)], close=True)  # a lot 60 x 40 m with nothing drawn in it
    msp.add_line((8000, 0), (12000, 0))  # a lone 40 m line
    views = views_of(doc)
    assert len(views) == 1 and views[0].outline and views[0].size_m(S) == pytest.approx((60.5, 40.5), abs=0.6)


def test_stray_pieces_of_a_big_drawing_go_and_boundary_lines_are_judged_by_their_place():
    from dwg2c4d.autodetect import _without_strays

    def piece(id_, box, cells, outline=False):
        return View(id_, box, cells, outline=outline)

    def left(*views, marks=()):
        return [v.id for v in _without_strays(list(views), list(marks), S)]

    site = piece(1, (0, 0, 10000, 8000), 6000)
    assert left(site, piece(2, (2000, 2000, 2400, 2500), 80)) == [1]  # 1% of the site, inside it
    assert left(site, piece(2, (10500, 3000, 10900, 3500), 80)) == [1]  # beside it, within 10 m
    assert left(site, piece(2, (30000, 3000, 30400, 3500), 80)) == [1, 2]  # 200 m away: a view of its own
    assert left(site, piece(2, (2000, 2000, 2400, 2500), 80), marks=[(2100, 2100, 2100, 2100)]) == [1, 2]  # a title is there
    assert left(site, piece(2, (-500, -500, 10500, 8500), 300, outline=True)) == [1]  # frames the site: a border
    assert left(site, piece(2, (9000, -2000, 11000, 6000), 100, outline=True)) == [1]  # half on the site: its edge
    assert left(site, piece(2, (12000, 0, 16000, 4000), 100, outline=True)) == [1, 2]  # beside it: a lot of its own


def test_a_few_contour_lines_cut_off_inside_a_site_are_not_views():
    doc, msp = new_sheet()
    _site(msp)
    for k in range(3):  # three bits of contour, each cut off from the rest by 3 m of empty space
        msp.add_lwpolyline([(300 + k * 700, 3500), (600 + k * 700, 3700), (900 + k * 700, 3650)])
    views = views_of(doc)
    assert sorted(v.kind for v in views) == ["plan", "site"]


def test_two_copies_of_a_plan_one_of_them_turned_are_copies():
    doc, msp = new_sheet()
    add_plan(msp, 0, 0)
    before = len(msp)
    add_plan(msp, 0, 0)
    turn = Matrix44.chain(Matrix44.z_rotate(math.radians(90)), Matrix44.translate(3000, 0, 0))
    for e in list(msp)[before:]:
        e.transform(turn)
    views = views_of(doc)
    assert [v.kind for v in views] == ["plan", "plan"]
    assert views[0].copy_of is None and views[1].copy_of == views[0].id


def _turned_copy(msp, at, turns=1, shift=(0, 0), **plan):
    """A plan drawn at ``at`` and then turned ``turns`` quarters about the origin and shifted by ``shift``: what a
    draughtsman does to put a plan into the site."""
    before = len(msp)
    add_plan(msp, *at, **plan)
    move = Matrix44.chain(Matrix44.z_rotate(math.radians(90 * turns)), Matrix44.translate(shift[0], shift[1], 0))
    for e in list(msp)[before:]:
        e.transform(move)


def test_a_plan_copied_into_the_site_has_the_exact_box_of_the_plan_and_is_its_copy():
    doc, msp = new_sheet()
    _site(msp, None)
    add_plan(msp, 12000, 0)  # the plan on the sheet
    _turned_copy(msp, (0, 0), turns=1, shift=(5500, 1500))  # ... and the same plan turned a quarter, in the site
    views = views_of(doc)
    site = next(v for v in views if v.kind == "site")
    plan = next(v for v in views if v.parent is None and v.kind == "plan")
    house = next(v for v in views if v.parent == site.id)
    assert house.kind == "plan" and house.copy_of == plan.id and plan.copy_of is None
    x0, y0, x1, y1 = box_m(house)  # the plan, 14 x 9 m, turned: 9 x 14 m, its corner (0, 0) goes to (55, 15)
    assert (x0, y0, x1, y1) == pytest.approx((46.0, 15.0, 55.0, 29.0), abs=0.6)  # the doors alone would say less


def test_words_in_the_same_places_in_two_different_plans_do_not_make_them_copies():
    doc, msp = new_sheet()
    for k, x in enumerate((0, 2000)):
        add_plan(msp, x, 0, furniture=0, doors=5 if k == 0 else 0)  # the same labels and the same outer walls...
        if k:
            for j in range(12):  # ... but another plan inside
                msp.add_line((x + 300 + j * 90, 100), (x + 300 + j * 90, 800))
                msp.add_line((x + 100, 100 + j * 55), (x + 550, 100 + j * 55))
        add_title(msp, ("PIANTA PIANO TERRA", "PIANTA PIANO PRIMO")[k], x, 1050)
    views = views_of(doc)
    assert [v.kind for v in views] == ["plan", "plan"] and all(v.copy_of is None for v in views)


def test_two_plans_of_a_sheet_that_repeat_each_other_are_copies_even_with_titles():
    doc, msp = new_sheet()
    for k, x in enumerate((0, 2000)):
        add_plan(msp, x, 0)
        add_title(msp, ("PIANTA PIANO TERRA", "PIANTA PIANO TERRA - COPIA")[k], x, 1050)
    first, second = views_of(doc)
    assert first.copy_of is None and second.copy_of == first.id and second.kind == "plan"


def test_the_plan_to_convert_is_the_biggest_that_is_not_a_site_a_copy_or_a_part_of_a_bigger_view():
    def v(id_, cells, kind="plan", **kw):
        return View(id_, (0, 0, 1, 1), cells, kind=kind, doors=kw.pop("doors", 3), **kw)

    views = [v(1, 5000, "site"), v(2, 900, copy_of=3), v(3, 700), v(4, 800, parent=1), v(5, 600, "elevation", doors=0)]
    assert choose_plan(views)[0].id == 3
    assert choose_plan([views[0], views[3]])[0].id == 4  # no other plan: the one on the lot
    assert choose_plan(views, 5)[0].id == 5 and choose_plan(views, 9)[0] is None


# --- a whole tavola ----------------------------------------------------------------------------

def test_a_tavola_with_plans_elevations_sections_and_titles_above_the_drawings():
    doc, msp = new_sheet()
    add_plan(msp, 0, 3000)
    add_plan(msp, 2200, 3000)
    for k, x in enumerate((0, 2200, 4400)):
        add_elevation(msp, x, 1000, w=1500)
        add_title(msp, ("PROSPETTO FRONTALE", "PROSPETTO RETROSTANTE", "PROSPETTO LATERALE")[k], x, 1000 + 600 + 150 + 120)
    for k, x in enumerate((0, 2200)):
        add_elevation(msp, x, -1500, w=1500)
        for j, name in enumerate(("CAMERA", "BAGNO", "CUCINA")):
            msp.add_text(name, height=20, dxfattribs={"insert": (x + 100 + j * 400, -1500 + 100)})
        add_title(msp, f"SEZIONE {'AB'[k]}-{'AB'[k]}", x, -1500 + 600 + 150 + 120)
    msp.add_lwpolyline([(-800, -2000), (6500, -2000), (6500, 4300), (-800, 4300)], close=True)
    views = views_of(doc)
    assert sorted(v.kind for v in views) == ["elevation"] * 3 + ["plan"] * 2 + ["section"] * 2
    for v in views:
        if v.kind == "elevation":
            assert [t[:9] for t in v.titles] == ["PROSPETTO"]
        if v.kind == "section":
            assert [t[:7] for t in v.titles] == ["SEZIONE"]
        if v.kind == "plan":
            assert not v.titles
    a = analyze(doc)
    assert a.plan is not None and a.plan.kind == "plan" and a.plan.size_m(S)[0] < 16


def test_a_damaged_entity_or_an_empty_sheet_does_not_stop_the_analysis():
    doc, msp = new_sheet()
    add_plan(msp, 0, 0)
    soup = scan(doc)
    soup.seg = np.vstack([soup.seg, [[0, 0, np.nan, 5], [0, 0, 1e300, 5], [np.inf, 0, 5, 5]]])
    soup.arcs = np.vstack([soup.arcs, [[np.nan, 0, 90, 0, 90], [0, 0, 1e300, 0, 90]]])
    soup.hatches = soup.hatches + [{"layer": "0", "pattern": "", "solid": True, "bbox": (0, 0, np.inf, 5)}]
    (v,) = find_views(soup, S)
    assert v.kind == "plan" and v.doors == 5
    empty = Soup("cm", ["0"], np.zeros((0, 4)), np.zeros(0, dtype=int), np.zeros((0, 5)), np.zeros(0, dtype=int),
                 np.zeros((0, 3)), np.zeros((0, 4)))
    assert find_views(empty, S) == []


# --- cost --------------------------------------------------------------------------------------

def test_eighty_thousand_lines_in_ten_drawings_are_read_in_a_few_seconds():
    rng = np.random.default_rng(3)
    boxes = [(x, y) for y in (0, 4000) for x in range(0, 15000, 3000)]  # ten drawings of 26 x 18 m, 4 m apart
    seg = []
    for x, y in boxes:
        p = np.column_stack([rng.uniform(x, x + 2600, 8000), rng.uniform(y, y + 1800, 8000)])
        seg.append(np.hstack([p, p + rng.uniform(-60, 60, (8000, 2))]))
    seg = np.vstack(seg)
    soup = Soup("cm", ["0"], seg, np.zeros(len(seg), dtype=int), np.zeros((0, 5)), np.zeros(0, dtype=int), np.zeros((0, 3)),
                np.zeros((0, 4)))
    t0 = time.time()
    views = find_views(soup, S)
    assert time.time() - t0 < 10.0
    assert len(views) == 10 and all(v.size_m(S)[0] < 30 for v in views)
