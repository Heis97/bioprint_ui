# -*- mode: python ; coding: utf-8 -*-
# Сборка Bioprint.exe (PyInstaller ≥ 6). Проще всего — запустить build_exe.bat.
# Вручную, из папки bioprint_ui:
#     python -m PyInstaller --noconfirm --clean bioprint.spec
# Результат: dist\Bioprint\Bioprint.exe (папка целиком — это программа).
#
# Сборка «папкой» (onedir), а не одним файлом: запускается за 1–2 с, а не 10–20
# (one-file каждый раз распаковывает ~200 МБ во временную папку), реже ловится
# антивирусом, а settings.json и снимки камер хранятся рядом с exe.

import os

HERE = SPECPATH                       # папка bioprint_ui
ROOT = os.path.dirname(HERE)          # папка, в которой лежит bioprint_ui (пакет)

a = Analysis(
    [os.path.join(HERE, "exe_main.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[
        (os.path.join(HERE, "assets"), "bioprint_ui/assets"),     # check.svg (галочки), значок
    ],
    hiddenimports=[
        "PyQt5.QtSvg",                # svg-галочки в стилях (плагин imageformats/qsvg)
        "PyQt5.sip",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # не используются пультом — меньше размер
        "tkinter", "matplotlib", "scipy", "pandas", "IPython", "PIL",
        "PyQt5.QtWebEngineWidgets", "PyQt5.QtWebEngineCore", "PyQt5.QtQml", "PyQt5.QtQuick",
        "PyQt5.Qt3DCore", "PyQt5.QtMultimedia", "PyQt5.QtBluetooth", "PyQt5.QtLocation",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Bioprint",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                        # UPX часто даёт ложные срабатывания антивирусов
    console=False,                    # без чёрного окна консоли
    disable_windowed_traceback=False,
    icon=os.path.join(HERE, "assets", "icon.ico"),
    version=os.path.join(HERE, "version_info.txt"),   # «Свойства → Подробно»: Биопринт 1.0
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Bioprint",
)
