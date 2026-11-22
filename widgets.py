"""Сенсорные виджеты: кнопки удержания, переключатели, степперы, карточки."""
from __future__ import annotations

from typing import Callable

from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtCore import Qt

from .theme import C, styled


def _repolish(w: QtWidgets.QWidget):
    w.style().unpolish(w)
    w.style().polish(w)
    w.update()


def _text_w(w: QtWidgets.QWidget, texts) -> int:
    w.ensurePolished()
    fm = w.fontMetrics()
    return max((fm.horizontalAdvance(t) for t in texts if t), default=0)


class ValueLabel(QtWidgets.QLabel):
    """Значение с зарезервированной шириной: блок не меняет размер при смене цифр.

    reserve — самые длинные ожидаемые строки (например "-000.00"). Если текст всё же
    длиннее резерва, метка расширяется: цифры всегда видны полностью.
    """

    def __init__(self, text: str = "", reserve=(), obj: str | None = None,
                 align=Qt.AlignLeft | Qt.AlignVCenter):
        super().__init__(text)
        if obj:
            self.setObjectName(obj)
        self._reserve = [reserve] if isinstance(reserve, str) else list(reserve)
        self.setAlignment(align)
        self.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Preferred)

    def set_reserve(self, *texts):
        self._reserve = list(texts)
        self.updateGeometry()

    def _w(self) -> int:
        return _text_w(self, self._reserve + [self.text()]) + 6

    def sizeHint(self):
        return QtCore.QSize(self._w(), super().sizeHint().height())

    def minimumSizeHint(self):
        return QtCore.QSize(self._w(), super().minimumSizeHint().height())


class ElidedLabel(QtWidgets.QLabel):
    """Длинный текст (имя файла): занимает доступную ширину, лишнее — «…», полный текст — в подсказке."""

    def __init__(self, text: str = "", obj: str | None = None):
        super().__init__()
        if obj:
            self.setObjectName(obj)
        self._full = ""
        self.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        self.setMinimumWidth(10)
        self.set_text(text)

    def set_text(self, text: str):
        self._full = text
        self.setToolTip(text)
        self._elide()

    def full_text(self) -> str:
        return self._full

    def _elide(self):
        fm = self.fontMetrics()
        super().setText(fm.elidedText(self._full, Qt.ElideMiddle, max(10, self.width() - 4)))

    def resizeEvent(self, e):
        self._elide()
        super().resizeEvent(e)

    def changeEvent(self, e):
        if e.type() in (QtCore.QEvent.FontChange, QtCore.QEvent.StyleChange):
            self._elide()
        super().changeEvent(e)


class ReserveButton(QtWidgets.QPushButton):
    """Кнопка, у которой меняется надпись: ширина рассчитана на самую длинную."""

    def __init__(self, text: str = "", reserve=()):
        super().__init__(text)
        self._reserve = list(reserve)

    def _w(self) -> int:
        return _text_w(self, self._reserve + [self.text()]) + 48

    def sizeHint(self):
        sh = super().sizeHint()
        return QtCore.QSize(max(sh.width(), self._w()), sh.height())

    def minimumSizeHint(self):
        sh = super().minimumSizeHint()
        return QtCore.QSize(max(sh.width(), self._w()), sh.height())


def button(text: str, kind: str | None = None, on_click: Callable | None = None,
           tooltip: str = "", reserve=()) -> QtWidgets.QPushButton:
    b = ReserveButton(text, reserve) if reserve else QtWidgets.QPushButton(text)
    if kind:
        b.setProperty("kind", kind)
    if on_click:
        b.clicked.connect(lambda _=False: on_click())
    if tooltip:
        b.setToolTip(tooltip)
    b.setFocusPolicy(Qt.NoFocus)
    return b


class HoldButton(QtWidgets.QPushButton):
    """Кнопка «пока держишь — едет»: callback(True) при нажатии, (False) при отпускании."""

    def __init__(self, text: str, callback: Callable[[bool], None], kind: str = "hold"):
        super().__init__(text)
        self.setProperty("kind", kind)
        self.setProperty("active", False)
        self.setFocusPolicy(Qt.NoFocus)
        self._cb = callback
        self._held = False
        self.pressed.connect(self._down)
        self.released.connect(self._up)

    def _down(self):
        self._held = True
        self.setProperty("active", True)
        _repolish(self)
        self._cb(True)

    def _up(self):
        if not self._held:
            return
        self._held = False
        self.setProperty("active", False)
        _repolish(self)
        self._cb(False)

    def hideEvent(self, e):  # на всякий случай — не оставить ось в движении
        self._up()
        super().hideEvent(e)


class ToggleButton(ReserveButton):
    """Переключатель с подписью «Название: вкл/выкл» (ширина не прыгает)."""

    def __init__(self, label: str, callback: Callable[[bool], None] | None = None,
                 on_text: str = "вкл", off_text: str = "выкл"):
        super().__init__("", [f"{label}: {on_text}", f"{label}: {off_text}"])
        self.setCheckable(True)
        self.setProperty("kind", "toggle")
        self.setFocusPolicy(Qt.NoFocus)
        self._label, self._on, self._off = label, on_text, off_text
        self._cb = callback
        self.toggled.connect(self._changed)
        self._refresh()

    def _refresh(self):
        self.setText(f"{self._label}: {self._on if self.isChecked() else self._off}")

    def _changed(self, state: bool):
        self._refresh()
        if self._cb:
            self._cb(state)

    def set_silent(self, state: bool):
        self.blockSignals(True)
        self.setChecked(state)
        self._refresh()
        self.blockSignals(False)


class Segmented(QtWidgets.QWidget):
    """Группа взаимоисключающих кнопок (например, объём шприца)."""

    changed = QtCore.pyqtSignal(object)

    def __init__(self, options: list[tuple[str, object]], value=None):
        super().__init__()
        self.setAttribute(Qt.WA_StyledBackground, False)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self._group = QtWidgets.QButtonGroup(self)
        self._group.setExclusive(True)
        self._values = {}
        for i, (text, val) in enumerate(options):
            b = button(text, "seg")
            b.setCheckable(True)
            self._group.addButton(b, i)
            self._values[i] = val
            lay.addWidget(b)
            if val == value:
                b.setChecked(True)
        self._group.idClicked.connect(lambda i: self.changed.emit(self._values[i]))

    def value(self):
        i = self._group.checkedId()
        return self._values.get(i)

    def set_value(self, val):
        """Выбрать кнопку по значению (None — снять выбор) без сигнала."""
        self._group.setExclusive(False)
        for i, b in enumerate(self._group.buttons()):
            b.setChecked(self._values.get(self._group.id(b)) == val and val is not None)
        self._group.setExclusive(True)


class Stepper(QtWidgets.QWidget):
    """[−] значение [+] — ввод числа без клавиатуры."""

    valueChanged = QtCore.pyqtSignal(float)

    def __init__(self, value: float, lo: float, hi: float, step: float = 1, suffix: str = "",
                 decimals: int = 0):
        super().__init__()
        self._v, self._lo, self._hi, self._step = value, lo, hi, step
        self._suffix, self._dec = suffix, decimals
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.minus = button("−", "step", lambda: self.set_value(self._v - self._step, True))
        self.plus = button("+", "step", lambda: self.set_value(self._v + self._step, True))
        self.lbl = ValueLabel("", [self._fmt(lo), self._fmt(hi), self._fmt(-abs(hi) - step)],
                              "MidValue", Qt.AlignCenter)
        self.lbl.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        lay.addWidget(self.minus)
        lay.addWidget(self.lbl, 1)
        lay.addWidget(self.plus)
        self._auto = QtCore.QTimer(self)
        self._auto.setInterval(120)
        for b, d in ((self.minus, -1), (self.plus, 1)):
            b.setAutoRepeat(True)
            b.setAutoRepeatDelay(400)
            b.setAutoRepeatInterval(90)
        self._render()

    def value(self) -> float:
        return self._v

    def set_value(self, v: float, emit: bool = False):
        v = max(self._lo, min(self._hi, round(v, 6)))
        changed = v != self._v
        self._v = v
        self._render()
        if emit and changed:
            self.valueChanged.emit(v)

    def _fmt(self, v: float) -> str:
        txt = f"{v:.{self._dec}f}" if self._dec else f"{int(round(v))}"
        return f"{txt}{self._suffix}"

    def _render(self):
        self.lbl.setText(self._fmt(self._v))


class Card(QtWidgets.QFrame):
    def __init__(self, title: str = "", hint: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.outer = QtWidgets.QVBoxLayout(self)
        self.outer.setContentsMargins(24, 20, 24, 22)
        self.outer.setSpacing(14)
        if title or hint:
            head = QtWidgets.QHBoxLayout()
            head.setSpacing(12)
            if title:
                t = QtWidgets.QLabel(title)
                t.setObjectName("CardTitle")
                head.addWidget(t)
            head.addStretch(1)
            self.head = head
            self.outer.addLayout(head)
            if hint:
                h = QtWidgets.QLabel(hint)
                h.setObjectName("CardHint")
                h.setWordWrap(True)
                self.outer.addWidget(h)
        self.body = QtWidgets.QVBoxLayout()
        self.body.setSpacing(12)
        self.outer.addLayout(self.body)

    def add(self, w, stretch: int = 0):
        if isinstance(w, QtWidgets.QLayout):
            self.body.addLayout(w, stretch)
        else:
            self.body.addWidget(w, stretch)
        return w

    def add_to_header(self, w):
        self.head.addWidget(w)


class Pill(QtWidgets.QLabel):
    """Статус-метка с цветной точкой."""

    @staticmethod
    def colors(level: str) -> tuple[str, str]:
        return {
            "ok": (C["ok_soft"], C["ok"]),
            "warn": (C["warn_soft"], C["warn"]),
            "danger": (C["danger_soft"], C["danger"]),
            "info": (C["accent_soft"], C["accent"]),
        }.get(level, (C["idle_soft"], C["muted"]))

    def __init__(self, text: str = "", level: str = "idle", reserve=()):
        super().__init__()
        self.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        self.setAlignment(Qt.AlignCenter)
        self._reserve = [f"●  {t}" for t in reserve]
        self.set(text, level)

    def _w(self) -> int:
        return _text_w(self, self._reserve + [self.text()]) + 40

    def sizeHint(self):
        return QtCore.QSize(self._w(), super().sizeHint().height())

    def minimumSizeHint(self):
        return self.sizeHint()

    def set(self, text: str, level: str = "idle"):
        self._level = level
        bg, fg = self.colors(level)
        self.setText(f"●  {text}")
        self.setStyleSheet(
            f"background:{bg}; color:{fg}; border-radius:18px; padding:6px 16px;"
            f"font-size:17px; font-weight:700;")
        self.updateGeometry()

    def theme_changed(self):
        self.set(self.text()[3:], self._level)


class Tile(QtWidgets.QWidget):
    """Подпись + крупное значение."""

    def __init__(self, name: str, value: str = "—", value_obj: str = "TileValue", reserve=()):
        super().__init__()
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 32, 0)
        lay.setSpacing(2)
        self.name = QtWidgets.QLabel(name)
        self.name.setObjectName("TileName")
        self.value = ValueLabel(value, reserve, value_obj)
        lay.addWidget(self.name)
        lay.addWidget(self.value)

    def set(self, v: str):
        self.value.setText(v)


class Led(QtWidgets.QWidget):
    """Индикатор: точка + текст (для чек-листов)."""

    def __init__(self, text: str):
        super().__init__()
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        self.dot = QtWidgets.QLabel()
        self.dot.setFixedSize(22, 22)
        self.lbl = QtWidgets.QLabel(text)
        styled(self.lbl, lambda: f"font-size:18px; color:{C['strong']};")
        lay.addWidget(self.dot)
        lay.addWidget(self.lbl, 1)
        self.set(False)

    def set(self, ok: bool, warn: bool = False):
        self._state = (ok, warn)
        color = C["ok"] if ok else (C["warn"] if warn else C["disabled"])
        self.dot.setStyleSheet(f"background:{color}; border-radius:11px;")

    def theme_changed(self):
        self.set(*self._state)


def hline() -> QtWidgets.QFrame:
    f = QtWidgets.QFrame()
    f.setFixedHeight(1)
    styled(f, lambda: f"background:{C['border']};")
    return f


def label(text: str, obj: str | None = None, wrap: bool = False) -> QtWidgets.QLabel:
    l = QtWidgets.QLabel(text)
    if obj:
        l.setObjectName(obj)
    l.setWordWrap(wrap)
    return l


class JogPad(QtWidgets.QWidget):
    """Крестовина XY + колонка Z. Движение пока кнопка удерживается."""

    def __init__(self, on_xy: Callable[[int, bool], None], on_z: Callable[[int, bool], None],
                 on_home: Callable[[], None] | None = None, size: int = 120, compact: bool = False):
        """compact — кнопки ровно size×size и шрифт мельче. Размер задаётся в самом стиле:
        min/max-width из общей темы иначе перебивают setFixedSize (≥ ~137 px или сжатие по тексту)."""
        super().__init__()
        grid = QtWidgets.QGridLayout(self)
        grid.setSpacing(10 if compact else 12)
        grid.setContentsMargins(0, 0, 0, 0)
        inner = size - 2                                   # минус рамка кнопки (1 px)
        fit = (f"min-width:{inner}px; max-width:{inner}px; min-height:{inner}px; max-height:{inner}px;"
               f" padding:0; font-size:22px;") if compact else ""

        def jb(text, cb):
            b = HoldButton(text, cb, kind="jog")
            b.setFixedSize(size, size)
            if fit:
                b.setStyleSheet(fit)
            return b

        # 0:+X 1:-X 2:+Y 3:-Y  — как в прошивке (M611)
        grid.addWidget(jb("▲\nY+", lambda s: on_xy(2, s)), 0, 1)
        grid.addWidget(jb("◀\nX−", lambda s: on_xy(1, s)), 1, 0)
        grid.addWidget(jb("▶\nX+", lambda s: on_xy(0, s)), 1, 2)
        grid.addWidget(jb("▼\nY−", lambda s: on_xy(3, s)), 2, 1)
        if on_home:
            h = button("⌂\nДом", None, on_home)
            h.setFixedSize(size, size)
            h.setStyleSheet(fit.replace("font-size:22px;", "font-size:18px;") if fit else "font-size:22px;")
            grid.addWidget(h, 1, 1)
        spacer = QtWidgets.QWidget()
        spacer.setFixedWidth(12 if compact else 20)
        spacer.setStyleSheet("background: transparent;")
        grid.addWidget(spacer, 0, 3)
        grid.addWidget(jb("▲\nZ+", lambda s: on_z(1, s)), 0, 4)
        zl = QtWidgets.QLabel("Z")
        zl.setAlignment(Qt.AlignCenter)
        styled(zl, lambda: f"font-size:26px; font-weight:700; color:{C['muted']};")
        grid.addWidget(zl, 1, 4)
        grid.addWidget(jb("▼\nZ−", lambda s: on_z(-1, s)), 2, 4)
