"""Wavefront OBJ + MTL writer, oriented for Cinema 4D (Y up)."""

from __future__ import annotations

from pathlib import Path

from .mesh import Mesh

OUT_SCALE = {"m": 1.0, "cm": 100.0, "mm": 1000.0}

# name -> (diffuse RGB, opacity)
MATERIALS = {
    "Muri": ((0.90, 0.89, 0.86), 1.0),
    "Muri_esterno": ((0.85, 0.78, 0.68), 1.0),
    "Muri_interno": ((0.94, 0.93, 0.91), 1.0),
    "Pilastri": ((0.78, 0.78, 0.78), 1.0),
    "Pavimento": ((0.60, 0.56, 0.52), 1.0),
    "Soffitto": ((0.95, 0.95, 0.95), 1.0),
    "Vetri": ((0.70, 0.85, 0.95), 0.3),
    "Tetto": ((0.62, 0.32, 0.24), 1.0),
    "Telai": ((0.92, 0.92, 0.90), 1.0),
    "Ante": ((0.55, 0.38, 0.22), 1.0),
    "Maniglie": ((0.78, 0.78, 0.80), 1.0),
    "Tramezzi": ((0.93, 0.92, 0.89), 1.0),
    "Battiscopa": ((0.96, 0.96, 0.95), 1.0),
    "Pavimentazione": ((0.62, 0.61, 0.60), 1.0),
    "Bordi": ((0.40, 0.40, 0.42), 1.0),
    "Prato": ((0.36, 0.55, 0.26), 1.0),
    "Terreno": ((0.45, 0.37, 0.29), 1.0),
    "Vasca": ((0.80, 0.86, 0.89), 1.0),
    "Acqua": ((0.30, 0.60, 0.78), 0.55),
    "Tronco": ((0.38, 0.27, 0.18), 1.0),
    "Chioma": ((0.20, 0.45, 0.20), 1.0),
    "Siepe": ((0.16, 0.36, 0.15), 1.0),
    "Cespuglio": ((0.27, 0.50, 0.22), 1.0),
    "Arredo": ((0.82, 0.72, 0.52), 1.0),
}


# parts of every opening (and of every plant, piece of garden furniture) share one material
SHARED = ("Telai", "Ante", "Vetri", "Maniglie", "Tronco", "Chioma", "Siepe", "Cespuglio", "Arredo")


def material_of(group: str) -> str:
    """Material of a mesh group: the parts of each opening (Telai_F01...) share the base one; a floor
    per room (Pavimento_Sala) keeps its own so each room can get another finish."""
    base = group.split("_", 1)[0]
    return base if base in SHARED else group


def _fmt(v: float) -> str:
    s = f"{v:.6f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def write_obj(mesh: Mesh, obj_path: str | Path, out_units: str = "m", mirror: bool = False) -> Path:
    """Write ``mesh`` (plan x/y, z up, metres) as OBJ with Y up.

    Normal: (x, y, z) -> (x, z, -y), a pure rotation, so a plan read from the top
    keeps its orientation in a right-handed viewer. ``mirror`` flips it
    (x, y, z) -> (x, z, y) and reverses the face winding to compensate.
    """
    obj_path = Path(obj_path)
    mtl_path = obj_path.with_suffix(".mtl")
    s = OUT_SCALE[out_units]
    sy = 1.0 if mirror else -1.0

    def pos(p):
        return p[0] * s, p[2] * s, p[1] * s * sy

    def nrm(n):
        return n[0], n[2], n[1] * sy

    lines = [
        "# Generato da dwg2c4d",
        f"# Unita': {out_units}  |  asse verticale: Y",
        f"mtllib {mtl_path.name}",
    ]
    lines += ["v " + " ".join(_fmt(c) for c in pos(v)) for v in mesh.vertices]
    lines += ["vn " + " ".join(_fmt(c) for c in nrm(n)) for n in mesh.normals]
    for name, faces in mesh.groups.items():
        lines += [f"o {name}", f"usemtl {material_of(name)}", "s off"]
        for ids, ni in faces:
            order = ids[::-1] if mirror else ids
            lines.append("f " + " ".join(f"{i + 1}//{ni + 1}" for i in order))
    obj_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    mtl = ["# Generato da dwg2c4d"]
    for name in dict.fromkeys(material_of(g) for g in mesh.groups):
        (r, g, b), alpha = MATERIALS.get(name) or MATERIALS.get(name.split("_", 1)[0], ((0.8, 0.8, 0.8), 1.0))
        mtl += [
            f"newmtl {name}",
            f"Kd {r} {g} {b}",
            "Ka 0 0 0",
            "Ks 0.05 0.05 0.05" if name not in ("Vetri", "Maniglie") else "Ks 0.6 0.6 0.6",
            f"d {alpha}",
            "illum 2",
            "",
        ]
    mtl_path.write_text("\n".join(mtl), encoding="utf-8")
    return obj_path
