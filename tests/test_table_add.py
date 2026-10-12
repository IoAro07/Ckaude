"""The openings table can add an opening the detector missed: a row with no sigla, its x, y, kind and width."""

import csv

import pytest
from builders import PLAN_AREA, building
from helpers import Obj

from dwg2c4d import Config, convert
from dwg2c4d.table import COLUMNS, read_table


@pytest.fixture
def plan(tmp_path):
    doc, _ = building()  # 10 x 6 m, 30 cm walls, a door and a window in the south wall, a window in the north wall
    path = tmp_path / "p.dxf"
    doc.saveas(path)
    return path


def run(plan, tmp_path, name="o", **kw):
    return convert(plan, tmp_path / f"{name}.obj", Config(area=PLAN_AREA, fixtures="detailed", **kw))


def add_row(path, **cells):
    """Append a row with no sigla, the way a user adds an opening: only the cells given are filled."""
    row = [cells.get(name, "") for name in COLUMNS]
    with open(path, "a", newline="", encoding="utf-8-sig") as fh:
        csv.writer(fh, delimiter=";").writerow(row)


def test_a_row_without_sigla_adds_a_window_in_the_wall_nearest_to_it(plan, tmp_path):
    first = run(plan, tmp_path)
    known = {o.id: (o.kind, o.center, o.width) for o in first.openings}
    add_row(first.table_path, x="1000", y="310", MODIFICA_tipo="finestra", MODIFICA_larghezza="150")  # a bit off the wall's middle
    again = run(plan, tmp_path, "o2", table_in=str(first.table_path))
    assert (again.windows, again.doors) == (first.windows + 1, first.doors)
    new = next(o for o in again.openings if o.id not in known)
    assert new.id == "F03" and new.kind == "window" and new.src["kind"] == "tabella" and new.src["width"] == "tabella"
    assert new.width == pytest.approx(1.5) and new.thickness == pytest.approx(0.30, abs=0.01)
    assert new.center == pytest.approx((9.85, 3.1), abs=0.01) and abs(new.axis[1]) == pytest.approx(1.0)  # on the wall's middle, along y
    assert (new.z0, new.z1) == (pytest.approx(0.9), pytest.approx(2.2)) and any("tabella" in n for n in new.notes)
    assert {o.id: (o.kind, o.center, o.width) for o in again.openings if o.id in known} == known  # the others keep sigla and size
    assert len(Obj(again.output).groups["Vetri"]) == len(Obj(first.output).groups["Vetri"]) + 6  # one more pane
    assert Obj(again.output).volume("Muri") < Obj(first.output).volume("Muri") - 0.2  # the hole is cut in the wall
    assert any(r["id"] == "F03" and r["larghezza"] == "150" for r in read_table(again.table_path))  # and it is in the table now


def test_a_row_without_sigla_adds_a_door_with_its_options(plan, tmp_path):
    first = run(plan, tmp_path)
    add_row(first.table_path, x="15", y="300", MODIFICA_tipo="porta", MODIFICA_larghezza="90", MODIFICA_cerniera="doppia")
    again = run(plan, tmp_path, "o2", table_in=str(first.table_path))
    new = next(o for o in again.openings if o.id == "P02")
    assert new.kind == "door" and new.z0 == 0.0 and sorted(l["hinge"] for l in new.leaves) == [-1, 1]
    assert new.center == pytest.approx((0.15, 3.0), abs=0.01) and new.width == pytest.approx(0.9)


def test_the_cells_of_the_plain_columns_are_enough_for_a_new_row(plan, tmp_path):
    first = run(plan, tmp_path)
    add_row(first.table_path, x="500", y="585", tipo="finestra", larghezza="120", davanzale="80")
    again = run(plan, tmp_path, "o2", table_in=str(first.table_path))
    new = next(o for o in again.openings if o.id == "F03")
    assert new.width == pytest.approx(1.2) and new.z0 == pytest.approx(0.8) and new.center[1] == pytest.approx(5.85, abs=0.01)


def test_a_new_row_far_from_every_wall_is_reported_and_adds_nothing(plan, tmp_path):
    first = run(plan, tmp_path)
    add_row(first.table_path, x="500", y="300", MODIFICA_tipo="finestra", MODIFICA_larghezza="120")  # the middle of the room
    again = run(plan, tmp_path, "o2", table_in=str(first.table_path))
    assert any("nessun muro" in w for w in again.warnings) and again.windows == first.windows


@pytest.mark.parametrize("cells", [
    {"x": "985", "MODIFICA_tipo": "finestra", "MODIFICA_larghezza": "120"},  # no y
    {"x": "985", "y": "300", "MODIFICA_larghezza": "120"},  # no kind
    {"x": "985", "y": "300", "MODIFICA_tipo": "finestra"},  # no width
    {"x": "985", "y": "300", "MODIFICA_tipo": "cancello", "MODIFICA_larghezza": "120"},  # unknown kind
])
def test_a_new_row_that_lacks_what_it_needs_says_so(plan, tmp_path, cells):
    first = run(plan, tmp_path)
    add_row(first.table_path, **cells)
    again = run(plan, tmp_path, "o2", table_in=str(first.table_path))
    assert any("riga senza sigla" in w for w in again.warnings) and (again.windows, again.doors) == (first.windows, first.doors)


def test_a_blank_row_is_no_new_opening(plan, tmp_path):
    first = run(plan, tmp_path)
    add_row(first.table_path)
    again = run(plan, tmp_path, "o2", table_in=str(first.table_path))
    assert not any("Tabella" in w for w in again.warnings) and again.windows == first.windows
