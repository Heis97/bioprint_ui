"""Превью программы: вид сверху по слоям, текущая позиция сопла."""
from __future__ import annotations

from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtCore import Qt

from . import config
from .gcode import Program
from .theme import C, HEAD_COLORS


class TopView(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.program: Program | None = None
        self.layer = 0
        self.show_all_layers = True
        self.nozzle: tuple[float, float] | None = None
        self.done_line = -1          # строки до этой уже выполнены (подсветка)
        self.fit_model = True        # масштаб по модели (иначе — весь стол)
        self.setMinimumSize(500, 500)

    def set_program(self, p: Program | None):
        self.program = p
        self.layer = len(p.layers) - 1 if p and p.layers else 0
        self.update()

    def set_layer(self, i: int):
        self.layer = i
        self.update()

    def set_nozzle(self, x: float, y: float):
        self.nozzle = (x, y)
        self.update()

    def _transform(self):
        r = self.rect().adjusted(30, 30, -30, -30)
        half = config.BED_DIAMETER_MM / 2
        minx, miny, maxx, maxy = -half, -half, half, half
        if self.program and self.program.segments:
            bx0, by0, bx1, by1 = self.program.bounds
            outside = bx0 < minx or by0 < miny or bx1 > maxx or by1 > maxy
            if self.fit_model or outside:
                span = max(bx1 - bx0, by1 - by0, 10.0)
                pad = span * 0.15 + 2
                mx, my = (bx0 + bx1) / 2, (by0 + by1) / 2
                minx, maxx = mx - span / 2 - pad, mx + span / 2 + pad
                miny, maxy = my - span / 2 - pad, my + span / 2 + pad
        w, h = maxx - minx, maxy - miny
        s = min(r.width() / w, r.height() / h)
        cx = r.center().x() - (minx + w / 2) * s
        cy = r.center().y() + (miny + h / 2) * s
        return s, cx, cy

    def paintEvent(self, _):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.fillRect(self.rect(), QtGui.QColor(C["surface"]))
        s, cx, cy = self._transform()

        def pt(x, y):
            return QtCore.QPointF(cx + x * s, cy - y * s)

        # стол и сетка 10 мм
        half = config.BED_DIAMETER_MM / 2
        p.setPen(QtGui.QPen(QtGui.QColor(C["grid"]), 1))
        step = 1 if s > 25 else (5 if s > 8 else 10)
        g = -half
        while g <= half + 0.01:
            p.drawLine(pt(g, -half), pt(g, half))
            p.drawLine(pt(-half, g), pt(half, g))
            g += step
        p.setPen(QtGui.QPen(QtGui.QColor(C["border"]), 2))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(pt(0, 0), half * s, half * s)
        p.setPen(QtGui.QPen(QtGui.QColor(C["guide"]), 1, Qt.DashLine))
        p.drawLine(pt(-6, 0), pt(6, 0))
        p.drawLine(pt(0, -6), pt(0, 6))

        prog = self.program
        if prog and prog.segments and prog.layers:
            zcur = prog.layers[min(self.layer, len(prog.layers) - 1)]
            lw = min(8.0, max(1.5, 0.35 * s))
            for seg in prog.segments:
                below = seg.z1 < zcur - 1e-6
                on = abs(seg.z1 - zcur) < 1e-6
                if not (on or (below and self.show_all_layers)):
                    continue
                if not seg.extrude:
                    if on:
                        pen = QtGui.QPen(QtGui.QColor(C["travel"]), 1, Qt.DashLine)
                        p.setPen(pen)
                        p.drawLine(pt(seg.x0, seg.y0), pt(seg.x1, seg.y1))
                    continue
                col = QtGui.QColor(HEAD_COLORS[seg.tool % len(HEAD_COLORS)])
                if below:
                    col.setAlpha(45)
                elif self.done_line >= 0 and seg.line_no > self.done_line:
                    col.setAlpha(110)
                pen = QtGui.QPen(col, lw, Qt.SolidLine, Qt.FlatCap)   # RoundCap в ~20 раз медленнее
                p.setPen(pen)
                p.drawLine(pt(seg.x0, seg.y0), pt(seg.x1, seg.y1))
        else:
            p.setPen(QtGui.QColor(C["muted"]))
            f = p.font()
            f.setPixelSize(22)
            p.setFont(f)
            p.drawText(self.rect(), Qt.AlignCenter, "Программа не загружена")

        if self.nozzle:
            c = pt(*self.nozzle)
            p.setPen(QtGui.QPen(QtGui.QColor(C["danger"]), 3))
            mb = QtGui.QColor(C["marker_bg"])
            mb.setAlpha(200)
            p.setBrush(mb)
            p.drawEllipse(c, 10, 10)
            p.drawLine(c + QtCore.QPointF(-16, 0), c + QtCore.QPointF(16, 0))
            p.drawLine(c + QtCore.QPointF(0, -16), c + QtCore.QPointF(0, 16))

        # масштабная линейка 10 мм
        p.setPen(QtGui.QPen(QtGui.QColor(C["muted"]), 2))
        x0, y0 = 24, self.height() - 24
        p.drawLine(x0, y0, int(x0 + 10 * s), y0)
        f = p.font()
        f.setPixelSize(15)
        p.setFont(f)
        p.drawText(x0, y0 - 8, "10 мм")
