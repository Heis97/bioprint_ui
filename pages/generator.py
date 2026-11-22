"""Генератор программ: решётки, цилиндр, диск, STL-модель; чашка Петри или лунки планшета; головки."""
from __future__ import annotations

import os

from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtCore import Qt

from .. import config, gcode, gen, stl
from ..context import Ctx
from ..theme import C, HEAD_COLORS
from ..view3d import View3D
from ..widgets import Card, ElidedLabel, Segmented, Stepper, Tile, ToggleButton, ValueLabel, button, label
from .components import page_scroll

GEN_PANEL_W = 800          # ширина левой панели параметров (было 660 — кнопки наезжали)


class WellPicker(QtWidgets.QWidget):
    """Схема подложки: касание лунки — выбрать/снять."""

    changed = QtCore.pyqtSignal()

    def __init__(self):
        super().__init__()
        self.sub = "petri60"
        self.selected: set[str] = set()
        self.setMinimumHeight(230)
        self._rects: dict[str, QtCore.QRectF] = {}
        self.paint_tools = False                 # режим «По лункам»: касание назначает головку
        self.brush = 1
        self.well_tools: dict[str, int] = {}

    def _tool_color(self, name: str) -> str:
        t = self.well_tools.get(name, self.brush)
        return HEAD_COLORS[(t - 1) % len(HEAD_COLORS)] if t else C["accent"]

    def set_substrate(self, sub: str):
        self.sub = sub
        self.selected = set(gen.well_names(sub))
        self.update()
        self.changed.emit()

    def set_all(self, on: bool):
        self.selected = set(gen.well_names(self.sub)) if on else set()
        self.update()
        self.changed.emit()

    def wells(self) -> list[str]:
        return [w for w in gen.well_names(self.sub) if w in self.selected]

    def paintEvent(self, _):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        _, rows, cols, pitch, well_d = gen.SUBSTRATES[self.sub]
        r = self.rect().adjusted(8, 8, -8, -8)
        self._rects.clear()
        if rows * cols == 1:
            d = min(r.width(), r.height())
            c = QtCore.QRectF(r.center().x() - d / 2, r.center().y() - d / 2, d, d)
            self._rects["центр"] = c
            col = self._tool_color("центр") if self.paint_tools else C["accent"]
            p.setPen(QtGui.QPen(QtGui.QColor(col), 3))
            p.setBrush(QtGui.QColor(C["accent_soft"]))
            p.drawEllipse(c)
            p.setPen(QtGui.QColor(C["strong"]))
            f = p.font()
            f.setPixelSize(18)
            p.setFont(f)
            extra = f"\nголовка {self.well_tools.get('центр', self.brush)}" if self.paint_tools else ""
            p.drawText(c, Qt.AlignCenter, f"{gen.SUBSTRATES[self.sub][0]}\nпечать в центре{extra}")
            return
        # планшет 127,8 × 85,5 мм
        W, H = 127.8, 85.5
        s = min(r.width() / W, r.height() / H)
        ox = r.center().x() - W * s / 2
        oy = r.center().y() - H * s / 2
        p.setPen(QtGui.QPen(QtGui.QColor(C["border"]), 2))
        p.setBrush(QtGui.QColor(C["surface2"]))
        p.drawRoundedRect(QtCore.QRectF(ox, oy, W * s, H * s), 10, 10)
        f = p.font()
        f.setPixelSize(max(10, min(16, int(pitch * s * 0.3))))
        f.setBold(True)
        p.setFont(f)
        for name, (x, y) in gen.well_centers(self.sub).items():
            cx = ox + (W / 2 + x) * s
            cy = oy + (H / 2 - y) * s
            d = well_d * s
            rect = QtCore.QRectF(cx - d / 2, cy - d / 2, d, d)
            self._rects[name] = rect
            on = name in self.selected
            fill = (self._tool_color(name) if self.paint_tools else C["accent"]) if on else C["surface"]
            p.setPen(QtGui.QPen(QtGui.QColor(fill if on else C["border"]), 2))
            p.setBrush(QtGui.QColor(fill))
            p.drawEllipse(rect)
            if d > 26:
                p.setPen(QtGui.QColor("#FFFFFF" if on else C["muted"]))
                text = f"{name}\n{self.well_tools.get(name, self.brush)}" if (on and self.paint_tools and d > 40) else name
                p.drawText(rect, Qt.AlignCenter, text)

    def mouseReleaseEvent(self, e):
        for name, rect in self._rects.items():
            if rect.adjusted(-4, -4, 4, 4).contains(e.pos()):
                if self.paint_tools:
                    # По лункам: касание — назначить выбранную головку; той же головкой — снять лунку
                    if name in self.selected and self.well_tools.get(name) == self.brush:
                        if len(gen.well_names(self.sub)) > 1:
                            self.selected.discard(name)
                    else:
                        self.selected.add(name)
                        self.well_tools[name] = self.brush
                elif len(gen.well_names(self.sub)) > 1:
                    self.selected ^= {name}
                self.update()
                self.changed.emit()
                return


class GeneratorPage(QtWidgets.QWidget):
    title = "Генератор"

    def __init__(self, ctx: Ctx):
        super().__init__()
        self.ctx = ctx
        self.p = gen.GenParams()
        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(28, 22, 28, 22)
        root.setSpacing(20)

        # ── параметры (прокрутка) ──
        inner = QtWidgets.QWidget()
        col = QtWidgets.QVBoxLayout(inner)
        col.setContentsMargins(0, 0, 8, 0)
        col.setSpacing(18)

        shape = Card("Фигура")
        self.shape_seg = Segmented([(v, k) for k, v in gen.SHAPES.items()], self.p.shape)
        self.shape_seg.changed.connect(self._set_shape)
        shape.add(self.shape_seg)
        col.addWidget(shape)

        sub = Card("Подложка", "Ноль программы — центр подложки на её поверхности (кнопка «Установить 0 здесь»).")
        self.sub_combo = QtWidgets.QComboBox()
        for k, v in gen.SUBSTRATES.items():
            self.sub_combo.addItem(v[0], k)
        self.sub_combo.setCurrentIndex(list(gen.SUBSTRATES).index(self.p.substrate))
        self.sub_combo.currentIndexChanged.connect(self._set_sub)
        sub.add(self.sub_combo)
        self.picker = WellPicker()
        self.picker.changed.connect(self._wells_changed)
        sub.add(self.picker)
        wr = QtWidgets.QHBoxLayout()
        self.b_all = button("Все лунки", "small", lambda: self.picker.set_all(True))
        self.b_none = button("Снять выбор", "small", lambda: self.picker.set_all(False))
        wr.addWidget(self.b_all)
        wr.addWidget(self.b_none)
        wr.addStretch(1)
        sub.add(wr)
        col.addWidget(sub)

        # ── STL ──
        self.model = stl.SlicedModel()
        self.stl_card = Card("STL-модель")
        sr = QtWidgets.QHBoxLayout()
        sr.addWidget(button("Открыть STL…", "primary", self._open_stl))
        self.stl_name = ElidedLabel("Файл не выбран", "MidValue")
        sr.addWidget(self.stl_name, 1)
        self.stl_card.add(sr)
        self.stl_size = ValueLabel("", "Размер: 000.0 × 000.0 × 000.0 мм · слоёв 0000", "Muted")
        self.stl_card.add(self.stl_size)
        self.form_stl = QtWidgets.QGridLayout()
        self.form_stl.setHorizontalSpacing(16)
        self.form_stl.setVerticalSpacing(10)
        self.stl_card.add(self.form_stl)
        col.addWidget(self.stl_card)

        # ── головки ──
        tc = Card("Головки")
        self.tool_mode = Segmented([(v, k) for k, v in gen.TOOL_MODES.items()], self.p.tool_mode)
        self.tool_mode.changed.connect(self._set_tool_mode)
        tc.add(self.tool_mode)
        tr = QtWidgets.QHBoxLayout()
        self.tool_lbl = label("Головка", "Muted")
        tr.addWidget(self.tool_lbl)
        self.tool_seg = Segmented([(f"Головка {i + 1}", i + 1) for i in range(len(config.HEADS))], self.p.tool)
        self.tool_seg.changed.connect(self._set_tool)
        tr.addWidget(self.tool_seg, 1)
        tc.add(tr)
        self.layer_rows_box = QtWidgets.QVBoxLayout()
        self.layer_rows_box.setSpacing(8)
        tc.add(self.layer_rows_box)
        self.b_add_rule = button("+ Добавить", "small", self._add_layer_rule)
        tc.add(self.b_add_rule)
        col.addWidget(tc)

        par = Card("Параметры")
        self.form = QtWidgets.QGridLayout()
        self.form.setHorizontalSpacing(16)
        self.form.setVerticalSpacing(10)
        self.rows = {}
        self._add("width", "Ширина (X)", 1, 80, 0.5, " мм", 1)
        self._add("length", "Длина (Y)", 1, 80, 0.5, " мм", 1)
        self._add("diameter", "Диаметр", 1, 80, 0.5, " мм", 1)
        self._add("pitch", "Шаг филаментов", 0.3, 10, 0.05, " мм", 2)
        self._add("walls", "Стенок", 1, 20, 1, "", 0)
        self._add("layers", "Слоёв", 1, 200, 1, "", 0)
        self._add("layer_h", "Высота слоя", 0.05, 2, 0.05, " мм", 2)
        self._add("speed", "Скорость печати", 0.5, 30, 0.5, " мм/с", 1)
        self._add("travel_speed", "Скорость переходов", 1, 50, 1, " мм/с", 0)
        self._add("safe_z", "Безопасная высота", 5, 100, 1, " мм", 0)
        par.add(self.form)
        # масштаб — один множитель на все оси (X, Y и Z вместе); задаётся в % или наибольшим размером
        self._add("stl_scale", "Масштаб (все оси)", 5, 2000, 5, " %", 0, self.form_stl)
        r = self.form_stl.rowCount()
        self.st_max = Stepper(10.0, 0.5, 300, 0.5, " мм", 1)
        self.st_max.valueChanged.connect(self._set_stl_max)
        self.form_stl.addWidget(label("Наибольший размер", "Muted"), r, 0)
        self.form_stl.addWidget(self.st_max, r, 1)
        qs = QtWidgets.QHBoxLayout()
        for pct in (50, 100, 200):
            qs.addWidget(button(f"{pct} %", "small", lambda pct=pct: self._set_stl_scale(pct)))
        qs.addWidget(button("Вписать в лунку", "small", self._fit_stl,
                            "Масштаб, при котором модель по X/Y занимает 90 % диаметра лунки"))
        self.form_stl.addLayout(qs, r + 1, 0, 1, 2)
        self._add("perimeters", "Периметров", 0, 10, 1, "", 0, self.form_stl)

        # подача: E считается из сечения филамента и диаметра шприца
        par.add(label("Подача материала", "CardTitle"))
        pre = QtWidgets.QHBoxLayout()
        pre.addWidget(label("Шприц", "Muted"))
        self.syr_seg = Segmented([(f"{v} мл", v) for v in config.SYRINGE_DIAMETERS], None)
        self.syr_seg.changed.connect(self._syringe_preset)
        pre.addWidget(self.syr_seg, 1)
        par.add(pre)
        self.form2 = QtWidgets.QGridLayout()
        self.form2.setHorizontalSpacing(16)
        self.form2.setVerticalSpacing(10)
        par.add(self.form2)
        self._add("syringe_d", "Внутр. Ø шприца", 2, 40, 0.05, " мм", 2, self.form2)
        self._add("nozzle_d", "Ø сопла", 0.1, 3, 0.05, " мм", 2, self.form2)
        self._add("flow", "Коэффициент подачи", 10, 300, 5, " %", 0, self.form2)
        self.e_info = ValueLabel("", "E на 1 мм пути: 0.000000 (ход поршня)", "Muted")
        self.e_info2 = ValueLabel("", "Сечение филамента 0.000 мм² · объём ≈ 00000 мкл", "Muted")
        par.add(self.e_info)
        par.add(self.e_info2)
        self.ang_row = QtWidgets.QHBoxLayout()
        self.ang_lbl = label("Углы слоёв", "Muted")
        self.ang_seg = Segmented([(k, k) for k in gen.ANGLE_SETS], self.p.angles)
        self.ang_seg.changed.connect(lambda v: self._set("angles", v))
        self.ang_row.addWidget(self.ang_lbl)
        self.ang_row.addWidget(self.ang_seg, 1)
        par.add(self.ang_row)
        tg = QtWidgets.QHBoxLayout()
        self.t_contour = ToggleButton("Контур", lambda v: self._set("contour", v))
        self.t_contour.set_silent(self.p.contour)
        self.t_cont = ToggleButton("Одним филаментом", lambda v: self._set("continuous", v), "да", "нет")
        self.t_cont.set_silent(self.p.continuous)
        tg.addWidget(self.t_contour)
        tg.addWidget(self.t_cont)
        par.add(tg)
        col.addWidget(par)

        col.addStretch(1)
        sa = page_scroll(inner)
        # ширина по самым широким рядам кнопок («Фигура», «Головки») + полоса прокрутки;
        # горизонтальной прокрутки нет — содержимое не должно уезжать под край
        sa.setFixedWidth(GEN_PANEL_W)
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # прокрутка пальцем на сенсорном экране (только касанием — мышь и кнопки не затрагивает)
        QtWidgets.QScroller.grabGesture(sa.viewport(), QtWidgets.QScroller.TouchGesture)
        root.addWidget(sa)

        # ── превью ──
        pv = Card("Предпросмотр")
        self.view = View3D()
        vt = QtWidgets.QHBoxLayout()
        vt.setSpacing(8)
        for text, fn in (("Изо", lambda: self.view.set_view("iso")), ("Сверху", lambda: self.view.set_view("top")),
                         ("Спереди", lambda: self.view.set_view("front")),
                         ("−", lambda: self.view.zoom_by(1 / 1.25)), ("+", lambda: self.view.zoom_by(1.25)),
                         ("Сброс", lambda: self.view.reset_view())):
            vt.addWidget(button(text, "small", fn))
        vt.addStretch(1)
        pv.add(vt)
        pv.add(self.view, 1)
        tiles = QtWidgets.QHBoxLayout()
        self.t_cmds = Tile("Команд", "—", reserve="000000")
        self.t_len = Tile("Длина филамента", "—", reserve="000000 мм")
        self.t_time = Tile("Время ≈", "—", reserve=("00 ч 00 мин", "00 мин 00 с"))
        self.t_e = Tile("Ход поршня", "—", reserve="0000.00 мм")
        tiles.setSpacing(0)
        for t in (self.t_cmds, self.t_len, self.t_time, self.t_e):
            # меньше отступ справа (32 → 16): иначе ряд показателей шире места рядом с панелью
            # параметров, и страница уезжала вправо с горизонтальной прокруткой
            t.layout().setContentsMargins(0, 0, 16, 0)
            tiles.addWidget(t)
        tiles.addStretch(1)
        pv.add(tiles)
        self.warn = ElidedLabel("", "Muted")
        pv.add(self.warn)
        br = QtWidgets.QHBoxLayout()
        br.addWidget(button("▶  На печать", "primary", self._to_print))
        self.b_save = button("Сохранить G-код…", None, self._save)
        br.addWidget(self.b_save)
        pv.add(br)
        root.addWidget(pv, 1)

        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._regen)
        self.picker.set_substrate(self.p.substrate)
        self._set_shape(self.p.shape)
        self._set("syringe_d", self.p.syringe_d)
        self._set_tool_mode(self.p.tool_mode)

    # --- форма ---
    def _add(self, key, text, lo, hi, step, suffix, dec, form=None):
        form = form or self.form
        r = form.rowCount()
        lbl = label(text, "Muted")
        st = Stepper(getattr(self.p, key), lo, hi, step, suffix, dec)
        st.valueChanged.connect(lambda v, k=key: self._set(k, int(v) if k in ("layers", "walls", "perimeters") else v))
        form.addWidget(lbl, r, 0)
        form.addWidget(st, r, 1)
        self.rows[key] = (lbl, st)

    def _set(self, key, v):
        setattr(self.p, key, v)
        if key == "stl_scale":
            self._sync_stl_max()
        if key == "syringe_d":
            match = [ml for ml, d in config.SYRINGE_DIAMETERS.items() if abs(d - v) < 1e-6]
            self.syr_seg.set_value(match[0] if match else None)
        self._e_text()
        self._timer.start()

    def _syringe_preset(self, ml):
        d = config.SYRINGE_DIAMETERS[ml]
        self.rows["syringe_d"][1].set_value(d)
        self._set("syringe_d", d)

    def _e_text(self):
        vol = self.p.flow / 100.0 * self.p.strand_area * getattr(self, "_extrude_len", 0.0)   # мм³ = мкл
        self.e_info.setText(f"E на 1 мм пути: {self.p.e_per_mm:.6f} (ход поршня)")
        self.e_info2.setText(f"Сечение филамента {self.p.strand_area:.3f} мм² · объём ≈ {vol:.0f} мкл · "
                             f"расход при печати {self.p.rate_ml_min:.3f} мл/мин")

    def _set_shape(self, shape):
        self.p.shape = shape
        is_stl = shape == "stl"
        vis = {
            "width": shape == "grid_rect", "length": shape == "grid_rect",
            "diameter": shape not in ("grid_rect", "stl"), "walls": shape == "cylinder",
            "layers": not is_stl,
        }
        self.stl_card.setVisible(is_stl)
        simple = getattr(self, "simple", False)
        advanced = {"travel_speed", "flow"}
        for k, (lbl, st) in self.rows.items():
            show = vis.get(k, True) and not (simple and k in advanced)
            lbl.setVisible(show)
            st.setVisible(show)
        grid = shape in ("grid_rect", "grid_circle")
        self.ang_lbl.setVisible((grid or is_stl) and not simple)
        self.ang_seg.setVisible((grid or is_stl) and not simple)
        self.t_cont.setVisible(grid and not simple)
        self.t_contour.setVisible(shape not in ("cylinder", "stl"))
        self.rows["pitch"][0].setText({"cylinder": "Шаг стенок", "stl": "Шаг заполнения"}.get(shape, "Шаг филаментов"))
        self._timer.start()

    def _set_sub(self, i):
        self.p.substrate = self.sub_combo.itemData(i)
        self.picker.set_substrate(self.p.substrate)
        plate = gen.SUBSTRATES[self.p.substrate][1] * gen.SUBSTRATES[self.p.substrate][2] > 1
        self.b_all.setVisible(plate)
        self.b_none.setVisible(plate)

    def _wells_changed(self):
        self.p.wells = self.picker.wells()
        self.p.well_tools = dict(self.picker.well_tools)
        self._timer.start()

    # --- генерация ---
    def _regen(self):
        segs, hi = gen.outline_segments(self.p.substrate, self.picker.wells())
        self.view.set_outline(segs, hi)
        self._stl_info()
        if not self.picker.wells():
            self.warn.set_text("Выберите хотя бы одну лунку")
            self.view.set_program(None)
            self.prog = None
            return
        if self.p.shape == "stl" and not self.model.loaded:
            self.warn.set_text("Откройте STL-файл")
            self.view.set_program(None)
            self.prog = None
            return
        self.p.well_tools = dict(self.picker.well_tools)
        QtWidgets.QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.text = gen.generate(self.p, self.model)
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        self.prog = gcode.parse(self.text)
        self.view.set_program(self.prog)
        p = self.prog
        self.t_cmds.set(str(p.conversion.moves))
        self.t_len.set(f"{p.extrude_len:.0f} мм")
        self.t_time.set(gcode.fmt_duration(p.est_seconds))
        e_total = self.p.e_per_mm * p.extrude_len
        self._extrude_len = p.extrude_len
        self.t_e.set(f"{e_total:.2f} мм")
        self._e_text()
        w = gen.fit_warning(self.p, self.model)
        n = self.p.n_layers(self.model)
        tools = ", ".join(f"T{t}" for t in dict.fromkeys(p.conversion.tools)) or "текущая головка"
        self.warn.set_text(("⚠ " + w) if w else f"Слоёв {n}, высота {n * self.p.layer_h:.2f} мм, "
                           f"лунок {len(self.picker.wells())}, головки: {tools}")

    def _title(self) -> str:
        if self.p.shape == "stl":
            return f"{os.path.basename(self.model.path)}, {self.p.stl_scale:g} %, {self.p.n_layers(self.model)} сл."
        name = gen.SHAPES[self.p.shape].replace(" ▭", " прямоуг.").replace(" ○", " круглая")
        size = f"{self.p.width:g}×{self.p.length:g}" if self.p.shape == "grid_rect" else f"Ø{self.p.diameter:g}"
        return f"{name} {size} мм, {self.p.layers} сл."

    # --- STL ---
    def _open_stl(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Открыть STL", self.ctx.settings.last_dir, "STL (*.stl);;Все файлы (*)")
        if not path:
            return
        try:
            self.model.load(path)
        except (OSError, ValueError) as e:
            QtWidgets.QMessageBox.critical(self, "STL", f"Не удалось открыть модель:\n{e}")
            return
        self.ctx.settings.last_dir = os.path.dirname(path)
        self.ctx.settings.save()
        self.stl_name.set_text(os.path.basename(path))
        self._sync_stl_max()
        self._timer.start()

    def _stl_base(self) -> float:
        """Наибольший размер модели при 100 %, мм."""
        return max(self.model.size(1.0)) if self.model.loaded else 0.0

    def _sync_stl_max(self):
        base = self._stl_base()
        if base > 0:
            self.st_max.set_value(base * self.p.stl_scale / 100.0)

    def _set_stl_scale(self, pct: float):
        pct = max(5.0, min(2000.0, round(float(pct), 1)))
        self.rows["stl_scale"][1].set_value(pct)
        self._set("stl_scale", pct)

    def _set_stl_max(self, mm: float):
        base = self._stl_base()
        if base > 0:
            self._set_stl_scale(mm / base * 100.0)

    def _fit_stl(self):
        if not self.model.loaded:
            return
        w, l, _ = self.model.size(1.0)
        well_d = gen.SUBSTRATES[self.p.substrate][4]
        xy = max(w, l)
        if xy > 0:
            self._set_stl_scale(0.9 * well_d / xy * 100.0)

    def _stl_info(self):
        if self.model.loaded:
            w, l, h = self.model.size(self.p.stl_scale / 100.0)
            self.stl_size.setText(f"Размер: {w:.1f} × {l:.1f} × {h:.1f} мм · слоёв {self.p.n_layers(self.model)}")
        else:
            self.stl_size.setText("")

    # --- головки ---
    def _set_tool_mode(self, mode: str):
        self.p.tool_mode = mode
        self.picker.paint_tools = mode == "wells"
        if mode == "wells":
            for w in self.picker.wells():
                self.picker.well_tools.setdefault(w, self.p.tool)
        self.tool_lbl.setVisible(mode in ("single", "wells"))
        self.tool_seg.setVisible(mode in ("single", "wells"))
        self.tool_lbl.setText("Кисть" if mode == "wells" else "Головка")
        self._rebuild_layer_rules()
        self.picker.update()
        self._timer.start()

    def _set_tool(self, t: int):
        self.p.tool = int(t)
        self.picker.brush = int(t)
        self.picker.update()
        self._timer.start()

    def _rebuild_layer_rules(self):
        while self.layer_rows_box.count():
            it = self.layer_rows_box.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()
        show = self.p.tool_mode == "layers"
        self.b_add_rule.setVisible(show)
        if not show:
            return
        for i, (t, c) in enumerate(self.p.layer_tools):
            roww = QtWidgets.QWidget()
            r = QtWidgets.QHBoxLayout(roww)
            r.setContentsMargins(0, 0, 0, 0)
            seg = Segmented([(f"{k + 1}", k + 1) for k in range(len(config.HEADS))], int(t))
            seg.changed.connect(lambda v, i=i: self._rule(i, 0, v))
            st = Stepper(int(c), 1, 50, 1, " сл.")
            st.valueChanged.connect(lambda v, i=i: self._rule(i, 1, int(v)))
            r.addWidget(label(f"{i + 1}.", "Muted"))
            r.addWidget(seg, 2)
            r.addWidget(st, 2)
            b = button("✕", "small", lambda i=i: self._del_rule(i))
            b.setEnabled(len(self.p.layer_tools) > 1)
            r.addWidget(b)
            self.layer_rows_box.addWidget(roww)

    def _rule(self, i: int, k: int, v):
        self.p.layer_tools[i][k] = int(v)
        self._timer.start()

    def _add_layer_rule(self):
        last = self.p.layer_tools[-1][0] if self.p.layer_tools else 1
        self.p.layer_tools.append([last % len(config.HEADS) + 1, 1])
        self._rebuild_layer_rules()
        self._timer.start()

    def _del_rule(self, i: int):
        if len(self.p.layer_tools) > 1:
            del self.p.layer_tools[i]
            self._rebuild_layer_rules()
            self._timer.start()

    def _to_print(self):
        if not getattr(self, "prog", None):
            return
        self.ctx.job.load_text(self.text, "", self._title())
        self.ctx.navigate("print")

    def _save(self):
        if not getattr(self, "prog", None):
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Сохранить G-код", os.path.join(self.ctx.settings.last_dir or "", "test.gcode"),
            "G-код (*.gcode)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.text)
            self.warn.set_text(f"Сохранено: {path}")

    def showEvent(self, e):
        self._timer.start()
        super().showEvent(e)

    def set_simple(self, on: bool):
        """Простой режим: только основные параметры фигуры и шприца."""
        self.simple = on
        self._set_shape(self.p.shape)
        self.b_save.setVisible(not on)
