"""Written texts: sizes and sills next to openings, room names and heights."""

import ezdxf
import pytest
from builders import DOOR, PLAN_AREA, T, W, WIN_N, WIN_S, building, south_elevation
from helpers import Obj

from dwg2c4d import Config, convert
from dwg2c4d.cli import main
from dwg2c4d.table import read_table

SOUTH_DOOR = ((DOOR[0] + DOOR[1]) / 2, T / 2)      # centre of the door on the wall's mid-plane (cm)
SOUTH_WIN = ((WIN_S[0] + WIN_S[1]) / 2, T / 2)
NORTH_WIN = ((WIN_N[0] + WIN_N[1]) / 2, 600 - T / 2)


def mtext(msp, text, x, y, height=10, layer="Quote e Testi", att=5):
    msp.add_mtext(text, dxfattribs={"insert": (x, y), "char_height": height, "layer": layer,
                                    "attachment_point": att})


def run(doc, tmp_path, name="o", **kw):
    path = tmp_path / f"{name}.dxf"
    doc.saveas(path)
    return convert(path, tmp_path / f"{name}.obj", Config(area=PLAN_AREA, **kw))


@pytest.fixture
def plan():
    doc, msp = building()
    doc.layers.add("Quote e Testi")
    return doc, msp


def opening(rep, kind, y_above):
    return next(o for o in rep.openings if o.kind == kind and (o.center[1] > 3.0) == y_above)


# --- openings --------------------------------------------------------------------------------

def test_stacked_size_and_sill_set_the_window(plan, tmp_path):
    doc, msp = plan
    mtext(msp, "120\n130", SOUTH_WIN[0], -45)   # width over height, 45 cm outside the wall
    mtext(msp, "ht 95", SOUTH_WIN[0], -75, height=5)
    rep = run(doc, tmp_path)
    win = opening(rep, "window", False)
    assert (win.z0, win.z1) == (pytest.approx(0.95), pytest.approx(2.25))
    assert win.src["height"] == "scritta" and win.src["sill"] == "scritta"
    assert win.label == "120 130 | ht 95"
    assert rep.labels == 1


@pytest.mark.parametrize("text", ["90x205", "90 x 205", "L90 H205", "90×205", "90*205"])
def test_size_written_on_one_line(plan, tmp_path, text):
    doc, msp = plan
    msp.add_text(text, dxfattribs={"insert": (SOUTH_DOOR[0] - 20, -45), "height": 10, "layer": "Quote e Testi"})
    door = opening(run(doc, tmp_path), "door", False)
    assert door.z1 == pytest.approx(2.05) and door.z0 == 0.0


def test_a_third_number_is_the_sill(plan, tmp_path):
    doc, msp = plan
    mtext(msp, "120x100x80", SOUTH_WIN[0], -45)
    win = opening(run(doc, tmp_path), "window", False)
    assert (win.z0, win.z1) == (pytest.approx(0.8), pytest.approx(1.8))


def test_h_and_its_number_typed_as_two_texts_are_one_sill(plan, tmp_path):
    doc, msp = plan
    mtext(msp, "120x130", SOUTH_WIN[0], -45)
    msp.add_text("ht", dxfattribs={"insert": (SOUTH_WIN[0] - 30, -80), "height": 8, "layer": "Quote e Testi"})
    msp.add_text("100", dxfattribs={"insert": (SOUTH_WIN[0] - 12, -80), "height": 8, "layer": "Quote e Testi"})
    win = opening(run(doc, tmp_path), "window", False)
    assert win.z0 == pytest.approx(1.0)


def test_tall_window_without_sill_starts_at_the_floor(plan, tmp_path):
    doc, msp = plan
    mtext(msp, "120x240", SOUTH_WIN[0], -45)
    win = opening(run(doc, tmp_path), "window", False)
    assert win.z0 == 0.0 and win.z1 == pytest.approx(2.4)
    assert any("finestra a terra" in n for n in win.notes)


def test_millimetre_texts_are_read_as_millimetres(plan, tmp_path):
    doc, msp = plan
    mtext(msp, "900x2050", SOUTH_DOOR[0], -45)
    mtext(msp, "1200x1300", SOUTH_WIN[0], -45)
    rep = run(doc, tmp_path)
    assert opening(rep, "door", False).z1 == pytest.approx(2.05)
    assert opening(rep, "window", False).z1 - opening(rep, "window", False).z0 == pytest.approx(1.3)


def test_text_in_a_block_attribute_is_read(plan, tmp_path):
    doc, msp = plan
    blk = doc.blocks.new("TAGSIZE")
    blk.add_attdef("SIZE", (0, 0), dxfattribs={"height": 10})
    msp.add_blockref("TAGSIZE", (SOUTH_DOOR[0] - 20, -45), dxfattribs={"layer": "Quote e Testi"}) \
        .add_auto_attribs({"SIZE": "90x200"})
    assert opening(run(doc, tmp_path), "door", False).z1 == pytest.approx(2.0)


def test_text_beats_the_elevation_and_says_so(plan, tmp_path):
    doc, msp = plan
    area = south_elevation(msp, sill=100, win_h=140)
    mtext(msp, "120x120", SOUTH_WIN[0], -45)
    mtext(msp, "ht 90", SOUTH_WIN[0], -75, height=5)
    rep = run(doc, tmp_path, elevations=[area])
    win = opening(rep, "window", False)
    assert (win.z0, win.z1) == (pytest.approx(0.9), pytest.approx(2.1))
    assert any("scritta e prospetto non coincidono" in n for n in win.notes)


def test_width_of_the_drawing_wins_over_the_text(plan, tmp_path):
    doc, msp = plan
    mtext(msp, "100x130", SOUTH_WIN[0], -45)  # the symbol is 120 wide
    win = opening(run(doc, tmp_path), "window", False)
    assert win.width == pytest.approx(1.2)
    assert any("scritta 100 cm ma il simbolo misura 120 cm" in n for n in win.notes)


def test_a_far_label_with_another_width_is_not_taken(plan, tmp_path):
    doc, msp = plan
    mtext(msp, "60x130", SOUTH_WIN[0], -100)  # 1.15 m away and 60 wide: not this window's
    rep = run(doc, tmp_path)
    assert rep.labels == 0
    assert any("non associata ad alcuna apertura" in w and "60x130" in w for w in rep.warnings)


def test_two_labels_go_to_the_right_openings_by_width(plan, tmp_path):
    doc, msp = plan
    # the door label sits nearer to the door than the window label: and both widths match
    mtext(msp, "90x210", SOUTH_DOOR[0], -45)
    mtext(msp, "120x150", SOUTH_WIN[0], -45)
    rep = run(doc, tmp_path)
    assert opening(rep, "door", False).label == "90x210"
    assert opening(rep, "window", False).label == "120x150"


def test_numbers_that_are_not_an_opening_size_are_ignored(plan, tmp_path):
    doc, msp = plan
    mtext(msp, "12/05", SOUTH_WIN[0], -45)   # a date, not 12 x 5 cm
    rep = run(doc, tmp_path)
    assert rep.labels == 0 and not any("12/05" in w for w in rep.warnings)


def test_no_texts_flag(plan, tmp_path):
    doc, msp = plan
    mtext(msp, "120x120", SOUTH_WIN[0], -45)
    rep = run(doc, tmp_path, texts=False)
    assert rep.labels == 0 and opening(rep, "window", False).src["height"] == "default"


def test_table_shows_the_text_that_was_read(plan, tmp_path):
    doc, msp = plan
    mtext(msp, "120x120", SOUTH_WIN[0], -45)
    rows = read_table(run(doc, tmp_path).table_path)
    assert [r["scritta"] for r in rows if r["tipo"] == "finestra" and r["scritta"]] == ["120x120"]


# --- rooms -----------------------------------------------------------------------------------

def test_room_name_and_height_from_texts(plan, tmp_path):
    doc, msp = plan
    mtext(msp, "SOGGIORNO", 500, 300)
    mtext(msp, "h 300", 500, 282)
    rep = run(doc, tmp_path)
    assert rep.rooms == [{"name": "Soggiorno", "area_m2": pytest.approx(9.4 * 5.4), "height": 3.0, "named": True}]
    assert rep.wall_height == 3.0 and rep.wall_height_source == "scritta"
    top = max(v[1] for v in Obj(rep.output).verts)
    assert top == pytest.approx(3.0)


def test_misspelt_room_name_is_corrected(plan, tmp_path):
    doc, msp = plan
    mtext(msp, "SOGGIORN0", 500, 300)
    assert run(doc, tmp_path).rooms[0]["name"] == "Soggiorno"


def test_a_given_wall_height_beats_the_text(plan, tmp_path):
    doc, msp = plan
    mtext(msp, "SOGGIORNO", 500, 300)
    mtext(msp, "h 300", 500, 282)
    rep = run(doc, tmp_path, wall_height=2.5)
    assert rep.wall_height == 2.5 and rep.wall_height_source == "indicata"


def test_room_without_a_name_gets_a_number(plan, tmp_path):
    doc, _ = plan
    rep = run(doc, tmp_path)
    assert rep.rooms[0]["name"] == "Locale 01" and not rep.rooms[0]["named"]
    assert rep.wall_height_source == "predefinita"


def test_a_height_written_far_from_any_room_is_ignored(plan, tmp_path):
    doc, msp = plan
    mtext(msp, "h 300", 500, -400)  # outside the building
    assert run(doc, tmp_path).wall_height == 2.7


def test_second_height_in_the_same_room_is_reported(plan, tmp_path):
    doc, msp = plan
    mtext(msp, "SOGGIORNO", 500, 300)
    mtext(msp, "h 300", 500, 282)
    mtext(msp, "h 290", 200, 150)
    rep = run(doc, tmp_path)
    assert rep.wall_height == 3.0
    assert any("h 290" in w for w in rep.warnings)


def test_cli_prints_rooms_and_wall_height(plan, tmp_path, capsys):
    doc, msp = plan
    mtext(msp, "SOGGIORNO", 500, 300)
    mtext(msp, "h 300", 500, 282)
    path = tmp_path / "c.dxf"
    doc.saveas(path)
    assert main([str(path), "-o", str(tmp_path / "c.obj"), "--area=" + ",".join(map(str, PLAN_AREA))]) == 0
    out = capsys.readouterr().out
    assert "Altezza muri       : 3 m (dalla scritta 'h' nei locali)" in out
    assert "Locale             : Soggiorno" in out and "h 3 m" in out


def test_cli_no_texts(plan, tmp_path, capsys):
    doc, msp = plan
    mtext(msp, "h 300", 500, 282)
    path = tmp_path / "c.dxf"
    doc.saveas(path)
    area = "--area=" + ",".join(map(str, PLAN_AREA))
    assert main([str(path), "-o", str(tmp_path / "c.obj"), area, "--no-scritte"]) == 0
    assert "(predefinita)" in capsys.readouterr().out


def test_unused_constants_and_helpers_are_importable():
    assert W == 1000 and ezdxf is not None
