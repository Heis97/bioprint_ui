"""Нарезка STL-моделей для генератора: периметры + заполнение сеткой.

Как режется слой:
1. сечение сетки треугольников плоскостью z (середина слоя) → отрезки → замкнутые контуры;
2. контуры закрашиваются в растр с шагом RES мм по правилу «чёт-нечет» (отверстия остаются
   пустыми, вложенность любая);
3. расстояние до края (cv2.distanceTransform) даёт эквидистанты: k-й периметр — изолиния
   на (k + ½)·ширина линии от края; заполнение — внутри (n·ширина) от края;
4. заполнение — параллельные линии с шагом pitch под углом слоя (0/90 — сетка), обрезанные
   по области заполнения, через одну в обратную сторону («змейкой»).
Растровый способ устойчив к узким местам и самопересечениям, которые ломают геометрическое
смещение контуров. Точность — RES (по умолчанию 0,02–0,05 мм).

Нужны numpy и opencv-python (уже в requirements.txt).
"""
from __future__ import annotations

import math
import os
import struct

import numpy as np

MAX_PIXELS = 2200          # предел размера растра по стороне


# ─────────────────────────── чтение ───────────────────────────
def load_stl(path: str) -> np.ndarray:
    """Треугольники (N, 3, 3), мм. Двоичный и текстовый STL."""
    with open(path, "rb") as f:
        data = f.read()
    if len(data) >= 84:
        n = struct.unpack_from("<I", data, 80)[0]
        if 84 + n * 50 == len(data) and n > 0:
            rec = np.frombuffer(data, dtype=np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")]),
                                count=n, offset=84)
            return rec["v"].astype(float)
    text = data.decode("ascii", "ignore")
    verts = [tuple(map(float, ln.split()[1:4])) for ln in text.splitlines()
             if ln.strip().startswith("vertex")]
    if len(verts) < 3:
        raise ValueError("Файл не похож на STL: нет треугольников")
    v = np.array(verts[: len(verts) // 3 * 3], float)
    return v.reshape(-1, 3, 3)


def prepare(tris: np.ndarray, scale: float = 1.0) -> np.ndarray:
    """Масштаб, центр XY в ноль, низ модели на Z = 0."""
    t = tris * scale
    pts = t.reshape(-1, 3)
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    shift = np.array([-(lo[0] + hi[0]) / 2, -(lo[1] + hi[1]) / 2, -lo[2]])
    return t + shift


def bounds(tris: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    pts = tris.reshape(-1, 3)
    return pts.min(axis=0), pts.max(axis=0)


# ─────────────────────────── сечение ───────────────────────────
def section_segments(tris: np.ndarray, z: float) -> np.ndarray:
    """Отрезки сечения плоскостью z: (M, 2, 2)."""
    zs = tris[:, :, 2]
    z = z + 1e-7                                       # не попадать точно в вершины
    hit = (zs.min(axis=1) < z) & (zs.max(axis=1) > z)
    t = tris[hit]
    if len(t) == 0:
        return np.zeros((0, 2, 2))
    out = []
    for a, b in ((0, 1), (1, 2), (2, 0)):
        pa, pb = t[:, a], t[:, b]
        za, zb = pa[:, 2], pb[:, 2]
        cross = (za - z) * (zb - z) < 0
        with np.errstate(divide="ignore", invalid="ignore"):
            k = (z - za) / (zb - za)
            p = pa[:, :2] + (pb[:, :2] - pa[:, :2]) * k[:, None]
        out.append((cross, p))
    segs = np.zeros((len(t), 2, 2))
    cnt = np.zeros(len(t), int)
    for cross, p in out:
        for i in np.nonzero(cross)[0]:
            if cnt[i] < 2:
                segs[i, cnt[i]] = p[i]
                cnt[i] += 1
    return segs[cnt == 2]


def chain_loops(segs: np.ndarray, tol: float = 1e-5) -> list[np.ndarray]:
    """Склеить отрезки в контуры (незамкнутые тоже возвращаются — закраска их замкнёт)."""
    if len(segs) == 0:
        return []
    key = lambda p: (round(p[0] / tol), round(p[1] / tol))
    ends: dict = {}
    for i, (a, b) in enumerate(segs):
        ends.setdefault(key(a), []).append((i, 0))
        ends.setdefault(key(b), []).append((i, 1))
    used = np.zeros(len(segs), bool)
    loops = []
    for start in range(len(segs)):
        if used[start]:
            continue
        used[start] = True
        pts = [segs[start, 0], segs[start, 1]]
        cur = key(segs[start, 1])
        while True:
            nxt = None
            for i, e in ends.get(cur, ()):
                if not used[i]:
                    nxt = (i, e)
                    break
            if nxt is None:
                break
            i, e = nxt
            used[i] = True
            other = segs[i, 1 - e]
            pts.append(other)
            cur = key(other)
        if len(pts) >= 3:
            loops.append(np.array(pts))
    return loops


# ─────────────────────────── растр и пути ───────────────────────────
class Raster:
    def __init__(self, lo, hi, res: float):
        self.res = res
        self.x0, self.y0 = lo[0] - 3 * res, lo[1] - 3 * res
        self.w = int(math.ceil((hi[0] - self.x0) / res)) + 4
        self.h = int(math.ceil((hi[1] - self.y0) / res)) + 4

    def to_px(self, pts: np.ndarray) -> np.ndarray:
        return np.column_stack([(pts[:, 0] - self.x0) / self.res - 0.5,
                                (pts[:, 1] - self.y0) / self.res - 0.5])

    def to_mm(self, px: np.ndarray) -> np.ndarray:
        return np.column_stack([self.x0 + (px[:, 0] + 0.5) * self.res,
                                self.y0 + (px[:, 1] + 0.5) * self.res])


def _fill(loops: list[np.ndarray], ras: Raster):
    import cv2
    mask = np.zeros((ras.h, ras.w), np.uint8)
    shift = 4                                          # субпиксельная точность fillPoly
    for lp in loops:
        tmp = np.zeros_like(mask)
        px = np.round(ras.to_px(lp) * (1 << shift)).astype(np.int32)
        cv2.fillPoly(tmp, [px.reshape(-1, 1, 2)], 1, lineType=cv2.LINE_8, shift=shift)
        mask ^= tmp                                    # чёт-нечет: отверстия вычитаются
    return mask


def _contours(region: np.ndarray, ras: Raster, min_len_px: int = 6) -> list[np.ndarray]:
    import cv2
    cs, _ = cv2.findContours(region.astype(np.uint8), cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    out = []
    for c in cs:
        if len(c) < min_len_px:
            continue
        c = cv2.approxPolyDP(c, 0.6, True).reshape(-1, 2).astype(float)
        if len(c) < 3:
            continue
        mm = ras.to_mm(c)
        out.append(np.vstack([mm, mm[:1]]))            # замкнуть
    return out


def _hatch(region: np.ndarray, ras: Raster, pitch: float, angle_deg: float, lo, hi) -> list[np.ndarray]:
    """Линии заполнения под углом, обрезанные по области; змейкой."""
    a = math.radians(angle_deg)
    d = np.array([math.cos(a), math.sin(a)])          # направление линий
    n = np.array([-d[1], d[0]])                       # нормаль — шаг между линиями
    corners = np.array([[lo[0], lo[1]], [hi[0], lo[1]], [lo[0], hi[1]], [hi[0], hi[1]]])
    s_min, s_max = (corners @ n).min(), (corners @ n).max()
    t_min, t_max = (corners @ d).min(), (corners @ d).max()
    step = ras.res * 0.5
    ts = np.arange(t_min, t_max + step, step)
    lines = []
    k = 0
    off = s_min + pitch / 2
    while off <= s_max:
        pts = off * n + ts[:, None] * d
        px = np.round(ras.to_px(pts)).astype(int)
        ok = (px[:, 0] >= 0) & (px[:, 0] < ras.w) & (px[:, 1] >= 0) & (px[:, 1] < ras.h)
        inside = np.zeros(len(ts), bool)
        inside[ok] = region[px[ok, 1], px[ok, 0]] > 0
        # участки подряд идущих «внутри»
        edges = np.diff(np.concatenate([[0], inside.astype(np.int8), [0]]))
        starts, stops = np.nonzero(edges == 1)[0], np.nonzero(edges == -1)[0] - 1
        segs = [(pts[s], pts[e]) for s, e in zip(starts, stops) if e - s >= 2]
        if k % 2:
            segs = [(b, a_) for a_, b in reversed(segs)]
        lines += [np.array(sg) for sg in segs]
        off += pitch
        k += 1
    return lines


def layer_paths(tris: np.ndarray, z_cut: float, line_w: float, perimeters: int, pitch: float,
                angle_deg: float, res: float | None = None) -> list[np.ndarray]:
    """Пути одного слоя: периметры (снаружи внутрь) + заполнение. Каждый путь — (K, 2), мм."""
    import cv2
    loops = chain_loops(section_segments(tris, z_cut))
    if not loops:
        return []
    allp = np.vstack(loops)
    lo, hi = allp.min(axis=0), allp.max(axis=0)
    size = float(max(hi - lo))
    res = res or max(min(0.05, line_w / 8), size / MAX_PIXELS, 0.005)
    ras = Raster(lo, hi, res)
    mask = _fill(loops, ras)
    if not mask.any():
        return []
    dist = cv2.distanceTransform(mask, cv2.DIST_L2, 5) * res
    paths = []
    for k in range(max(0, perimeters)):
        paths += _contours(dist > (k + 0.5) * line_w, ras)
    inner_off = perimeters * line_w if perimeters > 0 else 0.5 * line_w
    if pitch > 0:
        region = dist > inner_off
        if region.any():
            paths += _hatch(region, ras, pitch, angle_deg, lo, hi)
    return paths


class SlicedModel:
    """Загруженная и нарезанная модель (кэш по параметрам)."""

    def __init__(self):
        self.path = ""
        self.mtime = 0.0
        self.raw: np.ndarray | None = None
        self.tris: np.ndarray | None = None
        self.scale = 1.0
        self._cache: dict = {}

    def load(self, path: str):
        self.raw = load_stl(path)
        self.path = path
        self.mtime = os.path.getmtime(path)
        self.scale = None
        self._cache.clear()

    @property
    def loaded(self) -> bool:
        return self.raw is not None

    def size(self, scale: float) -> tuple[float, float, float]:
        lo, hi = bounds(self.raw * scale)
        return tuple(float(v) for v in (hi - lo))

    def layers(self, scale: float, layer_h: float, line_w: float, perimeters: int, pitch: float,
               angles: list) -> list[list[np.ndarray]]:
        key = (scale, layer_h, line_w, perimeters, pitch, tuple(angles))
        if key in self._cache:
            return self._cache[key]
        if self.scale != scale or self.tris is None:
            self.tris = prepare(self.raw, scale)
            self.scale = scale
        _, hi = bounds(self.tris)
        n = max(1, int(math.ceil(hi[2] / layer_h - 1e-6)))
        out = []
        for L in range(n):
            z_cut = min((L + 0.5) * layer_h, hi[2] - 1e-4)
            ang = angles[L % len(angles)] if angles else 0
            out.append(layer_paths(self.tris, z_cut, line_w, perimeters, pitch, ang))
        if len(self._cache) > 8:
            self._cache.clear()
        self._cache[key] = out
        return out
