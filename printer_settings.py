"""Окно «Настройки принтера»: правка settings_printer.json шлюза в таблицах.

Файл читает шлюз tcp_to_udp при старте и по команде «main M614» (load_settings):
параметры моторов уходят на платы командами M587, остальное шлюз использует сам
(смена головок, калибровка сопел, подложки G54, стол, сервоприводы).

Окно показывает только то, что есть в файле; неизвестные поля — в разделе «Прочее».
Типы сохраняются: целое поле принимает только целое. Перед записью — копия .bak.
"""
from __future__ import annotations

import json
import os
import shutil
from collections import OrderedDict

from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtCore import Qt

from . import config
from .theme import C
from .widgets import Segmented, button, label

FILE_NAME = "settings_printer.json"
APPLY_CMD = "main M614"          # шлюз перечитывает файл и отправляет настройки на платы

# ── Подписи ──────────────────────────────────────────────────────────────────
MOTORS = {
    1: ["Дельта A", "Дельта B", "Дельта C", "Гомоген. 1 подъём", "Гомоген. 1 поворот",
        "Гомоген. 2 подъём", "Гомоген. 2 поворот", "Поршень E"],
    2: ["Стол выдвижение", "Ось 1", "Стол подъём", "Манип. 1 подъём", "Манип. 1 поворот",
        "Манип. 2 подъём", "Манип. 2 поворот", "Ось 7"],
}
MOTOR_ROWS = [
    ("a_max", "Ускорение"),
    ("v_def", "Скорость"),
    ("steps_per_mm", "Шагов на мм"),
    ("motor_dir", "Направление мотора"),
    ("end_inv", "Инверсия концевика"),
    ("home_dir", "Направление поиска нуля"),
    ("soft_min_pos", "Программный минимум"),
    ("soft_max_pos", "Программный максимум"),
]
MANIP_ROWS = [("rot", "Поворот"), ("vert", "Подъём"), ("x", "X"), ("y", "Y"), ("z", "Z")]
TABLE_SCALARS = [
    ("table_work_pos", "Стол: рабочее положение"),
    ("table_change_pos", "Стол: положение смены"),
    ("table_open_pos", "Стол: выдвинут"),
    ("lift_up_val", "Подъём: верхнее положение"),
    ("servo_open_val", "Сервоприводы стола: открыто"),
    ("servo_close_val", "Сервоприводы стола: закрыто"),
    ("servo_osc1_open_val", "Гомогенизатор 1, серво: открыто"),
    ("servo_osc1_close_val", "Гомогенизатор 1, серво: закрыто"),
    ("servo_osc2_open_val", "Гомогенизатор 2, серво: открыто"),
    ("servo_osc2_close_val", "Гомогенизатор 2, серво: закрыто"),
    ("osc_rot_start_pos1", "Гомогенизатор 1: начальный поворот"),
    ("osc_rot_start_pos2", "Гомогенизатор 2: начальный поворот"),
]
PLANNER_SCALARS = [
    ("max_acs", "Максимальное ускорение"),
    ("max_r", "Радиус скругления"),
    ("min_dist", "Минимальное расстояние"),
]
# Поля, которые есть в файле, но в окне не нужны: положение нуля теперь задают
# программные пределы (soft_min/soft_max), шлюз home_pos на платы не отправляет.
HIDDEN_KEYS = {"home_pos1", "home_pos2"}
LASER_SCALARS = [("offset_las_sys_x", "X"), ("offset_las_sys_y", "Y"), ("offset_las_sys_z", "Z")]


def plate_names() -> dict[int, str]:
    """Номер подложки G54 X<i> → название (как в генераторе)."""
    try:
        from .gen import SUBSTRATES
    except Exception:                                  # noqa: BLE001 — подписи не обязательны
        SUBSTRATES = {}
    return {i: SUBSTRATES.get(k, (k,))[0] for k, i in config.G54_INDEX.items()}


def default_path(settings: config.UserSettings) -> str:
    p = getattr(settings, "printer_settings_path", "") or ""
    if p:
        return p
    gw = settings.gateway_path or config.DEFAULT_GATEWAY_PATH
    return os.path.join(os.path.dirname(gw), FILE_NAME)


# ── Данные ───────────────────────────────────────────────────────────────────
class SettingsModel:
    """settings_printer.json: чтение, проверка и запись значений с сохранением типов."""

    def __init__(self, path: str):
        self.path = path
        self.data: OrderedDict = OrderedDict()
        self.changed: set[tuple[str, int | None]] = set()

    def load(self) -> None:
        with open(self.path, "r", encoding="utf-8-sig") as f:
            self.data = json.load(f, object_pairs_hook=OrderedDict)
        self.changed.clear()

    def has(self, key: str) -> bool:
        return key in self.data

    def get(self, key: str, idx: int | None = None):
        v = self.data.get(key)
        if idx is None:
            return v
        if isinstance(v, list) and 0 <= idx < len(v):
            return v[idx]
        return None

    def is_int(self, key: str, idx: int | None = None) -> bool:
        v = self.data.get(key)
        if isinstance(v, list):
            vals = [x for x in v if x is not None]
            return bool(vals) and all(isinstance(x, int) and not isinstance(x, bool) for x in vals)
        return isinstance(v, int) and not isinstance(v, bool)

    def parse(self, key: str, idx: int | None, text: str):
        """Текст → значение нужного типа. ValueError с понятным сообщением."""
        t = text.strip().replace(",", ".").replace("−", "-")
        if not t:
            raise ValueError("Введите число")
        if self.is_int(key, idx):
            try:
                f = float(t)
            except ValueError:
                raise ValueError("Нужно целое число") from None
            if f != int(f):
                raise ValueError("Здесь только целое число")
            return int(f)
        try:
            return float(t)
        except ValueError:
            raise ValueError("Нужно число") from None

    def set(self, key: str, idx: int | None, value) -> bool:
        """Записать значение; True — изменилось."""
        if idx is None:
            old = self.data.get(key)
            self.data[key] = value
        else:
            arr = self.data[key]
            old = arr[idx]
            arr[idx] = value
        if old != value or type(old) is not type(value):
            self.changed.add((key, idx))
            return True
        return False

    @staticmethod
    def fmt(v) -> str:
        if isinstance(v, float):
            s = f"{v:.6f}".rstrip("0").rstrip(".")
            return s if s not in ("", "-0") else "0"
        return "—" if v is None else str(v)

    def save(self) -> str:
        """Записать файл (копия прежнего — .bak). Возвращает путь копии или ''."""
        bak = ""
        if os.path.exists(self.path):
            bak = self.path + ".bak"
            shutil.copy2(self.path, bak)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, self.path)
        self.changed.clear()
        return bak

    def known_keys(self) -> set[str]:
        keys = set()
        for b in (1, 2):
            keys |= {f"{k}{b}" for k, _ in MOTOR_ROWS} | {f"motors_count{b}"}
        for side in ("left", "right"):
            keys |= {f"take_{side}_manip_{k}" for k, _ in MANIP_ROWS}
        keys |= {"calibrate_nossle_x", "calibrate_nossle_y", "calibrate_nossle_z", "calibrate_nossles_offset_z",
                 "offset_plate_x", "offset_plate_y", "offset_plate_z"}
        keys |= {k for k, _ in TABLE_SCALARS + PLANNER_SCALARS + LASER_SCALARS}
        return keys | HIDDEN_KEYS

    def other_keys(self) -> list[str]:
        known = self.known_keys() - {"motors_count1", "motors_count2"}
        return [k for k in self.data if k not in known]


# ── Таблица ──────────────────────────────────────────────────────────────────
REF = Qt.UserRole + 1


class SettingsTable(QtWidgets.QTableWidget):
    """Таблица значений: строки — параметры, столбцы — моторы/точки/подложки.
    Ячейка помнит (ключ, индекс) в REF. Правка — в панели ввода справа."""

    def __init__(self, model: SettingsModel, rows: list[tuple[str, str]], columns: list[str] | None):
        rows = [(k, t) for k, t in rows if model.has(k)]
        super().__init__(len(rows), len(columns) if columns else 1)
        self.model = model
        self.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        self.setHorizontalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        self.setVerticalHeaderLabels([t for _, t in rows])
        self.setHorizontalHeaderLabels(columns or ["Значение"])
        self.verticalHeader().setDefaultSectionSize(50)
        self.verticalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Fixed)
        self.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        self.horizontalHeader().setMinimumSectionSize(90)
        for r, (key, _) in enumerate(rows):
            for c in range(self.columnCount()):
                idx = c if columns else None
                v = model.get(key, idx)
                it = QtWidgets.QTableWidgetItem(model.fmt(v))
                it.setTextAlignment(Qt.AlignCenter)
                if columns and v is None:
                    it.setFlags(Qt.NoItemFlags)                  # массив короче — ячейки нет
                else:
                    it.setData(REF, (key, idx))
                self.setItem(r, c, it)
        h = self.horizontalHeader().height() if self.horizontalHeader().height() > 0 else 44
        self.setMinimumHeight(min(len(rows), 9) * 50 + h + 6)

    def refresh_item(self, it: QtWidgets.QTableWidgetItem):
        key, idx = it.data(REF)
        it.setText(self.model.fmt(self.model.get(key, idx)))
        changed = (key, idx) in self.model.changed
        f = it.font()
        f.setBold(changed)
        it.setFont(f)
        it.setBackground(QtGui.QBrush(QtGui.QColor(C["accent_soft"])) if changed else QtGui.QBrush())


# ── Окно ─────────────────────────────────────────────────────────────────────
class PrinterSettingsDialog(QtWidgets.QDialog):
    def __init__(self, parent, ctx):
        super().__init__(parent, Qt.Dialog | Qt.FramelessWindowHint)
        self.ctx = ctx
        self.setObjectName("PowerDialog")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setModal(True)
        self.model: SettingsModel | None = None
        self.tables: list[SettingsTable] = []
        self._cur: tuple[SettingsTable, QtWidgets.QTableWidgetItem] | None = None

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(28, 22, 28, 22)
        lay.setSpacing(14)
        head = QtWidgets.QHBoxLayout()
        head.addWidget(label("Настройки принтера", "DialogTitle"))
        head.addStretch(1)
        self.path_lbl = label("", "Muted")
        head.addWidget(self.path_lbl)
        head.addWidget(button("Файл…", "small", self._choose_file))
        lay.addLayout(head)
        bar = QtWidgets.QFrame()
        bar.setObjectName("BrandBar")
        bar.setFixedHeight(5)
        lay.addWidget(bar)

        self.sections_box = QtWidgets.QVBoxLayout()
        self.sections_box.setSpacing(10)
        lay.addLayout(self.sections_box)

        body = QtWidgets.QHBoxLayout()
        body.setSpacing(20)
        self.stack = QtWidgets.QStackedWidget()
        body.addWidget(self.stack, 1)
        body.addWidget(self._input_panel())
        lay.addLayout(body, 1)

        self.msg = label("", "Muted", wrap=True)
        lay.addWidget(self.msg)
        foot = QtWidgets.QHBoxLayout()
        foot.addStretch(1)
        b_cancel = button("Закрыть", None, self._close)
        self.b_save = button("Сохранить", None, self._save)
        self.b_send = button("Сохранить и отправить в принтер", "primary", self._save_send)
        for b in (b_cancel, self.b_save, self.b_send):
            b.setMinimumHeight(72)
            foot.addWidget(b)
        lay.addLayout(foot)

        self._load(default_path(ctx.settings))

    # ── панель ввода (экранная цифровая клавиатура) ──
    def _input_panel(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        w.setFixedWidth(380)
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)
        self.cell_lbl = label("Выберите ячейку", "CardTitle", wrap=True)
        v.addWidget(self.cell_lbl)
        self.edit = QtWidgets.QLineEdit()
        self.edit.setMinimumHeight(64)
        self.edit.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.edit.setStyleSheet("font-size:28px; font-weight:700;")
        self.edit.returnPressed.connect(self._commit)
        self.edit.setEnabled(False)
        v.addWidget(self.edit)
        self.err = label("", "Muted", wrap=True)
        self.err.setStyleSheet(f"color:{C['danger']}; font-weight:600;")
        v.addWidget(self.err)
        pad = QtWidgets.QGridLayout()
        pad.setSpacing(8)
        keys = ["7", "8", "9", "⌫", "4", "5", "6", "C", "1", "2", "3", "−", "0", ".", "↵"]
        pos = [(0, 0), (0, 1), (0, 2), (0, 3), (1, 0), (1, 1), (1, 2), (1, 3),
               (2, 0), (2, 1), (2, 2), (2, 3), (3, 0), (3, 1), (3, 2)]
        self.keys = []
        for k, (r, c) in zip(keys, pos):
            b = button(k, "primary" if k == "↵" else None, lambda k=k: self._key(k))
            b.setFocusPolicy(Qt.NoFocus)
            b.setMinimumSize(80, 68)
            b.setStyleSheet("font-size:24px; font-weight:700;")
            pad.addWidget(b, r, c, 1, 2 if k == "↵" else 1)
            self.keys.append(b)
        v.addLayout(pad)
        v.addWidget(label("↵ — записать и перейти к следующей ячейке. Изменённые значения выделены.",
                          "Muted", wrap=True))
        v.addStretch(1)
        return w

    def _key(self, k: str):
        if not self.edit.isEnabled():
            return
        e = self.edit
        if k == "⌫":
            e.backspace()
        elif k == "C":
            e.clear()
        elif k == "−":
            t = e.text()
            e.setText(t[1:] if t.startswith("-") else "-" + t)
        elif k == "↵":
            self._commit()
        else:
            e.insert(k)
        self.err.setText("")

    # ── файл ──
    def _load(self, path: str):
        self.path_lbl.setText(path)
        self.path_lbl.setToolTip(path)
        m = SettingsModel(path)
        try:
            m.load()
        except FileNotFoundError:
            self._fail_load(f"Файл не найден: {path}\nУкажите его кнопкой «Файл…» "
                            f"(лежит рядом с tcp_to_udp.exe).")
            return
        except (OSError, ValueError) as e:
            self._fail_load(f"Не удалось прочитать {path}:\n{e}")
            return
        self.model = m
        self._build_sections()
        self.msg.setText("")
        self._sync_buttons()

    def _fail_load(self, text: str):
        self.model = None
        self._clear_sections()
        self.msg.setText(text)
        self._sync_buttons()

    def _choose_file(self):
        start = os.path.dirname(self.path_lbl.text()) or os.getcwd()
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Файл настроек принтера", start,
                                                        "Настройки (*.json);;Все файлы (*)")
        if not path:
            return
        if not self._confirm_discard():
            return
        self.ctx.settings.printer_settings_path = path
        self.ctx.settings.save()
        self._load(path)

    # ── разделы ──
    def _clear_sections(self):
        while self.sections_box.count():
            it = self.sections_box.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        while self.stack.count():
            w = self.stack.widget(0)
            self.stack.removeWidget(w)
            w.deleteLater()
        self.tables = []
        self._select(None, None)

    def _build_sections(self):
        self._clear_sections()
        m = self.model
        sections: list[tuple[str, QtWidgets.QWidget]] = []
        for b in (1, 2):
            n = len(m.get(f"a_max{b}") or m.get(f"steps_per_mm{b}") or [])
            if n:
                names = (MOTORS[b] + [f"Ось {i}" for i in range(8, n)])[:n]
                sections.append((f"Моторы платы {b}",
                                 self._page([(None, [(f"{k}{b}", t) for k, t in MOTOR_ROWS], names)])))
        parts = []
        for side, title in (("left", "Левый манипулятор"), ("right", "Правый манипулятор")):
            rows = [(f"take_{side}_manip_{k}", t) for k, t in MANIP_ROWS]
            n = max((len(m.get(k) or []) for k, _ in rows), default=0)
            if n:
                parts.append((title, rows, [f"Точка {i + 1}" for i in range(n)]))
        if parts:
            sections.append(("Смена головок", self._page(parts)))
        parts = []
        rows = [(f"calibrate_nossle_{a}", a.upper()) for a in "xyz"]
        n = max((len(m.get(k) or []) for k, _ in rows), default=0)
        if n:
            parts.append(("Точки калибровки", rows, [f"Точка {i + 1}" for i in range(n)]))
        n = len(m.get("calibrate_nossles_offset_z") or [])
        if n:
            parts.append(("Смещение Z головок", [("calibrate_nossles_offset_z", "Смещение Z")],
                          [f"Головка {i + 1}" for i in range(n)]))
        if parts:
            sections.append(("Калибровка сопел", self._page(parts)))
        parts = []
        rows = [(f"offset_plate_{a}", a.upper()) for a in "xyz"]
        n = max((len(m.get(k) or []) for k, _ in rows), default=0)
        if n:
            names = plate_names()
            parts.append(("Подложки (G54 X<номер>)", rows,
                          [f"{i}: {names[i]}" if i in names else str(i) for i in range(n)]))
        if any(m.has(k) for k, _ in LASER_SCALARS):
            parts.append(("Лазерная система", LASER_SCALARS, None))
        if parts:
            sections.append(("Подложки", self._page(parts)))
        if any(m.has(k) for k, _ in TABLE_SCALARS):
            sections.append(("Стол и сервоприводы", self._page([(None, TABLE_SCALARS, None)])))
        if any(m.has(k) for k, _ in PLANNER_SCALARS):
            sections.append(("Планировщик", self._page([(None, PLANNER_SCALARS, None)])))
        other = m.other_keys()
        if other:
            scal = [(k, k) for k in other if not isinstance(m.get(k), (list, dict))]
            parts = [(None, scal, None)] if scal else []
            for k in other:
                v = m.get(k)
                if isinstance(v, list) and v and all(isinstance(x, (int, float)) for x in v):
                    parts.append((k, [(k, k)], [str(i) for i in range(len(v))]))
            if parts:
                sections.append(("Прочее", self._page(parts)))

        names = [s for s, _ in sections]
        for _, w in sections:
            self.stack.addWidget(w)
        seg = Segmented([(n, i) for i, n in enumerate(names)], 0)
        seg.changed.connect(lambda i: (self.stack.setCurrentIndex(i), self._select(None, None)))
        self.sections_box.addWidget(seg)

    def _page(self, parts) -> QtWidgets.QWidget:
        inner = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(inner)
        v.setContentsMargins(0, 0, 8, 0)
        v.setSpacing(10)
        for title, rows, columns in parts:
            if title:
                v.addWidget(label(title, "CardTitle"))
            t = SettingsTable(self.model, rows, columns)
            t.itemClicked.connect(lambda it, t=t: self._select(t, it))
            t.currentItemChanged.connect(lambda it, _prev, t=t: self._select(t, it) if it else None)
            self.tables.append(t)
            v.addWidget(t)
        v.addStretch(1)
        sa = QtWidgets.QScrollArea()
        sa.setWidget(inner)
        sa.setWidgetResizable(True)
        sa.setFrameShape(QtWidgets.QFrame.NoFrame)
        return sa

    # ── правка ──
    def _select(self, table, item):
        if table is None or item is None or not item.data(REF):
            self._cur = None
            self.cell_lbl.setText("Выберите ячейку")
            self.edit.clear()
            self.edit.setEnabled(False)
            self.err.setText("")
            return
        self._cur = (table, item)
        r, c = item.row(), item.column()
        row_name = table.verticalHeaderItem(r).text() if table.verticalHeaderItem(r) else ""
        col_name = table.horizontalHeaderItem(c).text() if table.columnCount() > 1 and table.horizontalHeaderItem(c) else ""
        self.cell_lbl.setText(" · ".join(x for x in (col_name, row_name) if x))
        key, idx = item.data(REF)
        self.edit.setEnabled(True)
        self.edit.setText(self.model.fmt(self.model.get(key, idx)))
        self.edit.setPlaceholderText("целое" if self.model.is_int(key, idx) else "число")
        self.edit.selectAll()
        self.edit.setFocus()
        self.err.setText("")

    def _commit(self):
        if not self._cur:
            return
        table, item = self._cur
        key, idx = item.data(REF)
        try:
            value = self.model.parse(key, idx, self.edit.text())
        except ValueError as e:
            self.err.setText(str(e))
            return
        self.model.set(key, idx, value)
        table.refresh_item(item)
        self._sync_buttons()
        # следующая ячейка: вниз по столбцу, затем следующий столбец
        r, c = item.row(), item.column()
        for rr, cc in [(r + 1, c), (0, c + 1)]:
            if rr < table.rowCount() and cc < table.columnCount():
                nxt = table.item(rr, cc)
                if nxt is not None and nxt.data(REF):
                    table.setCurrentItem(nxt)
                    self._select(table, nxt)
                    return
        self._select(table, item)

    # ── сохранение ──
    def _sync_buttons(self):
        ok = self.model is not None
        n = len(self.model.changed) if ok else 0
        self.b_save.setEnabled(ok)
        busy = self.ctx.job.active
        self.b_send.setEnabled(ok and self.ctx.link.connected and not busy)
        self.b_send.setToolTip("Идёт печать — отправка настроек недоступна" if busy else
                               "Нет связи с принтером" if not self.ctx.link.connected else
                               "Записать файл и загрузить настройки в платы")
        if ok:
            self.msg.setText(f"Изменено значений: {n}" if n else "")

    def _write(self) -> bool:
        try:
            bak = self.model.save()
        except OSError as e:
            self.msg.setText(f"Не удалось записать файл:\n{e}")
            return False
        for t in self.tables:
            for r in range(t.rowCount()):
                for c in range(t.columnCount()):
                    it = t.item(r, c)
                    if it is not None and it.data(REF):
                        t.refresh_item(it)
        self.msg.setText("Сохранено" + (f" (прежний файл — {os.path.basename(bak)})" if bak else ""))
        return True

    def _save(self):
        if self.model and self._write():
            self._sync_buttons_keep_msg()

    def _save_send(self):
        if not self.model or not self._write():
            return
        if self.ctx.link.send(APPLY_CMD):
            self.msg.setText(self.msg.text() + ". Настройки отправлены в принтер.")
        self._sync_buttons_keep_msg()

    def _sync_buttons_keep_msg(self):
        text = self.msg.text()
        self._sync_buttons()
        self.msg.setText(text)

    def _confirm_discard(self) -> bool:
        if not self.model or not self.model.changed:
            return True
        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Question)
        box.setWindowTitle("Настройки принтера")
        box.setText(f"Изменено значений: {len(self.model.changed)}. Закрыть без сохранения?")
        yes = box.addButton("Закрыть без сохранения", QtWidgets.QMessageBox.AcceptRole)
        box.addButton("Отмена", QtWidgets.QMessageBox.RejectRole)
        box.exec_()
        return box.clickedButton() is yes

    def _close(self):
        if self._confirm_discard():
            self.accept()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self._close()
            return
        super().keyPressEvent(e)

    def showEvent(self, e):
        super().showEvent(e)
        p = self.parentWidget()
        win = p.window() if p is not None else None
        if win is not None:
            g = win.geometry()
            w, h = min(1700, g.width() - 80), min(980, g.height() - 60)
            self.resize(w, h)
            self.move(g.x() + (g.width() - w) // 2, g.y() + (g.height() - h) // 2)


def open_dialog(parent, ctx) -> None:
    PrinterSettingsDialog(parent, ctx).exec_()
