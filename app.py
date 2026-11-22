"""Точка входа.

Проще всего — открыть start.py в VS Code и нажать F5.
Из командной строки (из корня репозитория):
    python -m bioprint_ui            — подключение к контроллеру
    python -m bioprint_ui --demo     — встроенный симулятор без железа
    python -m bioprint_ui --window   — в окне (по умолчанию на весь экран)
    python -m bioprint_ui --screenshots [папка] — автосъёмка всех экранов для руководства
"""
from __future__ import annotations

import os
import sys

if __name__ == "__main__" and not __package__:      # запуск app.py напрямую (F5)
    import importlib
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    importlib.import_module("bioprint_ui")
    __package__ = "bioprint_ui"

from PyQt5 import QtCore, QtGui, QtWidgets

from . import auth, config, screenshots, theme
from .context import Ctx
from .gateway import StartupDialog
from .main_window import MainWindow


def _screenshots_dir(argv: list) -> str | None:
    """--screenshots [папка] → папка для снимков; None — обычный запуск."""
    if "--screenshots" not in argv:
        return None
    i = argv.index("--screenshots")
    nxt = argv[i + 1] if i + 1 < len(argv) else ""
    return os.path.abspath(nxt) if nxt and not nxt.startswith("--") else screenshots.default_dir()


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    settings = config.UserSettings.load()
    shots = _screenshots_dir(argv)
    if shots:
        screenshots.prepare_settings(settings)      # демо, settings.json не трогаем
    # Масштаб: дробный масштаб Windows (125/150 % на 4K) — без округления до ×2.
    # Собственного масштаба у пульта нет (убран 20.10: при 150 % часть кнопок уходила за экран);
    # старое значение ui_scale в settings.json игнорируется.
    try:
        QtWidgets.QApplication.setHighDpiScaleFactorRoundingPolicy(
            QtCore.Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    except AttributeError:                          # Qt < 5.14
        pass
    QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling, True)
    QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_SynthesizeMouseForUnhandledTouchEvents, True)
    app = QtWidgets.QApplication(argv)
    app.setStyle("Fusion")
    app.setApplicationName(config.APP_NAME)
    app.setApplicationVersion(config.APP_VERSION)
    if os.path.exists(config.ICON_FILE):
        app.setWindowIcon(QtGui.QIcon(config.ICON_FILE))
    body, _ = theme.pick_fonts()
    app.setFont(QtGui.QFont(body, 12))
    theme.set_theme(settings.theme)
    app.setStyleSheet(theme.build_qss())

    if "--demo" in argv:
        settings.demo = True
    ctx = Ctx(settings)
    if shots:
        ctx.link.start()
        win = MainWindow(ctx)
        win._mode()
        screenshots.show_offscreen(win)
        win._shooter = screenshots.run(win, ctx, shots)
        code = app.exec_()
        ctx.link.stop()
        return code
    # шлюз запускается сразу — пока вводят пароль, он успевает подняться
    ctx.gateway.start()
    ctx.link.start()
    if not auth.login(settings):
        ctx.link.stop()
        if settings.gateway_stop_on_exit:
            ctx.gateway.stop(any_instance=True)
        return 0
    win = MainWindow(ctx)
    win._mode()
    if "--window" in argv:
        win.resize(1920, 1080)
        win.show()
    else:
        win.showFullScreen()
    if not settings.demo and (ctx.gateway.applicable() or ctx.gateway.message):
        QtCore.QTimer.singleShot(300, lambda: StartupDialog(win, ctx, ctx.gateway).exec_())
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
