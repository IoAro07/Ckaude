"""The garden: ground outside the building, a dug pool, plants and furniture from blocks."""

import json
import math
from collections import Counter

import pytest
from builders import D, W, building, south_elevation
from helpers import Obj

from dwg2c4d import Config, convert
from dwg2c4d.cli import main
from dwg2c4d.config import LayerRules
from dwg2c4d.garden import block_kind, kind_from_colour
from dwg2c4d.garden_table import read_garden_table
from dwg2c4d.reader import layer_summary

GREEN, GREY, BLUE, BROWN = (60, 140, 60), (150, 150, 155), (120, 170, 220), (160, 110, 70)


def rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def hatch(msp, outline, rgb=GREY, layer="Retini", holes=(), pattern=None):
    h = msp.add_hatch(dxfattribs={"layer": layer})
    h.rgb = rgb
    h.paths.add_polyline_path(outline, is_closed=True)
    for hole in holes:
        h.paths.add_polyline_path(hole, is_closed=True)
    if pattern:
        h.set_pattern_fill(pattern, scale=10)
    return h


def block(doc, name, outline):
    doc.blocks.new(name).add_lwpolyline(outline, close=True)


def save(doc, tmp_path, name="g"):
    path = tmp_path / f"{name}.dxf"
    doc.saveas(path)
    return path


def garden_plan(tmp_path, with_pool=True, with_objects=True):
    """The 10 x 6 m building with, to the south (y < 0): a lawn 8 x 5 m, a paving 6 x 5 m with a pool in it, a hedge
    and a tree on the lawn, a sunbed on the paving, a shrub."""
    doc, msp = building()
    doc.layers.add("Retini")
    doc.layers.add("Verde")
    hatch(msp, rect(-200, -500, 600, 0), GREEN)
    if with_pool:
        hatch(msp, rect(600, -500, 1200, 0), GREY, holes=[rect(780, -420, 1120, -80)])
        hatch(msp, rect(800, -400, 1100, -100), BLUE)
    else:
        hatch(msp, rect(600, -500, 1200, 0), GREY)
    if with_objects:
        block(doc, "Siepe", rect(0, -40, 100, 0))
        block(doc, "Albero", rect(-100, -100, 100, 100))
        block(doc, "Cespuglio 1", rect(-40, -40, 40, 40))
        block(doc, "Sdraio", rect(-80, -25, 70, 25))
        msp.add_blockref("Siepe", (100, -450), dxfattribs={"layer": "Verde", "rotation": 90})
        msp.add_blockref("Albero", (300, -250), dxfattribs={"layer": "Verde"})
        msp.add_blockref("Cespuglio 1", (-100, -100), dxfattribs={"layer": "Verde"})
        msp.add_blockref("Sdraio", (700, -50), dxfattribs={"layer": "Arredo Esterno", "rotation": 10})
        doc.layers.add("Arredo Esterno")
    return doc, msp


def run(doc, tmp_path, name="o", **kw):
    path = save(doc, tmp_path, name)
    kw.setdefault("garden", True)
    kw.setdefault("floors_to_outer_face", False)
    return convert(path, tmp_path / f"{name}.obj", Config(**kw))


def height_of(obj, group):
    lo, hi = obj.bbox(group)
    return hi[1] - lo[1]


def top_of(obj, group):
    return obj.bbox(group)[1][1]


def assert_closed(obj, group):
    """Every directed edge has its reverse (watertight), no edge is used twice, the volume is positive."""
    edges = Counter()
    for ids, _ in obj.groups[group]:
        for k in range(len(ids)):
            edges[(ids[k], ids[(k + 1) % len(ids)])] += 1
    assert all(edges.get((b, a), 0) == c for (a, b), c in edges.items()), f"{group} has open edges"
    assert max(edges.values()) == 1, f"{group} has an edge used twice"
    assert obj.volume(group) > 0, f"{group} faces inwards"


# --- layer names and kinds ----------------------------------------------------------------------

def test_garden_layer_names():
    rules = LayerRules()
    kinds = {"Prato": "lawn", "SIEPI": "plants", "Piscina": "water", "Pavimentazione": "paving",
             "Pavimenti esterni": "paving", "15 Verde": "garden", "3 Esterno": "garden", "Arredo Esterno": "furniture",
             "Giardino": "garden", "Terrazza": "paving", "Quote esterne": None, "Prospetto Giardino": None,
             "0": None, "Tetto": None}
    for name, kind in kinds.items():
        assert rules.garden_kind(name) == kind, name
    # the layers of the plan stay what they were; the paving of the garden is no floor of a room
    assert rules.classify_layer("Pavimentazione") is None and rules.classify_layer("PAVIMENTI") == "floor"
    assert rules.classify_layer("Muri esterni") == "wall" and rules.classify_layer("Infissi esterni") == "window"


def test_block_and_colour_kinds():
    assert block_kind("Siepe", None) == "hedge" and block_kind("Albero 2", None) == "tree"
    assert block_kind("Cespuglio 1", None) == "shrub" and block_kind("Sdraio", None) == "furniture"
    assert block_kind("Albero Prospetto", "plants") is None  # drawn for an elevation, not a plant of the plan
    assert block_kind("Albero Prospetto", "plants", in_elevation=True) == "tree"
    assert block_kind("Bidet", None) is None and block_kind("Bidet", "plants") == "shrub"
    assert kind_from_colour(GREEN) == "lawn" and kind_from_colour(BLUE) == "water"
    assert kind_from_colour(GREY) == "paving" and kind_from_colour(BROWN) == "paving"
    assert kind_from_colour((255, 255, 255)) is None  # white is a mask


def test_elenca_layer_names_the_garden_layers(tmp_path):
    doc, msp = garden_plan(tmp_path)
    path = save(doc, tmp_path)
    from dwg2c4d.dwgfile import open_drawing

    rows = {r["layer"]: r for r in layer_summary(open_drawing(path), Config())}
    assert rows["Verde"]["garden"] == "garden" and rows["Retini"]["garden"] is None
    assert rows["Arredo Esterno"]["garden"] == "furniture"
    assert rows["MURI"]["garden"] is None  # a layer of the plan is not garden


# --- ground ---------------------------------------------------------------------------------------

def test_the_library_leaves_the_garden_off(tmp_path):
    doc, _ = garden_plan(tmp_path)
    rep = run(doc, tmp_path, garden=False)
    assert rep.garden is None
    assert not any(g in Obj(rep.output).groups for g in ("Prato", "Pavimentazione", "Vasca", "Acqua"))


def test_ground_by_colour_one_object_per_kind_without_overlaps(tmp_path):
    doc, _ = garden_plan(tmp_path)
    obj = Obj(run(doc, tmp_path).output)
    assert {"Prato", "Pavimentazione", "Vasca", "Acqua"} <= set(obj.groups)
    thickness = 0.2
    assert obj.volume("Prato") / thickness == pytest.approx(8.0 * 5.0, rel=0.01)
    # paving 6 x 5 m, minus the hole of the pool (3.4 x 3.4 m, the coping reaches the edge of that hole)
    assert obj.volume("Pavimentazione") / thickness == pytest.approx(6.0 * 5.0 - 3.4 * 3.4, rel=0.01)
    assert top_of(obj, "Pavimentazione") == pytest.approx(0.0, abs=1e-6)
    assert top_of(obj, "Prato") == pytest.approx(-0.05, abs=1e-6)  # the lawn is lower than the paving
    assert obj.bbox("Prato")[0][1] == pytest.approx(-0.05 - thickness, abs=1e-6)
    for g in ("Prato", "Pavimentazione", "Vasca", "Acqua"):
        assert_closed(obj, g)


def test_the_ground_stops_at_the_wall_and_is_not_built_inside(tmp_path):
    doc, msp = garden_plan(tmp_path, with_pool=False, with_objects=False)
    hatch(msp, rect(100, 100, 400, 400), BROWN)  # a hatch inside the rooms: a floor finish, not the garden
    hatch(msp, rect(-100, -100, 300, 100), GREEN)  # overlapping the south wall: only the part outside counts
    rep = run(doc, tmp_path)
    obj = Obj(rep.output)
    assert obj.volume("Pavimentazione") / 0.2 == pytest.approx(30.0, rel=0.01)  # the paving as before: nothing inside
    footprint = rep.plan.solid_walls
    garden = rep.garden
    for kind, geom in garden.surfaces.items():
        assert geom.intersection(footprint).area < 1e-6, kind  # never over the walls
    inside_room = rep.plan.solid_walls.envelope.buffer(-1.0)
    assert all(g.intersection(inside_room).area < 1e-6 for g in garden.surfaces.values())


def test_later_hatches_cover_earlier_ones(tmp_path):
    doc, msp = garden_plan(tmp_path, with_pool=False, with_objects=False)
    hatch(msp, rect(200, -300, 400, -100), GREY)  # a paving drawn over the lawn
    rep = run(doc, tmp_path)
    obj = Obj(rep.output)
    assert obj.volume("Pavimentazione") / 0.2 == pytest.approx(30.0 + 2.0 * 2.0, rel=0.01)
    assert obj.volume("Prato") / 0.2 == pytest.approx(40.0 - 4.0, rel=0.01)  # the lawn has a hole there
    union = rep.garden.surfaces
    assert union["paving"].intersection(union["lawn"]).area < 1e-6  # nothing is twice


def test_the_layer_name_decides_before_the_colour(tmp_path):
    doc, msp = building()
    doc.layers.add("Pavimentazione")
    doc.layers.add("Prato")
    hatch(msp, rect(-200, -500, 600, 0), GREEN, layer="Pavimentazione")  # green, but the layer says paving
    msp.add_lwpolyline(rect(600, -500, 1200, 0), close=True, dxfattribs={"layer": "Prato"})  # no hatch at all
    rep = run(doc, tmp_path)
    obj = Obj(rep.output)
    assert obj.volume("Pavimentazione") / 0.2 == pytest.approx(40.0, rel=0.01)
    assert obj.volume("Prato") / 0.2 == pytest.approx(30.0, rel=0.01)
    # the paving of the garden is no floor of a room
    assert not [g for g in obj.groups if g.startswith("Pavimento_")]  # no floor of a room made of the garden's paving


def test_pattern_names_decide_before_the_colour(tmp_path):
    doc, msp = building()
    doc.layers.add("Retini")
    hatch(msp, rect(-200, -500, 600, 0), GREY, pattern="GRASS")
    rep = run(doc, tmp_path)
    assert rep.garden.area("lawn") == pytest.approx(40.0, rel=0.01) and rep.garden.area("paving") == 0.0


def test_thin_strips_are_kerbs_and_gaps_are_soil(tmp_path):
    doc, msp = garden_plan(tmp_path, with_pool=False, with_objects=False)
    hatch(msp, rect(-200, -530, 1200, -500), GREY)  # a 30 cm strip along the bottom: a kerb or a low wall
    hatch(msp, rect(1220, -500, 1500, 0), GREEN)  # 20 cm of nothing between this and the paving: soil
    rep = run(doc, tmp_path)
    garden = rep.garden
    assert garden.area("edge") == pytest.approx(14.0 * 0.3, rel=0.02)
    assert garden.area("soil") == pytest.approx(0.2 * 5.0, rel=0.1)
    obj = Obj(rep.output)
    assert {"Bordi", "Terreno"} <= set(obj.groups)
    assert top_of(obj, "Terreno") == pytest.approx(-0.05, abs=1e-6)


def test_a_bare_hole_gets_what_surrounds_it(tmp_path):
    doc, msp = building()
    doc.layers.add("Retini")
    hatch(msp, rect(-200, -500, 600, 0), GREEN, holes=[rect(100, -300, 160, -240)])  # where a bush stands
    rep = run(doc, tmp_path)
    assert rep.garden.area("lawn") == pytest.approx(40.0, rel=0.01)  # no hole in the lawn


def test_what_is_far_from_the_house_or_drawn_in_an_elevation_is_not_the_garden(tmp_path):
    doc, msp = garden_plan(tmp_path, with_pool=False, with_objects=False)
    hatch(msp, rect(0, 3000, 500, 3500), GREY)  # 24 m away: a roof plan, another drawing
    zone = south_elevation(msp)
    hatch(msp, rect(100, -2000, 400, -1800), GREEN)  # inside the elevation
    rep = run(doc, tmp_path, elevations=[zone])
    assert rep.garden.area("paving") == pytest.approx(30.0, rel=0.01) and rep.garden.area("lawn") == pytest.approx(40.0, rel=0.01)
    hatch(msp, rect(0, 600, 500, 900), GREY)  # touching the north wall: it is garden...
    assert run(doc, tmp_path, "b").garden.area("paving") == pytest.approx(30.0 + 5.0 * 3.0, rel=0.01)
    assert run(doc, tmp_path, "c", garden_exclude=[(0, 600, 500, 900)]).garden.area("paving") \
        == pytest.approx(30.0, rel=0.01)
    # a hatch only partly inside the excluded box (drawing units: xmin, ymin, xmax, ymax) is not left out
    hatch(msp, rect(0, 900, 800, 1000), GREY)
    assert run(doc, tmp_path, "d", garden_exclude=[(0, 600, 500, 1000)]).garden.area("paving") \
        == pytest.approx(30.0 + 8.0 * 1.0, rel=0.01)


def test_garden_area_option_limits_the_garden(tmp_path):
    doc, _ = garden_plan(tmp_path, with_pool=False, with_objects=False)
    rep = run(doc, tmp_path, garden_area=(-200, -500, 550, 0))  # what touches the area is taken whole
    assert rep.garden.area("lawn") == pytest.approx(40.0, rel=0.01)
    assert rep.garden.area("paving") == 0.0  # the paving starts at x = 600


def test_a_drawing_without_a_garden_has_none(tmp_path):
    doc, msp = building()
    rep = run(doc, tmp_path)
    assert rep.garden is None and not any(g in Obj(rep.output).groups for g in ("Prato", "Pavimentazione"))


# --- pool ---------------------------------------------------------------------------------------

def test_pool_is_a_closed_shell_with_the_water_in_it(tmp_path):
    doc, _ = garden_plan(tmp_path, with_objects=False)
    cfg = dict(pool_depth=1.4, pool_wall=0.2, pool_water_drop=0.25)
    obj = Obj(run(doc, tmp_path, **cfg).output)
    water, shell = 3.0 * 3.0, 3.4 * 3.4
    assert obj.volume("Acqua") == pytest.approx(water * (1.4 - 0.25), rel=0.01)
    assert top_of(obj, "Acqua") == pytest.approx(-0.25, abs=1e-6)
    # walls from the coping (down to the floor) all around the water; the floor under all of it
    assert obj.volume("Vasca") == pytest.approx((shell - water) * 1.4 + shell * 0.2, rel=0.01)
    assert top_of(obj, "Vasca") == pytest.approx(0.0, abs=1e-6)
    assert obj.bbox("Vasca")[0][1] == pytest.approx(-1.6, abs=1e-6)  # depth + the floor
    assert_closed(obj, "Vasca") and assert_closed(obj, "Acqua")


def test_pool_without_a_bare_coping_gets_a_wall_of_its_own(tmp_path):
    doc, msp = building()
    doc.layers.add("Retini")
    hatch(msp, rect(600, -500, 1200, 0), GREY, holes=[rect(800, -400, 1100, -100)])  # the hole is the water itself
    hatch(msp, rect(800, -400, 1100, -100), BLUE)
    rep = run(doc, tmp_path)
    obj = Obj(rep.output)
    assert obj.volume("Vasca") == pytest.approx((3.3 * 3.3 - 9.0) * 1.5 + 3.3 * 3.3 * 0.15, rel=0.02)
    assert rep.garden.area("paving") == pytest.approx(30.0 - 3.3 * 3.3, rel=0.02)  # the paving does not overlap the shell


def test_water_is_a_pool_only_when_big_enough(tmp_path):
    doc, msp = building()
    doc.layers.add("Retini")
    hatch(msp, rect(-200, -500, 600, 0), GREEN)
    hatch(msp, rect(100, -300, 160, -240), BLUE)  # 36 x 36 cm of blue on the lawn: not a pool
    assert not run(doc, tmp_path).garden.pools


# --- plants and furniture ------------------------------------------------------------------------

def test_objects_become_standins_with_ids_sizes_and_heights(tmp_path):
    doc, _ = garden_plan(tmp_path)
    rep = run(doc, tmp_path)
    obj = Obj(rep.output)
    kinds = {o.block: o for o in rep.garden.objects}
    assert {o.id for o in rep.garden.objects} == {"S01", "A01", "C01", "E01"}
    hedge, tree, shrub, bed = kinds["Siepe"], kinds["Albero"], kinds["Cespuglio 1"], kinds["Sdraio"]
    # the block outline, turned by the insert: a hedge 1 x 0.4 m laid north-south
    assert (hedge.length, hedge.width) == pytest.approx((1.0, 0.4)) and hedge.angle == pytest.approx(math.pi / 2)
    assert (hedge.cx, hedge.cy) == pytest.approx((1.2, -4.0))
    assert (tree.length, tree.width) == pytest.approx((2.0, 2.0)) and (tree.cx, tree.cy) == pytest.approx((3.0, -2.5))
    assert (bed.length, bed.width) == pytest.approx((1.5, 0.5)) and bed.angle == pytest.approx(math.radians(10))
    heights = {o.id: o.height for o in rep.garden.objects}
    assert heights == pytest.approx({"S01": 1.2, "A01": 4.5, "C01": 0.9, "E01": 0.35})
    assert all(o.height_src == "predefinita" for o in rep.garden.objects)
    for name in ("Siepe_S01", "Tronco_A01", "Chioma_A01", "Cespuglio_C01", "Arredo_E01"):
        assert name in obj.groups
        assert_closed(obj, name)
    assert height_of(obj, "Siepe_S01") == pytest.approx(1.2)
    assert height_of(obj, "Arredo_E01") == pytest.approx(0.35, abs=1e-6)
    assert top_of(obj, "Chioma_A01") == pytest.approx(-0.05 + 4.5, abs=1e-6)  # on the lawn: 5 cm lower than the paving
    assert top_of(obj, "Arredo_E01") == pytest.approx(0.35, abs=1e-6)  # on the paving
    assert len(rep.garden.objects) == 4


def test_a_block_inside_the_building_or_for_an_elevation_is_no_garden_object(tmp_path):
    doc, msp = garden_plan(tmp_path, with_objects=False)
    block(doc, "Siepe", rect(0, -40, 100, 0))
    block(doc, "Albero Prospetto", rect(-50, 0, 50, 300))
    msp.add_blockref("Siepe", (500, 300), dxfattribs={"layer": "Verde"})  # in a room
    msp.add_blockref("Albero Prospetto", (200, -300), dxfattribs={"layer": "Verde"})  # named for an elevation
    block(doc, "Bidet", rect(0, 0, 40, 40))
    msp.add_blockref("Bidet", (300, -300), dxfattribs={"layer": "0"})  # unknown name on a layer that says nothing
    assert not run(doc, tmp_path).garden.objects


def test_heights_from_the_elevation(tmp_path):
    doc, msp = garden_plan(tmp_path)
    zone = south_elevation(msp)  # ground at y = -2000
    block(doc, "Albero Prospetto", rect(-60, 0, 60, 320))
    msp.add_blockref("Albero Prospetto", (150, -2000), dxfattribs={"layer": "Verde"})  # a tree 3.2 m tall
    msp.add_blockref("Siepe", (400, -1960), dxfattribs={"layer": "Verde"})  # two rows of hedge, 40 cm each
    msp.add_blockref("Siepe", (400, -1920), dxfattribs={"layer": "Verde"})
    rep = run(doc, tmp_path, elevations=[zone])
    by_id = {o.id: o for o in rep.garden.objects}
    assert by_id["A01"].height == pytest.approx(3.2) and by_id["A01"].height_src == "prospetto"
    assert by_id["S01"].height == pytest.approx(0.8) and by_id["S01"].height_src == "prospetto"
    assert by_id["C01"].height_src == "predefinita"  # no shrub in the elevation
    assert by_id["E01"].height_src == "predefinita"
    assert "(prospetto)" in "\n".join(__import__("dwg2c4d.qa", fromlist=["x"]).summary_lines(rep))
    # the elevation's own plants are no plants of the plan
    assert len(rep.garden.objects) == 4


def test_hedge_heights_from_a_hatch_as_wide_as_the_hedge(tmp_path):
    doc, msp = garden_plan(tmp_path)
    zone = south_elevation(msp)
    hatch(msp, rect(300, -2000, 400, -1900), GREEN, layer="Verde")  # 1 m wide: like the hedge of the plan
    hatch(msp, rect(300, -1900, 400, -1860), GREEN, layer="Verde")  # and 40 cm above it: 1.4 m together
    hatch(msp, rect(500, -2000, 900, -1700), GREEN, layer="Verde")  # 4 m wide: a lawn, not the hedge
    rep = run(doc, tmp_path, elevations=[zone])
    by_id = {o.id: o for o in rep.garden.objects}
    assert by_id["S01"].height == pytest.approx(1.4) and by_id["S01"].height_src == "prospetto"


# --- the table -----------------------------------------------------------------------------------

def test_garden_table_is_written_and_applied(tmp_path):
    doc, _ = garden_plan(tmp_path)
    rep = run(doc, tmp_path)
    assert rep.garden_table_path and rep.garden_table_path.name == "o_giardino.csv"
    rows = read_garden_table(rep.garden_table_path)
    by_block = {r["blocco"]: r for r in rows}
    assert {r["id"] for r in rows} == {"S01", "A01", "C01", "E01"}
    assert by_block["Albero"]["tipo"] == "albero" and by_block["Albero"]["altezza"] == "450"
    assert by_block["Siepe"]["lunghezza"] == "100" and by_block["Siepe"]["larghezza"] == "40"
    assert by_block["Siepe"]["x"] == "120" and by_block["Siepe"]["y"] == "-400"  # in the units of the drawing
    # edit it: a taller hedge, the shrub becomes a tree, the sunbed is dropped, a bad row is reported
    text = rep.garden_table_path.read_text(encoding="utf-8-sig").splitlines()
    head = text[0].split(";")
    edited = []
    for line in text[1:]:
        cells = line.split(";")
        row = dict(zip(head, cells))
        if row["id"] == "S01":
            cells[head.index("MODIFICA_altezza")] = "150"
        if row["id"] == "C01":
            cells[head.index("MODIFICA_tipo")] = "albero"
        if row["id"] == "E01":
            cells[head.index("MODIFICA_tieni")] = "no"
        edited.append(";".join(cells))
    unknown = [""] * len(head)
    unknown[0], unknown[head.index("MODIFICA_altezza")] = "Z99", "50"  # an id that does not exist: reported, ignored
    edited.append(";".join(unknown))
    table = tmp_path / "edited.csv"
    table.write_text("\n".join([text[0], *edited]) + "\n", encoding="utf-8-sig")
    rep2 = run(doc, tmp_path, "o2", garden_table_in=str(table))
    by_id = {o.id: o for o in rep2.garden.objects}
    assert by_id["S01"].height == pytest.approx(1.5) and by_id["S01"].height_src == "tabella"
    assert by_id["C01"].kind == "tree" and by_id["C01"].height == pytest.approx(4.5)  # a tree's default height
    assert not by_id["E01"].keep
    obj = Obj(rep2.output)
    assert "Arredo_E01" not in obj.groups and "Siepe_S01" in obj.groups
    assert height_of(obj, "Siepe_S01") == pytest.approx(1.5)
    assert "Chioma_C01" in obj.groups  # the shrub is a tree now
    assert any("Z99" in w for w in rep2.warnings)
    # the table that was applied is never overwritten
    rep3 = run(doc, tmp_path, "o2", garden_table_in=str(rep2.garden_table_path))
    assert rep3.garden_table_path.name == "o2_giardino_nuova.csv"


def test_garden_table_errors_are_reported_not_fatal(tmp_path):
    doc, _ = garden_plan(tmp_path)
    rep = run(doc, tmp_path)
    head = "id;tipo;blocco;layer;x;y;rotazione;lunghezza;larghezza;altezza;origine_altezza;note;MODIFICA_tipo;" \
           "MODIFICA_lunghezza;MODIFICA_larghezza;MODIFICA_altezza;MODIFICA_tieni"
    n = head.count(";")
    rows = [
        ";".join(["Z99"] + [""] * (n - 4) + ["", "", "50", ""]),  # an id that does not exist
        ";".join(["A01"] + [""] * (n - 4) + ["", "", "abc", ""]),  # a height that is not a number
        ";".join(["S01"] + [""] * (n - 5) + ["bosco", "", "", "", ""]),  # a type that does not exist
    ]
    table = tmp_path / "bad.csv"
    table.write_text("\n".join([head, *rows]) + "\n", encoding="utf-8-sig")
    rep2 = run(doc, tmp_path, "o2", garden_table_in=str(table))
    text = "\n".join(rep2.warnings)
    assert "Z99" in text and "A01" in text and "S01" in text
    assert {o.id: o.height for o in rep2.garden.objects}["A01"] == pytest.approx(4.5)  # unchanged


# --- JSON for Cinema 4D -----------------------------------------------------------------------------

def test_json_gives_every_object_its_own_axis_at_the_centre_of_its_base(tmp_path):
    doc, _ = garden_plan(tmp_path)
    rep = run(doc, tmp_path, c4d_json=True, origin="center")
    model = json.loads(rep.json_path.read_text(encoding="utf-8"))
    groups = {g["name"]: g for g in model["groups"]}
    assert {"Giardino", "Piscina", "Alberi", "Siepi", "Cespugli", "Arredi_esterni"} <= set(groups)
    offset = model["origin_offset_m"]  # the model is moved to its centre: the axes are where the objects are
    assert offset == pytest.approx([-W / 200.0, -D / 200.0])
    tree = next(o for o in rep.garden.objects if o.kind == "tree")
    g = groups["A01"]
    assert g["parent"] == "Alberi"
    assert g["origin"] == pytest.approx([(tree.cx + offset[0]) * 100, (tree.cy + offset[1]) * 100, -5.0])
    assert g["ex"] == pytest.approx([1.0, 0.0]) and g["ey"] == pytest.approx([0.0, 1.0])
    hedge = groups["S01"]
    assert hedge["parent"] == "Siepi" and hedge["ex"] == pytest.approx([0.0, 1.0])  # laid north-south
    objects = {o["name"]: o for o in model["objects"]}
    crown = objects["Chioma_A01"]
    assert crown["group"] == "A01" and crown["material"] == "foliage"
    xs, ys, zs = (crown["points"][i::3] for i in range(3))
    assert (min(xs), max(xs)) == pytest.approx((-100, 100), abs=0.01)  # the points are relative to the axis
    assert (min(ys), max(ys)) == pytest.approx((-100, 100), abs=0.01)
    assert min(zs) > 0 and max(zs) == pytest.approx(450.0)
    hedge_points = objects["Siepe_S01"]["points"]
    assert min(hedge_points[2::3]) == pytest.approx(0.0)  # the lowest point is the origin's height
    assert objects["Prato"]["group"] == "Giardino" and objects["Prato"]["material"] == "lawn"
    assert objects["Acqua"]["material"] == "water" and objects["Vasca"]["material"] == "pool_shell"
    assert {"paving", "lawn", "water", "pool_shell", "foliage", "trunk", "hedge", "shrub",
            "outdoor_furniture"} <= set(model["materials"])


def test_obj_has_a_material_for_each_garden_group(tmp_path):
    doc, _ = garden_plan(tmp_path)
    rep = run(doc, tmp_path)
    mtl = rep.output.with_suffix(".mtl").read_text(encoding="utf-8")
    for name in ("Prato", "Pavimentazione", "Acqua", "Vasca", "Tronco", "Chioma", "Siepe", "Cespuglio", "Arredo"):
        assert f"newmtl {name}\n" in mtl
    water = mtl.split("newmtl Acqua\n")[1].split("newmtl")[0]
    assert "d 0.55" in water  # the water is see-through in the viewer
    text = rep.output.read_text(encoding="utf-8")
    assert "usemtl Chioma" in text and "o Chioma_A01" in text


def test_the_garden_does_not_change_the_house(tmp_path):
    doc, _ = garden_plan(tmp_path)
    with_garden = Obj(run(doc, tmp_path, "a").output)
    without = Obj(run(doc, tmp_path, "b", garden=False).output)
    for group in without.groups:
        assert group in with_garden.groups
        assert with_garden.volume(group) == pytest.approx(without.volume(group), rel=1e-6)


# --- command line ----------------------------------------------------------------------------------

def test_cli_builds_the_garden_by_default_and_can_leave_it_out(tmp_path):
    doc, _ = garden_plan(tmp_path)
    path = save(doc, tmp_path)
    out = tmp_path / "c.obj"
    assert main([str(path), "-o", str(out), "--no-immagini", "--json-c4d"]) == 0
    obj = Obj(out)
    assert {"Prato", "Pavimentazione", "Acqua", "Vasca", "Siepe_S01"} <= set(obj.groups)
    assert (tmp_path / "c_giardino.csv").exists()
    out2 = tmp_path / "d.obj"
    assert main([str(path), "-o", str(out2), "--no-immagini", "--no-giardino"]) == 0
    assert not any(g in Obj(out2).groups for g in ("Prato", "Acqua", "Siepe_S01"))
    assert not (tmp_path / "d_giardino.csv").exists()
    out3 = tmp_path / "e.obj"
    assert main([str(path), "-o", str(out3), "--no-immagini", "--no-tabella"]) == 0
    assert not (tmp_path / "e_giardino.csv").exists() and not (tmp_path / "e_aperture.csv").exists()


def test_cli_options_for_the_garden(tmp_path):
    doc, _ = garden_plan(tmp_path)
    path = save(doc, tmp_path)
    out = tmp_path / "f.obj"
    assert main([str(path), "-o", str(out), "--no-immagini", "--profondita-piscina", "2", "--altezza-alberi", "6",
                 "--altezza-siepi", "1.8", "--altezza-cespugli", "1.1"]) == 0
    obj = Obj(out)
    assert obj.bbox("Vasca")[0][1] == pytest.approx(-2.15, abs=1e-6)
    assert height_of(obj, "Siepe_S01") == pytest.approx(1.8)
    assert top_of(obj, "Chioma_A01") == pytest.approx(-0.05 + 6.0, abs=1e-6)
    assert height_of(obj, "Cespuglio_C01") == pytest.approx(1.1, abs=1e-6)
    out2 = tmp_path / "g2.obj"
    assert main([str(path), "-o", str(out2), "--no-immagini", "--area-giardino=-200,-500,550,0"]) == 0
    assert "Acqua" not in Obj(out2).groups  # the pool is out of that area


def test_cli_images_show_the_garden(tmp_path):
    pytest.importorskip("matplotlib")
    doc, _ = garden_plan(tmp_path)
    path = save(doc, tmp_path)
    out = tmp_path / "h.obj"
    assert main([str(path), "-o", str(out)]) == 0
    assert (tmp_path / "h_controllo_pianta.png").stat().st_size > 5000
    report = (tmp_path / "h_report.txt").read_text(encoding="utf-8")
    assert "Giardino" in report and "1 piscina" in report and "h_giardino.csv" in report


def test_config_validation_of_the_garden_values():
    for bad in ({"pool_depth": 0}, {"pool_wall": -1}, {"tree_height": 0}, {"garden_lawn_drop": -0.1},
                {"pool_water_drop": 2.0, "pool_depth": 1.5}, {"garden_area": (0, 0, -1, 5)}):
        with pytest.raises(ValueError):
            Config(**bad).validate()
    cfg = Config.from_dict({"garden": True, "garden_area": [0, 0, 100, 100], "pool_depth": 2})
    assert cfg.garden and cfg.garden_area == (0.0, 0.0, 100.0, 100.0)


# --- regressions found by the review ---------------------------------------------------------------

def test_a_block_with_a_base_point_is_placed_by_its_geometry(tmp_path):
    doc, msp = garden_plan(tmp_path, with_objects=False)
    tree = doc.blocks.new("Albero", base_point=(50, 50))
    tree.add_lwpolyline(rect(-100, -100, 100, 100), close=True)
    msp.add_blockref("Albero", (300, -250), dxfattribs={"layer": "Verde"})
    (obj_,) = run(doc, tmp_path).garden.objects
    assert (obj_.cx, obj_.cy) == pytest.approx((2.5, -3.0))  # the base point 50, 50 sits on the insertion point


def test_gradient_hatches_have_the_colour_of_their_gradient(tmp_path):
    doc, msp = building()
    doc.layers.add("Retini")
    green = msp.add_hatch(dxfattribs={"layer": "Retini"})
    green.paths.add_polyline_path(rect(-200, -500, 600, 0), is_closed=True)
    green.set_gradient((0, 160, 0), (0, 80, 0))
    blue = msp.add_hatch(dxfattribs={"layer": "Retini"})
    blue.paths.add_polyline_path(rect(100, -400, 400, -100), is_closed=True)
    blue.set_gradient((60, 120, 220), (30, 60, 160))
    rep = run(doc, tmp_path)
    assert len(rep.garden.pools) == 1  # the blue gradient is the water ...
    assert rep.garden.area("lawn") == pytest.approx(40.0 - 3.3 * 3.3, rel=0.03)  # ... the green one the lawn around its shell


def test_pattern_names_are_read_by_whole_words():
    from shapely.geometry import Polygon

    from dwg2c4d.garden import _fill_kind, _Fill

    def kind(pattern, rgb=GREY):
        return _fill_kind(_Fill(0, "L", None, pattern, rgb, Polygon()))

    assert kind("TERRA") == "soil" and kind("AR-WATER") == "water" and kind("GRASS1") == "lawn"
    assert kind("TERRACOTTA") == "paving" and kind("TERRAZZO") == "paving" and kind("WATERPROOF") == "paving"


def test_the_draw_order_table_of_the_drawing_decides_what_is_on_top(tmp_path):
    doc, msp = building()
    doc.layers.add("Retini")
    lawn = hatch(msp, rect(-200, -500, 600, 0), GREEN)
    paving = hatch(msp, rect(200, -300, 400, -100), GREY)  # drawn later, so on top ...
    msp.set_redraw_order([(lawn.dxf.handle, "FFFF"), (paving.dxf.handle, "1")])  # ... unless DRAWORDER says otherwise
    rep = run(doc, tmp_path)
    assert rep.garden.area("paving") == 0.0 and rep.garden.area("lawn") == pytest.approx(40.0, rel=0.01)


def test_arrays_and_wrapper_blocks_hold_garden_objects(tmp_path):
    doc, msp = garden_plan(tmp_path, with_objects=False)
    block(doc, "Siepe", rect(0, -40, 100, 0))
    row = msp.add_blockref("Siepe", (-100, -450), dxfattribs={"layer": "Verde", "row_count": 1, "column_count": 3,
                                                               "row_spacing": 0, "column_spacing": 150})
    assert row.mcount == 3
    wrapper = doc.blocks.new("Siepi nord")  # a block that only holds another reference
    wrapper.add_blockref("Siepe", (0, 0))
    msp.add_blockref("Siepi nord", (500, -450), dxfattribs={"layer": "Verde"})
    xs = sorted(round(o.cx, 2) for o in run(doc, tmp_path).garden.objects if o.kind == "hedge")
    assert xs == pytest.approx([-0.5, 1.0, 2.5, 5.5])  # three of the array, one of the wrapper (50 cm = half a hedge)


def test_outlines_on_a_water_layer_are_one_pool_not_a_moat(tmp_path):
    doc, msp = building()
    doc.layers.add("Piscina")
    hatch(msp, rect(-200, -600, 1200, 0), GREY)
    msp.add_lwpolyline(rect(300, -500, 900, -100), close=True, dxfattribs={"layer": "Piscina"})  # the coping line
    msp.add_lwpolyline(rect(340, -460, 860, -140), close=True, dxfattribs={"layer": "Piscina"})  # the water line
    rep = run(doc, tmp_path)
    assert len(rep.garden.pools) == 1 and rep.garden.pools[0].area == pytest.approx(6.0 * 4.0, rel=0.01)


def test_what_is_in_an_elevation_core_is_not_garden_but_the_roof_margin_is(tmp_path):
    doc, msp = garden_plan(tmp_path, with_pool=False, with_objects=False)
    # a south elevation drawn close below the plan: its zone stretches 4 m towards the plan for the roof lines, and
    # that stretch reaches into the garden (y -500..0); the garden there is still garden
    south_elevation(msp, ground_y=-1500)
    msp.add_lwpolyline(rect(-100, -1500, 1100, -700), close=True, dxfattribs={"layer": "Prospetto Sud"})
    hatch(msp, rect(100, -1450, 300, -1350), GREEN)  # inside the elevation itself: not garden
    rep = run(doc, tmp_path)
    assert rep.elevations and rep.garden.area("lawn") == pytest.approx(40.0, rel=0.01)


def test_floor_layers_of_the_rooms_stay_floors_and_the_coping_is_paving():
    rules = LayerRules()
    for name in ("Pavimentazione interna", "PAVIMENTAZIONE_INT", "Pavimentazione piano interrato"):
        assert rules.classify_layer(name) == "floor" and rules.garden_kind(name) is None, name
    assert rules.garden_kind("Pavimento piscina") == "paving" and rules.classify_layer("Pavimento piscina") is None


def test_config_can_switch_the_garden_off_and_a_walls_layer_named_esterno_is_still_walls(tmp_path):
    doc, _ = garden_plan(tmp_path)
    path = save(doc, tmp_path)
    cfg_file = tmp_path / "c.json"
    cfg_file.write_text(json.dumps({"garden": False}), encoding="utf-8")
    out = tmp_path / "x.obj"
    assert main([str(path), "-o", str(out), "--no-immagini", "--config", str(cfg_file)]) == 0
    assert "Prato" not in Obj(out).groups
    out2 = tmp_path / "y.obj"
    assert main([str(path), "-o", str(out2), "--no-immagini"]) == 0 and "Prato" in Obj(out2).groups
    # the walls of a drawing on a layer called "Esterno" (only lines): the layer is still proposed as the walls
    from builders import T, _rect

    import ezdxf

    doc2 = ezdxf.new("R2018", setup=True)
    doc2.units = 5
    doc2.layers.add("Esterno")
    msp2 = doc2.modelspace()
    _rect(msp2, 0, 0, W, D, "Esterno")
    _rect(msp2, T, T, W - T, D - T, "Esterno")
    path2 = tmp_path / "e.dxf"
    doc2.saveas(path2)
    out3 = tmp_path / "z.obj"
    assert main([str(path2), "-o", str(out3), "--no-immagini", "--accetta-proposte"]) == 0
    assert "Muri_interno" in Obj(out3).groups or "Muri" in Obj(out3).groups


def test_several_bare_holes_are_all_filled_without_leaving_rings(tmp_path):
    from shapely.geometry import Point, Polygon, box

    from dwg2c4d import garden as G

    lawn = box(0, 0, 8, 5).difference(Point(2, 2.5).buffer(0.45, 16)).difference(box(5, 1, 6.5, 1.5))
    ground, _, _ = G.build_ground([G._Fill(0, "L", "lawn", "", None, lawn)], Polygon(), Config(garden=True))
    pieces = G.polygons_of(ground["lawn"])
    assert len(pieces) == 1 and not list(pieces[0].interiors)  # one piece, no hole, no island inside a hole
    assert ground["lawn"].area == pytest.approx(40.0, rel=0.01)


def test_nothing_beyond_the_reach_is_garden_even_if_a_chain_of_ground_leads_there(tmp_path):
    """A sheet holds other drawings: a strip of paving that runs on for 190 m and a tree at its far end are not the garden of
    the converted plan when the analysis says where the plan's surroundings end (``garden_reach``)."""
    def far(**kw):
        doc, msp = garden_plan(tmp_path, with_pool=False, with_objects=False)
        hatch(msp, rect(1200, -500, 20000, 0), GREY)  # touching the paving: a chain that leads away from the house
        block(doc, "Albero", rect(-100, -100, 100, 100))
        msp.add_blockref("Albero", (19000, -250), dxfattribs={"layer": "Verde"})
        return run(doc, tmp_path, "far", **kw)

    everything = far()
    assert any(o.cx > 100 for o in everything.garden.objects)  # without a reach the far tree is a plant of the garden
    near = far(garden_reach=(-1500, -1500, 3000, 1500))
    assert near.garden is not None and not any(o.cx > 100 for o in near.garden.objects)


# --- a failure in the garden never stops the conversion ----------------------------------------

def test_a_collection_of_slivers_and_stray_lines_is_tidied_without_crashing():
    """The site plan of a real sheet left, after the pieces of ground were cut out of each other, a collection of
    slivers and stray lines (coordinates 46 km from the origin); the opening of it is empty and GEOS cannot intersect
    that with a collection ("Unable to determine overlay result geometry dimension")."""
    from shapely.geometry import GeometryCollection, LineString, Polygon, box

    from dwg2c4d import garden as G

    sliver = Polygon([(24863.96978324881, 46613.45296265214), (24864.029965771995, 46613.45634149456),
                      (24864.030032606046, 46613.45620923092), (24863.980781438517, 46613.43101605976)])
    stray = LineString([(24864.03, 46613.4563), (24862.0592, 46617.3565)])
    assert G._tidy(GeometryCollection([sliver, stray])).is_empty
    assert G._tidy(GeometryCollection([box(0, 0, 5, 5), sliver, stray])).area == pytest.approx(25.0, rel=0.01)


def test_a_garden_that_cannot_be_built_is_a_warning_not_the_end_of_the_conversion(tmp_path, monkeypatch):
    import dwg2c4d.pipeline as pipeline

    def broken(*args, **kwargs):
        raise ValueError("boom")

    doc, _ = garden_plan(tmp_path)
    monkeypatch.setattr(pipeline, "build_garden", broken)
    rep = run(doc, tmp_path)
    groups = Obj(tmp_path / "o.obj").groups
    assert rep.garden is None and rep.wall_area_m2 > 5.0 and "Prato" not in groups
    assert any(w.startswith("Giardino non costruito: ValueError: boom") for w in rep.warnings)
    assert any(g.startswith("Muri") for g in groups)


def test_a_garden_whose_mesh_fails_leaves_the_house_alone(tmp_path, monkeypatch):
    import dwg2c4d.model as model

    real = model.add_garden

    def half_built(mesh, garden, cfg):
        real(mesh, garden, cfg)
        raise RuntimeError("mesh went wrong")

    doc, _ = garden_plan(tmp_path)
    monkeypatch.setattr(model, "add_garden", half_built)
    rep = run(doc, tmp_path)
    groups = Obj(tmp_path / "o.obj").groups
    assert rep.garden is None and not {"Prato", "Pavimentazione", "Acqua", "Vasca"} & set(groups)
    assert not any(g.startswith(("Siepe_", "Tronco_", "Chioma_", "Arredo_")) for g in groups)
    assert any(w.startswith("Giardino non costruito: RuntimeError: mesh went wrong") for w in rep.warnings)
    assert any(g.startswith("Muri") for g in groups) and any(g.startswith("Pavimento") for g in groups)
