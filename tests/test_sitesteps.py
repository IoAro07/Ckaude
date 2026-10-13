"""The steps of the stairs outside the building: treads and risers built from the rows of step lines the plan draws."""

import json

import pytest
from builders import PLAN_AREA, building
from helpers import Obj
from shapely.geometry import box
from test_falsewalls import house, steps

from dwg2c4d import Config, convert
from dwg2c4d.falsewalls import FalseWalls, drop_stair_steps
from dwg2c4d.mesh import Mesh
from dwg2c4d.qa import summary_lines
from dwg2c4d.sitesteps import DEFAULT_RISE, add_stairs, build_stairs


def outlines(items):
    false = FalseWalls()
    drop_stair_steps(items, false)
    return false.stairs


def stairs_of(row, building_shape=None, levels=()):
    """The stairs built from a row of step lines next to a 10 x 6 m building (metres)."""
    return build_stairs(outlines(house() + row), building_shape or box(0, 0, 10, 6), list(levels))


# --- the stair, as a shape and as a solid ------------------------------------------------------------------

def test_a_stair_outside_the_building_is_built_with_one_step_more_than_the_lines_between_the_treads():
    stairs = stairs_of(steps(n=8, x0=-1.3, y0=-4.0, width=1.2, pitch=0.28))  # 8 lines, below the building
    assert len(stairs) == 1
    st = stairs[0]
    assert st.count == 9 and st.rise == DEFAULT_RISE and st.rise_source == "predefinita"
    assert st.tread == pytest.approx(0.28, abs=0.02) and st.id == "Sito_Gradini_01"


def test_the_stair_climbs_towards_the_end_that_touches_the_building():
    row = steps(n=8, x0=2.0, y0=-2.6, width=1.2, pitch=0.28)  # its last tread is next to the south wall (y = 0)
    st = stairs_of(row)[0]
    assert st.toward == "il lato che tocca la casa"
    foot, top = st.region(1).centroid, st.region(st.count).centroid
    assert top.y > foot.y  # up, towards y = 0
    flipped = stairs_of(steps(n=8, x0=2.0, y0=6.4, width=1.2, pitch=0.28))[0]  # the same stair on the north side
    assert flipped.toward == "il lato che tocca la casa" and flipped.region(1).centroid.y > flipped.region(flipped.count).centroid.y


def test_a_stair_that_touches_neither_end_or_both_climbs_towards_the_house():
    st = stairs_of(steps(n=8, x0=2.0, y0=-9.0, width=1.2, pitch=0.28))[0]
    assert st.toward == "la casa"
    assert st.region(st.count).centroid.y > st.region(1).centroid.y


def test_each_riser_is_one_riser_higher_and_the_solid_is_closed():
    st = stairs_of(steps(n=8, x0=2.0, y0=-2.6, width=1.2, pitch=0.28))[0]
    mesh = Mesh()
    add_stairs(mesh, [st])
    z = [v[2] for v in mesh.vertices]
    assert min(z) == pytest.approx(-0.20) and max(z) == pytest.approx(9 * DEFAULT_RISE)
    assert sorted({round(v, 4) for v in z if v > 0}) == [round(k * DEFAULT_RISE, 4) for k in range(1, 10)]


def test_a_level_of_the_storey_above_that_the_risers_can_reach_sets_the_riser():
    row = steps(n=8, x0=2.0, y0=-2.6, width=1.2, pitch=0.28)
    st = stairs_of(row, levels=[1.5])[0]  # 9 risers of 16.7 cm
    assert st.rise_source == "piano" and st.rise == pytest.approx(1.5 / 9) and st.height == pytest.approx(1.5)
    assert stairs_of(row, levels=[3.2])[0].rise_source == "predefinita"  # 3.2 m is not 9 plausible risers
    near = stairs_of(row, levels=[1.2, 1.6, 3.2])[0]
    assert near.rise == pytest.approx(1.6 / 9)  # the plausible level nearest to the default riser


def test_a_stair_of_the_house_is_not_built_here():
    row = steps(n=8, x0=2.0, y0=0.6, width=1.2, pitch=0.28)  # inside the 10 x 6 m building
    assert stairs_of(row) == []


def test_a_row_of_lines_that_is_no_stair_gives_no_steps():
    assert build_stairs([], box(0, 0, 10, 6), []) == []


# --- the conversion ----------------------------------------------------------------------------------------

def plan_with_stair(tmp_path):
    doc, msp = building()
    for k in range(8):  # eight lines 28 cm apart, 1.2 m wide, west of the building: running east, its last tread by the wall
        x = -250 + k * 28
        msp.add_line((x, 200), (x, 320), dxfattribs={"layer": "MURI"})
    path = tmp_path / "s.dxf"
    doc.saveas(path)
    return path


def run(path, tmp_path, **cfg):
    config = dict(auto=True, units="cm", images=False, floors_to_outer_face=False)
    config.update(cfg)
    return convert(path, tmp_path / "s.obj", Config(**config))


def test_the_stair_of_the_plan_becomes_steps_in_a_group_of_its_own(tmp_path):
    report = run(plan_with_stair(tmp_path), tmp_path, site_surfaces=True)
    assert len(report.stairs) == 1 and report.stairs[0].count == 9
    obj = Obj(tmp_path / "s.obj")
    lo, hi = obj.bbox("Sito_Gradini_01")
    assert hi[1] == pytest.approx(9 * DEFAULT_RISE, abs=1e-6)
    assert hi[0] > lo[0] and (hi[0] - lo[0]) == pytest.approx(9 * 0.28, abs=0.1)  # nine treads, along x
    assert obj.open_edges("Sito_Gradini_01") == 0 and obj.winding_matches_normals("Sito_Gradini_01")
    assert "newmtl Sito_Gradini" in (tmp_path / "s.mtl").read_text()
    assert any("Gradini:" in w and "Sito_Gradini_NN" in w and "17 cm" in w for w in report.warnings)
    assert any("Gradini" in line and "9 gradini alti 17 cm" in line for line in summary_lines(report))


def test_without_the_option_the_stair_stays_as_it_was(tmp_path):
    report = run(plan_with_stair(tmp_path), tmp_path)
    assert report.stairs == [] and "Sito_Gradini_01" not in Obj(tmp_path / "s.obj").groups
    assert report.false_walls.stairs and any("1 scale" in w for w in report.warnings)


def test_the_steps_are_in_the_json_of_cinema_4d_in_the_null_of_the_site(tmp_path):
    run(plan_with_stair(tmp_path), tmp_path, site_surfaces=True, c4d_json=True)
    data = json.loads((tmp_path / "s_model.json").read_text())
    stair = next(o for o in data["objects"] if o["name"] == "Sito_Gradini_01")
    assert stair["group"] == "Superfici_sito" and stair["material"] == "site_steps" and "site_steps" in data["materials"]


def test_a_failure_in_the_steps_is_never_the_end_of_the_conversion(tmp_path, monkeypatch):
    import dwg2c4d.pipeline as pipeline

    def boom(*args, **kwargs):
        raise RuntimeError("che sfortuna")

    monkeypatch.setattr(pipeline, "build_stairs", boom)
    report = run(plan_with_stair(tmp_path), tmp_path, site_surfaces=True)
    assert report.stairs == [] and any("Gradini non costruiti" in w and "che sfortuna" in w for w in report.warnings)
    assert report.wall_pieces > 0


def test_the_note_of_the_stairs_left_out_of_the_walls_says_when_the_outside_ones_are_built():
    false = FalseWalls()
    drop_stair_steps(house() + steps(), false)
    assert "la scala non e' modellata" in " ".join(false.notes(0.5, 0.15))
    built = " ".join(false.notes(0.5, 0.15, steps_built=True))
    assert "si costruiscono a gradini" in built and "la scala non e' modellata" not in built
