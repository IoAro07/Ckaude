"""Two symbols drawn on top of each other are one opening, not two frames and two panes in the same hole."""

import pytest
from builders import PLAN_AREA, T, building

from dwg2c4d import Config, convert
from dwg2c4d.openings import Opening, merge_duplicates


def with_blocks(tmp_path, placements):
    """The 10 x 6 m building with a window block (a rectangle across the south wall) inserted at each ``(x, width)``."""
    doc, msp = building()
    for width in {w for _, w in placements}:
        block = doc.blocks.new(f"FINESTRA_{width}")
        block.add_lwpolyline([(0, 0), (width, 0), (width, T), (0, T)], close=True, dxfattribs={"layer": "FINESTRE"})
    for x, width in placements:
        msp.add_blockref(f"FINESTRA_{width}", (x, 0), dxfattribs={"layer": "FINESTRE"})
    doc.saveas(tmp_path / "w.dxf")
    return tmp_path / "w.dxf"


def run(path, tmp_path):
    return convert(path, tmp_path / "o.obj", Config(area=PLAN_AREA))


def windows(report):
    return [o for o in report.openings if o.kind == "window" and o.block]


def test_two_identical_symbols_in_the_same_place_are_one_window(tmp_path):
    report = run(with_blocks(tmp_path, [(700, 120), (700, 120)]), tmp_path)
    (op,) = windows(report)
    assert op.width == pytest.approx(1.2, abs=0.01) and op.center[0] == pytest.approx(7.6, abs=0.01)
    assert any("disegnata due volte" in n for n in op.notes)
    assert any("erano disegnate due volte" in w for w in report.warnings)


def test_the_window_without_the_second_symbol_is_the_same_window(tmp_path):
    twice = windows(run(with_blocks(tmp_path, [(700, 120), (700, 120)]), tmp_path))[0]
    once = windows(run(with_blocks(tmp_path, [(700, 120)]), tmp_path))[0]
    assert (twice.center, twice.width, twice.thickness) == (once.center, once.width, once.thickness)


def test_windows_side_by_side_or_of_another_width_are_not_duplicates(tmp_path):
    assert len(windows(run(with_blocks(tmp_path, [(700, 120), (830, 120)]), tmp_path))) == 2  # next to each other
    assert len(windows(run(with_blocks(tmp_path, [(700, 120), (715, 90)]), tmp_path))) == 2  # same centre, not the same width


def test_a_window_and_a_door_in_the_same_place_are_not_duplicates():
    from shapely.geometry import Polygon

    def opening(kind):
        return Opening(kind, Polygon(), Polygon(), 0.0, 2.1, None, center=(1.0, 2.0), width=0.9)

    kept, twice = merge_duplicates([opening("window"), opening("door")])
    assert len(kept) == 2 and twice == 0


def test_the_one_that_says_more_stays():
    from shapely.geometry import Polygon

    plain = Opening("window", Polygon(), Polygon(), 0.9, 2.2, None, center=(1.0, 2.0), width=2.6)
    divided = Opening("window", Polygon(), Polygon(), 0.9, 2.2, None, center=(1.0, 2.0), width=2.6, dividers=[0.0])
    kept, twice = merge_duplicates([plain, divided])
    assert kept == [divided] and twice == 1
