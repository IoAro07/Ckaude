"""Cinema 4D model.json: a neutral description a Python script inside Cinema 4D can build.

Units are centimetres and axes are the CAD ones (x, y, z up); the importer maps them to
Cinema 4D's (x, z, y) and reorders the vertices for the handedness change. Faces are
quads (a triangle repeats its last vertex). Objects are grouped under named nulls, each
object has a material key that the importer turns into a (Corona) material.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from .mesh import Mesh

# mesh group -> (null name, material key)
GROUPS = {
    "Muri": ("Murature", "wall"),
    "Muri_esterno": ("Murature", "wall_outer"),
    "Muri_interno": ("Murature", "wall_inner"),
    "Pilastri": ("Pilastri", "column"),
    "Muretti": ("Murature", "low_wall"),
    "Cordoli": ("Murature", "curb"),
    "Vetri": ("Infissi", "glass"),
    "Telai": ("Infissi", "frame"),
    "Ante": ("Infissi", "door_leaf"),
    "Maniglie": ("Infissi", "metal"),
    "Pavimento": ("Pavimenti", "floor"),
    "Tramezzi": ("Murature", "partition"),
    "Battiscopa": ("Battiscopa", "skirting"),
    "Soffitto": ("Soffitti", "ceiling"),
    "Tetto": ("Tetto", "roof"),
    # the garden: the ground and the pool, then plants and furniture (a null for each kind, one for each object)
    "Pavimentazione": ("Giardino", "paving"),
    "Bordi": ("Giardino", "garden_edge"),
    "Prato": ("Giardino", "lawn"),
    "Terreno": ("Giardino", "soil"),
    "Vasca": ("Piscina", "pool_shell"),
    "Acqua": ("Piscina", "water"),
    "Tronco": ("Alberi", "trunk"),
    "Chioma": ("Alberi", "foliage"),
    "Siepe": ("Siepi", "hedge"),
    "Cespuglio": ("Cespugli", "shrub"),
    "Arredo": ("Arredi_esterni", "outdoor_furniture"),
}
FIXTURE_PARTS = ("Telai", "Ante", "Vetri", "Maniglie")
GARDEN_PARTS = ("Tronco", "Chioma", "Siepe", "Cespuglio", "Arredo")  # parts of a plant / piece of furniture
FRAME_PARTS = FIXTURE_PARTS + GARDEN_PARTS  # groups named PART_ID (Telai_F01, Chioma_A01) hang under a null ID
MATERIALS = {
    "wall": {"name": "Muro", "color": [0.90, 0.89, 0.86], "rough": 0.85},
    "wall_outer": {"name": "Muro esterno", "color": [0.85, 0.78, 0.68], "rough": 0.9},
    "wall_inner": {"name": "Muro interno", "color": [0.94, 0.93, 0.91], "rough": 0.85},
    "column": {"name": "Pilastro", "color": [0.78, 0.78, 0.78], "rough": 0.8},
    "low_wall": {"name": "Muretto", "color": [0.66, 0.64, 0.60], "rough": 0.9},
    "curb": {"name": "Cordolo", "color": [0.52, 0.51, 0.50], "rough": 0.9},
    "glass": {"name": "Vetro", "color": [0.70, 0.85, 0.95], "rough": 0.0},
    "partition": {"name": "Tramezzo", "color": [0.93, 0.92, 0.89], "rough": 0.85},
    "skirting": {"name": "Battiscopa", "color": [0.96, 0.96, 0.95], "rough": 0.5},
    "floor": {"name": "Pavimento", "color": [0.60, 0.56, 0.52], "rough": 0.5},
    "ceiling": {"name": "Soffitto", "color": [0.95, 0.95, 0.95], "rough": 0.9},
    "roof": {"name": "Tetto", "color": [0.62, 0.32, 0.24], "rough": 0.7},
    "frame": {"name": "Telaio", "color": [0.92, 0.92, 0.90], "rough": 0.45},
    "door_leaf": {"name": "Anta porta", "color": [0.55, 0.38, 0.22], "rough": 0.5},
    "metal": {"name": "Metallo", "color": [0.78, 0.78, 0.80], "rough": 0.25},
    "paving": {"name": "Pavimentazione esterna", "color": [0.62, 0.61, 0.60], "rough": 0.7},
    "garden_edge": {"name": "Bordi e cordoli", "color": [0.40, 0.40, 0.42], "rough": 0.8},
    "lawn": {"name": "Prato", "color": [0.36, 0.55, 0.26], "rough": 0.95},
    "soil": {"name": "Terreno", "color": [0.45, 0.37, 0.29], "rough": 0.95},
    "pool_shell": {"name": "Vasca piscina", "color": [0.80, 0.86, 0.89], "rough": 0.4},
    "water": {"name": "Acqua", "color": [0.30, 0.60, 0.78], "rough": 0.02},
    "trunk": {"name": "Tronco", "color": [0.38, 0.27, 0.18], "rough": 0.9},
    "foliage": {"name": "Chioma", "color": [0.20, 0.45, 0.20], "rough": 0.9},
    "hedge": {"name": "Siepe", "color": [0.16, 0.36, 0.15], "rough": 0.95},
    "shrub": {"name": "Cespuglio", "color": [0.27, 0.50, 0.22], "rough": 0.95},
    "outdoor_furniture": {"name": "Arredi esterni", "color": [0.82, 0.72, 0.52], "rough": 0.6},
}


def _frames(mesh: Mesh, openings, origin_offset: tuple[float, float], garden_objects=None) -> dict[str, dict]:
    """The local frame of each opening's (and each plant's, piece of furniture's) group, in centimetres and CAD
    axes: the origin at the centre of the object and the lowest point of its parts, X (``ex``) along the wall (or
    along the length of the plant), Y (``ey``) across it looking out, Z up. A group is then a null placed there,
    with its objects' points relative to it."""
    low: dict[str, float] = {}
    for gname, faces in mesh.groups.items():
        base, _, oid = gname.partition("_")
        if oid and base in FRAME_PARTS:
            for ids, _n in faces:
                for vid in ids:
                    low[oid] = min(low.get(oid, math.inf), mesh.vertices[vid][2])
    frames = {}
    for o in openings or []:
        if o.id not in low:
            continue
        sign = o.face_sign or 1
        ux, uy = o.axis
        frames[o.id] = {
            "origin": [round((o.center[0] + origin_offset[0]) * 100, 3), round((o.center[1] + origin_offset[1]) * 100, 3),
                       round(low[o.id] * 100, 3)],
            "ex": [round(sign * ux, 6) + 0.0, round(sign * uy, 6) + 0.0],  # "+ 0.0": no -0.0 in the file
            "ey": [round(-sign * uy, 6) + 0.0, round(sign * ux, 6) + 0.0],
        }
    for g in garden_objects or []:
        if g.id not in low:
            continue
        ux, uy = g.axis
        frames[g.id] = {
            "origin": [round((g.cx + origin_offset[0]) * 100, 3), round((g.cy + origin_offset[1]) * 100, 3),
                       round(low[g.id] * 100, 3)],
            "ex": [round(ux, 6) + 0.0, round(uy, 6) + 0.0],
            "ey": [round(-uy, 6) + 0.0, round(ux, 6) + 0.0],
        }
    return frames


def to_model_dict(mesh: Mesh, name: str, origin_offset: tuple[float, float] = (0.0, 0.0), openings=None,
                  garden_objects=None) -> dict:
    """``mesh`` is in metres, CAD axes. ``origin_offset`` (m) was already applied to it and is
    recorded so the CAD position can be recovered. With ``openings`` the group of each door/window has its
    own origin and axes (``origin``, ``ex``, ``ey``) and its objects' points are relative to them; so does each
    plant and piece of furniture of the garden (``garden_objects``)."""
    frames = _frames(mesh, openings, origin_offset, garden_objects)
    groups, objects, used = [], [], set()
    for gname, faces in mesh.groups.items():
        base, _, opening = gname.partition("_")
        null, mat = GROUPS.get(gname) or GROUPS.get(base, (gname, "wall"))
        if null not in used:
            used.add(null)
            groups.append({"name": null, "label": null, "parent": name})
        frame = None
        if opening and base in FRAME_PARTS:  # Telai_F01: under a null F01 inside Infissi; Chioma_A01 inside Alberi
            frame = frames.get(opening)
            if opening not in used:
                used.add(opening)
                groups.append({"name": opening, "label": opening, "parent": null, **(frame or {})})
            null = opening
        index: dict[int, int] = {}
        points: list[float] = []
        polys: list[int] = []
        for ids, _normal in faces:
            quad = []
            for vid in ids:
                if vid not in index:
                    index[vid] = len(index)
                    x, y, z = (c * 100 for c in mesh.vertices[vid])
                    if frame:  # relative to the group's origin, along its axes
                        ox, oy, oz = frame["origin"]
                        dx, dy = x - ox, y - oy
                        (ex0, ex1), (ey0, ey1) = frame["ex"], frame["ey"]
                        x, y, z = dx * ex0 + dy * ex1, dx * ey0 + dy * ey1, z - oz
                    points += [round(x, 3), round(y, 3), round(z, 3)]
                quad.append(index[vid])
            # triangles repeat the last vertex; anything larger is fanned
            for k in range(1, len(quad) - 1, 2 if len(quad) > 3 else 1):
                tri = [quad[0], quad[k], quad[k + 1]]
                if len(quad) == 4:
                    polys += quad
                    break
                polys += [*tri, tri[2]]
        objects.append({"name": gname, "group": null, "material": mat, "points": points, "polys": polys})
    used_mats = {o["material"] for o in objects}
    return {
        "name": name,
        "units": "cm",
        "origin_offset_m": [round(origin_offset[0], 4), round(origin_offset[1], 4)],
        "materials": {k: v for k, v in MATERIALS.items() if k in used_mats},
        "groups": groups,
        "objects": objects,
    }


def write_json(model: dict, path: str | Path) -> Path:
    path = Path(path)
    path.write_text(json.dumps(model, separators=(",", ":")), encoding="utf-8")
    return path
