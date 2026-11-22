"""Камеры: приём JPEG-кадров по UDP (по образцу CameraThreadUDP из String_line4), снимки.

Шлюз (StreamVideo) шлёт каждый кадр одной UDP-дейтаграммой с JPEG. Для каждой камеры
настраивается IP источника (от кого принимать; пусто — от любого) и UDP-порт приёма.
"""
from __future__ import annotations

import errno
import os
import socket
import struct
import threading
import time

from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtCore import Qt

from .. import config
from ..context import Ctx
from ..protocol import Cmd
from ..theme import C
from ..widgets import Card, ElidedLabel, JogPad, Stepper, ToggleButton, button, label
from .manual import JogSettings, jog_handlers

try:
    import cv2
    import numpy as np
except ImportError:          # камеры просто не будут работать
    cv2 = None

MAX_W = 1920                 # кадр шире уменьшается (FullHD показывается как есть)

# Кадр из нескольких UDP-пакетов (FullHD не помещается в один пакет ~64 КБ).
# Каждая часть: заголовок 12 байт + кусок JPEG. Заголовок (big-endian):
#   "BPF1" | номер кадра uint32 | номер части uint16 | число частей uint16
# Пакет без заголовка — целый JPEG в одном пакете (прежний формат шлюза).
FRAG_MAGIC = b"BPF1"
FRAG_HEADER = struct.Struct(">4sIHH")


class FrameAssembler:
    """Сборка кадров из частей. Неполные кадры отбрасываются, как только пришёл кадр новее."""

    MAX_PENDING = 3

    def __init__(self):
        self.pending: dict[int, list] = {}      # номер кадра → [частей всего, {номер: байты}]
        self.last_done = -1
        self.lost = 0                            # кадров выброшено недособранными (потеря пакетов)

    def push(self, data: bytes) -> bytes | None:
        """Вернуть целый JPEG, если этот пакет его завершил (или это кадр в одном пакете)."""
        if not data.startswith(FRAG_MAGIC) or len(data) < FRAG_HEADER.size:
            return data
        _, fid, idx, count = FRAG_HEADER.unpack_from(data)
        if count == 0 or idx >= count:
            return None
        if fid <= self.last_done and self.last_done - fid < 1 << 31:
            return None                              # опоздавшая часть уже показанного кадра
        entry = self.pending.setdefault(fid, [count, {}])
        entry[1][idx] = data[FRAG_HEADER.size:]
        if len(entry[1]) < entry[0]:
            if len(self.pending) > self.MAX_PENDING:     # потерянные части — кадр не дождёмся
                del self.pending[min(self.pending)]
                self.lost += 1
            return None
        parts = entry[1]
        jpeg = b"".join(parts[i] for i in range(entry[0]))
        self.last_done = fid
        for k in [k for k in self.pending if k <= fid]:
            if k < fid:
                self.lost += 1                   # более старый кадр так и не собрался
            del self.pending[k]
        return jpeg
FRAME_POLL_MS = 15           # проверка новых кадров; отрисовка — только когда кадр новый (до 30 к/с и выше)
NO_SIGNAL_S = 2.0


def parse_ips(text: str) -> list[str]:
    """«192.168.1.81, 192.168.1.82» → список; пусто или * — принимать от всех."""
    parts = [p.strip() for p in text.replace(";", ",").replace(" ", ",").split(",")]
    parts = [p for p in parts if p]
    return [] if parts in ([], ["*"]) else parts


def valid_ip(ip: str) -> bool:
    parts = ip.split(".")
    return len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)


class CameraThread(QtCore.QThread):
    """Приёмник одной камеры. Кадр не отправляется сигналом: поток кладёт последний
    кадр в latest, а экран забирает его по таймеру (старые кадры просто пропускаются)."""

    def __init__(self, index: int, ip: str, port: int, bind: str = config.DEFAULT_CAMERA_BIND):
        super().__init__()
        self.index, self.port, self.bind = index, int(port), bind or "0.0.0.0"
        self.allowed = parse_ips(ip)
        self._running = True
        self._lock = threading.Lock()
        self.latest: QtGui.QImage | None = None
        self.seq = 0                 # номер последнего кадра
        self.last_rx = 0.0           # время последнего кадра
        self.error = ""              # ошибка открытия порта
        self.rejected_from = ""      # последний «чужой» отправитель (для подсказки)
        self.rejected_at = 0.0
        self.bad_frames = 0
        self.asm: FrameAssembler | None = None

    def stop(self):
        self._running = False

    def take(self) -> tuple[int, QtGui.QImage | None]:
        with self._lock:
            return self.seq, self.latest

    def run(self):
        if cv2 is None:
            self.error = "нет opencv"
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 16 * 1024 * 1024)   # FullHD: ~10 частей на кадр
        except OSError:
            pass
        try:
            sock.bind((self.bind, self.port))
        except OSError as e:
            if e.errno in (errno.EADDRINUSE, 10048):
                self.error = f"порт {self.port} занят"
            elif e.errno in (errno.EADDRNOTAVAIL, 10049):
                self.error = f"нет интерфейса {self.bind}"
            elif e.errno in (errno.EACCES, 10013):
                self.error = f"порт {self.port} недоступен"
            else:
                self.error = f"ошибка порта: {e.strerror or e}"
            sock.close()
            return
        sock.settimeout(0.5)
        asm = self.asm = FrameAssembler()
        while self._running:
            try:
                data, addr = sock.recvfrom(65536)
            except socket.timeout:
                continue
            except OSError as e:
                # Windows: ICMP «порт недоступен» от прошлой отправки — не причина закрываться
                if getattr(e, "winerror", None) == 10054 or e.errno == errno.ECONNRESET:
                    continue
                break
            src = str(addr[0])
            if self.allowed and src not in self.allowed:
                self.rejected_from, self.rejected_at = src, time.time()
                continue
            data = asm.push(data)
            if data is None:
                continue
            img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                self.bad_frames += 1
                continue
            h, w = img.shape[:2]
            if w > MAX_W:
                img = cv2.resize(img, (MAX_W, int(round(h * MAX_W / w))), interpolation=cv2.INTER_AREA)
            rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            h, w, _ = rgb.shape
            q = QtGui.QImage(rgb.data, w, h, rgb.strides[0], QtGui.QImage.Format_RGB888).copy()
            with self._lock:
                self.latest = q
                self.seq += 1
                self.last_rx = time.time()
        sock.close()


class CamView(QtWidgets.QLabel):
    clicked = QtCore.pyqtSignal()

    def __init__(self, name: str, smooth: bool = True):
        super().__init__()
        self.name = name
        self.smooth = smooth
        self.img: QtGui.QImage | None = None
        self.note = "нет сигнала"
        self.setMinimumSize(320, 180)
        self.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Ignored)
        self.setAlignment(Qt.AlignCenter)
        self.border = ""
        self.theme_changed()
        self.setText(f"{name}\n{self.note}")

    def set_border(self, color_key: str):
        self.border = color_key
        self.theme_changed()

    def theme_changed(self):
        border = f"border:4px solid {C[self.border]};" if self.border else "border:4px solid transparent;"
        size = 18 if self.smooth else 16
        self.setStyleSheet(f"background:{C['cam_bg']}; color:{C['nav_text']}; border-radius:12px;"
                           f"font-size:{size}px; {border}")

    def set_image(self, img: QtGui.QImage):
        self.img = img
        self._render()

    def clear(self, note: str = "нет сигнала"):
        self.img = None
        self.note = note
        self.setPixmap(QtGui.QPixmap())
        self.setText(f"{self.name}\n{note}")

    def _render(self):
        if self.img is None:
            return
        mode = Qt.SmoothTransformation if self.smooth else Qt.FastTransformation
        self.setPixmap(QtGui.QPixmap.fromImage(self.img).scaled(self.size(), Qt.KeepAspectRatio, mode))

    def resizeEvent(self, e):
        self._render()
        super().resizeEvent(e)

    def mouseReleaseEvent(self, e):
        self.clicked.emit()


class CamRow(QtWidgets.QWidget):
    """Строка настройки камеры: имя и состояние, IP источника, порт."""

    def __init__(self, name: str, ip: str, port: int):
        super().__init__()
        lay = QtWidgets.QGridLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setHorizontalSpacing(10)
        lay.setVerticalSpacing(6)
        self.name = label(name, "Muted")
        self.status = ElidedLabel("выкл", "Muted")
        self.status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.ip = QtWidgets.QLineEdit(ip)
        self.ip.setPlaceholderText("любой IP")
        self.ip.setToolTip("От кого принимать кадры. Пусто — от любого адреса; несколько — через запятую")
        self.port = QtWidgets.QLineEdit(str(port))
        self.port.setValidator(QtGui.QIntValidator(1, 65535, self))
        self.port.setFixedWidth(110)
        self.port.setAlignment(Qt.AlignCenter)
        self.port.setToolTip("UDP-порт приёма на этом компьютере")
        lay.addWidget(self.name, 0, 0)
        lay.addWidget(self.status, 0, 1)
        lay.addWidget(self.ip, 1, 0)
        lay.addWidget(self.port, 1, 1, Qt.AlignRight)
        lay.setColumnStretch(0, 1)
        lay.setColumnStretch(1, 1)

    def values(self) -> tuple[str, str]:
        return self.ip.text().strip(), self.port.text().strip()

    def set_values(self, ip: str, port: int):
        self.ip.setText(ip)
        self.port.setText(str(port))

    def set_status(self, text: str, level: str = "muted"):
        if level != getattr(self, "_level", None):
            self._level = level
            self.theme_changed()
        if self.status.full_text() != text:
            self.status.set_text(text)

    def theme_changed(self):
        level = getattr(self, "_level", "muted")
        self.status.setStyleSheet(f"color:{C[level]}; font-weight:600;" if level in ("ok", "warn", "danger") else "")


class CamerasPage(QtWidgets.QWidget):
    title = "Камеры"

    def __init__(self, ctx: Ctx):
        super().__init__()
        self.ctx = ctx
        self.threads: list[CameraThread | None] = [None] * len(config.CAMERAS)
        self.shown = [0] * len(config.CAMERAS)
        self.fps_mark = [(0, time.time())] * len(config.CAMERAS)
        self.fps = [0.0] * len(config.CAMERAS)
        self.selected = 0
        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 28)
        root.setSpacing(20)

        main = Card("")
        self.big = CamView("")
        main.add(self.big, 1)
        thumbs = QtWidgets.QHBoxLayout()
        thumbs.setSpacing(16)
        main.add(thumbs)
        root.addWidget(main, 1)

        side = QtWidgets.QVBoxLayout()
        side.setSpacing(16)

        # ── приём UDP ──
        net = Card("Видео по UDP")
        self.toggle = ToggleButton("Приём", self._enable)
        net.add_to_header(self.toggle)
        head = QtWidgets.QHBoxLayout()
        head.addWidget(label("IP источника", "Muted"), 1)
        head.addWidget(label("Порт", "Muted"))
        net.add(head)
        self.rows: list[CamRow] = []
        s = ctx.settings
        for i, cam in enumerate(config.CAMERAS):
            c = s.cameras[i]
            r = CamRow(cam["name"], c["ip"], c["port"])
            self.rows.append(r)
            net.add(r)
        bind = QtWidgets.QHBoxLayout()
        bind.setSpacing(10)
        bind.addWidget(label("Приём на", "Muted"))
        self.bind = QtWidgets.QLineEdit(s.camera_bind)
        self.bind.setPlaceholderText("0.0.0.0")
        self.bind.setToolTip("Адрес сетевой карты этого ПК для приёма; 0.0.0.0 — все")
        bind.addWidget(self.bind, 1)
        net.add(bind)
        btns = QtWidgets.QHBoxLayout()
        btns.setSpacing(10)
        btns.addWidget(button("Применить", "primary", self._apply,
                              "Сохранить адреса и порты и перезапустить приём"), 1)
        btns.addWidget(button("По умолчанию", None, self._defaults))
        net.add(btns)
        self.net_msg = ElidedLabel("", "Muted")
        net.add(self.net_msg)
        side.addWidget(net)

        # ── выбранная камера ──
        ctl = Card("Выбранная камера")
        ctl.add(label("Экспозиция", "Muted"))
        self.expo = Stepper(-7, -13, -1, 1)          # диапазон камер A4tech FHD: −13…−1
        self.expo.valueChanged.connect(lambda v: ctx.link.send(Cmd.camera_exposure(self.selected, v)))
        ctl.add(self.expo)
        ctl.add(button("📷  Снимок", "primary", self._snapshot))
        self.snap_lbl = ElidedLabel("", "Muted")
        ctl.add(self.snap_lbl)
        side.addWidget(ctl)

        self.thumbs: list[CamView] = []
        for i, cam in enumerate(config.CAMERAS):
            v = CamView(cam["name"], smooth=False)
            v.setFixedHeight(170)
            v.clicked.connect(lambda i=i: self._select(i))
            self.thumbs.append(v)
            thumbs.addWidget(v)
        # ── перемещение: калибровка по изображению камеры ──
        jog = Card("Перемещение")
        xy, z = jog_handlers(ctx)
        jog.add(JogPad(xy, z, on_home=lambda: ctx.link.send(Cmd.HOME), size=88, compact=True))
        jog.add(JogSettings(ctx, stacked=True))
        side.addWidget(jog)

        side.addStretch(1)
        w = QtWidgets.QWidget()
        w.setLayout(side)
        w.setFixedWidth(460)
        side.setContentsMargins(0, 0, 0, 0)
        # правая колонка прокручивается полосой (жест касания не включаем — он мешал бы
        # кнопкам перемещения «пока держишь»)
        sa = QtWidgets.QScrollArea()
        sa.setWidget(w)
        sa.setWidgetResizable(True)
        sa.setFrameShape(QtWidgets.QFrame.NoFrame)
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sa.setFixedWidth(460 + 18)
        root.addWidget(sa)

        self._frames_timer = QtCore.QTimer(self)
        self._frames_timer.timeout.connect(self._pull_frames)
        self._status_timer = QtCore.QTimer(self)
        self._status_timer.timeout.connect(self._update_status)
        self._status_timer.start(1000)

        self._select(0)
        self._update_status()
        if s.cameras_enabled:
            self.toggle.setChecked(True)

    # ── настройки ──
    def _read_form(self):
        """Проверить поля. Возвращает (cameras, bind) или None и показывает ошибку."""
        cams, ports = [], set()
        for i, r in enumerate(self.rows):
            ip_txt, port_txt = r.values()
            name = config.CAMERAS[i]["name"]
            bad = [p for p in parse_ips(ip_txt) if not valid_ip(p)]
            if bad:
                return self._err(f"{name}: неверный IP «{bad[0]}»")
            if not port_txt.isdigit() or not 1 <= int(port_txt) <= 65535:
                return self._err(f"{name}: порт должен быть от 1 до 65535")
            port = int(port_txt)
            if port in ports:
                return self._err(f"{name}: порт {port} уже занят другой камерой")
            ports.add(port)
            cams.append({"ip": ", ".join(parse_ips(ip_txt)), "port": port})
        bind = self.bind.text().strip() or config.DEFAULT_CAMERA_BIND
        if not valid_ip(bind):
            return self._err(f"Неверный адрес приёма «{bind}»")
        return cams, bind

    def _err(self, text: str):
        self._msg_err = True
        self.theme_changed()
        self.net_msg.set_text(text)
        return None

    def _msg(self, text: str):
        self._msg_err = False
        self.theme_changed()
        self.net_msg.set_text(text)

    def theme_changed(self):
        err = getattr(self, "_msg_err", False)
        self.net_msg.setStyleSheet(f"color:{C['danger']}; font-weight:600;" if err else "")

    def _save(self) -> bool:
        form = self._read_form()
        if form is None:
            return False
        cams, bind = form
        s = self.ctx.settings
        s.cameras, s.camera_bind = cams, bind
        s.save()
        for r, c in zip(self.rows, cams):
            r.set_values(c["ip"], c["port"])
        self.bind.setText(bind)
        return True

    def _apply(self):
        if not self._save():
            return
        if self.toggle.isChecked():
            self._start()
            self._msg("Сохранено, приём перезапущен")
        else:
            self._msg("Сохранено")

    def _defaults(self):
        for r, c in zip(self.rows, config.default_camera_settings()):
            r.set_values(c["ip"], c["port"])
        self.bind.setText(config.DEFAULT_CAMERA_BIND)
        self._msg("Значения по умолчанию — нажмите «Применить»")

    def _enable(self, on: bool):
        if on and not self._save():
            self.toggle.set_silent(False)
            return
        self.ctx.settings.cameras_enabled = on
        self.ctx.settings.save()
        if on:
            self._start()
            self._msg("")
        else:
            self.stop()

    # ── потоки ──
    def _start(self):
        self.stop()
        s = self.ctx.settings
        for i in range(len(config.CAMERAS)):
            c = s.cameras[i]
            t = CameraThread(i, c["ip"], c["port"], s.camera_bind)
            t.start()
            self.threads[i] = t
            self.shown[i] = 0
            self.fps_mark[i] = (0, time.time())
            self.fps[i] = 0.0
        self._frames_timer.start(FRAME_POLL_MS)
        self._update_status()

    def stop(self):
        self._frames_timer.stop()
        for t in self.threads:
            if t is not None:
                t.stop()
        for t in self.threads:
            if t is not None:
                t.wait(1500)
        self.threads = [None] * len(config.CAMERAS)
        for t in self.thumbs:
            t.clear()
        self.big.clear()
        self._update_status()

    def _pull_frames(self):
        for i, t in enumerate(self.threads):
            if t is None:
                continue
            seq, img = t.take()
            if seq != self.shown[i] and img is not None:
                self.shown[i] = seq
                self.thumbs[i].set_image(img)
                if i == self.selected:
                    self.big.set_image(img)

    def _update_status(self):
        now = time.time()
        for i, (r, t) in enumerate(zip(self.rows, self.threads)):
            if t is None:
                r.set_status("выкл")
                continue
            seq, _ = t.take()
            n0, t0 = self.fps_mark[i]
            if now - t0 >= 0.9:
                self.fps[i] = (seq - n0) / (now - t0)
                self.fps_mark[i] = (seq, now)
            if t.error:
                r.set_status(t.error, "danger")
                self._no_signal(i, t.error)
            elif t.last_rx and now - t.last_rx < NO_SIGNAL_S:
                # диагностика FullHD: недособранные кадры = потеря UDP-пакетов по пути к пульту
                lost = t.asm.lost if t.asm else 0
                r.set_status(f"{self.fps[i]:.0f} к/с" + (f", потеряно {lost}" if lost else ""),
                             "warn" if lost else "ok")
                r.status.setToolTip(f"Принято кадров в секунду: {self.fps[i]:.1f}\n"
                                    f"Выброшено недособранных кадров (потеря пакетов): {lost}\n"
                                    f"Не удалось распаковать JPEG: {t.bad_frames}")
            elif t.rejected_from and now - t.rejected_at < NO_SIGNAL_S:
                r.set_status(f"идёт с {t.rejected_from}", "warn")
                r.status.setToolTip(f"Кадры приходят с {t.rejected_from}, а в поле указан другой IP. "
                                    f"Впишите этот адрес или очистите поле")
                self._no_signal(i, f"кадры с {t.rejected_from}")
            else:
                r.set_status("нет сигнала", "warn" if t.isRunning() else "danger")
                self._no_signal(i, "нет сигнала")

    def _no_signal(self, i: int, note: str):
        v = self.thumbs[i]
        if v.img is not None and self.threads[i] is not None and self.threads[i].last_rx \
                and time.time() - self.threads[i].last_rx < NO_SIGNAL_S:
            return
        if v.img is not None or v.note != note:
            v.clear(note)
            if i == self.selected:
                self.big.clear(note)

    # ── выбор и снимок ──
    def _select(self, i: int):
        self.selected = i
        self.big.name = config.CAMERAS[i]["name"]
        for j, t in enumerate(self.thumbs):
            t.set_border("accent" if j == i else "")
        img = self.thumbs[i].img
        if img is not None:
            self.big.set_image(img)
        else:
            self.big.clear(self.thumbs[i].note)

    def _snapshot(self):
        img = self.thumbs[self.selected].img
        if img is None:
            self.snap_lbl.set_text("Нет кадра с выбранной камеры")
            return
        os.makedirs(config.SNAPSHOT_DIR, exist_ok=True)
        path = os.path.join(config.SNAPSHOT_DIR, time.strftime(f"cam{self.selected + 1}_%Y%m%d_%H%M%S.png"))
        img.save(path)
        self.snap_lbl.set_text(f"Сохранено: {path}")
