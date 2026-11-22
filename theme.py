"""Оформление в фирменном стиле Университета МИСИС.

Источник: «Руководство по использованию фирменного стиля» (misis.ru/university/rebranding).
  Голубой       #37EBFF  — цвет фирменного градиента
  Синий         #0541F0  — цвет фирменного градиента
  Тёмно-синий   #0A1E64  — фоновый цвет
  Тёмно-серый   #505569  — основной цвет текста
  Белый         #FFFFFF  — основной фоновый цвет
Шрифты: Nekst (заголовки), TT Norms Pro (текст) — коммерческие; для цифровых и
офисных материалов брендбук допускает Tahoma. Интерфейс берёт первый
установленный шрифт из списков ниже.

Статусные цвета (зелёный, оранжевый, красный) — функциональные, для безопасности.
"""
import os

from PyQt5 import QtGui, QtWidgets, sip

BODY_FONTS = ["TT Norms Pro", "Tahoma", "Segoe UI", "DejaVu Sans", "Arial"]
HEAD_FONTS = ["Nekst", "TT Norms Pro", "Tahoma", "Segoe UI", "DejaVu Sans", "Arial"]

_CHECK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "check.svg").replace("\\", "/")

BRAND = {
    "cyan": "#37EBFF",
    "blue": "#0541F0",
    "navy": "#0A1E64",
    "gray": "#505569",
    "white": "#FFFFFF",
}

LIGHT = {
    "bg": "#F1F4FA",
    "surface": "#FFFFFF",
    "surface2": "#F4F6FB",
    "border": "#D8DEEB",
    "text": BRAND["gray"],          # основной текст
    "strong": BRAND["navy"],        # заголовки и значения
    "muted": "#858AA0",
    "accent": BRAND["blue"],
    "accent_hover": "#0433C4",
    "accent_soft": "#E3EAFF",
    "cyan": BRAND["cyan"],
    "nav": BRAND["navy"],
    "nav_text": "#AEBCE3",
    "ok": "#159A67",
    "ok_soft": "#DDF4EA",
    "warn": "#D08300",
    "warn_soft": "#FCF0D9",
    "danger": "#E0312B",
    "danger_hover": "#B8241F",
    "danger_soft": "#FCE3E1",
    "disabled": "#BCC3D3",
    "idle_soft": "#E8ECF1",
    "grid": "#EDF0F4",          # сетка стола в превью
    "grid3d": "#E2E7F1",        # сетка стола в 3D
    "guide": "#C5CCD6",         # перекрестие нуля
    "travel": "#B7BFCA",        # холостые перемещения
    "marker_bg": "#FFFFFF",     # заливка маркера сопла
    "cam_bg": "#0F1720",
}

# Тёмная тема: фон — фирменный тёмно-синий МИСИС, акценты ярче для контраста
DARK = {
    "bg": "#07122F",
    "surface": "#0E1C45",
    "surface2": "#15265A",
    "border": "#263A78",
    "text": "#C3CCE8",
    "strong": "#F2F5FF",
    "muted": "#8D99C4",
    "accent": "#3D6BFF",
    "accent_hover": "#2A55E6",
    "accent_soft": "#1D3180",
    "cyan": BRAND["cyan"],
    "nav": "#040B22",
    "nav_text": "#AEBCE3",
    "ok": "#2BC48A",
    "ok_soft": "#0F3B34",
    "warn": "#F2A93B",
    "warn_soft": "#3D300F",
    "danger": "#FF4D46",
    "danger_hover": "#D93A34",
    "danger_soft": "#461A22",
    "disabled": "#44508A",
    "idle_soft": "#1B2A5E",
    "grid": "#172A60",
    "grid3d": "#1C3068",
    "guide": "#3B4F8F",
    "travel": "#56659C",
    "marker_bg": "#0E1C45",
    "cam_bg": "#030916",
}

THEMES = {"light": LIGHT, "dark": DARK}
THEME = "light"
C = dict(LIGHT)      # текущие цвета; меняются на месте, поэтому все модули видят новые

GRADIENT = f"qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {BRAND['blue']}, stop:1 {BRAND['cyan']})"

# цвета головок (превью, карточки) — одинаковые во всём интерфейсе
HEAD_COLORS = [BRAND["blue"], "#00A6C4", "#8B5CF6"]

BODY_FONT = "Tahoma"
HEAD_FONT = "Tahoma"


def pick_fonts() -> tuple[str, str]:
    """Выбрать установленные шрифты (нужен созданный QApplication)."""
    global BODY_FONT, HEAD_FONT
    fams = set(QtGui.QFontDatabase().families())
    BODY_FONT = next((f for f in BODY_FONTS if f in fams), "Arial")
    HEAD_FONT = next((f for f in HEAD_FONTS if f in fams), BODY_FONT)
    return BODY_FONT, HEAD_FONT


_styled: list = []       # (виджет, функция → стиль) — пересобираются при смене темы


def styled(w: QtWidgets.QWidget, make) -> QtWidgets.QWidget:
    """Задать виджету стиль, зависящий от цветов темы (обновится при переключении)."""
    w.setStyleSheet(make())
    _styled.append((w, make))
    return w


def set_theme(name: str) -> None:
    """Сменить палитру (до создания окна или вместе с apply_theme)."""
    global THEME
    THEME = name if name in THEMES else "light"
    C.clear()
    C.update(THEMES[THEME])


def apply_theme(name: str) -> None:
    """Переключить тему на лету: общий стиль, стили отдельных виджетов, перерисовка."""
    set_theme(name)
    app = QtWidgets.QApplication.instance()
    if app is None:
        return
    app.setStyleSheet(build_qss())
    alive = []
    for w, make in _styled:
        if not sip.isdeleted(w):
            w.setStyleSheet(make())
            alive.append((w, make))
    _styled[:] = alive
    for w in app.allWidgets():
        f = getattr(w, "theme_changed", None)
        if callable(f):
            f()
        w.update()


def build_qss() -> str:
    b, h = BODY_FONT, HEAD_FONT
    return f"""
QWidget {{ background: {C['bg']}; color: {C['text']}; font-family: "{b}"; font-size: 19px; }}
QLabel {{ background: transparent; }}

/* ── навигация ── */
#Sidebar {{ background: {C['nav']}; }}
#NavScroll, #NavList, #NavScroll > QWidget > QWidget {{ background: transparent; }}
#Sidebar QLabel {{ color: #FFFFFF; }}
#Brand {{ font-family: "{h}"; font-size: 24px; font-weight: 700; color: #FFFFFF; }}
#BrandSub {{ font-size: 14px; color: {C['nav_text']}; }}
#BrandBar {{ background: {GRADIENT}; border-radius: 3px; }}
QPushButton#NavButton {{
    background: transparent; color: {C['nav_text']}; border: none; border-radius: 12px;
    text-align: left; padding: 0 20px; font-size: 20px; min-height: 66px;
}}
QPushButton#NavButton:checked {{ background: {GRADIENT}; color: #FFFFFF; font-weight: 700; }}
QPushButton#NavButton:pressed {{ background: #16307F; }}

/* ── верхняя панель ── */
#TopBar {{ background: {C['surface']}; border-bottom: 1px solid {C['border']}; }}
#PageTitle {{ font-family: "{h}"; font-size: 30px; font-weight: 700; color: {C['strong']}; }}
#ReadoutName {{ font-size: 14px; color: {C['muted']}; }}
#ReadoutValue {{ font-size: 24px; font-weight: 700; color: {C['strong']}; }}

/* ── карточки ── */
QFrame#Card {{ background: {C['surface']}; border: 1px solid {C['border']}; border-radius: 16px; }}
QFrame#Card QLabel {{ background: transparent; }}
#CardTitle {{ font-family: "{h}"; font-size: 21px; font-weight: 700; color: {C['strong']}; }}
#CardHint {{ font-size: 15px; color: {C['muted']}; }}
#Muted {{ color: {C['muted']}; font-size: 16px; }}
#BigValue {{ font-size: 44px; font-weight: 700; color: {C['strong']}; }}
#CoordValue {{ font-size: 36px; font-weight: 700; color: {C['strong']}; }}
#MidValue {{ font-size: 28px; font-weight: 700; color: {C['strong']}; }}
#TileName {{ font-size: 15px; color: {C['muted']}; }}
#TileValue {{ font-size: 28px; font-weight: 700; color: {C['strong']}; }}

/* ── кнопки ── */
QPushButton {{
    background: {C['surface2']}; color: {C['strong']}; border: 1px solid {C['border']};
    border-radius: 12px; min-height: 64px; padding: 0 20px; font-size: 19px; font-weight: 700;
}}
QPushButton:pressed {{ background: {C['accent_soft']}; border-color: {C['accent']}; }}
QPushButton:disabled {{ color: {C['disabled']}; background: {C['surface2']}; border-color: {C['border']}; }}
QPushButton[kind="primary"] {{ background: {C['accent']}; color: #FFFFFF; border: none; }}
QPushButton[kind="primary"]:pressed {{ background: {C['accent_hover']}; }}
QPushButton[kind="primary"]:disabled {{ background: {C['disabled']}; color: #FFFFFF; }}
QPushButton[kind="danger"] {{ background: {C['danger']}; color: #FFFFFF; border: none; }}
QPushButton[kind="danger"]:pressed {{ background: {C['danger_hover']}; }}
QPushButton[kind="danger"]:disabled {{ background: {C['disabled']}; color: #FFFFFF; }}
QPushButton[kind="estop"] {{
    background: {C['danger']}; color: #FFFFFF; border: 3px solid #9E1A15; border-radius: 14px;
    font-size: 20px; font-weight: 800; min-height: 80px; padding: 0 18px;
}}
QPushButton[kind="estop"]:pressed {{ background: #9E1A15; }}
QPushButton[kind="stop"] {{
    background: {C['warn_soft']}; color: {C['warn']}; border: 3px solid {C['warn']}; border-radius: 14px;
    font-size: 20px; font-weight: 800; min-height: 80px; padding: 0 18px;
}}
QPushButton[kind="stop"]:pressed {{ background: {C['warn']}; color: #FFFFFF; }}
QPushButton#SideButton {{
    background: transparent; color: {C['nav_text']}; border: 1px solid #2A3F86; border-radius: 12px;
    min-height: 60px; padding: 0 4px; font-size: 17px; font-weight: 700;
}}
QPushButton#SideButton:pressed {{ background: #16307F; color: #FFFFFF; }}
QLabel#Toast {{ background: {C['warn_soft']}; color: {C['warn']}; border: 2px solid {C['warn']};
    border-radius: 14px; font-size: 20px; font-weight: 700; padding: 8px 24px; }}
QDialog#PowerDialog {{ background: {C['bg']}; border: 2px solid {C['border']}; border-radius: 18px; }}
#DialogTitle {{ font-family: "{h}"; font-size: 28px; font-weight: 700; color: {C['strong']}; }}
QPushButton[kind="jog"] {{ font-size: 28px; min-width: 96px; min-height: 96px; border-radius: 16px; }}
QPushButton[kind="jog"][active="true"] {{ background: {C['accent']}; color: #FFFFFF; border-color: {C['accent']}; }}
QPushButton[kind="hold"][active="true"] {{ background: {C['accent']}; color: #FFFFFF; border-color: {C['accent']}; }}
QPushButton[kind="toggle"]:checked {{ background: {C['accent']}; color: #FFFFFF; border-color: {C['accent']}; }}
QPushButton[kind="seg"] {{ min-height: 56px; border-radius: 10px; padding: 0 14px; }}
QPushButton[kind="seg"]:checked {{ background: {C['accent']}; color: #FFFFFF; border-color: {C['accent']}; }}
QPushButton[kind="step"] {{ min-width: 64px; max-width: 64px; font-size: 28px; padding: 0; }}
QPushButton[kind="small"] {{ min-height: 52px; min-width: 52px; padding: 0 12px; font-size: 17px; border-radius: 10px; }}
QPushButton[kind="small"]:checked {{ background: {C['accent']}; color: #FFFFFF; border-color: {C['accent']}; }}

/* ── поля ── */
QLineEdit, QComboBox, QSpinBox {{
    background: {C['surface']}; border: 1px solid {C['border']}; border-radius: 10px;
    min-height: 56px; padding: 0 14px; font-size: 19px; color: {C['strong']};
}}
QLineEdit:focus, QComboBox:focus {{ border: 2px solid {C['accent']}; }}
QComboBox::drop-down {{ width: 44px; border: none; }}
QComboBox QAbstractItemView {{ font-size: 19px; background: {C['surface']}; selection-background-color: {C['accent_soft']}; selection-color: {C['strong']}; }}
QPlainTextEdit {{
    background: {C['surface']}; border: 1px solid {C['border']}; border-radius: 10px; color: {C['strong']};
    font-family: "Consolas", "DejaVu Sans Mono", monospace; font-size: 16px; padding: 6px;
}}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: {C['surface2']}; border: 1px solid {C['border']}; padding: 12px 24px; font-size: 17px; font-weight: 700; color: {C['text']}; border-radius: 10px; margin-right: 8px; }}
QTabBar::tab:selected {{ background: {C['accent']}; color: #FFFFFF; border-color: {C['accent']}; }}
QProgressBar {{
    background: {C['surface2']}; border: 1px solid {C['border']}; border-radius: 10px; color: {C['strong']};
    min-height: 26px; max-height: 26px; text-align: center; font-size: 15px; font-weight: 700;
}}
QProgressBar::chunk {{ background: {GRADIENT}; border-radius: 9px; }}
QSlider {{ background: transparent; }}
QSlider::groove:horizontal {{ height: 10px; background: {C['border']}; border-radius: 5px; }}
QSlider::sub-page:horizontal {{ background: {C['accent']}; border-radius: 5px; }}
QSlider::handle:horizontal {{ width: 40px; height: 40px; margin: -16px 0; border-radius: 20px; background: {C['surface']}; border: 3px solid {C['accent']}; }}
QSlider::groove:vertical {{ width: 10px; background: {C['border']}; border-radius: 5px; }}
QSlider::add-page:vertical {{ background: {C['accent']}; border-radius: 5px; }}
QSlider::handle:vertical {{ width: 40px; height: 40px; margin: 0 -16px; border-radius: 20px; background: {C['surface']}; border: 3px solid {C['accent']}; }}
QCheckBox {{ spacing: 14px; font-size: 18px; background: transparent; color: {C['strong']}; }}
QCheckBox::indicator {{ width: 34px; height: 34px; border-radius: 8px; border: 2px solid {C['border']}; background: {C['surface']}; }}
QCheckBox::indicator:checked {{ background: {C['accent']}; border-color: {C['accent']}; image: url({_CHECK}); }}
QScrollBar:vertical {{ width: 16px; background: transparent; }}
QScrollBar::handle:vertical {{ background: {C['border']}; border-radius: 8px; min-height: 60px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QTableWidget {{ background: {C['surface']}; border: none; gridline-color: {C['border']}; font-size: 16px; color: {C['strong']}; }}
QHeaderView::section {{ background: {C['surface2']}; border: none; border-bottom: 1px solid {C['border']}; padding: 8px; font-size: 15px; color: {C['muted']}; }}
QToolTip {{ background: {C['nav']}; color: #FFFFFF; border: none; padding: 8px; font-size: 16px; }}
QMessageBox QLabel {{ font-size: 19px; color: {C['strong']}; }}
QMessageBox QPushButton {{ min-width: 140px; }}
"""
