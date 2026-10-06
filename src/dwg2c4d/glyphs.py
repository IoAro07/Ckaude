"""Recognise one letter or digit drawn as lines (an exploded text).

The outline segments of a glyph are filled (even-odd, so the holes of 0 6 8 A B stay empty),
reduced to a 32x32 bitmap and compared with the bitmaps of the same characters in a few
sans-serif fonts (``data/glyph_templates.npz``, made once from Liberation Sans, FreeSans and
DejaVu Sans). Only numpy is needed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

GRID = 32
TEMPLATE_FILE = Path(__file__).parent / "data" / "glyph_templates.npz"


def fit_bitmap(mask: np.ndarray) -> tuple[np.ndarray | None, float]:
    """Crop a boolean mask to its bounding box and fit it into GRID x GRID keeping the proportions.
    Returns (bitmap, width / height)."""
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return None, 1.0
    crop = mask[ys.min(): ys.max() + 1, xs.min(): xs.max() + 1]
    h, w = crop.shape
    s = (GRID - 2) / max(h, w)
    nh, nw = max(1, int(round(h * s))), max(1, int(round(w * s)))
    yi = np.minimum((np.arange(nh * 4) / (nh * 4) * h).astype(int), h - 1)
    xi = np.minimum((np.arange(nw * 4) / (nw * 4) * w).astype(int), w - 1)
    small = crop[np.ix_(yi, xi)].astype(np.float32).reshape(nh, 4, nw, 4).mean(axis=(1, 3))
    out = np.zeros((GRID, GRID), np.float32)
    y0, x0 = (GRID - nh) // 2, (GRID - nw) // 2
    out[y0:y0 + nh, x0:x0 + nw] = small
    return out, w / h


def fill_segments(segs: np.ndarray, res: int = 96) -> np.ndarray | None:
    """Even-odd fill of the glyph given as segments (N x 4: x0, y0, x1, y1): a boolean
    res x res mask (row 0 = top), or None for a degenerate glyph."""
    xs = np.concatenate([segs[:, 0], segs[:, 2]])
    ys = np.concatenate([segs[:, 1], segs[:, 3]])
    x0, y1 = xs.min(), ys.max()
    span = max(xs.max() - x0, y1 - ys.min())
    if span <= 0:
        return None
    s = (res - 4) / span
    px0, py0 = (segs[:, 0] - x0) * s + 2, (y1 - segs[:, 1]) * s + 2
    px1, py1 = (segs[:, 2] - x0) * s + 2, (y1 - segs[:, 3]) * s + 2
    mask = np.zeros((res, res), bool)
    for r in range(res):
        yc = r + 0.5
        crossing = (py0 <= yc) != (py1 <= yc)
        if not crossing.any():
            continue
        t = (yc - py0[crossing]) / (py1[crossing] - py0[crossing])
        xc = np.sort(px0[crossing] + t * (px1[crossing] - px0[crossing]))
        for i in range(0, len(xc) - 1, 2):
            c0, c1 = int(math.ceil(xc[i] - 0.5)), int(math.floor(xc[i + 1] - 0.5))
            if c1 >= c0:
                mask[r, max(c0, 0): min(c1, res - 1) + 1] = True
    return mask


@dataclass
class Matcher:
    bitmaps: np.ndarray
    aspects: np.ndarray
    chars: np.ndarray
    _flat: np.ndarray = field(init=False)

    def __post_init__(self) -> None:
        self._flat = self.bitmaps.reshape(len(self.bitmaps), -1)

    @staticmethod
    def load(path: str | Path = TEMPLATE_FILE) -> "Matcher":
        data = np.load(path)
        return Matcher(data["bitmaps"], data["aspects"], data["chars"])

    def classify(self, mask: np.ndarray, allowed: str | None = None, top: int = 3) -> list[tuple[str, float]]:
        """[(character, score)] best first; score is an IoU lowered by a wrong aspect ratio."""
        bmp, aspect = fit_bitmap(mask)
        if bmp is None:
            return []
        flat = bmp.reshape(-1)
        iou = np.minimum(self._flat, flat).sum(axis=1) / np.maximum(np.maximum(self._flat, flat).sum(axis=1), 1e-6)
        score = iou - 0.35 * np.minimum(np.abs(np.log(aspect / self.aspects)), 1.5)
        best: dict[str, float] = {}
        for i in np.argsort(-score):
            ch = str(self.chars[i])
            if allowed is not None and ch not in allowed:
                continue
            if ch not in best:
                best[ch] = float(score[i])
            if len(best) >= top:
                break
        return sorted(best.items(), key=lambda kv: -kv[1])
