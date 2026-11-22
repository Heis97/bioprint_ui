"""Автосъёмка экранов для руководства пользователя.

    python -m bioprint_ui --screenshots [папка]

Пульт запускается в демо-режиме (без принтера и без шлюза, пароль не спрашивается),
сам обходит все экраны и основные окна, сохраняет снимки PNG в папку и закрывается.
Окно на экран не выводится: оно рисуется в памяти, поэтому длинные страницы снимаются
целиком, по всей высоте, а не только видимая часть.
Папка по умолчанию — screenshots рядом с программой. Настройки пульта (settings.json)
при этом не меняются: сохранение отключено на время съёмки.
"""
from __future__ import annotations

import os
import time
import traceback

from PyQt5 import QtCore, QtWidgets
from PyQt5.QtCore import Qt

from . import auth, config, theme

WIN_W, WIN_H = 1920, 1080
START_DELAY_MS = 2500       # демо-«принтер» успевает выйти на связь и прислать состояние
PAGE_DELAY_MS = 900         # страница дорисовывается (3D, таймеры обновления)
MAX_H = 4000                # предел высоты снимка длинной страницы


def default_dir() -> str:
    return os.path.join(config.DATA_DIR, "screenshots")


def prepare_settings(settings: config.UserSettings) -> None:
    """Демо-режим и никаких записей в settings.json."""
    settings.demo = True
    settings.save = lambda: None          # перекрываем метод только у этого экземпляра


class Shooter(QtCore.QObject):
    def __init__(self, win, ctx, out_dir: str):
        super().__init__(win)
        self.win, self.ctx, self.out = win, ctx, out_dir
        os.makedirs(out_dir, exist_ok=True)
        self.saved: list[str] = []
        self.errors: list[str] = []
        self.theme0 = theme.THEME
        self.mode0 = ctx.settings.ui_mode
        self.steps = self._plan()

    # ── план съёмки: (имя файла, действие перед снимком, что снимать) ──
    def _plan(self):
        w = self.win
        p = w.pages

        def page(key):
            return lambda: w.go(key)

        def to_print():
            w.go("gen")
            gen = p["gen"]
            gen._regen()
            gen._to_print()

        return [
            ("01_vhod", None, lambda: auth.PasswordDialog(self.ctx.settings, auth.PasswordDialog.LOGIN)),
            ("02_glavnaya_bez_zadaniya", lambda: w.set_ui_mode("extended", save=False) or w.go("home"), w),
            ("03_generator", page("gen"), w),
            ("04_pechat", to_print, w),
            ("05_pechat_gkod", None, lambda: _gcode_dialog(p["print"], self.ctx)),
            ("06_glavnaya", page("home"), w),
            ("07_ruchnoe", page("manual"), w),
            ("08_periferiya", page("periph"), w),
            ("09_kalibrovka", page("calib"), w),
            ("10_kamery", page("cams"), w),
            ("11_konsol", page("console"), w),
            ("12_nastroyki_printera", None, lambda: _printer_settings_dialog(w, self.ctx)),
            ("13_o_programme", None, lambda: _about_dialog(w)),
            ("14_zavershenie_raboty", None, lambda: _power_dialog(w, self.ctx)),
            ("15_podklyuchenie", None, lambda: _startup_dialog(w, self.ctx)),
            ("16_prostoy_glavnaya", lambda: (w.set_ui_mode("simple", save=False), w.go("home")), w),
            ("17_prostoy_generator", page("gen"), w),
            ("18_prostoy_pechat", page("print"), w),
            ("19_temnaya_tema", lambda: (w.set_ui_mode("extended", save=False), w.set_theme("dark", save=False),
                                         w.go("home")), w),
        ]

    def start(self):
        QtCore.QTimer.singleShot(START_DELAY_MS, self._next)

    def _next(self):
        if not self.steps:
            self._finish()
            return
        name, action, target = self.steps.pop(0)
        try:
            if action:
                action()
        except Exception:
            self.errors.append(f"{name}: подготовка\n{traceback.format_exc()}")
        QtCore.QTimer.singleShot(PAGE_DELAY_MS, lambda: self._shoot(name, target))

    def _shoot(self, name: str, target):
        dlg = None
        try:
            if callable(target) and not isinstance(target, QtWidgets.QWidget):
                dlg = target()                      # окно: показываем без exec_(), снимаем, закрываем
                dlg.setModal(False)
                dlg.setAttribute(Qt.WA_DontShowOnScreen, True)
                dlg.show()
                if not getattr(dlg, "keep_size", False):
                    dlg.adjustSize()
                _center(dlg, self.win)
                QtWidgets.QApplication.processEvents()
                QtCore.QTimer.singleShot(PAGE_DELAY_MS // 2, lambda: self._save(name, dlg, close=True))
                return
            if target is self.win:
                self._fit_page()
            self._save(name, target)
        except Exception:
            self.errors.append(f"{name}\n{traceback.format_exc()}")
            if dlg is not None:
                dlg.close()
            self._next()

    def _scroll_areas(self):
        cur = self.win.stack.currentWidget()
        areas = [cur] if isinstance(cur, QtWidgets.QScrollArea) else []
        areas += cur.findChildren(QtWidgets.QScrollArea)
        return [a for a in areas if a.isVisible() and a.verticalScrollBarPolicy() != Qt.ScrollBarAlwaysOff]

    def _fit_page(self):
        """Вытянуть окно по высоте, пока страница не поместится целиком (без прокрутки)."""
        h = self.win.height()
        for _ in range(8):
            QtWidgets.QApplication.processEvents()
            areas = self._scroll_areas()
            for a in areas:
                a.verticalScrollBar().setValue(0)
            extra = max((a.verticalScrollBar().maximum() for a in areas), default=0)
            if extra <= 0 or h >= MAX_H:
                break
            h = min(h + extra, MAX_H)
            self.win.resize(WIN_W, h)
        QtWidgets.QApplication.processEvents()

    def _save(self, name: str, widget, close: bool = False):
        try:
            QtWidgets.QApplication.processEvents()
            path = os.path.join(self.out, name + ".png")
            if widget.grab().save(path):
                self.saved.append(path)
            else:
                self.errors.append(f"{name}: не удалось записать {path}")
        except Exception:
            self.errors.append(f"{name}\n{traceback.format_exc()}")
        if close:
            widget.close()
            widget.deleteLater()
        if self.win.height() != WIN_H:
            self.win.resize(WIN_W, WIN_H)        # следующая страница — снова с обычной высоты
        self._next()

    def _finish(self):
        try:
            self.win.set_theme(self.theme0, save=False)
            self.win.set_ui_mode(self.mode0, save=False)
        except Exception:
            pass
        report = os.path.join(self.out, "otchet.txt")
        with open(report, "w", encoding="utf-8") as f:
            f.write(f"{config.APP_NAME} {config.APP_VERSION} — автосъёмка экранов, "
                    f"{time.strftime('%d.%m.%Y %H:%M')}\n\n")
            f.write(f"Сохранено снимков: {len(self.saved)}\n")
            for s in self.saved:
                f.write(f"  {os.path.basename(s)}\n")
            if self.errors:
                f.write(f"\nОшибки ({len(self.errors)}):\n\n" + "\n".join(self.errors))
        print(f"Снимков: {len(self.saved)}, ошибок: {len(self.errors)}. Папка: {self.out}")
        QtWidgets.QApplication.quit()


def _center(dlg, win):
    c = win.mapToGlobal(win.rect().center())
    dlg.move(c.x() - dlg.width() // 2, c.y() - dlg.height() // 2)


def _gcode_dialog(print_page, ctx):
    from .pages.print_page import GcodeEditor
    job = ctx.job
    dlg = GcodeEditor(print_page, job.program.text if job.program else "")
    dlg.keep_size = True
    return dlg


def _printer_settings_dialog(win, ctx):
    from .printer_settings import PrinterSettingsDialog
    dlg = PrinterSettingsDialog(win, ctx)
    dlg.resize(int(win.width() * 0.9), int(win.height() * 0.9))
    dlg.keep_size = True
    return dlg


def _about_dialog(win):
    from .main_window import AboutDialog
    return AboutDialog(win)


def _power_dialog(win, ctx):
    from .main_window import PowerDialog
    return PowerDialog(win, False, False)


def _startup_dialog(win, ctx):
    from .gateway import StartupDialog
    return StartupDialog(win, ctx, ctx.gateway)


def show_offscreen(win) -> None:
    """Показать главное окно «в памяти»: без вывода на экран, размер не ограничен экраном."""
    win.setAttribute(Qt.WA_DontShowOnScreen, True)
    win.resize(WIN_W, WIN_H)
    win.show()


def run(win, ctx, out_dir: str) -> Shooter:
    print(f"Идёт съёмка экранов (около минуты), окно пульта не показывается. Папка: {out_dir}")
    s = Shooter(win, ctx, out_dir)
    s.start()
    return s
