"""Ручное управление: перемещение XYZ (удержанием или на шаг), скорость, ноль, выдавливание."""
from __future__ import annotations

from PyQt5 import QtWidgets

from .. import config, extrusion
from ..context import Ctx
from ..protocol import Cmd, coord_system_name, coord_system_short
from ..widgets import Card, ElidedLabel, HoldButton, JogPad, Segmented, Stepper, Tile, ValueLabel, button, label
from .components import HeadCard, page_layout

_XY_AXES = {0: ("X", 1), 1: ("X", -1), 2: ("Y", 1), 3: ("Y", -1)}     # код M611 → ось, знак


def jog_handlers(ctx: Ctx):
    """Обработчики кнопок крестовины для обоих режимов (настройка jog_mode).

    «Пока держишь» — M611 (шлюз двигает к точке в 100 мм, отпускание — M597 2).
    «Шаг» — короткая программа в текущей СК: G1 на выбранный шаг (Cmd.jog_step).
    """
    def step_ok() -> bool:
        return not ctx.job.active

    def xy(direction: int, pressed: bool):
        s = ctx.settings
        if s.jog_mode == "step":
            if pressed and step_ok():
                axis, sign = _XY_AXES[direction]
                ctx.link.send_many(Cmd.jog_step(axis, sign * s.jog_step, s.jog_speed))
            return
        ctx.link.send_many(Cmd.jog_xy_start(direction) if pressed else [Cmd.JOG_STOP])

    def z(direction: int, pressed: bool):
        s = ctx.settings
        if s.jog_mode == "step":
            if pressed and step_ok():
                ctx.link.send_many(Cmd.jog_step("Z", direction * s.jog_step, s.jog_speed))
            return
        ctx.link.send_many(Cmd.jog_z_start(direction) if pressed else [Cmd.JOG_STOP])

    return xy, z


class JogSettings(QtWidgets.QWidget):
    """Скорость перемещения (M610), режим и шаг. Общие настройки для «Ручного» и «Калибровки»."""

    def __init__(self, ctx: Ctx, stacked: bool = False):
        """stacked — подписи над полями (узкая колонка рядом с крестовиной на «Ручном»)."""
        super().__init__()
        self.ctx = ctx
        s = ctx.settings
        g = QtWidgets.QGridLayout(self)
        g.setContentsMargins(0, 0, 0, 0)
        g.setHorizontalSpacing(14)
        g.setVerticalSpacing(8 if stacked else 10)
        self.speed = Stepper(s.jog_speed, config.JOG_SPEED_MIN, config.JOG_SPEED_MAX, 0.5, " мм/с", 1)
        self.speed.valueChanged.connect(self._speed)
        self.mode = Segmented([("Пока держишь", "hold"), ("На шаг", "step")], s.jog_mode)
        self.mode.changed.connect(self._mode)
        unit = "" if stacked else " мм"                      # в узкой колонке «мм» — в подписи
        self.step = Segmented([(f"{v:g}{unit}", v) for v in config.JOG_STEPS], s.jog_step)
        self.step.changed.connect(self._step)
        rows = (("Скорость", self.speed), ("Режим", self.mode), ("Шаг, мм" if stacked else "Шаг", self.step))
        for i, (name, w) in enumerate(rows):
            if stacked:
                g.addWidget(label(name, "Muted"), 2 * i, 0)
                g.addWidget(w, 2 * i + 1, 0)
            else:
                g.addWidget(label(name, "Muted"), i, 0)
                g.addWidget(w, i, 1)
        g.setColumnStretch(0 if stacked else 1, 1)
        if stacked:
            g.setRowStretch(2 * len(rows), 1)
        self._sync_step()

    def _speed(self, v: float):
        self.ctx.settings.jog_speed = round(float(v), 2)
        self.ctx.settings.save()
        self.ctx.link.send(Cmd.jog_speed(v))

    def _mode(self, m: str):
        self.ctx.settings.jog_mode = m
        self.ctx.settings.save()
        self._sync_step()

    def _step(self, v: float):
        self.ctx.settings.jog_step = float(v)
        self.ctx.settings.save()

    def _sync_step(self):
        self.step.setEnabled(self.ctx.settings.jog_mode == "step")

    def showEvent(self, e):                        # настройку могли поменять на другом экране
        s = self.ctx.settings
        self.speed.set_value(s.jog_speed)
        self.mode.set_value(s.jog_mode)
        self.step.set_value(s.jog_step)
        self._sync_step()
        super().showEvent(e)


class ExtrudeCard(Card):
    """Ручная экструзия поршнем головки на сопле: расход в мл/мин, «пока держишь» или на объём.

    Поршень двигает прошивка (M587 I7): скорость M, мм/с, и ход S, мм — от текущего положения.
    Ход считается по объёму и шприцу головки, поэтому объём точный; время = объём / расход.
    """

    def __init__(self, ctx: Ctx):
        super().__init__("Экструзия")
        self.ctx = ctx
        s = ctx.settings
        self.info = ElidedLabel("", "Muted")
        self.add_to_header(self.info)

        top = QtWidgets.QHBoxLayout()
        top.setSpacing(16)
        top.addWidget(label("Расход", "Muted"))
        self.rate = Stepper(s.extrude_rate, config.EXTRUDE_RATE_MIN, config.EXTRUDE_RATE_MAX,
                            config.EXTRUDE_RATE_STEP, " мл/мин", 3)
        self.rate.valueChanged.connect(self._rate)
        top.addWidget(self.rate, 2)
        self.rate_quick = Segmented([(f"{v:g}", v) for v in config.EXTRUDE_RATE_PRESETS], s.extrude_rate)
        self.rate_quick.changed.connect(self._rate_preset)
        top.addWidget(self.rate_quick, 3)
        top.addSpacing(12)
        self.mode = Segmented([("Пока держишь", "hold"), ("На объём", "volume")], s.extrude_mode)
        self.mode.changed.connect(self._mode)
        top.addWidget(self.mode, 2)
        self.add(top)

        mid = QtWidgets.QHBoxLayout()
        mid.setSpacing(16)
        mid.addWidget(label("Объём, мкл", "Muted"))
        self.volume = Segmented([(f"{v:g}", float(v)) for v in config.EXTRUDE_VOLUMES_UL], float(s.extrude_volume))
        self.volume.changed.connect(self._volume)
        mid.addWidget(self.volume, 3)
        self.calc = ValueLabel("", "ход 0000.000 мм · 00 мин 00 с", "Muted")
        mid.addWidget(self.calc)
        self.add(mid)

        bot = QtWidgets.QHBoxLayout()
        bot.setSpacing(10)
        self.b_out = HoldButton("▼  Выдавить", lambda p: self._press(p, 1))
        self.b_in = HoldButton("▲  Втянуть", lambda p: self._press(p, -1))
        self.b_stop = button("■  Стоп", None, lambda: ctx.link.send(Cmd.E_STOP))
        for b in (self.b_out, self.b_in, self.b_stop):
            bot.addWidget(b, 1)
        self.add(bot)

        ctx.link.state_changed.connect(lambda b, st: self._refresh() if b == 1 else None)
        self._refresh()

    # головка на сопле → шприц
    def _head(self) -> int | None:
        st = self.ctx.link.main
        if not st.tools_known:
            return 0                       # телеметрии о головках ещё нет — как раньше: головка 1
        n = st.tool_active
        return n - 1 if 1 <= n <= len(config.HEADS) else None

    def _syringe(self) -> int:
        i = self._head()
        return self.ctx.settings.heads[i].syringe_ml if i is not None else 5

    def _refresh(self):
        i = self._head()
        ok = i is not None
        if ok:
            ml = self.ctx.settings.heads[i].syringe_ml
            self.info.set_text(f"{config.HEADS[i]['name']} · шприц {ml} мл (Ø {extrusion.syringe_diameter(ml):g} мм)")
        else:
            self.info.set_text("На сопле нет головки — задайте текущую")
        for b in (self.b_out, self.b_in):
            b.setEnabled(ok)
        vol_mode = self.ctx.settings.extrude_mode == "volume"
        self.volume.setEnabled(vol_mode)
        if ok and vol_mode:
            ml = self._syringe()
            v, q = self.ctx.settings.extrude_volume, self.ctx.settings.extrude_rate
            self.calc.setText(f"ход {extrusion.travel_mm(v, ml):.3f} мм · "
                              f"{extrusion.fmt_duration(extrusion.duration_s(v, q))}")
        else:
            self.calc.setText("")

    def _press(self, pressed: bool, direction: int):
        s = self.ctx.settings
        ml = self._syringe()
        speed = Cmd.e_speed(extrusion.speed_mm_s(s.extrude_rate, ml))
        if s.extrude_mode == "volume":
            if pressed:
                self.ctx.link.send_many([speed, Cmd.e_move(direction * extrusion.travel_mm(s.extrude_volume, ml))])
            return
        if pressed:
            self.ctx.link.send_many([speed, Cmd.e_move(direction * Cmd.E_HOLD_TRAVEL)])
        else:
            self.ctx.link.send(Cmd.E_STOP)

    def _rate_preset(self, v: float):
        self.rate.set_value(v)
        self._rate(v)

    def _rate(self, v: float):
        self.ctx.settings.extrude_rate = round(float(v), 4)
        self.ctx.settings.save()
        self._refresh()

    def _mode(self, m: str):
        self.ctx.settings.extrude_mode = m
        self.ctx.settings.save()
        self._refresh()

    def _volume(self, v: float):
        self.ctx.settings.extrude_volume = float(v)
        self.ctx.settings.save()
        self._refresh()

    def showEvent(self, e):                # шприц могли сменить на «Главной»
        self._refresh()
        super().showEvent(e)


class ManualPage(QtWidgets.QWidget):
    title = "Ручное управление"

    def __init__(self, ctx: Ctx):
        super().__init__()
        self.ctx = ctx
        _, lay = page_layout(self)

        row = QtWidgets.QHBoxLayout()
        row.setSpacing(20)

        # Верхний ряд сделан низким, чтобы головки помещались без прокрутки (1920×1080):
        # крестовина и настройки — рядом, а не друг под другом; X/Y/Z — в строку.
        jog = Card("Перемещение")
        jog.setToolTip("«Пока держишь» — сопло движется, пока кнопка нажата.\n"
                       "«На шаг» — одно нажатие сдвигает сопло на выбранный шаг.")
        xy, z = jog_handlers(ctx)
        jr = QtWidgets.QHBoxLayout()
        jr.setSpacing(16)
        jr.addWidget(JogPad(xy, z, on_home=lambda: ctx.link.send(Cmd.HOME), size=104, compact=True))
        jr.addWidget(JogSettings(ctx, stacked=True), 1)
        jog.add(jr)
        jog.body.addStretch(1)
        row.addWidget(jog, 0)

        pos = Card("Координаты")
        self.cs_lbl = ValueLabel("СК: —", "СК: Машинная", "Muted")
        pos.add_to_header(self.cs_lbl)
        g = QtWidgets.QHBoxLayout()
        g.setSpacing(14)
        self.tx, self.ty, self.tz = (Tile(f"{a}, мм", "0.00", "CoordValue", reserve="-000.00") for a in "XYZ")
        for t in (self.tx, self.ty, self.tz):
            g.addWidget(t, 1)
        pos.add(g)
        res = "X -000.00  Y -000.00  Z -000.00"
        self.m_lbl = ValueLabel("Машина: —", "Машина:      " + res, "Muted")
        self.t_lbl = ValueLabel("Инструмент: —", "Инструмент: " + res, "Muted")
        pos.add(self.m_lbl)
        pos.add(self.t_lbl)
        pos.body.addStretch(1)
        pos.add(label("Ноль и базирование", "Muted"))
        zr = QtWidgets.QGridLayout()
        zr.setHorizontalSpacing(12)
        zr.setVerticalSpacing(12)
        zr.addWidget(button("Установить 0 здесь", None, lambda: ctx.link.send(Cmd.SET_ZERO)), 0, 0)
        zr.addWidget(button("Перейти в 0", None, lambda: ctx.link.send(Cmd.MOVE_TO_ZERO)), 0, 1)
        zr.addWidget(button("Сбросить ноль", None, ctx.link.reset_zero,
                            "Ноль ручной СК активной головки — в ноль машины"), 1, 0)
        zr.addWidget(button("⌂  Home — базирование", "primary", lambda: ctx.link.send(Cmd.HOME)), 1, 1)
        # как в oct_ui: «Home manipulators» (M630) и «Home oscillators» (M634)
        zr.addWidget(button("⌂  Дом манипуляторов", "primary", lambda: ctx.link.send(Cmd.HOME_MANIPULATORS),
                            "Базирование обоих манипуляторов смены головок"), 2, 0)
        zr.addWidget(button("⌂  Дом осцилляторов", "primary", lambda: ctx.link.send(Cmd.HOME_HOMOGENIZERS),
                            "Базирование осцилляторов (гомогенизаторов)"), 2, 1)
        pos.add(zr)
        row.addWidget(pos, 1)
        lay.addLayout(row)

        heads = QtWidgets.QHBoxLayout()
        heads.setSpacing(20)
        for i in range(len(config.HEADS)):
            heads.addWidget(HeadCard(ctx, i, compact=True, set_current=True, extrude=False))
        lay.addLayout(heads)
        lay.addWidget(ExtrudeCard(ctx))
        lay.addStretch(1)

        ctx.link.state_changed.connect(self._on_state)

    def _on_state(self, board, st):
        if board != 1:
            return
        self.tx.set(f"{st.x:.2f}")
        self.ty.set(f"{st.y:.2f}")
        self.tz.set(f"{st.z:.2f}")
        self.cs_lbl.setText(f"СК: {coord_system_short(st.coord_sys)}")
        self.cs_lbl.setToolTip(coord_system_name(st.coord_sys))
        self.m_lbl.setText(f"Машина:      X {st.mx:.2f}  Y {st.my:.2f}  Z {st.mz:.2f}")
        self.t_lbl.setText(f"Инструмент: X {st.tx:.2f}  Y {st.ty:.2f}  Z {st.tz:.2f}")
