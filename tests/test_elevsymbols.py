"""Doors and windows of elevations drawn on any layer (synthetic drawings, metres)."""

import ezdxf
import pytest

from dwg2c4d import Config
from dwg2c4d.elevsymbols import FoundSymbol, ViewSymbols, read_view_symbols
from dwg2c4d.reader import read_items

AREA = (-2.0, -2.0, 22.0, 8.0)  # the view: a facade 20 m wide and 5 m tall
FLOOR = 0.0


def _doc():
    doc = ezdxf.new("R2018", setup=True)
    doc.units = 6
    return doc, doc.modelspace()


def _lines(msp, x0, y0, x1, y1, layer="0"):
    """A rectangle drawn as four loose lines."""
    for a, b in (((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)), ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))):
        msp.add_line(a, b, dxfattribs={"layer": layer})


def _ring(msp, x0, y0, x1, y1, layer="0"):
    """A rectangle drawn as one closed polyline."""
    msp.add_lwpolyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], close=True, dxfattribs={"layer": layer})


def _facade(msp, ground=True, roof=True):
    """The wall: the ground line, the two ends, the eaves."""
    if ground:
        msp.add_line((-1, FLOOR), (21, FLOOR))
    msp.add_line((0, FLOOR), (0, 5))
    msp.add_line((20, FLOOR), (20, 5))
    if roof:
        msp.add_line((-0.5, 5), (20.5, 5))


def _read(doc, cfg=None) -> ViewSymbols:
    return read_view_symbols(doc, cfg or Config(), AREA, "m", 1.0)


def _find(res, kind, cx, tol=0.05):
    found = [s for s in res.symbols if s.kind == kind and abs((s.x0 + s.x1) / 2 - cx) <= tol]
    assert len(found) == 1, [(s.kind, s.x0, s.x1) for s in res.symbols]
    return found[0]


def test_rectangles_of_lines_on_layer_zero_are_windows_and_doors():
    doc, msp = _doc()
    _facade(msp)
    _lines(msp, 3, 1.0, 4.2, 2.4)  # window, sill 1.0 above the floor
    _lines(msp, 6, 0, 7.0, 2.1)  # door, standing on the floor
    res = _read(doc)
    win, door = _find(res, "window", 3.6), _find(res, "door", 6.5)
    assert (win.x1 - win.x0, win.y0, win.y1) == pytest.approx((1.2, 1.0, 2.4), abs=0.01)
    assert (door.x1 - door.x0, door.y0, door.y1) == pytest.approx((1.0, 0.0, 2.1), abs=0.01)
    assert len(res.symbols) == 2
    assert isinstance(win, FoundSymbol) and win.source == "forma"


def test_nested_frames_collapse_to_the_outermost_and_keep_the_pane():
    doc, msp = _doc()
    _facade(msp)
    _lines(msp, 3, 1.0, 4.0, 2.0)  # frame
    _lines(msp, 3.06, 1.06, 3.94, 1.94)  # inner pane
    _ring(msp, 3.1, 1.1, 3.9, 1.9)  # one more
    _lines(msp, 6, 0, 7, 2.1)
    res = _read(doc)
    win = _find(res, "window", 3.5)
    assert (win.x0, win.x1, win.y0, win.y1) == pytest.approx((3.0, 4.0, 1.0, 2.0), abs=0.01)
    assert win.pane == pytest.approx((3.06, 1.06, 3.94, 1.94), abs=0.01)
    assert len(res.symbols) == 2


def test_the_same_rectangle_drawn_twice_is_one_symbol():
    doc, msp = _doc()
    _facade(msp)
    _lines(msp, 3, 1.0, 4.0, 2.0)
    _ring(msp, 3, 1.0, 4.0, 2.0)  # the same window as a polyline
    _lines(msp, 6, 0, 7, 2.1)
    assert len(_read(doc).symbols) == 2


def test_the_two_leaves_of_a_door_are_one_door():
    doc, msp = _doc()
    _facade(msp)
    _lines(msp, 6, 0, 6.8, 2.2)
    _lines(msp, 6.8, 0, 7.6, 2.2)
    res = _read(doc)
    assert len(res.symbols) == 1
    door = _find(res, "door", 6.8)
    assert door.x1 - door.x0 == pytest.approx(1.6, abs=0.01)


def test_stacked_panels_of_an_entrance_are_one_door():
    doc, msp = _doc()
    _facade(msp)
    for k in range(4):  # a glazed entrance of 2 x 4 panels, 0.5 m high each
        _lines(msp, 8, 0.5 * k, 9.2, 0.5 * (k + 1))
        _lines(msp, 9.2, 0.5 * k, 10.4, 0.5 * (k + 1))
    res = _read(doc)
    assert len(res.symbols) == 1
    assert (res.symbols[0].x1 - res.symbols[0].x0, res.symbols[0].y1) == pytest.approx((2.4, 2.0), abs=0.01)


def test_shutters_folded_beside_the_sashes_are_left_out_of_the_clear_width():
    doc, msp = _doc()
    _facade(msp)
    # shutter (a panel and its inset), two sashes with glazing bars, shutter
    for x0, x1 in ((3.0, 3.6), (4.2, 4.8)):
        _ring(msp, x0, 1.0, x1, 2.4)
        _ring(msp, x0 + 0.05, 1.05, x1 - 0.05, 2.35)
    for x0 in (3.6, 3.9, ):
        _ring(msp, x0, 1.0, x0 + 0.3, 2.4)
        _ring(msp, x0 + 0.04, 1.04, x0 + 0.26, 2.36)
        msp.add_line((x0 + 0.04, 1.7), (x0 + 0.26, 1.7))
    res = _read(doc)
    win = _find(res, "window", 3.9)
    assert (win.x1 - win.x0) == pytest.approx(0.6, abs=0.01)  # the sashes
    assert win.full_x == pytest.approx((3.0, 4.8), abs=0.01)  # with the shutters
    assert (win.y0, win.y1) == pytest.approx((1.0, 2.4), abs=0.01)


def test_a_window_complete_on_its_own_stays_apart_from_the_door_it_touches():
    doc, msp = _doc()
    _facade(msp)
    _lines(msp, 3, 0, 3.9, 2.2)  # door
    _lines(msp, 4.0, 1.0, 4.8, 2.2)  # window with the same lintel, 10 cm away
    _ring(msp, 4.05, 1.05, 4.75, 2.15)
    res = _read(doc)
    assert sorted(s.kind for s in res.symbols) == ["door", "window"]


def test_a_sash_cut_in_two_by_a_railing_in_front_still_joins_its_shutters():
    doc, msp = _doc()
    _facade(msp)
    for x0, x1 in ((3.0, 3.6), (4.2, 4.8)):  # the shutters, intact
        _ring(msp, x0, 1.0, x1, 2.4)
        _ring(msp, x0 + 0.05, 1.05, x1 - 0.05, 2.35)
    _lines(msp, 3.6, 1.0, 4.2, 2.4)  # the sash, loose lines ...
    msp.add_line((2.5, 1.6), (5.5, 1.6))  # ... cut by the railing
    res = _read(doc)
    assert len(res.symbols) == 1
    win = res.symbols[0]
    assert (win.y0, win.y1) == pytest.approx((1.0, 2.4), abs=0.01)  # the sill is behind the railing: shutters know it


def test_noise_is_not_an_opening():
    doc, msp = _doc()
    _facade(msp)
    _lines(msp, 3, 1.0, 4.0, 2.0)  # the window
    _lines(msp, 6, 0, 7, 2.1)  # the door that gives the floor
    _lines(msp, 13.8, 4.4, 19.5, 4.9)  # a strip of fascia under the eaves
    _lines(msp, 15, 0, 16, 0.4)  # a planter on the ground line
    _lines(msp, 17, 5.3, 17.8, 6.3)  # a chimney pot above the roof
    # a bay of the wall between two pilasters, lines that go on at its four corners
    msp.add_line((10, 0), (10, 5))
    msp.add_line((12.5, 0), (12.5, 5))
    msp.add_line((9, 3.4), (13.5, 3.4))
    res = _read(doc)
    assert sorted((s.kind, round((s.x0 + s.x1) / 2, 1)) for s in res.symbols) == [("door", 6.5), ("window", 3.5)]
    assert any("scartate" in n for n in res.notes)


def test_a_door_is_told_from_a_window_by_where_it_stands():
    doc, msp = _doc()
    _facade(msp)
    _lines(msp, 3, 0, 4.0, 2.2)  # on the floor and tall: a door
    _lines(msp, 6, 1.0, 7, 2.2)  # same height, sill above the floor: a window
    _lines(msp, 9, 0.0, 10, 1.2)  # not tall enough for a door
    res = _read(doc)
    assert [(s.kind, round(s.x0)) for s in res.symbols] == [("door", 3), ("window", 6), ("window", 9)] or \
           [(s.kind, round(s.x0)) for s in res.symbols] == [("door", 3), ("window", 6)]


def test_an_arched_head_makes_the_apex_the_top():
    doc, msp = _doc()
    _facade(msp)
    # a door 1.2 wide: the body, then a semicircular head over the springing line at 2.0
    _lines(msp, 5, 0, 6.2, 2.0)
    msp.add_arc((5.6, 2.0), 0.6, 0, 180)
    res = _read(doc)
    door = _find(res, "door", 5.6)
    assert door.arched and door.y1 == pytest.approx(2.6, abs=0.02)


def test_hatch_glass_is_kept_only_when_asked():
    doc, msp = _doc()
    _facade(msp)
    _lines(msp, 3, 1.0, 4.0, 2.0)
    hatch = msp.add_hatch(dxfattribs={"layer": "0"})
    hatch.paths.add_polyline_path([(3.06, 1.06), (3.94, 1.06), (3.94, 1.94), (3.06, 1.94)])
    plain = read_items(doc, Config(), area=None, ignore_veto=True, keep_other=True, unit="m")
    assert not any(p.kind == "fill" for it in plain.items for p in it.prims)
    wanted = read_items(doc, Config(), area=None, ignore_veto=True, keep_other=True, unit="m", keep_fills=True)
    assert any(p.kind == "fill" for it in wanted.items for p in it.prims)


def test_a_hatch_alone_is_a_window():
    doc, msp = _doc()
    _facade(msp)
    hatch = msp.add_hatch(dxfattribs={"layer": "0"})
    hatch.paths.add_polyline_path([(3, 1), (4, 1), (4, 2), (3, 2)])
    _lines(msp, 6, 0, 7, 2.1)
    res = _read(doc)
    assert (_find(res, "window", 3.5).y0, _find(res, "window", 3.5).y1) == pytest.approx((1.0, 2.0), abs=0.01)


# --- the floor ----------------------------------------------------------------------------------------------

def test_the_floor_is_the_line_a_level_mark_labels():
    doc, msp = _doc()
    _facade(msp, ground=False)
    msp.add_line((-1, 10.0 - 10.0 + 0.0), (21, 0.0))
    _lines(msp, 3, 1.0, 4.0, 2.0)
    msp.add_text("+0,00", dxfattribs={"height": 0.12, "insert": (12, 0.1), "layer": "Quote"})
    res = _read(doc)
    assert res.floor == pytest.approx(0.0, abs=0.01) and res.floor_source == "quota +0,00"
    assert any("Pavimento" in n for n in res.notes)


def test_a_mark_with_a_triangle_points_at_its_apex_and_a_negative_mark_is_a_ground_level():
    doc, msp = _doc()
    _facade(msp, ground=False)
    _lines(msp, 3, 1.0, 4.0, 2.0)
    # the terrain is 0.4 below the floor: "-0,40", the little triangle pointing at the line
    msp.add_lwpolyline([(1, -0.4), (20, -0.4)])
    msp.add_text("-0,40", dxfattribs={"height": 0.12, "insert": (10, -0.2)})
    msp.add_lwpolyline([(10.6, -0.28), (10.76, -0.28), (10.68, -0.4)], close=True)
    res = _read(doc)
    assert res.floor == pytest.approx(0.0, abs=0.01) and res.floor_source == "quota +0,00"


def test_marks_of_the_other_storeys_give_the_levels():
    doc, msp = _doc()
    _facade(msp)
    msp.add_line((0, 3.2), (20, 3.2))  # the slab
    _lines(msp, 3, 1.0, 4.0, 2.0)
    msp.add_text("±0,00", dxfattribs={"height": 0.12, "insert": (12, 0.1)})
    msp.add_text("+3,20", dxfattribs={"height": 0.12, "insert": (12, 3.3)})
    res = _read(doc)
    assert res.floor == pytest.approx(0.0, abs=0.01)
    assert res.levels == pytest.approx([0.0, 3.2], abs=0.01)


def test_without_marks_the_floor_is_the_bottom_of_the_lowest_door():
    doc, msp = _doc()
    _facade(msp, ground=False)
    msp.add_line((0, 0.1), (20, 0.1))  # the ground line is a little under
    _lines(msp, 3, 1.0, 4.0, 2.0)
    _lines(msp, 6, 0.25, 7, 2.3)
    _lines(msp, 9, 0.25, 10, 2.3)
    res = _read(doc)
    assert res.floor == pytest.approx(0.25, abs=0.01) and res.floor_source == "porta"
    assert _find(res, "door", 6.5).y0 == pytest.approx(0.25, abs=0.01)


def test_a_double_ground_line_gives_its_top_when_there_is_no_door():
    doc, msp = _doc()
    _facade(msp, ground=False)
    msp.add_line((-1, -0.1), (21, -0.1))
    msp.add_line((-1, 0.0), (21, 0.0))
    _lines(msp, 3, 2.0, 4.0, 3.0)
    res = _read(doc)
    assert res.floor == pytest.approx(0.0, abs=0.01) and res.floor_source == "linea di terra"
    assert _find(res, "window", 3.5).kind == "window"


def test_the_eaves_are_not_taken_for_the_ground_line():
    doc, msp = _doc()
    _facade(msp, ground=False)  # the roof line is the only long line, above the window
    _lines(msp, 3, 1.0, 2.0, 2.0)
    res = _read(doc)
    assert res.floor is None and res.floor_source == ""
    assert any("non determinabile" in n for n in res.notes)


def test_the_floor_can_come_from_the_foot_of_a_person_figure():
    doc, msp = _doc()
    _facade(msp, ground=False)
    fig = doc.blocks.new("FIGURA")
    fig.add_lwpolyline([(-0.2, 0), (0.2, 0), (0.2, 1.75), (-0.2, 1.75)], close=True)
    msp.add_blockref("FIGURA", (12, 0.3))
    _lines(msp, 3, 1.3, 2.3, 2.3)
    res = _read(doc)
    assert res.floor == pytest.approx(0.3, abs=0.01) and res.floor_source == "blocco figura"


# --- layers that name their openings ---------------------------------------------------------------------------

def test_named_layers_stay_primary_and_the_frame_completes_the_pane():
    doc, msp = _doc()
    _facade(msp)
    doc.layers.add("FINESTRE")
    doc.layers.add("PORTE")
    _ring(msp, 3.06, 1.06, 3.94, 1.94, "FINESTRE")  # the glass only, named
    _lines(msp, 3, 1.0, 4.0, 2.0)  # the frame, on layer 0
    _ring(msp, 6, 0, 7, 2.1, "PORTE")  # a door that is only named
    _lines(msp, 9, 1.0, 10, 2.0)  # a window nobody named
    res = _read(doc)
    named = _find(res, "window", 3.5)
    assert (named.x0, named.x1, named.y0, named.y1) == pytest.approx((3.0, 4.0, 1.0, 2.0), abs=0.01)
    assert named.source == "layer+forma"
    door = _find(res, "door", 6.5)
    assert door.source in ("layer", "layer+forma") and door.y0 == pytest.approx(0.0, abs=0.01)
    assert _find(res, "window", 9.5).source == "forma"
    assert len(res.symbols) == 3


def test_a_named_door_keeps_its_kind_even_where_its_shape_looks_like_a_window():
    doc, msp = _doc()
    _facade(msp)
    doc.layers.add("PORTE")
    _lines(msp, 3, 1.2, 4.0, 2.0, "PORTE")  # a small hatch door, named, sill above the floor
    res = _read(doc)
    assert _find(res, "door", 3.5).kind == "door"


def test_one_layer_for_every_opening_is_told_apart_by_where_it_stands():
    doc, msp = _doc()
    _facade(msp)
    doc.layers.add("INFISSI")
    _lines(msp, 3, 1.0, 4.0, 2.0, "INFISSI")
    _lines(msp, 6, 0, 7, 2.1, "INFISSI")
    msp.add_text("+0,00", dxfattribs={"height": 0.12, "insert": (12, 0.1)})
    res = _read(doc)
    assert _find(res, "window", 3.5).kind == "window" and _find(res, "door", 6.5).kind == "door"


def test_the_roof_silhouette_lines_are_returned():
    doc, msp = _doc()
    _facade(msp)
    msp.add_line((0, 5), (10, 5.6))  # a slope: not a horizontal line
    msp.add_line((2, 6.4), (18, 6.4))  # a ridge
    _lines(msp, 3, 1.0, 4.0, 2.0)
    res = _read(doc)
    ys = [round(y, 2) for y, a, b in res.hlines]
    assert 6.4 in ys and 5.0 in ys
    assert all(b - a >= 0.5 for _, a, b in res.hlines)
    assert (6.4, 2.0, 18.0) in [(round(y, 2), a, b) for y, a, b in res.hlines]


def test_only_what_lies_inside_the_view_is_read():
    doc, msp = _doc()
    _facade(msp)
    _lines(msp, 3, 1.0, 4.0, 2.0)
    _lines(msp, 40, 1.0, 41.0, 2.0)  # another drawing, beside this one
    assert len(_read(doc).symbols) == 1


def test_a_drawing_in_centimetres_gives_metres():
    doc, msp = _doc()
    for a, b in (((-100, 0), (2100, 0)), ((0, 0), (0, 500)), ((2000, 0), (2000, 500)), ((-50, 500), (2050, 500))):
        msp.add_line(a, b)
    _lines(msp, 300, 100, 420, 240)
    _lines(msp, 600, 0, 700, 210)
    res = read_view_symbols(doc, Config(), (-200, -200, 2200, 800), "cm", 0.01)
    win = _find(res, "window", 3.6)
    assert (win.x1 - win.x0, win.y0, win.y1) == pytest.approx((1.2, 1.0, 2.4), abs=0.01)
    assert res.floor == pytest.approx(0.0, abs=0.01)


def test_an_empty_view_is_not_an_error():
    doc, _ = _doc()
    res = _read(doc)
    assert res.symbols == [] and res.floor is None and res.levels == []


# --- what the real sheets taught --------------------------------------------------------------------------------

def test_the_loops_of_a_vine_along_a_beam_are_not_an_arched_head():
    doc, msp = _doc()
    _facade(msp)
    msp.add_line((6, 0), (6, 5))  # two posts and a beam: a pergola, an empty bay and not an opening
    msp.add_line((12, 0), (12, 5))
    msp.add_line((5.5, 3.0), (12.5, 3.0))
    for k in range(40):
        msp.add_circle((6.1 + 0.15 * k, 3.06), 0.06)
    _lines(msp, 3, 1.0, 4.0, 2.0)
    res = _read(doc)
    assert [s.kind for s in res.symbols] == ["window"]


def test_a_stair_cutting_a_corner_does_not_make_an_arch():
    doc, msp = _doc()
    _facade(msp)
    # the face under a stair: a frame whose top right corner is cut by the stair's underside
    msp.add_lwpolyline([(6, 0), (7.6, 0), (7.6, 2.4), (7.0, 3.0), (6, 3.0)], close=True)
    for x in (6, 7.6):  # the posts of the frame go on past the corners: not a bare opening
        msp.add_line((x, -0.5), (x, 3.5))
    msp.add_line((5.5, 0.0), (8, 0.0))
    msp.add_line((5.5, 3.0), (8, 3.0))
    _lines(msp, 3, 1.0, 4.0, 2.0)
    res = _read(doc)
    assert [s.kind for s in res.symbols] == ["window"]


def test_a_shutter_trimmed_by_the_roof_is_still_a_shutter():
    doc, msp = _doc()
    _facade(msp)
    _ring(msp, 3.0, 1.0, 3.3, 2.4)  # left shutter and its inset
    _ring(msp, 3.04, 1.04, 3.26, 2.36)
    for x0 in (3.3, 3.6):  # two sashes with a glazing bar
        _ring(msp, x0, 1.0, x0 + 0.3, 2.4)
        _ring(msp, x0 + 0.04, 1.04, x0 + 0.26, 2.36)
        msp.add_line((x0 + 0.04, 1.7), (x0 + 0.26, 1.7))
    # right shutter, a corner cut
    msp.add_lwpolyline([(3.9, 1.0), (4.2, 1.0), (4.2, 2.0), (4.0, 2.4), (3.9, 2.4)], close=True)
    msp.add_lwpolyline([(3.94, 1.04), (4.16, 1.04), (4.16, 1.98), (3.98, 2.36), (3.94, 2.36)], close=True)
    res = _read(doc)
    win = res.symbols[0]
    assert len(res.symbols) == 1
    assert (win.x1 - win.x0) == pytest.approx(0.6, abs=0.01) and win.full_x == pytest.approx((3.0, 4.2), abs=0.01)


def test_a_chimney_stack_above_the_roof_is_not_a_window_but_a_gable_window_is():
    doc, msp = _doc()
    _facade(msp)
    msp.add_line((0, 5), (10, 6.6))  # the two slopes of a gable
    msp.add_line((10, 6.6), (20, 5))
    _lines(msp, 3, 1.0, 4.0, 2.0)
    _lines(msp, 9.5, 5.3, 10.5, 6.0)  # a window in the gable, under the ridge
    _lines(msp, 4.1, 5.7, 4.6, 6.2)  # a chimney on the slope: a stack with a cap line over it, no long line above
    msp.add_line((4.0, 6.4), (4.7, 6.4))
    res = _read(doc)
    assert sorted(round((s.x0 + s.x1) / 2, 1) for s in res.symbols) == [3.5, 10.0]


def test_a_named_window_keeps_its_own_width_when_the_shape_adds_a_shutter():
    doc, msp = _doc()
    _facade(msp)
    doc.layers.add("INFISSI")
    _ring(msp, 3.0, 1.0, 3.6, 2.4, "INFISSI")  # the sash, named
    _ring(msp, 3.05, 1.05, 3.55, 2.35, "INFISSI")
    _ring(msp, 3.6, 1.0, 4.2, 2.4)  # the shutter beside it, layer 0
    _ring(msp, 3.65, 1.05, 4.15, 2.35)
    _lines(msp, 6, 0, 7, 2.1)
    res = _read(doc)
    win = _find(res, "window", 3.3)
    assert (win.x0, win.x1) == pytest.approx((3.0, 3.6), abs=0.01) and win.full_x == pytest.approx((3.0, 4.2), abs=0.01)


def test_a_door_frame_open_at_the_bottom_is_found_whole():
    doc, msp = _doc()
    _facade(msp)
    # the frame and the leaf both stand on the floor line: the frame is no closed ring; the leaf is cut in slats
    msp.add_line((6, 0), (6, 2.15))
    msp.add_line((6.8, 0), (6.8, 2.15))
    msp.add_line((6, 2.15), (6.8, 2.15))
    for x in (6.05, 6.75):
        msp.add_line((x, 0), (x, 2.1))
    msp.add_line((6.05, 2.1), (6.75, 2.1))
    for k in range(1, 11):
        msp.add_line((6.05, 0.19 * k), (6.75, 0.19 * k))
    _lines(msp, 3, 1.0, 4.0, 2.0)
    res = _read(doc)
    door = _find(res, "door", 6.4)
    assert (door.x0, door.x1, door.y0, door.y1) == pytest.approx((6.0, 6.8, 0.0, 2.15), abs=0.01)


def test_a_railing_is_not_a_window():
    doc, msp = _doc()
    _facade(msp)
    _lines(msp, 3, 1.0, 4.0, 2.0)
    _ring(msp, 8, 3.0, 11, 4.0)  # a balcony: its panel, balusters every 11 cm (each drawn as two lines)
    for k in range(27):
        x = 8.07 + 0.11 * k
        msp.add_line((x, 3.05), (x, 3.95))
        msp.add_line((x + 0.01, 3.05), (x + 0.01, 3.95))
    res = _read(doc)
    assert [s.kind for s in res.symbols] == ["window"]


def test_slits_are_openings_but_small_squares_are_not():
    doc, msp = _doc()
    _facade(msp)
    _lines(msp, 3, 1.0, 4.0, 2.0)
    _lines(msp, 6, 1.5, 6.3, 2.4)  # a slit: 0.3 wide, 0.9 tall
    _lines(msp, 9, 1.5, 9.3, 1.86)  # a small square, 0.3 x 0.36
    res = _read(doc)
    assert sorted(round((s.x0 + s.x1) / 2, 2) for s in res.symbols) == [3.5, 6.15]


def test_an_arch_whose_foot_is_hidden_by_the_ground_is_not_a_planter():
    doc, msp = _doc()
    _facade(msp)
    _lines(msp, 3, 1.0, 4.0, 2.0)
    # an arcade arch seen above the ground: a semicircle on a short body, its bottom under the floor
    msp.add_lwpolyline([(8, -0.4), (9.6, -0.4), (9.6, 0.1, 0, 0, 1), (8, 0.1)], format="xyseb", close=True)
    res = _read(doc)
    arch = _find(res, "window", 8.8)
    assert arch.arched and arch.y0 == pytest.approx(-0.4, abs=0.02) and arch.y1 == pytest.approx(0.9, abs=0.02)


def test_pieces_of_a_named_window_are_not_more_windows():
    doc, msp = _doc()
    _facade(msp)
    doc.layers.add("FINESTRE")
    _ring(msp, 3.0, 1.0, 4.2, 2.4, "FINESTRE")
    _lines(msp, 3.4, 1.2, 3.9, 1.6)  # a leaf's fragment on layer 0, inside it
    _lines(msp, 6, 0, 7, 2.1)
    res = _read(doc)
    assert sorted(s.kind for s in res.symbols) == ["door", "window"]


# --- storeys ---------------------------------------------------------------------------------------------------

def _two_storeys(msp, slab=3.2, with_line=True):
    _facade(msp)
    if with_line:
        msp.add_line((0, slab), (20, slab))
    for x in (3, 8, 13):
        _lines(msp, x, 1.0, x + 1.0, 2.2)  # ground floor windows
        _lines(msp, x, slab + 0.5, x + 1.0, slab + 1.6)  # first floor windows


def test_two_rows_of_windows_with_a_slab_between_them_give_two_levels():
    doc, msp = _doc()
    _two_storeys(msp)
    res = _read(doc)
    assert res.floor == pytest.approx(0.0, abs=0.01) and res.floor_source == "linea di terra"
    assert res.levels == pytest.approx([0.0, 3.2], abs=0.01)


def test_no_levels_without_a_slab_line_or_when_a_window_crosses_it():
    doc, msp = _doc()
    _two_storeys(msp, with_line=False)
    assert _read(doc).levels == []
    doc, msp = _doc()
    _two_storeys(msp)
    _lines(msp, 16, 1.0, 17, 3.9)  # a tall window across the slab line
    assert _read(doc).levels == []


def test_a_short_line_between_two_rows_is_not_a_slab():
    doc, msp = _doc()
    _two_storeys(msp, with_line=False)
    msp.add_line((2, 3.2), (6, 3.2))  # a cornice over one window, not as long as half the building
    assert _read(doc).levels == []


# --- one reading of the sheet for all its views ----------------------------------------------------------------

def test_the_sheet_is_read_once_for_all_the_views_of_a_drawing(monkeypatch):
    from dwg2c4d import elevsymbols
    doc, msp = _doc()
    _facade(msp)
    _lines(msp, 3, 1.0, 4.0, 2.0)
    _lines(msp, 40, 1.0, 41.0, 2.0)  # another view, beside
    msp.add_line((38, 5), (43, 5))
    calls = []
    real = elevsymbols.read_items
    monkeypatch.setattr(elevsymbols, "read_items", lambda *a, **k: calls.append(1) or real(*a, **k))
    cfg = Config()
    first = read_view_symbols(doc, cfg, AREA, "m", 1.0)
    second = read_view_symbols(doc, cfg, (30.0, -2.0, 52.0, 8.0), "m", 1.0)
    assert len(calls) == 1 and len(first.symbols) == 1 and len(second.symbols) == 1
    read_view_symbols(doc, Config(units="cm"), AREA, "m", 1.0)  # other settings: read again
    assert len(calls) == 2


def test_the_sashes_of_a_tall_window_with_a_transom_rail_are_one_window():
    doc, msp = _doc()
    _facade(msp)
    _lines(msp, 3, 1.0, 3.8, 1.7)  # the lower sash and, above a 0.18 m rail, the upper one
    _lines(msp, 3, 1.88, 3.8, 2.6)
    _lines(msp, 8, 1.0, 8.8, 1.7)  # the windows of the next one stay apart: 0.6 m of wall between
    _lines(msp, 8, 2.3, 8.8, 3.0)
    res = _read(doc)
    tall = _find(res, "window", 3.4)
    assert (tall.y0, tall.y1) == pytest.approx((1.0, 2.6), abs=0.01)
    assert len([s for s in res.symbols if abs((s.x0 + s.x1) / 2 - 8.4) < 0.05]) == 2


def test_a_line_that_leaves_the_view_and_comes_back_does_not_break_it():
    doc, msp = _doc()
    _facade(msp)
    _lines(msp, 3, 1.0, 4.0, 2.0)
    msp.add_lwpolyline([(1, 7), (1, 10), (6, 10), (6, 7), (8, 7), (8, 10)])  # a zigzag above the view: in, out, in ...
    msp.add_lwpolyline([(-5, 7.5), (1, 7.5), (1, 9), (3, 9), (3, 7.5), (5, 7.5), (5, 9), (7, 9)])
    res = _read(doc)
    assert [s.kind for s in res.symbols] == ["window"]


# --- the floor and the storeys when the doors say a storey too high ----------------------------------------------

def _tall_facade(msp, height=7.0):
    """A two-storey wall: the ground line, the two ends, the eaves."""
    msp.add_line((-1, FLOOR), (21, FLOOR))
    msp.add_line((0, FLOOR), (0, height))
    msp.add_line((20, FLOOR), (20, height))
    msp.add_line((-0.5, height), (20.5, height))


def test_the_floor_is_the_ground_line_when_one_ground_door_sits_under_french_windows():
    doc, msp = _doc()
    _tall_facade(msp)
    _lines(msp, 1, 0, 2.1, 2.2)  # the only door on the ground
    for x in (6, 10.5):
        _lines(msp, x, 0.9, x + 1.2, 2.2)  # windows of the ground floor
    for k in range(4):
        _lines(msp, 1 + 4.5 * k, 3.2, 2.4 + 4.5 * k, 5.4)  # french windows of the floor above
    res = read_view_symbols(doc, Config(), (-2.0, -2.0, 24.0, 9.0), "m", 1.0)
    assert res.floor == pytest.approx(0.0, abs=0.01)  # the busiest level of door feet is the upper one: not the floor
    assert res.floor_source == "linea di terra"
    assert _find(res, "door", 1.55).y0 == pytest.approx(0.0, abs=0.01)


def test_the_floor_is_the_ground_line_when_the_only_doors_are_those_of_the_upper_storey():
    doc, msp = _doc()
    _tall_facade(msp)
    for x in (6, 10.5, 14):
        _lines(msp, x, 0.9, x + 1.2, 2.2)
    for k in range(2):
        _lines(msp, 1 + 4.5 * k, 3.2, 2.4 + 4.5 * k, 5.4)
    res = read_view_symbols(doc, Config(), (-2.0, -2.0, 24.0, 9.0), "m", 1.0)
    assert res.floor == pytest.approx(0.0, abs=0.01) and res.floor_source == "linea di terra"
    assert _find(res, "window", 6.6).y0 == pytest.approx(0.9, abs=0.01)  # the sill is above the floor, not clamped


def test_a_line_with_nothing_on_it_is_no_ground_for_the_doors_above():
    doc, msp = _doc()
    _facade(msp)
    _lines(msp, 3, 0, 4, 2.1)
    msp.add_line((-1, -2.4), (21, -2.4))  # the underline of a title, far under the building: no wall stands on it
    res = read_view_symbols(doc, Config(), (-2.0, -4.0, 22.0, 8.0), "m", 1.0)
    assert res.floor == pytest.approx(0.0, abs=0.01) and res.floor_source == "porta"


def _mark(msp, y, text, x=21.2):
    """A level mark: its text sits on a tick line to the right of the facade."""
    msp.add_line((x - 0.2, y), (x + 2.3, y))
    msp.add_text(text, dxfattribs={"height": 0.2, "insert": (x, y + 0.05)})


def _gable_house(msp, upper_row=False):
    """A wall 3.0 m to the eaves with a gable roof up to 5.5 m, ground floor openings, optionally a row above."""
    msp.add_line((-1, FLOOR), (21, FLOOR))
    msp.add_line((0, FLOOR), (0, 3.0))
    msp.add_line((20, FLOOR), (20, 3.0))
    msp.add_line((-0.5, 3.0), (20.5, 3.0))  # the eaves
    msp.add_line((-0.5, 3.0), (10, 5.5))
    msp.add_line((10, 5.5), (20.5, 3.0))
    _lines(msp, 3, 0, 4, 2.4)
    _lines(msp, 8, 0.9, 9.2, 2.3)
    _lines(msp, 12, 0, 13, 2.4)
    if upper_row:
        msp.add_line((0, 3.0), (20, 3.0))
        for x in (3, 8, 12):
            _lines(msp, x, 3.9, x + 1.0, 5.0)


def test_the_ridge_mark_of_a_gable_roof_is_no_floor():
    doc, msp = _doc()
    _gable_house(msp)
    _mark(msp, 0.0, "+0,00")
    _mark(msp, 5.5, "+5,50")  # the ridge: 5.5 m above the floor is no storey of this house
    res = read_view_symbols(doc, Config(), (-2.0, -2.0, 24.0, 8.0), "m", 1.0)
    assert res.floor == pytest.approx(0.0, abs=0.01) and res.levels == []


def test_a_mark_with_no_row_of_openings_above_it_is_no_floor_but_one_with_a_row_is():
    doc, msp = _doc()
    _gable_house(msp)
    _mark(msp, 0.0, "+0,00")
    _mark(msp, 2.4, "+2,40")  # the head of the doors: nothing stands on or above it
    res = read_view_symbols(doc, Config(), (-2.0, -2.0, 24.0, 8.0), "m", 1.0)
    assert res.levels == []
    doc, msp = _doc()
    _gable_house(msp, upper_row=True)
    _mark(msp, 0.0, "+0,00")
    _mark(msp, 3.0, "+3,00")
    res = read_view_symbols(doc, Config(), (-2.0, -2.0, 24.0, 8.0), "m", 1.0)
    assert res.levels == pytest.approx([0.0, 3.0], abs=0.01)


def test_floors_lie_between_two_and_a_half_and_four_and_a_half_metres_apart():
    doc, msp = _doc()
    msp.add_line((-1, FLOOR), (21, FLOOR))
    msp.add_line((0, FLOOR), (0, 12))
    msp.add_line((20, FLOOR), (20, 12))
    msp.add_line((-0.5, 12), (20.5, 12))
    for y in (3.2, 5.0, 8.4):  # a row of windows above each mark
        for x in (3, 8, 13):
            _lines(msp, x, y + 0.9, x + 1.0, y + 2.0)
    _lines(msp, 3, 0.9, 4, 2.0)
    for y, text in ((0.0, "+0,00"), (3.2, "+3,20"), (5.0, "+5,00"), (8.4, "+8,40")):
        _mark(msp, y, text)
    res = read_view_symbols(doc, Config(), (-2.0, -2.0, 24.0, 13.0), "m", 1.0)
    # +5,00 is only 1.8 m above +3,20 (a parapet, a landing); +8,40 is 5.2 m above it (a storey is missing)
    assert res.levels == pytest.approx([0.0, 3.2], abs=0.01)


def test_the_eaves_line_is_no_slab_for_its_mark_because_no_wall_goes_on_above_it():
    doc, msp = _doc()
    _gable_house(msp)
    _mark(msp, 0.0, "+0,00")
    _mark(msp, 3.0, "+3,00")  # the eaves: a line as long as the building, but the wall ends there
    res = read_view_symbols(doc, Config(), (-2.0, -2.0, 24.0, 8.0), "m", 1.0)
    assert res.floor == pytest.approx(0.0, abs=0.01) and res.levels == []


def test_a_mark_under_the_floor_does_not_hide_the_slab_between_two_rows_of_openings():
    doc, msp = _doc()
    _two_storeys(msp)
    _mark(msp, -0.4, "-0,40")  # the ground level: it says where the floor is, not a storey above it
    res = read_view_symbols(doc, Config(), (-2.0, -2.0, 24.0, 8.0), "m", 1.0)
    assert res.floor == pytest.approx(0.0, abs=0.01) and res.floor_source == "quota +0,00"
    assert res.levels == pytest.approx([0.0, 3.2], abs=0.01)


# --- opening marks on the sashes -----------------------------------------------------------------------------------

def _two_sashes_with_marks(msp, outline, mark, x0=3.0, y0=1.0, x1=4.2, y1=2.4):
    """A window of two sashes whose opening marks (V towards the free side, or X) run to the corners."""
    (_lines if outline == "lines" else _ring)(msp, x0, y0, x1, y1)
    xm, ym = (x0 + x1) / 2, (y0 + y1) / 2
    msp.add_line((xm, y0), (xm, y1))
    for a, b in ((x0, xm), (xm, x1)):
        if mark == "V":
            hinge, free = (a, b) if a == x0 else (b, a)
            msp.add_line((hinge, y0), (free, ym))
            msp.add_line((hinge, y1), (free, ym))
        else:
            msp.add_line((a, y0), (b, y1))
            msp.add_line((a, y1), (b, y0))


@pytest.mark.parametrize("outline", ["lines", "ring"])
@pytest.mark.parametrize("mark", ["V", "X"])
def test_a_window_whose_sashes_carry_opening_marks_is_found(outline, mark):
    doc, msp = _doc()
    _facade(msp)
    _two_sashes_with_marks(msp, outline, mark)
    _lines(msp, 6, 0, 7, 2.1)
    res = _read(doc)
    win = _find(res, "window", 3.6)
    assert (win.x0, win.x1, win.y0, win.y1) == pytest.approx((3.0, 4.2, 1.0, 2.4), abs=0.01)
    assert len(res.symbols) == 2


def test_a_bay_of_the_wall_with_a_brace_across_it_is_still_a_bay():
    doc, msp = _doc()
    _facade(msp)
    _lines(msp, 3, 1.0, 4.0, 2.0)
    msp.add_line((10, 0), (10, 5))  # two pilasters and a beam: the lines go on past the corners
    msp.add_line((12.5, 0), (12.5, 5))
    msp.add_line((9, 3.4), (13.5, 3.4))
    msp.add_line((10, 0.0), (12.5, 3.4))  # a diagonal brace across the bay
    res = _read(doc)
    assert [s.kind for s in res.symbols] == ["window"]


# --- shutters and sashes that are alike ----------------------------------------------------------------------------

def test_a_plain_pane_between_two_plain_shutters_is_the_window_and_the_shutters_fold_beside_it():
    doc, msp = _doc()
    _facade(msp)
    _lines(msp, 2.4, 1.0, 3.0, 2.4)
    _lines(msp, 3.0, 1.0, 4.2, 2.4)  # nothing inside it: every corner is a junction with the shutters' lines
    _lines(msp, 4.2, 1.0, 4.8, 2.4)
    _lines(msp, 6, 0, 7, 2.1)
    res = _read(doc)
    win = _find(res, "window", 3.6)
    assert (win.x0, win.x1) == pytest.approx((3.0, 4.2), abs=0.01)
    assert win.full_x == pytest.approx((2.4, 4.8), abs=0.01)
    assert len(res.symbols) == 2


def test_a_pane_with_one_inset_between_plain_shutters_is_the_window():
    doc, msp = _doc()
    _facade(msp)
    for x0, x1 in ((2.4, 3.0), (3.0, 4.2), (4.2, 4.8)):
        _ring(msp, x0, 1.0, x1, 2.4)
        _ring(msp, x0 + 0.05, 1.05, x1 - 0.05, 2.35)
    _lines(msp, 6, 0, 7, 2.1)
    win = _find(_read(doc), "window", 3.6)
    assert (win.x0, win.x1) == pytest.approx((3.0, 4.2), abs=0.01)
    assert win.full_x == pytest.approx((2.4, 4.8), abs=0.01)


def test_two_single_pane_sashes_between_louvred_shutters_are_one_window_of_their_own_width():
    doc, msp = _doc()
    _facade(msp)
    for x0 in (3.6, 4.2):  # the sashes: a leaf and its single pane
        _ring(msp, x0, 1.0, x0 + 0.6, 2.4)
        _ring(msp, x0 + 0.04, 1.04, x0 + 0.56, 2.36)
    for x0, draw in ((3.0, _lines), (4.8, _ring)):  # the shutters: slats every 0.2 m, in loose lines and in a ring
        draw(msp, x0, 1.0, x0 + 0.6, 2.4)
        for k in range(1, 7):
            msp.add_line((x0, 1.0 + 0.2 * k), (x0 + 0.6, 1.0 + 0.2 * k))
    _lines(msp, 8, 0, 9, 2.1)
    win = _find(_read(doc), "window", 4.2)
    assert (win.x0, win.x1) == pytest.approx((3.6, 4.8), abs=0.01)
    assert win.full_x == pytest.approx((3.0, 5.4), abs=0.01)


def test_three_alike_plain_sashes_are_one_window_not_a_window_between_shutters():
    doc, msp = _doc()
    _facade(msp)
    for x0 in (3.0, 3.6, 4.2):
        _ring(msp, x0, 1.0, x0 + 0.6, 2.4)
        _ring(msp, x0 + 0.04, 1.04, x0 + 0.56, 2.36)
    _lines(msp, 8, 0, 9, 2.1)
    win = _find(_read(doc), "window", 3.9)
    assert (win.x0, win.x1) == pytest.approx((3.0, 4.8), abs=0.01) and win.full_x is None


def test_four_sashes_with_their_glass_hatched_are_one_window_not_a_window_between_shutters():
    doc, msp = _doc()
    _facade(msp)
    for x0 in (3.0, 3.45, 3.9, 4.35):  # 0.45 m leaves alike: only a hatch says the outer ones hold glass
        _lines(msp, x0, 1.0, x0 + 0.45, 2.4)
        hatch = msp.add_hatch(dxfattribs={"layer": "0"})
        hatch.paths.add_polyline_path([(x0 + 0.05, 1.05), (x0 + 0.4, 1.05), (x0 + 0.4, 2.35), (x0 + 0.05, 2.35)])
    _lines(msp, 8, 0, 9, 2.1)
    win = _find(_read(doc), "window", 3.9)
    assert (win.x0, win.x1) == pytest.approx((3.0, 4.8), abs=0.01) and win.full_x is None


# --- blocks whose lines are on layer 0 -----------------------------------------------------------------------------

def _block_of_lines(doc, name, w, h):
    """A block whose rectangle is drawn on layer 0 (which inside a block means: the layer of the reference)."""
    block = doc.blocks.new(name)
    for a, b in (((0, 0), (w, 0)), ((w, 0), (w, h)), ((w, h), (0, h)), ((0, h), (0, 0))):
        block.add_line(a, b, dxfattribs={"layer": "0"})


def test_a_window_made_as_a_block_of_layer_zero_lines_is_found():
    doc, msp = _doc()
    _facade(msp)
    doc.layers.add("Quote")
    _block_of_lines(doc, "BLK120", 1.2, 1.4)
    msp.add_blockref("BLK120", (3, 1), dxfattribs={"layer": "0"})
    msp.add_blockref("BLK120", (8, 1), dxfattribs={"layer": "Quote"})
    _lines(msp, 14, 0, 15, 2.1)
    res = _read(doc)
    assert sorted(round(s.x0, 1) for s in res.symbols) == [3.0, 8.0, 14.0]


def test_the_level_mark_of_a_block_of_layer_zero_lines_gives_the_floor():
    doc, msp = _doc()
    doc.layers.add("Quote")
    msp.add_line((0, 0.4), (0, 5))
    msp.add_line((20, 0.4), (20, 5))
    msp.add_line((-0.5, 5), (20.5, 5))
    _lines(msp, 3, 1.7, 4.2, 3.0)
    block = doc.blocks.new("QUOTA")  # the tick and the text of a level mark, on layer 0 inside the block
    block.add_line((-0.3, 0), (1.0, 0), dxfattribs={"layer": "0"})
    block.add_text("+0,00", dxfattribs={"height": 0.2, "insert": (0.0, 0.05), "layer": "0"})
    msp.add_blockref("QUOTA", (21.0, 0.4), dxfattribs={"layer": "Quote"})
    res = read_view_symbols(doc, Config(), (-2.0, -2.0, 24.0, 8.0), "m", 1.0)
    assert res.floor == pytest.approx(0.4, abs=0.01) and res.floor_source == "quota +0,00"


def test_a_plan_still_reads_no_layer_zero_of_a_block():
    doc, msp = _doc()
    doc.layers.add("Layer1")
    leaf = doc.blocks.new("LEAF")  # a door leaf with its swing arc on layer 0, in a block on a layer with no meaning
    leaf.add_arc((0, 0), 0.9, 0, 90, dxfattribs={"layer": "0"})
    msp.add_blockref("LEAF", (14, 0), dxfattribs={"layer": "Layer1"})
    assert read_items(doc, Config(shape_openings=True), area=None, unit="m").items == []


def test_layer_zero_of_a_block_is_read_only_for_the_elevations_and_follows_the_layer_of_its_reference():
    doc, msp = _doc()
    doc.layers.add("Quote")
    doc.layers.add("Spenta").off()
    _block_of_lines(doc, "BLK", 1.2, 1.4)
    msp.add_blockref("BLK", (3, 1), dxfattribs={"layer": "Quote"})
    msp.add_blockref("BLK", (8, 1), dxfattribs={"layer": "Spenta"})  # its reference is on a layer that is switched off
    wanted = read_items(doc, Config(), area=None, ignore_veto=True, keep_other=True, unit="m")
    assert [(it.category, it.layer) for it in wanted.items] == [("other", "Quote")] * 4


def test_a_loose_cluster_of_boxes_is_not_one_opening_and_does_not_swallow_the_window_inside_it():
    doc, msp = _doc()
    _facade(msp)
    _lines(msp, 4.0, 0.5, 6.5, 1.3)  # a step, whose top goes on past its end ...
    msp.add_line((3.0, 1.3), (4.0, 1.3))
    _lines(msp, 6.5, 0.5, 7.2, 3.8)  # ... and a pier beside it: they touch, but leave most of the box they span empty
    _lines(msp, 4.8, 2.0, 5.8, 3.0)  # a window in the wall above the step
    res = _read(doc)
    win = _find(res, "window", 5.3)
    assert (win.x0, win.x1, win.y0, win.y1) == pytest.approx((4.8, 5.8, 2.0, 3.0), abs=0.01)
    assert all(s.x1 - s.x0 < 3.0 for s in res.symbols)
