"""The fence of the lot: a low wall with a cap, pillars, a railing, a gate gap, a wire fence. Synthetic sheets (centimetres): the 10 x 6 m
building of ``builders`` in a lot whose front wall (y = -800) has a gate, with a side wall at each side (x = -488 and 1488), pillars at the
corners and at both sides of the gate, a wire fence at the back (y = 700), and the front elevation drawn straight below the plan."""

import json

import ezdxf
import pytest
from builders import building, south_elevation
from helpers import Obj
from shapely.geometry import Polygon, box

from dwg2c4d import Config, convert
from dwg2c4d import lotfence as lf
from dwg2c4d.cli import main
from dwg2c4d.config import LayerRules
from dwg2c4d.qa import summary_lines

FRONT, BACK, LEFT, RIGHT = -800, 700, -488, 1488  # the front wall, the back line, the side walls (their centres)
GATE = (500, 610)  # the gap between the pillars of the gate
PILLARS = [(LEFT, FRONT), (RIGHT, FRONT), (488, FRONT), (622, FRONT), (LEFT, BACK), (RIGHT, BACK)]  # their centres
FRONT_RUNS = [(-476, 476), (634, 1476)]  # the front wall between the pillars
SIDE_RUN = (FRONT + 12, BACK - 12)  # the side walls between the corner pillars
GROUND = -2000  # the ground line of the elevation
ZONE = (-600, GROUND - 100, 1600, GROUND + 500)  # the elevation, in line with the plan


def line(msp, a, b, layer):
    msp.add_lwpolyline([a, b], dxfattribs={"layer": layer})


def square(msp, cx, cy, layer, half=12):
    msp.add_lwpolyline([(cx - half, cy - half), (cx + half, cy - half), (cx + half, cy + half), (cx - half, cy + half)], close=True,
                       dxfattribs={"layer": layer})


def lot_lines(msp, layer="Esterno", railing=True, pillars=True, wire=True, wall=True):
    """The lot of the sheet: pairs of lines 20 cm apart as the walls, pairs 2 cm apart along their middle as the railing (and across the
    gate), 24 cm squares as the pillars, a single line at the back."""
    if wall:
        for x0, x1 in FRONT_RUNS:
            for dy in (-10, 10):
                line(msp, (x0, FRONT + dy), (x1, FRONT + dy), layer)
        for cx in (LEFT, RIGHT):
            for dx in (-10, 10):
                line(msp, (cx + dx, SIDE_RUN[0]), (cx + dx, SIDE_RUN[1]), layer)
    if railing:
        for x0, x1 in [*FRONT_RUNS, GATE]:  # the leaf of the gate is drawn as a railing too
            for dy in (-1, 1):
                line(msp, (x0, FRONT + dy), (x1, FRONT + dy), layer)
        for cx in (LEFT, RIGHT):
            for dx in (-1, 1):
                line(msp, (cx + dx, SIDE_RUN[0]), (cx + dx, SIDE_RUN[1]), layer)
    if pillars:
        for cx, cy in PILLARS:
            square(msp, cx, cy, layer)
    if wire:
        line(msp, (LEFT + 12, BACK), (RIGHT - 12, BACK), layer)


def lot_elevation(msp, wall=77, cap=2, pillar=150, pillar_cap=2, rails=((15, 16), (46, 47), (62, 63)), pitch=10, bar=1.2,
                  layer="Prospetto Sud", move=(0, 0), sign=1):
    """The lot seen from the front, below the plan: the ground, the wall (its top and the bottom of its cap), the pillars with their caps, the
    rails and the bars of the railing. (The numbers are centimetres above the ground.) ``move``: the drawing shifted by this much, ``sign``
    -1: seen from behind (mirrored); both for an elevation that is not in line with the plan."""
    g = GROUND + move[1]

    def at(x, y):
        return sign * x + move[0], y

    def seg(a, b):
        line(msp, at(*a), at(*b), layer)

    def closed(points):
        msp.add_lwpolyline([at(*p) for p in points], close=True, dxfattribs={"layer": layer})

    seg((-520, g), (1520, g))
    for h in (wall, wall - cap):
        seg((-476, g + h), (1476, g + h))
    for cx, _ in PILLARS[:4]:
        closed([(cx - 10.5, g), (cx + 10.5, g), (cx + 10.5, g + pillar), (cx - 10.5, g + pillar)])
        closed([(cx - 12, g + pillar), (cx + 12, g + pillar), (cx + 12, g + pillar + pillar_cap), (cx - 12, g + pillar + pillar_cap)])
    for x0, x1 in FRONT_RUNS:
        for lo, hi in rails:
            for h in (lo, hi):
                seg((x0, g + wall + h), (x1, g + wall + h))
        x = x0 + pitch / 2.0
        while x < x1 - pitch / 2.0:
            for dx in (-bar / 2.0, bar / 2.0):
                seg((x + dx, g + wall + 5), (x + dx, g + wall + 57))
            x += pitch


def lot_sheet(tmp_path, name="lot", elevation=True, layer="Esterno", extra=None, **kw):
    """The sheet with the lot (and its front elevation): the path of the DXF."""
    doc, msp = building()
    for n in ("Esterno", "Muretto", "Recinzione", "Retini"):
        doc.layers.add(n)
    lot_lines(msp, layer=layer, **kw)
    if elevation:
        south_elevation(msp, ground_y=GROUND)  # the door of the house: the floor of the elevation
        lot_elevation(msp)
    if extra:
        extra(doc, msp)
    path = tmp_path / f"{name}.dxf"
    doc.saveas(path)
    return path


def run(tmp_path, path, **kw):
    kw.setdefault("fences", True)
    kw.setdefault("elevations", [ZONE])
    out = tmp_path / "out.obj"
    return convert(path, out, Config(**kw)), Obj(out)


def detect(tmp_path, name="d", **kw):
    """(the fence, the document) of the sheet as the program reads it before the walls: with the default configuration."""
    path = lot_sheet(tmp_path, name, elevation=False, **kw)
    doc = ezdxf.readfile(path)
    return lf.detect_lot_fence(doc, Config(fences=True), 0.01, []), doc


def only_lines(tmp_path, build, layer="Esterno", **cfg):
    """The fence found in a drawing of nothing but what ``build(msp, layer)`` draws."""
    doc = ezdxf.new("R2018", setup=True)
    doc.units = 5
    if not doc.layers.has_entry(layer):
        doc.layers.add(layer)
    build(doc.modelspace(), layer)
    return lf.detect_lot_fence(doc, Config(fences=True, **cfg), 0.01, [])


# --- the shapes of the plan --------------------------------------------------------------------------------------------

def test_the_walls_the_pillars_the_railing_and_the_gate_are_found_from_the_shapes(tmp_path):
    fence, _ = detect(tmp_path)
    assert fence is not None and len(fence.pillars) == 6
    wall, rail, _ = fence.lengths()
    assert wall == pytest.approx(9.52 + 8.42 + 2 * 14.76, abs=0.05)
    assert rail == pytest.approx(wall, abs=0.05)  # a railing along every wall; the leaf of the gate is not one
    assert len(fence.railings) == 4
    assert [round(g.width, 2) for g in fence.gates] == [1.10]
    assert fence.layers == {"Esterno"}
    assert fence.walls.intersection(box(-3.8, -8.1, 4.8, -7.9)).area == pytest.approx(8.6 * 0.20, abs=0.01)  # 20 cm thick strips


def test_a_pair_of_lines_alone_is_not_a_fence(tmp_path):
    """A kerb, a paving joint, the edge of a bed: two long parallel lines 20 cm apart prove nothing."""
    def pair(msp, layer):
        line(msp, (0, 0), (1000, 0), layer)
        line(msp, (0, 20), (1000, 20), layer)

    assert only_lines(tmp_path, pair) is None


@pytest.mark.parametrize("layer", ["MURI", "Porte", "Pilastri", "Arredo esterno", "Verde", "Prospetto Sud", "Tetto", "Quote"])
def test_the_layers_of_the_building_the_furniture_and_the_elevations_are_not_looked_at(tmp_path, layer):
    """The walls of the house are drawn as pairs of lines and its columns as squares too: they are the building, not the lot."""
    def wall(msp, name):
        line(msp, (0, 0), (1000, 0), name)
        line(msp, (0, 20), (1000, 20), name)
        square(msp, 1012, 10, name)

    assert only_lines(tmp_path, wall, layer=layer) is None


@pytest.mark.parametrize("layer", ["Esterno", "0", "Recinzione", "Confine lotto"])
def test_a_wall_with_a_pillar_is_a_fence_on_any_other_layer(tmp_path, layer):
    def wall(msp, name):
        line(msp, (0, 0), (1000, 0), name)
        line(msp, (0, 20), (1000, 20), name)
        square(msp, 1012, 10, name)

    assert only_lines(tmp_path, wall, layer=layer) is not None


def test_a_pair_of_lines_with_a_pillar_at_one_end_is_a_wall(tmp_path):
    def wall(msp, layer):
        line(msp, (0, 0), (1000, 0), layer)
        line(msp, (0, 20), (1000, 20), layer)
        square(msp, 1012, 10, layer)

    fence = only_lines(tmp_path, wall)
    assert fence is not None and len(fence.pillars) == 1 and fence.lengths()[0] == pytest.approx(10.0, abs=0.05)


def test_squares_alone_are_not_pillars_they_may_be_stools(tmp_path):
    def stools(msp, layer):
        for x in (0, 300, 600):
            square(msp, x, 0, layer)

    assert only_lines(tmp_path, stools) is None


def test_a_railing_along_a_wall_vouches_for_it(tmp_path):
    def wall(msp, layer):
        for dy in (0, 20):
            line(msp, (0, dy), (500, dy), layer)
        for dy in (9, 11):
            line(msp, (0, dy), (500, dy), layer)

    fence = only_lines(tmp_path, wall)
    assert fence is not None and fence.lengths()[0] == pytest.approx(5.0, abs=0.05) and len(fence.railings) == 1


def test_the_name_of_the_layer_vouches_for_a_pair_of_lines(tmp_path):
    def pair(msp, layer):
        line(msp, (0, 0), (1000, 0), layer)
        line(msp, (0, 20), (1000, 20), layer)

    assert only_lines(tmp_path, pair, layer="Recinzione lotto") is not None
    assert only_lines(tmp_path, pair, layer="Aiuola") is None


def test_what_the_user_said_about_a_layer_is_never_taken(tmp_path):
    def pair(msp, layer):
        line(msp, (0, 0), (1000, 0), layer)
        line(msp, (0, 20), (1000, 20), layer)

    assert only_lines(tmp_path, pair, layer="Muretto") is not None
    assert only_lines(tmp_path, pair, layer="Muretto", layers=LayerRules({"wall": ["Muretto"]})) is None  # --muri Muretto: a wall of the house


def test_a_curved_run_follows_the_curve(tmp_path):
    import math

    def curved(msp, layer):
        for r in (1000, 1020):
            msp.add_arc((0, 0), r, 0, 60, dxfattribs={"layer": layer})
        square(msp, 1010, 0, layer)  # at the end of the arc that starts at (r, 0)

    fence = only_lines(tmp_path, curved)
    assert fence is not None
    length = math.radians(60) * 10.1
    assert fence.lengths()[0] == pytest.approx(length, rel=0.03)
    assert fence.walls.area == pytest.approx(length * 0.20, rel=0.03)
    x0, y0, x1, y1 = fence.walls.bounds
    assert y1 > 8.0 and x1 > 10.0 and x0 < 5.3  # it turns: a straight strip would not reach both


def test_a_thin_closed_outline_is_a_wall_too(tmp_path):
    def outline(msp, layer):
        msp.add_lwpolyline([(0, 0), (600, 0), (600, 20), (0, 20)], close=True, dxfattribs={"layer": layer})
        square(msp, -12, 10, layer)

    fence = only_lines(tmp_path, outline)
    assert fence is not None and fence.lengths()[0] == pytest.approx(6.0, abs=0.05) and len(fence.pillars) == 1


def test_what_lies_in_the_building_is_no_fence(tmp_path):
    """The wall, the railing and the pillars the plan has in a (made up) building that holds the front of the lot are not the lot's."""
    fence, doc = detect(tmp_path, "inside")
    house = box(-6.0, -8.5, 16.0, -7.5)
    kept = lf.finish_lot_fence(fence, doc, Config(fences=True), "cm", house, [], [])
    assert kept is not None and len(kept.railings) == 2 and len(kept.pillars) == 2  # the sides, the back pillars
    assert kept.walls.intersection(house).area < 0.2 * 0.8 and kept.lengths()[0] == pytest.approx(2 * 14.76, abs=0.05)


def test_a_wire_fence_is_a_single_line_from_a_pillar_to_the_building_or_the_boundary(tmp_path):
    fence, doc = detect(tmp_path, "wire")
    kept = lf.finish_lot_fence(fence, doc, Config(fences=True), "cm", Polygon(), [], [])
    assert kept is not None and len(kept.wires) == 1 and kept.wires[0].length == pytest.approx(19.52, abs=0.05)
    other, doc = detect(tmp_path, "nowire", wire=False)
    assert lf.finish_lot_fence(other, doc, Config(fences=True), "cm", Polygon(), [], []).wires == []  # nothing drawn: nothing built


def test_a_single_line_that_touches_no_pillar_is_no_wire_fence(tmp_path):
    def build(msp, layer):
        lot_lines(msp, layer, wire=False)
        line(msp, (-300, 400), (900, 400), layer)  # a line in the lot: a path, a pipe, an axis

    doc = ezdxf.new("R2018", setup=True)
    doc.units = 5
    doc.layers.add("Esterno")
    build(doc.modelspace(), "Esterno")
    fence = lf.detect_lot_fence(doc, Config(fences=True), 0.01, [])
    assert lf.finish_lot_fence(fence, doc, Config(fences=True), "cm", Polygon(), [], []).wires == []


def test_a_single_long_line_on_a_layer_called_fence_is_a_wire_fence(tmp_path):
    def build(msp, layer):
        line(msp, (0, 0), (800, 0), layer)

    doc = ezdxf.new("R2018", setup=True)
    doc.units = 5
    doc.layers.add("Recinzione")
    build(doc.modelspace(), "Recinzione")
    fence = lf.detect_lot_fence(doc, Config(fences=True), 0.01, [])
    assert fence is None  # (nothing but a line: detect needs a wall, a railing or a pillar to start from)
    doc.modelspace().add_lwpolyline([(0, 100), (800, 100)], dxfattribs={"layer": "Recinzione"})
    doc.modelspace().add_lwpolyline([(0, 101.5), (800, 101.5)], dxfattribs={"layer": "Recinzione"})  # with a railing: found
    fence = lf.detect_lot_fence(doc, Config(fences=True), 0.01, [])
    kept = lf.finish_lot_fence(fence, doc, Config(fences=True), "cm", Polygon(), [], [])
    assert kept is not None and len(kept.railings) == 1 and len(kept.wires) == 1  # the single line is the wire fence


def test_two_pillars_with_a_wall_between_them_are_no_gate(tmp_path):
    def wall(msp, layer):
        for dy in (0, 20):
            line(msp, (0, dy), (1000, dy), layer)
        square(msp, -12, 10, layer)
        square(msp, 1012, 10, layer)
        square(msp, 500, 10, layer)  # a pillar in the middle: the wall runs through it

    assert only_lines(tmp_path, wall).gates == []


# --- the heights and the design from the elevation ---------------------------------------------------------------------

def elsewhere(tmp_path, move, sign=1, **kw):
    """(the fence, the profile read) when the lot's elevation is drawn at ``move`` (cm), mirrored when ``sign`` is -1, and not in line with the plan."""
    doc, msp = building()
    doc.layers.add("Esterno")
    lot_lines(msp)
    lot_elevation(msp, move=move, sign=sign, **kw)
    cfg = Config(fences=True)
    fence = lf.detect_lot_fence(doc, cfg, 0.01, [])
    xs = sorted(sign * x + move[0] for x in (-520, 1520))
    view = (xs[0] - 100, GROUND + move[1] - 100, xs[1] + 100, GROUND + move[1] + 500)
    kept = lf.finish_lot_fence(fence, doc, cfg, "cm", Polygon(), [], [], views=[view])
    return kept.profile


@pytest.mark.parametrize("move,sign", [((9000, -1500), 1), ((-7000, 3000), 1), ((20000, 0), -1)])
def test_an_elevation_drawn_elsewhere_is_laid_on_the_plan_by_its_pillars(tmp_path, move, sign):
    prof = elsewhere(tmp_path, move, sign)
    assert prof.wall == pytest.approx(0.77, abs=1e-6) and prof.pillar == pytest.approx(1.52, abs=1e-6)
    assert prof.rail == pytest.approx(0.63, abs=1e-6) and prof.pitch == pytest.approx(0.10, abs=1e-6)
    assert {prof.sources[k] for k in ("muretto", "pilastri", "ringhiera")} == {"prospetto"}


def test_a_wall_that_turns_a_corner_is_read_by_its_straight_sides(tmp_path):
    """One L-shaped polygon (two polylines drawn round the corner): it is taller than wide, but its long horizontal side shows in a front view."""
    def corner(msp, layer):
        msp.add_lwpolyline([(0, 600), (0, 0), (1000, 0)], dxfattribs={"layer": layer})
        msp.add_lwpolyline([(20, 600), (20, 20), (1000, 20)], dxfattribs={"layer": layer})
        square(msp, 1012, 10, layer)

    fence = only_lines(tmp_path, corner)
    assert len(lf.polygons_of(fence.walls)) == 1
    runs = lf._front_runs(fence.walls)
    assert sorted((round(r[0], 2), round(r[2], 2)) for r in runs) == [(0.0, 10.0), (0.2, 10.0)]  # the two long sides, not the box of the L


def test_an_elevation_drawn_elsewhere_whose_pillars_do_not_fit_is_not_used(tmp_path):
    """Pillars of another size or at other distances (the elevation of another lot, a different building): the defaults stay."""
    doc, msp = building()
    doc.layers.add("Esterno")
    lot_lines(msp)
    for cx in (0, 300, 900):  # three shafts that are not where the pillars of the plan are
        msp.add_lwpolyline([(cx + 6000 - 10.5, -2000), (cx + 6000 + 10.5, -2000), (cx + 6000 + 10.5, -1850), (cx + 6000 - 10.5, -1850)], close=True,
                           dxfattribs={"layer": "Prospetto Sud"})
    line(msp, (5900, -2000), (7000, -2000), "Prospetto Sud")
    cfg = Config(fences=True)
    fence = lf.detect_lot_fence(doc, cfg, 0.01, [])
    prof = lf.finish_lot_fence(fence, doc, cfg, "cm", Polygon(), [], [], views=[(5800, -2100, 7100, -1500)]).profile
    assert prof.sources["muretto"] == prof.sources["pilastri"] == "predefinita" and prof.wall == lf.DEFAULT_WALL


def test_heights_and_design_come_from_the_elevation(tmp_path):
    report, _ = run(tmp_path, lot_sheet(tmp_path))
    prof = report.lot.profile
    assert prof.wall == pytest.approx(0.77, abs=1e-6) and prof.cap == pytest.approx(0.02, abs=1e-6)
    assert prof.pillar == pytest.approx(1.52, abs=1e-6) and prof.pillar_cap == pytest.approx(0.02, abs=1e-6)
    assert prof.rail == pytest.approx(0.63, abs=1e-6) and prof.pitch == pytest.approx(0.10, abs=1e-6)
    assert prof.bar == pytest.approx(0.012, abs=1e-6)
    assert [(round(r.z0, 2), round(r.z1, 2)) for r in prof.rails] == [(0.15, 0.16), (0.46, 0.47), (0.62, 0.63)]
    assert {prof.sources[k] for k in ("muretto", "pilastri", "ringhiera")} == {"prospetto"} and prof.sources["rete"] == "predefinita"


def test_without_an_elevation_the_defaults_are_used_and_the_report_says_so(tmp_path):
    report, _ = run(tmp_path, lot_sheet(tmp_path, elevation=False), elevations=[])
    prof = report.lot.profile
    assert (prof.wall, prof.pillar, prof.rail, prof.pitch) == (lf.DEFAULT_WALL, lf.DEFAULT_WALL + lf.DEFAULT_PILLAR_RISE, lf.DEFAULT_RAIL, lf.DEFAULT_PITCH)
    line_ = next(l for l in summary_lines(report) if "Recinzione del lotto" in l)
    assert line_.count("predefinita") >= 4 and "prospetto" not in line_


def test_the_height_the_user_gives_beats_the_elevation_and_the_defaults(tmp_path):
    report, obj = run(tmp_path, lot_sheet(tmp_path), lot_wall_height=1.0)
    assert report.lot.profile.wall == 1.0 and report.lot.profile.sources["muretto"] == "indicata"
    assert obj.bbox("Copertina")[1][1] == pytest.approx(1.52, abs=1e-3)  # the pillars of the elevation stay as the elevation has them
    assert obj.bbox("Muretto")[1][1] == pytest.approx(1.0 - 0.02, abs=1e-3)
    with pytest.raises(ValueError):
        Config(lot_wall_height=-1.0).validate()


# --- what is built -------------------------------------------------------------------------------------------------------

def test_the_groups_and_their_heights(tmp_path):
    report, obj = run(tmp_path, lot_sheet(tmp_path))
    for name in ("Muretto", "Copertina", "Pilastri_lotto", "Ringhiera", "Recinzione_pali", "Recinzione_rete"):
        assert name in obj.groups, name
    assert obj.bbox("Muretto")[0][1] == pytest.approx(-0.20, abs=1e-6) and obj.bbox("Muretto")[1][1] == pytest.approx(0.75, abs=1e-6)
    assert obj.bbox("Copertina")[1][1] == pytest.approx(1.52, abs=1e-6)  # the cap of the pillars is the highest
    assert obj.bbox("Pilastri_lotto")[1][1] == pytest.approx(1.50, abs=1e-6)
    assert obj.bbox("Ringhiera")[1][1] == pytest.approx(0.77 + 0.63, abs=1e-6) and obj.bbox("Ringhiera")[0][1] == pytest.approx(0.77, abs=1e-6)
    assert obj.bbox("Recinzione_pali")[1][1] == pytest.approx(lf.DEFAULT_WIRE, abs=1e-6)
    for name in ("Muretto", "Pilastri_lotto"):
        assert obj.open_edges(name) == 0 and obj.winding_matches_normals(name), name


def test_the_cap_is_wider_than_the_wall_and_the_pillars_are_square_and_taller(tmp_path):
    report, obj = run(tmp_path, lot_sheet(tmp_path))
    walls, pillars = report.lot.walls, report.lot.pillars
    assert all(p.bounds[2] - p.bounds[0] == pytest.approx(0.24, abs=1e-6) and p.bounds[3] - p.bounds[1] == pytest.approx(0.24, abs=1e-6) for p in pillars)
    (x0, _), (x1, _) = obj.bbox("Copertina")[0][[0, 2]], obj.bbox("Copertina")[1][[0, 2]]
    (a0, _), (a1, _) = obj.bbox("Muretto")[0][[0, 2]], obj.bbox("Muretto")[1][[0, 2]]
    assert x0 < a0 - 0.01 and x1 > a1 + 0.01  # the pillar caps and the wall caps reach beyond the wall

    def across(group):  # the plan y of the top face of a group's front wall, left of the gate (its middle is near x = 0)
        for ids, n in obj.faces(group):
            p = obj.v[ids]
            if obj.n[n][1] > 0.9 and abs(p[:, 0].mean()) < 1.0 and 7.8 < p[:, 2].mean() < 8.2 and p[:, 1].min() > 0.7:
                return -p[:, 2].max(), -p[:, 2].min()
        raise AssertionError(group)

    (c0, c1), (w0, w1) = across("Copertina"), across("Muretto")
    assert w1 - w0 == pytest.approx(0.20, abs=1e-6) and c1 - c0 == pytest.approx(0.23, abs=1e-6)  # 1.5 cm more at each side
    assert walls.area == pytest.approx(0.2 * (9.52 + 8.42 + 2 * 14.76), abs=0.05)


def test_the_railing_has_the_bars_of_the_elevation_and_stays_light(tmp_path):
    report, obj = run(tmp_path, lot_sheet(tmp_path))
    faces = len(obj.faces("Ringhiera"))
    bars = round((9.52 + 8.42 + 2 * 14.76) / 0.10)
    assert bars * 5 <= faces <= bars * 6 + 100  # a bar is a box: five faces seen from outside, the rails come on top
    assert obj.face_count if False else sum(len(f) for f in obj.groups.values()) < 30_000


def test_a_long_railing_has_fewer_bars_when_there_would_be_too_many(tmp_path, monkeypatch):
    path = lot_sheet(tmp_path)
    full = len(run(tmp_path, path)[1].faces("Ringhiera"))
    monkeypatch.setattr(lf, "MAX_BARS", 200)
    light = len(run(tmp_path, path)[1].faces("Ringhiera"))
    assert light < 0.4 * full  # (the pitch doubles until the bars are few enough)


def test_the_gate_is_left_open(tmp_path):
    report, obj = run(tmp_path, lot_sheet(tmp_path))
    gap = box(5.0, -8.1, 6.1, -7.9)  # between the pillars of the gate
    for name in ("Muretto", "Copertina", "Pilastri_lotto", "Ringhiera"):
        ids = sorted({i for f, _ in obj.faces(name) for i in f})
        xs, ys = obj.v[ids][:, 0], -obj.v[ids][:, 2]
        assert not any(gap.buffer(-0.02).contains(__import__("shapely").geometry.Point(x, y)) for x, y in zip(xs, ys)), name
    assert report.lot.gates and "lasciato aperto" in next(l for l in summary_lines(report) if "Recinzione del lotto" in l)


# --- the sheet ---------------------------------------------------------------------------------------------------------

def hatch_over_the_wall(doc, msp):
    """The wall strips drawn as a grey hatch, as the sheet of the user has them: the garden must not make paving of it."""
    h = msp.add_hatch(dxfattribs={"layer": "Retini"})
    h.rgb = (99, 99, 102)
    h.paths.add_polyline_path([(-476, FRONT - 10), (476, FRONT - 10), (476, FRONT + 10), (-476, FRONT + 10)], is_closed=True)
    lawn = msp.add_hatch(dxfattribs={"layer": "Retini"})  # the garden the strip lies at the edge of
    lawn.rgb = (60, 140, 60)
    lawn.paths.add_polyline_path([(-300, FRONT + 20), (800, FRONT + 20), (800, 0), (-300, 0)], is_closed=True)


def test_the_garden_is_not_laid_on_the_wall_the_hatch_of_the_wall_is_no_paving(tmp_path):
    path = lot_sheet(tmp_path, extra=hatch_over_the_wall)
    on, _ = run(tmp_path, path, garden=True)
    strip = box(-4.7, -8.1, 4.7, -7.9)
    assert on.garden is not None and all(g.intersection(strip).area < 1e-6 for g in on.garden.surfaces.values())
    off, _ = run(tmp_path, path, garden=True, fences=False)
    assert off.garden is not None and any(g.intersection(strip).area > 1.0 for g in off.garden.surfaces.values())  # without the fence it is ground


def test_a_layer_named_muretto_is_the_fence_not_a_wall_of_the_house(tmp_path):
    path = lot_sheet(tmp_path, layer="Muretto")  # a wall layer for the names: it would be built 2.7 m high
    report, obj = run(tmp_path, path)
    assert report.lot is not None and "Muretto" in obj.groups
    lo, hi = obj.bbox("Muri")
    assert hi[0] - lo[0] < 11.0 and hi[2] - lo[2] < 7.0  # the house, 10 x 6 m: not the lot
    assert any("Muretto" in w and "non sono letti come muri" in w for w in report.warnings)
    plain, plain_obj = run(tmp_path, path, fences=False)
    assert plain.lot is None and "Pilastri_lotto" not in plain_obj.groups
    assert plain_obj.bbox("Muri")[1][0] - plain_obj.bbox("Muri")[0][0] > 15.0  # (the lot as walls)


def test_nothing_changes_on_a_sheet_without_a_fence(tmp_path):
    doc, msp = building()
    path = tmp_path / "plain.dxf"
    doc.saveas(path)
    on, obj_on = run(tmp_path, path, elevations=[])
    off, obj_off = run(tmp_path, path, elevations=[], fences=False)
    assert on.lot is None and obj_on.groups.keys() == obj_off.groups.keys() and on.faces == off.faces
    assert not any("Recinzione" in w for w in on.warnings)


def test_the_library_leaves_it_off_the_command_line_turns_it_on(tmp_path):
    path = lot_sheet(tmp_path)
    out = tmp_path / "lib.obj"
    assert convert(path, out, Config(elevations=[ZONE])).lot is None
    assert main([str(path), "-o", str(tmp_path / "cli.obj"), "--no-immagini", "--no-tabella", f"--prospetto={','.join(map(str, ZONE))}"]) == 0
    assert "Muretto" in Obj(tmp_path / "cli.obj").groups
    assert main([str(path), "-o", str(tmp_path / "no.obj"), "--no-immagini", "--no-tabella", "--no-recinzioni"]) == 0
    assert "Muretto" not in Obj(tmp_path / "no.obj").groups


def test_the_height_option_of_the_command_line(tmp_path):
    path = lot_sheet(tmp_path)
    assert main([str(path), "-o", str(tmp_path / "h.obj"), "--no-immagini", "--no-tabella", "--altezza-muretto-lotto", "1.1",
                 f"--prospetto={','.join(map(str, ZONE))}"]) == 0
    assert Obj(tmp_path / "h.obj").bbox("Muretto")[1][1] == pytest.approx(1.1 - 0.02, abs=1e-3)


def test_the_report_and_the_json(tmp_path):
    report, _ = run(tmp_path, lot_sheet(tmp_path), c4d_json=True)
    text = next(l for l in summary_lines(report) if "Recinzione del lotto" in l)
    assert "m di muretto alto 0.77 m (prospetto)" in text and "6 pilastri alti 1.52 m (prospetto)" in text
    assert "m di ringhiera alta 0.63 m sul muretto, un montante ogni 10 cm (prospetto)" in text
    assert any("Recinzione del lotto riconosciuta dalla forma del disegno" in w for w in report.warnings)
    model = json.loads((tmp_path / "out_model.json").read_text())
    objects = {o["name"]: o for o in model["objects"]}
    assert {objects[n]["group"] for n in ("Muretto", "Copertina", "Pilastri_lotto", "Ringhiera", "Recinzione_rete")} == {"Recinzione"}
    assert {"lot_wall", "lot_cap", "lot_pillar", "railing", "fence_mesh"} <= set(model["materials"])


def test_the_fence_is_not_of_a_storey_of_a_stack(tmp_path):
    from dwg2c4d.mesh import split_storey
    from dwg2c4d.export import is_site_group

    assert all(is_site_group(n) for n in ("Muretto", "Copertina", "Pilastri_lotto", "Ringhiera", "Recinzione_pali", "Recinzione_rete"))
    assert split_storey("Muretto") == ("", "Muretto")


# --- on a terrain ------------------------------------------------------------------------------------------------------

def test_on_a_terrain_the_wall_and_the_pillars_are_laid_on_it_and_the_railing_follows_it(tmp_path):
    """The slope of ``test_terrain`` (10 % up eastwards): the top of the wall is a fixed distance over the ground everywhere, so is the foot
    of every bar; the rails of the railing step from piece to piece."""
    from test_terrain import add_contours

    def slope(doc, msp):
        doc.layers.add("CURVE-LIV")
        add_contours(msp)
        msp.add_text("+0.00 (100.50)", height=20, dxfattribs={"insert": (1200, 300), "layer": "0"})

    report, obj = run(tmp_path, lot_sheet(tmp_path, extra=slope), terrain_auto=True)
    ground = report.plan.terrain
    assert ground is not None and report.lot is not None

    def off(group):
        ids = sorted({i for f, _ in obj.faces(group) for i in f})
        p = obj.v[ids]
        return p[:, 1] - ground.height(p[:, 0], -p[:, 2]), p[:, 1]

    top, z = off("Muretto")
    assert set(top.round(4)) == {0.95, -0.05} and z.max() - z.min() > 1.5  # the top 0.75 over the platform, the foot in the ground; and it climbs
    cap, _ = off("Copertina")
    assert set(cap.round(4)) == {0.97, 0.95, 1.72, 1.70}  # the caps of the wall (0.77 over the floor, 20 cm over the platform) and of the pillars (1.52)
    bars, _ = off("Ringhiera")
    assert bars.min() == pytest.approx(0.97, abs=5e-3)  # every bar stands on the cap (to the few millimetres its own width gives)
    assert obj.open_edges("Muretto") == 0 and obj.winding_matches_normals("Muretto")


def test_on_flat_ground_everything_stands_from_zero(tmp_path):
    report, obj = run(tmp_path, lot_sheet(tmp_path))
    assert report.plan.terrain is None
    assert obj.bbox("Ringhiera")[0][1] == pytest.approx(0.77, abs=1e-6)  # the foot of the bars: the top of the wall
