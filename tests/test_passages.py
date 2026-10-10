"""Doorways drawn only as a gap between two wall ends."""

import csv

import pytest
from shapely.geometry import box
from builders import D, PLAN_AREA, T, W, building
from helpers import Obj

from dwg2c4d import Config, convert
from dwg2c4d.table import COLUMNS, read_table


def partition(msp, x0, y0, x1, y1):
    msp.add_lwpolyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], close=True, dxfattribs={"layer": "MURI"})


def plan(tmp_path, gap=(200, 320), second_x=500, thickness=10, name="p"):
    """The 10 x 6 m building with a partition at x = 500 that leaves a gap between y = gap[0] and gap[1]."""
    doc, msp = building()
    partition(msp, 500, T - 1, 500 + thickness, gap[0])
    partition(msp, second_x, gap[1], second_x + thickness, D - T + 1)
    path = tmp_path / f"{name}.dxf"
    doc.saveas(path)
    return path


def run(path, tmp_path, name="o", **kw):
    return convert(path, tmp_path / f"{name}.obj", Config(area=PLAN_AREA, fixtures="detailed", **kw))


def passages(rep):
    return [o for o in rep.openings if o.kind == "passage"]


def test_two_facing_ends_make_a_passage(tmp_path):
    rep = run(plan(tmp_path), tmp_path)
    (v,) = passages(rep)
    assert v.id == "V01" and rep.passages == 1
    assert v.width == pytest.approx(1.2, abs=0.01) and v.thickness == pytest.approx(0.10, abs=0.005)
    assert v.axis == (0.0, 1.0) and (v.z0, v.z1) == (0.0, pytest.approx(2.1))
    assert v.center == (pytest.approx(5.05, abs=0.01), pytest.approx(2.6, abs=0.01))
    assert any("senza simbolo" in n for n in v.notes)


def test_the_lintel_closes_the_wall_above_the_passage(tmp_path):
    with_p = Obj(run(plan(tmp_path), tmp_path).output)
    without = Obj(run(plan(tmp_path), tmp_path, "o2", passages=False).output)
    assert with_p.volume("Muri") - without.volume("Muri") == pytest.approx(1.2 * 0.10 * (2.7 - 2.1), abs=0.01)


def test_the_passage_has_lining_and_casing_but_no_leaf(tmp_path):
    obj = Obj(run(plan(tmp_path), tmp_path).output)
    assert len(obj.groups["Ante"]) == 6  # one box: the leaf of the building's own door, none for the passage
    # lining and casing around the passage (x = 5.0..5.1, y = 2.0..3.2), reaching the lintel's height 2.1 m
    near = [v for ids, _ in obj.groups["Telai"] for v in (obj.v[i] for i in ids)
            if 4.9 < v[0] < 5.2 and 1.9 < -v[2] < 3.3]
    assert near and max(v[1] for v in near) > 2.1


def test_a_passage_splits_the_rooms_it_joins(tmp_path):
    assert len(run(plan(tmp_path), tmp_path).rooms) == 2
    assert len(run(plan(tmp_path), tmp_path, "o2", passages=False).rooms) == 1


def test_a_gap_wider_than_the_limit_is_open_space(tmp_path):
    rep = run(plan(tmp_path, gap=(120, 420)), tmp_path)  # 3 m
    assert not passages(rep)
    assert passages(run(plan(tmp_path, gap=(120, 420)), tmp_path, "o2", passage_max=3.5))


def test_ends_that_do_not_line_up_are_not_a_passage(tmp_path):
    assert not passages(run(plan(tmp_path, second_x=540), tmp_path))


def test_a_door_symbol_in_the_gap_is_not_also_a_passage(tmp_path):
    doc, msp = building()
    partition(msp, 500, T - 1, 510, 200)
    partition(msp, 500, 320, 510, D - T + 1)
    msp.add_lwpolyline([(500, 200), (510, 200), (510, 320), (500, 320)], close=True,
                       dxfattribs={"layer": "PORTE"})
    doc.saveas(tmp_path / "d.dxf")
    rep = run(tmp_path / "d.dxf", tmp_path)
    assert not passages(rep) and any(o.kind == "door" and o.center[1] > 1.5 for o in rep.openings)


def test_a_written_size_goes_to_the_passage(tmp_path):
    doc, msp = building()
    partition(msp, 500, T - 1, 510, 200)
    partition(msp, 500, 320, 510, D - T + 1)
    doc.layers.add("Quote e Testi")
    msp.add_mtext("120\n240", dxfattribs={"insert": (560, 260), "char_height": 10, "layer": "Quote e Testi",
                                          "attachment_point": 5})
    doc.saveas(tmp_path / "t.dxf")
    (v,) = passages(run(tmp_path / "t.dxf", tmp_path))
    assert v.z1 == pytest.approx(2.4) and v.src["height"] == "scritta"


def test_the_table_lists_it_and_can_close_it(tmp_path):
    rep = run(plan(tmp_path), tmp_path)
    rows = read_table(rep.table_path)
    (row,) = [r for r in rows if r["tipo"] == "vano"]
    assert row["id"] == "V01" and row["larghezza"] == "120"
    row["MODIFICA_tieni"] = "no"
    with open(rep.table_path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, delimiter=";")
        w.writeheader()
        w.writerows(rows)
    again = run(plan(tmp_path), tmp_path, "o2", table_in=str(rep.table_path))
    assert Obj(again.output).volume("Muri") > Obj(rep.output).volume("Muri") + 1.2 * 0.1 * 2.0
    assert W == 1000


def _room_with_a_doorway():
    """A 4 x 4 m room with 30 cm walls and a 1.2 m doorway in its north wall."""
    return box(0, 0, 4, 4).difference(box(0.3, 0.3, 3.7, 3.7)).difference(box(1.5, 3.5, 2.7, 4.5))


def test_a_doorway_is_a_window_when_nothing_stands_beyond_it():
    from dwg2c4d.passages import find_passages

    (op,) = find_passages(_room_with_a_doorway(), Config(shape_openings=True), [])
    assert op.kind == "window"  # the outside is behind it: a gap in the outside wall


def test_a_doorway_to_a_space_the_walls_do_not_close_is_not_a_window():
    from dwg2c4d.passages import find_passages

    walls = _room_with_a_doorway().union(box(-2, 8, 6, 8.3))  # another wall across, 4 m away: the room is not alone
    (op,) = find_passages(walls, Config(shape_openings=True), [])
    assert op.kind == "passage"
