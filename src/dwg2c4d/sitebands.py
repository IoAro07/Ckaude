"""Steps drawn as a row of hatched bands: a stepped seat, a flight of garden steps, built as treads one riser higher than the other.

A site plan draws a curved flight of steps, or a stepped seat, as a row of hatched bands, one for each tread (a straight stair is a
row of lines: ``sitesteps``). Hatches that are long and thin (``BAND_THICKNESS``, ``BAND_ASPECT``) and lie one beside the other
(within ``BAND_GAP``) in a row of at least ``MIN_BANDS`` are the treads of a stair: they are taken out of the surfaces (a hatch of
the steps is no lawn and no paving) and built as one object, ``Sito_Gradinata_01``, each band a prism one riser higher than the
previous.

The drawing does not say which end is the top, nor how high a riser is: the top is the end nearer to the building, and the riser
``DEFAULT_RISE``; the report says so.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
import shapely
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry
from shapely.strtree import STRtree

from .garden import _Fill
from .geom import polygons_of, union
from .mesh import SITE_PREFIX, Mesh, Slab
from .sitesteps import DEFAULT_RISE, FOOT_DEPTH
from .terrainmesh import EMBED

BAND_THICKNESS = (0.15, 0.70)  # m: a band of a tread is this deep (2 * area / perimeter)
BAND_ASPECT = 2.5  # ... and this many times longer than deep
BAND_AREA = (0.3, 30.0)  # m2
BAND_GAP = 0.30  # m: two bands this close or closer are neighbours in the row
MIN_BANDS = 3  # a row of fewer bands than this may be anything (a deck of boards, a table): it is not taken for steps
KIND_NAME = "Gradinata"


@dataclass
class BandStair:
    """A stair of hatched bands: the bands foot first, one riser higher each."""

    id: str  # Sito_Gradinata_01
    bands: list[Polygon]  # metres, the frame of the plan; the foot first
    rise: float = DEFAULT_RISE
    toward: str = "la casa"
    keep: bool = True

    @property
    def count(self) -> int:
        return len(self.bands)

    @property
    def height(self) -> float:
        return self.count * self.rise

    @property
    def geom(self) -> BaseGeometry:
        return union(self.bands)


def _is_band(poly: Polygon) -> bool:
    """Long and thin: deep ``BAND_THICKNESS``, ``BAND_ASPECT`` times longer than deep, of a size a tread can be."""
    if not BAND_AREA[0] <= poly.area <= BAND_AREA[1] or poly.length <= 0:
        return False
    thick = 2.0 * poly.area / poly.length
    corners = list(shapely.minimum_rotated_rectangle(poly).exterior.coords)
    sides = sorted(float(np.hypot(corners[k + 1][0] - corners[k][0], corners[k + 1][1] - corners[k][1])) for k in range(2))
    return BAND_THICKNESS[0] <= thick <= BAND_THICKNESS[1] and sides[0] > 0 and sides[1] >= BAND_ASPECT * sides[0]


def _rows(bands: list[Polygon]) -> list[list[int]]:
    """The bands grouped in rows: bands that are neighbours (within ``BAND_GAP``), a chain counts."""
    parent = list(range(len(bands)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    first, second = STRtree(bands).query(bands, predicate="dwithin", distance=BAND_GAP)
    for i, j in zip(first.tolist(), second.tolist()):
        parent[find(i)] = find(j)
    rows: dict[int, list[int]] = {}
    for i in range(len(bands)):
        rows.setdefault(find(i), []).append(i)
    return [r for r in rows.values() if len(r) >= MIN_BANDS]


def _foot_first(bands: list[Polygon], building: BaseGeometry) -> list[Polygon]:
    """The bands of a row in the order they follow one another, the foot first: along the main direction of their centres, the
    top being the end nearer to the building."""
    centres = np.array([b.centroid.coords[0] for b in bands])
    _, _, vt = np.linalg.svd(centres - centres.mean(axis=0), full_matrices=False)
    order = np.argsort(centres @ vt[0])
    ordered = [bands[i] for i in order]
    to_house = building.centroid
    if ordered[0].centroid.distance(to_house) < ordered[-1].centroid.distance(to_house):
        ordered.reverse()
    return ordered


def find_band_stairs(fills: list[_Fill], building: BaseGeometry) -> tuple[list[BandStair], list[_Fill]]:
    """(the stairs of hatched bands among ``fills``, the fills without those bands). Hatches inside the building are no steps."""
    bands: list[tuple[int, Polygon]] = []  # (the fill it belongs to, the band)
    for n, f in enumerate(fills):
        bands.extend((n, p) for p in polygons_of(f.geom) if _is_band(p) and p.centroid.distance(building) > 0)
    if len(bands) < MIN_BANDS:
        return [], fills
    stairs = []
    used: set[int] = set()
    for row in _rows([p for _, p in bands]):
        used.update(row)
        stairs.append(BandStair("", _foot_first([bands[i][1] for i in row], building)))
    stairs.sort(key=lambda s: (-s.geom.area, s.geom.centroid.x))
    for n, s in enumerate(stairs, 1):
        s.id = f"{SITE_PREFIX}{KIND_NAME}_{n:02d}"
    taken: dict[int, list[Polygon]] = {}
    for i in used:
        taken.setdefault(bands[i][0], []).append(bands[i][1])
    left = [replace(f, geom=f.geom.difference(union(taken[n]).buffer(1e-6))) if n in taken else f for n, f in enumerate(fills)]
    return stairs, [f for f in left if not f.geom.is_empty]


def add_band_stairs(mesh: Mesh, stairs: list[BandStair], ground=None) -> None:
    """Each band as a prism, its top one riser higher than the band before; on a terrain the foot stands on the ground and the
    prisms go down to it, else they rise from the platform."""
    for st in stairs:
        if not st.keep:
            continue
        base = 0.0 if ground is None else float(ground.lift(*st.bands[0].centroid.coords[0]))
        for k, band in enumerate(st.bands, 1):
            if ground is None:
                low = -FOOT_DEPTH
            else:
                xs, ys = np.asarray(band.exterior.coords).T
                low = min(float(np.min(ground.lift(xs, ys))), base) - EMBED
            mesh.add_extrusion(st.id, [Slab(low, base + k * st.rise, band)], bottom=True)


def summary(stairs: list[BandStair]) -> str:
    kept = [s for s in stairs if s.keep]
    return f"{len(kept)} gradinate, {sum(s.count for s in kept)} gradini (" + ", ".join(
        f"{s.id.rsplit('_', 1)[1]}: {s.count} gradini alti {s.rise * 100:.0f} cm = {s.height:.2f} m" for s in kept) + ")"


def notes(stairs: list[BandStair], warnings: list[str]) -> None:
    """What was guessed, to be checked in the 3D preview."""
    if stairs:
        warnings.append(
            f"Gradinate: {len(stairs)} file di campiture lunghe e strette una accanto all'altra (gradini curvi, sedute a gradoni) "
            f"costruite come gradini, ognuna un oggetto a se' (Sito_Gradinata_NN), non come prato o pavimento. Il disegno non dice "
            f"quanto sale ne' da che parte: sale verso la casa e ogni gradino e' alto {DEFAULT_RISE * 100:.0f} cm: controlla "
            "nell'anteprima 3D; per non averle usa --no-superfici-sito.")
