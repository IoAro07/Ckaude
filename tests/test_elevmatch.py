"""Which facade of the plan an elevation shows, found by the row of its windows (no position, no layer names)."""

import math

import ezdxf
import pytest
from shapely.geometry import Polygon

from dwg2c4d import convert
from dwg2c4d.config import Config
from dwg2c4d.dwgfile import ConversionError
from dwg2c4d.elevation import Symbol
from types import SimpleNamespace

import numpy as np

from dwg2c4d.elevmatch import (_best_shift, _eaves, _extent_along, _fit_extent, _north_of_the_sheet, _storey_band, _storey_height, _unstack,
                               apply_match, facades_of, match_symbols, storey_of, storey_stated, title_side)
from dwg2c4d.openings import Opening


def opening(kind, x, y, width, axis=(1.0, 0.0), thickness=0.3):
    return Opening(kind, Polygon(), Polygon(), 0.9 if kind == "window" else 0.0, 2.1, None, axis=axis,
                   center=(x, y), width=width, thickness=thickness)


def rotated(o: Opening, angle: float, about=(0.0, 0.0)) -> Opening:
    c, s = math.cos(angle), math.sin(angle)
    x, y = o.center[0] - about[0], o.center[1] - about[1]
    return opening(o.kind, about[0] + c * x - s * y, about[1] + s * x + c * y, o.width,
                   (c * o.axis[0] - s * o.axis[1], s * o.axis[0] + c * o.axis[1]), o.thickness)


def symbols(centres, widths, y0=0.9, y1=2.2, kind="window"):
    return [Symbol(kind, c - w / 2, c + w / 2, y0, y1) for c, w in zip(centres, widths)]


# A 10 x 6 m house. South wall (y = 0) and north wall (y = 6) carry windows; the east wall (x = 10) and the west
# wall (x = 0) too. Every row is different so that the facade can be told from the row alone.
SOUTH = [(1.0, 1.2), (3.0, 0.9), (4.4, 0.9), (7.5, 1.6)]
NORTH = [(2.0, 0.9), (4.0, 1.2), (8.5, 0.9)]
EAST = [(1.5, 0.9), (3.3, 1.5), (5.0, 0.9)]
WEST = [(0.9, 1.2), (2.2, 0.9), (4.8, 0.9), (5.4, 0.9)]


def walls_of_the_house():
    """The four walls (30 cm) of the 10 x 6 m house, as one footprint."""
    ring = Polygon([(-0.15, -0.15), (10.15, -0.15), (10.15, 6.15), (-0.15, 6.15)],
                   [[(0.15, 0.15), (0.15, 5.85), (9.85, 5.85), (9.85, 0.15)]])
    return ring


def house():
    ops = [opening("window", x, 0.0, w) for x, w in SOUTH]
    ops += [opening("window", x, 6.0, w) for x, w in NORTH]
    ops += [opening("window", 10.0, y, w, axis=(0.0, 1.0)) for y, w in EAST]
    ops += [opening("window", 0.0, y, w, axis=(0.0, 1.0)) for y, w in WEST]
    return ops


def test_every_orientation_of_the_walls_gives_two_facades():
    facades = facades_of(house())
    assert len(facades) == 4
    assert sorted(f.side for f in facades) == ["est", "nord", "ovest", "sud"]
    south = next(f for f in facades if f.side == "sud")
    assert south.run == pytest.approx((1.0, 0.0)) and len(south.openings) == 4 + 3  # no walls given: all are listed


def test_an_opening_with_a_wall_in_front_of_it_counts_less():
    facades = facades_of(house(), walls_of_the_house())
    south = next(f for f in facades if f.side == "sud")
    seen = {round(fo.t, 2): fo.exposure for fo in south.openings}
    assert [seen[t] for t in (1.0, 3.0, 4.4, 7.5)] == [1.0] * 4  # the south wall's windows look south
    assert [fo.exposure for fo in south.openings if fo.opening.center[1] == 6.0] == [0.6] * 3  # the north wall's do not
    north = next(f for f in facades if f.side == "nord")
    assert [round(fo.t, 2) for fo in north.openings if fo.exposure == 1.0] == [-8.5, -4.0, -2.0]  # east to west


@pytest.mark.parametrize("side,row,flip", [("sud", SOUTH, False), ("nord", NORTH, True)])
def test_an_elevation_drawn_anywhere_finds_its_south_or_north_facade(side, row, flip):
    # seen from outside a north facade runs from east to west: the drawing's x runs the other way
    xs = [-(x) if flip else x for x, _ in row]
    shift_in_the_drawing = 4000.0 + (50.0 if flip else 0.0)
    found = match_symbols(symbols([x + shift_in_the_drawing for x in xs], [w for _, w in row]), facades_of(house()))
    assert found is not None and found.facade.side == side
    assert found.matched == len(row) and found.residual < 0.01


@pytest.mark.parametrize("side,row", [("est", EAST), ("ovest", WEST)])
def test_east_and_west_facades_run_along_y(side, row):
    # east: left to right is south to north (increasing y); west: north to south
    xs = [y if side == "est" else -y for y, _ in row]
    found = match_symbols(symbols([x - 31.0 for x in xs], [w for _, w in row]), facades_of(house(), walls_of_the_house()))
    assert found is not None and found.facade.side == side and found.matched == len(row)


def test_a_house_turned_by_30_degrees_is_matched_on_its_own_facades():
    angle = math.radians(30)
    ops = [rotated(o, angle) for o in house()]
    found = match_symbols(symbols([x + 777.0 for x, _ in SOUTH], [w for _, w in SOUTH]), facades_of(ops))
    assert found is not None and found.matched == 4
    assert found.facade.side == "a 300 gradi"  # the south facade of the house, turned by 30 degrees


def test_wider_elevation_symbols_with_shutters_still_meet_the_plan_opening():
    row = [(1.0, 1.83), (4.0, 1.20), (7.5, 0.60), (9.0, 1.83)]
    ops = [opening("door", x, 0.0, w) for x, w in row]
    drawn = symbols([x for x, _ in row], [3.30, 1.06, 0.60, 3.30])
    found = match_symbols(drawn, facades_of(ops))
    assert found is not None and found.matched == 4


def test_two_matches_are_chance_not_a_facade():
    ops = [opening("window", x, 0.0, 1.0) for x in (1.0, 4.0)]
    assert match_symbols(symbols([1.0, 4.0], [1.0, 1.0]), facades_of(ops)) is None  # 2 pairs: below MIN_PAIRS


def test_a_row_that_matches_nothing_is_left_alone():
    assert match_symbols(symbols([0.3, 2.9, 5.1, 6.4, 9.7], [1.0] * 5), facades_of(house())) is None


def test_symbols_of_other_storeys_do_not_stop_the_match():
    ground = [(x, 0.9) for x in (1.0, 3.0, 4.4, 7.5)]
    drawn = symbols([x + 100 for x, _ in ground], [0.9] * 4) + symbols([x + 100 + 0.35 for x in (2.0, 5.2, 6.1, 9.3)],
                                                                        [0.9] * 4, y0=4.2, y1=5.6)
    ops = [opening("window", x, 0.0, 0.9) for x, _ in ground]
    found = match_symbols(drawn, facades_of(ops))
    assert found is not None and found.matched >= 4


def test_a_symmetric_house_can_say_which_side_by_its_title_only():
    row = [1.0, 3.0, 7.0, 9.0]  # the same row on the south and on the north wall, symmetric: either facade fits
    ops = [opening("window", x, 0.0, 1.0) for x in row] + [opening("window", x, 6.0, 1.0) for x in row]
    drawn = symbols([x + 50.0 for x in row], [1.0] * 4)
    facades = facades_of(ops, walls_of_the_house())
    assert match_symbols(drawn, facades) is None  # the heights would land on the wrong wall half of the time
    assert match_symbols(drawn, facades, "PROSPETTO NORD") is None  # a title alone is not enough: the sheet may be turned
    titled = match_symbols(drawn, facades, "PROSPETTO NORD", rotation=0.0)
    assert titled is not None and titled.facade.side == "nord" and titled.by_title
    turned = match_symbols(drawn, facades, "PROSPETTO NORD", rotation=180.0)  # the north of this sheet points down
    assert turned is not None and turned.facade.side == "sud"


def test_the_same_row_read_from_either_side_of_one_wall_gives_the_same_heights():
    row = [1.0, 3.0, 7.0, 9.0]
    ops = [opening("window", x, 0.0, 1.0) for x in row]  # a single wall: facing south or north, same openings
    found = match_symbols(symbols([x + 50.0 for x in row], [1.0] * 4), facades_of(ops))
    assert found is not None and found.matched == 4


def test_the_outermost_opening_of_a_place_is_the_one_the_elevation_shows():
    outer = opening("window", 3.0, 0.0, 1.0)
    inner = opening("door", 3.0, 3.0, 1.0)  # an interior door behind it, same place along the facade
    ops = [opening("window", 1.0, 0.0, 1.0), outer, inner, opening("window", 5.0, 0.0, 1.0),
           opening("window", 8.0, 0.0, 1.0)]
    found = match_symbols(symbols([1.0, 3.0, 5.0, 8.0], [1.0] * 4), facades_of(ops))
    assert found is not None and found.facade.side == "sud"
    assert outer in [p.opening for p in found.pairs] and inner not in [p.opening for p in found.pairs]


def test_titles_and_storeys():
    assert title_side("PROSPETTO SUD") == "sud" and title_side("Prospetto nord-est") == "nord"
    assert title_side("PROSPETTO FRONTALE") is None and title_side("SEZIONE A-A") is None
    assert storey_of("PLANIMETRIA PIANO TERRA") == 0 and storey_of("PIANTA PIANO PRIMO") == 1
    assert storey_of("PIANTA 1° PIANO") == 1 and storey_of("") == 0
    assert storey_of("PIANTA PIANO SECONDO") == 2 and storey_of("PIANTA SEMINTERRATO") == -1


def test_a_storey_is_read_from_a_digit_but_not_from_a_scale_or_a_measure():
    assert [storey_of(t) for t in ("PIANTA PIANO 1", "PIANTA 2° PIANO", "PIANO 1° - SCALA 1:100", "1 PIANO")] == [1, 2, 1, 1]
    assert storey_of("PIANTA PIANO RIALZATO") == 0 and storey_stated("PIANTA PIANO RIALZATO") == 0
    # scales and measures are no storeys; the ground floor is only assumed when the title says nothing
    assert storey_stated("PIANTA SCALA 1:50 PIANO TERRA") == 0
    assert storey_stated("PIANTA 1:100") is None and storey_stated("SCALA 1:5 PIANTA") is None
    assert storey_stated("PIANTA 12 PIANO") is None and storey_stated("") is None


def test_apply_match_sets_the_sill_and_the_height_from_the_floor():
    ops = [opening("window", x, 0.0, 1.0) for x in (1.0, 3.0, 5.0, 8.0)]
    ops.append(opening("door", 9.5, 0.0, 0.9))
    drawn = symbols([1.0, 3.0, 5.0, 8.0], [1.0] * 4, y0=100.0 + 1.0, y1=100.0 + 2.4) + \
        symbols([9.5], [0.9], y0=100.0, y1=100.0 + 2.2, kind="door")
    found = match_symbols(drawn, facades_of(ops))
    assert found is not None and found.matched == 5
    cfg = Config(wall_height=3.0)
    assert apply_match(found, floor=100.0, cfg=cfg, warnings=[]) == 5
    assert [round(o.z0, 2) for o in ops[:4]] == [1.0] * 4 and [round(o.z1, 2) for o in ops[:4]] == [2.4] * 4
    assert ops[4].z0 == 0.0 and ops[4].z1 == pytest.approx(2.2)  # the door starts at the floor
    assert all(o.from_elevation for o in ops)


def test_apply_match_never_clips_below_a_usable_height_and_leaves_set_openings_alone():
    ops = [opening("window", x, 0.0, 1.0) for x in (1.0, 3.0, 5.0, 8.0)]
    ops[0].from_elevation, ops[0].z0, ops[0].z1 = True, 0.5, 1.5  # already set by a better elevation
    found = match_symbols(symbols([1.0, 3.0, 5.0, 8.0], [1.0] * 4, y0=1.0, y1=2.4), facades_of(ops))
    assert found is not None
    assert apply_match(found, floor=0.0, cfg=Config(wall_height=2.7), warnings=[]) == 3
    assert (ops[0].z0, ops[0].z1) == (0.5, 1.5) and ops[1].z1 == pytest.approx(2.4)


# --- the whole chain: a sheet with the plan and two elevations drawn elsewhere --------------------

S_ROW = [(150, 100), (360, 120), (600, 100), (840, 120)]  # windows of the south wall: centre x and width (cm)
S_DOOR = (480, 90)
N_ROW = [(330, 100), (540, 120), (760, 100)]
N_DOOR = (120, 90)


def _rect(msp, x0, y0, x1, y1, layer):
    msp.add_lwpolyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], close=True, dxfattribs={"layer": layer})


def sheet_with_free_elevations(path, with_north=True, turn_plan=False, tall=False, title="PIANTA PIANO TERRA",
                               aligned_south=False):
    """A 10 x 6 m plan (cm) and, 40 m to the right, the south elevation (windows 90-220 above the ground) and, further
    right, the north one (drawn from outside: its x runs from east to west; windows 110-240)."""
    doc = ezdxf.new("R2018", setup=True)
    doc.units = 5
    for name in ("MURI", "PORTE", "FINESTRE", "TESTI"):
        doc.layers.add(name)
    msp = doc.modelspace()

    def plan_rect(x0, y0, x1, y1, layer):  # the plan, turned a quarter turn counter-clockwise when asked
        pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        if turn_plan:
            pts = [(-y, x) for x, y in pts]
        msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": layer})

    plan_rect(0, 0, 1000, 600, "MURI")
    plan_rect(30, 30, 970, 570, "MURI")
    for c, w in S_ROW:
        plan_rect(c - w / 2, 0, c + w / 2, 30, "FINESTRE")
    plan_rect(S_DOOR[0] - 45, 0, S_DOOR[0] + 45, 30, "PORTE")
    for c, w in N_ROW:
        plan_rect(c - w / 2, 570, c + w / 2, 600, "FINESTRE")
    plan_rect(N_DOOR[0] - 45, 570, N_DOOR[0] + 45, 600, "PORTE")
    msp.add_text(title, height=20,
                 dxfattribs={"layer": "TESTI", "insert": (350, -150) if not turn_plan else (150, 350)})
    # the south elevation: x as in the plan plus 4000; ground at y = -3000
    ground = -3000
    if aligned_south:  # ... or straight below the plan, as before, on a layer that says what it is
        doc.layers.add("Prospetto Sud")
        below = -1500
        for c, w in S_ROW:
            _rect(msp, c - w / 2, below + 90, c + w / 2, below + 320, "FINESTRE")
        _rect(msp, S_DOOR[0] - 45, below, S_DOOR[0] + 45, below + 210, "PORTE")
        msp.add_lwpolyline([(-30, below), (1030, below), (1030, below + 500), (-30, below + 500)], close=True,
                           dxfattribs={"layer": "Prospetto Sud"})
    else:
        for c, w in S_ROW:
            _rect(msp, c + 4000 - w / 2, ground + (200 if tall else 90), c + 4000 + w / 2,
                  ground + (300 if tall else 220), "FINESTRE")
        if tall:  # the line that closes the wall at the top: 4.95 m above the floor, along the whole facade
            msp.add_line((3970, ground + 495), (5030, ground + 495), dxfattribs={"layer": "0"})
        _rect(msp, S_DOOR[0] + 4000 - 45, ground, S_DOOR[0] + 4000 + 45, ground + 210, "PORTE")
        msp.add_line((3900, ground), (5100, ground), dxfattribs={"layer": "0"})
        msp.add_text("PROSPETTO SUD", height=20, dxfattribs={"layer": "TESTI", "insert": (4350, ground - 150)})
    if with_north:  # the north elevation: left to right is east to west: x_e = 12000 - x_plan
        top = 300 if aligned_south and tall else 240
        for c, w in N_ROW:
            _rect(msp, 12000 - c - w / 2, ground + 110, 12000 - c + w / 2, ground + top, "FINESTRE")
        _rect(msp, 12000 - N_DOOR[0] - 45, ground, 12000 - N_DOOR[0] + 45, ground + 210, "PORTE")
        msp.add_line((10900, ground), (12100, ground), dxfattribs={"layer": "0"})
        if aligned_south and tall:
            msp.add_line((10970, ground + 495), (12030, ground + 495), dxfattribs={"layer": "0"})
        msp.add_text("PROSPETTO NORD", height=20, dxfattribs={"layer": "TESTI", "insert": (11350, ground - 150)})
    doc.saveas(path)
    return path


def test_elevations_drawn_elsewhere_give_the_sills_and_heights_of_their_facades(tmp_path):
    path = sheet_with_free_elevations(tmp_path / "free.dxf")
    report = convert(path, tmp_path / "f.obj", Config(auto=True, images=True))
    by_x = {}
    for o in report.openings:
        by_x[(round(o.center[0] * 100), "S" if o.center[1] < 3 else "N")] = o
    south = [by_x[(c, "S")] for c, _ in S_ROW]
    north = [by_x[(c, "N")] for c, _ in N_ROW]
    assert all(o.from_elevation and o.z0 == pytest.approx(0.9) and o.z1 == pytest.approx(2.2) for o in south)
    assert all(o.from_elevation and o.z0 == pytest.approx(1.1) and o.z1 == pytest.approx(2.4) for o in north)
    assert by_x[(S_DOOR[0], "S")].z0 == 0.0 and by_x[(S_DOOR[0], "S")].z1 == pytest.approx(2.1)
    sides = sorted(ev["side"] for ev in report.elevations)
    assert sides == ["nord", "sud"] and all(ev["view"] for ev in report.elevations)
    assert (tmp_path / "f_viste.png").exists()


def test_a_row_of_windows_that_fits_no_facade_is_reported_and_changes_nothing(tmp_path):
    path = sheet_with_free_elevations(tmp_path / "free2.dxf", with_north=False)
    doc = ezdxf.readfile(path)
    moves = iter([0.0, 55.0, -80.0, 120.0, 95.0])  # every symbol moved by another amount: no common shift remains
    for e in sorted((e for e in doc.modelspace() if e.dxftype() == "LWPOLYLINE" and e.dxf.layer in ("FINESTRE", "PORTE")
                     and min(p[0] for p in e.get_points()) > 3000), key=lambda e: min(p[0] for p in e.get_points())):
        e.translate(next(moves), 0, 0)
    doc.saveas(path)
    report = convert(path, tmp_path / "g.obj", Config(auto=True))
    assert not any(o.from_elevation for o in report.openings)
    assert any("non corrisponde a nessuna facciata" in w for w in report.warnings)


def test_the_north_of_the_sheet_comes_from_the_elevations_that_say_their_side():
    facades = facades_of(house(), walls_of_the_house())
    south = match_symbols(symbols([x + 4000 for x, _ in SOUTH], [w for _, w in SOUTH]), facades)
    north = match_symbols(symbols([-x + 12000 for x, _ in NORTH], [w for _, w in NORTH]), facades)
    assert south is not None and north is not None
    assert _north_of_the_sheet([(south, "sud"), (north, "nord")]) == pytest.approx(0.0, abs=1e-6)
    assert _north_of_the_sheet([(south, "nord"), (north, "sud")]) == pytest.approx(180.0, abs=1e-6)
    assert _north_of_the_sheet([(south, "sud"), (north, "sud")]) is None  # two titles that cannot both be right
    assert _north_of_the_sheet([]) is None


def test_a_plan_drawn_turned_on_the_sheet_still_gives_every_elevation_its_facade(tmp_path):
    path = sheet_with_free_elevations(tmp_path / "turned.dxf", turn_plan=True)
    report = convert(path, tmp_path / "t.obj", Config(auto=True))
    assert sorted(ev["side"] for ev in report.elevations) == ["nord", "sud"]  # the sheet's sides, not the drawing's
    assert all(ev["north_known"] for ev in report.elevations)
    heights = sorted((round(o.z0, 2), round(o.z1, 2)) for o in report.openings if o.from_elevation and o.kind == "window")
    assert heights == [(0.9, 2.2)] * 4 + [(1.1, 2.4)] * 3


def test_two_elevations_of_the_same_facade_set_it_once(tmp_path):
    path = sheet_with_free_elevations(tmp_path / "twice.dxf", with_north=False)
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()
    ground = -3000
    for c, w in S_ROW:  # a second drawing of the same south facade, further right, with other heights
        _rect(msp, c + 9000 - w / 2, ground + 150, c + 9000 + w / 2, ground + 250, "FINESTRE")
    _rect(msp, S_DOOR[0] + 9000 - 45, ground, S_DOOR[0] + 9000 + 45, ground + 210, "PORTE")
    msp.add_line((8900, ground), (10100, ground), dxfattribs={"layer": "0"})
    msp.add_text("PROSPETTO SUD", height=20, dxfattribs={"layer": "TESTI", "insert": (9350, ground - 150)})
    doc.saveas(path)
    report = convert(path, tmp_path / "w.obj", Config(auto=True))
    assert len(report.elevations) == 1  # the first one (best, then the other is the same facade again)
    assert any("stessa facciata" in w for w in report.warnings)


def test_windows_higher_than_the_walls_in_use_give_the_wall_height_from_the_eaves_line(tmp_path):
    path = sheet_with_free_elevations(tmp_path / "tall.dxf", with_north=False, tall=True)
    report = convert(path, tmp_path / "h.obj", Config(auto=True))
    assert report.wall_height == pytest.approx(4.95) and report.wall_height_source == "prospetto"
    windows = [o for o in report.openings if o.from_elevation and o.kind == "window"]
    assert len(windows) == 4 and all(o.z0 == pytest.approx(2.0) and o.z1 == pytest.approx(3.0) for o in windows)


def test_the_wall_height_written_or_given_is_never_replaced_by_the_elevation(tmp_path):
    path = sheet_with_free_elevations(tmp_path / "tall2.dxf", with_north=False, tall=True)
    report = convert(path, tmp_path / "i.obj", Config(auto=True, wall_height=3.2))
    assert report.wall_height == 3.2 and report.wall_height_source == "indicata"
    windows = [o for o in report.openings if o.from_elevation and o.kind == "window"]
    assert all(o.z1 == pytest.approx(3.0) for o in windows)


# --- the review: faces away, kinds, storeys, eaves, columns ------------------------------------

def test_an_opening_in_the_wall_that_faces_the_other_way_is_not_set_by_this_elevation():
    ops = house()
    facades = facades_of(ops, walls_of_the_house())
    south = next(f for f in facades if f.side == "sud")
    away = [fo for fo in south.openings if fo.opening.center[1] == 6.0]
    assert away and all(fo.away for fo in away) and not any(fo.away for fo in south.openings if fo.opening.center[1] == 0.0)
    # the row of the south wall, and one more symbol that stands where a north window is (x = 760: F with t = 7.6 -> 2.0)
    extra = [x for x, _ in NORTH][0]
    drawn = symbols([x + 50.0 for x, _ in SOUTH], [w for _, w in SOUTH], y0=1.0, y1=2.4) + \
        symbols([extra + 50.0], [0.9], y0=0.3, y1=1.0)
    found = match_symbols(drawn, [south])
    assert found is not None
    taken = [p.opening for p in found.pairs]
    north_window = next(fo.opening for fo in away if abs(fo.t - extra) < 1e-6)
    assert north_window in taken  # it fits the row, and counts towards the match...
    assert apply_match(found, floor=0.0, cfg=Config(wall_height=3.0), warnings=[]) == 4
    assert not north_window.from_elevation and len(found.settable) == 4  # ...but is not set from this elevation


def test_the_kinds_break_the_tie_between_bays_of_a_periodic_row():
    # windows every 2.5 m with one door; the elevation shows 'W D W W' with the first bay hidden behind a tree
    kinds = ["window", "window", "door", "window", "window", "window"]
    ops = [opening(k, 1.0 + 2.5 * i, 0.0, 0.9) for i, k in enumerate(kinds)]
    drawn = [Symbol(k, 1.0 + 2.5 * (i + 1) - 0.45 + 100, 1.0 + 2.5 * (i + 1) + 0.45 + 100, 0.0, 2.1)
             for i, k in enumerate(kinds[1:5])]
    found = _best_shift(drawn, next(f for f in facades_of(ops) if f.side == "sud"))
    assert found is not None
    shift, pairs = found
    assert all(p.symbol.kind == p.opening.kind for p in pairs) and len(pairs) == 4
    assert shift == pytest.approx(-100.0, abs=1e-6)  # symbols stand 100 m to the right of the plan, the first bay lost


def test_a_plan_below_the_ground_takes_nothing_from_the_elevations(tmp_path):
    path = sheet_with_free_elevations(tmp_path / "base.dxf", title="PIANTA PIANO INTERRATO")
    report = convert(path, tmp_path / "b.obj", Config(auto=True))
    assert not any(o.from_elevation for o in report.openings) and not report.elevations
    assert report.wall_height > 0
    assert any("interrato" in w for w in report.warnings)


def test_storey_below_the_ground_has_no_band_and_no_height():
    vs = SimpleNamespace(levels=[0.0, 3.0], symbols=symbols([1.0], [1.0]), floor=0.0, floor_source="porta", hlines=[])
    assert _storey_band(vs, -1) == ([], None, "")
    assert _storey_height(vs, 0.0, -1, vs.symbols, 10.0) is None


def test_the_eaves_line_is_the_first_long_line_above_the_windows_even_when_close():
    band = symbols([1.0, 3.0], [1.0, 1.0], y0=1.0, y1=2.7)
    vs = SimpleNamespace(hlines=[(2.9, 0.0, 8.0), (4.6, 0.0, 8.0)])  # the eaves 0.2 m above the window tops, then the ridge
    assert _eaves(vs, 0.0, band, 8.0) == pytest.approx(2.9)
    vs = SimpleNamespace(hlines=[(2.7, 0.0, 8.0), (4.6, 0.0, 8.0)])  # a line AT the window tops is their lintel, not the eaves
    assert _eaves(vs, 0.0, band, 8.0) == pytest.approx(4.6)


def test_symbols_of_two_storeys_one_above_the_other_without_level_marks_keep_the_lowest():
    ground = symbols([1.0, 3.0, 5.0], [0.9] * 3, y0=0.9, y1=2.1)
    upper = symbols([1.02, 3.0, 5.04], [0.9] * 3, y0=3.9, y1=5.1)
    kept, stacked = _unstack(ground + upper)
    assert stacked and [round(s.y0, 1) for s in kept] == [0.9] * 3
    kept, stacked = _unstack(ground)
    assert not stacked and len(kept) == 3


def test_a_wall_height_needs_a_sure_facade_or_two_elevations_that_agree(tmp_path):
    # a single elevation that is only 'media' may not move the wall height; one 'alta' may (the tall test above)
    path = sheet_with_free_elevations(tmp_path / "tall3.dxf", with_north=False, tall=True)
    report = convert(path, tmp_path / "h3.obj", Config(auto=True))
    assert any(ev["confidence"] == "alta" for ev in report.elevations) == (report.wall_height_source == "prospetto")


def test_an_elevation_of_another_size_than_the_building_is_not_its_facade():
    ops = [opening("window", x, 0.0, w) for x, w in SOUTH]
    found = match_symbols(symbols([x + 50.0 for x, _ in SOUTH], [w for _, w in SOUTH]), facades_of(ops))
    assert found is not None
    length = _extent_along(np.array([(0.0, 0.0), (10.3, 0.0), (10.3, 6.0)]), found.facade.run)
    assert length == pytest.approx(10.3)
    assert _fit_extent(found, 3.1 * length, length) is None and _fit_extent(found, 0.4 * length, length) is None
    found.confidence = "alta"
    assert _fit_extent(found, 1.2 * length, length).confidence == "alta"
    assert _fit_extent(found, 1.9 * length, length).confidence == "media"  # the right drawing is that wide, but it is doubtful
    assert _fit_extent(found, 5.0, None) is found  # the building is not known: nothing is judged


def test_aligned_openings_follow_the_wall_height_when_an_elevation_raises_it(tmp_path):
    path = sheet_with_free_elevations(tmp_path / "mixed.dxf", with_north=True, tall=True, aligned_south=True)
    report = convert(path, tmp_path / "m.obj", Config(auto=True))
    assert report.wall_height == pytest.approx(4.95) and report.wall_height_source == "prospetto"
    south = [o for o in report.openings if o.kind == "window" and o.center[1] < 3 and o.from_elevation]
    assert south and all(o.z1 == pytest.approx(3.2) for o in south)  # the aligned windows reach 3.2 m, not the old 2.7


def test_a_sheet_with_elevations_and_no_plan_says_so_instead_of_converting_junk(tmp_path):
    doc = ezdxf.new("R2018", setup=True)
    doc.units = 5
    for name in ("FINESTRE", "PORTE", "TESTI"):
        doc.layers.add(name)
    msp = doc.modelspace()
    for x, title in ((0, "PROSPETTO SUD"), (2500, "PROSPETTO NORD")):
        _rect(msp, x, 0, x + 1000, 600, "0")
        for c, w in S_ROW:
            _rect(msp, x + c - w / 2, 90, x + c + w / 2, 220, "FINESTRE")
        _rect(msp, x + 450, 0, x + 540, 210, "PORTE")
        msp.add_line((x - 50, 0), (x + 1050, 0), dxfattribs={"layer": "0"})
        msp.add_text(title, height=20, dxfattribs={"layer": "TESTI", "insert": (x + 350, -150)})
    path = tmp_path / "only_elevations.dxf"
    doc.saveas(path)
    with pytest.raises(ConversionError, match="nessuna e' la pianta di un edificio"):
        convert(path, tmp_path / "x.obj", Config(auto=True, images=True))
    assert (tmp_path / "x_viste.png").exists()  # the sheet as understood: the way to choose


def test_a_failure_in_the_matching_never_stops_the_conversion(tmp_path, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("dwg2c4d.pipeline.match_views", broken)
    path = sheet_with_free_elevations(tmp_path / "boom.dxf")
    report = convert(path, tmp_path / "boom.obj", Config(auto=True))
    assert report.wall_pieces > 0 and not any(o.from_elevation for o in report.openings)
    assert any("non usati" in w and "boom" in w for w in report.warnings)
