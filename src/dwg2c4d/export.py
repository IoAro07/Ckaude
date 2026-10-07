"""Cinema 4D model.json: a neutral description a Python script inside Cinema 4D can build.

Units are centimetres and axes are the CAD ones (x, y, z up); the importer maps them to
Cinema 4D's (x, z, y) and reorders the vertices for the handedness change. Faces are
quads (a triangle repeats its last vertex). Objects are grouped under named nulls, each
object has a material key that the importer turns into a (Corona) material.
"""

from __future__ import annotations

import json
from pathlib import Path

from .mesh import Mesh

# mesh group -> (null name, material key)
GROUPS = {
    "Muri": ("Murature", "wall"),
    "Pilastri": ("Pilastri", "column"),
    "Vetri": ("Infissi", "glass"),
    "Telai": ("Infissi", "frame"),
    "Ante": ("Infissi", "door_leaf"),
    "Maniglie": ("Infissi", "metal"),
    "Pavimento": ("Pavimenti", "floor"),
    "Tramezzi": ("Murature", "partition"),
    "Battiscopa": ("Battiscopa", "skirting"),
    "Soffitto": ("Soffitti", "ceiling"),
    "Tetto": ("Tetto", "roof"),
}
MATERIALS = {
    "wall": {"name": "Muro", "color": [0.90, 0.89, 0.86], "rough": 0.85},
    "column": {"name": "Pilastro", "color": [0.78, 0.78, 0.78], "rough": 0.8},
    "glass": {"name": "Vetro", "color": [0.70, 0.85, 0.95], "rough": 0.0},
    "partition": {"name": "Tramezzo", "color": [0.93, 0.92, 0.89], "rough": 0.85},
    "skirting": {"name": "Battiscopa", "color": [0.96, 0.96, 0.95], "rough": 0.5},
    "floor": {"name": "Pavimento", "color": [0.60, 0.56, 0.52], "rough": 0.5},
    "ceiling": {"name": "Soffitto", "color": [0.95, 0.95, 0.95], "rough": 0.9},
    "roof": {"name": "Tetto", "color": [0.62, 0.32, 0.24], "rough": 0.7},
    "frame": {"name": "Telaio", "color": [0.92, 0.92, 0.90], "rough": 0.45},
    "door_leaf": {"name": "Anta porta", "color": [0.55, 0.38, 0.22], "rough": 0.5},
    "metal": {"name": "Metallo", "color": [0.78, 0.78, 0.80], "rough": 0.25},
}


def to_model_dict(mesh: Mesh, name: str, origin_offset: tuple[float, float] = (0.0, 0.0)) -> dict:
    """``mesh`` is in metres, CAD axes. ``origin_offset`` (m) was already applied to it and is
    recorded so the CAD position can be recovered."""
    groups, objects, used = [], [], set()
    for gname, faces in mesh.groups.items():
        null, mat = GROUPS.get(gname) or GROUPS.get(gname.split("_", 1)[0], (gname, "wall"))
        if null not in used:
            used.add(null)
            groups.append({"name": null, "label": null, "parent": name})
        index: dict[int, int] = {}
        points: list[float] = []
        polys: list[int] = []
        for ids, _normal in faces:
            quad = []
            for vid in ids:
                if vid not in index:
                    index[vid] = len(index)
                    x, y, z = mesh.vertices[vid]
                    points += [round(x * 100, 3), round(y * 100, 3), round(z * 100, 3)]
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
