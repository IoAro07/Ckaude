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
    return list(getattr(clean.all_lines(), "geoms", [clean.all_lines()]))


def total_length(clean):
    return clean.all_lines().length


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


# --- the slope, the storeys, the notes ---------------------------------------------------------------

from dwg2c4d.config import Config  # noqa: E402
from dwg2c4d.elevation import Elevation  # noqa: E402
from dwg2c4d.roof import Roof, build_roof  # noqa: E402


@pytest.mark.parametrize("text,degrees", [
    ("Pendenza 25 %", 14.04), ("PENDENZA 25%", 14.04), ("pendenza falde 30 %", 16.7), ("Pend. 20 gradi", 20.0),
    ("pendenza: 35°", 35.0), ("Pendenza 12,5 %", 7.13), ("Pendenza 40 %", 21.8), ("slope pend 100 %", 45.0),
])
def test_a_slope_written_in_percent_or_degrees_is_read(text, degrees):
    assert rv.pitch_from_texts([text])[0] == pytest.approx(degrees, abs=0.1)


@pytest.mark.parametrize("text", ["Pendenza 2 %", "pendenza 0,5 %", "pendenza 90 gradi", "Pendenza scarico", "tetto 25 %", ""])
def test_a_text_that_is_no_roof_slope_is_not_read(text):
    assert rv.pitch_from_texts([text]) is None


def test_of_several_written_slopes_the_one_written_most_often_wins():
    deg, words = rv.pitch_from_texts(["Pendenza 25 %", "Pendenza 25 %", "Pendenza 35 %", "Pendenza 40 %"])
    assert deg == pytest.approx(14.04, abs=0.05) and words == "25 %"


def hip_items(tmp_overhang=0.0):
    body = box(0, 0, 12, 8)
    items = [item("Tetto", LineString(body.exterior.coords))]
    items.append(item("Tetto", LineString([(4, 4), (8, 4)])))
    for corner, end in (((0, 0), (4, 4)), ((0, 8), (4, 4)), ((12, 0), (8, 4)), ((12, 8), (8, 4))):
        items.append(item("Tetto", LineString([corner, end])))
    for it in items:
        it.category = "roof"
    return items


def test_ridge_heights_from_an_elevation_that_imply_a_wild_pitch_are_ignored_and_said():
    """The elevation shows two storeys: its ridge stands 8 m over the eaves of the one-storey plan, over a half span of 4 m: 63 degrees."""
    warnings = []
    roof = build_roof(hip_items(), Config(units="m", roof=True, roof_offset=(0.0, 0.0)), 1.0, (0, 0, 12, 8),
                      [(4.0, 8.0, 8.0)], warnings)
    assert roof is not None and roof.pitch_source == "predefinita"
    assert any("piu' ripide" in w for w in warnings)


def test_ridge_heights_from_an_elevation_that_make_a_sane_pitch_are_still_used():
    warnings = []
    roof = build_roof(hip_items(), Config(units="m", roof=True, roof_offset=(0.0, 0.0)), 1.0, (0, 0, 12, 8),
                      [(4.0, 8.0, 2.0)], warnings)
    assert roof.pitch_source == "prospetto" and roof.pitch_deg == pytest.approx(26.6, abs=0.2)


def fake_roof(ridge=2.0):
    return Roof([], [], [], 14.0, (14.0, 14.0), "scritta", ridge, 4, (0.0, 0.0), 0)


def fake_found():
    reg = rv.Registration(0, False, (-54.0, 0.0), 0.72, 0.6)
    return rv.ViewRoof([], [], 6, reg, 0.6, (16.9, 16.8), False)


def test_elevations_that_reach_far_above_the_roof_are_said_to_show_more_storeys():
    ev = Elevation("south", 0.0, "porta", [], [(8.1, 0.0, 12.0)])
    notes = rv.view_roof_notes(fake_found(), fake_roof(), None, [ev], 3.2)
    assert any("piu' piani" in n for n in notes)


def test_elevations_that_end_at_the_roof_say_nothing_about_storeys():
    ev = Elevation("south", 0.0, "porta", [], [(5.3, 0.0, 12.0)])
    assert not any("piu' piani" in n for n in rv.view_roof_notes(fake_found(), fake_roof(), None, [ev], 3.2))
    assert not any("piu' piani" in n for n in rv.view_roof_notes(fake_found(), fake_roof(), None, [], 3.2))


def test_the_notes_tell_where_the_roof_came_from_and_how_it_was_put_on_the_plan():
    note = rv.view_roof_notes(fake_found(), fake_roof(), (14.04, "25 %"), [], 3.2)
    assert "vista 6" in note[0] and "-54.00" in note[0] and "72 %" in note[0] and "0.60 m" in note[0]
    assert any("25 %" in n for n in note)


def test_the_rise_of_a_ridge_is_measured_from_the_eaves_of_the_storeys_over_the_plan():
    """Walls of 3.2 m, a second storey, its eaves at 6.2 m along the whole facade and the ridge at 8.0 m."""
    from dwg2c4d.roof import ridge_hints_from

    ev = Elevation("south", 0.0, "porta", [], [(3.2, 0.0, 16.9), (6.2, 0.0, 16.9), (8.0, 2.0, 13.7)])
    assert ridge_hints_from([ev], 3.2) == [(2.0, 13.7, pytest.approx(1.8))]


def test_the_ridge_of_a_one_storey_roof_is_measured_from_the_walls_as_ever():
    from dwg2c4d.roof import ridge_hints_from

    ev = Elevation("south", 0.0, "porta", [], [(3.2, 0.0, 12.0), (5.2, 3.0, 9.0)])
    assert ridge_hints_from([ev], 3.2) == [(3.0, 9.0, pytest.approx(2.0))]


def test_a_chimney_over_a_ridge_does_not_make_the_ridge_an_eaves_line():
    from dwg2c4d.roof import ridge_hints_from

    ev = Elevation("south", 0.0, "porta", [], [(3.2, 0.0, 12.0), (5.2, 0.0, 12.0), (6.5, 5.0, 5.5)])
    assert ridge_hints_from([ev], 3.2) == [(0.0, 12.0, pytest.approx(2.0))]


def test_of_two_poses_that_overlap_alike_the_one_where_every_body_of_the_roof_stands_on_the_building_wins():
    """A long main roof and a pavilion at one end, over a building that has a stump at its other end too: the two ends fit
    the main roof as well as each other (the overlap alone does not tell them apart), but only at one end the
    pavilion stands on walls."""
    building = box(0, 0, 120, 30).union(box(120, -2, 123, 32)).union(box(-3, 8, 0, 22))
    main, pavilion = box(0.3, 0.3, 119.7, 29.7), box(120.2, -1.8, 122.8, 31.8)
    roof = main.union(pavilion)
    drawn = drawn_elsewhere(roof, 3, False, (400.0, 300.0))
    reg = rv.fit_roof(drawn, building)
    assert reg is not None and reg.sound
    smallest = min(laid_back(drawn, reg).geoms, key=lambda g: g.area)
    assert smallest.centroid.x == pytest.approx(121.5, abs=1.0)  # the pavilion is at the end that has the walls
    assert not reg.alternatives


# --- flat roofs --------------------------------------------------------------------------------------

from dwg2c4d.roof import FlatBody, build_flat_roof  # noqa: E402


def test_a_roof_drawn_as_an_outline_alone_is_a_flat_roof():
    clean = rv.clean_roof(outline_lines(ROOF))
    assert clean.pitched == [] and len(clean.flat) == 1
    (body,) = rv.flat_bodies(clean)
    assert body.slab.symmetric_difference(ROOF).area < 0.05 and body.parapet is None


def test_planes_that_meet_along_a_ridge_are_a_pitched_roof_not_a_flat_one():
    clean = rv.clean_roof(roof_view())
    assert len(clean.pitched) == 2 and clean.flat == []


def test_a_plane_alone_under_a_hatch_of_tiles_is_a_pitched_roof_and_without_it_a_flat_one():
    centre = ROOF.centroid
    assert len(rv.clean_roof(outline_lines(ROOF), [(centre.x, centre.y)]).pitched) == 1
    assert len(rv.clean_roof(outline_lines(ROOF), [(centre.x + 200, centre.y)]).flat) == 1  # tiles of another roof


def test_a_strip_beside_a_roof_is_no_slope_of_it_and_two_flat_roofs_side_by_side_stay_flat():
    canopy, strip = box(0, 0, 19, 3.5), box(0, 3.5, 19, 4.4)  # a canopy and a 0.9 m strip along it
    lines = [*outline_lines(canopy), *outline_lines(strip)]
    clean = rv.clean_roof(lines)
    assert clean.pitched == [] and len(clean.flat) == 2


def parapet_roof(w=30.0, d=10.0, rim=0.25):
    """A flat roof drawn the way an architect draws one with a parapet: a double line along the edge."""
    return outline_lines(box(0, 0, w, d)) + outline_lines(box(rim, rim, w - rim, d - rim))


def test_the_double_line_along_the_edge_of_a_flat_roof_is_its_parapet():
    clean = rv.clean_roof(parapet_roof())
    assert clean.pitched == [] and clean.overhang is None
    (body,) = rv.flat_bodies(clean)
    assert body.slab.symmetric_difference(box(0, 0, 30, 10)).area < 0.5
    assert body.parapet is not None and body.parapet.area == pytest.approx(300 - 29.5 * 9.5, abs=0.5)


def test_a_wall_line_far_inside_a_pitched_roof_is_not_a_parapet():
    clean = rv.clean_roof(roof_view([item("0", LineString(HOUSE.exterior.coords))]))
    assert clean.overhang == pytest.approx(0.6, abs=0.01)
    assert all(b.parapet is None for b in rv.flat_bodies(clean))


def test_the_bays_of_a_flat_roof_with_double_lines_between_them_are_one_slab():
    lines = outline_lines(box(0, 0, 30, 10))
    for x in (9.875, 19.875):  # two ribs drawn as double lines 0.25 m apart across the roof, from edge to edge
        for dx in (0, 0.25):
            lines.append(item("0", LineString([(x + dx, 0), (x + dx, 10)])))
    clean = rv.clean_roof(lines)
    assert clean.pitched == [] and len(clean.flat) == 3
    (body,) = rv.flat_bodies(clean)
    assert body.slab.symmetric_difference(box(0, 0, 30, 10)).area < 0.5
    assert body.parapet is None  # the ribs run across the roof, not along the edge


def test_a_flat_roof_gets_a_slab_on_the_walls_and_a_parapet_of_the_default_height_when_the_plan_draws_one():
    bodies = rv.flat_bodies(rv.clean_roof(parapet_roof()))
    flat = build_flat_roof(bodies, [], 3.0)
    assert flat.thickness == pytest.approx(0.2) and flat.parapet is not None
    assert flat.parapet_height == pytest.approx(0.5) and flat.parapet_source == "presunta" and flat.bodies == 1


def test_the_parapet_is_as_high_as_the_long_line_the_elevation_draws_a_little_over_the_walls():
    ev = Elevation("south", 0.0, "porta", [], [(3.0, 0.0, 30.0), (3.9, 0.0, 30.0), (2.0, 5.0, 6.0)])
    flat = build_flat_roof(rv.flat_bodies(rv.clean_roof(parapet_roof())), [ev], 3.0)
    assert flat.parapet_height == pytest.approx(0.9 - 0.2) and flat.parapet_source == "prospetto"


def test_there_is_no_flat_roof_without_flat_bodies_and_no_parapet_without_the_double_line():
    assert build_flat_roof([], [], 3.0) is None
    flat = build_flat_roof([FlatBody(box(0, 0, 5, 5))], [], 3.0)
    assert flat.parapet is None
