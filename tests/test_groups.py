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


# --- groups without doors/windows are dropped by themselves (no --area given) ---------------

def convert_all(path, tmp_path, **kw):
    return convert(path, tmp_path / "o.obj", Config(**kw))  # no area: the whole drawing


def test_a_group_without_openings_is_left_out_when_no_area_is_given(tmp_path):
    """A roof plan drawn with the wall layer, above the real plan: no doors or windows on it."""
    from helpers import Obj

    rep = convert_all(with_second_drawing(tmp_path), tmp_path)
    assert rep.size_m == pytest.approx((10.0, 6.0))
    assert rep.warnings[0].startswith("Ho escluso 1 gruppo/i di muri senza porte ne' finestre")
    assert "5.0 x 3.0 m" in rep.warnings[0]
    hi = Obj(rep.output).bbox("Muri")[1]
    assert hi[2] <= 0.1  # nothing at y = 15 m (z = -15): only the plan is modelled
    assert Obj(rep.output).bbox("Muri")[0][2] == pytest.approx(-6.0)


def test_two_groups_both_with_openings_are_both_kept(tmp_path):
    doc, msp = building()
    for pts in ([(1500, 0), (2000, 0), (2000, 300), (1500, 300)],
                [(1530, 30), (1970, 30), (1970, 270), (1530, 270)]):
        msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": "MURI"})
    msp.add_lwpolyline([(1600, 0), (1690, 0), (1690, 30), (1600, 30)], close=True, dxfattribs={"layer": "PORTE"})
    doc.saveas(tmp_path / "two.dxf")
    rep = convert_all(tmp_path / "two.dxf", tmp_path)
    assert any("2 gruppi distanti" in w for w in rep.warnings)
    assert not any("Ho escluso" in w for w in rep.warnings) and rep.size_m[0] > 19.0


def test_groups_without_any_opening_are_kept_and_reported(tmp_path):
    doc = ezdxf.new("R2018", setup=True)
    doc.units = 5
    doc.layers.add("MURI")
    msp = doc.modelspace()
    for dx in (0, 1500):
        for pts in ([(dx, 0), (dx + 500, 0), (dx + 500, 300), (dx, 300)],
                    [(dx + 30, 30), (dx + 470, 30), (dx + 470, 270), (dx + 30, 270)]):
            msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": "MURI"})
    doc.saveas(tmp_path / "none.dxf")
    rep = convert_all(tmp_path / "none.dxf", tmp_path)
    assert any("2 gruppi distanti" in w for w in rep.warnings) and rep.size_m[0] > 19.0
