"""Главная в простом режиме: три шага — подготовка, программа, печать."""
from __future__ import annotations

import os

from PyQt5 import QtCore, QtWidgets
from PyQt5.QtCore import Qt

from .. import gcode
from ..context import Ctx
from ..job import Job
from ..protocol import Cmd, coord_system_name
from ..theme import C, styled
from ..widgets import Card, ElidedLabel, Led, Pill, Tile, ValueLabel, button, label
from .components import BedCard, page_layout, start_with_checks


def _step_title(n: int, text: str) -> QtWidgets.QWidget:
    w = QtWidgets.QWidget()
    w.setObjectName("StepTitle")
    w.setStyleSheet("#StepTitle { background: transparent; }")
    lay = QtWidgets.QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(14)
    num = QtWidgets.QLabel(str(n))
    num.setFixedSize(48, 48)
    num.setAlignment(Qt.AlignCenter)
    styled(num, lambda: f"background:{C['accent']}; color:#FFFFFF; border-radius:24px; font-size:24px; font-weight:800;")
    lay.addWidget(num)
    lay.addWidget(label(text, "CardTitle"), 1)
    return w


class SimpleHomePage(QtWidgets.QWidget):
    title = "Главная"

    def __init__(self, ctx: Ctx):
        super().__init__()
        self.ctx = ctx
        _, lay = page_layout(self)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(20)

        # ── 1. подготовка ──
        prep = Card()
        prep.add(_step_title(1, "Подготовка"))
        self.led_conn = Led("Связь с принтером")
        self.led_home = Led("Базирование выполнено")
        prep.add(self.led_conn)
        prep.add(self.led_home)
        b_home = button("⌂  Home — базирование", "primary", lambda: ctx.link.send(Cmd.HOME))
        b_home.setMinimumHeight(80)
        prep.add(b_home)
        prep.add(label("Подведите сопло к центру подложки и задайте ноль", "Muted", wrap=True))
        prep.add(button("Ручное управление и ноль", None, lambda: ctx.navigate("manual")))
        self.zero_lbl = ValueLabel("", "Система координат: Авто (калибровка + подложка)", "Muted")
        prep.add(self.zero_lbl)
        prep.body.addStretch(1)
        row.addWidget(prep, 1)

        # ── 2. программа ──
        prog = Card()
        prog.add(_step_title(2, "Программа"))
        self.p_name = ElidedLabel("Не выбрана", "MidValue")
        prog.add(self.p_name)
        self.p_info = ElidedLabel("Откройте G-код или создайте фигуру", "Muted")
        prog.add(self.p_info)
        b_gen = button("▦  Генератор фигур", "primary", lambda: ctx.navigate("gen"))
        b_gen.setMinimumHeight(80)
        prog.add(b_gen)
        prog.add(button("Открыть файл…", None, self._open))
        prog.add(button("Посмотреть в 3D", None, lambda: ctx.navigate("print")))
        prog.body.addStretch(1)
        row.addWidget(prog, 1)

        # ── 3. печать ──
        pr = Card()
        pr.add(_step_title(3, "Печать"))
        self.pill = Pill("Готов", "idle", reserve=Job.TITLES.values())
        pr.add(self.pill)
        self.bar = QtWidgets.QProgressBar()
        self.bar.setRange(0, 1000)
        pr.add(self.bar)
        tl = QtWidgets.QHBoxLayout()
        dur = ("00 ч 00 мин", "00 мин 00 с")
        self.t_el = Tile("Прошло", "—", reserve=dur)
        self.t_left = Tile("Осталось", "—", reserve=dur)
        tl.addWidget(self.t_el)
        tl.addWidget(self.t_left)
        tl.addStretch(1)
        pr.add(tl)
        self.chk = QtWidgets.QCheckBox("Подложка установлена")
        self.chk.toggled.connect(lambda _: self._refresh())
        pr.add(self.chk)
        self.b_start = button("▶  Старт", "primary", self._start)
        self.b_start.setMinimumHeight(90)
        self.b_start.setStyleSheet("font-size:26px;")
        pr.add(self.b_start)
        self.b_stop = button("■  Остановить", "danger", ctx.job.stop)
        self.b_stop.setMinimumHeight(80)
        pr.add(self.b_stop)
        pr.body.addStretch(1)
        row.addWidget(pr, 1)
        lay.addLayout(row)

        bottom = QtWidgets.QHBoxLayout()
        bottom.setSpacing(20)
        bottom.addWidget(BedCard(ctx), 1)
        pos = Card("Положение сопла (от нуля)")
        pt = QtWidgets.QHBoxLayout()
        self.px, self.py, self.pz = (Tile(f"{a}, мм", "0.00", "BigValue", reserve="-000.00") for a in "XYZ")
        for t in (self.px, self.py, self.pz):
            pt.addWidget(t)
        pos.add(pt)
        pos.body.addStretch(1)
        bottom.addWidget(pos, 1)
        lay.addLayout(bottom)
        lay.addStretch(1)

        ctx.link.connected_changed.connect(lambda ok: self._refresh())
        ctx.link.homed_changed.connect(lambda ok: self._refresh())
        ctx.link.zero_changed.connect(self._zero)
        ctx.link.state_changed.connect(self._on_state)
        ctx.job.program_changed.connect(lambda _: self._refresh())
        ctx.job.status_changed.connect(lambda _: self._refresh())
        ctx.job.progress.connect(lambda *_: self._refresh())
        t = QtCore.QTimer(self)
        t.timeout.connect(self._refresh)
        t.start(1000)
        self._zero()
        self._refresh()

    def _open(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Открыть программу", self.ctx.settings.last_dir,
            "G-код (*.gcode *.gco *.nc *.txt);;Все файлы (*)")
        if path:
            self.ctx.settings.last_dir = os.path.dirname(path)
            self.ctx.settings.save()
            self.ctx.job.load_file(path)

    def _start(self):
        start_with_checks(self, self.ctx)

    def _zero(self):
        s = self.ctx.settings
        self.zero_lbl.setText("Система координат: " + coord_system_name("AUTO" if s.autocalibration else "MANUAL"))

    def _on_state(self, board, st):
        if board == 1:
            x, y, z = self.ctx.link.prog_xyz(st)
            self.px.set(f"{x:.2f}")
            self.py.set(f"{y:.2f}")
            self.pz.set(f"{z:.2f}")

    def _refresh(self):
        link, job = self.ctx.link, self.ctx.job
        self._zero()
        self.led_conn.set(link.connected)
        self.led_home.set(link.homed)
        p = job.program
        if p:
            self.p_name.set_text(job.name)
            self.p_info.set_text(f"{len(p.layers)} слоёв · ~{gcode.fmt_duration(job.total_estimate())}")
        self.pill.set(Job.TITLES[job.status], Job.LEVELS[job.status])
        self.bar.setValue(int(job.fraction * 1000))
        active = job.active
        if job.started_at:
            el = job.elapsed()
            self.t_el.set(gcode.fmt_duration(el))
            if p and active:
                self.t_left.set(gcode.fmt_duration(job.remaining()))
            else:
                self.t_left.set("—")
        self.b_start.setEnabled(bool(p) and not active and self.chk.isChecked())
        self.b_stop.setEnabled(active)
