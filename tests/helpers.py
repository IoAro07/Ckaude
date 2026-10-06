"""Helpers to inspect the generated OBJ files."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import numpy as np


class Obj:
    def __init__(self, path: Path):
        self.verts: list[list[float]] = []
        self.normals: list[list[float]] = []
        self.groups: dict[str, list[tuple[list[int], int]]] = {}
        cur = None
        for line in Path(path).read_text().splitlines():
            t = line.split()
            if not t or t[0].startswith("#"):
                continue
            if t[0] == "v":
                self.verts.append([float(x) for x in t[1:4]])
            elif t[0] == "vn":
                self.normals.append([float(x) for x in t[1:4]])
            elif t[0] == "o":
                cur = t[1]
                self.groups[cur] = []
            elif t[0] == "f":
                ids, nid = [], None
                for tok in t[1:]:
                    parts = tok.split("/")
                    ids.append(int(parts[0]) - 1)
                    nid = int(parts[2]) - 1
                self.groups[cur].append((ids, nid))
        self.v = np.array(self.verts)
        self.n = np.array(self.normals)

    def bbox(self, group: str | None = None):
        faces = self.faces(group)
        idx = sorted({i for f, _ in faces for i in f})
        pts = self.v[idx]
        return pts.min(axis=0), pts.max(axis=0)

    def faces(self, group: str | None = None):
        if group:
            return self.groups[group]
        return [f for fs in self.groups.values() for f in fs]

    def volume(self, group: str) -> float:
        """Signed volume (origin on the y=0 plane, so open bottoms at y=0 add nothing)."""
        total = 0.0
        for ids, _ in self.groups[group]:
            p = self.v[ids]
            for k in range(1, len(ids) - 1):
                total += np.dot(p[0], np.cross(p[k], p[k + 1])) / 6.0
        return total

    def winding_matches_normals(self, group: str | None = None) -> bool:
        """Every face's vertex order must agree with its declared normal."""
        for ids, ni in self.faces(group):
            p = self.v[ids]
            geo = np.zeros(3)
            for k in range(1, len(ids) - 1):
                geo += np.cross(p[k] - p[0], p[k + 1] - p[0])
            if np.dot(geo, self.n[ni]) <= 0:
                return False
        return True

    def open_edges(self, group: str, above: float | None = None) -> int:
        """Undirected edges used by only one face (a closed surface has none).
        ``above``: ignore edges lying entirely at or below this y."""
        count: Counter = Counter()
        for ids, _ in self.groups[group]:
            for a, b in zip(ids, ids[1:] + ids[:1]):
                count[(min(a, b), max(a, b))] += 1
        n = 0
        for (a, b), c in count.items():
            if c == 1:
                if above is not None and max(self.v[a][1], self.v[b][1]) <= above + 1e-9:
                    continue
                n += 1
        return n

    def non_manifold_edges(self, group: str) -> int:
        count: Counter = Counter()
        for ids, _ in self.groups[group]:
            for a, b in zip(ids, ids[1:] + ids[:1]):
                count[(min(a, b), max(a, b))] += 1
        return sum(1 for c in count.values() if c > 2)
