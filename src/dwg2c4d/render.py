"""A small software renderer (z-buffer, flat shading) for previews and checks.

Needs only numpy; matplotlib is used to write the PNG. Meant for a few thousand triangles.
Coordinates are the mesh's own: metres, plan x/y, z up.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from .mesh import Mesh

COLORS = {
    "Muri": (0.86, 0.84, 0.80), "Muri_esterno": (0.84, 0.74, 0.62), "Muri_interno": (0.93, 0.92, 0.90),
    "Pilastri": (0.75, 0.75, 0.75), "Pavimento": (0.66, 0.60, 0.54),
    "Soffitto": (0.95, 0.95, 0.95), "Tetto": (0.72, 0.40, 0.31), "Vetri": (0.50, 0.72, 0.88),
    "Telai": (0.97, 0.97, 0.95), "Tramezzi": (0.90, 0.88, 0.84), "Battiscopa": (0.98, 0.98, 0.97), "Ante": (0.60, 0.42, 0.25), "Maniglie": (0.12, 0.12, 0.14),
}
BACKGROUND = (0.96, 0.96, 0.97)


def mesh_triangles(mesh: Mesh, hide: tuple[str, ...] = (), clip: tuple[float, float, float, float] | None = None):
    """(triangles Nx3x3, colors Nx3) of a Mesh. ``clip`` = (xmin, xmax, ymin, ymax) keeps only the
    faces whose centre is inside; ``hide`` drops whole groups."""
    tris, cols = [], []
    verts = mesh.vertices
    for group, faces in mesh.groups.items():
        if group in hide:
            continue
        color = COLORS.get(group) or COLORS.get(group.split("_", 1)[0], (0.8, 0.8, 0.8))
        for ids, _ in faces:
            pts = [verts[i] for i in ids]
            if clip is not None:
                cx = sum(p[0] for p in pts) / len(pts)
                cy = sum(p[1] for p in pts) / len(pts)
                if not (clip[0] <= cx <= clip[1] and clip[2] <= cy <= clip[3]):
                    continue
            for k in range(1, len(pts) - 1):
                tris.append((pts[0], pts[k], pts[k + 1]))
                cols.append(color)
    return np.asarray(tris, float).reshape(-1, 3, 3), np.asarray(cols, float).reshape(-1, 3)


def _look_at(eye, target, up=(0.0, 0.0, 1.0)):
    f = np.asarray(target, float) - np.asarray(eye, float)
    f /= np.linalg.norm(f)
    r = np.cross(f, up)
    r /= np.linalg.norm(r)
    u = np.cross(r, f)
    return r, u, f


def render(tris: np.ndarray, cols: np.ndarray, azimuth: float, elevation: float, size=(900, 640),
           fov: float = 32.0, margin: float = 1.08, perspective: bool = True) -> np.ndarray:
    """Render triangles from a camera on a sphere around their centre.
    azimuth: degrees from the +x axis towards +y (the camera looks at the model from there)."""
    w, h = size
    img = np.empty((h, w, 3))
    img[:] = BACKGROUND
    if len(tris) == 0:
        return img
    pts = tris.reshape(-1, 3)
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    centre = (lo + hi) / 2.0
    radius = float(np.linalg.norm(hi - lo)) / 2.0 or 1.0
    az, el = math.radians(azimuth), math.radians(elevation)
    direction = np.array([math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), math.sin(el)])
    dist = radius * margin / math.sin(math.radians(fov) / 2.0)
    eye = centre + direction * dist
    right, up, fwd = _look_at(eye, centre)
    rel = tris - eye
    cam = np.stack([rel @ right, rel @ up, rel @ fwd], axis=-1)  # x right, y up, z depth
    z = cam[..., 2]
    ok = (z > 1e-3).all(axis=1)
    cam, cols, tri3 = cam[ok], cols[ok], tris[ok]
    f = (h / 2.0) / math.tan(math.radians(fov) / 2.0)
    if perspective:
        sx = w / 2.0 + f * cam[..., 0] / cam[..., 2]
        sy = h / 2.0 - f * cam[..., 1] / cam[..., 2]
    else:
        s = f / dist
        sx, sy = w / 2.0 + s * cam[..., 0], h / 2.0 - s * cam[..., 1]
    depth = cam[..., 2]
    # flat shading: normal of the triangle, turned towards the camera; light from above-left of it
    n = np.cross(tri3[:, 1] - tri3[:, 0], tri3[:, 2] - tri3[:, 0])
    ln = np.linalg.norm(n, axis=1)
    keep = ln > 1e-12
    n[keep] /= ln[keep, None]
    centre_of = tri3.mean(axis=1)
    toward = eye - centre_of
    n[(n * toward).sum(axis=1) < 0] *= -1
    light = direction * 0.55 + right * -0.45 + up * 0.55
    light /= np.linalg.norm(light)
    shade = 0.38 + 0.62 * np.clip((n * light).sum(axis=1), 0.0, 1.0)
    zbuf = np.full((h, w), np.inf)
    order = np.argsort(-depth.mean(axis=1))  # irrelevant for correctness, helps nothing; keeps it deterministic
    for t in order:
        if not keep[t]:
            continue
        xs, ys, zs = sx[t], sy[t], depth[t]
        x0, x1 = int(max(math.floor(xs.min()), 0)), int(min(math.ceil(xs.max()), w - 1))
        y0, y1 = int(max(math.floor(ys.min()), 0)), int(min(math.ceil(ys.max()), h - 1))
        if x1 < x0 or y1 < y0:
            continue
        px, py = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        d = (ys[1] - ys[2]) * (xs[0] - xs[2]) + (xs[2] - xs[1]) * (ys[0] - ys[2])
        if abs(d) < 1e-9:
            continue
        l0 = ((ys[1] - ys[2]) * (px - xs[2]) + (xs[2] - xs[1]) * (py - ys[2])) / d
        l1 = ((ys[2] - ys[0]) * (px - xs[2]) + (xs[0] - xs[2]) * (py - ys[2])) / d
        l2 = 1.0 - l0 - l1
        inside = (l0 >= -1e-6) & (l1 >= -1e-6) & (l2 >= -1e-6)
        if not inside.any():
            continue
        zz = l0 * zs[0] + l1 * zs[1] + l2 * zs[2]
        sub = zbuf[y0:y1 + 1, x0:x1 + 1]
        win = inside & (zz < sub - 1e-9)
        sub[win] = zz[win]
        img[y0:y1 + 1, x0:x1 + 1][win] = np.clip(cols[t] * shade[t], 0, 1)
    return img


def write_png(path: str | Path, rgb: np.ndarray) -> Path:
    """Write an H x W x 3 float (0..1) or uint8 array as a PNG (numpy and zlib only)."""
    import struct
    import zlib

    img = (np.clip(rgb, 0, 1) * 255 + 0.5).astype(np.uint8) if rgb.dtype != np.uint8 else rgb
    h, w, _ = img.shape
    raw = b"".join(b"\x00" + img[y].tobytes() for y in range(h))

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))
    path = Path(path)
    path.write_bytes(png)
    return path


def save_views(mesh: Mesh, path: str | Path, views=((-60, 30), (-120, 30), (-90, 80)), size=(700, 520),
               hide: tuple[str, ...] = (), clip=None) -> Path:
    """Render several views side by side into one PNG."""
    tris, cols = mesh_triangles(mesh, hide, clip)
    panels = [render(tris, cols, az, el, size) for az, el in views]
    return write_png(path, np.concatenate(panels, axis=1))
