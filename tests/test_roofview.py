"""The roof plan drawn as a view of the sheet: cleaned of everything that is no roof structure and laid over the plan, on
synthetic shapes (metres)."""

import math

import pytest
from shapely import affinity
from shapely.geometry import LineString, Polygon, box

from dwg2c4d import roofview as rv
from dwg2c4d.reader import Item, Prim

# An L-shaped house, 12 x 8 m with a 5 x 5 m wing; its roof has 0.6 m eaves all round.
HOUSE = Polygon([(0, 0), (12, 0), (12, 8), (5, 8), (5, 13), (0, 13)])
ROOF = HOUSE.buffer(0.6, join_style="mitre")


def drawn_elsewhere(shape, turns, mirror, shift):
    """What a draughtsman does with a roof plan: a copy of ``shape`` turned, maybe mirrored, and put down elsewhere."""
    a, b, d, e = rv.pose_of(turns, mirror)
    return affinity.affine_transform(shape, [a, b, d, e, shift[0], shift[1]])


def laid_back(drawn, reg):
    return rv.place(drawn, reg)


# --- the registration ----------------------------------------------------------------------------

@pytest.mark.parametrize("turns", [0, 1, 2, 3])
def test_a_roof_plan_turned_by_any_quarter_and_drawn_far_away_is_laid_over_the_house(turns):
    drawn = drawn_elsewhere(ROOF, turns, False, (-53.97 + 400, 17.5 - 500))
    reg = rv.register_roof(drawn, HOUSE)
    assert reg is not None and reg.turns == (4 - turns) % 4 and not reg.mirror
    assert laid_back(drawn, reg).symmetric_difference(ROOF).area < 0.5
    assert reg.iou > 0.9 and reg.overhang == pytest.approx(0.6, abs=0.1) and not reg.tied


def test_a_mirrored_roof_plan_is_laid_over_the_house_when_it_fits_much_better_than_any_turned_one():
    stairs = Polygon([(0, 0), (14, 0), (14, 3), (10, 3), (10, 6), (4, 6), (4, 11), (0, 11)])  # no turn of it is its mirror
    roof = stairs.buffer(0.6, join_style="mitre")
    drawn = drawn_elsewhere(roof, 0, True, (100.0, -20.0))
    reg = rv.register_roof(drawn, stairs)
    assert reg is not None and reg.mirror and reg.iou > 0.9
    assert laid_back(drawn, reg).symmetric_difference(roof).area < 0.5


def test_a_roof_that_is_its_own_mirror_is_never_mirrored():
    box_house = box(0, 0, 12, 8)
    reg = rv.register_roof(box_house.buffer(0.6, join_style="mitre"), box_house)
    assert reg is not None and not reg.mirror and reg.turns == 0


def test_a_roof_that_covers_only_part_of_the_building_is_still_found():
    """The ground floor is wider than the roof of the floor above (a portico): the overlap is what counts."""
    ground = HOUSE.union(box(12, 0, 17, 6))
    drawn = drawn_elsewhere(ROOF, 1, False, (300.0, 40.0))
    reg = rv.register_roof(drawn, ground)
    assert reg is not None
    assert laid_back(drawn, reg).symmetric_difference(ROOF).area < 1.0


def test_a_roof_that_looks_like_nothing_of_the_plan_is_not_placed():
    strip = box(0, 0, 40, 3)
    assert rv.register_roof(strip, box(0, 0, 10, 10)) is None


def test_a_square_roof_fits_in_every_pose_and_the_one_drawn_as_it_is_wins():
    square = box(0, 0, 10, 10)
    reg = rv.register_roof(box(100, 50, 110, 60), square)
    assert reg is not None and reg.turns == 0 and reg.tied
    assert reg.shift == pytest.approx((-100.0, -50.0), abs=0.05)


def test_a_roof_that_fits_in_many_places_is_put_on_the_middle_of_the_building():
    reg = rv.register_roof(box(100, 50, 110, 54), box(0, 0, 14, 4))  # 10 m of roof over 14 m of house: any of 4 m of shifts
    assert reg is not None
    moved = rv.place(box(100, 50, 110, 54), reg)
    assert moved.centroid.x == pytest.approx(7.0, abs=0.3)


def test_a_roof_covering_a_small_part_of_a_long_building_is_not_placed():
    assert rv.register_roof(box(0, 0, 4, 4), box(0, 0, 30, 4)) is None


# --- the cleaning --------------------------------------------------------------------------------

def item(layer, geom, kind="line"):
    return Item(layer, None, "other", [Prim(geom, kind)])


def outline_lines(poly, layer="0"):
    return [item(layer, LineString(poly.exterior.coords))]


def roof_view(extra=()):
    """The outline of ROOF, a ridge along the wing, and what else a roof view holds (``extra``)."""
    return [*outline_lines(ROOF), item("0", LineString([(-0.6, 4.0), (12.6, 4.0)])), *extra]


def lines_of(clean):
    return [p.geom for it in clean.items for p in it.prims]


def total_length(clean):
    return sum(l.length for l in lines_of(clean))


def test_the_outline_and_the_ridge_are_what_is_left_of_a_plain_roof_view():
    clean = rv.clean_roof(roof_view())
    assert clean is not None
    assert clean.outline.symmetric_difference(ROOF).area < 0.01
    assert total_length(clean) == pytest.approx(ROOF.length + 13.2, abs=0.01)


def test_dimensions_texts_and_section_marks_are_not_roof_lines():
    extra = [item("Quote", LineString([(-0.6, -2.0), (12.6, -2.0)])), item("Testi", LineString([(3, 3), (3, 4)])),
             item("sezioni", LineString([(6.0, 1.0), (6.0, 6.0)])), item("SEZIONI-A", LineString([(2.0, 1.0), (2.0, 6.0)]))]
    clean = rv.clean_roof(roof_view(extra))
    assert total_length(clean) == pytest.approx(ROOF.length + 13.2, abs=0.01)


def test_a_layer_of_hundreds_of_short_lines_is_a_hatch_drawn_with_lines_and_goes():
    tiles = [item("tegole", LineString([(x * 0.1, y), (x * 0.1 + 0.08, y + 0.3)])) for x in range(30) for y in (1.0, 2.0, 3.0)]
    clean = rv.clean_roof(roof_view(tiles))
    assert len(lines_of(clean)) == len(lines_of(rv.clean_roof(roof_view())))


def test_a_few_short_lines_of_a_layer_are_kept_when_they_are_no_pattern():
    hip = [item("0", LineString([(0.0, 0.0), (2.0, 4.0)])), item("0", LineString([(12.0, 0.0), (10.0, 4.0)]))]
    assert len(lines_of(rv.clean_roof(roof_view(hip)))) > len(lines_of(rv.clean_roof(roof_view())))


def test_a_pattern_layer_names_itself():
    """A layer called after a hatch is dropped by its name, however few lines it holds."""
    clean = rv.clean_roof(roof_view([item("TRATTEGGIO_tetto", LineString([(1, 1), (1, 3)]))]))
    assert total_length(clean) == pytest.approx(ROOF.length + 13.2, abs=0.01)


def test_the_gutter_strips_along_the_eaves_are_not_planes():
    strips = [item("0", Polygon([(-0.6, -0.6), (12.6, -0.6), (12.6, -0.45), (-0.6, -0.45)]), "ring")]
    clean = rv.clean_roof(roof_view(strips))
    assert total_length(clean) == pytest.approx(ROOF.length + 13.2, abs=0.01)


def test_skylights_chimneys_and_panels_are_no_planes():
    small = [item("0", box(2, 1, 2.5, 1.5), "ring"), item("0", box(8, 5, 9, 6), "ring"),
             item("0", LineString([(3, 2), (3.3, 2), (3.3, 2.3), (3, 2.3), (3, 2)]))]  # the last one an open polyline closed on itself
    clean = rv.clean_roof(roof_view(small))
    assert total_length(clean) == pytest.approx(ROOF.length + 13.2, abs=0.01)


def test_an_array_of_panels_standing_in_a_plane_is_not_a_plane_of_the_roof():
    """Big enough to be a roof (4 m2 and more), but it lies in the plane of the ridge's south side, touching nothing."""
    array = [item("0", box(2, 0.5, 7, 3.5), "ring")]
    clean = rv.clean_roof(roof_view(array))
    assert total_length(clean) == pytest.approx(ROOF.length + 13.2, abs=0.01)


def test_a_section_line_that_goes_beyond_the_outline_is_no_ridge():
    extra = [item("0", LineString([(6.0, -3.0), (6.0, 16.0)])), item("0", LineString([(-3.0, 2.0), (15.0, 2.0)]))]
    clean = rv.clean_roof(roof_view(extra))
    assert total_length(clean) == pytest.approx(ROOF.length + 13.2, abs=0.01)


def test_arrows_and_leaders_that_end_in_the_air_are_dropped():
    arrow = [item("0", LineString([(3.0, 6.0), (3.0, 5.0)])), item("0", LineString([(3.0, 5.0), (2.8, 5.3)])),
             item("0", LineString([(3.0, 5.0), (3.2, 5.3)]))]
    clean = rv.clean_roof(roof_view(arrow))
    assert total_length(clean) == pytest.approx(ROOF.length + 13.2, abs=0.01)


def test_the_wall_line_under_the_eaves_is_dropped_and_tells_the_overhang():
    wall = [item("0", LineString(HOUSE.exterior.coords))]
    clean = rv.clean_roof(roof_view(wall))
    assert clean.overhang == pytest.approx(0.6, abs=0.01)
    assert total_length(clean) == pytest.approx(ROOF.length + 13.2, abs=0.01)


def test_a_loop_that_is_not_inside_the_outline_all_round_is_no_eaves_line():
    partial = [item("0", box(2.0, 1.0, 10.0, 7.0), "ring")]  # an inside rectangle, not parallel to the outline everywhere
    assert rv.clean_roof(roof_view(partial)).overhang is None


def test_an_outline_drawn_with_four_lines_is_found_as_well_as_one_closed_polyline():
    house = box(0, 0, 10, 6)
    lines = [item("0", LineString(seg)) for seg in zip(list(house.exterior.coords)[:-1], list(house.exterior.coords)[1:])]
    lines.append(item("0", LineString([(0, 3), (10, 3)])))
    clean = rv.clean_roof(lines)
    assert clean.outline.symmetric_difference(house).area < 0.01
    assert total_length(clean) == pytest.approx(house.length + 10, abs=0.01)


def test_two_separate_roofs_in_one_view_are_both_kept():
    wing = box(30, 0, 40, 8)
    items = roof_view([*outline_lines(wing), item("0", LineString([(30, 4), (40, 4)]))])
    clean = rv.clean_roof(items)
    assert clean.outline.intersects(wing) and clean.outline.intersects(ROOF)
    assert total_length(clean) == pytest.approx(ROOF.length + 13.2 + wing.length + 10, abs=0.01)


def test_a_view_with_no_closed_plane_has_no_roof():
    assert rv.clean_roof([item("0", LineString([(0, 0), (10, 0)])), item("Quote", LineString([(0, 1), (10, 1)]))]) is None
    assert rv.clean_roof([]) is None
