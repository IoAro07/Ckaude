"""Storeys drawn as plan views of their own on the sheet (PIANTA PIANO TERRA, PIANTA PIANO PRIMO) are stacked in one model:
whole conversions of synthetic sheets (centimetres), and the pieces (finding the storeys, laying one over the other, heights)."""

import json

import pytest
from shapely.geometry import box

from dwg2c4d import Config, convert
from dwg2c4d.autodetect import analyze
from dwg2c4d.cli import main
from dwg2c4d.mesh import split_storey, storey_name, storey_tag
from dwg2c4d.stacking import (DEFAULT_SLAB, Floor, consensus_levels, find_storeys, place_storeys, register, slab_pieces,
                              stacking_on, storey_of_view)
from helpers import Obj
from test_views import add_title, new_sheet

UPPER_ROOMS = ("STUDIO", "LETTO", "SALA", "CABINA")


def add_floor(msp, x, y, w=1400, d=900, names=("CAMERA", "BAGNO", "CUCINA", "SOGGIORNO"), doors=5, door_gap=250, furniture=0):
    """A plan with its south-west corner at (x, y), like test_views.add_plan, with the rooms ``names`` (the first floor
    does not repeat the rooms of the ground floor) and ``furniture`` little closed outlines that touch no wall."""
    for k in range(furniture):
        msp.add_lwpolyline([(x + 100 + k * 200, y + 400), (x + 180 + k * 200, y + 400), (x + 180 + k * 200, y + 480),
                            (x + 100 + k * 200, y + 480)], close=True)
    for off in (0, 30):
        msp.add_lwpolyline([(x + off, y + off), (x + w - off, y + off), (x + w - off, y + d - off), (x + off, y + d - off)],
                           close=True)
    for off in (0, 30):
        msp.add_line((x + 600 + off, y), (x + 600 + off, y + d))
    for k in range(doors):
        cx = x + 120 + k * door_gap
        msp.add_arc((cx, y + 30), 90, 0, 90)
        msp.add_line((cx, y + 30), (cx, y + 120))
    for k, name in enumerate(names):
        msp.add_text(f"{name} MQ {10 + k},5", height=20, dxfattribs={"insert": (x + 100 + k * 300, y + 700)})


def two_floor_sheet(path, upper_title="PIANTA PIANO PRIMO", upper_size=(1400, 900), upper_at=(3200, 0), base_title="PIANTA PIANO TERRA"):
    """The ground floor at the origin and, 32 m to the east, the floor above it, each with its title."""
    doc, msp = new_sheet()
    add_floor(msp, 0, 0)
    add_title(msp, base_title, 100, -150)
    add_floor(msp, upper_at[0], upper_at[1], *upper_size, names=UPPER_ROOMS, furniture=3)
    add_title(msp, upper_title, upper_at[0] + 100, upper_at[1] - 150)
    doc.saveas(path)
    return path


def run(path, tmp_path, name="m", **kw):
    cfg = Config(auto=True, images=False, **kw)
    return convert(path, tmp_path / f"{name}.obj", cfg), tmp_path / f"{name}.obj"


# --- the whole conversion ------------------------------------------------------------------------------------

def test_a_sheet_with_a_plan_per_floor_is_one_model_of_two_storeys(tmp_path):
    report, out = run(two_floor_sheet(tmp_path / "s.dxf"), tmp_path)
    assert [(f.index, f.view) for f in report.floors] == [(0, 1), (1, 2)]
    ground, first = report.floors
    assert first.shift == pytest.approx((-32.0, 0.0), abs=0.05) and first.iou > 0.95
    assert ground.level == 0.0 and ground.height == pytest.approx(2.7)
    assert first.level == pytest.approx(2.7 + DEFAULT_SLAB) and first.slab == pytest.approx(DEFAULT_SLAB)
    obj = Obj(out)
    assert "Muri" not in obj.groups and {"P0_Muri", "P1_Muri", "P1_Solaio"} <= set(obj.groups)
    (x0, y0, z0), (x1, y1, z1) = obj.bbox("P0_Muri")
    (u0, v0, w0), (u1, v1, w1) = obj.bbox("P1_Muri")
    assert (y0, y1) == pytest.approx((0.0, 2.7), abs=1e-6)
    assert (v0, v1) == pytest.approx((first.level, first.level + 2.7), abs=1e-6)
    assert (u0, u1, w0, w1) == pytest.approx((x0, x1, z0, z1), abs=0.05)  # the first floor stands over the ground floor
    (_, s0, _), (_, s1, _) = obj.bbox("P1_Solaio")
    assert (s0, s1) == pytest.approx((2.7, first.level), abs=1e-6)  # the slab fills the space between the walls


def test_a_stack_lists_its_storeys_in_the_report_and_the_warnings(tmp_path):
    report, _ = run(two_floor_sheet(tmp_path / "s.dxf"), tmp_path)
    text = report.report_path.read_text(encoding="utf-8")
    assert "Piani              : 2 piani impilati" in text
    assert "Piano 1 " in text and "pavimento a 3.00 m" in text
    assert any(w.startswith("Piano 1: ") for w in report.warnings)  # what the first floor's conversion said carries its storey


def test_the_stacked_model_materials_are_the_ones_of_a_single_plan(tmp_path):
    report, out = run(two_floor_sheet(tmp_path / "s.dxf"), tmp_path)
    obj_text = out.read_text()
    assert "o P1_Muri\nusemtl Muri\n" in obj_text and "o P1_Solaio\nusemtl Solaio\n" in obj_text
    mtl = out.with_suffix(".mtl").read_text()
    assert "newmtl Muri" in mtl and "newmtl P1_Muri" not in mtl


def test_the_json_puts_every_storey_in_a_null_of_its_own(tmp_path):
    report, out = run(two_floor_sheet(tmp_path / "s.dxf"), tmp_path, c4d_json=True)
    model = json.loads(report.json_path.read_text())
    names = [g["name"] for g in model["groups"]]
    assert {"P0", "P1", "P0_Murature", "P1_Murature", "P1_Solai"} <= set(names)
    storey_null = next(g for g in model["groups"] if g["name"] == "P1")
    assert storey_null["parent"] == model["name"] and storey_null["label"] == "Piano 1"
    assert next(g for g in model["groups"] if g["name"] == "P1_Murature")["parent"] == "P1"
    assert {o["name"]: o["group"] for o in model["objects"]}["P1_Muri"] == "P1_Murature"
    assert names.index("P1") < names.index("P1_Murature")  # the importer needs the parents first


def test_without_a_title_that_names_the_storeys_nothing_is_stacked(tmp_path):
    report, out = run(two_floor_sheet(tmp_path / "s.dxf", upper_title="PIANTA", base_title="PIANTA"), tmp_path)
    assert report.floors == [] and "Muri" in Obj(out).groups


def test_no_piani_converts_the_chosen_plan_only(tmp_path):
    report, out = run(two_floor_sheet(tmp_path / "s.dxf"), tmp_path, stacking="no")
    assert report.floors == [] and set(Obj(out).groups) >= {"Muri"} and not any(g.startswith("P1_") for g in Obj(out).groups)


def test_choosing_the_view_means_that_view_only_unless_piani_is_asked(tmp_path):
    path = two_floor_sheet(tmp_path / "s.dxf")
    one, out = run(path, tmp_path, "one", view=1)
    assert one.floors == [] and "Muri" in Obj(out).groups
    both, out = run(path, tmp_path, "both", view=1, stacking="yes")
    assert len(both.floors) == 2 and "P1_Muri" in Obj(out).groups
    area, out = run(path, tmp_path, "area", area=(-100, -100, 1500, 1000))
    assert area.floors == []


def test_layers_that_name_the_storeys_are_the_other_way_of_drawing_floors_and_stack_nothing(tmp_path):
    path = two_floor_sheet(tmp_path / "s.dxf")
    import ezdxf

    doc = ezdxf.readfile(path)
    doc.layers.add("P1_Muri")
    doc.layers.add("P2_Muri")
    doc.modelspace().add_lwpolyline([(0, 0), (500, 0), (500, 500)], dxfattribs={"layer": "P1_Muri"})
    doc.modelspace().add_lwpolyline([(0, 0), (400, 0), (400, 400)], dxfattribs={"layer": "P2_Muri"})
    doc.saveas(path)
    report, _ = run(path, tmp_path)
    assert report.floors == []


def test_a_floor_whose_walls_do_not_overlay_the_one_below_is_left_out_and_the_report_says_why(tmp_path):
    path = two_floor_sheet(tmp_path / "s.dxf", upper_size=(700, 900))  # half as wide: the outlines overlap 50 %
    report, out = run(path, tmp_path)
    assert report.floors == [] and "P1_Muri" not in Obj(out).groups
    assert any(w.startswith("Piano 1 (vista 2") and "non impilato" in w and "combacia" in w and "53 %" in w and "--piani" in w
               for w in report.warnings)
    forced, out = run(path, tmp_path, "forced", stacking="yes")  # --piani: the user says they are floors of one building
    assert len(forced.floors) == 2 and forced.floors[1].iou == pytest.approx(0.53, abs=0.02)


def test_storeys_must_follow_one_another(tmp_path):
    report, _ = run(two_floor_sheet(tmp_path / "s.dxf", upper_title="PIANTA PIANO SECONDO"), tmp_path)
    assert report.floors == []
    assert any("piano 2" in w and "piano 1" in w and "non l'ho impilata" in w for w in report.warnings)


def test_three_floors_are_three_storeys(tmp_path):
    doc, msp = new_sheet()
    for k, (title, names) in enumerate((("PIANTA PIANO TERRA", ("CAMERA", "BAGNO", "CUCINA", "SOGGIORNO")),
                                        ("PIANTA PIANO PRIMO", UPPER_ROOMS),
                                        ("PIANTA PIANO SECONDO", ("MANSARDA", "LAVANDERIA", "DEPOSITO", "TERRAZZA")))):
        add_floor(msp, 3200 * k, 0, names=names)
        add_title(msp, title, 3200 * k + 100, -150)
    doc.saveas(tmp_path / "s.dxf")
    report, out = run(tmp_path / "s.dxf", tmp_path)
    assert [f.index for f in report.floors] == [0, 1, 2]
    assert report.floors[2].shift == pytest.approx((-64.0, 0.0), abs=0.05)
    assert report.floors[2].level == pytest.approx(2 * (2.7 + DEFAULT_SLAB))
    assert {"P0_Muri", "P1_Muri", "P2_Muri", "P1_Solaio", "P2_Solaio"} <= set(Obj(out).groups)


def test_a_sheet_with_a_single_plan_is_converted_as_before(tmp_path):
    doc, msp = new_sheet()
    add_floor(msp, 0, 0)
    add_title(msp, "PIANTA PIANO TERRA", 100, -150)
    doc.saveas(tmp_path / "s.dxf")
    report, out = run(tmp_path / "s.dxf", tmp_path)
    assert report.floors == [] and "Muri" in Obj(out).groups
    assert not any(split_storey(g)[0] for g in Obj(out).groups)


# --- the pieces -----------------------------------------------------------------------------------------------

def analysis_of(tmp_path, **kw):
    import ezdxf

    return analyze(ezdxf.readfile(two_floor_sheet(tmp_path / "a.dxf", **kw)))


def test_the_storey_a_view_shows_comes_from_its_own_title_before_its_copies(tmp_path):
    a = analysis_of(tmp_path)
    assert [storey_of_view(a.views, v)[0] for v in a.views] == [0, 1]
    assert a.views[1].copy_of == a.views[0].id  # drawn alike: a copy by the lines, but its title names another floor


def test_find_storeys_gives_the_floors_above_the_plan_in_order(tmp_path):
    a = analysis_of(tmp_path)
    stack = find_storeys(a)
    assert stack.base_index == 0 and [(c.index, c.view.id) for c in stack.candidates] == [(1, 2)]
    missing = find_storeys(analysis_of(tmp_path, upper_title="PIANTA PIANO SECONDO"))
    assert missing.candidates == [] and "non quella del piano 1" in missing.notes[0]


def test_stacking_is_asked_by_the_command_line_and_kept_off_by_the_options_that_choose_the_plan(tmp_path):
    a = analysis_of(tmp_path)
    auto = Config(auto=True)
    auto.user = auto
    assert stacking_on(auto, a, False) and not stacking_on(auto, a, True)  # layers that name the storeys: no
    for kw in ({"view": 1}, {"area": (0, 0, 1, 1)}):
        chosen = Config(auto=True, **kw)
        chosen.user = chosen
        assert not stacking_on(chosen, a, False)
        chosen.stacking = "yes"
        assert stacking_on(chosen, a, False)
    no = Config(auto=True, stacking="no")
    assert not stacking_on(no, a, False)
    part = Config(auto=True, part=True)
    assert not stacking_on(part, a, False)


def test_register_finds_the_shift_that_lays_one_outline_over_the_other():
    below = box(0, 0, 14, 9)
    reg, why = register(box(32, 0, 46, 9), below, False)
    assert why == "" and reg.shift == pytest.approx((-32.0, 0.0), abs=0.02) and reg.iou > 0.97
    reg, why = register(box(32, 0, 44, 9), below, False)  # a smaller floor: it fits at every shift from -32 to -30
    assert reg is not None and -32.05 <= reg.shift[0] <= -29.95 and reg.iou == pytest.approx(12 / 14, abs=0.01)


def ring(x0, y0, x1, y1, t=0.3):
    return box(x0, y0, x1, y1).difference(box(x0 + t, y0 + t, x1 - t, y1 - t))


def test_the_walls_decide_where_a_smaller_floor_stands_on_a_bigger_one():
    below_walls = ring(0, 0, 14, 9).union(box(5, 0, 5.3, 9))  # a partition 5 m from the west wall
    upper_walls = ring(32, 0, 44, 9).union(box(37, 0, 37.3, 9))  # ... and one on the floor above, 5 m from its west wall
    reg, why = register(box(32, 0, 44, 9), box(0, 0, 14, 9), False, upper_walls, below_walls)
    assert why == "" and reg.shift == pytest.approx((-32.0, 0.0), abs=0.02)
    upper_walls = ring(32, 0, 44, 9).union(box(39, 0, 39.3, 9))  # the partition 7 m from the west wall, 5 m from the east one
    below_walls = ring(0, 0, 14, 9).union(box(9, 0, 9.3, 9))  # ... as 9 m from the west wall is 5 m from the east wall
    reg, _ = register(box(32, 0, 44, 9), box(0, 0, 14, 9), False, upper_walls, below_walls)
    assert reg.shift == pytest.approx((-30.0, 0.0), abs=0.02)


def test_register_refuses_outlines_that_do_not_match_and_poses_that_are_not_a_shift():
    below = box(0, 0, 14, 9)
    reg, why = register(box(0, 0, 5, 9), below, False)
    assert reg is None and "combacia" in why and "%" in why
    reg, _ = register(box(0, 0, 5, 9), below, True)  # --piani asks less
    assert reg is None  # ... but not nothing: 5 x 9 over 14 x 9 is a third
    reg, why = register(box(0, 0, 9, 14), below, False)  # turned a quarter: not a shift
    assert reg is None and "ruotato" in why


def test_the_walls_end_under_the_slab_when_the_elevations_give_the_floors():
    base = Floor(0, 1, "t", None, Config(wall_height=3.2), [])
    first = Floor(1, 2, "t", None, Config(wall_height=3.0), [])
    notes = place_storeys([base, first], [0.0, 3.2], 0.4, False)
    assert (base.level, base.height, base.height_source) == (0.0, pytest.approx(2.8), "prospetto")
    assert (first.level, first.height, first.slab) == (pytest.approx(3.2), 3.0, 0.4)  # the top storey keeps its walls
    assert notes and "3.20" in notes[0]


def test_without_levels_the_slab_sits_on_the_walls_of_each_storey():
    base = Floor(0, 1, "t", None, Config(wall_height=2.7), [])
    first = Floor(1, 2, "t", None, Config(wall_height=2.5), [])
    assert place_storeys([base, first], [], 0.3, False) == []
    assert (base.height, first.level, first.slab) == (2.7, pytest.approx(3.0), 0.3)


def test_the_height_the_user_gave_is_never_replaced_by_the_elevations():
    base = Floor(0, 1, "t", None, Config(wall_height=3.0), [])
    first = Floor(1, 2, "t", None, Config(wall_height=3.0), [])
    place_storeys([base, first], [0.0, 3.6], 0.3, True)
    assert (base.height, first.level) == (3.0, pytest.approx(3.3))


def test_elevations_that_disagree_on_the_floors_give_no_level():
    assert consensus_levels([[0.0, 3.2], [0.0, 3.21], [0.0, 3.2, 6.4]]) == pytest.approx([0.0, 3.2, 6.4], abs=0.01)
    assert consensus_levels([[0.0, 3.2], [0.0, 3.6]]) == []  # two readings, two heights
    assert consensus_levels([]) == [] and consensus_levels([[0.0]]) == []


def test_the_slab_stops_under_the_floors_of_the_rooms_and_goes_on_where_they_are_not():
    walls = box(0, 0, 10, 6).difference(box(0.3, 0.3, 9.7, 5.7))
    rooms = box(0.3, 0.3, 9.7, 5.7)
    whole, rest = slab_pieces(walls, rooms, 3.0, 0.4, 0.2)
    assert (whole.z0, whole.z1) == pytest.approx((2.6, 2.8)) and whole.geom.area == pytest.approx(60.0)
    assert (rest.z0, rest.z1) == pytest.approx((2.8, 3.0)) and rest.geom.area == pytest.approx(60.0 - rooms.area)
    (only,) = slab_pieces(walls, box(0, 0, 0, 0), 3.0, 0.4, 0.2)  # no floor: one slab all the way up
    assert (only.z0, only.z1) == pytest.approx((2.6, 3.0))


def test_storey_tags_name_the_groups_of_a_stack():
    assert [storey_tag(i) for i in (0, 1, 2, -1)] == ["P0", "P1", "P2", "PM1"]
    assert split_storey("P1_Telai_F01") == ("P1", "Telai_F01") and split_storey("PM1_Muri") == ("PM1", "Muri")
    assert split_storey("Pavimento_Sala") == ("", "Pavimento_Sala") and split_storey("Telai_P01") == ("", "Telai_P01")
    assert [storey_name(t) for t in ("P0", "P2", "PM1")] == ["Piano 0", "Piano 2", "Piano -1"]


def test_the_command_line_takes_piani_no_piani_and_the_slab(tmp_path, capsys):
    path = two_floor_sheet(tmp_path / "s.dxf")
    assert main([str(path), "-o", str(tmp_path / "c.obj"), "--spessore-solaio", "0.4", "--no-immagini"]) == 0
    assert "Piani" in capsys.readouterr().out
    assert "P1_Solaio" in Obj(tmp_path / "c.obj").groups
    assert Obj(tmp_path / "c.obj").bbox("P1_Solaio")[0][1] == pytest.approx(2.7, abs=1e-6)  # slab 0.4: 2.7 + 0.4 = floor 3.1
    assert main([str(path), "-o", str(tmp_path / "d.obj"), "--no-piani", "--no-immagini"]) == 0
    assert "Muri" in Obj(tmp_path / "d.obj").groups
    assert main([str(path), "-o", str(tmp_path / "e.obj"), "--piani", "--no-piani"]) == 1
