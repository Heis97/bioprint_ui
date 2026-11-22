"""Периферия: подача подложек, термостол, пневматика, гомогенизаторы, манипуляторы, свет, лазеры."""
from __future__ import annotations

from PyQt5 import QtCore, QtWidgets
from PyQt5.QtCore import Qt

from ..context import Ctx
from ..protocol import RELAYS, Axis, Cmd
from ..widgets import Card, HoldButton, Led, ToggleButton, ValueLabel, button, label
from .components import BedCard, CoverLiftCheck, page_layout


def axis_hold(ctx: Ctx, axis: tuple[str, int], direction: int):
    board, ax = axis

    def cb(pressed: bool):
        ctx.link.send(Cmd.periph_move(board, ax, direction) if pressed else Cmd.periph_stop(board, ax))
    return cb


def axis_row(ctx: Ctx, axis, minus_text: str, plus_text: str, home_text: str | None = None):
    row = QtWidgets.QHBoxLayout()
    row.setSpacing(10)
    row.addWidget(HoldButton(minus_text, axis_hold(ctx, axis, -1)))
    row.addWidget(HoldButton(plus_text, axis_hold(ctx, axis, 1)))
    if home_text:
        b, a = axis
        h = button(home_text, None, lambda: ctx.link.send(Cmd.periph_home(b, a)))
        h.setMaximumWidth(150)
        row.addWidget(h)
    return row


class HomogCard(Card):
    def __init__(self, ctx: Ctx, idx: int):
        super().__init__(f"Гомогенизатор {idx + 1}", "Перемешивание биочернил перед печатью")
        ax = Axis.HOMOG[idx]
        self.add(label("Вращение", "Muted"))
        self.add(axis_row(ctx, ax["rot"], "⟲  −", "⟳  +", "⌂ Ноль"))
        self.add(label("Подъём", "Muted"))
        self.add(axis_row(ctx, ax["lift"], "▼  Вниз", "▲  Вверх", "⌂ Ноль"))
        head = QtWidgets.QHBoxLayout()
        head.addWidget(label("Сервопривод", "Muted"))
        head.addStretch(1)
        self.val = ValueLabel("120", "000", "Muted", Qt.AlignRight | Qt.AlignVCenter)
        head.addWidget(self.val)
        self.add(head)
        sl = QtWidgets.QSlider(Qt.Horizontal)
        sl.setRange(40, 240)
        sl.setValue(120)
        sl.setMinimumHeight(48)
        sl.setMinimumWidth(200)
        # как в String_line4: команда уходит при каждом изменении положения ползунка
        sl.valueChanged.connect(lambda v: (self.val.setText(str(v)), ctx.link.send(Cmd.servo(ax["servo"], v))))
        self.add(sl)
        self.body.addStretch(1)


class ManipCard(Card):
    def __init__(self, ctx: Ctx):
        super().__init__("Манипуляторы", "Смена рабочих органов и захват крышек")
        for i, ax in enumerate(Axis.MANIP):
            self.add(label(f"Манипулятор {i + 1}", "CardTitle"))
            g = QtWidgets.QGridLayout()
            g.setHorizontalSpacing(10)
            g.setVerticalSpacing(10)
            for r, (name, key, m, p) in enumerate((("Поворот", "rot", "⟲ −", "⟳ +"),
                                                   ("Подъём", "lift", "▼", "▲"))):
                g.addWidget(label(name, "Muted"), r, 0)
                b, a = ax[key]
                g.addWidget(HoldButton(m, axis_hold(ctx, ax[key], -1)), r, 1)
                g.addWidget(HoldButton(p, axis_hold(ctx, ax[key], 1)), r, 2)
                g.addWidget(button("⌂", None, lambda b=b, a=a: ctx.link.send(Cmd.periph_home(b, a))), r, 3)
            g.setColumnStretch(1, 1)
            g.setColumnStretch(2, 1)
            self.add(g)
        self.body.addStretch(1)


class LightCard(Card):
    """Освещение: num2 M581 A<0..255> I1 — как ползунок «Освещение» в String_line4."""

    def __init__(self, ctx: Ctx):
        super().__init__("Освещение")
        head = QtWidgets.QHBoxLayout()
        head.addWidget(label("Яркость", "Muted"))
        head.addStretch(1)
        self.val = ValueLabel("", "000 %", "MidValue", Qt.AlignRight | Qt.AlignVCenter)
        head.addWidget(self.val)
        self.add(head)
        self.sl = QtWidgets.QSlider(Qt.Horizontal)
        self.sl.setRange(0, 255)
        self.sl.setValue(int(ctx.settings.light))
        self.sl.setMinimumHeight(48)
        self._text(self.sl.value())

        def changed(v: int):
            self._text(v)
            ctx.link.send(Cmd.light(v))           # при каждом изменении, как в String_line4
            ctx.settings.light = int(v)

        self.sl.valueChanged.connect(changed)
        self.sl.sliderReleased.connect(ctx.settings.save)
        self.add(self.sl)
        row = QtWidgets.QHBoxLayout()
        for text, v in (("Выкл", 0), ("50 %", 128), ("100 %", 255)):
            row.addWidget(button(text, None, lambda v=v: (self.sl.setValue(v), ctx.settings.save())))
        self.add(row)
        self.body.addStretch(1)

    def _text(self, v: int):
        self.val.setText(f"{round(v * 100 / 255)} %")


class LaserCard(Card):
    """Лазерная система калибровки сопла (плата 1): замкнут ли каждый луч."""

    def __init__(self, ctx: Ctx):
        super().__init__("Лазерная система", "Калибровка сопла. Замкнут — луч проходит; "
                                             "разомкнут — луч перекрыт (соплом или посторонним предметом).")
        self.leds = []
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(28)
        for i in range(2):
            col = QtWidgets.QVBoxLayout()
            col.setSpacing(6)
            led = Led(f"Лазер {i + 1}")
            st = ValueLabel("нет данных", ["нет данных", "замкнут", "разомкнут"], "MidValue")
            col.addWidget(led)
            col.addWidget(st)
            row.addLayout(col)
            self.leds.append((led, st))
        row.addStretch(1)
        self.add(row)
        self.search = ValueLabel("", "Поиск сопла X Y Z: 000", "Muted")
        self.add(self.search)
        self.body.addStretch(1)
        ctx.link.state_changed.connect(self._on_state)

    def _on_state(self, board, st):
        if board != 1:
            return
        for i, (led, lbl) in enumerate(self.leds):
            v = st.laser(i)
            led.set(bool(v), warn=v is False)
            lbl.setText("нет данных" if v is None else ("замкнут" if v else "разомкнут"))
        self.search.setText(f"Поиск сопла X Y Z: {st.nozzle_search or '—'}")


class PeripheryPage(QtWidgets.QWidget):
    title = "Периферия"

    def __init__(self, ctx: Ctx):
        super().__init__()
        self.ctx = ctx
        _, lay = page_layout(self)
        grid = QtWidgets.QGridLayout()
        grid.setSpacing(20)

        feed = Card("Стол", "Стол с чашкой Петри или планшетом")
        feed.add(label("Выдвижение стола", "Muted"))
        feed.add(axis_row(ctx, Axis.TABLE_INOUT, "◀  Задвинуть", "▶  Выдвинуть", "⌂ Ноль"))
        feed.add(label("Подъём стола", "Muted"))
        feed.add(axis_row(ctx, Axis.TABLE_LIFT, "▼  Вниз", "▲  Вверх"))
        feed.add(CoverLiftCheck(ctx))
        feed.body.addStretch(1)
        grid.addWidget(feed, 0, 0)

        grid.addWidget(BedCard(ctx), 0, 1)

        rel = Card("Пневматика")
        self.relays = []
        for idx, name in RELAYS:
            t = ToggleButton(name, lambda s, i=idx: ctx.link.send(Cmd.relay(i, s)))
            rel.add(t)
            self.relays.append(t)
        rel.body.addStretch(1)
        grid.addWidget(rel, 0, 2)

        grid.addWidget(HomogCard(ctx, 0), 1, 0)
        grid.addWidget(HomogCard(ctx, 1), 1, 1)
        grid.addWidget(ManipCard(ctx), 1, 2)
        grid.addWidget(LightCard(ctx), 2, 0)
        grid.addWidget(LaserCard(ctx), 2, 1, 1, 2)
        for c in range(3):
            grid.setColumnStretch(c, 1)
        lay.addLayout(grid)
        lay.addStretch(1)
