"""A real plan with a garden (the author's own: house, terrace, pool, lawn, hedge, trees; elevation and roof plan on the
same sheet): the numbers that were checked by hand.

The drawing is not published with the project: put a copy in tests/data/pianta_giardino.dxf to run these. Its layers
are the ones of the author's habit (0 = walls, 14 Retini, 15 Verde, 3 Esterno, 10 Arredo Esterno, 16 Tetto, 11 Prospetto...).
"""

from collections import Counter
from pathlib import Path

import pytest
from helpers import Obj

from dwg2c4d import Config, convert
from dwg2c4d.config import LayerRules

PLAN = Path(__file__).parent / "data" / "pianta_giardino.dxf"
pytestmark = pytest.mark.skipif(not PLAN.exists(), reason="manca tests/data/pianta_giardino.dxf (disegno privato)")


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    out = tmp_path_factory.mktemp("garden") / "p.obj"
    return convert(PLAN, out, Config(layers=LayerRules({"wall": ["0"]}), garden=True, c4d_json=True, images=False,
                                     roof_auto=True))


def test_the_garden_is_found_around_the_house_and_nothing_else(report):
    garden = report.garden
    x0, y0, x1, y1 = garden.window
    # the terrace, the lawn and the hedge: 26 x 12 m next to the house; the elevation (10 m below) and the roof plan
    # (5 m above) that the sheet also holds are left out
    assert (x1 - x0, y1 - y0) == pytest.approx((26.1, 11.7), abs=0.5)
    assert 480.0 <= y0 and y1 <= 493.0


def test_ground_pool_and_objects(report):
    garden = report.garden
    assert garden.area("paving") == pytest.approx(66, rel=0.10)
    assert garden.area("lawn") == pytest.approx(62, rel=0.10)
    assert garden.area("edge") == pytest.approx(6, abs=2.5)  # the low wall around it, thin strips
    assert len(garden.pools) == 1 and garden.pools[0].area == pytest.approx(21.0, rel=0.05)
    count = Counter(o.kind for o in garden.objects)
    assert dict(count) == {"hedge": 17, "tree": 1, "shrub": 8, "furniture": 4}


def test_heights_come_from_the_front_elevation(report):
    by_kind = {}
    for o in report.garden.objects:
        by_kind.setdefault(o.kind, set()).add((o.height_src, round(o.height, 2)))
    assert by_kind["hedge"] == {("prospetto", 1.29)}  # two rows of leaves, 66 cm each
    assert by_kind["tree"] == {("prospetto", 2.11)}
    assert by_kind["shrub"] == {("predefinita", 0.9)} and by_kind["furniture"] == {("predefinita", 0.35)}


def test_every_garden_object_is_a_closed_solid_with_its_own_axis(report):
    obj = Obj(report.output)
    garden_groups = [g for g in obj.groups if g.split("_")[0] in (
        "Pavimentazione", "Prato", "Terreno", "Bordi", "Vasca", "Acqua", "Tronco", "Chioma", "Siepe", "Cespuglio", "Arredo")]
    assert len(garden_groups) == 37  # 6 of ground and pool, 17 + 8 + 4 objects, a tree has two parts
    for group in garden_groups:
        edges = Counter()
        for ids, _ in obj.groups[group]:
            for k in range(len(ids)):
                edges[(ids[k], ids[(k + 1) % len(ids)])] += 1
        assert all(edges.get((b, a), 0) == c for (a, b), c in edges.items()), group
        assert max(edges.values()) == 1 and obj.volume(group) > 0, group
    import json

    model = json.loads(report.json_path.read_text(encoding="utf-8"))
    frames = {g["name"] for g in model["groups"] if g.get("origin")}
    assert {"A01", "S01", "S17", "C01", "C08", "E01", "E04"} <= frames


def test_the_house_is_what_it_was(report):
    assert report.size_m == pytest.approx((14.84, 8.50), abs=0.02)
    assert (report.doors, report.windows) == (3, 6)
    assert report.roof and report.roof["faces"] == 6
