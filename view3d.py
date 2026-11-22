"""3D-сцена программы: траектория по слоям, стол, позиция сопла.

Рисуется через QPainter + numpy — не требует OpenGL и драйверов видеокарты.
Управление: палец/мышь — вращение; правая кнопка или два пальца — сдвиг;
колесо или щипок — масштаб; кнопки видов — на экране «Печать».
"""
from __future__ import annotations

import math
import time

import numpy as np
from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtCore import Qt

from . import config
from .gcode import Program
from .theme import C, HEAD_COLORS

DONE_REFRESH_S = 1.0         # как часто перерисовывать «напечатано/осталось»

VIEWS = {
    "iso": (-40.0, 32.0),
    "top": (0.0, 90.0),
    "front": (0.0, 4.0),
    "side": (-90.0, 4.0),
}


class View3D(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(500, 500)
        self.setAttribute(Qt.WA_AcceptTouchEvents, True)
        self.grabGesture(Qt.PinchGesture)
        self.program: Program | None = None
        self.max_layer = 0
        self.done_line = -1
        self.show_travel = False
        self.nozzle: tuple[float, float, float] | None = None
        self.yaw, self.el = VIEWS["iso"]
        self.zoom = 1.0
        self.pan = QtCore.QPointF(0, 0)
        self._drag = None
        self._dragging = False
        self._cache: QtGui.QPixmap | None = None
        self._cache_key = None
        self._done_drawn = -1
        self._done_time = 0.0
        self.out_a = np.zeros((0, 3))          # контуры подложки (чашка, планшет, лунки)
        self.out_b = np.zeros((0, 3))
        self.out_hi = np.zeros(0, bool)         # выделенные (выбранные лунки)
        self._out_ver = 0
        self._empty()

    # ─── данные ───
    def _empty(self):
        self.p0 = np.zeros((0, 3))
        self.p1 = np.zeros((0, 3))
        self.ext = np.zeros(0, bool)
        self.tool = np.zeros(0, int)
        self.line = np.zeros(0, int)
        self.layer = np.zeros(0, int)
        self.center = np.array([0.0, 0.0, 0.0])
        self.radius = config.BED_DIAMETER_MM / 2

    def set_program(self, p: Program | None):
        self.program = p
        if not p or not p.segments:
            self._empty()
            self._fit()
            self.update()
            return
        segs = p.segments
        self.p0 = np.array([(s.x0, s.y0, s.z0) for s in segs], float)
        self.p1 = np.array([(s.x1, s.y1, s.z1) for s in segs], float)
        self.ext = np.array([s.extrude for s in segs], bool)
        self.tool = np.array([s.tool for s in segs], int)
        self.line = np.array([s.line_no for s in segs], int)
        layers = np.array(p.layers if p.layers else [0.0])
        idx = np.searchsorted(layers, self.p1[:, 2] - 1e-6)
        self.layer = np.clip(idx, 0, len(layers) - 1)
        self._fit()
        self.max_layer = len(p.layers) - 1 if p.layers else 0
        self.reset_view(keep_angles=True)

    def set_outline(self, segs: list, highlight: list | None = None):
        """Контуры подложки: список отрезков ((x0, y0, z0), (x1, y1, z1)); highlight — флаги отрезков."""
        if segs:
            arr = np.array(segs, float)
            self.out_a, self.out_b = arr[:, 0, :], arr[:, 1, :]
            self.out_hi = np.array(highlight if highlight is not None else [False] * len(segs), bool)
        else:
            self.out_a = self.out_b = np.zeros((0, 3))
            self.out_hi = np.zeros(0, bool)
        self._out_ver += 1
        self._fit()
        self.update()

    def _fit(self):
        """Центр и масштаб сцены — по траектории и контурам подложки вместе."""
        parts = []
        if len(self.p0):
            parts.append(np.vstack([self.p0[self.ext], self.p1[self.ext]]) if self.ext.any()
                         else np.vstack([self.p0, self.p1]))
        if len(self.out_a):
            parts.append(np.vstack([self.out_a, self.out_b]))
        if not parts:
            return
        pts = np.vstack(parts)
        lo, hi = pts.min(axis=0), pts.max(axis=0)
        self.center = (lo + hi) / 2
        self.radius = max(float(np.linalg.norm(hi - lo)) / 2, 3.0)

    def set_max_layer(self, i: int):
        self.max_layer = i
        self.update()

    def set_nozzle(self, x: float, y: float, z: float):
        self.nozzle = (x, y, z)
        self.update()

    # ─── камера ───
    def set_view(self, name: str):
        self.yaw, self.el = VIEWS[name]
        self.update()

    def reset_view(self, keep_angles: bool = False):
        if not keep_angles:
            self.yaw, self.el = VIEWS["iso"]
        self.zoom = 1.0
        self.pan = QtCore.QPointF(0, 0)
        self.update()

    def zoom_by(self, k: float):
        self.zoom = max(0.2, min(40.0, self.zoom * k))
        self.update()

    def _project(self, pts: np.ndarray):
        """Мировые точки (N,3) → экранные (N,2)."""
        q = pts - self.center
        a = math.radians(self.yaw)
        e = math.radians(self.el)
        ca, sa = math.cos(a), math.sin(a)
        x1 = q[:, 0] * ca - q[:, 1] * sa
        y1 = q[:, 0] * sa + q[:, 1] * ca
        z1 = q[:, 2]
        u = x1
        v = z1 * math.cos(e) + y1 * math.sin(e)
        d = y1 * math.cos(e) - z1 * math.sin(e)
        D = self.radius * 6
        k = D / np.maximum(D + d, D * 0.2)
        s = self.zoom * min(self.width(), self.height()) / (2.3 * self.radius)
        cx = self.width() / 2 + self.pan.x()
        cy = self.height() / 2 + self.pan.y()
        return np.column_stack([cx + s * k * u, cy - s * k * v])

    # ─── рисование ───
    @staticmethod
    def _lines(a: np.ndarray, b: np.ndarray):
        return [QtCore.QLineF(x0, y0, x1, y1) for x0, y0, x1, y1 in np.hstack([a, b]).tolist()]

    def theme_changed(self):
        self._cache_key = None

    def paintEvent(self, _):
        # Траектория рисуется в кэш-картинку и перерисовывается только при смене вида,
        # слоя или (не чаще раза в секунду) прогресса; маркер сопла — поверх, каждый кадр.
        # Отрисовка держит интерпретатор Python — без кэша она отнимала бы время у потока связи.
        now = time.monotonic()
        if self.done_line != self._done_drawn and (now - self._done_time >= DONE_REFRESH_S or self.done_line < 0):
            self._done_drawn = self.done_line
            self._done_time = now
        key = (self.width(), self.height(), self.yaw, self.el, self.zoom, self.pan.x(), self.pan.y(),
               self.max_layer, self._done_drawn, self.show_travel, id(self.program), self._dragging,
               self._out_ver)
        if key != self._cache_key or self._cache is None:
            self._cache = QtGui.QPixmap(self.size())
            cp = QtGui.QPainter(self._cache)
            self._render_scene(cp, self._done_drawn)
            cp.end()
            self._cache_key = key
        p = QtGui.QPainter(self)
        p.drawPixmap(0, 0, self._cache)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        if self.nozzle and len(self.p0):
            n = np.array([self.nozzle, (self.nozzle[0], self.nozzle[1], 0.0)])
            s = self._project(n)
            p.setPen(QtGui.QPen(QtGui.QColor(C["danger"]), 1.5, Qt.DashLine))
            p.drawLine(QtCore.QPointF(*s[0]), QtCore.QPointF(*s[1]))
            p.setPen(QtGui.QPen(QtGui.QColor(C["danger"]), 3))
            mb = QtGui.QColor(C["marker_bg"])
            mb.setAlpha(220)
            p.setBrush(mb)
            c = QtCore.QPointF(*s[0])
            p.drawEllipse(c, 9, 9)
            p.drawLine(c + QtCore.QPointF(-15, 0), c + QtCore.QPointF(15, 0))
            p.drawLine(c + QtCore.QPointF(0, -15), c + QtCore.QPointF(0, 15))

    def _render_scene(self, p: QtGui.QPainter, done_line: int):
        p.setRenderHint(QtGui.QPainter.Antialiasing, not self._dragging)
        p.fillRect(self.rect(), QtGui.QColor(C["surface"]))
        self._draw_bed(p)
        if len(self.out_a):
            oa, ob = self._project(self.out_a), self._project(self.out_b)
            for hi, alpha, w in ((False, 110, 1.2), (True, 200, 1.8)):
                m = self.out_hi == hi
                if m.any():
                    col = QtGui.QColor(C["accent"])
                    col.setAlpha(alpha)
                    p.setPen(QtGui.QPen(col, w, Qt.SolidLine, Qt.FlatCap))
                    p.drawLines(self._lines(oa[m], ob[m]))

        if len(self.p0) == 0:
            p.setPen(QtGui.QColor(C["muted"]))
            f = p.font()
            f.setPixelSize(22)
            p.setFont(f)
            p.drawText(self.rect(), Qt.AlignCenter, "Программа не загружена")
            self._draw_axes(p)
            return

        vis = self.layer <= self.max_layer
        stride = 1
        if self._dragging and vis.sum() > 40000:
            stride = int(vis.sum() // 40000) + 1
        a2 = self._project(self.p0)
        b2 = self._project(self.p1)

        if self.show_travel:
            m = vis & ~self.ext
            p.setPen(QtGui.QPen(QtGui.QColor(C["muted"]), 1, Qt.DashLine))
            p.drawLines(self._lines(a2[m][::stride], b2[m][::stride]))

        top = self.layer == self.max_layer
        pending = self.line > done_line if done_line >= 0 else np.zeros(len(self.line), bool)
        for t in sorted(set(self.tool.tolist())):
            base = QtGui.QColor(HEAD_COLORS[t % len(HEAD_COLORS)])
            groups = [
                (vis & self.ext & (self.tool == t) & ~top & ~pending, 150, 1.6),
                (vis & self.ext & (self.tool == t) & pending, 55, 1.6),
                (vis & self.ext & (self.tool == t) & top & ~pending, 255, 2.4),
            ]
            for m, alpha, w in groups:
                if not m.any():
                    continue
                col = QtGui.QColor(base)
                col.setAlpha(alpha)
                # FlatCap: круглые концы (RoundCap) рисуются в ~20 раз медленнее
                p.setPen(QtGui.QPen(col, w, Qt.SolidLine, Qt.FlatCap))
                p.drawLines(self._lines(a2[m][::stride], b2[m][::stride]))
        self._draw_axes(p)

    def _draw_bed(self, p: QtGui.QPainter):
        R = config.BED_DIAMETER_MM / 2
        ang = np.linspace(0, 2 * math.pi, 97)
        ring = np.column_stack([R * np.cos(ang), R * np.sin(ang), np.zeros_like(ang)])
        pts = self._project(ring)
        poly = QtGui.QPolygonF([QtCore.QPointF(x, y) for x, y in pts.tolist()])
        p.setPen(QtGui.QPen(QtGui.QColor(C["border"]), 2))
        p.setBrush(QtGui.QColor(C["surface2"]))
        p.drawPolygon(poly)
        # сетка 10 мм внутри круга
        step = 10.0 if self.radius > 12 else 2.0
        a, b = [], []
        g = -R + (R % step)
        while g <= R:
            h = math.sqrt(max(R * R - g * g, 0))
            a += [(g, -h, 0), (-h, g, 0)]
            b += [(g, h, 0), (h, g, 0)]
            g += step
        if a:
            p.setPen(QtGui.QPen(QtGui.QColor(C["grid3d"]), 1))
            p.drawLines(self._lines(self._project(np.array(a)), self._project(np.array(b))))

    def _draw_axes(self, p: QtGui.QPainter):
        a = math.radians(self.yaw)
        e = math.radians(self.el)
        o = QtCore.QPointF(52, self.height() - 52)
        f = p.font()
        f.setPixelSize(15)
        f.setBold(True)
        p.setFont(f)
        for vec, col, name in (((1, 0, 0), "#E0312B", "X"), ((0, 1, 0), "#159A67", "Y"), ((0, 0, 1), C["accent"], "Z")):
            x, y, z = vec
            x1 = x * math.cos(a) - y * math.sin(a)
            y1 = x * math.sin(a) + y * math.cos(a)
            u, v = x1, z * math.cos(e) + y1 * math.sin(e)
            end = o + QtCore.QPointF(u * 34, -v * 34)
            p.setPen(QtGui.QPen(QtGui.QColor(col), 3, Qt.SolidLine, Qt.FlatCap))
            p.drawLine(o, end)
            p.drawText(end + QtCore.QPointF(u * 10 - 5, -v * 10 + 5), name)

    # ─── управление ───
    def mousePressEvent(self, e):
        self._drag = (e.pos(), e.button())

    def mouseMoveEvent(self, e):
        if not self._drag:
            return
        start, btn = self._drag
        d = e.pos() - start
        self._drag = (e.pos(), btn)
        self._dragging = True
        if btn in (Qt.RightButton, Qt.MiddleButton):
            self.pan += QtCore.QPointF(d)
        else:
            self.yaw += d.x() * 0.45
            self.el = max(-5.0, min(90.0, self.el + d.y() * 0.35))
        self.update()

    def mouseReleaseEvent(self, e):
        self._drag = None
        if self._dragging:
            self._dragging = False
            self.update()

    def wheelEvent(self, e):
        self.zoom_by(1.12 ** (e.angleDelta().y() / 120))

    def event(self, e):
        if e.type() == QtCore.QEvent.Gesture:
            g = e.gesture(Qt.PinchGesture)
            if g is not None:
                self.zoom_by(g.scaleFactor())
                c = g.centerPoint() - g.lastCenterPoint()
                self.pan += c
                return True
        return super().event(e)
