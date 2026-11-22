"""Главная: задание, готовность принтера, головки, термостол."""
from __future__ import annotations

from PyQt5 import QtCore, QtWidgets

from .. import config, gcode
from ..context import Ctx
from ..job import Job
from PyQt5.QtCore import Qt

from ..protocol import COORD_SYSTEMS_SHORT, Cmd, coord_system_name, coord_system_short
from ..widgets import Card, ElidedLabel, Led, Pill, Tile, ValueLabel, button, label
from .components import BedCard, CoverLiftCheck, HeadCard, page_layout


class HomePage(QtWidgets.QWidget):
    title = "Главная"

    def __init__(self, ctx: Ctx):
        super().__init__()
        self.ctx = ctx
        _, lay = page_layout(self)

        # ── ряд 1: задание + готовность ──
        row1 = QtWidgets.QHBoxLayout()
        row1.setSpacing(20)

        job = Card("Текущее задание")
        self.job_pill = Pill("Готов", "idle", reserve=Job.TITLES.values())
        job.add_to_header(self.job_pill)
        self.job_name = ElidedLabel("Программа не загружена", "MidValue")
        job.add(self.job_name)
        self.job_info = ElidedLabel("Откройте файл G-кода на экране «Печать»", "Muted")
        job.add(self.job_info)
        self.bar = QtWidgets.QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setFormat("%p%")
        job.add(self.bar)
        tiles = QtWidgets.QHBoxLayout()
        dur = ("00 ч 00 мин", "00 мин 00 с")
        self.t_elapsed = Tile("Прошло", "—", reserve=dur)
        self.t_left = Tile("Осталось (оценка)", "—", reserve=dur)
        self.t_layers = Tile("Слоёв", "—", reserve="00000")
        for t in (self.t_elapsed, self.t_left, self.t_layers):
            tiles.addWidget(t)
        tiles.addStretch(1)
        job.add(tiles)
        btns = QtWidgets.QHBoxLayout()
        btns.addWidget(button("Открыть экран печати", "primary", lambda: ctx.navigate("print")))
        self.b_stop = button("Остановить", "danger", ctx.job.stop)
        btns.addWidget(self.b_stop)
        btns.addStretch(1)
        job.add(btns)
        row1.addWidget(job, 3)

        ready = Card("Готовность принтера")
        self.led_conn = Led("Связь с контроллером")
        self.led_home = Led("Базирование (Home) выполнено")
        self.led_delta = Led("Калибровка дельты")
        self.led_bed = Led("Стол: нагрев выключен")
        for w in (self.led_conn, self.led_home, self.led_delta, self.led_bed):
            ready.add(w)
        ready.body.addStretch(1)
        rb = QtWidgets.QHBoxLayout()
        rb.addWidget(button("⌂  Home", None, lambda: ctx.link.send(Cmd.HOME)))
        rb.addWidget(button("Калибровка", None, lambda: ctx.navigate("calib")))
        ready.add(rb)
        row1.addWidget(ready, 2)
        lay.addLayout(row1)

        # ── ряд 2: головки ──
        row2 = QtWidgets.QHBoxLayout()
        row2.setSpacing(20)
        for i in range(len(config.HEADS)):
            row2.addWidget(HeadCard(ctx, i))
        lay.addLayout(row2)

        # ── ряд 3: термостол, позиция ──
        row3 = QtWidgets.QHBoxLayout()
        row3.setSpacing(20)
        row3.addWidget(BedCard(ctx), 1)

        table = Card("Стол", "Подача подложки")
        tb = QtWidgets.QVBoxLayout()
        tb.setSpacing(10)
        self.b_out = button("▶  Выдвинуть", "primary", lambda: self._table(Cmd.TABLE_OUT),
                            "Стол выезжает наружу — можно снять или поставить подложку")
        self.b_in = button("◀  Забрать", None, lambda: self._table(Cmd.TABLE_IN),
                           "Стол уезжает в рабочее положение")
        for b in (self.b_out, self.b_in):
            b.setMinimumHeight(72)
            tb.addWidget(b)
        table.add(tb)
        self.chk_cover = CoverLiftCheck(ctx)
        table.add(self.chk_cover)
        table.body.addStretch(1)
        row3.addWidget(table, 0)

        pos = Card("Координаты")
        self.cs_pill = Pill("СК: —", "idle", reserve=[f"СК: {n}" for n in COORD_SYSTEMS_SHORT.values()])
        pos.add_to_header(self.cs_pill)
        pg = QtWidgets.QGridLayout()
        pg.setHorizontalSpacing(18)
        pg.setVerticalSpacing(6)
        for c, a in enumerate("XYZ"):
            pg.addWidget(label(f"{a}, мм", "TileName"), 0, c + 1)
        self.coord_rows = {}
        for r, (key, name) in enumerate((("cur", "Текущая СК"), ("tool", "Инструмент"), ("mach", "Машина"))):
            pg.addWidget(label(name, "Muted"), r + 1, 0)
            vals = []
            for c in range(3):
                v = ValueLabel("—", "-000.00", "MidValue", Qt.AlignRight | Qt.AlignVCenter)
                pg.addWidget(v, r + 1, c + 1)
                vals.append(v)
            self.coord_rows[key] = vals
        pg.setColumnStretch(0, 1)
        pos.add(pg)
        pos.body.addStretch(1)
        pb = QtWidgets.QHBoxLayout()
        pb.addWidget(button("Ручное управление", None, lambda: ctx.navigate("manual")))
        pb.addWidget(button("В точку 0", None, lambda: ctx.link.send(Cmd.MOVE_TO_ZERO)))
        pos.add(pb)
        row3.addWidget(pos, 2)

        lay.addLayout(row3)
        lay.addStretch(1)

        ctx.link.state_changed.connect(self._on_state)
        ctx.link.connected_changed.connect(lambda ok: self.led_conn.set(ok))
        ctx.link.homed_changed.connect(lambda ok: self.led_home.set(ok))
        ctx.job.program_changed.connect(lambda _: self._refresh_job())
        ctx.job.status_changed.connect(lambda _: self._refresh_job())
        ctx.job.progress.connect(lambda *_: self._refresh_job())
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._refresh_job)
        self._timer.start(1000)
        self._refresh_job()


    def _table(self, cmd: str):
        if self.ctx.job.active:
            QtWidgets.QMessageBox.warning(self, "Стол", "Идёт печать — стол двигать нельзя.")
            return
        self.ctx.link.send_many([Cmd.cover_lift(self.ctx.settings.cover_lift), cmd])

    def _refresh_job(self):
        job = self.ctx.job
        self.job_pill.set(Job.TITLES[job.status], Job.LEVELS[job.status])
        p = job.program
        if p:
            self.job_name.set_text(job.name)
            self.job_info.set_text(
                f"{len(p.layers)} слоёв · оценка {gcode.fmt_duration(job.total_estimate())}"
                f" при скорости {self.ctx.settings.speed_pct} %")
            self.t_layers.set(str(len(p.layers)))
        self.bar.setValue(int(job.fraction * 1000))
        active = job.active
        self.b_stop.setEnabled(active)
        if job.started_at:
            el = job.elapsed()
            self.t_elapsed.set(gcode.fmt_duration(el))
            self.t_left.set(gcode.fmt_duration(job.remaining()) if p and active else "—")

    def _on_state(self, board, st):
        if board == 1:
            for key, xyz in (("cur", (st.x, st.y, st.z)), ("tool", (st.tx, st.ty, st.tz)),
                             ("mach", (st.mx, st.my, st.mz))):
                for lbl, v in zip(self.coord_rows[key], xyz):
                    lbl.setText(f"{v:.2f}")
            level = {"AUTO": "ok", "MANUAL": "info", "CURRENT": "warn", "INTERNAL": "warn"}.get(st.coord_sys, "idle")
            self.cs_pill.set(f"СК: {coord_system_short(st.coord_sys)}", level)
            self.cs_pill.setToolTip(coord_system_name(st.coord_sys))
            self.led_delta.set(bool(st.delta_calib))
        if board == config.TEMP_SOURCE_BOARD:
            bed = self.ctx.bed
            t = self.ctx.link.bed_temp()
            reached = bed.on and abs(t - bed.setpoint) <= 1
            self.led_bed.set(reached, warn=bed.on)
            text = "Стол: нагрет" if reached else ("Стол: нагревается" if bed.on else "Стол: нагрев выключен")
            if self.led_bed.lbl.text() != text:
                self.led_bed.lbl.setText(text)
