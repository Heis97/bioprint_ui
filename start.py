"""Запуск пульта управления биопринтером.

Откройте этот файл в Visual Studio Code и нажмите F5.
Виртуальное окружение не нужно: при первом запуске недостающие библиотеки
(PyQt5, opencv-python, numpy) ставятся в тот Python, которым запущен файл.

Режим запуска меняется константами ниже. Ключи командной строки: --demo — симулятор,
--fullscreen — на весь экран (так запускает ярлык на рабочем столе).
"""

DEMO = False          # True — встроенный симулятор без принтера
FULLSCREEN = False    # True — на весь экран (для сенсорной панели)

# ─────────────────────────────────────────────────────────────────────────
import importlib
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)           # папка, в которой лежит bioprint_ui
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

REQUIRED = {             # модуль для import : пакет для pip
    "PyQt5": "PyQt5",
    "numpy": "numpy",
    "cv2": "opencv-python",
}


def _missing() -> list:
    out = []
    for mod, pkg in REQUIRED.items():
        try:
            importlib.import_module(mod)
        except ImportError:
            out.append(pkg)
    return out


def ensure_dependencies() -> None:
    need = _missing()
    if not need:
        return
    print(f"Устанавливаю библиотеки: {', '.join(need)} …")
    base = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check"]
    for extra in ([], ["--user"]):          # если нет прав на системную папку — ставим пользователю
        if subprocess.call(base + extra + need) == 0:
            break
    importlib.invalidate_caches()
    if _missing():
        import site
        user_site = site.getusersitepackages()
        if user_site not in sys.path:
            sys.path.append(user_site)
    still = _missing()
    if "PyQt5" in still:
        sys.exit("Не удалось установить PyQt5. Выполните в терминале:\n"
                 f'  "{sys.executable}" -m pip install PyQt5 opencv-python numpy')
    if still:
        print(f"Без {', '.join(still)} камеры работать не будут, остальное — работает.")


if __name__ == "__main__":
    os.chdir(ROOT)
    ensure_dependencies()
    from bioprint_ui.app import main

    args = [sys.argv[0]] + [a for a in sys.argv[1:] if a != "--fullscreen"]
    if DEMO and "--demo" not in args:
        args.append("--demo")
    if not FULLSCREEN and "--fullscreen" not in sys.argv:
        args.append("--window")
    sys.exit(main(args))
