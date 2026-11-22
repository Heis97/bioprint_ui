"""Калибровка и сервис: базирование, дельта, стол по 3 точкам, подключение."""
from __future__ import annotations

import os

from PyQt5 import QtWidgets
from PyQt5.QtCore import Qt

from ..context import Ctx
from ..protocol import Cmd
from ..theme import C
from .. import config
from ..widgets import Card, JogPad, Led, Segmented, ToggleButton, Tile, ValueLabel, button, label
from .components import page_layout
from .manual import JogSettings, jog_handlers


class StepBadge(QtWidgets.QLabel):
    def __init__(self, n: int):
        super().__init__(str(n))
        self.setFixedSize(44, 44)
        self.setAlignment(Qt.AlignCenter)
        self.set_done(False)

    def theme_changed(self):
        self.set_done(self._done)

    def set_done(self, done: bool):
        self._done = done
        bg, fg = (C["ok"], "#FFFFFF") if done else (C["surface2"], C["muted"])
        self.setStyleSheet(f"background:{bg}; color:{fg}; border-radius:22px; font-size:20px; font-weight:700;"
                           f"border:1px solid {C['border']};")
        if done:
            self.setText("✓")


class CalibrationPage(QtWidgets.QWidget):
    title = "Калибровка и сервис"

    def __init__(self, ctx: Ctx, on_reconnect):
        super().__init__()
        self.ctx = ctx
        _, lay = page_layout(self)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(20)

        # ── калибровка стола ──
        bed = Card("Калибровка плоскости стола",
                   "Подведите сопло к каждой из трёх точек стола кнопками перемещения и нажмите «Запомнить». "
                   "Затем нажмите «Рассчитать».")
        body = QtWidgets.QHBoxLayout()
        body.setSpacing(28)
        xy, z = jog_handlers(ctx)
        jcol = QtWidgets.QVBoxLayout()
        jcol.setSpacing(14)
        jcol.addWidget(JogPad(xy, z, size=120))
        jcol.addWidget(JogSettings(ctx))
        jcol.addStretch(1)
        body.addLayout(jcol)
        steps = QtWidgets.QVBoxLayout()
        steps.setSpacing(14)
        self.badges = []
        for i in range(3):
            r = QtWidgets.QHBoxLayout()
            b = StepBadge(i + 1)
            self.badges.append(b)
            r.addWidget(b)
            r.addWidget(label(f"Точка {i + 1}"), 1)
            r.addWidget(button("Запомнить", None, lambda n=i: self._remember(n)))
            steps.addLayout(r)
        self.pos_lbl = ValueLabel("X 0.00   Y 0.00   Z 0.00", "X -000.00   Y -000.00   Z -000.00", "Muted")
        steps.addWidget(self.pos_lbl)
        steps.addStretch(1)
        fin = QtWidgets.QHBoxLayout()
        fin.addWidget(button("Рассчитать", "primary", self._compute))
        fin.addWidget(button("Сбросить", None, self._reset))
        steps.addLayout(fin)
        body.addLayout(steps, 1)
        bed.add(body)
        row.addWidget(bed, 3)

        # ── базирование ──
        mech = Card("Кинематика")
        self.led_home = Led("Home выполнен")
        self.led_delta = Led("Дельта откалибрована")
        mech.add(self.led_home)
        mech.add(self.led_delta)
        mech.add(button("⌂  Home — базирование", "primary", lambda: ctx.link.send(Cmd.HOME)))
        mech.add(button("Home — тест", None, lambda: ctx.link.send(Cmd.HOME_TEST)))
        mech.add(button("Калибровка дельты", None, self._delta))
        mech.add(button("Загрузить настройки в контроллер", None, lambda: ctx.link.send(Cmd.LOAD_SETTINGS)))
        mech.body.addStretch(1)
        right = QtWidgets.QVBoxLayout()
        right.setSpacing(20)
        right.addWidget(mech)

        # ── инструмент (головка на сопле) — как блок инструментов в String_line4 ──
        tool = Card("Инструмент")
        tool.add(label("Текущий инструмент", "Muted"))
        self.tool_seg = Segmented([(f"Головка {i + 1}", i + 1) for i in range(len(config.HEADS))])
        self.tool_seg.changed.connect(lambda n: ctx.link.send(Cmd.set_active_tool(n)))
        self.tool_seg.setToolTip("Задать текущий инструмент (main M639) — без движения")
        tool.add(self.tool_seg)
        tool.add(label("Сменить инструмент", "Muted"))
        takes = QtWidgets.QHBoxLayout()
        takes.setSpacing(10)
        for i in range(len(config.HEADS)):
            takes.addWidget(button(f"Взять {i + 1}", None, lambda n=i + 1: self._take(n),
                                   f"Сопло отдаст текущую головку в свободный манипулятор и возьмёт головку {i + 1}"))
        tool.add(takes)
        tool.add(button("Отдать текущий", None, self._drop,
                        "Сопло отдаст текущую головку в свободный манипулятор"))
        self.tool_lbl = ValueLabel("На сопле: —   ·   Л: —   П: —",
                                   "На сопле: 0   ·   Л: 0   П: 0", "Muted")
        tool.add(self.tool_lbl)
        right.addWidget(tool)
        row.addLayout(right, 2)
        lay.addLayout(row)

        # ── подключение ──
        conn = Card("Подключение к контроллеру")
        g = QtWidgets.QHBoxLayout()
        g.setSpacing(16)
        self.host = QtWidgets.QLineEdit(ctx.settings.host)
        self.port = QtWidgets.QLineEdit(str(ctx.settings.port))
        self.port.setMaximumWidth(160)
        g.addWidget(label("Адрес"))
        g.addWidget(self.host, 2)
        g.addWidget(label("Порт"))
        g.addWidget(self.port)
        self.demo = ToggleButton("Демо-режим")
        self.demo.set_silent(ctx.settings.demo)
        g.addWidget(self.demo)
        g.addWidget(button("Применить и переподключить", "primary", lambda: self._apply(on_reconnect)))
        conn.add(g)
        # шлюз: путь, автозапуск
        g2 = QtWidgets.QHBoxLayout()
        g2.setSpacing(16)
        g2.addWidget(label("Шлюз"))
        self.gw_path = QtWidgets.QLineEdit(ctx.settings.gateway_path or config.DEFAULT_GATEWAY_PATH)
        g2.addWidget(self.gw_path, 1)
        g2.addWidget(button("Обзор…", None, self._browse_gw))
        self.gw_auto = ToggleButton("Запускать при старте", on_text="да", off_text="нет")
        self.gw_auto.set_silent(ctx.settings.gateway_autostart)
        g2.addWidget(self.gw_auto)
        g2.addWidget(button("Перезапустить шлюз", None, self._restart_gw,
                            "Закрыть шлюз, запущенный пультом, и запустить снова"))
        conn.add(g2)
        self.gw_msg = label("", "Muted", wrap=True)
        conn.add(self.gw_msg)
        lay.addWidget(conn)
        lay.addStretch(1)

        ctx.link.state_changed.connect(self._on_state)
        ctx.link.homed_changed.connect(lambda ok: self.led_home.set(ok))

    def _remember(self, n: int):
        self.ctx.link.send(Cmd.bed_point(n))
        self.badges[n].set_done(True)

    def _compute(self):
        self.ctx.link.send(Cmd.BED_COMPUTE)

    def _reset(self):
        self.ctx.link.send(Cmd.BED_RESET)
        for i, b in enumerate(self.badges):
            b.setText(str(i + 1))
            b.set_done(False)

    def _take(self, n: int):
        r = QtWidgets.QMessageBox.question(
            self, "Смена инструмента",
            f"Взять головку {n}?\nСопло поедет к манипуляторам. Убедитесь, что рабочая зона свободна.")
        if r == QtWidgets.QMessageBox.Yes:
            self.ctx.link.send(Cmd.take_tool(n))

    def _drop(self):
        r = QtWidgets.QMessageBox.question(
            self, "Смена инструмента",
            "Отдать текущую головку в манипулятор?\nСопло поедет к манипуляторам.")
        if r == QtWidgets.QMessageBox.Yes:
            self.ctx.link.send(Cmd.DROP_TOOL)

    def _delta(self):
        r = QtWidgets.QMessageBox.question(
            self, "Калибровка дельты",
            "Запустить автоматическую калибровку дельта-кинематики?\nУбедитесь, что рабочая зона свободна.")
        if r == QtWidgets.QMessageBox.Yes:
            self.ctx.link.send(Cmd.DELTA_CALIBRATE)

    def _apply(self, on_reconnect):
        s = self.ctx.settings
        s.host = self.host.text().strip() or s.host
        try:
            s.port = int(self.port.text())
        except ValueError:
            self.port.setText(str(s.port))
        s.demo = self.demo.isChecked()
        self._save_gw()
        s.save()
        if s.gateway_autostart:
            self.ctx.gateway.start()
            self.gw_msg.setText(self.ctx.gateway.message)
        on_reconnect()

    def _save_gw(self):
        s = self.ctx.settings
        path = self.gw_path.text().strip()
        s.gateway_path = "" if path == config.DEFAULT_GATEWAY_PATH else path
        s.gateway_autostart = self.gw_auto.isChecked()

    def _browse_gw(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Шлюз tcp_to_udp", os.path.dirname(self.gw_path.text()), "Программа (*.exe);;Все файлы (*)")
        if path:
            self.gw_path.setText(path)
            self._save_gw()
            self.ctx.settings.save()

    def _restart_gw(self):
        self._save_gw()
        self.ctx.settings.save()
        self.ctx.gateway.restart()
        self.gw_msg.setText(self.ctx.gateway.message)

    def _on_state(self, board, st):
        if board == 1:
            self.led_delta.set(bool(st.delta_calib))
            if st.tools_known:
                t = lambda v: str(v) if v else "—"
                self.tool_lbl.setText(f"На сопле: {t(st.tool_active)}   ·   "
                                      f"Л: {t(st.tool_manip_left)}   П: {t(st.tool_manip_right)}")
                if st.tool_active != self.tool_seg.value():
                    self.tool_seg.set_value(st.tool_active or None)
            self.pos_lbl.setText(f"X {st.x:.2f}   Y {st.y:.2f}   Z {st.z:.2f}")
