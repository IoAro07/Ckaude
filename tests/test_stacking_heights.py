"""Stacked storeys and the elevations of the sheet: the floors they mark, the sills and heights of the openings of every storey,
the eaves of the top one (whole conversions of a synthetic sheet, centimetres)."""

import ezdxf
import pytest

from dwg2c4d.stacking import DEFAULT_SLAB
from test_stacking import run

S_ROW = [(150, 100), (360, 120), (600, 100), (840, 120)]  # the windows of the south wall: centre x and width (cm)
S_DOOR = (480, 90)
N_ROW = [(330, 100), (540, 120), (760, 100)]


def _rect(msp, x0, y0, x1, y1, layer):
    msp.add_lwpolyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], close=True, dxfattribs={"layer": layer})


def house_sheet(path, first_floor_windows=(0, 1, 2, 3), eaves=620, marks=True, soffit=None, upper_width=1000, title_on_copy=False):
    """The ground floor and the first floor of a 10 x 6 m house (cm), each with its title, and the south elevation drawn 40 m
    to the right: the windows of the ground floor (90 to 220 above the floor), those of the first floor (100 to 230 above its
    floor, which is 3.2 m up), the ground line, the slab line, the level marks and the line of the eaves."""
    doc = ezdxf.new("R2018", setup=True)
    doc.units = 5
    for name in ("MURI", "PORTE", "FINESTRE", "TESTI"):
        doc.layers.add(name)
    msp = doc.modelspace()

    def plan(dx, windows, door, title, dy=0, width=1000):
        _rect(msp, dx, dy, dx + width, dy + 600, "MURI")
        _rect(msp, dx + 30, dy + 30, dx + width - 30, dy + 570, "MURI")
        for k in windows:
            c, w = S_ROW[k]
            _rect(msp, dx + c - w / 2, dy, dx + c + w / 2, dy + 30, "FINESTRE")
        if door:
            _rect(msp, dx + S_DOOR[0] - 45, dy, dx + S_DOOR[0] + 45, dy + 30, "PORTE")
        for c, w in N_ROW:
            if c + w / 2 < width:
                _rect(msp, dx + c - w / 2, dy + 570, dx + c + w / 2, dy + 600, "FINESTRE")
        if title:
            msp.add_text(title, height=20, dxfattribs={"layer": "TESTI", "insert": (dx + 350, dy - 150)})

    plan(0, range(4), True, "PIANTA PIANO TERRA")
    if title_on_copy:  # the first floor is drawn twice: the title is on the copy, far from the plan the elevations are for
        plan(2500, first_floor_windows, False, "", width=upper_width)
        plan(2500, first_floor_windows, False, "PIANTA PIANO PRIMO", dy=1800, width=upper_width)
    else:
        plan(2500, first_floor_windows, False, "PIANTA PIANO PRIMO", width=upper_width)
    ground = -3000
    for c, w in S_ROW:  # the south elevation: x as in the plan plus 4000
        _rect(msp, c + 4000 - w / 2, ground + 90, c + 4000 + w / 2, ground + 220, "FINESTRE")
    for k in first_floor_windows:
        c, w = S_ROW[k]
        _rect(msp, c + 4000 - w / 2, ground + 320 + 100, c + 4000 + w / 2, ground + 320 + 230, "FINESTRE")
    _rect(msp, S_DOOR[0] + 4000 - 45, ground, S_DOOR[0] + 4000 + 45, ground + 210, "PORTE")
    msp.add_line((3900, ground), (5100, ground), dxfattribs={"layer": "0"})
    msp.add_line((3970, ground + 320), (5030, ground + 320), dxfattribs={"layer": "0"})  # the slab
    msp.add_line((3970, ground + eaves), (5030, ground + eaves), dxfattribs={"layer": "0"})  # the eaves
    if soffit is not None:  # the slab seen from outside: a second line under the one of the floor
        msp.add_line((3970, ground + soffit), (5030, ground + soffit), dxfattribs={"layer": "0"})
    if marks:
        msp.add_text("±0,00", height=12, dxfattribs={"layer": "TESTI", "insert": (5050, ground + 5)})
        msp.add_text("+3,20", height=12, dxfattribs={"layer": "TESTI", "insert": (5050, ground + 325)})
    msp.add_text("PROSPETTO SUD", height=20, dxfattribs={"layer": "TESTI", "insert": (4350, ground - 150)})
    doc.saveas(path)
    return path


def windows_of(floor, side="S"):
    return sorted((o for o in floor.openings if o.kind == "window" and (o.center[1] < 3) == (side == "S")), key=lambda o: o.center[0])


def test_the_floors_of_the_elevations_give_the_level_of_the_storey_and_the_height_of_its_walls(tmp_path):
    from helpers import Obj

    report, out = run(house_sheet(tmp_path / "h.dxf"), tmp_path)
    ground, first = report.floors
    assert first.level == pytest.approx(3.2, abs=0.01) and first.slab == pytest.approx(DEFAULT_SLAB)
    assert ground.height == pytest.approx(3.2 - DEFAULT_SLAB, abs=0.01) and ground.height_source == "prospetto"
    assert first.height == pytest.approx(3.0, abs=0.01) and first.height_source == "prospetto"  # the eaves at 6.2 m
    obj = Obj(out)
    assert obj.bbox("P0_Muri")[1][1] == pytest.approx(2.9, abs=0.01)
    assert obj.bbox("P1_Muri")[0][1] == pytest.approx(3.2, abs=0.01) and obj.bbox("P1_Muri")[1][1] == pytest.approx(6.2, abs=0.01)
    assert any("Piano 0: i prospetti danno il piano sopra a 3.20 m" in w for w in report.warnings)


def test_every_storey_takes_the_sills_and_heights_of_its_own_row_of_the_elevation(tmp_path):
    report, _ = run(house_sheet(tmp_path / "h.dxf"), tmp_path)
    ground, first = report.floors
    assert all(o.from_elevation and (o.z0, o.z1) == pytest.approx((0.9, 2.2)) for o in windows_of(ground))
    assert len(windows_of(first)) == 4
    assert all(o.from_elevation and (o.z0, o.z1) == pytest.approx((1.0, 2.3)) for o in windows_of(first))
    assert [e["side"] for e in first.report.elevations] == ["sud"]
    assert report.report_path.read_text(encoding="utf-8").count("Prosp. P1 sud") == 1


def test_an_elevation_the_floor_below_placed_is_placed_on_the_floor_above_even_with_two_windows(tmp_path):
    report, _ = run(house_sheet(tmp_path / "h.dxf", first_floor_windows=(0, 2)), tmp_path)
    first = report.floors[1]
    windows = windows_of(first)
    assert len(windows) == 2  # two symbols are no evidence of a facade by themselves: this one is the one the ground floor found
    assert all(o.from_elevation and (o.z0, o.z1) == pytest.approx((1.0, 2.3)) for o in windows)
    assert first.report.elevations and first.report.elevations[0]["matched"] == 2


def test_without_level_marks_in_the_elevations_the_storeys_stand_on_their_walls_and_the_slab(tmp_path):
    report, _ = run(house_sheet(tmp_path / "h.dxf", marks=False, eaves=2000), tmp_path)
    ground, first = report.floors
    assert first.level == pytest.approx(ground.height + DEFAULT_SLAB)


def test_a_slab_drawn_in_the_elevation_with_two_lines_gives_its_thickness(tmp_path):
    report, out = run(house_sheet(tmp_path / "h.dxf", soffit=280), tmp_path)
    ground, first = report.floors
    assert first.slab == pytest.approx(0.40, abs=0.005) and first.level == pytest.approx(3.2, abs=0.01)
    assert ground.height == pytest.approx(2.8, abs=0.01)  # the walls end under the slab: 3.2 less 40 cm
    assert any("solaio di 40 cm" in w and "letto nei prospetti" in w for w in report.warnings)
    from helpers import Obj

    obj = Obj(out)
    (_, s0, _), (_, s1, _) = obj.bbox("P1_Solaio")
    assert (s0, s1) == pytest.approx((2.8, 3.0), abs=0.01)  # the floors of the rooms (20 cm) take the top of it
    assert min(obj.bbox(g)[0][1] for g in obj.groups if g.startswith("P1_Pavimento")) == pytest.approx(3.0, abs=0.01)


def test_a_slab_the_user_gives_beats_the_one_drawn(tmp_path):
    report, _ = run(house_sheet(tmp_path / "h.dxf", soffit=280), tmp_path, slab_thickness=0.25)
    assert report.floors[1].slab == pytest.approx(0.25) and report.floors[0].height == pytest.approx(2.95, abs=0.01)
    assert not any("letto nei prospetti" in w for w in report.warnings)


def test_no_opening_stands_higher_than_the_walls_of_its_storey_once_they_end_under_the_slab():
    from dwg2c4d.config import Config
    from dwg2c4d.openings import Opening
    from dwg2c4d.stacking import Floor, fit_openings
    from shapely.geometry import Polygon

    tall = Opening("door", Polygon(), Polygon(), 0.0, 3.1, None)
    low = Opening("window", Polygon(), Polygon(), 0.9, 2.2, None)
    floor = Floor(0, 1, "t", None, Config(wall_height=2.9), [tall, low])
    fit_openings([floor])
    assert (tall.z1, low.z1) == (2.9, 2.2)


def test_a_first_floor_titled_through_its_copy_takes_the_elevation_row_of_the_first_floor(tmp_path):
    report, _ = run(house_sheet(tmp_path / "h.dxf", title_on_copy=True, upper_width=900, first_floor_windows=(0, 1, 2)), tmp_path)
    first = report.floors[1]
    assert "PIANTA PIANO PRIMO" in first.how and first.view != report.analysis.plan.id
    assert all(o.from_elevation and (o.z0, o.z1) == pytest.approx((1.0, 2.3)) for o in windows_of(first))  # not the ground floor's row
