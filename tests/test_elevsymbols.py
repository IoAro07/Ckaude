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
    assert (win.y0, win.y1) == pytest.approx((1.0, 2.4), abs=0.01)  # the sill is behind the railing, the shutters know it


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
