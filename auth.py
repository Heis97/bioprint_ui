"""Пароль входа в программу.

Где хранится: в settings.json рядом с программой (в exe-версии — рядом с Bioprint.exe),
поле "password": {"salt", "hash", "iter"}. Сам пароль не хранится — только хэш
PBKDF2-HMAC-SHA256 со случайной солью, поэтому по файлу пароль не узнать.

Как задаётся:
- первый запуск (поля нет) — окно «Задайте пароль»;
- сменить — «Консоль → Интерфейс → Сменить пароль» (нужен текущий пароль);
- забыли — удалите строку "password" из settings.json (закройте программу перед правкой):
  при следующем запуске программа попросит задать новый.

Ввод — с экранной цифровой клавиатуры или с обычной клавиатуры (можно буквы).
"""
from __future__ import annotations

import hashlib
import hmac
import os

from PyQt5 import QtCore, QtWidgets
from PyQt5.QtCore import Qt

from . import config
from .widgets import button, label

ITERATIONS = 200_000
MIN_LEN = 4


def make_record(password: str) -> dict:
    salt = os.urandom(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
    return {"salt": salt.hex(), "hash": h.hex(), "iter": ITERATIONS}


def verify(password: str, rec: dict) -> bool:
    try:
        salt = bytes.fromhex(rec["salt"])
        want = bytes.fromhex(rec["hash"])
        it = int(rec.get("iter", ITERATIONS))
    except (KeyError, ValueError, TypeError):
        return False
    got = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, it)
    return hmac.compare_digest(got, want)


def is_set(settings: config.UserSettings) -> bool:
    rec = settings.password
    return isinstance(rec, dict) and bool(rec.get("hash")) and bool(rec.get("salt"))


class PasswordDialog(QtWidgets.QDialog):
    """Вход / задание / смена пароля. Экранная цифровая клавиатура пишет в активное поле."""

    LOGIN, CREATE, CHANGE = "login", "create", "change"

    def __init__(self, settings: config.UserSettings, mode: str, parent=None):
        super().__init__(parent, Qt.Dialog | Qt.FramelessWindowHint)
        self.settings, self.mode = settings, mode
        self.setObjectName("PowerDialog")               # оформление как у окна «Завершение работы»
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setModal(True)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(36, 30, 36, 30)
        lay.setSpacing(16)
        titles = {self.LOGIN: f"{config.APP_NAME} — вход", self.CREATE: "Задайте пароль",
                  self.CHANGE: "Смена пароля"}
        lay.addWidget(label(titles[mode], "DialogTitle"))
        bar = QtWidgets.QFrame()
        bar.setObjectName("BrandBar")
        bar.setFixedHeight(5)
        lay.addWidget(bar)
        hints = {
            self.LOGIN: "Введите пароль.",
            self.CREATE: f"Пароль ещё не задан. Придумайте пароль (не короче {MIN_LEN} символов) — "
                         "он будет нужен при каждом запуске.",
            self.CHANGE: "Введите текущий пароль и новый.",
        }
        lay.addWidget(label(hints[mode], "CardHint", wrap=True))

        self.fields: list[QtWidgets.QLineEdit] = []
        names = {self.LOGIN: ["Пароль"], self.CREATE: ["Новый пароль", "Ещё раз"],
                 self.CHANGE: ["Текущий пароль", "Новый пароль", "Ещё раз"]}[mode]
        form = QtWidgets.QGridLayout()
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)
        for i, n in enumerate(names):
            e = QtWidgets.QLineEdit()
            e.setEchoMode(QtWidgets.QLineEdit.Password)
            e.setMinimumHeight(56)
            e.returnPressed.connect(self._next_or_ok)
            e.installEventFilter(self)
            form.addWidget(label(n, "Muted"), i, 0)
            form.addWidget(e, i, 1)
            self.fields.append(e)
        form.setColumnStretch(1, 1)
        lay.addLayout(form)
        self._active = self.fields[0]

        self.err = label("", "Muted", wrap=True)
        self.err.setStyleSheet("color:#E0312B; font-weight:600;")
        lay.addWidget(self.err)

        pad = QtWidgets.QGridLayout()
        pad.setSpacing(10)
        keys = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "⌫", "0", "C"]
        for i, k in enumerate(keys):
            b = button(k, None, lambda k=k: self._key(k))
            b.setMinimumSize(110, 72)
            b.setStyleSheet("font-size:26px; font-weight:700;")
            pad.addWidget(b, i // 3, i % 3)
        lay.addLayout(pad)

        row = QtWidgets.QHBoxLayout()
        self.show_btn = button("Показать", None, self._toggle_show)
        self.show_btn.setCheckable(True)
        row.addWidget(self.show_btn)
        row.addStretch(1)
        cancel = "Выход" if mode == self.LOGIN else "Отмена"
        b_cancel = button(cancel, None, self.reject)
        b_ok = button("Войти" if mode == self.LOGIN else "Сохранить", "primary", self._ok)
        for b in (b_cancel, b_ok):
            b.setMinimumHeight(72)
            b.setMinimumWidth(180)
            row.addWidget(b)
        lay.addLayout(row)
        self.setFixedWidth(620)
        self.fields[0].setFocus()

    # --- ввод ---
    def eventFilter(self, obj, ev):
        if ev.type() == QtCore.QEvent.FocusIn and obj in self.fields:
            self._active = obj
        return super().eventFilter(obj, ev)

    def _key(self, k: str):
        e = self._active
        if k == "⌫":
            e.backspace()
        elif k == "C":
            e.clear()
        else:
            e.insert(k)
        self.err.setText("")

    def _toggle_show(self):
        mode = QtWidgets.QLineEdit.Normal if self.show_btn.isChecked() else QtWidgets.QLineEdit.Password
        for e in self.fields:
            e.setEchoMode(mode)

    def _next_or_ok(self):
        i = self.fields.index(self._active) if self._active in self.fields else 0
        if i + 1 < len(self.fields):
            self.fields[i + 1].setFocus()
        else:
            self._ok()

    def _fail(self, text: str, field: int = 0):
        self.err.setText(text)
        for e in self.fields[field:]:
            e.clear()
        self.fields[field].setFocus()

    def _ok(self):
        vals = [e.text() for e in self.fields]
        if self.mode == self.LOGIN:
            if verify(vals[0], self.settings.password):
                self.accept()
            else:
                self.setEnabled(False)                 # пауза после неверного ввода
                QtCore.QTimer.singleShot(1000, lambda: (self.setEnabled(True),
                                                        self._fail("Неверный пароль")))
            return
        if self.mode == self.CHANGE and not verify(vals[0], self.settings.password):
            self._fail("Текущий пароль неверный")
            return
        new, again = vals[-2], vals[-1]
        first_new = len(vals) - 2
        if len(new) < MIN_LEN:
            self._fail(f"Пароль должен быть не короче {MIN_LEN} символов", first_new)
            return
        if new != again:
            self._fail("Пароли не совпадают", first_new)
            return
        self.settings.password = make_record(new)
        self.settings.save()
        self.accept()

    def showEvent(self, e):
        super().showEvent(e)
        self.adjustSize()
        p = self.parentWidget()
        if p is not None:
            c = p.mapToGlobal(p.rect().center())
        else:
            c = QtWidgets.QApplication.primaryScreen().availableGeometry().center()
        self.move(c.x() - self.width() // 2, c.y() - self.height() // 2)


def login(settings: config.UserSettings) -> bool:
    """Окно входа при запуске; если пароля нет — сначала задать. True — можно работать."""
    if not is_set(settings):
        if PasswordDialog(settings, PasswordDialog.CREATE).exec_() != QtWidgets.QDialog.Accepted:
            return False
        return True
    return PasswordDialog(settings, PasswordDialog.LOGIN).exec_() == QtWidgets.QDialog.Accepted


def change(settings: config.UserSettings, parent=None) -> bool:
    mode = PasswordDialog.CHANGE if is_set(settings) else PasswordDialog.CREATE
    return PasswordDialog(settings, mode, parent).exec_() == QtWidgets.QDialog.Accepted
