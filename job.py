"""Текущее задание печати: программа, статус и прогресс.

Прогресс считается по фактическому положению сопла (координаты от шлюза),
а не по счётчикам платы: шлюз дробит каждую команду G1 на десятки кадров
кольцевого буфера, и номер кадра не связан с номером строки программы.

Паузы нет намеренно: шлюз при «паузе» (M597 1) лишь перестаёт подкачивать кадры,
а продолжить с того же места нельзя — M597 0 строит программу заново с начала
(см. ANALYSIS.md). Доступна только остановка — M700: шлюз останавливает обе платы
и пропускает остаток программы (M597 2 остановил бы только текущий участок).

Шлюз исполняет программу участками: паузы G4, нагрев, вентиляторы делят её на части,
и между участками кольцевой буфер ненадолго выключается. Поэтому «печать закончена»
считается, только когда принтер простаивает дольше IDLE_DONE_S.
"""
from __future__ import annotations

import os
import time

import numpy as np
from PyQt5 import QtCore

from . import gcode
from .printer import PrinterLink
from .protocol import Cmd, emergency_stop_sequence

MATCH_TOL_MM = 1.5          # насколько сопло может отстоять от траектории
SEARCH_WINDOW = 600         # сколько отрезков вперёд искать текущий
Z_WEIGHT = 4.0
AHEAD_PENALTY = 0.02        # мм «стоимости» за каждый мм пути вперёд
STOP_REPEAT_MS = 250       # повтор «Стоп»
IDLE_DONE_S = 5.0           # сколько принтер должен простоять, чтобы печать считалась законченной


class Job(QtCore.QObject):
    IDLE, LOADING, PRINTING, DONE, STOPPED = "idle", "loading", "printing", "done", "stopped"
    TITLES = {IDLE: "Готов", LOADING: "Загрузка", PRINTING: "Печать", DONE: "Завершено", STOPPED: "Остановлено"}
    LEVELS = {IDLE: "idle", LOADING: "warn", PRINTING: "info", DONE: "ok", STOPPED: "danger"}

    program_changed = QtCore.pyqtSignal(object)
    status_changed = QtCore.pyqtSignal(str)
    progress = QtCore.pyqtSignal(float, int)        # доля 0..1, индекс команды G1

    def __init__(self, link: PrinterLink):
        super().__init__()
        self.link = link
        self.program: gcode.Program | None = None
        self.path = ""
        self.title = ""
        self.status = self.IDLE
        self.started_at = 0.0
        self.finished_at = 0.0
        self.fraction = 0.0
        self.line_no = -1
        self.upload = (0, 0)
        self._seg = 0
        self._seg_t = 0.0
        self._seen_running = False
        self._idle_since = 0.0
        self._geom = None
        link.state_changed.connect(self._on_state)
        link.upload_progress.connect(self._on_upload)

    # --- программа ---
    def load_text(self, text: str, path: str = "", title: str = ""):
        self.program = gcode.parse(text)
        self.path = path
        self.title = title
        self.fraction, self.line_no = 0.0, -1
        self._build_geom()
        self._set_status(self.IDLE)
        self.program_changed.emit(self.program)

    def load_file(self, path: str):
        text = ""
        for enc in ("utf-8", "cp1251", "latin-1"):
            try:
                with open(path, "r", encoding=enc) as f:
                    text = f.read()
                break
            except UnicodeDecodeError:
                continue
        self.load_text(text, path)

    @property
    def name(self) -> str:
        if self.path:
            return os.path.basename(self.path)
        return self.title or ("Программа из редактора" if self.program else "—")

    def _build_geom(self):
        p = self.program
        if not p or not p.segments:
            self._geom = None
            return
        a = np.array([(s.x0, s.y0, s.z0) for s in p.segments], float)
        b = np.array([(s.x1, s.y1, s.z1) for s in p.segments], float)
        ln = np.linalg.norm(b - a, axis=1)
        cum = np.concatenate([[0.0], np.cumsum(ln)])
        self._geom = (a, b, ln, cum)

    # --- управление ---
    @property
    def active(self) -> bool:
        return self.status in (self.LOADING, self.PRINTING)

    def tool_plan(self, skip_lines: tuple = ()) -> list[tuple[int, int, str, bool]]:
        """Проверка всех T программы по текущей раскладке головок — так, как их выполнит шлюз (M620).

        → [(индекс строки, головка, что случится, шлюз упадёт)]. Логика M620: сначала текущая головка
        отдаётся в свободный манипулятор (левый, а если свободен и правый — правый); если свободного
        нет — шлюз пишет «Нет свободного места» и останавливает программу. Затем головка берётся из
        манипулятора, в котором она была ДО отдачи; если её там нет (на сопле, в гомогенизаторе,
        не установлена) — шлюз падает (AddRange(null)).
        """
        p, st = self.program, self.link.main
        if not p or not p.tool_changes or not st.tools_known:
            return []
        left, right, active = st.tool_manip_left, st.tool_manip_right, st.tool_active
        out = []
        for idx, n in p.tool_changes:
            if idx in skip_lines:
                continue
            if n <= 0:
                out.append((idx, n, "T0 шлюз не поддерживает", True))
                break
            pre_left, pre_right = left, right
            if active:
                if right == 0:
                    right = active
                elif left == 0:
                    left = active
                else:
                    out.append((idx, n, f"нет свободного манипулятора для головки {active} — "
                                        f"шлюз остановит программу", False))
                    break
            if pre_left == n:
                left = 0
            elif pre_right == n:
                right = 0
            else:
                where = "уже на сопле" if active == n else "не в манипуляторе"
                out.append((idx, n, f"головка {n} {where} — шлюз упадёт с ошибкой", True))
                break
            active = n
        return out

    def first_tool_conflict(self) -> int | None:
        """Номер головки, если программа начинается с T этой же головки, а она уже на сопле.

        Шлюз на такой T отдаёт головку в манипулятор и падает (берёт «ничего»), поэтому
        пульт предупреждает до старта.
        """
        p, st = self.program, self.link.main
        if p and p.first_tool is not None and st.tools_known and st.tool_active == p.first_tool:
            return p.first_tool
        return None

    def start(self, skip_lines: tuple = ()):
        """Старт. Перед программой — флаги, как в String_line4: автокалибровка (M641),
        подъём крышки (M643), коэффициенты скорости и подачи (M618, M617).
        skip_lines — индексы строк exec_lines, которые оператор разрешил не отправлять.
        """
        if not self.program or not self.program.exec_lines or self.active:
            return
        s = self.link.settings
        self.link.send_many([Cmd.autocalibration(s.autocalibration), Cmd.cover_lift(s.cover_lift),
                             Cmd.speed_factor(s.speed_pct), Cmd.extrusion_factor(s.flow_pct)])
        self._seg = 0
        self._seg_t = 0.0
        self._seen_running = False
        self._idle_since = 0.0
        self.fraction, self.line_no = 0.0, -1
        self.started_at = time.time()
        self.finished_at = 0.0
        self._set_status(self.LOADING)
        lines = [ln for i, ln in enumerate(self.program.exec_lines) if i not in skip_lines]
        self.link.upload_program(lines)

    def _stop_again(self):
        """Повтор M700 через 0,25 с: шлюз может потерять команду (гонка в его TCP-приёме)."""
        QtCore.QTimer.singleShot(STOP_REPEAT_MS, lambda: self.link.send(Cmd.STOP_ALL))

    def stop(self):
        self.link.flush()
        self.link.send(Cmd.PROGRAM_STOP)
        self._stop_again()
        if self.active:
            self.finished_at = time.time()
            self._set_status(self.STOPPED)

    def emergency_stop(self):
        self.link.flush()
        self.link.send_many(emergency_stop_sequence())
        self._stop_again()
        if self.active:
            self.finished_at = time.time()
            self._set_status(self.STOPPED)

    def remaining(self) -> float:
        """Оценка оставшегося времени с учётом текущего коэффициента скорости (M618)."""
        p = self.program
        if not p:
            return 0.0
        k = max(self.link.settings.speed_pct, 1) / 100.0
        if self.status == self.PRINTING and self.line_no >= 0:
            return p.remaining_at(self._seg, self._seg_t) / k
        if self.status in (self.IDLE, self.LOADING):
            return p.est_seconds / k
        return 0.0

    def total_estimate(self) -> float:
        p = self.program
        return p.est_seconds / (max(self.link.settings.speed_pct, 1) / 100.0) if p else 0.0

    def elapsed(self) -> float:
        if not self.started_at:
            return 0.0
        end = self.finished_at or time.time()
        return max(0.0, end - self.started_at)

    # --- события ---
    def _on_upload(self, sent: int, total: int):
        self.upload = (sent, total)
        if self.status == self.LOADING:
            if total and sent >= total:
                self._set_status(self.PRINTING)
            self.progress.emit(self.fraction, self.line_no)

    def _on_state(self, board: int, st):
        if board != 1 or self.status != self.PRINTING or self._geom is None:
            return
        st2 = self.link.states.get(2)
        # Флаг «ждём температуру» платы 2 прошивка не сбрасывает, пока нагрев включён, поэтому
        # он учитывается, только если в программе есть M190 и она ещё не дошла до конца.
        wait_heat = (st2 is not None and st2.waiting_temp and self.program is not None
                     and self.program.has_m190 and self.fraction < 0.999)
        busy = bool(st.ring_buf_en or st.moving_kinem or st.waiting_time
                    or (st2 is not None and st2.moving) or wait_heat)
        now = time.time()
        if busy:
            self._seen_running = True
            self._idle_since = 0.0
        elif self._seen_running and not self._idle_since:
            self._idle_since = now
        if st.has_xyz:
            self._match(np.array(self.link.prog_xyz(st)))
        if self._seen_running and self._idle_since and now - self._idle_since >= IDLE_DONE_S:
            self.finished_at = self._idle_since
            self._set_status(self.DONE if self.fraction > 0.9 else self.STOPPED)
            if self.status == self.DONE:
                self.fraction = 1.0
        self.progress.emit(self.fraction, self.line_no)

    def _match(self, pnt: np.ndarray):
        a, b, ln, cum = self._geom
        i0 = self._seg
        i1 = min(len(a), i0 + SEARCH_WINDOW)
        if i0 >= i1:
            return
        aa, bb, ll = a[i0:i1], b[i0:i1], ln[i0:i1]
        d = bb - aa
        with np.errstate(invalid="ignore", divide="ignore"):
            t = np.clip(np.einsum("ij,ij->i", pnt - aa, d) / np.maximum(ll * ll, 1e-12), 0, 1)
        proj = aa + d * t[:, None]
        diff = proj - pnt
        diff[:, 2] *= Z_WEIGHT                      # слои разнесены по Z — ошибка по Z дороже
        dist = np.linalg.norm(diff, axis=1)
        ahead = cum[i0:i1] + ll * t - (cum[i0] if i0 < len(cum) else 0)
        cost = dist + AHEAD_PENALTY * ahead         # не перескакивать вперёд по траектории
        k = int(np.argmin(cost))
        if dist[k] > MATCH_TOL_MM:
            return
        self._seg = i0 + k
        self._seg_t = float(t[k])
        self.line_no = self.program.segments[self._seg].line_no
        total = cum[-1]
        self.fraction = float((cum[self._seg] + ll[k] * t[k]) / total) if total > 0 else 0.0

    def _set_status(self, s: str):
        if s != self.status:
            self.status = s
            self.status_changed.emit(s)
