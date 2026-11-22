"""Настройки пульта управления биопринтером.

Постоянные параметры железа лежат здесь. Пользовательские (адрес, демо-режим,
шприцы и материалы головок) сохраняются в settings.json рядом с программой.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field, asdict

APP_NAME = "Биопринт"
APP_VERSION = "1.0"
APP_TITLE = f"{APP_NAME} {APP_VERSION} — пульт управления биопринтером"
APP_DIR = os.path.dirname(os.path.abspath(__file__))          # модули и assets
# Настройки и снимки камер: рядом с программой. В собранном exe (PyInstaller) папка модулей
# временная/служебная, поэтому данные хранятся рядом с Bioprint.exe.
DATA_DIR = os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, "frozen", False) else APP_DIR
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
SNAPSHOT_DIR = os.path.join(DATA_DIR, "snapshots")
ICON_FILE = os.path.join(APP_DIR, "assets", "icon.png")


# ── Связь ──────────────────────────────────────────────────────────────
DEFAULT_HOST = "127.0.0.1"      # шлюз serv/tcp_to_udp.exe
DEFAULT_PORT = 62000
RECONNECT_MS = 1000

# ── Шлюз tcp_to_udp ────────────────────────────────────────────────────
# Пульт может сам запускать шлюз при старте. Путь по умолчанию — serv\tcp_to_udp.exe в папке
# oct_ui (рядом с папкой bioprint_ui); для exe-версии путь задаётся на экране «Калибровка».
# В exe-версии — папка serv рядом с папкой Bioprint.
_ROOT = os.path.dirname(DATA_DIR) if getattr(sys, "frozen", False) else os.path.dirname(APP_DIR)
DEFAULT_GATEWAY_PATH = os.path.join(_ROOT, "serv", "tcp_to_udp.exe")
GATEWAY_WAIT_S = 40             # сколько ждать при запуске, пока шлюз и обе платы выйдут на связь

# ── Камеры (UDP, JPEG-кадры, как CameraThreadUDP в String_line4) ───────
# ip — от кого принимать кадры (пусто — от любого адреса; можно несколько через запятую),
# port — UDP-порт приёма на этом ПК. Значения по умолчанию — как в оригинале;
# пользователь меняет их на экране «Камеры», они сохраняются в settings.json.
DEFAULT_CAMERA_HOST = "192.168.1.81"
DEFAULT_CAMERA_BIND = "0.0.0.0"     # сетевой интерфейс приёма: 0.0.0.0 — все
CAMERAS = [
    {"ip": DEFAULT_CAMERA_HOST, "port": 5000, "name": "Камера 1"},
    {"ip": DEFAULT_CAMERA_HOST, "port": 5001, "name": "Камера 2"},
    {"ip": DEFAULT_CAMERA_HOST, "port": 5002, "name": "Камера 3"},
]


def default_camera_settings() -> list:
    return [{"ip": c["ip"], "port": c["port"]} for c in CAMERAS]

# ── Оценка времени программы ───────────────────────────────────────────
TOOL_CHANGE_EST_S = 60.0        # смена головки (T): взять/отдать манипулятором (+ калибровка)
HOME_EST_S = 20.0               # базирование G28
E_SPIKE_WARN = 0.05             # с какого E в начале участка предупреждать о выбросе, мм

# ── Термостол ──────────────────────────────────────────────────────────
BED_TEMP_MIN, BED_TEMP_MAX, BED_TEMP_DEFAULT = 20, 50, 36   # ТУ п. 1.1.10: подложка (36 ± 1) °C   # как слайдер в String_line4
# Из какой строки телеметрии брать температуру стола: 1 = st1, 2 = st2.
# Нагревом управляет плата 2 (команды num2 M579), её датчик — в st2.
# Прошивка шлёт температуру ×100 (3712 = 37,12 °C), protocol.py уже делит на 100.
TEMP_SOURCE_BOARD = 2
TEMP_SCALE = 1.0                # temp_cur * TEMP_SCALE = °C

# ── Рабочая зона для превью, мм ────────────────────────────────────────
BED_DIAMETER_MM = 120

# ── Печатающие головки ─────────────────────────────────────────────────
# Головки 1–3 — сменные инструменты: на сопле стоит одна, остальные держат манипуляторы.
# Поршень любой головки на сопле двигает одна и та же ось E платы 1 (M587 I7).
# Где какая головка, шлюз сообщает в телеметрии; выдавливать можно только головкой на сопле.
# e_axis: (плата, номер оси) для M587; None — кнопки выдавливания неактивны.
HEADS = [
    {"name": "Головка 1", "e_axis": ("num1", 7)},
    {"name": "Головка 2", "e_axis": ("num1", 7)},
    {"name": "Головка 3", "e_axis": ("num1", 7)},
]
SYRINGE_VOLUMES = [3, 5, 10]
# Типовые внутренние диаметры шприцев (BD Luer-Lok), мм — для быстрого выбора в генераторе
SYRINGE_DIAMETERS = {3: 8.66, 5: 12.06, 10: 14.50}

MATERIALS = [
    "—",
    "Collagen Viscoll 80 мг/мл",
    "Альгинат натрия",
    "GelMA",
    "Желатин",
    "Pluronic F-127",
]

JOG_SPEED = 100000              # S-параметр M587 для периферии (как в оригинале)

# ── Ручное перемещение сопла ───────────────────────────────────────────
JOG_SPEED_MIN, JOG_SPEED_MAX, JOG_SPEED_DEFAULT = 0.1, 15.0, 10.0   # мм/с (M610), как ползунок jog_vel
JOG_STEPS = [0.1, 0.5, 1.0, 5.0, 10.0, 20.0]                        # шаги, мм; 20 — методика ТУ (скорость)

# ── Ручная экструзия (вкладка «Ручное») ─────────────────────────────
# ТУ п. 1.1.6: скорость экструзии от 0,1 до 10 мл/мин
EXTRUDE_RATE_MIN, EXTRUDE_RATE_MAX, EXTRUDE_RATE_DEFAULT = 0.001, 10.0, 0.1   # мл/мин
EXTRUDE_RATE_STEP = 0.01
EXTRUDE_RATE_PRESETS = [0.1, 0.5, 1.0, 2.0, 5.0, 10.0]                        # мл/мин, быстрый выбор
EXTRUDE_VOLUMES_UL = [1, 5, 10, 50, 100, 500, 1000, 5000]                    # мкл, «На объём»

# ── Коэффициенты печати (M618 скорость, M617 подача), % — как ползунки vel_koef / e_koef ──
FACTOR_MIN, FACTOR_MAX, FACTOR_STEP = 10, 340, 5

# ── Подложки и G54 ─────────────────────────────────────────────────────
# G54 X<i> выбирает подложку i: шлюз берёт её сдвиг из offset_plate_x/y/z[i] в
# settings_printer.json. Центр подложки — ноль X/Y программы, у разных подложек меняется Z.
G54_INDEX = {
    "petri35": 0, "petri60": 1, "petri90": 2,
    "plate6": 3, "plate12": 4, "plate24": 5, "plate48": 6, "plate96": 7,
}


@dataclass
class HeadSettings:
    syringe_ml: int = 5
    material: str = "—"


@dataclass
class UserSettings:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    demo: bool = False
    cameras_enabled: bool = False
    cameras: list = field(default_factory=default_camera_settings)   # [{"ip", "port"}] на каждую камеру
    camera_bind: str = DEFAULT_CAMERA_BIND
    bed_setpoint: int = BED_TEMP_DEFAULT
    heads: list = field(default_factory=lambda: [HeadSettings() for _ in HEADS])
    last_dir: str = ""
    ui_mode: str = "extended"   # интерфейс: "simple" (Простой) / "extended" (Расширенный)
    theme: str = "light"        # оформление: "light" (Светлая) / "dark" (Тёмная)
    # печать
    autocalibration: bool = False   # M641: печать в системе AUTO (калибровка сопла после смены головки)
    cover_lift: bool = False        # M643: снимать крышку при выдвижении/заборе стола
    speed_pct: int = 100            # M618
    flow_pct: int = 100             # M617
    # ручное
    jog_speed: float = JOG_SPEED_DEFAULT
    jog_step: float = 1.0
    jog_mode: str = "hold"          # "hold" — пока держишь, "step" — на шаг
    extrude_rate: float = EXTRUDE_RATE_DEFAULT   # мл/мин
    extrude_mode: str = "hold"      # "hold" — пока держишь, "volume" — на объём
    extrude_volume: float = 10.0    # мкл
    # периферия
    light: int = 0                  # освещение 0..255
    # шлюз
    gateway_path: str = ""          # пусто — DEFAULT_GATEWAY_PATH
    printer_settings_path: str = ""  # settings_printer.json шлюза; пусто — рядом с tcp_to_udp.exe
    gateway_autostart: bool = True
    gateway_stop_on_exit: bool = True
    # пароль входа: {"salt": hex, "hash": hex, "iter": n} — PBKDF2-SHA256 (см. auth.py)
    password: dict = field(default_factory=dict)

    @classmethod
    def load(cls) -> "UserSettings":
        s = cls()
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError):
            return s
        for k, v in raw.items():
            if k == "heads":
                heads = [HeadSettings(**h) for h in v][: len(HEADS)]
                while len(heads) < len(HEADS):
                    heads.append(HeadSettings())
                s.heads = heads
            elif k == "cameras":
                s.cameras = _cameras_from(v)
            elif k == "camera_host":          # старый формат: один адрес на все камеры
                if "cameras" not in raw:
                    for c in s.cameras:
                        c["ip"] = str(v)
            elif hasattr(s, k):
                setattr(s, k, v)
        return s

    def save(self) -> None:
        data = asdict(self)
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except OSError:
            pass


def _cameras_from(v) -> list:
    out = default_camera_settings()
    if isinstance(v, list):
        for i, c in enumerate(v[: len(out)]):
            if not isinstance(c, dict):
                continue
            out[i]["ip"] = str(c.get("ip", out[i]["ip"]))
            try:
                out[i]["port"] = int(c.get("port", out[i]["port"]))
            except (TypeError, ValueError):
                pass
    return out
