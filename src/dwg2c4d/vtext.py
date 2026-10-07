"""Texts that were exploded into lines (EXPLODE / TXTEXP): rebuilt from their letters.

1. segments that touch form a glyph (a letter, a digit); a glyph inside another one's box is
   one of its holes (0 6 8 A B);
2. glyphs of similar size, side by side, form a word, horizontal or vertical;
3. each word is read at the angles it can have (0/90/180/270 or its own line) by comparing
   every glyph with the templates of ``glyphs.py``; the reading with the best score wins.

The result is a list of ``RawText``, the same as real TEXT/MTEXT, so the rest of the program
does not care where a text came from.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from ezdxf import path as dxfpath
from ezdxf.document import Drawing

from .config import Config
from .glyphs import Matcher, fill_segments
from .reader import layer_used
from .texts import RawText

MAX_GLYPH = 0.40  # m: a letter taller than this is not text
TOUCH = 0.0005  # m: segments whose ends are this close are connected
MIN_SEGMENTS = 8
MIN_CONFIDENCE = 0.42
TEXT_LAYER_TOKENS = ("quot", "test", "text", "annot", "scritt", "nom", "dim", "note", "label", "misur",
                     "richiam", "leader", "didasc")
SKIP_LAYER_TOKENS = ("arred", "furnit", "verde", "retin", "hatch", "luci", "prese", "interrutt", "immagin")


def _close_pairs(points: np.ndarray, radius: float):
    """Index pairs (i < j) whose points are at most ``radius`` apart (grid hashing, no scipy)."""
    cells: dict[tuple[int, int], list[int]] = {}
    for i, key in enumerate(map(tuple, np.floor(points / radius).astype(int))):
        cells.setdefault(key, []).append(i)
    for (kx, ky), idx in cells.items():
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in cells.get((kx + dx, ky + dy), ()):
                    for i in idx:
                        if i < j and math.hypot(*(points[i] - points[j])) <= radius:
                            yield i, j


class _Union:
    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, a: int) -> int:
        while self.parent[a] != a:
            self.parent[a] = self.parent[self.parent[a]]
            a = self.parent[a]
        return a

    def join(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def _components(segs: np.ndarray, tol: float) -> list[list[int]]:
    n = len(segs)
    ends = np.vstack([segs[:, 0:2], segs[:, 2:4]])
    owner = np.concatenate([np.arange(n), np.arange(n)])
    uf = _Union(n)
    for i, j in _close_pairs(ends, max(tol, 1e-9)):
        uf.join(owner[i], owner[j])
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(uf.find(i), []).append(i)
    return list(groups.values())


def _bbox(segs: np.ndarray) -> tuple[float, float, float, float]:
    xs = np.concatenate([segs[:, 0], segs[:, 2]])
    ys = np.concatenate([segs[:, 1], segs[:, 3]])
    return xs.min(), ys.min(), xs.max(), ys.max()


def _rotate(segs: np.ndarray, angle: float, cx: float, cy: float) -> np.ndarray:
    a = math.radians(-angle)
    c, s = math.cos(a), math.sin(a)
    out = segs.copy()
    for k in (0, 2):
        x, y = segs[:, k] - cx, segs[:, k + 1] - cy
        out[:, k], out[:, k + 1] = x * c - y * s, x * s + y * c
    return out


@dataclass
class Glyph:
    idx: np.ndarray
    segs: np.ndarray
    bbox: tuple
    cx: float
    cy: float
    size: float

    @staticmethod
    def of(idx, segs: np.ndarray) -> "Glyph":
        s = segs[idx]
        b = _bbox(s)
        return Glyph(np.asarray(idx), s, b, (b[0] + b[2]) / 2, (b[1] + b[3]) / 2, max(b[2] - b[0], b[3] - b[1]))


def _glyphs(segs: np.ndarray, max_size: float, tol: float) -> tuple[list[Glyph], list[Glyph]]:
    glyphs, others = [], []
    for comp in _components(segs, tol):
        g = Glyph.of(comp, segs)
        (glyphs if g.size <= max_size else others).append(g)
    glyphs.sort(key=lambda g: -g.size)
    merged: list[Glyph] = []
    for g in glyphs:
        host = next((h for h in merged
                     if g.size < h.size and g.bbox[0] >= h.bbox[0] - 1e-6 and g.bbox[2] <= h.bbox[2] + 1e-6
                     and g.bbox[1] >= h.bbox[1] - 1e-6 and g.bbox[3] <= h.bbox[3] + 1e-6), None)
        if host is None:
            merged.append(g)
        else:
            host.idx = np.concatenate([host.idx, g.idx])
            host.segs = segs[host.idx]
            host.bbox = _bbox(host.segs)
            host.cx, host.cy = (host.bbox[0] + host.bbox[2]) / 2, (host.bbox[1] + host.bbox[3]) / 2
            host.size = max(host.bbox[2] - host.bbox[0], host.bbox[3] - host.bbox[1])
    return merged, others


def _gap(a: Glyph, b: Glyph) -> float:
    dx = max(a.bbox[0] - b.bbox[2], b.bbox[0] - a.bbox[2], 0)
    dy = max(a.bbox[1] - b.bbox[3], b.bbox[1] - a.bbox[3], 0)
    return math.hypot(dx, dy)


def _words(glyphs: list[Glyph], gap_factor: float = 0.42, size_ratio: float = 1.7) -> list[list[Glyph]]:
    """Group glyphs into words, linking horizontally (text at 0/180) or vertically (90/270);
    each glyph joins the bigger of its two groups, so two overlapping lines do not merge."""
    n = len(glyphs)
    if n == 0:
        return []
    centres = np.array([[g.cx, g.cy] for g in glyphs])
    uf = {"H": _Union(n), "V": _Union(n)}
    for i, j in _close_pairs(centres, max(g.size for g in glyphs) * 3):
        a, b = glyphs[i], glyphs[j]
        big, small = max(a.size, b.size), min(a.size, b.size)
        ratio = big / max(small, 1e-9)
        if size_ratio < ratio <= 4.0:
            continue  # different sizes: "ht" small next to "100" big
        if _gap(a, b) > gap_factor * big:
            continue
        dx, dy = abs(a.cx - b.cx), abs(a.cy - b.cy)
        axis = "H" if dx >= dy else "V"
        if (dy if axis == "H" else dx) > 0.45 * big and ratio <= size_ratio:
            continue  # not on the same line
        uf[axis].join(i, j)
    comp: dict[str, dict[int, list[int]]] = {"H": {}, "V": {}}
    for axis in "HV":
        for i in range(n):
            comp[axis].setdefault(uf[axis].find(i), []).append(i)
    words: dict[tuple[str, int], list[Glyph]] = {}
    for i in range(n):
        h, v = comp["H"][uf["H"].find(i)], comp["V"][uf["V"].find(i)]
        key = ("H", uf["H"].find(i)) if len(h) >= len(v) else ("V", uf["V"].find(i))
        words.setdefault(key, []).append(glyphs[i])
    return list(words.values())


def _angles(word: list[Glyph]) -> list[float]:
    if len(word) == 1:
        return [0.0, 90.0, 180.0, 270.0]
    c = np.array([[g.cx, g.cy] for g in word])
    _, _, vt = np.linalg.svd(c - c.mean(axis=0), full_matrices=False)
    ang = (math.degrees(math.atan2(vt[0][1], vt[0][0])) + 360) % 360
    for k in (0, 90, 180, 270, 360):
        if abs(ang - k) < 4:
            ang = k % 360  # orthogonal texts are the common case
    return [ang, (ang + 180) % 360]


def _read(word: list[Glyph], angle: float, matcher: Matcher):
    cx, cy = np.mean([g.cx for g in word]), np.mean([g.cy for g in word])
    items = []
    for g in word:
        rotated = _rotate(g.segs, angle, cx, cy)
        b = _bbox(rotated)
        mask = fill_segments(rotated)
        cand = matcher.classify(mask, top=4) if mask is not None else []
        if cand:
            items.append(((b[0] + b[2]) / 2, b[3] - b[1], cand))
    items.sort(key=lambda t: t[0])
    return items


def _recognise(word: list[Glyph], matcher: Matcher):
    best = None
    for ang in _angles(word):
        items = _read(word, ang, matcher)
        if not items:
            continue
        chars = [c[0][0] for _, _, c in items]
        scores = [c[0][1] for _, _, c in items]
        digits = sum(ch.isdigit() for ch in chars)
        if 0 < digits < len(chars) and digits * 2 >= len(chars):
            # mostly digits: a letter that could be a digit is one (J -> 1, O -> 0)
            for k, (_, _, cand) in enumerate(items):
                if not chars[k].isdigit():
                    dig = [(c, s) for c, s in cand if c.isdigit()]
                    if dig:
                        chars[k], scores[k] = dig[0]
        conf = float(np.mean(scores))
        upright = ang < 4 or abs(ang - 90) < 4
        penalty = 0.0 if upright else (0.18 if abs(ang - 180) < 4 or abs(ang - 270) < 4 else 0.05)
        if best is None or conf - penalty > best[0]:
            best = (conf - penalty, ang, "".join(chars), conf, items)
    return best


def recognise_text(segs: np.ndarray, layer: str, min_conf: float = MIN_CONFIDENCE,
                   matcher: Matcher | None = None) -> list[RawText]:
    """Words made of the segments ``segs`` (N x 4, metres), as RawText."""
    matcher = matcher or Matcher.load()
    glyphs, _ = _glyphs(segs, MAX_GLYPH, TOUCH)
    out: list[RawText] = []
    for word in _words(glyphs):
        best = _recognise(word, matcher)
        if best is None or best[3] < min_conf:
            continue
        _, angle, text, _, items = best
        cx, cy = float(np.mean([g.cx for g in word])), float(np.mean([g.cy for g in word]))
        box = _bbox(_rotate(np.vstack([g.segs for g in word]), angle, cx, cy))
        out.append(RawText([text], cx, cy, float(angle), float(max(h for _, h, _ in items)), layer, "esploso",
                           width=float(box[2] - box[0])))
    return out


# --- reading the segments from the drawing ----------------------------------------------------

def _is_text_layer(name: str, extra: tuple[str, ...]) -> bool:
    import fnmatch

    low = name.lower()
    if name == "0":
        return True
    if any(fnmatch.fnmatchcase(low, p.lower()) for p in extra):
        return True
    if any(tok in low for tok in SKIP_LAYER_TOKENS):
        return False
    return any(tok in low for tok in TEXT_LAYER_TOKENS)


def read_segments(doc: Drawing, cfg: Config, scale: float) -> dict[str, np.ndarray]:
    """Line work of the layers that may hold exploded texts, per layer: N x 4 arrays in metres."""
    extra = tuple(cfg.text_layers)
    flat = 0.0005 / scale  # flattening distance, drawing units
    area = cfg.area
    per_layer: dict[str, list[tuple[float, float, float, float]]] = {}
    for e in doc.modelspace():
        t = e.dxftype()
        if t not in ("LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "ELLIPSE", "SPLINE"):
            continue
        layer = e.dxf.layer
        if not _is_text_layer(layer, extra):
            continue
        if not layer_used(doc, cfg, layer):
            continue
        try:
            if t == "LINE":
                s, d = e.dxf.start, e.dxf.end
                polylines = [[(s.x, s.y), (d.x, d.y)]]
            else:
                polylines = [[(v.x, v.y) for v in sub.flattening(flat)]
                             for sub in dxfpath.make_path(e).sub_paths()]
        except Exception:
            continue
        for pts in polylines:
            for (ax, ay), (bx, by) in zip(pts, pts[1:]):
                if area and not (area[0] <= ax <= area[2] and area[1] <= ay <= area[3]):
                    continue
                per_layer.setdefault(layer, []).append((ax * scale, ay * scale, bx * scale, by * scale))
    return {k: np.asarray(v, float).reshape(-1, 4) for k, v in per_layer.items() if len(v) >= MIN_SEGMENTS}


def read_exploded_texts(doc: Drawing, cfg: Config, scale: float) -> list[RawText]:
    texts: list[RawText] = []
    matcher = None
    for layer, segs in read_segments(doc, cfg, scale).items():
        matcher = matcher or Matcher.load()
        texts.extend(recognise_text(segs, layer, matcher=matcher))
    return texts
