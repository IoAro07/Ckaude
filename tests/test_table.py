"""The openings table: written next to the model, corrected by hand, applied on the next run."""

import csv

import pytest
from builders import DOOR, PLAN_AREA, T, WIN_N, WIN_S, building
from helpers import Obj

from dwg2c4d import Config, convert
from dwg2c4d.cli import main
from dwg2c4d.table import COLUMNS, read_table


@pytest.fixture
def plan(tmp_path):
    doc, _ = building()
    path = tmp_path / "p.dxf"
    doc.saveas(path)
    return path


def run(plan, tmp_path, name="o", **kw):
    cfg = Config(area=PLAN_AREA, fixtures="detailed", **kw)
    return convert(plan, tmp_path / f"{name}.obj", cfg)


def by_kind(report):
    """{(kind, 'N'/'S'): opening} for the three openings of tests/builders.building()."""
    out = {}
    for o in report.openings:
        out[(o.kind, "N" if o.center[1] > 3.0 else "S")] = o
    return out


def edit(path, changes):
    """changes: {id: {column: value}}; writes the file back the way Excel would (same format)."""
    rows = read_table(path)
    for row in rows:
        row.update(changes.get(row["id"], {}))
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, delimiter=";")
        w.writeheader()
        w.writerows(rows)


def ids(report):
    k = by_kind(report)
    return {"door": k[("door", "S")].id, "win_s": k[("window", "S")].id, "win_n": k[("window", "N")].id}


# --- writing ---------------------------------------------------------------------------------

def test_table_is_written_by_default_with_one_row_per_opening(plan, tmp_path):
    rep = run(plan, tmp_path)
    assert rep.table_path == tmp_path / "o_aperture.csv"
    raw = rep.table_path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # BOM: Excel opens it as UTF-8
    rows = read_table(rep.table_path)
    assert len(rows) == 3 and list(rows[0]) == COLUMNS
    assert {r["tipo"] for r in rows} == {"porta", "finestra"}
    door = next(r for r in rows if r["tipo"] == "porta")
    assert door["larghezza"] == "90" and door["spessore_muro"] == "30"
    assert door["x"] == "245" and door["y"] == "15"  # drawing coordinates, cm
    assert all(r["MODIFICA_larghezza"] == "" for r in rows)


def test_numbers_use_the_decimal_comma(plan, tmp_path):
    rep = run(plan, tmp_path, window_sill=0.905)
    win = next(r for r in read_table(rep.table_path) if r["tipo"] == "finestra")
    assert win["davanzale"] == "90,5"


def test_no_table_when_disabled(plan, tmp_path):
    rep = run(plan, tmp_path, write_table=False)
    assert rep.table_path is None and not (tmp_path / "o_aperture.csv").exists()


def test_source_columns_say_where_the_numbers_come_from(plan, tmp_path):
    rows = read_table(run(plan, tmp_path).table_path)
    assert all("width" in r["origine_misure"] for r in rows)


# --- applying edits --------------------------------------------------------------------------

def test_empty_edit_columns_change_nothing(plan, tmp_path):
    first = run(plan, tmp_path)
    again = run(plan, tmp_path, "o2", table_in=str(first.table_path))
    assert Obj(first.output).volume("Muri") == pytest.approx(Obj(again.output).volume("Muri"))
    assert [o.width for o in first.openings] == [o.width for o in again.openings]


def test_width_edit_resizes_the_opening(plan, tmp_path):
    first = run(plan, tmp_path)
    wid = ids(first)["win_s"]
    edit(first.table_path, {wid: {"MODIFICA_larghezza": "200"}})
    again = run(plan, tmp_path, "o2", table_in=str(first.table_path))
    win = by_kind(again)[("window", "S")]
    assert win.width == pytest.approx(2.0) and win.src["width"] == "tabella"
    glass = Obj(again.output).bbox("Vetri")
    assert glass[1][0] - glass[0][0] > 1.5
    assert not again.warnings or not any("Tabella" in w for w in again.warnings)


def test_sill_and_height_edit_move_the_window(plan, tmp_path):
    first = run(plan, tmp_path)
    wid = ids(first)["win_n"]
    edit(first.table_path, {wid: {"MODIFICA_davanzale": "60", "MODIFICA_altezza": "150"}})
    win = by_kind(run(plan, tmp_path, "o2", table_in=str(first.table_path)))[("window", "N")]
    assert win.z0 == pytest.approx(0.6) and win.z1 == pytest.approx(2.1)
    assert win.src["sill"] == "tabella" and win.src["height"] == "tabella"


def test_a_head_above_the_wall_is_clamped_and_noted(plan, tmp_path):
    first = run(plan, tmp_path)
    wid = ids(first)["win_n"]
    edit(first.table_path, {wid: {"MODIFICA_davanzale": "100", "MODIFICA_altezza": "300"}})
    win = by_kind(run(plan, tmp_path, "o2", table_in=str(first.table_path)))[("window", "N")]
    assert win.z1 == pytest.approx(Config().wall_height)
    assert any("architrave" in n for n in win.notes)


def test_kind_edit_turns_a_window_into_a_door(plan, tmp_path):
    first = run(plan, tmp_path)
    wid = ids(first)["win_s"]
    edit(first.table_path, {wid: {"MODIFICA_tipo": "porta"}})
    again = run(plan, tmp_path, "o2", table_in=str(first.table_path))
    assert (again.doors, again.windows) == (2, 1)
    new = next(o for o in again.openings if o.id == wid)
    assert new.kind == "door" and new.z0 == 0.0 and new.leaves and new.src["kind"] == "tabella"
    assert new.glass is None  # no pane: it is a leaf now
    assert len(Obj(again.output).groups["Ante"]) == 2 * 6  # two door leaves, a box each


def test_sash_count_edit_for_a_window(plan, tmp_path):
    first = run(plan, tmp_path)
    wid = ids(first)["win_s"]
    edit(first.table_path, {wid: {"MODIFICA_ante": "3"}})
    again = run(plan, tmp_path, "o2", table_in=str(first.table_path))
    win = by_kind(again)[("window", "S")]
    assert win.sashes == 3 and [round(d, 3) for d in win.dividers] == [-0.2, 0.2]
    assert len(Obj(again.output).groups["Vetri"]) == 6 * (3 + 1)  # 3 panes here + 1 in the north window


def test_hinge_edit_moves_the_handle_to_the_other_side(plan, tmp_path):
    first = run(plan, tmp_path)
    did = ids(first)["door"]
    assert by_kind(first)[("door", "S")].leaves[0]["hinge"] == -1
    left_handle = [Obj(first.output).v[i][0] for ids_, _ in Obj(first.output).groups["Maniglie"]
                   for i in ids_ if 2.0 < Obj(first.output).v[i][0] < 2.9]
    edit(first.table_path, {did: {"MODIFICA_cerniera": "est"}})  # wall along x: est = +u
    again = run(plan, tmp_path, "o2", table_in=str(first.table_path))
    door = by_kind(again)[("door", "S")]
    assert door.leaves[0]["hinge"] == 1 and door.src["leaves"] == "tabella"
    obj = Obj(again.output)
    xs = [obj.v[i][0] for ids_, _ in obj.groups["Maniglie"] for i in ids_ if 2.0 < obj.v[i][0] < 2.9]
    assert max(xs) < 2.45 and min(left_handle) > 2.0  # the handle is now on the left half
    assert max(left_handle) > 2.5


def test_double_door_edit(plan, tmp_path):
    first = run(plan, tmp_path)
    did = ids(first)["door"]
    edit(first.table_path, {did: {"MODIFICA_cerniera": "doppia"}})
    door = by_kind(run(plan, tmp_path, "o2", table_in=str(first.table_path)))[("door", "S")]
    assert sorted(l["hinge"] for l in door.leaves) == [-1, 1]


def test_keep_no_closes_the_opening_with_wall(plan, tmp_path):
    first = run(plan, tmp_path)
    wid = ids(first)["win_n"]
    edit(first.table_path, {wid: {"MODIFICA_tieni": "no"}})
    again = run(plan, tmp_path, "o2", table_in=str(first.table_path))
    assert not next(o for o in again.openings if o.id == wid).keep
    assert Obj(again.output).volume("Muri") > Obj(first.output).volume("Muri") + 0.05
    # one pane less, and no frame parts at that window
    assert len(Obj(again.output).groups["Vetri"]) < len(Obj(first.output).groups["Vetri"])
    hi_frame = [Obj(again.output).v[i] for ids_, _ in Obj(again.output).groups["Telai"] for i in ids_]
    assert not any(3.0 < v[0] < 4.2 and v[2] < -5.5 for v in hi_frame)  # north wall (y ~ 6 m): nothing there


# --- bad input -------------------------------------------------------------------------------

def test_unknown_id_is_reported_and_the_other_rows_still_apply(plan, tmp_path):
    first = run(plan, tmp_path)
    wid = ids(first)["win_s"]
    edit(first.table_path, {wid: {"MODIFICA_larghezza": "150"}})
    with open(first.table_path, "a", newline="", encoding="utf-8-sig") as fh:
        row = [""] * len(COLUMNS)
        row[0], row[COLUMNS.index("MODIFICA_larghezza")] = "F99", "80"
        csv.writer(fh, delimiter=";").writerow(row)
    again = run(plan, tmp_path, "o2", table_in=str(first.table_path))
    assert any("F99" in w for w in again.warnings)
    assert by_kind(again)[("window", "S")].width == pytest.approx(1.5)


@pytest.mark.parametrize("column,value,fragment", [
    ("MODIFICA_larghezza", "5", "troppo piccola"),
    ("MODIFICA_tipo", "cancello", "sconosciuto"),
    ("MODIFICA_cerniera", "nord", "non valida"),  # the south wall runs along x: only ovest/est
    ("MODIFICA_ante", "20", "da 1 a 8"),
    ("MODIFICA_altezza", "abc", "could not convert"),
])
def test_invalid_cells_warn_and_leave_the_opening_untouched(plan, tmp_path, column, value, fragment):
    first = run(plan, tmp_path)
    did = ids(first)["door"]
    edit(first.table_path, {did: {column: value}})
    again = run(plan, tmp_path, "o2", table_in=str(first.table_path))
    assert any(did in w and fragment in w for w in again.warnings), again.warnings
    door = by_kind(again)[("door", "S")]
    assert door.width == pytest.approx(0.9) and door.src.get("width") != "tabella"


def test_a_comma_separated_table_is_read_too(plan, tmp_path):
    first = run(plan, tmp_path)
    wid = ids(first)["win_s"]
    rows = read_table(first.table_path)
    for row in rows:
        if row["id"] == wid:
            row["MODIFICA_larghezza"] = "140"
    path = tmp_path / "virgole.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:  # a comma-separated export, quoted where needed
        w = csv.DictWriter(fh, fieldnames=COLUMNS, delimiter=",")
        w.writeheader()
        w.writerows(rows)
    again = run(plan, tmp_path, "o2", table_in=str(path))
    assert by_kind(again)[("window", "S")].width == pytest.approx(1.4)


def test_the_input_table_is_never_overwritten(plan, tmp_path):
    first = run(plan, tmp_path)
    wid = ids(first)["win_s"]
    edit(first.table_path, {wid: {"MODIFICA_larghezza": "150"}})
    before = first.table_path.read_bytes()
    again = run(plan, tmp_path, "o", table_in=str(first.table_path))  # same output name -> same table path
    assert first.table_path.read_bytes() == before
    assert again.table_path.name == "o_aperture_nuova.csv"
    assert next(r for r in read_table(again.table_path) if r["id"] == wid)["larghezza"] == "150"


def test_missing_table_file_is_a_clear_error(plan, tmp_path):
    with pytest.raises(OSError):
        run(plan, tmp_path, table_in=str(tmp_path / "manca.csv"))


# --- command line ----------------------------------------------------------------------------

def test_cli_writes_the_table_and_applies_it(plan, tmp_path, capsys):
    out = tmp_path / "m.obj"
    area = "--area=" + ",".join(str(v) for v in PLAN_AREA)
    assert main([str(plan), "-o", str(out), area]) == 0
    assert "m_aperture.csv" in capsys.readouterr().out
    table = tmp_path / "m_aperture.csv"
    rows = read_table(table)
    wid = next(r["id"] for r in rows if r["tipo"] == "finestra" and float(r["y"].replace(",", ".")) < 100)
    edit(table, {wid: {"MODIFICA_larghezza": "180"}})
    out2 = tmp_path / "n.obj"
    assert main([str(plan), "-o", str(out2), area, "--tabella", str(table)]) == 0
    new = next(r for r in read_table(tmp_path / "n_aperture.csv") if r["id"] == wid)
    assert new["larghezza"] == "180"


def test_cli_no_table_flag(plan, tmp_path):
    area = "--area=" + ",".join(str(v) for v in PLAN_AREA)
    assert main([str(plan), "-o", str(tmp_path / "q.obj"), area, "--no-tabella"]) == 0
    assert not (tmp_path / "q_aperture.csv").exists()


def test_table_x_y_are_in_drawing_units_even_with_a_recentred_model(plan, tmp_path):
    rep = run(plan, tmp_path, origin="center")
    door = next(r for r in read_table(rep.table_path) if r["tipo"] == "porta")
    assert door["x"] == "245" and door["y"] == "15"
    assert (DOOR[0] + DOOR[1]) / 2 == 245 and T / 2 == 15 and WIN_N and WIN_S


def test_opening_axes_point_east_or_north(plan, tmp_path):
    """Hinge sides in the table are named from the axis: it must not depend on how edges were drawn."""
    for o in run(plan, tmp_path).openings:
        ux, uy = o.axis
        assert ux > 1e-9 or (abs(ux) <= 1e-9 and uy > 0)
