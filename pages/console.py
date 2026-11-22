"""Консоль инженера: журнал команд, ручная команда, телеметрия плат."""
from __future__ import annotations

import time

from PyQt5 import QtWidgets

from .. import auth, config, printer_settings
from ..context import Ctx
from ..protocol import AXIS_NAMES, BoardState

# Расшифровка телеметрии (формат — protocol.py): подпись строки таблицы → значение
_YN = lambda v: "да" if v else "—"
_TOOL = lambda v: str(v) if v else "—"
TELEMETRY_ROWS = [
    ("Принята команда N", lambda s: s.line_num),
    ("Кадров записано в буфер", lambda s: s.ring_counter),
    ("Моторы движутся", lambda s: _YN(s.moving)),
    ("Кинематика движется", lambda s: _YN(s.moving_kinem)),
    ("Идёт программа", lambda s: _YN(s.ring_buf_en)),
    ("Пауза G4", lambda s: _YN(s.waiting_time)),
    ("Ожидание нагрева", lambda s: _YN(s.waiting_temp)),
    ("Калибровка дельты", lambda s: _YN(s.delta_calib)),
    ("Температура, °C", lambda s: f"{s.temp_cur:.2f}"),
    ("Лазерные датчики", lambda s: s.lasers or "—"),
    ("Поиск сопла X Y Z", lambda s: s.nozzle_search or "—"),
    ("Датчики головок", lambda s: s.tool_sensors or "—"),
    ("Счётчик цикла", lambda s: s.control_counter),
    ("X от нуля, мм", lambda s: f"{s.x:.3f}" if s.has_xyz else "—"),
    ("Y от нуля, мм", lambda s: f"{s.y:.3f}" if s.has_xyz else "—"),
    ("Z от нуля, мм", lambda s: f"{s.z:.3f}" if s.has_xyz else "—"),
    ("Головка на сопле", lambda s: _TOOL(s.tool_active) if s.tools_known else "—"),
    ("Манипулятор левый", lambda s: _TOOL(s.tool_manip_left) if s.tools_known else "—"),
    ("Манипулятор правый", lambda s: _TOOL(s.tool_manip_right) if s.tools_known else "—"),
    ("Гомогенизаторы 1 / 2", lambda s: " / ".join(_TOOL(t) for t in s.tool_homog) if s.tools_known else "—"),
]
TELEMETRY_ROWS += [(f"Положение {a}, шаги", lambda s, i=i: s.cur_pos[i]) for i, a in enumerate(AXIS_NAMES)]
TELEMETRY_ROWS += [(f"Концевик {a}", lambda s, i=i: s.cur_end[i]) for i, a in enumerate(AXIS_NAMES)]
_RING = ["записано", "выполнено", "конец", "строка", "время кадра", "№ программы кадра",
         "№ текущей программы", "X кадра, шаги"]
TELEMETRY_ROWS += [(f"Буфер: {n}", lambda s, i=i: s.ring[i]) for i, n in enumerate(_RING)]
from ..widgets import Card, Segmented, ToggleButton, button, label


class ConsolePage(QtWidgets.QWidget):
    title = "Консоль"

    def __init__(self, ctx: Ctx):
        super().__init__()
        self.ctx = ctx
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 28)
        outer.setSpacing(20)

        mode = Card("Интерфейс", "Простой — для оператора: основные шаги печати. "
                                 "Расширенный — все экраны и инженерные настройки.")
        self.mode_seg = Segmented([("Простой", "simple"), ("Расширенный", "extended")], ctx.settings.ui_mode)
        self.mode_seg.changed.connect(lambda m: ctx.set_ui_mode(m))
        self.theme_seg = Segmented([("☀  Светлая", "light"), ("☾  Тёмная", "dark")], ctx.settings.theme)
        self.theme_seg.changed.connect(lambda t: ctx.set_theme(t))
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(16)
        row.addWidget(label("Режим", "Muted"))
        row.addWidget(self.mode_seg, 3)
        row.addSpacing(24)
        row.addWidget(label("Тема", "Muted"))
        row.addWidget(self.theme_seg, 2)
        row.addSpacing(24)
        row.addWidget(button("⚙  Настройки принтера", None, self._printer_settings,
                             "Параметры моторов, смены головок, подложек и стола (settings_printer.json)"))
        row.addWidget(button("🔒  Сменить пароль", None, self._change_password,
                             "Пароль входа хранится в settings.json в виде хэша"))
        mode.add(row)
        outer.addWidget(mode)

        root = QtWidgets.QHBoxLayout()
        root.setSpacing(20)
        outer.addLayout(root, 1)

        logc = Card("Журнал обмена")
        self.show_rx = ToggleButton("Телеметрия", on_text="показ.", off_text="скрыта")
        logc.add_to_header(self.show_rx)
        logc.add_to_header(button("Очистить", None, lambda: self.log.clear()))
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(3000)
        logc.add(self.log, 1)
        row = QtWidgets.QHBoxLayout()
        self.board = QtWidgets.QComboBox()
        self.board.addItems(["main", "num1", "num2", "(без префикса)"])
        self.board.setFixedWidth(220)
        self.cmd = QtWidgets.QLineEdit()
        self.cmd.setPlaceholderText("Команда, например M587 I4 H")
        self.cmd.returnPressed.connect(self._send)
        row.addWidget(self.board)
        row.addWidget(self.cmd, 1)
        row.addWidget(button("Отправить", "primary", self._send))
        logc.add(row)
        root.addWidget(logc, 3)

        self.cmd_row = row
        tc = Card("Телеметрия")
        self.tel_card = tc
        self.table = QtWidgets.QTableWidget()
        self.rows = [name for name, _ in TELEMETRY_ROWS]
        self.table.setRowCount(len(self.rows))
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(["st1", "st2"])
        self.table.setVerticalHeaderLabels(self.rows)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        tc.add(self.table, 1)
        root.addWidget(tc, 2)

        ctx.link.command_sent.connect(lambda c: self._append("→", c))
        ctx.link.raw_lines.connect(self._rx)
        ctx.link.state_changed.connect(self._state)
        ctx.link.connected_changed.connect(
            lambda ok: self._append("•", "подключено" if ok else "нет связи"))

    def _printer_settings(self):
        printer_settings.open_dialog(self, self.ctx)

    def _change_password(self):
        if auth.change(self.ctx.settings, self):
            self._append("•", "пароль входа изменён")

    def theme_synced(self, name: str):
        self.theme_seg.set_value(name)

    def _append(self, arrow: str, text: str):
        self.log.appendPlainText(f"{time.strftime('%H:%M:%S')}  {arrow}  {text}")

    def _rx(self, lines: list):
        if self.show_rx.isChecked():
            # телеметрия идёт ~2000 строк/с — показываем последние из пачки
            for ln in lines[-10:]:
                self._append("←", ln)

    def _send(self):
        t = self.cmd.text().strip()
        if not t:
            return
        b = self.board.currentText()
        self.ctx.link.send(t if b.startswith("(") else f"{b} {t}")
        self.cmd.clear()

    def _state(self, board: int, st: BoardState):
        col = board - 1
        if col not in (0, 1):
            return
        for i, (_, get) in enumerate(TELEMETRY_ROWS):
            try:
                txt = str(get(st))
            except (IndexError, ValueError):
                txt = "—"
            it = self.table.item(i, col)
            if it is None:
                self.table.setItem(i, col, QtWidgets.QTableWidgetItem(txt))
            elif it.text() != txt:
                it.setText(txt)

    def set_simple(self, on: bool):
        """В простом режиме — только журнал; ручные команды и телеметрия скрыты."""
        self.mode_seg.set_value("simple" if on else "extended")
        self.tel_card.setVisible(not on)
        for i in range(self.cmd_row.count()):
            w = self.cmd_row.itemAt(i).widget()
            if w:
                w.setVisible(not on)
