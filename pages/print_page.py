"""Печать: загрузка программы, конвертация под контроллер, 3D/послойное превью, запуск."""
from __future__ import annotations

import os

from PyQt5 import QtCore, QtWidgets
from PyQt5.QtCore import Qt

from .. import config, gcode
from ..context import Ctx
from ..job import Job
from ..preview import TopView
from ..protocol import Cmd
from ..view3d import View3D
from ..widgets import Card, ElidedLabel, Pill, Stepper, Tile, ValueLabel, button, label
from .components import start_with_checks

DUR = ("00 ч 00 мин", "00 мин 00 с")


def sample_grid(size=10.0, pitch=1.25, layers=4, lh=0.3, feed=240) -> str:
    """Тестовая решётка 0/90° — временно, до генератора решёток."""
    out = ["; Тестовая решётка", "G90", "G92 E0", f"G1 Z{lh:.2f} F600"]
    e = 0.0
    h = size / 2
    n = int(size / pitch) + 1
    for L in range(layers):
        z = lh * (L + 1)
        out.append(f"G1 Z{z:.2f} F600")
        for i in range(n):
            c = -h + i * pitch
            a, b = (-h, h) if i % 2 == 0 else (h, -h)
            if L % 2 == 0:
                out.append(f"G0 X{a:.2f} Y{c:.2f} F{feed}")
                e += size * 0.02
                out.append(f"G1 X{b:.2f} Y{c:.2f} E{e:.3f} F{feed}")
            else:
                out.append(f"G0 X{c:.2f} Y{a:.2f} F{feed}")
                e += size * 0.02
                out.append(f"G1 X{c:.2f} Y{b:.2f} E{e:.3f} F{feed}")
    out.append("G1 Z10 F600")
    return "\n".join(out) + "\n"


class GcodeEditor(QtWidgets.QDialog):
    """Исходный G-код (правка) и то, что реально уйдёт в контроллер."""

    def __init__(self, parent, text: str):
        super().__init__(parent)
        self.setWindowTitle("G-код программы")
        self.resize(1200, 860)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(16)
        tabs = QtWidgets.QTabWidget()
        self.ed = QtWidgets.QPlainTextEdit(text)
        self.out = QtWidgets.QPlainTextEdit()
        self.out.setReadOnly(True)
        tabs.addTab(self.ed, "Исходный G-код")
        tabs.addTab(self.out, "Без комментариев")
        tabs.currentChanged.connect(lambda i: i == 1 and self._convert())
        lay.addWidget(tabs, 1)
        self.info = label("", "Muted")
        lay.addWidget(self.info)
        row = QtWidgets.QHBoxLayout()
        row.addStretch(1)
        row.addWidget(button("Отмена", None, self.reject))
        row.addWidget(button("Применить", "primary", self.accept))
        lay.addLayout(row)
        self._convert()

    def _convert(self):
        p = gcode.parse(self.ed.toPlainText())
        self.out.setPlainText("\n".join(p.exec_lines))
        conv = p.conversion
        self.info.setText(conv.summary() + ("\n⚠ " + "\n⚠ ".join(conv.notes) if conv.notes else ""))
        self.info.setWordWrap(True)


class PrintPage(QtWidgets.QWidget):
    title = "Печать"

    def __init__(self, ctx: Ctx):
        super().__init__()
        self.ctx = ctx
        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(28, 22, 28, 22)
        root.setSpacing(20)

        # ── превью ──
        pv = Card("Превью")
        self.mode_btns = []
        for i, name in enumerate(("3D", "Слой")):
            b = button(name, "small", lambda i=i: self._mode(i))
            b.setCheckable(True)
            b.setMinimumWidth(96)
            self.mode_btns.append(b)
            pv.add_to_header(b)
        pv.head.insertSpacing(1, 16)
        pv.head.addSpacing(16)
        self.layer_lbl = ValueLabel("Слой —", ["Слой 0000 из 0000 · Z 000.00 мм"], "Muted",
                                    Qt.AlignRight | Qt.AlignVCenter)
        pv.add_to_header(self.layer_lbl)

        tools = QtWidgets.QHBoxLayout()
        tools.setSpacing(8)
        self.tb3d = []
        for text, fn in (("Изо", lambda: self.v3.set_view("iso")), ("Сверху", lambda: self.v3.set_view("top")),
                         ("Спереди", lambda: self.v3.set_view("front")), ("Сбоку", lambda: self.v3.set_view("side")),
                         ("−", lambda: self.v3.zoom_by(1 / 1.25)), ("+", lambda: self.v3.zoom_by(1.25)),
                         ("Сброс", lambda: self.v3.reset_view())):
            b = button(text, "small", fn)
            tools.addWidget(b)
            self.tb3d.append(b)
        tools.addStretch(1)
        self.b_travel = button("Перемещения", "small", self._travel)
        self.b_travel.setCheckable(True)
        self.b_below = button("Нижние слои", "small", self._below)
        self.b_below.setCheckable(True)
        self.b_below.setChecked(True)
        tools.addWidget(self.b_travel)
        tools.addWidget(self.b_below)
        pv.add(tools)

        body = QtWidgets.QHBoxLayout()
        self.views = QtWidgets.QStackedWidget()
        self.v3 = View3D()
        self.v2 = TopView()
        self.views.addWidget(self.v3)
        self.views.addWidget(self.v2)
        body.addWidget(self.views, 1)
        side = QtWidgets.QVBoxLayout()
        self.b_up = button("▲", "step", lambda: self.slider.setValue(self.slider.value() + 1))
        self.slider = QtWidgets.QSlider(Qt.Vertical)
        self.slider.setMinimumWidth(60)
        self.slider.valueChanged.connect(self._layer)
        self.b_dn = button("▼", "step", lambda: self.slider.setValue(self.slider.value() - 1))
        side.addWidget(self.b_up)
        side.addWidget(self.slider, 1, Qt.AlignHCenter)
        side.addWidget(self.b_dn)
        body.addLayout(side)
        pv.add(body, 1)
        root.addWidget(pv, 1)

        # ── правая колонка ──
        col = QtWidgets.QVBoxLayout()
        col.setSpacing(18)
        col.setContentsMargins(0, 0, 0, 0)

        fc = Card("Программа")
        self.f_name = ElidedLabel("Не загружена", "MidValue")
        fc.add(self.f_name)
        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        self.t_layers = Tile("Слоёв", "—", reserve="00000")
        self.t_time = Tile("Время (оценка)", "—", reserve=DUR)
        grid.addWidget(self.t_layers, 0, 0)
        grid.addWidget(self.t_time, 0, 1)
        grid.setColumnStretch(2, 1)
        fc.add(grid)
        self.conv1 = ElidedLabel("", "Muted")
        self.conv2 = ElidedLabel("", "Muted")
        fc.add(self.conv1)
        fc.add(self.conv2)
        fr = QtWidgets.QHBoxLayout()
        fr.addWidget(button("Открыть…", "primary", self._open))
        self.b_gcode = button("G-код", None, self._edit)
        fr.addWidget(self.b_gcode)
        fr.addWidget(button("Генератор", None, lambda: ctx.navigate("gen"),
                            "Решётки, цилиндры, диски для тестовой печати"))
        fc.add(fr)
        col.addWidget(fc)

        jc = Card("Выполнение")
        self.pill = Pill("Готов", "idle", reserve=Job.TITLES.values())
        jc.add_to_header(self.pill)
        self.chk_ready = QtWidgets.QCheckBox("Подложка установлена")
        self.chk_auto = QtWidgets.QCheckBox("Автокалибровка")
        self.chk_auto.setToolTip("Печать в системе координат AUTO: после смены головки шлюз калибрует "
                                 "сопло лазерами, ноль — по подложке (G54). Выкл — ноль оператора (MANUAL)")
        self.chk_auto.setChecked(ctx.settings.autocalibration)
        self.chk_auto.toggled.connect(self._autocal)
        for w in (self.chk_ready, self.chk_auto):
            jc.add(w)
        self.bar = QtWidgets.QProgressBar()
        self.bar.setRange(0, 1000)
        jc.add(self.bar)
        self.prog_lbl = ElidedLabel("—", "Muted")
        jc.add(self.prog_lbl)
        self.b_start = button("▶  Старт", "primary", self._start)
        self.b_start.setMinimumHeight(72)
        self.b_start.setStyleSheet("font-size:24px;")
        jc.add(self.b_start)
        self.b_stop = button("■  Остановить печать", "danger", ctx.job.stop,
                             "Остановка: подкачка кадров прекращается, плата выключает кольцевой буфер")
        jc.add(self.b_stop)
        col.addWidget(jc)

        # ── скорость и подача (M618 / M617, как vel_koef / e_koef в String_line4) ──
        kc = Card("Скорость и подача", "Меняются на ходу; 100 % — как в программе")
        kg = QtWidgets.QGridLayout()
        kg.setHorizontalSpacing(12)
        kg.setVerticalSpacing(10)
        self.k_speed = Stepper(ctx.settings.speed_pct, config.FACTOR_MIN, config.FACTOR_MAX,
                               config.FACTOR_STEP, " %")
        self.k_flow = Stepper(ctx.settings.flow_pct, config.FACTOR_MIN, config.FACTOR_MAX,
                              config.FACTOR_STEP, " %")
        self.k_speed.valueChanged.connect(lambda v: self._factor("speed_pct", v))
        self.k_flow.valueChanged.connect(lambda v: self._factor("flow_pct", v))
        for r, (name, st, key) in enumerate((("Скорость печати", self.k_speed, "speed_pct"),
                                             ("Экструзия (подача)", self.k_flow, "flow_pct"))):
            kg.addWidget(label(name, "Muted"), r, 0)
            kg.addWidget(st, r, 1)
            b = button("100 %", "small", lambda st=st, key=key: (st.set_value(100), self._factor(key, 100)))
            kg.addWidget(b, r, 2)
        kg.setColumnStretch(1, 1)
        kc.add(kg)
        col.addWidget(kc)
        col.addStretch(1)

        wrap = QtWidgets.QWidget()
        wrap.setFixedWidth(580)
        wrap.setLayout(col)
        root.addWidget(wrap)

        ctx.job.program_changed.connect(self._program)
        ctx.job.status_changed.connect(lambda _: self._refresh())
        ctx.job.progress.connect(lambda *_: self._refresh())
        ctx.link.state_changed.connect(self._on_state)
        ctx.link.connected_changed.connect(lambda _: self._refresh())
        self.chk_ready.toggled.connect(lambda _: self._refresh())
        t = QtCore.QTimer(self)
        t.timeout.connect(self._refresh)            # время «осталось» — раз в секунду
        t.start(1000)
        self._mode(0)
        self._program(None)

    # --- вид ---
    def _mode(self, i: int):
        self.views.setCurrentIndex(i)
        for j, b in enumerate(self.mode_btns):
            b.setChecked(i == j)
        simple = getattr(self, "simple", False)
        for b in self.tb3d + [self.b_travel]:
            b.setVisible(i == 0 and not (simple and (b is self.b_travel or b.text() in ("Спереди", "Сбоку"))))
        self.b_below.setVisible(i == 1)

    def _travel(self):
        self.v3.show_travel = self.b_travel.isChecked()
        self.v3.update()

    def _below(self):
        self.v2.show_all_layers = self.b_below.isChecked()
        self.v2.update()

    # --- действия ---
    def _open(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Открыть программу", self.ctx.settings.last_dir,
            "G-код (*.gcode *.gco *.nc *.txt);;Все файлы (*)")
        if not path:
            return
        self.ctx.settings.last_dir = os.path.dirname(path)
        self.ctx.settings.save()
        try:
            self.ctx.job.load_file(path)
        except OSError as e:
            QtWidgets.QMessageBox.critical(self, "Ошибка", f"Не удалось открыть файл:\n{e}")

    def _edit(self):
        job = self.ctx.job
        dlg = GcodeEditor(self, job.program.text if job.program else "")
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            job.load_text(dlg.ed.toPlainText(), job.path, job.title)

    def _start(self):
        start_with_checks(self, self.ctx)

    def _autocal(self, on: bool):
        self.ctx.settings.autocalibration = on
        self.ctx.settings.save()
        self.ctx.link.send(Cmd.autocalibration(on))

    def _factor(self, key: str, v: float):
        v = int(round(v))
        setattr(self.ctx.settings, key, v)
        self.ctx.settings.save()
        self.ctx.link.send(Cmd.speed_factor(v) if key == "speed_pct" else Cmd.extrusion_factor(v))
        self._refresh()

    # --- обновление ---
    def _program(self, _):
        p = self.ctx.job.program
        self.v3.set_program(p)
        self.v2.set_program(p)
        n = len(p.layers) if p else 0
        self.slider.blockSignals(True)
        self.slider.setRange(0, max(0, n - 1))
        self.slider.setValue(max(0, n - 1))
        self.slider.blockSignals(False)
        self._layer(self.slider.value())
        if p:
            conv = p.conversion
            self.f_name.set_text(self.ctx.job.name)
            self.t_layers.set(str(n))
            tools = ", ".join(f"T{t}" for t in dict.fromkeys(conv.tools)) or "текущая головка"
            plates = ", ".join(f"G54 X{x}" for x in dict.fromkeys(conv.plates))
            self.conv1.set_text(f"Головки: {tools}" + (f" · {plates}" if plates else ""))
            if conv.notes:
                self.conv2.set_text(f"⚠ {conv.notes[0]}" + (f" (+ ещё {len(conv.notes) - 1})" if len(conv.notes) > 1 else ""))
            elif conv.dropped:
                items = ", ".join(f"{k}×{v}" for k, v in conv.dropped.most_common())
                self.conv2.set_text(f"Не выполняются: {items}")
            else:
                self.conv2.set_text("")
            tip = conv.summary() + ("\n⚠ " + "\n⚠ ".join(conv.notes) if conv.notes else "")
            self.conv1.setToolTip(tip)
            self.conv2.setToolTip(tip)
        else:
            self.f_name.set_text("Не загружена")
            self.conv1.set_text("Откройте файл G-кода или загрузите тестовую решётку")
            self.conv2.set_text("")
        self._refresh()

    def _layer(self, i):
        self.v2.set_layer(i)
        self.v3.set_max_layer(i)
        p = self.ctx.job.program
        if p and p.layers:
            self.layer_lbl.setText(f"Слой {i + 1} из {len(p.layers)} · Z {p.layers[i]:.2f} мм")
        else:
            self.layer_lbl.setText("Слой —")

    def _on_state(self, board, st):
        if board == 1:
            x, y, z = self.ctx.link.prog_xyz(st)       # от нуля программы, как траектория
            self.v2.set_nozzle(x, y)
            self.v3.set_nozzle(x, y, z)
            job = self.ctx.job
            if job.status == Job.PRINTING and job.program and job.program.layers:
                li = job.program.layer_index(z)
                if li != self.slider.value():
                    self.slider.setValue(li)

    def _refresh(self):
        job = self.ctx.job
        self.pill.set(Job.TITLES[job.status], Job.LEVELS[job.status])
        self.bar.setValue(int(job.fraction * 1000))
        done = job.line_no if job.status != Job.IDLE else -1
        self.v2.done_line = done
        self.v3.done_line = done
        n = len(job.program.exec_lines) if job.program else 0
        if job.status == Job.LOADING:
            sent, total = job.upload
            self.prog_lbl.set_text(f"Загрузка программы: {sent} из {total}")
        elif job.status == Job.PRINTING:
            self.prog_lbl.set_text(f"Строка {max(0, job.line_no + 1)} из {n} · осталось ≈ "
                                   f"{gcode.fmt_duration(job.remaining())} при скорости {self.ctx.settings.speed_pct} %")
        elif job.status == Job.DONE:
            self.prog_lbl.set_text(f"Выполнено за {gcode.fmt_duration(job.elapsed())}")
        else:
            self.prog_lbl.set_text("—")
        active = job.active
        self.b_start.setEnabled(bool(job.program) and not active and self.chk_ready.isChecked())
        self.b_start.setToolTip("" if self.chk_ready.isChecked() else "Подтвердите, что подложка установлена")
        self.b_stop.setEnabled(active)
        if job.program:
            self.t_time.set(gcode.fmt_duration(job.total_estimate()))
        self.v2.update()
        self.v3.update()

    def set_simple(self, on: bool):
        """Простой режим: только 3D, без правки G-кода и служебных видов."""
        self.simple = on
        if on:
            self._mode(0)
        for b in self.mode_btns:
            b.setVisible(not on)
        self.b_gcode.setVisible(not on)
        self.b_travel.setVisible(not on)
        for b in self.tb3d:
            if b.text() in ("Спереди", "Сбоку"):
                b.setVisible(not on)
