"""What is not a wall on the layers taken for the walls: furniture, stairs, curbs, lots, solid blocks."""

import pytest
from builders import D, PLAN_AREA, W, building
from shapely.geometry import LineString, Polygon, box

from dwg2c4d import Config, convert
from dwg2c4d.cli import build_parser, config_from_args, main
from dwg2c4d.config import LayerRules
from dwg2c4d.falsewalls import (FalseWalls, drop_loose_outlines, drop_stair_steps, lower_far_walls, separate_prims,
                                split_low)
from dwg2c4d.geom import polygons_of
from dwg2c4d.reader import Item, Prim


# --- items (metres) ---------------------------------------------------------------------------

def ring(x0, y0, x1, y1):
    return Prim(box(x0, y0, x1, y1), "ring")


def line(x0, y0, x1, y1):
    return Prim(LineString([(x0, y0), (x1, y1)]), "line")


def fill(x0, y0, x1, y1):
    return Prim(box(x0, y0, x1, y1), "fill")


def wall(*prims, layer="MURI"):
    return Item(layer, None, "wall", list(prims))


def house():
    """A 10 x 6 m building: the outline of a big ring and the line of a wall across it."""
    return [wall(ring(0, 0, 10, 6)), wall(line(5, 0, 5, 6))]


def left(items):
    return [(it.category, p.kind) for it in items for p in it.prims]


# --- furniture --------------------------------------------------------------------------------

def test_a_small_closed_outline_that_touches_nothing_is_furniture():
    items = house() + [wall(ring(2, 2, 2.5, 2.6))]
    kept, false = drop_loose_outlines(items)
    assert len(left(kept)) == 2 and len(false.furniture) == 1
    out = false.furniture[0]
    assert (out.kind, round(out.x, 2), round(out.y, 2)) == ("arredo", 2.25, 2.3)
    assert false.columns.is_empty


def test_what_touches_a_wall_a_hatch_or_a_symbol_is_attached_and_stays():
    items = house() + [wall(ring(5, 2, 5.5, 2.6)),  # leans on the line of the wall
                       wall(ring(7, 2, 7.5, 2.6)), wall(fill(7.5, 2, 7.6, 2.6)),  # a hatch next to it
                       wall(ring(8, 3, 8.5, 3.6)), Item("PORTE", None, "door", [line(8.5, 3, 8.5, 3.6)])]  # a door symbol
    kept, false = drop_loose_outlines(items)
    assert len(left(kept)) == len(left(items)) and not false.furniture


def test_a_wall_drawn_as_a_chain_of_small_outlines_is_no_furniture():
    """A plan whose walls are one closed polyline per piece: every piece touches the next, none is furniture."""
    pieces = [wall(ring(x, 0, x + 0.6, 0.3)) for x in [i * 0.6 for i in range(8)]]
    kept, false = drop_loose_outlines(pieces)
    assert len(left(kept)) == 8 and not false.furniture


def test_a_row_of_chairs_and_the_squares_nested_in_a_seat_are_furniture_never_columns():
    seats = [wall(ring(x, 3, x + 0.5, 3.5), ring(x + 0.1, 3.1, x + 0.4, 3.4)) for x in (1.0, 1.7, 2.4)]
    kept, false = drop_loose_outlines(house() + seats)
    assert len(left(kept)) == 2 and false.columns.is_empty
    assert len(false.furniture) == 3  # three seats, three marks in the report


def test_lone_squares_are_columns_when_the_drawing_repeats_them_else_stool():
    one = house() + [wall(ring(2, 2, 2.4, 2.4))]
    assert not drop_loose_outlines(one)[1].columns.area and len(drop_loose_outlines(one)[1].furniture) == 1
    two = one + [wall(ring(8, 2, 8.4, 2.4))]  # another one of the same size, far away: a grid of columns
    kept, false = drop_loose_outlines(two)
    assert len(polygons_of(false.columns)) == 2 and not false.furniture and len(left(kept)) == 2
    odd = one + [wall(ring(8, 2, 8.6, 2.6))]  # another size: neither is repeated
    assert not drop_loose_outlines(odd)[1].columns.area


def test_a_square_the_column_layer_repeats_is_a_column_too():
    items = house() + [wall(ring(2, 2, 2.4, 2.4)), Item("PILASTRI", None, "column", [ring(8, 2, 8.4, 2.4)])]
    assert len(polygons_of(drop_loose_outlines(items)[1].columns)) == 1


def test_a_column_of_20_cm_is_a_column_whatever_the_rounding_of_the_drawing():
    sides = [0.19999999999708962, 0.2000000000007276]  # as the 20 x 20 squares of a real sheet come out
    items = house() + [wall(ring(x, 3, x + s, 3 + s)) for x, s in ((2, sides[0]), (7, sides[1]))]
    assert len(polygons_of(drop_loose_outlines(items)[1].columns)) == 2


def test_big_or_long_groups_are_not_furniture():
    items = house() + [wall(ring(2, 1, 2.5, 4.5)),  # 3.5 m long
                       wall(ring(7, 1, 9.2, 3.2))]  # 4.8 m2
    assert len(left(drop_loose_outlines(items)[0])) == len(left(items))


def test_lines_of_a_group_go_with_it_and_a_long_line_makes_it_a_wall():
    kept, _ = drop_loose_outlines(house() + [wall(ring(2, 2, 2.5, 2.6), line(2.25, 2.6, 2.25, 3.0))])
    assert len(left(kept)) == 2
    kept, false = drop_loose_outlines(house() + [wall(ring(2, 2, 2.5, 2.6), line(2.25, 2.6, 2.25, 3.8))])
    assert len(left(kept)) == 4 and not false.furniture


def test_nothing_is_left_out_on_a_layer_the_names_vouch_for():
    items = house() + [wall(ring(2, 2, 2.5, 2.6), layer="TRAMEZZI"), wall(ring(4, 4, 4.5, 4.6)),
                       wall(line(4.5, 4.3, 4.9, 4.3), layer="TRAMEZZI")]
    kept, false = drop_loose_outlines(items, frozenset({"TRAMEZZI"}))
    assert len(left(kept)) == len(left(items)) and not false.furniture


# --- stairs -----------------------------------------------------------------------------------

def steps(n=7, x0=2.0, width=1.2, pitch=0.28, y0=1.0):
    return [wall(line(x0, y0 + k * pitch, x0 + width, y0 + k * pitch)) for k in range(n)]


def test_a_row_of_equal_parallel_lines_a_step_apart_is_a_stair_and_leaves_no_block():
    items = house() + steps() + [wall(line(2.6, 1.0, 2.6, 2.9))]  # a rail down the middle
    stairs = FalseWalls()
    kept = drop_stair_steps(items, stairs)
    assert left(kept) == left(house())
    assert len(stairs.stairs) == 1 and stairs.stairs[0].steps == 7 and stairs.stairs[0].kind == "scala"
    assert stairs.stairs[0].width == pytest.approx(1.1, abs=0.2)


def test_a_step_drawn_twice_goes_with_the_row():
    rows = steps()
    twin = rows[3].prims[0]
    kept = drop_stair_steps(house() + rows + [wall(Prim(twin.geom, "line"))], FalseWalls())
    assert left(kept) == left(house())


def test_the_walls_at_the_sides_of_a_stair_stay():
    sides = [wall(line(1.95, 0.5, 1.95, 3.5)), wall(line(3.25, 0.5, 3.25, 3.5))]  # just outside the steps
    kept = drop_stair_steps(house() + steps() + sides, FalseWalls())
    assert len(left(kept)) == 2 + 2


@pytest.mark.parametrize("rows", [
    steps(4),  # too few
    steps(7, pitch=0.005),  # the lines of a glazing, a few millimetres apart
    steps(7, pitch=0.9),  # a row of tables
    [wall(line(2 + (k % 2) * 0.3, 1 + k * 0.28, 3.2 - (k % 2) * 0.3, 1 + k * 0.28)) for k in range(7)],  # lengths differ
    [wall(line(2 + (k % 3) * 0.4, 1 + k * 0.28, 3.2 + (k % 3) * 0.4, 1 + k * 0.28)) for k in range(7)],  # ends do not line up
    [wall(line(2, y, 3.2, y)) for y in (1.0, 1.2, 1.6, 1.8, 2.2, 2.4, 2.8)],  # uneven: 20 cm, then 40, then 20...
])
def test_what_is_not_a_row_of_steps_is_not_a_stair(rows):
    stairs = FalseWalls()
    kept = drop_stair_steps(house() + rows, stairs)
    assert len(left(kept)) == 2 + len(rows) and not stairs.stairs


def test_a_stair_turned_by_any_angle_is_found():
    import math

    c, s = math.cos(math.radians(30)), math.sin(math.radians(30))
    rows = [wall(Prim(LineString([(2 + x0 * c - y * s, 1 + x0 * s + y * c), (2 + x1 * c - y * s, 1 + x1 * s + y * c)]), "line"))
            for x0, x1, y in [(0, 1.2, k * 0.28) for k in range(7)]]
    stairs = FalseWalls()
    assert left(drop_stair_steps(house() + rows, stairs)) == left(house()) and stairs.stairs[0].steps == 7


def test_the_lines_of_a_partition_layer_are_never_steps():
    rows = steps()
    rows = [Item("TRAMEZZI", None, "wall", it.prims) for it in rows]
    assert len(left(drop_stair_steps(house() + rows, FalseWalls(), frozenset({"TRAMEZZI"})))) == 2 + 7


# --- curbs, lots, solid blocks (hatched drawings) --------------------------------------------

def hatched_house():
    """A building whose walls are marked with hatches: 30 cm walls, 8 x 5 m."""
    t = 0.3
    return [wall(fill(0, 0, 8, t), fill(0, 5 - t, 8, 5), fill(0, t, t, 5 - t), fill(8 - t, t, 8, 5 - t))]


def walls_of(items, *extra):
    from dwg2c4d.geom import union

    return union([p.geom for it in items for p in it.prims] + list(extra))


def test_what_lies_outside_the_envelope_of_the_hatched_walls_is_low():
    curb = box(-2, -6, 10, -5.9)  # a curb 10 cm thick, 12 m long, 6 m south of the house
    items = hatched_house()
    walls = walls_of(items, curb)
    false = FalseWalls()
    rest = lower_far_walls(walls, items, false)
    assert false.low.area == pytest.approx(curb.area, rel=0.05) and rest.area == pytest.approx(walls.area - curb.area, rel=0.01)
    assert len(false.lowered) == 1 and false.lowered[0].kind == "basso"
    thin, thick = split_low(false.low)
    assert thin.area > 0 and thick.is_empty  # 10 cm: a curb


def test_the_walls_of_the_building_itself_and_pieces_with_a_door_or_window_stay():
    items = hatched_house() + [Item("PORTE", None, "door", [line(9.0, -6.0, 9.0, -5.9)])]
    walls = walls_of(hatched_house(), box(-2, -6, 10, -5.9))
    false = FalseWalls()
    rest = lower_far_walls(walls, items, false)
    assert false.low.is_empty and rest.area == pytest.approx(walls.area)  # the curb carries a door: it is a wall


def test_a_drawing_without_hatches_is_never_sorted_by_them():
    items = [wall(ring(0, 0, 8, 5), ring(0.3, 0.3, 7.7, 4.7)), wall(ring(-2, -6, 10, -5.9))]
    walls = walls_of(items)
    false = FalseWalls()
    assert lower_far_walls(walls, items, false).area == pytest.approx(walls.area) and false.low.is_empty


def test_when_most_of_the_walls_would_go_low_the_hatches_are_no_evidence():
    items = hatched_house()
    far = box(-30, -30, -5, -29.5)  # 12 m2 of walls beside the 7.4 m2 of hatches: more than half would be lowered
    walls = walls_of(items, far)
    false = FalseWalls()
    assert lower_far_walls(walls, items, false).area == pytest.approx(walls.area) and false.low.is_empty
    walls = walls_of(items, box(-30, -30, -20, -29.5))  # 5 m2: that is not much, it is lowered
    assert lower_far_walls(walls, items, FalseWalls()).area == pytest.approx(walls.area - 5.0)


def test_a_long_thin_free_standing_body_in_a_hatched_drawing_is_low_but_not_when_it_encloses_a_room():
    items = hatched_house()
    inside = box(2, 1, 6, 1.1)  # a partition inside the building, touching nothing: inside the envelope
    free = box(9.0, 0, 9.1, 4.0)  # a thin wall beside the building, outside the margin, touching nothing
    false = FalseWalls()
    lower_far_walls(walls_of(items, free), items, false)
    assert false.low.area == pytest.approx(free.area)
    false = FalseWalls()
    lower_far_walls(walls_of(items, inside), items, false)
    assert false.low.is_empty  # in the building: kept whatever it touches


def test_with_few_hatches_only_the_free_standing_bodies_go_low_and_with_fewer_nothing_does():
    door = Item("PORTE", None, "door", [line(25, 0, 25, 2)])
    shed = box(20, 0, 30, 2)  # unhatched but with a door: a wall
    curb = box(-2, -6, 10, -5.9)  # free-standing
    items = hatched_house() + [door]
    for extra, share in ((shed, "some"), (box(20, 0, 60, 6), "few")):  # 7.4 m2 of hatch in 27 m2, then in 247 m2 of walls
        false = FalseWalls()
        walls = walls_of(items, extra, curb)
        rest = lower_far_walls(walls, items + [Item("PORTE", None, "door", [line(35, 0, 35, 2)])], false)
        if share == "some":
            assert false.low.area == pytest.approx(curb.area) and rest.area == pytest.approx(walls.area - curb.area)
        else:
            assert false.low.is_empty and rest.area == pytest.approx(walls.area)


def test_a_free_standing_room_is_no_curb_however_thin_its_walls():
    door = Item("PORTE", None, "door", [line(25, 0, 25, 2)])
    items = hatched_house() + [door]
    shed = box(20, 0, 30, 2)  # with a door: keeps the share of the hatches in the middle band
    room = box(-12, 0, -8, 4).difference(box(-11.9, 0.1, -8.1, 3.9))  # 40 cm thick walls around 14 m2, no hatch, no door
    straight = box(-12, -6, -6, -5.9)  # a thin wall in the open
    walls = walls_of(items, shed, room, straight)
    false = FalseWalls()
    rest = lower_far_walls(walls, items, false)
    assert false.low.area == pytest.approx(straight.area) and rest.area == pytest.approx(walls.area - straight.area)


def test_in_a_site_plan_the_hatches_are_not_needed_and_the_lot_is_no_room():
    room = box(-12, 0, -8, 4).difference(box(-11.9, 0.1, -8.1, 3.9))  # a hut: 14 m2 inside
    lot = box(30, -20, 90, 20).difference(box(30.1, -19.9, 89.9, 19.9))  # the boundary of the lot: 2300 m2 inside
    road = box(-5, -40, 60, -39.9)
    items = [Item("PORTE", None, "door", [line(-10, 0, -10, 0.1)])]  # the hut has a door: a wall
    walls = walls_of(items, room, lot, road)
    false = FalseWalls()
    assert lower_far_walls(walls, items, false).area == pytest.approx(walls.area)  # not a site plan: no hatch, no judgement
    rest = lower_far_walls(walls, items, false, site=True)
    assert false.low.area == pytest.approx(lot.area + road.area) and rest.area == pytest.approx(room.area)
    items = [it for it in items if it.category != "door"]  # without its door it is a hut with a roof: a room, still
    false = FalseWalls()
    assert lower_far_walls(walls_of(items, room, road), items, false, site=True).area == pytest.approx(room.area)


def test_the_conversion_judges_a_site_plan_only_when_the_user_asked_for_it():
    from types import SimpleNamespace

    from dwg2c4d.pipeline import _read_walls

    items = [wall(ring(-12, 0, -8, 4), ring(-11.9, 0.1, -8.1, 3.9)),  # a hut
             wall(ring(30, -20, 90, 20), ring(30.1, -19.9, 89.9, 19.9)),  # the boundary of the lot
             Item("PORTE", None, "door", [line(-10, 0, -10, 0.1)])]

    def read(kind, **cfg):
        analysis = SimpleNamespace(plan=SimpleNamespace(kind=kind, id=2))
        return _read_walls(items, Config(walls_guessed=True, view=2, analysis=analysis, **cfg), [])

    walls, _, false = read("site")
    assert false.low.area > 1 and walls.area < 3 and len(false.lowered) == 1  # the lot goes low, the hut stays
    assert read("plan")[2].low.is_empty  # a plan: no hatches, no judgement
    assert read("site", area=(0, 0, 1, 1))[2].low.is_empty  # where the building is was said


def test_a_thick_part_no_hatch_fills_is_a_block_and_low():
    items = hatched_house()
    block = box(2, 1, 3.4, 4.0)  # 1.4 m wide: the landing of a stair
    false = FalseWalls()
    rest = lower_far_walls(walls_of(items, block), items, false)
    assert false.low.area == pytest.approx(block.area, rel=0.05) and rest.area == pytest.approx(walls_of(items).area, rel=0.01)
    thin, thick = split_low(false.low)
    assert thick.area > 0 and thin.is_empty  # a low wall, not a curb


def test_what_the_layer_names_vouch_for_is_never_lowered():
    items = hatched_house()
    curb = box(-2, -6, 10, -5.9)
    false = FalseWalls()
    assert lower_far_walls(walls_of(items, curb), items, false, keep=curb).area == pytest.approx(walls_of(items, curb).area)
    assert false.low.is_empty


def test_the_report_says_how_many_and_how_to_undo_it():
    items, false = separate_prims(house() + steps() + [wall(ring(2, 4.5, 2.5, 5.1))])
    lower_far_walls(walls_of(hatched_house(), box(-2, -6, 10, -5.9)), hatched_house(), false)
    notes = " ".join(false.notes(0.5, 0.15))
    assert "1 gruppi di contorni chiusi" in notes and "1 scale (7 gradini" in notes and "1 elementi" in notes
    assert "--muri-tutti" in notes and "0.5 m (Muretti)" in notes and "0.15 m (Cordoli" in notes
    listing = "\n".join(false.listing(0.01, "cm"))
    assert "arredo" in listing and "scala" in listing and "basso" in listing and "7 gradini" in listing


# --- the conversion ---------------------------------------------------------------------------

def plan_with_furniture(tmp_path, extra=None):
    """The 10 x 6 m building of the tests, a bench and a stool in the room, and a curb of 10 cm on the south."""
    doc, msp = building()
    for e in list(msp.query("LWPOLYLINE[layer=='PORTE']")) + list(msp.query("LWPOLYLINE[layer=='FINESTRE']")):
        msp.delete_entity(e)
    a = {"layer": "MURI"}
    msp.add_lwpolyline([(300, 200), (480, 200), (480, 260), (300, 260)], close=True, dxfattribs=a)  # a bench
    msp.add_lwpolyline([(600, 300), (640, 300), (640, 340), (600, 340)], close=True, dxfattribs=a)  # a stool
    for e in extra or []:
        e(msp)
    path = tmp_path / "f.dxf"
    doc.saveas(path)
    return path


def run(path, tmp_path, name="o", **kw):
    return convert(path, tmp_path / f"{name}.obj", Config(area=PLAN_AREA, **kw))


def test_the_furniture_is_not_extruded_and_is_listed_in_the_report_file(tmp_path):
    path = plan_with_furniture(tmp_path)
    plain = run(path, tmp_path, "plain", walls_guessed=False)
    sorted_ = run(path, tmp_path, "sorted", walls_guessed=True)
    assert sorted_.wall_area_m2 == pytest.approx(plain.wall_area_m2 - 1.8 * 0.6 - 0.4 * 0.4, abs=0.2)
    assert len(sorted_.false_walls.furniture) == 2
    assert any("contorni chiusi piccoli" in w and "--muri-tutti" in w for w in sorted_.warnings)
    text = sorted_.report_path.read_text(encoding="utf-8")
    assert "MURI MESSI DA PARTE" in text and text.count("arredo (contorni chiusi non estrusi)") == 2
    assert "MURI MESSI DA PARTE" not in plain.report_path.read_text(encoding="utf-8")


def test_nothing_changes_when_the_user_names_the_walls_or_asks_for_all_of_them(tmp_path):
    path = plan_with_furniture(tmp_path)
    plain = run(path, tmp_path, walls_guessed=False)
    named = convert(path, tmp_path / "n.obj", Config(area=PLAN_AREA, auto=True, units="cm",
                                                    layers=LayerRules({"wall": ["MURI"]})))
    assert named.wall_area_m2 == pytest.approx(plain.wall_area_m2) and named.false_walls.empty
    everything = run(path, tmp_path, walls_guessed=True, separate_walls=False)
    assert everything.wall_area_m2 == pytest.approx(plain.wall_area_m2) and everything.false_walls.empty
    solid = run(path, tmp_path, walls_guessed=True, wall_mode="solid")  # a mode the user chose
    assert solid.false_walls.empty


def test_the_names_of_the_layers_alone_make_the_program_sort_the_walls(tmp_path):
    path = plan_with_furniture(tmp_path)
    rep = convert(path, tmp_path / "a.obj", Config(auto=True, units="cm"))
    assert len(rep.false_walls.furniture) == 2


def test_a_drawing_of_nothing_but_furniture_is_still_read(tmp_path):
    doc, msp = building()
    for e in list(msp.query("LWPOLYLINE")):
        msp.delete_entity(e)
    msp.add_lwpolyline([(0, 0), (60, 0), (60, 40), (0, 40)], close=True, dxfattribs={"layer": "MURI"})
    path = tmp_path / "x.dxf"
    doc.saveas(path)
    rep = convert(path, tmp_path / "x.obj", Config(walls_guessed=True))
    assert rep.wall_area_m2 > 0 and rep.false_walls.empty


def test_curbs_come_out_low_in_their_own_groups_and_the_walls_keep_their_height(tmp_path):
    from helpers import Obj

    doc, msp = building()
    for e in list(msp.query("LWPOLYLINE")):
        msp.delete_entity(e)
    h = msp.add_hatch(dxfattribs={"layer": "MURI"})  # the walls marked with a hatch
    h.paths.add_polyline_path([(0, 0), (W, 0), (W, D), (0, D)], is_closed=True)
    h.paths.add_polyline_path([(30, 30), (W - 30, 30), (W - 30, D - 30), (30, D - 30)], is_closed=True)
    msp.add_lwpolyline([(-200, -800), (1200, -800), (1200, -790), (-200, -790)], close=True, dxfattribs={"layer": "MURI"})
    path = tmp_path / "c.dxf"
    doc.saveas(path)
    rep = convert(path, tmp_path / "c.obj", Config(area=(-400, -1000, 1400, 800), walls_guessed=True, low_wall_height=0.6,
                                                   curb_height=0.2, floor_thickness=0.0))
    obj = Obj(tmp_path / "c.obj")
    assert "Cordoli" in rep.groups and "Muretti" not in rep.groups
    assert obj.bbox("Cordoli")[1][1] == pytest.approx(0.2) and obj.bbox("Muri")[1][1] == pytest.approx(2.7)
    assert any("alti 0.6 m (Muretti) o 0.2 m (Cordoli" in w for w in rep.warnings)
    assert rep.wall_area_m2 == pytest.approx(rep.plan.solid_walls.area) and rep.plan.low.area > 0


def test_the_partition_layers_the_names_vouch_for_are_never_lowered(tmp_path):
    def drawing(layer):
        doc, msp = building()
        for e in list(msp.query("LWPOLYLINE")):
            msp.delete_entity(e)
        if layer not in doc.layers:
            doc.layers.add(layer)
        h = msp.add_hatch(dxfattribs={"layer": "MURI"})
        h.paths.add_polyline_path([(0, 0), (W, 0), (W, D), (0, D)], is_closed=True)
        h.paths.add_polyline_path([(30, 30), (W - 30, 30), (W - 30, D - 30), (30, D - 30)], is_closed=True)
        msp.add_lwpolyline([(-200, -800), (1200, -800), (1200, -790), (-200, -790)], close=True, dxfattribs={"layer": layer})
        path = tmp_path / f"{layer}.dxf"
        doc.saveas(path)
        return path

    area = (-400, -1000, 1400, 800)
    assert convert(drawing("MURI"), tmp_path / "a.obj", Config(area=area, walls_guessed=True)).plan.low.area > 0
    kept = convert(drawing("TRAMEZZI"), tmp_path / "b.obj", Config(area=area, walls_guessed=True))
    assert kept.plan.low.is_empty and kept.false_walls.empty


def test_the_command_line_has_the_switches():
    args = build_parser().parse_args(["x.dxf", "--muri-tutti", "--altezza-muretti", "0.8", "--altezza-cordoli", "0.1"])
    cfg = config_from_args(args)
    assert cfg.separate_walls is False and cfg.low_wall_height == 0.8 and cfg.curb_height == 0.1
    assert config_from_args(build_parser().parse_args(["x.dxf"])).separate_walls is True
    assert main(["x.dxf", "--altezza-cordoli", "0"]) == 1  # a curb has a height
