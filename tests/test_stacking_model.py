"""Stacked storeys in what the conversion writes: the roof on the top storey, the JSON frames of the openings of every storey, the
openings table with its storey column (whole conversions of synthetic sheets, centimetres)."""

import csv
import json

import ezdxf
import pytest
from ezdxf.math import Matrix44

from dwg2c4d import Config, convert
from dwg2c4d.table import rows_of_storey
from helpers import Obj
from test_roofview_sheet import add_hip_roof
from test_stacking_heights import house_sheet, windows_of
from test_views import add_title


def run(path, tmp_path, name="m", **kw):
    cfg = Config(auto=True, images=False, **kw)
    return convert(path, tmp_path / f"{name}.obj", cfg), tmp_path / f"{name}.obj"


def with_roof_view(path, at=(1800, 1500)):
    """``house_sheet`` and, far from both floors, the roof plan of the house: a hip roof with 60 cm of eaves round the 10 x 6 m walls."""
    doc = ezdxf.readfile(path)
    for name in ("TRATTEGGIO", "QUOTE", "sezioni"):
        doc.layers.add(name)
    msp = doc.modelspace()
    before = len(msp)
    add_hip_roof(msp, w=1000, d=600)
    add_title(msp, "PIANTA COPERTURA", 100, -250)
    for e in list(msp)[before:]:
        e.transform(Matrix44.translate(*at, 0))
    doc.saveas(path)
    return path


# --- the roof ---------------------------------------------------------------------------------------------------

def test_the_roof_of_the_sheet_stands_on_the_walls_of_the_top_storey(tmp_path):
    report, out = run(with_roof_view(house_sheet(tmp_path / "r.dxf")), tmp_path, roof_auto=True)
    top = report.floors[-1]
    eaves = top.level + top.height
    obj = Obj(out)
    assert eaves == pytest.approx(6.2, abs=0.01) and "P1_Tetto" in obj.groups and "Tetto" not in obj.groups
    (rx0, ry0, rz0), (rx1, ry1, rz1) = obj.bbox("P1_Tetto")
    assert ry0 == pytest.approx(eaves - 0.15, abs=0.02)  # the roof is 15 cm thick under the eaves
    assert ry1 > eaves + 1.0
    (wx0, _, wz0), (wx1, _, wz1) = obj.bbox("P1_Muri")
    assert (rx0, rx1) == pytest.approx((wx0 - 0.6, wx1 + 0.6), abs=0.1)  # 60 cm of eaves over the walls of the first floor
    assert report.roof["ridge_height"] > eaves + 1.0  # above the floor of the ground, not above the walls of the first floor
    assert report.roof["ridge_height"] == pytest.approx(ry1, abs=0.02)


def test_the_roof_is_registered_on_the_top_storey_not_on_the_converted_plan(tmp_path):
    path = with_roof_view(house_sheet(tmp_path / "r.dxf"), at=(1800, 1500))
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()
    for e in msp:  # the first floor is a little to the side of the ground floor: 3 m east, 1 m north of where it would stand
        if e.dxf.layer in ("MURI", "FINESTRE", "PORTE") and e.dxftype() == "LWPOLYLINE" and min(p[0] for p in e.get_points()) >= 2490:
            e.translate(300, 100, 0)
    doc.saveas(path)
    report, out = run(path, tmp_path, roof_auto=True)
    assert len(report.floors) == 2 and report.floors[1].shift == pytest.approx((-28.0, -1.0), abs=0.05)
    obj = Obj(out)
    (rx0, _, rz0), (rx1, _, rz1) = obj.bbox("P1_Tetto")
    (wx0, _, wz0), (wx1, _, wz1) = obj.bbox("P1_Muri")
    assert (rx0, rx1) == pytest.approx((wx0 - 0.6, wx1 + 0.6), abs=0.1)
    assert (rz0, rz1) == pytest.approx((wz0 - 0.6, wz1 + 0.6), abs=0.1)


def test_the_note_about_storeys_the_plan_does_not_show_goes_when_every_storey_is_converted(tmp_path):
    path = with_roof_view(house_sheet(tmp_path / "r.dxf"))
    stacked, _ = run(path, tmp_path, roof_auto=True)
    single, _ = run(path, tmp_path, "single", stacking="no", roof_auto=True)
    assert not any("mostrano piu' piani" in w for w in stacked.warnings)
    assert report_has(single, "mostrano piu' piani")


def report_has(report, text):
    return any(text in w for w in report.warnings)


# --- the JSON for Cinema 4D ------------------------------------------------------------------------------------

def test_the_frames_of_the_openings_of_an_upper_storey_are_in_the_frame_of_the_model(tmp_path):
    report, out = run(house_sheet(tmp_path / "h.dxf"), tmp_path, c4d_json=True, fixtures="detailed", fixtures_per_opening=True,
                      origin="drawing")
    model = json.loads(report.json_path.read_text())
    groups = {g["name"]: g for g in model["groups"]}
    first = windows_of(report.floors[1])[0]
    frame = groups[f"P1_{first.id}"]
    assert frame["label"] == first.id and frame["parent"] == "P1_Infissi" and groups["P1_Infissi"]["layer"] == "Infissi"
    shift = report.floors[1].shift
    assert frame["origin"][:2] == pytest.approx([(first.center[0] + shift[0]) * 100, (first.center[1] + shift[1]) * 100], abs=0.01)
    assert frame["origin"][2] == pytest.approx(report.floors[1].level * 100 + first.z0 * 100, abs=5.0)  # at the foot of the frame (a few cm under the sill)
    ground = windows_of(report.floors[0])[0]
    assert groups[f"P0_{ground.id}"]["origin"][:2] == pytest.approx([ground.center[0] * 100, ground.center[1] * 100], abs=0.01)
    names = {o["name"]: o["group"] for o in model["objects"]}
    assert names[f"P1_Telai_{first.id}"] == f"P1_{first.id}" and names[f"P0_Telai_{ground.id}"] == f"P0_{ground.id}"


def test_the_ids_of_the_openings_are_unique_in_the_model_once_the_storey_is_said(tmp_path):
    report, out = run(house_sheet(tmp_path / "h.dxf"), tmp_path, fixtures="detailed", fixtures_per_opening=True)
    keys = [o.key for o in report.openings]
    assert len(keys) == len(set(keys)) and any(k.startswith("P1_") for k in keys) and any(k.startswith("P0_") for k in keys)
    assert len({o.id for o in report.openings}) < len(keys)  # the siglas alone repeat from floor to floor
    groups = Obj(out).groups
    assert any(g.startswith("P1_Telai_") for g in groups) and any(g.startswith("P0_Telai_") for g in groups)


# --- the openings table -----------------------------------------------------------------------------------------

def read_rows(path):
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh, delimiter=";"))


def write_rows(path, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]), delimiter=";")
        w.writeheader()
        w.writerows(rows)


def test_the_table_of_a_stack_has_a_column_for_the_storey_and_a_row_for_every_opening(tmp_path):
    report, _ = run(house_sheet(tmp_path / "h.dxf"), tmp_path)
    rows = read_rows(report.table_path)
    assert list(rows[0])[:2] == ["piano", "id"]
    assert {r["piano"] for r in rows} == {"0", "1"} and len(rows) == len(report.openings)
    assert len({(r["piano"], r["id"]) for r in rows}) == len(rows)  # the siglas repeat, the pairs do not


def test_a_row_of_the_table_changes_the_opening_of_its_own_storey_only(tmp_path):
    path = house_sheet(tmp_path / "h.dxf")
    first, _ = run(path, tmp_path)
    rows = read_rows(first.table_path)
    twin = windows_of(first.floors[1])[0]
    row = next(r for r in rows if r["piano"] == "1" and r["id"] == twin.id)
    assert any(r["piano"] == "0" and r["id"] == twin.id for r in rows)  # the ground floor has an opening with that sigla too
    row["MODIFICA_larghezza"] = "150"
    write_rows(tmp_path / "t.csv", rows)
    second, _ = run(path, tmp_path, "second", table_in=str(tmp_path / "t.csv"))
    changed = next(o for o in second.openings if o.storey == 1 and o.id == twin.id)
    same = next(o for o in second.openings if o.storey == 0 and o.id == twin.id)
    assert changed.width == pytest.approx(1.5) and same.width != pytest.approx(1.5)
    assert changed.src["width"] == "tabella" and same.src["width"] != "tabella"


def test_a_table_written_for_one_storey_applies_to_the_converted_plan(tmp_path):
    path = house_sheet(tmp_path / "h.dxf")
    single, _ = run(path, tmp_path, stacking="no")
    rows = read_rows(single.table_path)
    assert "piano" not in rows[0]
    target = next(r for r in rows if r["tipo"] == "finestra")
    target["MODIFICA_larghezza"] = "170"
    write_rows(tmp_path / "t.csv", rows)
    stacked, _ = run(path, tmp_path, "stacked", table_in=str(tmp_path / "t.csv"))
    assert next(o for o in stacked.openings if o.storey == 0 and o.id == target["id"]).width == pytest.approx(1.7)
    assert not any(o.src.get("width") == "tabella" for o in stacked.openings if o.storey == 1)


def test_the_rows_of_other_storeys_are_told_when_the_model_is_not_stacked():
    rows = [{"piano": "0", "id": "F01"}, {"piano": "1", "id": "F01"}]
    warnings: list[str] = []
    assert rows_of_storey(rows, Config(), warnings) == rows[:1]
    assert warnings and "1 righe sono di altri piani" in warnings[0]
    assert rows_of_storey(rows, Config(part=True, storey=1), []) == rows[1:]
    assert rows_of_storey([{"id": "F01"}], Config(part=True, storey=1), []) == []
    assert rows_of_storey([{"id": "F01"}], Config(), []) == [{"id": "F01"}]
