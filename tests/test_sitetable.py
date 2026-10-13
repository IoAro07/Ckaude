"""The table of the site: what was read of every surface, curb, wall and stair, and the cells that correct it by hand."""

import csv

import pytest
from helpers import Obj
from test_sitesteps import plan_with_stair
from test_sitesteps import run as run_stair
from test_sitesurfaces import converted, surface, wall_at

from dwg2c4d import Config, convert
from dwg2c4d.cli import build_parser, config_from_args, main
from dwg2c4d.sitetable import COLUMNS


def rows_of(path):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh, delimiter=";"))


def write_edits(path, edits):
    """A table with these cells filled: ``edits`` is {id: {column: value}}."""
    rows = []
    for rid, cells in edits.items():
        row = dict.fromkeys(COLUMNS, "")
        row["id"] = rid
        row.update({f"MODIFICA_{k}": v for k, v in cells.items()})
        rows.append(row)
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, delimiter=";")
        w.writeheader()
        w.writerows(rows)
    return path


# --- writing -----------------------------------------------------------------------------------------------

def test_the_table_lists_every_object_of_the_site_with_what_was_read(tmp_path):
    report = converted(tmp_path)
    rows = rows_of(tmp_path / "s_sito.csv")
    by_id = {r["id"]: r for r in rows}
    ids = [s.id for s in report.site_surfaces.surfaces] + [w.id for w in report.site_surfaces.walls]
    assert sorted(by_id) == sorted(ids) and report.site_table_path == tmp_path / "s_sito.csv"
    lawn = by_id[surface(report, "prato", (13, 37)).id]
    assert lawn["oggetto"] == "superficie" and lawn["tipo"] == "prato" and float(lawn["area_m2"].replace(",", ".")) > 100
    assert "PRATO" in lawn["perche"]
    wall = by_id["Sito_Muro_01"]
    assert wall["oggetto"] == "muro" and wall["altezza_cm"] == "50" and wall["lunghezza_m"] != ""
    assert all(r["MODIFICA_tipo"] == r["MODIFICA_quota"] == r["MODIFICA_tieni"] == "" for r in rows)


def test_the_stairs_are_in_the_table_too_and_it_is_not_written_when_there_is_nothing_of_the_site(tmp_path):
    run_stair(plan_with_stair(tmp_path), tmp_path, site_surfaces=True)
    rows = rows_of(tmp_path / "s_sito.csv")
    assert [r["id"] for r in rows] == ["Sito_Gradini_01"] and rows[0]["alzata_cm"] == "17"
    plain = run_stair(plan_with_stair(tmp_path), tmp_path)
    assert plain.site_table_path is None and plain.stairs == []


def test_the_table_is_not_written_when_the_user_does_not_want_tables(tmp_path):
    report = converted(tmp_path, write_site_table=False)
    assert report.site_table_path is None and not (tmp_path / "s_sito.csv").exists()


# --- reading -----------------------------------------------------------------------------------------------

def test_a_cell_that_changes_the_kind_renames_the_object_and_gives_it_the_material_and_level_of_the_new_kind(tmp_path):
    first = converted(tmp_path)
    room = surface(first, "pavimentazione", (8, 18))
    table = write_edits(tmp_path / "e.csv", {room.id: {"tipo": "prato"}})
    report = converted(tmp_path, site_table_in=str(table))
    moved = surface(report, "prato", (8, 18))
    assert moved.id.startswith("Sito_Prato_") and moved.top == pytest.approx(-0.05)
    obj = Obj(tmp_path / "s.obj")
    assert moved.id in obj.groups
    text = (tmp_path / "s.obj").read_text().splitlines()
    assert text[text.index(f"o {moved.id}") + 1] == "usemtl Sito_Prato"
    assert len({s.id for s in report.site_surfaces.surfaces}) == len(report.site_surfaces.surfaces)  # the names stay one for each


def test_a_level_a_height_and_a_riser_from_the_table_beat_what_was_read(tmp_path):
    first = converted(tmp_path)
    room, curb = surface(first, "pavimentazione", (8, 18)), wall_at(first, 31, 42.1)
    table = write_edits(tmp_path / "e.csv", {room.id: {"quota": "-10"}, curb.id: {"altezza": "30"}})
    report = converted(tmp_path, site_table_in=str(table))
    assert surface(report, "pavimentazione", (8, 18)).top == pytest.approx(-0.10)
    changed = wall_at(report, 31, 42.1)
    assert changed.height == pytest.approx(0.30) and changed.height_source == "tabella"
    obj = Obj(tmp_path / "s.obj")
    assert obj.bbox(changed.id)[1][1] == pytest.approx(0.30)
    stairs = run_stair(plan_with_stair(tmp_path), tmp_path, site_surfaces=True,
                       site_table_in=str(write_edits(tmp_path / "t.csv", {"Sito_Gradini_01": {"alzata": "20"}})))
    assert stairs.stairs[0].rise == pytest.approx(0.20) and stairs.stairs[0].rise_source == "tabella"
    assert Obj(tmp_path / "s.obj").bbox("Sito_Gradini_01")[1][1] == pytest.approx(9 * 0.20)


def test_a_wall_can_be_made_a_curb_and_the_other_way_round(tmp_path):
    first = converted(tmp_path)
    wall, curb = wall_at(first, 30, 36.2), wall_at(first, 31, 42.1)
    table = write_edits(tmp_path / "e.csv", {wall.id: {"tipo": "cordolo"}, curb.id: {"tipo": "muro"}})
    report = converted(tmp_path, site_table_in=str(table))
    assert wall_at(report, 30, 36.2).kind == "cordolo" and wall_at(report, 30, 36.2).height == pytest.approx(0.15)
    assert wall_at(report, 31, 42.1).kind == "muro" and wall_at(report, 31, 42.1).height == pytest.approx(0.5)


def test_an_object_the_table_says_to_drop_is_left_out_of_the_model(tmp_path):
    first = converted(tmp_path)
    room = surface(first, "pavimentazione", (8, 18))
    curb = wall_at(first, 31, 42.1)
    table = write_edits(tmp_path / "e.csv", {room.id: {"tieni": "no"}, curb.id: {"tieni": "no"}})
    report = converted(tmp_path, site_table_in=str(table))
    obj = Obj(tmp_path / "s.obj")
    assert room.id not in obj.groups and curb.id not in obj.groups
    assert not any(w.geom.contains(curb.geom.representative_point()) and w.keep for w in report.site_surfaces.walls)


def test_what_the_table_gets_wrong_is_said_and_never_stops_the_conversion(tmp_path):
    first = converted(tmp_path)
    room = surface(first, "pavimentazione", (8, 18))
    table = write_edits(tmp_path / "e.csv", {"Sito_Prato_77": {"tipo": "prato"}, room.id: {"tipo": "giardinetto", "quota": "5000"}})
    report = converted(tmp_path, site_table_in=str(table))
    notes = " ".join(report.warnings)
    assert "l'id Sito_Prato_77 non esiste" in notes and "tipo 'giardinetto' sconosciuto" in notes
    assert surface(report, "pavimentazione", (8, 18)).top == 0.0  # nothing of that row was applied
    empty = write_edits(tmp_path / "n.csv", {room.id: {}})
    assert any("nessuna riga con celle MODIFICA_*" in w for w in converted(tmp_path, site_table_in=str(empty)).warnings)


def test_a_table_for_a_sheet_with_no_site_says_there_is_nothing_to_apply(tmp_path):
    from test_views import add_plan, add_title, new_sheet

    doc, msp = new_sheet()
    add_plan(msp, 0, 0)
    add_title(msp, "PIANTA PIANO TERRA", 100, -150)
    doc.saveas(tmp_path / "p.dxf")
    table = write_edits(tmp_path / "e.csv", {"Sito_Prato_01": {"tipo": "prato"}})
    report = convert(tmp_path / "p.dxf", tmp_path / "p.obj", Config(auto=True, site_surfaces=True, images=False, site_table_in=str(table)))
    assert any("Tabella del sito indicata, ma non ho trovato" in w for w in report.warnings)


def test_the_table_that_was_given_is_never_overwritten(tmp_path):
    converted(tmp_path)
    given = tmp_path / "s_sito.csv"
    report = converted(tmp_path, site_table_in=str(given))
    assert report.site_table_path == tmp_path / "s_sito_nuova.csv" and given.exists()


# --- the command line --------------------------------------------------------------------------------------

def test_the_options_of_the_command_line(tmp_path):
    args = build_parser().parse_args(["x.dxf", "--tabella-sito", "t.csv"])
    cfg = config_from_args(args)
    assert cfg.site_table_in == "t.csv" and cfg.site_surfaces and cfg.write_site_table
    off = config_from_args(build_parser().parse_args(["x.dxf", "--no-superfici-sito", "--no-tabella"]))
    assert not off.site_surfaces and not off.write_site_table


def test_the_command_line_says_where_the_table_of_the_site_is(tmp_path, capsys):
    from test_sitesurfaces import site_sheet

    path = site_sheet(tmp_path / "c.dxf")
    assert main([str(path), "-o", str(tmp_path / "c.obj"), "--no-immagini"]) == 0
    out = capsys.readouterr().out
    assert "c_sito.csv" in out and "--tabella-sito" in out and "Superfici del sito" in out
