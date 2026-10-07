"""A real plan (the author's Planimetria_1, furniture layers removed): the numbers that were checked by hand.

Layers follow the naming convention (18 Muri, 19 Fondelli, 20 Battiscopa, 21 Pavimenti, 1 Porte, 2 Finestre);
the texts (room names, "h 300", sizes like 120/150, "ht 100") are exploded into lines on layer 0.
"""

from pathlib import Path

import pytest
from helpers import Obj

from dwg2c4d import Config, convert

PLAN = Path(__file__).parent / "data" / "planimetria_1_ridotta.dxf"


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    out = tmp_path_factory.mktemp("real") / "p.obj"
    return convert(PLAN, out, Config(fixtures="detailed", fixtures_per_opening=True, c4d_json=True, images=True))


def test_units_were_corrected_and_the_building_has_its_size(report):
    assert report.unit == "cm" and any("compatibili con 'cm'" in w for w in report.warnings)
    assert report.size_m == pytest.approx((14.84, 8.50), abs=0.02)


def test_every_opening_is_found_with_the_width_and_direction_of_its_symbol(report):
    by_id = {o.id: o for o in report.openings}
    assert {k: round(o.width, 2) for k, o in by_id.items() if k[0] == "P"} == {"P01": 0.85, "P02": 0.90, "P03": 1.35}
    assert {k: round(o.width, 2) for k, o in by_id.items() if k[0] == "F"} == {
        "F01": 1.2, "F02": 2.2, "F03": 4.5, "F04": 3.0, "F05": 1.5, "F06": 1.2}
    # all of them sit in a wall: their thickness is the wall's, never a crossing wall's (was 1.2 m, found by eye)
    assert all(0.09 <= o.thickness <= 0.21 for o in report.openings if o.kind != "passage")
    assert by_id["P03"].axis == pytest.approx((1.0, 0.0), abs=1e-6)  # in an east-west wall
    assert by_id["P02"].axis == pytest.approx((0.0, 1.0), abs=1e-6)  # in a north-south wall


def test_the_doors_swing_where_the_arcs_say(report):
    hinge = {o.id: o.leaves[0]["hinge"] for o in report.openings if o.kind == "door"}
    assert set(hinge) == {"P01", "P02", "P03"} and all(h in (-1, 1) for h in hinge.values())
    assert all(o.src["leaves"] == "arco" for o in report.openings if o.kind == "door")


def test_exploded_texts_gave_sizes_sills_and_the_wall_height(report):
    assert report.wall_height == 3.0 and report.wall_height_source == "scritta"
    assert report.labels == 9
    by_id = {o.id: o for o in report.openings}
    assert (by_id["F03"].z0, by_id["F03"].z1) == (0.0, pytest.approx(2.7))  # 450 x 270, no sill written: to the floor
    assert (by_id["F01"].z0, by_id["F01"].z1) == (pytest.approx(1.0), pytest.approx(2.5))  # 120 x 150, ht 100
    assert by_id["P01"].z1 == pytest.approx(2.1)


def test_rooms_and_their_names(report):
    names = {r["name"] for r in report.rooms}
    assert {"Bagno", "Camera da letto", "Disimpegno"} <= names and any("Soggiorno" in n for n in names)
    assert any(r["height"] == 3.0 for r in report.rooms)


def test_a_size_without_a_door_symbol_is_reported(report):
    assert any("Scritta '120 210'" in w for w in report.warnings)


def test_objects(report):
    groups = set(Obj(report.output).groups)
    assert {"Muri", "Tramezzi", "Battiscopa", "Telai_F03", "Ante_P01", "Vetri_F03", "Maniglie_P03"} <= groups
    assert any(g.startswith("Pavimento_Bagno") for g in groups)


def test_the_check_files_exist(report):
    for path in (report.table_path, report.report_path, report.preview_path, report.json_path):
        assert path is not None and path.stat().st_size > 500
