"""The height of a curb or a low wall written next to it ("Hmuro= 1,00 ml.")."""

from types import SimpleNamespace

import pytest
from builders import building
from helpers import Obj
from shapely.geometry import box

from dwg2c4d import Config, convert
from dwg2c4d.cli import build_parser, config_from_args
from dwg2c4d.lowheights import REACH, written_height, written_heights


def word(text, x, y):
    return SimpleNamespace(text=text, x=x, y=y)


# --- the text ------------------------------------------------------------------------------------

@pytest.mark.parametrize("text, metres", [
    ("Hmuro= 1,00 ml.", 1.0), ("Hmuro= 0,50 ml.", 0.5), ("H muro 60 cm", 0.6), ("h muretto = 0.8 m", 0.8),
    ("Recinzione di confine con palo e rete metallica verde H= 160 cm.", 1.6), ("Muro di contenimento H=2.4", 2.4),
    ("Hmuro=100", 1.0), ("parapetto h: 1,10 m", 1.1),
])
def test_a_text_that_names_a_wall_and_gives_its_height_is_read(text, metres):
    assert written_height(text) == pytest.approx(metres)


@pytest.mark.parametrize("text", ["H 2,70 m", "CAMERA MQ 12,43 H 2,70 m", "h 300", "Hmuro= 99 ml.", "Hmuro=", "muro di contenimento esistente",
                                  "Testa argine (scarpata)", "QUOTA PIANO 297.40"])
def test_a_room_height_a_missing_number_or_an_absurd_one_is_no_wall_height(text):
    assert written_height(text) is None


# --- which wall ----------------------------------------------------------------------------------

def test_the_text_belongs_to_the_nearest_low_wall_within_reach_and_the_others_keep_the_default():
    near, far = box(0, 0, 10, 0.3), box(0, 20, 10, 20.3)
    rest, written, notes = written_heights(near.union(far), [word("Hmuro= 1,00 ml.", 5.0, 2.0)])
    assert [(round(h, 2), t) for _, h, t in written] == [(1.0, "Hmuro= 1,00 ml.")] and written[0][0].equals(near)
    assert rest.equals(far) and len(notes) == 1 and "1 m da 'Hmuro= 1,00 ml.'" in notes[0]


def test_a_text_farther_than_the_reach_is_not_that_walls():
    wall = box(0, 0, 10, 0.3)
    rest, written, notes = written_heights(wall, [word("Hmuro= 1,00 ml.", 5.0, REACH + 1.0)])
    assert written == [] and rest is wall and notes == []  # the very same shape: nothing was touched, the mesh is what it was


def test_two_walls_two_texts():
    a, b = box(0, 0, 10, 0.3), box(0, 12, 10, 12.3)
    rest, written, _ = written_heights(a.union(b), [word("Hmuro= 1,00 ml.", 5, 1), word("Hmuro= 0,50 ml.", 5, 11)])
    assert sorted(round(h, 2) for _, h, _ in written) == [0.5, 1.0] and rest.is_empty


def test_texts_that_disagree_on_one_wall_leave_it_at_the_default_and_say_so():
    wall = box(0, 0, 20, 0.3)
    rest, written, notes = written_heights(wall, [word("Hmuro= 1,00 ml.", 2, 1), word("Hmuro= 0,50 ml.", 18, 1)])
    assert written == [] and rest.equals(wall)
    assert len(notes) == 1 and "altezze diverse" in notes[0] and "--altezza-muretti" in notes[0]


def test_texts_that_agree_on_one_wall_are_one_height():
    wall = box(0, 0, 20, 0.3)
    _, written, _ = written_heights(wall, [word("Hmuro= 1,00 ml.", 2, 1), word("H muro 100 cm", 18, 1)])
    assert len(written) == 1 and written[0][1] == pytest.approx(1.0)


# --- a conversion --------------------------------------------------------------------------------

def curb_sheet(tmp_path, text="Hmuro= 1,00 ml.", at=(300, -450)):
    """The 10 x 6 m house with its walls marked by a hatch and a free-standing 14 x 0.3 m wall south of it."""
    doc, msp = building()
    for e in list(msp.query("LWPOLYLINE")):
        msp.delete_entity(e)
    h = msp.add_hatch(dxfattribs={"layer": "MURI"})
    h.paths.add_polyline_path([(0, 0), (1000, 0), (1000, 600), (0, 600)], is_closed=True)
    h.paths.add_polyline_path([(30, 30), (970, 30), (970, 570), (30, 570)], is_closed=True)
    msp.add_lwpolyline([(-200, -800), (1200, -800), (1200, -770), (-200, -770)], close=True, dxfattribs={"layer": "MURI"})
    if text:
        msp.add_text(text, height=20, dxfattribs={"layer": "TESTI", "insert": at})
    doc.saveas(tmp_path / "w.dxf")
    return tmp_path / "w.dxf"


def run(tmp_path, path=None, **kw):
    path = path or curb_sheet(tmp_path, at=(300, -950))  # the text 1.5 m from the wall
    cfg = Config(area=(-400, -1200, 1400, 800), walls_guessed=True, floors_to_outer_face=False, **kw)
    return convert(path, tmp_path / "w.obj", cfg), Obj(tmp_path / "w.obj")


def test_the_height_written_next_to_a_low_wall_is_its_height(tmp_path):
    report, obj = run(tmp_path)
    assert obj.bbox("Muretti")[1][1] == pytest.approx(1.0)  # not the default 0.5 m
    assert len(report.plan.low_written) == 1
    assert any("l'altezza scritta nel disegno" in w and "'Hmuro= 1,00 ml.'" in w for w in report.warnings)
    assert obj.bbox("Muri")[1][1] == pytest.approx(2.7)


def test_without_a_text_the_default_height_stays(tmp_path):
    report, obj = run(tmp_path, curb_sheet(tmp_path, text=None))
    assert obj.bbox("Muretti")[1][1] == pytest.approx(0.5) and report.plan.low_written == []
    assert not any("l'altezza scritta" in w for w in report.warnings)


def test_a_height_the_user_gives_beats_the_written_one(tmp_path):
    report, obj = run(tmp_path, low_wall_height=0.7)
    assert obj.bbox("Muretti")[1][1] == pytest.approx(0.7) and report.plan.low_written == []
    cfg = config_from_args(build_parser().parse_args(["x.dxf", "--altezza-muretti", "0.7"]))
    assert not cfg.low_heights_auto
    assert config_from_args(build_parser().parse_args(["x.dxf"])).low_heights_auto


def test_the_texts_can_be_switched_off(tmp_path):
    report, obj = run(tmp_path, texts=False)
    assert obj.bbox("Muretti")[1][1] == pytest.approx(0.5)


def test_a_far_text_changes_nothing(tmp_path):
    report, obj = run(tmp_path, curb_sheet(tmp_path, at=(300, 600)))  # 14 m from the wall, in the plan area
    assert obj.bbox("Muretti")[1][1] == pytest.approx(0.5)
