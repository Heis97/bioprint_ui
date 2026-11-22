"""Карточки, которые повторяются на нескольких экранах."""
from __future__ import annotations

from PyQt5 import QtWidgets
from PyQt5.QtCore import Qt

from .. import config
from ..context import Ctx
from ..protocol import Cmd
from ..theme import C, HEAD_COLORS, styled
from ..widgets import Card, ElidedLabel, HoldButton, Segmented, Stepper, ToggleButton, Tile, button, label


def page_scroll(inner: QtWidgets.QWidget) -> QtWidgets.QScrollArea:
    sa = QtWidgets.QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QtWidgets.QFrame.NoFrame)
    sa.setWidget(inner)
    return sa


def page_layout(page: QtWidgets.QWidget) -> tuple[QtWidgets.QWidget, QtWidgets.QVBoxLayout]:
    outer = QtWidgets.QVBoxLayout(page)
    outer.setContentsMargins(0, 0, 0, 0)
    inner = QtWidgets.QWidget()
    lay = QtWidgets.QVBoxLayout(inner)
    lay.setContentsMargins(28, 22, 28, 22)
    lay.setSpacing(18)
    outer.addWidget(page_scroll(inner))
    return inner, lay


class HeadCard(Card):
    """Печатающая головка: материал, шприц, выдавливание, «задать текущей» / «снять с сопла».

    extrude=False — без кнопок выдавливания (на «Ручном» для этого отдельный блок «Экструзия»).
    """

    def __init__(self, ctx: Ctx, index: int, compact: bool = False, set_current: bool = False,
                 extrude: bool = True):
        head = config.HEADS[index]
        super().__init__(head["name"])
        self.ctx, self.index = ctx, index
        hs = ctx.settings.heads[index]
        color = HEAD_COLORS[index % len(HEAD_COLORS)]
        self.setStyleSheet(f"QFrame#Card {{ border-top: 6px solid {color}; }}")

        axis = head["e_axis"]
        # Где головка — по телеметрии шлюза (5 цифр в конце st1); пока данных нет —
        # как раньше: головка 1 готова, остальные ждут.
        self._level = "ok" if axis and index == 0 else "warn"
        self.state_lbl = label("готова" if self._level == "ok" else "нет данных", None)
        self.state_lbl.setAlignment(Qt.AlignCenter)
        styled(self.state_lbl, self._pill_css)
        self.state_lbl.ensurePolished()
        fm = self.state_lbl.fontMetrics()
        self.state_lbl.setMinimumWidth(max(fm.horizontalAdvance(t) for t in self.PLACES.values()) + 40)
        self.add_to_header(self.state_lbl)

        self.summary = None
        if not compact:
            form = QtWidgets.QGridLayout()
            form.setHorizontalSpacing(14)
            form.setVerticalSpacing(10)
            form.addWidget(label("Материал", "Muted"), 0, 0)
            cb = QtWidgets.QComboBox()
            cb.setEditable(True)
            cb.addItems(config.MATERIALS)
            cb.setCurrentText(hs.material)
            cb.currentTextChanged.connect(self._set_material)
            form.addWidget(cb, 0, 1)
            # шприц головки — для расчёта ручной экструзии (мл/мин, мкл); генератор — отдельно
            form.addWidget(label("Шприц", "Muted"), 1, 0)
            syr = Segmented([(f"{v} мл", v) for v in config.SYRINGE_VOLUMES], hs.syringe_ml)
            syr.changed.connect(self._set_syringe)
            form.addWidget(syr, 1, 1)
            form.setColumnStretch(1, 1)
            self.add(form)
        else:
            self.summary = ElidedLabel("", "Muted")
            self.add(self.summary)
            self._summary()

        self.b_out = self.b_in = None
        if extrude:
            row = QtWidgets.QHBoxLayout()
            row.setSpacing(10)
            self.b_out = HoldButton("▼  Выдавить", lambda s: self._extrude(s, 1))
            self.b_in = HoldButton("▲  Втянуть", lambda s: self._extrude(s, -1))
            for b in (self.b_out, self.b_in):
                b.setEnabled(axis is not None and index == 0)
                if axis is None:
                    b.setToolTip("Ось выдавливания для этой головки не задана в config.HEADS")
                row.addWidget(b)
            self.add(row)
        # «Задать как текущий» — как «Задать текущий инстр. как N» в String_line4 (main M639 N):
        # сообщить шлюзу, что на сопле стоит эта головка. «Снять с сопла» — «как 0» (main M639 0):
        # головку сняли вручную, на сопле ничего нет.
        # В компактной карточке с двумя кнопками — одна под другой: в ряд «✓ Текущий инструмент»
        # и «Снять с сопла» делали три карточки на «Ручном» шире экрана 1920 (страница уезжала вправо).
        stack = compact and set_current
        brow = QtWidgets.QVBoxLayout() if stack else QtWidgets.QHBoxLayout()
        brow.setSpacing(10)
        self.b_current = None
        if set_current:
            self.b_current = button(self.CURRENT_TEXT, None,
                                    lambda: ctx.link.send(Cmd.set_active_tool(index + 1)),
                                    "Эта головка сейчас на сопле",
                                    reserve=(self.CURRENT_TEXT, self.CURRENT_DONE))
            brow.addWidget(self.b_current, 0 if stack else 1)
        self.b_remove = button(self.REMOVE_TEXT, None, lambda: ctx.link.send(Cmd.set_active_tool(0)),
                               "Головку сняли с сопла вручную — сообщить шлюзу, что на сопле ничего нет")
        brow.addWidget(self.b_remove, 0 if stack else 1)
        self.add(brow)
        self._place = None
        ctx.link.state_changed.connect(self._on_state)

    def _pill_css(self) -> str:
        return (f"font-size:15px; font-weight:600; padding:4px 12px; border-radius:12px;"
                f"background:{C[self._level + '_soft']}; color:{C[self._level]};")

    CURRENT_TEXT = "◎  Задать как текущий"
    CURRENT_DONE = "✓  Текущий инструмент"
    REMOVE_TEXT = "⊘  Снять с сопла"

    # где головка → надпись
    PLACES = {
        "nozzle": "на сопле",
        "left": "в манипуляторе Л",
        "right": "в манипуляторе П",
        "homog": "в гомогенизаторе",
        "": "не установлена",
    }

    def _on_state(self, board, st):
        if board != 1 or not st.tools_known:
            return
        place = st.tool_place(self.index + 1)
        if place == self._place:
            return
        self._place = place
        self._level = {"nozzle": "ok", "": "warn"}.get(place, "accent")
        self.state_lbl.setText(self.PLACES[place])
        self.state_lbl.setStyleSheet(self._pill_css())
        on_nozzle = place == "nozzle" and config.HEADS[self.index]["e_axis"] is not None
        for b in (self.b_out, self.b_in):
            if b is None:
                continue
            b.setEnabled(on_nozzle)
            b.setToolTip("" if on_nozzle else "Выдавливать можно только головкой, установленной на сопло")
        self.b_remove.setEnabled(place == "nozzle")
        if self.b_current is not None:
            self.b_current.setText(self.CURRENT_DONE if place == "nozzle" else self.CURRENT_TEXT)
            self.b_current.setEnabled(place != "nozzle")

    def _summary(self):
        hs = self.ctx.settings.heads[self.index]
        self.summary.set_text(f"Материал: {hs.material} · шприц {hs.syringe_ml} мл")

    def showEvent(self, e):
        if self.summary is not None:
            self._summary()
        super().showEvent(e)

    def _extrude(self, pressed: bool, direction: int):
        axis = config.HEADS[self.index]["e_axis"]
        if not axis:
            return
        board, ax = axis
        self.ctx.link.send(Cmd.periph_move(board, ax, direction) if pressed else Cmd.periph_stop(board, ax))

    def _set_syringe(self, v):
        self.ctx.settings.heads[self.index].syringe_ml = int(v)
        self.ctx.settings.save()

    def _set_material(self, t: str):
        self.ctx.settings.heads[self.index].material = t
        self.ctx.settings.save()


class CoverLiftCheck(QtWidgets.QCheckBox):
    """«Подъём крышки» (M643). Одна настройка на «Главной» и «Периферии»:
    при показе экрана галочка берётся из настроек — изменение на другом экране видно сразу."""

    def __init__(self, ctx: Ctx):
        super().__init__("Подъём крышки")
        self.ctx = ctx
        self.setToolTip("Снимать крышку чашки/планшета захватом при заборе стола и "
                        "возвращать при выдвижении")
        self.setChecked(ctx.settings.cover_lift)
        self.toggled.connect(self._toggled)

    def _toggled(self, on: bool):
        if on == self.ctx.settings.cover_lift:
            return
        self.ctx.settings.cover_lift = on
        self.ctx.settings.save()
        self.ctx.link.send(Cmd.cover_lift(on))

    def showEvent(self, e):
        if self.isChecked() != self.ctx.settings.cover_lift:
            self.blockSignals(True)
            self.setChecked(self.ctx.settings.cover_lift)
            self.blockSignals(False)
        super().showEvent(e)


class BedCard(Card):
    """Термостол: текущая и установленная температура, нагрев."""

    def __init__(self, ctx: Ctx, title: str = "Термостол"):
        super().__init__(title)
        self.ctx = ctx
        top = QtWidgets.QHBoxLayout()
        top.setSpacing(20)
        self.cur = Tile("Сейчас", "— °C", "BigValue", reserve="00 °C")
        top.addWidget(self.cur)
        col = QtWidgets.QVBoxLayout()
        col.setSpacing(4)
        col.addWidget(label("Установка", "TileName"))
        self.step = Stepper(ctx.bed.setpoint, config.BED_TEMP_MIN, config.BED_TEMP_MAX, 1, " °C")
        self.step.valueChanged.connect(ctx.bed.set_setpoint)
        self.step.setMaximumWidth(380)
        col.addWidget(self.step)
        top.addLayout(col, 1)
        self.add(top)
        self.toggle = ToggleButton("Нагрев", ctx.bed.set_on)
        self.add(self.toggle)
        self.body.addStretch(1)
        ctx.bed.changed.connect(self._sync)
        ctx.link.state_changed.connect(self._on_state)
        self._sync()

    def _sync(self):
        self.step.set_value(self.ctx.bed.setpoint)
        self.toggle.set_silent(self.ctx.bed.on)

    def _on_state(self, board, st):
        if board == config.TEMP_SOURCE_BOARD:
            self.cur.set(f"{self.ctx.link.bed_temp():.0f} °C")


def _ask(parent, title: str, text: str, ok_text: str | None, icon=None) -> bool:
    box = QtWidgets.QMessageBox(parent)
    box.setIcon(icon or QtWidgets.QMessageBox.Warning)
    box.setWindowTitle(title)
    box.setText(text)
    ok = box.addButton(ok_text, QtWidgets.QMessageBox.AcceptRole) if ok_text else None
    box.addButton("Отмена" if ok_text else "Понятно", QtWidgets.QMessageBox.RejectRole)
    box.exec_()
    return ok is not None and box.clickedButton() is ok


def start_with_checks(parent: QtWidgets.QWidget, ctx: Ctx) -> None:
    """Старт печати с проверками: связь и все смены головок (T) по текущей раскладке."""
    job = ctx.job
    if job.active or not job.program:
        return
    if not ctx.link.connected:
        QtWidgets.QMessageBox.warning(parent, "Нет связи", "Нет связи с контроллером принтера.")
        return
    p, st = job.program, ctx.link.main
    skip = ()
    if p.tool_changes and not st.tools_known:
        if not _ask(parent, "Смена головок",
                    "В программе есть смены головок (T), а шлюз ещё не сообщил, где стоят головки.\n"
                    "Проверить смены заранее нельзя.", "Печатать без проверки"):
            return
    n = job.first_tool_conflict()
    if n is not None:
        if not _ask(parent, "Смена головки",
                    f"Программа начинается с T{n}, а головка {n} уже на сопле.\n"
                    f"Шлюз на такую команду отдаёт головку в манипулятор и останавливается с ошибкой.",
                    f"Не отправлять T{n} и печатать"):
            return
        skip = (p.first_tool_line,)
    plan = job.tool_plan(skip)
    if plan:
        idx, tool, what, fatal = plan[0]
        line = f"Строка {p.src_lines[idx]}: T{tool} — {what}." if idx < len(p.src_lines) else f"T{tool} — {what}."
        if fatal:
            _ask(parent, "Смена головки", line + "\n\nИсправьте программу или раскладку головок "
                                               "(«Калибровка → Инструмент»).", None, QtWidgets.QMessageBox.Critical)
            return
        if not _ask(parent, "Смена головки", line + "\n\nПечать дойдёт до этой строки и остановится.",
                    "Печатать всё равно"):
            return
    job.start(skip)
