"""Точка входа собранного Bioprint.exe (PyInstaller, см. bioprint.spec).

По умолчанию — на весь экран. Ключи (можно дописать в ярлык после пути к exe):
    --window   в окне 1920×1080
    --demo     встроенный симулятор без принтера
    --screenshots [папка]  автосъёмка экранов для руководства (демо, затем выход)

Окна консоли у exe нет, поэтому необработанные ошибки пишутся в bioprint_error.log
рядом с exe и показываются сообщением.
"""
import os
import sys
import time
import traceback

from bioprint_ui import config


def _log_error(exc_type, exc, tb):
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    try:
        with open(os.path.join(config.DATA_DIR, "bioprint_error.log"), "a", encoding="utf-8") as f:
            f.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} {config.APP_NAME} {config.APP_VERSION}\n{text}")
    except OSError:
        pass
    try:
        from PyQt5 import QtWidgets
        if QtWidgets.QApplication.instance():
            QtWidgets.QMessageBox.critical(None, config.APP_NAME,
                                           f"Ошибка программы:\n{exc}\n\nПодробности — в bioprint_error.log")
    except Exception:
        pass


if __name__ == "__main__":
    sys.excepthook = _log_error
    from bioprint_ui.app import main
    sys.exit(main())
