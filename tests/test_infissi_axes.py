"""The group of every door/window has its own axis: centre, lowest point, aligned with the wall.

The Cinema 4D script is run against a stand-in for the ``c4d`` module that does the matrix arithmetic, and
every object's world position is compared with the same model written without the per-group axes."""

import importlib.util
import json
import math
import sys
from pathlib import Path
from unittest.mock import MagicMock

import ezdxf
import pytest
from builders import PLAN_AREA, T, W, WIN_S, building

from dwg2c4d import Config, convert
from dwg2c4d.export import to_model_dict

SCRIPT = Path(__file__).resolve().parents[1] / "c4d" / "plan2c4d_import.py"


class Vector:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x, self.y, self.z = x, y, z

    def tuple(self):
        return (self.x, self.y, self.z)


class Matrix:
    """Like C4D's: a point (px, py, pz) in the object goes to off + px*v1 + py*v2 + pz*v3 in its parent."""

    def __init__(self, off=None, v1=None, v2=None, v3=None):
        self.off = off or Vector()
        self.v1, self.v2, self.v3 = v1 or Vector(1, 0, 0), v2 or Vector(0, 1, 0), v3 or Vector(0, 0, 1)

    def apply(self, p):
        return tuple(self.off.tuple()[k] + p[0] * self.v1.tuple()[k] + p[1] * self.v2.tuple()[k]
                     + p[2] * self.v3.tuple()[k] for k in range(3))

    def det(self):
        a, b, c = self.v1.tuple(), self.v2.tuple(), self.v3.tuple()
        return (a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0])
                + a[2] * (b[0] * c[1] - b[1] * c[0]))


class BaseObject:
    def __init__(self, kind=None):
        self.name, self.ml, self.parent = None, Matrix(), None

    def SetName(self, n):
        self.name = n

    def SetMl(self, m):
        self.ml = m

    def __setitem__(self, key, value):
        pass

    def MakeTag(self, t):
        return MagicMock()


class PolygonObject(BaseObject):
    def __init__(self, npoints, npolys):
        super().__init__()
        self.points, self.polys, self.npoints = [], {}, npoints

    def SetAllPoints(self, pts):
        assert len(pts) == self.npoints
        self.points = pts

    def SetPolygon(self, i, poly):
        self.polys[i] = poly

    def Message(self, m):
        pass


class CPolygon:
    def __init__(self, *v):
        self.v = v


class Doc:
    def __init__(self):
        self.objects = []

    def InsertObject(self, o, parent=None, pred=None):
        o.parent = parent
        self.objects.append(o)

    def InsertMaterial(self, m):
        pass

    def GetLayerObjectRoot(self):
        return MagicMock()


def load_script(monkeypatch):
    fake = MagicMock()
    fake.Vector, fake.Matrix, fake.BaseObject, fake.PolygonObject, fake.CPolygon = (
        Vector, Matrix, BaseObject, PolygonObject, CPolygon)
    fake.BaseMaterial = lambda *a: BaseObject()
    monkeypatch.setitem(sys.modules, "c4d", fake)
    monkeypatch.setitem(sys.modules, "c4d.gui", MagicMock())
    spec = importlib.util.spec_from_file_location("dwg2c4d_import_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def world_points(module, model):
    """{object name: sorted rounded world points (cm, C4D axes)} after running the script's build()."""
    doc = Doc()
    module.build(doc, model, use_corona=False, use_layers=False)
    out = {}
    for o in doc.objects:
        if not isinstance(o, PolygonObject):
            continue
        chain, node = [], o
        while node is not None:
            chain.append(node.ml)
            node = node.parent
        pts = []
        for p in o.points:
            q = p.tuple()
            for m in chain:  # object, then its null, then up to the root
                q = m.apply(q)
            pts.append(tuple(round(c, 2) for c in q))
        out[o.name] = sorted(pts)
    return out


def make(tmp_path, doc, **kw):
    path = tmp_path / "a.dxf"
    doc.saveas(path)
    return convert(path, tmp_path / "a.obj", Config(area=PLAN_AREA, fixtures="detailed", fixtures_per_opening=True,
                                                    origin="center", **kw))


@pytest.fixture
def plan(tmp_path):
    doc, msp = building()
    # an interior door with a swing arc, on a partition, so every kind of facing is covered
    doc.layers.add("Quote e Testi")
    return make(tmp_path, doc)


def groups_by_name(model):
    return {g["name"]: g for g in model["groups"]}


def test_every_opening_group_has_an_axis(plan):
    model = to_model_dict(plan.mesh, "m", plan.origin_offset, plan.openings)
    groups = groups_by_name(model)
    for o in plan.openings:
        g = groups[o.id]
        assert set(("origin", "ex", "ey")) <= set(g) and g["parent"] == "Infissi"
        (ex0, ex1), (ey0, ey1) = g["ex"], g["ey"]
        assert math.hypot(ex0, ex1) == pytest.approx(1.0) and math.hypot(ey0, ey1) == pytest.approx(1.0)
        assert ex0 * ey0 + ex1 * ey1 == pytest.approx(0.0, abs=1e-9)  # perpendicular
        assert ex0 * ey1 - ex1 * ey0 == pytest.approx(1.0)  # a proper rotation: Y is X turned 90 degrees


def test_the_origin_is_the_centre_and_the_lowest_point_of_the_opening(plan):
    model = to_model_dict(plan.mesh, "m", plan.origin_offset, plan.openings)
    groups = groups_by_name(model)
    ox, oy = plan.origin_offset
    for o in plan.openings:
        x, y, z = groups[o.id]["origin"]
        assert (x, y) == pytest.approx(((o.center[0] + ox) * 100, (o.center[1] + oy) * 100), abs=1e-2)
        # the parts' own lowest point, in local coordinates, is 0 on the vertical axis
        lows = [min(p[2] for p in zip(*[iter(ob["points"])] * 3)) for ob in model["objects"] if ob["group"] == o.id]
        assert min(lows) == pytest.approx(0.0, abs=1e-6)
    door = next(o for o in plan.openings if o.kind == "door")
    assert groups[door.id]["origin"][2] == pytest.approx(0.0, abs=0.01)  # a door starts at the floor
    window = next(o for o in plan.openings if o.kind == "window")
    assert groups[window.id]["origin"][2] == pytest.approx((window.z0 - 0.03) * 100, abs=0.01)  # the sill ledge


def test_the_front_looks_to_the_outside(plan):
    model = to_model_dict(plan.mesh, "m", plan.origin_offset, plan.openings)
    groups = groups_by_name(model)
    cx, cy = W / 200, 3.0  # the building's centre (drawing = model before the centring shift)
    for o in plan.openings:
        ey = groups[o.id]["ey"]
        away = (o.center[0] - cx) * ey[0] + (o.center[1] - cy) * ey[1]
        assert away > 0, o.id  # +Y of the local frame points away from the middle of the building


def test_the_objects_stay_where_they_were(plan, monkeypatch):
    """The script puts every object back in the same place whether or not the groups have their own axis."""
    module = load_script(monkeypatch)
    with_axes = world_points(module, to_model_dict(plan.mesh, "m", plan.origin_offset, plan.openings))
    absolute = world_points(module, to_model_dict(plan.mesh, "m", plan.origin_offset))
    assert with_axes.keys() == absolute.keys() and len(with_axes) > 6
    for name in absolute:
        assert with_axes[name] == absolute[name], name


def test_the_matrix_the_script_builds_is_a_rotation_with_the_origin_at_the_group(plan, monkeypatch):
    module = load_script(monkeypatch)
    model = to_model_dict(plan.mesh, "m", plan.origin_offset, plan.openings)
    for g in model["groups"]:
        if "origin" not in g:
            continue
        m = module.frame_matrix(g)
        assert m.det() == pytest.approx(1.0) and m.v2.tuple() == (0.0, 1.0, 0.0)  # Y up, no mirroring
        ox, oy, oz = g["origin"]
        assert m.off.tuple() == (ox, oz, oy)  # CAD (x, y, z) -> C4D (x, z, y)


def test_the_json_written_to_disk_has_the_axes(tmp_path):
    doc, _ = building()
    rep = make(tmp_path, doc, c4d_json=True)
    model = json.loads(rep.json_path.read_text())
    assert all("origin" in g for g in model["groups"] if g["parent"] == "Infissi")
    assert "origin" not in groups_by_name(model)["Infissi"]


def test_without_the_per_opening_objects_nothing_changes(tmp_path):
    doc, _ = building()
    path = tmp_path / "u.dxf"
    doc.saveas(path)
    rep = convert(path, tmp_path / "u.obj", Config(area=PLAN_AREA, fixtures="detailed", c4d_json=True))
    model = json.loads(rep.json_path.read_text())
    assert not any("origin" in g for g in model["groups"])


def test_a_door_in_a_north_south_wall_is_aligned_with_it_and_faces_its_swing(tmp_path):
    """A door in a partition running along y, its leaf swinging to +x: the group's X axis runs along y and its
    front (+Y of the frame) looks to +x, the side the leaf opens to (an interior door has no 'outside')."""
    doc, msp = building()
    msp.add_lwpolyline([(500, 30), (510, 30), (510, 570), (500, 570)], close=True, dxfattribs={"layer": "MURI"})
    a = {"layer": "PORTE"}
    msp.add_line((500, 250), (510, 250), dxfattribs=a)
    msp.add_line((500, 340), (510, 340), dxfattribs=a)
    msp.add_arc((505, 250), 90, 0, 90, dxfattribs=a)
    msp.add_line((505, 250), (595, 250), dxfattribs=a)
    rep = make(tmp_path, doc)
    model = to_model_dict(rep.mesh, "m", rep.origin_offset, rep.openings)
    inner = [o for o in rep.openings if o.kind == "door" and 4.0 < o.center[0] < 6.0]
    assert inner, [(o.id, o.center) for o in rep.openings]
    g = groups_by_name(model)[inner[0].id]
    assert abs(g["ex"][1]) == pytest.approx(1.0, abs=1e-6) and abs(g["ex"][0]) < 1e-6
    assert g["ey"] == pytest.approx([1.0, 0.0], abs=1e-6)
    assert ezdxf is not None and T == 30 and WIN_S
