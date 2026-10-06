"""Detailed doors and windows built from oriented boxes (no boolean operations).

Every opening has a local frame: u along the wall, v across it (wall thickness T, centred on 0)
and z up. Parts go into four mesh groups:
  Telai     frames, linings (imbotti), casings (cornici), sashes, sills
  Ante      door leaves
  Vetri     glass, one pane per sash
  Maniglie  handles, escutcheon plates and keyhole plates (metal)

Doors: lining + casing on both faces, one leaf per swing arc of the plan symbol (the arc centre
is the hinge, so the handle goes on the opposite edge, lever pointing to the hinge), keyhole
plate under the handle. A symbol without arc gets one leaf hinged on the left (listed in the notes
and in the openings table, where it can be changed).
Windows: fixed frame, one sash per space between the lines that cut across the symbol, a pane in
each sash, a handle on each, a sill. Portes-fenêtres (sill at 0) are the same with a threshold-free frame.
"""

from __future__ import annotations

from shapely.geometry import Polygon

from .config import Config
from .geom import oriented_rect
from .mesh import Mesh, Slab
from .openings import Opening

LINING = 0.03  # imbotto: lining of the opening's reveals and head
CASING_W = 0.07  # cornice: trim around the opening on each wall face
CASING_D = 0.012
GAP = 0.003  # clearance between leaf/sash and its frame
LEAF_T = 0.04
LEAF_FLOOR_GAP = 0.008
FRAME_W = 0.055  # window fixed frame (telaio)
FRAME_D = 0.07
SASH_W = 0.045  # sash frame (anta)
SASH_D = 0.05
GLASS_T = 0.006
HANDLE_Z = 1.00  # door handle height
SASH_STAND = 0.005  # plate thickness on a sash/leaf face

Box = tuple[Polygon, float, float]
Parts = dict[str, list[Box]]


def _box(op: Opening, u0: float, u1: float, v0: float, v1: float, z0: float, z1: float) -> Box:
    """A box given in the opening's local frame, as (plan footprint, z0, z1)."""
    ux, uy = op.axis
    cx, cy = op.center
    uc = (u0 + u1) / 2.0
    return oriented_rect(cx + uc * ux, cy + uc * uy, ux, uy, abs(u1 - u0), min(v0, v1), max(v0, v1)), z0, z1


def _add(parts: Parts, group: str, box: Box) -> None:
    poly, z0, z1 = box
    if z1 - z0 > 1e-4 and poly.area > 1e-8:
        parts.setdefault(group, []).append(box)


def _casing(op: Opening, parts: Parts, top_limit: float, with_bottom: bool = False) -> None:
    """Cornice on both wall faces: two jambs and a head (and a sill-level strip if asked)."""
    W, T, z0, z1 = op.width, op.thickness, op.z0, op.z1
    cw = min(CASING_W, W / 4.0)
    ztop = min(z1 + cw, top_limit)
    for face in (1, -1):
        v0, v1 = (T / 2, T / 2 + CASING_D) if face > 0 else (-T / 2 - CASING_D, -T / 2)
        _add(parts, "Telai", _box(op, -W / 2 - cw, -W / 2, v0, v1, z0, ztop))
        _add(parts, "Telai", _box(op, W / 2, W / 2 + cw, v0, v1, z0, ztop))
        _add(parts, "Telai", _box(op, -W / 2 - cw, W / 2 + cw, v0, v1, z1, ztop))
        if with_bottom:
            _add(parts, "Telai", _box(op, -W / 2 - cw, W / 2 + cw, v0, v1, max(z0 - cw, 0.0), z0))


def _plate_and_lever(op: Opening, parts: Parts, uh: float, zh: float, half_thickness: float,
                     lever_dir: int, vertical_lever: bool) -> None:
    """Escutcheon plate + lever on both faces of a leaf/sash whose half thickness is given."""
    for face in (1, -1):
        base = face * half_thickness
        outer = face * (half_thickness + SASH_STAND)
        _add(parts, "Maniglie", _box(op, uh - 0.02, uh + 0.02, base, outer, zh - 0.10, zh + 0.08))
        stem_v = (outer, outer + face * 0.014)
        _add(parts, "Maniglie", _box(op, uh - 0.008, uh + 0.008, *stem_v, zh - 0.008, zh + 0.008))
        lever_v = (outer + face * 0.012, outer + face * 0.032)
        if vertical_lever:  # window handle: lever hanging down
            _add(parts, "Maniglie", _box(op, uh - 0.009, uh + 0.009, *lever_v, zh - 0.12, zh + 0.008))
        else:  # door lever: horizontal, pointing to the hinge
            u_end = uh + lever_dir * 0.12
            _add(parts, "Maniglie", _box(op, min(uh, u_end) - 0.009, max(uh, u_end) + 0.009, *lever_v,
                                         zh - 0.009, zh + 0.009))


def _keyhole(op: Opening, parts: Parts, uh: float, zh: float, half_thickness: float) -> None:
    for face in (1, -1):
        base = face * (half_thickness + SASH_STAND)
        top = face * (half_thickness + SASH_STAND + 0.004)
        _add(parts, "Maniglie", _box(op, uh - 0.013, uh + 0.013, base, top, zh - 0.095, zh - 0.045))  # toppa
        slot = face * (half_thickness + SASH_STAND + 0.007)
        _add(parts, "Maniglie", _box(op, uh - 0.003, uh + 0.003, top, slot, zh - 0.085, zh - 0.060))  # slot


def _door(op: Opening, cfg: Config, parts: Parts) -> None:
    W, T, z0, z1 = op.width, op.thickness, op.z0, op.z1
    lin = min(LINING, W / 8.0)
    # imbotto: reveals and head lining the opening, full wall depth
    _add(parts, "Telai", _box(op, -W / 2, -W / 2 + lin, -T / 2, T / 2, z0, z1))
    _add(parts, "Telai", _box(op, W / 2 - lin, W / 2, -T / 2, T / 2, z0, z1))
    _add(parts, "Telai", _box(op, -W / 2, W / 2, -T / 2, T / 2, z1 - lin, z1))
    _casing(op, parts, cfg.wall_height)
    if op.kind != "door":
        return  # a passage has no leaf
    c0, c1 = -W / 2 + lin + GAP, W / 2 - lin - GAP
    leaves = sorted(op.leaves, key=lambda leaf: leaf["hinge"]) or [{"hinge": -1}]
    if len(leaves) == 1:
        spans = [(c0, c1, leaves[0]["hinge"])]
    else:  # double door: each leaf takes its own half
        mid = 0.0
        spans = [(c0, mid - GAP / 2, leaves[0]["hinge"]), (mid + GAP / 2, c1, leaves[-1]["hinge"])]
    lz0, lz1 = z0 + LEAF_FLOOR_GAP, z1 - lin - GAP
    zh = min(z0 + HANDLE_Z, lz1 - 0.25)
    for u0, u1, hinge in spans:
        _add(parts, "Ante", _box(op, u0, u1, -LEAF_T / 2, LEAF_T / 2, lz0, lz1))
        free = u1 if hinge < 0 else u0  # the edge opposite the hinge
        uh = free - 0.05 if hinge < 0 else free + 0.05
        _plate_and_lever(op, parts, uh, zh, LEAF_T / 2, lever_dir=hinge, vertical_lever=False)
        _keyhole(op, parts, uh, zh, LEAF_T / 2)


def _window(op: Opening, cfg: Config, parts: Parts) -> None:
    W, T, z0, z1 = op.width, op.thickness, op.z0, op.z1
    fd = min(FRAME_D, max(T - 0.02, 0.03))
    fw = min(FRAME_W, W / 6.0, (z1 - z0) / 6.0)
    # fixed frame
    _add(parts, "Telai", _box(op, -W / 2, -W / 2 + fw, -fd / 2, fd / 2, z0, z1))
    _add(parts, "Telai", _box(op, W / 2 - fw, W / 2, -fd / 2, fd / 2, z0, z1))
    _add(parts, "Telai", _box(op, -W / 2 + fw, W / 2 - fw, -fd / 2, fd / 2, z0, z0 + fw))
    _add(parts, "Telai", _box(op, -W / 2 + fw, W / 2 - fw, -fd / 2, fd / 2, z1 - fw, z1))
    iu0, iu1, iz0, iz1 = -W / 2 + fw, W / 2 - fw, z0 + fw, z1 - fw
    edges = [iu0, *sorted(d for d in op.dividers if iu0 < d < iu1), iu1]
    n = len(edges) - 1
    sd = min(SASH_D, fd)
    sw = min(SASH_W, (edges[1] - edges[0]) / 4.0, (iz1 - iz0) / 4.0)
    zh = z0 + (0.5 * (z1 - z0) if z0 > 0.05 else min(0.5 * (z1 - z0), 1.05))
    for i in range(n):
        su0 = edges[i] + (GAP if i == 0 else GAP / 2)
        su1 = edges[i + 1] - (GAP if i == n - 1 else GAP / 2)
        _add(parts, "Telai", _box(op, su0, su0 + sw, -sd / 2, sd / 2, iz0, iz1))
        _add(parts, "Telai", _box(op, su1 - sw, su1, -sd / 2, sd / 2, iz0, iz1))
        _add(parts, "Telai", _box(op, su0 + sw, su1 - sw, -sd / 2, sd / 2, iz0, iz0 + sw))
        _add(parts, "Telai", _box(op, su0 + sw, su1 - sw, -sd / 2, sd / 2, iz1 - sw, iz1))
        _add(parts, "Vetri", _box(op, su0 + sw, su1 - sw, -GLASS_T / 2, GLASS_T / 2, iz0 + sw, iz1 - sw))
        # the handle goes on the stile towards the middle of the window; a single sash follows its hinge
        centre = (su0 + su1) / 2.0
        if n == 1:
            side = 1 if op.hinge <= 0 else -1
        else:
            side = 1 if centre <= 0.0 else -1
        uh = su1 - sw / 2 if side > 0 else su0 + sw / 2
        _plate_and_lever(op, parts, uh, zh, sd / 2, lever_dir=0, vertical_lever=True)
    if z0 > 0.05:  # davanzale
        _add(parts, "Telai", _box(op, -W / 2 - 0.03, W / 2 + 0.03, -T / 2 - 0.03, T / 2 + 0.03, z0 - 0.03, z0))
    _casing(op, parts, cfg.wall_height, with_bottom=False)


def build_fixtures(openings: list[Opening], cfg: Config) -> Parts:
    parts: Parts = {}
    for op in openings:
        if not op.keep or op.width < 0.2 or op.thickness < 0.02:
            continue
        if op.kind == "window":
            _window(op, cfg, parts)
        else:
            _door(op, cfg, parts)
    return parts


def add_fixtures(mesh: Mesh, openings: list[Opening], cfg: Config) -> None:
    for group, boxes in build_fixtures(openings, cfg).items():
        for poly, z0, z1 in boxes:
            mesh.add_extrusion(group, [Slab(z0, z1, poly)], bottom=True)
