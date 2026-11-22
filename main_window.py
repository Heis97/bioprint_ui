"""Главное окно: боковая навигация, верхняя панель состояния, кнопка СТОП."""
from __future__ import annotations

import subprocess
import sys
import time

from PyQt5 import QtCore, QtWidgets
from PyQt5.QtCore import Qt

from . import config, theme
from .context import Ctx
from .job import Job
from .pages.calibration import CalibrationPage
from .pages.cameras import CamerasPage
from .pages.console import ConsolePage
from .pages.generator import GeneratorPage
from .pages.home import HomePage
from .pages.simple_home import SimpleHomePage
from .pages.manual import ManualPage
from .pages.periphery import PeripheryPage
from .pages.components import page_scroll
from .pages.print_page import PrintPage
from .widgets import Pill, ValueLabel, button, label

NO_TELEMETRY_S = 2.0      # нет телеметрии от платы 1 дольше — «Нет данных»

NAV = [
    ("home", "⌂", "Главная"),
    ("print", "▶", "Печать"),
    ("gen", "▦", "Генератор"),
    ("manual", "✥", "Ручное"),
    ("periph", "◎", "Периферия"),
    ("calib", "⚙", "Калибровка"),
    ("cams", "◉", "Камеры"),
    ("console", "≡", "Консоль"),
]


class Readout(QtWidgets.QWidget):
    def __init__(self, name: str, reserve: str):
        super().__init__()
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.n = label(name, "ReadoutName")
        self.v = ValueLabel("—", reserve, "ReadoutValue")
        lay.addWidget(self.n)
        lay.addWidget(self.v)


def shutdown_command() -> list[str]:
    if sys.platform.startswith("win"):
        return ["shutdown", "/s", "/t", "0"]
    if sys.platform == "darwin":
        return ["osascript", "-e", 'tell app "System Events" to shut down']
    return ["systemctl", "poweroff"]


class PowerDialog(QtWidgets.QDialog):
    """Завершение работы: закрыть программу или выключить компьютер."""

    EXIT, SHUTDOWN = "exit", "shutdown"

    def __init__(self, parent: QtWidgets.QWidget, printing: bool, heating: bool):
        super().__init__(parent, Qt.Dialog | Qt.FramelessWindowHint)
        self.setObjectName("PowerDialog")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setModal(True)
        self.choice: str | None = None
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(36, 30, 36, 30)
        lay.setSpacing(18)
        lay.addWidget(label("Завершение работы", "DialogTitle"))
        notes = []
        if printing:
            notes.append("Идёт печать — программа на принтере будет остановлена.")
        if heating:
            notes.append("При выключении компьютера нагрев стола будет выключен.")
        if notes:
            lay.addWidget(label("\n".join(notes), "CardHint", wrap=True))
        b_exit = button("Закрыть программу", "primary", lambda: self._pick(self.EXIT))
        b_off = button("⏻  Выключить компьютер", "danger", lambda: self._pick(self.SHUTDOWN))
        b_cancel = button("Отмена", None, self.reject)
        for b in (b_exit, b_off, b_cancel):
            b.setMinimumHeight(76)
            lay.addWidget(b)
        self.setFixedWidth(560)

    def _pick(self, choice: str):
        self.choice = choice
        self.accept()

    def showEvent(self, e):
        super().showEvent(e)
        p = self.parentWidget()
        if p is not None:
            self.adjustSize()
            c = p.mapToGlobal(p.rect().center())
            self.move(c.x() - self.width() // 2, c.y() - self.height() // 2)


class AboutDialog(QtWidgets.QDialog):
    """О программе: название ПО и версия."""

    def __init__(self, parent: QtWidgets.QWidget):
        super().__init__(parent, Qt.Dialog | Qt.FramelessWindowHint)
        self.setObjectName("PowerDialog")           # то же оформление, что у окна «Завершение работы»
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setModal(True)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(36, 30, 36, 30)
        lay.setSpacing(18)
        lay.addWidget(label("О программе", "DialogTitle"))
        bar = QtWidgets.QFrame()
        bar.setObjectName("BrandBar")
        bar.setFixedHeight(5)
        lay.addWidget(bar)
        form = QtWidgets.QGridLayout()
        form.setHorizontalSpacing(24)
        form.setVerticalSpacing(12)
        form.addWidget(label("Название ПО", "Muted"), 0, 0)
        form.addWidget(label(config.APP_NAME, "CardTitle"), 0, 1)
        form.addWidget(label("Версия", "Muted"), 1, 0)
        form.addWidget(label(config.APP_VERSION, "CardTitle"), 1, 1)
        form.setColumnStretch(1, 1)
        lay.addLayout(form)
        ok = button("Закрыть", "primary", self.accept)
        ok.setMinimumHeight(76)
        lay.addWidget(ok)
        self.setFixedWidth(560)

    def showEvent(self, e):
        super().showEvent(e)
        p = self.parentWidget()
        if p is not None:
            self.adjustSize()
            c = p.mapToGlobal(p.rect().center())
            self.move(c.x() - self.width() // 2, c.y() - self.height() // 2)


class MainWindow(QtWidgets.QWidget):
    def __init__(self, ctx: Ctx):
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle(config.APP_TITLE)
        ctx.navigate = self.go

        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── боковая панель ──
        side = QtWidgets.QFrame()
        side.setObjectName("Sidebar")
        side.setFixedWidth(260)
        sl = QtWidgets.QVBoxLayout(side)
        sl.setContentsMargins(16, 22, 16, 18)
        sl.setSpacing(6)
        sl.addWidget(label("БИОПРИНТЕР", "Brand"))
        sl.addWidget(label("ПАК трёхмерной биопечати", "BrandSub"))
        sl.addSpacing(14)
        bar = QtWidgets.QFrame()
        bar.setObjectName("BrandBar")
        bar.setFixedHeight(5)
        sl.addWidget(bar)
        sl.addSpacing(20)
        self.nav_group = QtWidgets.QButtonGroup(self)
        self.nav_buttons = {}
        # разделы — в прокрутке: на низком экране панель не растягивает окно (часы и кнопки видны)
        nav_w = QtWidgets.QWidget()
        nav_w.setObjectName("NavList")
        nav_l = QtWidgets.QVBoxLayout(nav_w)
        nav_l.setContentsMargins(0, 0, 0, 0)
        nav_l.setSpacing(6)
        for key, icon, text in NAV:
            b = QtWidgets.QPushButton(f"{icon}    {text}")
            b.setObjectName("NavButton")
            b.setCheckable(True)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(lambda _=False, k=key: self.go(k))
            self.nav_group.addButton(b)
            self.nav_buttons[key] = b
            nav_l.addWidget(b)
        nav_l.addStretch(1)
        nav_sa = QtWidgets.QScrollArea()
        nav_sa.setObjectName("NavScroll")
        nav_sa.setWidgetResizable(True)
        nav_sa.setFrameShape(QtWidgets.QFrame.NoFrame)
        nav_sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        nav_sa.setWidget(nav_w)
        QtWidgets.QScroller.grabGesture(nav_sa.viewport(), QtWidgets.QScroller.TouchGesture)
        sl.addWidget(nav_sa, 1)
        about = QtWidgets.QPushButton("ⓘ  О программе")
        about.setObjectName("SideButton")
        about.setFocusPolicy(Qt.NoFocus)
        about.setToolTip("Название и версия программы")
        about.clicked.connect(self.about)
        sl.addWidget(about)
        sl.addSpacing(8)
        sys_row = QtWidgets.QHBoxLayout()
        sys_row.setSpacing(8)
        self.theme_btn = QtWidgets.QPushButton()
        self.theme_btn.setObjectName("SideButton")
        self.theme_btn.setFocusPolicy(Qt.NoFocus)
        self.theme_btn.setToolTip("Светлая / тёмная тема")
        self.theme_btn.clicked.connect(lambda: self.set_theme("light" if theme.THEME == "dark" else "dark"))
        power = QtWidgets.QPushButton("⏻  Выход")
        power.setObjectName("SideButton")
        power.setFocusPolicy(Qt.NoFocus)
        power.setToolTip("Закрыть программу или выключить компьютер")
        power.clicked.connect(self.power_menu)
        self.theme_btn.setFixedWidth(76)
        self.theme_btn.setStyleSheet("font-size:24px;")
        sys_row.addWidget(self.theme_btn)
        sys_row.addWidget(power, 1)
        sl.addLayout(sys_row)
        sl.addSpacing(10)
        self.mode_lbl = label("", "BrandSub")
        self.mode_lbl.setWordWrap(True)
        sl.addWidget(self.mode_lbl)
        self.clock = label("", "Brand")
        # высота строки по шрифту — часы не обрезаются, даже если панели не хватает места
        self.clock.ensurePolished()
        self.clock.setMinimumHeight(self.clock.fontMetrics().height() + 8)
        self.clock.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)
        sl.addWidget(self.clock)
        root.addWidget(side)

        # ── правая часть ──
        right = QtWidgets.QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        top = QtWidgets.QFrame()
        top.setObjectName("TopBar")
        top.setFixedHeight(108)
        tl = QtWidgets.QHBoxLayout(top)
        tl.setContentsMargins(28, 0, 24, 0)
        tl.setSpacing(18)
        self.page_title = label("", "PageTitle")
        tl.addWidget(self.page_title)
        tl.addStretch(1)
        self.conn_pill = Pill("Нет связи", "danger", reserve=("Нет связи", "На связи", "Демо", "Нет данных"))
        self.job_pill = Pill("Готов", "idle", reserve=Job.TITLES.values())
        tl.addWidget(self.conn_pill, 0, Qt.AlignVCenter)
        tl.addWidget(self.job_pill, 0, Qt.AlignVCenter)
        self.r_x, self.r_y, self.r_z = (Readout(f"{a}, мм", "-000.00") for a in "XYZ")
        self.r_t = Readout("Стол, °C", "000")
        for r in (self.r_x, self.r_y, self.r_z, self.r_t):
            tl.addWidget(r)
        stop = button("■  Стоп", "stop", self.stop,
                      "Остановить программу печати и перемещение сопла")
        estop = button("АВАРИЙНЫЙ\nСТОП", "estop", self.emergency_stop,
                       "Остановить программу, все оси периферии и выключить нагрев стола")
        tl.addWidget(stop)
        tl.addWidget(estop)
        right.addWidget(top)

        self.stack = QtWidgets.QStackedWidget()
        self.cams = CamerasPage(ctx)
        self.pages = {
            "home": HomePage(ctx),
            "home_simple": SimpleHomePage(ctx),
            "print": PrintPage(ctx),
            "gen": GeneratorPage(ctx),
            "manual": ManualPage(ctx),
            "periph": PeripheryPage(ctx),
            "calib": CalibrationPage(ctx, self.reconnect),
            "cams": self.cams,
            "console": ConsolePage(ctx),
        }
        self.wraps = {}
        for k, p in self.pages.items():
            # страница в прокрутке: если экрану не хватит места (4K с масштабом, маленький монитор),
            # появится полоса прокрутки, а окно не станет больше экрана
            self.wraps[k] = page_scroll(p)
            self.stack.addWidget(self.wraps[k])
        right.addWidget(self.stack, 1)
        root.addLayout(right, 1)

        # подсказка «заблокировано во время печати»
        self.toast = QtWidgets.QLabel(self)
        self.toast.setObjectName("Toast")
        self.toast.setAlignment(Qt.AlignCenter)
        self.toast.hide()
        self._toast_timer = QtCore.QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self.toast.hide)
        ctx.link.blocked.connect(lambda _c: self.show_toast(
            "Идёт печать — перемещения заблокированы. Остановите печать кнопкой «Стоп»."))

        ctx.link.connected_changed.connect(self._conn)
        ctx.link.state_changed.connect(self._state)
        ctx.job.status_changed.connect(self._job)
        t = QtCore.QTimer(self)
        t.timeout.connect(lambda: self.clock.setText(time.strftime("%H:%M")))
        t.start(1000)
        self.clock.setText(time.strftime("%H:%M"))
        ctx.set_ui_mode = self.set_ui_mode
        ctx.set_theme = self.set_theme
        self._theme_btn_text()
        # Пульт подключается к шлюзу ещё во время ввода пароля — раньше, чем создано это окно,
        # и сигнал «на связи» уходил в никуда: индикаторы оставались «Нет связи».
        # Повторяем текущее состояние всем страницам, когда окно собрано.
        QtCore.QTimer.singleShot(0, lambda: ctx.link.connected_changed.emit(ctx.link.connected))
        # Индикатор в шапке проверяется и по таймеру: TCP-связь со шлюзом есть,
        # но телеметрия от платы 1 не приходит — «Нет данных».
        self._conn_timer = QtCore.QTimer(self)
        self._conn_timer.timeout.connect(self._conn_refresh)
        self._conn_timer.start(500)
        self._conn_refresh()
        self._mode()
        self.set_ui_mode(ctx.settings.ui_mode, save=False)

    SIMPLE_NAV = ("home", "print", "gen", "manual", "console")

    def set_ui_mode(self, mode: str, save: bool = True):
        """Простой / Расширенный интерфейс."""
        simple = mode == "simple"
        self.simple = simple
        if save:
            self.ctx.settings.ui_mode = mode
            self.ctx.settings.save()
        for key, b in self.nav_buttons.items():
            b.setVisible(not simple or key in self.SIMPLE_NAV)
        for page in self.pages.values():
            if hasattr(page, "set_simple"):
                page.set_simple(simple)
        cur = self.stack.currentWidget()
        key = next((k for k, w in self.wraps.items() if w is cur), "home")
        key = "home" if key == "home_simple" else key
        self.go(key if (not simple or key in self.SIMPLE_NAV) else "home")

    def go(self, key: str):
        if key == "home" and getattr(self, "simple", False):
            page = self.pages["home_simple"]
            self.stack.setCurrentWidget(self.wraps["home_simple"])
            self.nav_buttons["home"].setChecked(True)
            self.page_title.setText(page.title)
            return
        page = self.pages[key]
        self.stack.setCurrentWidget(self.wraps[key])
        self.nav_buttons[key].setChecked(True)
        self.page_title.setText(page.title)

    def set_theme(self, name: str, save: bool = True):
        """Светлая / тёмная тема — на лету, без перезапуска."""
        theme.apply_theme(name)
        if save:
            self.ctx.settings.theme = theme.THEME
            self.ctx.settings.save()
        self._theme_btn_text()
        for page in self.pages.values():
            f = getattr(page, "theme_synced", None)
            if callable(f):
                f(theme.THEME)

    def _theme_btn_text(self):
        self.theme_btn.setText("☀" if theme.THEME == "dark" else "☾")
        self.theme_btn.setToolTip("Светлая тема" if theme.THEME == "dark" else "Тёмная тема")

    def stop(self):
        """Стоп: остановить программу и перемещение (main M700 — шлюз останавливает обе платы)."""
        self.ctx.job.stop()

    def emergency_stop(self):
        """Аварийный стоп: программа, все оси периферии и нагрев стола."""
        self.ctx.job.emergency_stop()
        self.ctx.bed.force_off()

    def show_toast(self, text: str, ms: int = 3000):
        self.toast.setText(text)
        self.toast.adjustSize()
        w = min(self.width() - 80, self.toast.width() + 60)
        self.toast.resize(w, self.toast.height() + 24)
        self.toast.move((self.width() - w) // 2, 120)
        self.toast.raise_()
        self.toast.show()
        self._toast_timer.start(ms)

    def about(self):
        AboutDialog(self).exec_()

    def power_menu(self):
        job = self.ctx.job
        dlg = PowerDialog(self, job.active, self.ctx.bed.on)
        if dlg.exec_() != QtWidgets.QDialog.Accepted or not dlg.choice:
            return
        if job.active:
            job.stop()
        if dlg.choice == PowerDialog.SHUTDOWN:
            self.ctx.bed.force_off()
            self.ctx.link.drain()
            try:
                kw = {"creationflags": 0x08000000} if sys.platform.startswith("win") else {}
                subprocess.Popen(shutdown_command(), **kw)
            except OSError as e:
                QtWidgets.QMessageBox.warning(self, "Выключение", f"Не удалось выключить компьютер:\n{e}")
                return
        else:
            self.ctx.link.drain()
        self.close()

    def reconnect(self):
        self.ctx.link.start()
        self._mode()

    def _mode(self):
        s = self.ctx.settings
        self.mode_lbl.setText("ДЕМО-РЕЖИМ\nсимулятор" if s.demo else f"{s.host}:{s.port}")

    def _conn(self, _ok: bool = False):
        self._conn_refresh()

    def _conn_refresh(self):
        link = self.ctx.link
        if not link.connected:
            state = ("Нет связи", "danger", "Нет подключения к шлюзу tcp_to_udp")
        elif link.is_demo:
            state = ("Демо", "warn", "Демо-режим: симулятор вместо принтера")
        elif time.monotonic() - link.last_rx.get(1, 0) > NO_TELEMETRY_S:
            state = ("Нет данных", "warn", "Шлюз на связи, но телеметрия от платы 1 не приходит:\n"
                                           "проверьте питание плат и сеть 192.168.10.x")
        else:
            state = ("На связи", "ok", "Шлюз и плата 1 на связи")
        if state != getattr(self, "_conn_state", None):
            self._conn_state = state
            self.conn_pill.set(state[0], state[1])
            self.conn_pill.setToolTip(state[2])

    def _job(self, status: str):
        self.job_pill.set(Job.TITLES[status], Job.LEVELS[status])

    def _state(self, board, st):
        if board == 1:
            self.r_x.v.setText(f"{st.x:.2f}")
            self.r_y.v.setText(f"{st.y:.2f}")
            self.r_z.v.setText(f"{st.z:.2f}")
        if board == config.TEMP_SOURCE_BOARD:
            self.r_t.v.setText(f"{self.ctx.link.bed_temp():.0f}")

    def closeEvent(self, e):
        self.cams.stop()
        self.ctx.link.stop()
        if self.ctx.settings.gateway_stop_on_exit:
            self.ctx.gateway.stop(any_instance=True)
        super().closeEvent(e)
