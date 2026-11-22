"""Автозапуск шлюза tcp_to_udp и ожидание, пока всё выйдет на связь.

При старте пульта (не в демо-режиме):
1. если процесс tcp_to_udp.exe уже запущен (вручную) — второй не запускается.
   Порт шлюза пульт не «прощупывает»: шлюз обслуживает одного клиента, и пробное
   подключение заняло бы место пульта;
2. иначе запускается tcp_to_udp.exe из настроек (рабочая папка — его папка: там
   settings_printer.json), в своём окне консоли — его журнал виден инженеру;
3. окно «Подключение» ждёт: шлюз → связь с пультом → телеметрия платы 1 → платы 2;
   закрывается само, когда всё готово, или по кнопке «Продолжить без ожидания».
При выходе из пульта шлюз закрывается (настройка gateway_stop_on_exit) — и запущенный
пультом, и запущенный вручную, если он работает на этом же компьютере.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

from PyQt5 import QtCore, QtWidgets
from PyQt5.QtCore import Qt

from . import config
from .widgets import Led, button, label

LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1", "")


def process_running(path: str) -> bool:
    """Запущен ли процесс с таким именем файла (tcp_to_udp.exe)."""
    name = os.path.basename(path) or "tcp_to_udp.exe"
    try:
        if sys.platform.startswith("win"):
            out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {name}", "/NH", "/FO", "CSV"],
                                 capture_output=True, text=True, timeout=3,
                                 creationflags=0x08000000).stdout          # CREATE_NO_WINDOW
            return f'"{name.lower()}"' in out.lower()
        out = subprocess.run(["pgrep", "-f", name], capture_output=True, text=True, timeout=3)
        return out.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def kill_by_name(path: str) -> None:
    """Завершить все процессы шлюза с таким именем файла (в т.ч. запущенные вручную)."""
    name = os.path.basename(path) or "tcp_to_udp.exe"
    if not process_running(path):
        return
    try:
        if sys.platform.startswith("win"):
            subprocess.run(["taskkill", "/IM", name, "/T", "/F"], capture_output=True, timeout=5,
                           creationflags=0x08000000)                       # CREATE_NO_WINDOW
        else:
            subprocess.run(["pkill", "-f", name], capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        pass


class GatewayProcess:
    def __init__(self, settings: config.UserSettings):
        self.settings = settings
        self.proc: subprocess.Popen | None = None
        self.message = ""

    @property
    def path(self) -> str:
        return self.settings.gateway_path or config.DEFAULT_GATEWAY_PATH

    @property
    def started_by_us(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def applicable(self) -> bool:
        s = self.settings
        return not s.demo and s.gateway_autostart and s.host.strip().lower() in LOCAL_HOSTS

    def start(self, force: bool = False) -> bool:
        """Запустить шлюз, если нужно. True — шлюз запущен сейчас или уже работал."""
        if not force and not self.applicable():
            self.message = ""
            return False
        if self.started_by_us:
            self.message = "Шлюз уже запущен пультом"
            return True
        if process_running(self.path):
            self.message = "Шлюз уже работает (запущен вручную)"
            return True
        path = self.path
        if not os.path.isfile(path):
            self.message = f"Не найден шлюз: {path}\nУкажите путь на экране «Калибровка → Подключение»."
            return False
        kw = {}
        if sys.platform.startswith("win"):
            kw["creationflags"] = 0x00000010            # CREATE_NEW_CONSOLE: своё окно с журналом
        try:
            self.proc = subprocess.Popen([path], cwd=os.path.dirname(path), **kw)
        except OSError as e:
            self.message = f"Не удалось запустить шлюз:\n{e}"
            return False
        self.message = "Шлюз запущен"
        return True

    def stop(self, any_instance: bool = False):
        """Закрыть шлюз. any_instance — и запущенный не пультом (вручную, от прошлого сеанса),
        если шлюз локальный (адрес 127.0.0.1) и не демо-режим."""
        p = self.proc
        if p is not None and p.poll() is None:
            try:
                p.terminate()
                p.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    p.kill()
                except OSError:
                    pass
        self.proc = None
        s = self.settings
        if any_instance and not s.demo and s.host.strip().lower() in LOCAL_HOSTS:
            kill_by_name(self.path)

    def restart(self) -> bool:
        self.stop()
        time.sleep(0.5)
        return self.start(force=True)


class StartupDialog(QtWidgets.QDialog):
    """Ожидание: шлюз запущен → связь → плата 1 → плата 2."""

    def __init__(self, parent, ctx, gw: GatewayProcess):
        super().__init__(parent, Qt.Dialog | Qt.FramelessWindowHint)
        self.ctx, self.gw = ctx, gw
        self.setObjectName("PowerDialog")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setModal(True)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(36, 30, 36, 30)
        lay.setSpacing(16)
        lay.addWidget(label("Подключение к принтеру", "DialogTitle"))
        self.msg = label(gw.message or "Ожидание шлюза…", "CardHint", wrap=True)
        lay.addWidget(self.msg)
        self.leds = [Led("Шлюз tcp_to_udp работает"), Led("Связь пульта со шлюзом"),
                     Led("Плата 1 (кинематика) на связи"), Led("Плата 2 (периферия) на связи")]
        for l in self.leds:
            lay.addWidget(l)
        self.time_lbl = label("", "Muted")
        lay.addWidget(self.time_lbl)
        row = QtWidgets.QHBoxLayout()
        self.b_restart = button("Перезапустить шлюз", None, self._restart)
        row.addWidget(self.b_restart)
        row.addStretch(1)
        b = button("Продолжить без ожидания", "primary", self.accept)
        b.setMinimumHeight(72)
        row.addWidget(b)
        lay.addLayout(row)
        self.setFixedWidth(680)
        self.t0 = time.monotonic()
        self._proc_ok = False
        self._proc_checked = 0.0
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(250)
        self._tick()

    def _restart(self):
        self.gw.restart()
        self.msg.setText(self.gw.message)
        self.t0 = time.monotonic()

    def _tick(self):
        link = self.ctx.link
        now = time.monotonic()
        if not (self.gw.started_by_us or link.connected) and now - self._proc_checked > 2.0:
            self._proc_checked = now                     # tasklist небыстрый — не чаще раза в 2 с
            self._proc_ok = process_running(self.gw.path)
        gw_ok = self.gw.started_by_us or link.connected or self._proc_ok
        b1 = now - link.last_rx.get(1, 0) < 2.0
        b2 = now - link.last_rx.get(2, 0) < 2.0
        oks = [gw_ok, link.connected, b1, b2]
        for led, ok in zip(self.leds, oks):
            led.set(ok)
        left = config.GATEWAY_WAIT_S - (now - self.t0)
        if all(oks):
            self.timer.stop()
            QtCore.QTimer.singleShot(400, self.accept)
            return
        if left > 0:
            self.time_lbl.setText(f"Ожидание… ещё до {left:.0f} с")
        else:
            self.time_lbl.setText("Не всё вышло на связь. Проверьте питание плат и сеть, "
                                  "окно шлюза — или продолжите без ожидания.")

    def showEvent(self, e):
        super().showEvent(e)
        self.adjustSize()
        p = self.parentWidget()
        c = p.mapToGlobal(p.rect().center()) if p is not None else \
            QtWidgets.QApplication.primaryScreen().availableGeometry().center()
        self.move(c.x() - self.width() // 2, c.y() - self.height() // 2)
