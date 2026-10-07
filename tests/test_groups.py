"""Walls in groups far apart (a roof plan or a section on the same layer): tell, with the --area of each."""

import ezdxf
import pytest
from builders import PLAN_AREA, T, building

from dwg2c4d import Config, convert


def with_second_drawing(tmp_path, dx=0, dy=1500, size=(500, 300)):
    doc, msp = building()
    w, d = size
    for pts in ([(dx, dy), (dx + w, dy), (dx + w, dy + d), (dx, dy + d)],
                [(dx + T, dy + T), (dx + w - T, dy + T), (dx + w - T, dy + d - T), (dx + T, dy + d - T)]):
        msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": "MURI"})
    path = tmp_path / "g.dxf"
    doc.saveas(path)
    return path


def run(path, tmp_path, **kw):
    return convert(path, tmp_path / "o.obj", Config(area=(-200, -200, 1200, 2200), **kw))


def test_two_far_groups_are_reported_with_the_area_of_each(tmp_path):
    rep = run(with_second_drawing(tmp_path), tmp_path)
    (msg,) = [w for w in rep.warnings if "gruppi distanti" in w]
    assert "1) 10.0 x 6.0 m" in msg and "2) 5.0 x 3.0 m" in msg  # the bigger one first
    assert "--area=-50,-50,1050,650" in msg and "--area=-50,1450,550,1850" in msg


def test_the_area_it_suggests_gives_one_building(tmp_path):
    path = with_second_drawing(tmp_path)
    rep = convert(path, tmp_path / "o.obj", Config(area=(-50, -50, 1050, 650)))
    assert rep.size_m == pytest.approx((10.0, 6.0)) and not any("gruppi distanti" in w for w in rep.warnings)


def test_a_single_building_is_not_remarked(tmp_path):
    doc, _ = building()
    doc.saveas(tmp_path / "b.dxf")
    assert not any("gruppi" in w for w in convert(tmp_path / "b.dxf", tmp_path / "o.obj",
                                                  Config(area=PLAN_AREA)).warnings)


def test_a_small_free_standing_wall_is_not_a_group(tmp_path):
    doc, msp = building()
    msp.add_lwpolyline([(1500, 0), (1530, 0), (1530, 30), (1500, 30)], close=True, dxfattribs={"layer": "MURI"})
    doc.saveas(tmp_path / "s.dxf")
    rep = convert(tmp_path / "s.dxf", tmp_path / "o.obj", Config(area=(-200, -200, 2000, 800)))
    assert not any("gruppi" in w for w in rep.warnings) and ezdxf is not None


def test_close_pieces_are_one_building(tmp_path):
    rep = run(with_second_drawing(tmp_path, dx=0, dy=700), tmp_path)  # 0.7 m from the first
    assert not any("gruppi" in w for w in rep.warnings)
