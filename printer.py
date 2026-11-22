"""Связь с принтером: TCP к шлюзу tcp_to_udp или встроенный симулятор (демо).

Особенности шлюза, под которые подстроена отправка (см. ANALYSIS.md):
- шлюз разбирает входной поток по '\\n' без учёта строк, разорванных между
  TCP-пакетами, поэтому программа отправляется пачками только из целых строк
  (TCP_NODELAY) с паузой между пачками;
- числа в программе всегда с точкой (как в прежнем интерфейсе); координаты от шлюза
  разбираются и с точкой, и с запятой;
- телеметрия приходит ~1000 раз в секунду с каждой платы: она разбирается в
  фоновом потоке, а интерфейс обновляется не чаще 40 раз в секунду;
- координаты сопла шлюз присылает в трёх видах: текущая СК, машина, инструмент.
"""
from __future__ import annotations

import collections
import math
import queue
import socket
import threading
import time

from PyQt5 import QtCore

from . import config
from .protocol import BoardState, Cmd, format_state_line, parse_state_line

UPLOAD_BATCH = 10               # строк программы в одном TCP-пакете (только целые строки)
UPLOAD_DELAY_S = 0.005          # пауза между пакетами программы
UI_PERIOD_MS = 25               # период обновления интерфейса телеметрией (40 раз/с)
HOME_TIMEOUT_S = 180.0          # сколько ждать окончания базирования

# Пока идёт печать, эти команды не отправляются (motion_lock): они двигают сопло, ось E,
# стол или манипуляторы, а M611/M612/M615/M619–M621/M63x/M640–M643 в шлюзе ещё и
# подменяют программу или систему координат — печать бы оборвалась.
# Разрешены: стоп (M700, M589 S), нагрев, свет, серво, коэффициенты, гомогенизаторы (num1 I3–I6).
_LOCK_MAIN = ("M597 2", "M611", "M612", "M613", "M615", "M616", "M619", "M620", "M621", "M623",
              "M624", "M630", "M631", "M634", "M635", "M639", "M640", "M641", "M642", "M643")
_LOCK_AXES = {"num1": {"7"}, "num2": {"0", "2", "3", "4", "5", "6"}}


def motion_command(cmd: str) -> bool:
    """Команда, которую нельзя отправлять во время печати."""
    parts = cmd.split()
    if len(parts) < 2:
        return False
    dest, rest = parts[0], " ".join(parts[1:])
    if dest == "main":
        return any(rest == k or rest.startswith(k + " ") for k in _LOCK_MAIN)
    if dest in _LOCK_AXES and parts[1] == "M587":
        axis = next((p[1:] for p in parts[2:] if p.startswith("I")), "")
        return axis in _LOCK_AXES[dest]
    if dest == "num1" and parts[1] == "M589":
        return any(p[:1] in ("X", "Y", "Z") for p in parts[2:])
    return False



class _TcpWorker(threading.Thread):
    """Фоновый поток: подключение, чтение телеметрии, отправка из очереди с паузами."""

    def __init__(self, link: "PrinterLink", host: str, port: int):
        super().__init__(daemon=True)
        self.link, self.host, self.port = link, host, port
        self.out_q: "queue.Queue[tuple[str, float, object]]" = queue.Queue()
        self._stop = threading.Event()
        self.sock: socket.socket | None = None

    def stop(self):
        self._stop.set()
        try:
            if self.sock:
                self.sock.close()
        except OSError:
            pass

    def flush(self):
        while True:
            try:
                self.out_q.get_nowait()
            except queue.Empty:
                return

    def run(self):
        while not self._stop.is_set():
            try:
                self.sock = socket.create_connection((self.host, self.port), timeout=2.0)
                self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                self.sock.settimeout(0.005)
            except OSError:
                self.link._set_connected(False)
                self._stop.wait(config.RECONNECT_MS / 1000)
                continue
            self.link._set_connected(True)
            buf = ""
            next_send = 0.0
            try:
                while not self._stop.is_set():
                    now = time.monotonic()
                    while now >= next_send:
                        try:
                            cmd, delay, on_sent = self.out_q.get_nowait()
                        except queue.Empty:
                            break
                        self.sock.sendall((cmd + "\n").encode("ascii", "ignore"))
                        if on_sent:
                            on_sent()
                        if delay > 0:
                            next_send = now + delay
                            break
                    try:
                        data = self.sock.recv(65536)
                        if not data:
                            raise OSError("closed")
                        buf += data.decode("ascii", "ignore")
                        *lines, buf = buf.split("\n")
                        self.link._ingest(lines)
                        if len(buf) > 65536:
                            buf = ""
                    except socket.timeout:
                        pass
            except OSError:
                pass
            finally:
                try:
                    self.sock.close()
                except OSError:
                    pass
                self.link._set_connected(False)
            self._stop.wait(config.RECONNECT_MS / 1000)


class _Simulator(QtCore.QObject):
    """Имитация связки «шлюз + две платы» в формате настоящей телеметрии.

    Повторяет модель шлюза: системы координат (MANUAL / AUTO / INTERNAL / CURRENT),
    программа из участков, смена головки по T, подложка по G54, ручное M611.
    """

    JOG_DIRS = [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)]
    MANIP_POS = {"left": (-60.0, 40.0, 40.0), "right": (60.0, 40.0, 40.0)}
    SYS = ["MANUAL", "AUTO", "CURRENT", "INTERNAL"]           # номера M640

    def __init__(self, link: "PrinterLink", comma: bool = False):
        super().__init__()
        self.link = link
        self.comma = comma
        self.m = [0.0, 0.0, 60.0]                   # машинные координаты сопла
        self.v = [0.0, 0.0, 0.0]                    # скорость ручного перемещения
        self.jog_speed = 10.0
        self.manual_zero = [[0.0, 0.0, 0.0] for _ in range(4)]   # offset_zero_pos по головкам
        self.plate = 0
        self.cur_sys, self.dest_sys = "CURRENT", "MANUAL"
        self.cur_off = [0.0, 0.0, 0.0]              # offset_frame
        self.homing_left = 0.0
        self.delta = 0
        self.temp = 23.0
        self.setpoint = config.BED_TEMP_DEFAULT
        self.heater = False
        self.orig: list[str] = []
        self.queue: list = []                       # действия программы
        self.wait_left = 0.0
        self.board2_left = 0.0
        self.k_vel = 1.0
        self.lasers = "11"
        self.line_num = [0, 0]
        # головки: левый манипулятор, правый, гомогенизаторы 1 и 2, на сопле
        self.tools = [2, 0, 3, 0, 1]
        self._tick = 0
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._step)
        self.timer.start(20)

    # --- системы координат ---
    def _offset(self, sys_name: str) -> list:
        if sys_name == "MANUAL":
            return list(self.manual_zero[self.tools[4] if self.tools[4] < 4 else 0])
        if sys_name == "AUTO":
            return [0.0, 0.0, -5.0 - 2.0 * self.plate]           # сдвиг подложки: меняется Z
        if sys_name == "INTERNAL":
            return [0.0, 0.0, 0.0]
        return list(self.cur_off)

    def _set_cur(self, sys_name: str):
        self.cur_sys = sys_name
        self.cur_off = list(self.m) if sys_name == "CURRENT" else self._offset(sys_name)

    # --- команды ---
    def handle(self, cmd: str):
        parts = cmd.replace(",", ".").split()
        if len(parts) < 2 or parts[0] not in ("main", "num1", "num2"):
            return                                  # шлюз исполняет только команды с адресатом
        dest, c, args = parts[0], parts[1], parts[2:]
        if dest == "num1":
            self.line_num[0] += 1
        elif dest == "num2":
            self.line_num[1] += 1
            if c == "M587" and any(a.startswith(("P", "H")) for a in args):
                self.board2_left = 1.0
        kv = {a[0]: a[1:] for a in args if a}
        if c == "M611":
            if not self._busy():
                d = int(float(args[0])) if args else 0
                if 0 <= d < 6:
                    self._set_cur("CURRENT")
                    self.v = [k * self.jog_speed for k in self.JOG_DIRS[d]]
        elif c == "M610" and args:
            self.jog_speed = float(args[0])
        elif c == "M597":
            self.v = [0.0, 0.0, 0.0]
            mode = args[0] if args else "0"
            if mode == "0":
                self._load_program(self.orig)
            else:
                self.queue = []
        elif c == "M700" or (c == "M589" and "S" in kv and dest == "num1"):
            self.v = [0.0, 0.0, 0.0]
            self.queue = []
            self.homing_left = self.wait_left = 0.0
        elif c == "M589" and "X" in kv:
            self.homing_left = 1.5
            self.queue = []
        elif c == "M598":
            self.orig = []
        elif c == "M596":
            self.orig.append(" ".join(args))
        elif c == "M613":
            self.delta = 1
        elif c == "M615":
            self.queue = [("move_m", self._offset("MANUAL"), self.jog_speed)]
        elif c == "M612" and len(args) >= 7:
            v = [float(a) for a in args[:8]]
            t = self.tools[4] if self.tools[4] < 4 else 0
            for i in range(3):
                if v[4 + i] == 1:
                    self.manual_zero[t][i] = self.m[i] - v[i]
            if self.cur_sys == "MANUAL":
                self.cur_off = self._offset("MANUAL")
        elif c == "M640" and args:
            n = int(float(args[0]))
            if 0 <= n < 4:
                self._set_cur(self.SYS[n])
        elif c == "M641" and args:
            self.dest_sys = "AUTO" if args[0] == "1" else "MANUAL"
            self._set_cur(self.dest_sys)
        elif c == "M637" and args:
            self.plate = int(float(args[0]))
        elif c == "M620" and args:
            self.queue = self._tool_actions(int(float(args[0]))) + self.queue
        elif c == "M621":
            self.queue = self._tool_actions(0) + self.queue
        elif c == "M639" and args:
            self.tools[4] = int(float(args[0]))
        elif c == "M631":
            self.queue = [("lasers", "01"), ("wait", 0.5), ("lasers", "11")] + self.queue
        elif c in ("M623", "M624"):
            self.board2_left = 2.0
        elif c == "M618" and args:
            self.k_vel = max(float(args[0]), 1.0) / 100.0
        elif c == "M579":
            for a in args:
                if a[0] == "T":
                    self.setpoint = int(float(a[1:]))
                elif a[0] == "E":
                    self.heater = a[1:] == "1"

    def _tool_actions(self, n: int) -> list:
        """Смена головки: в машинных координатах к манипулятору и обратно (как M620/M621)."""
        acts = [("sys", "INTERNAL")]
        cur = self.tools[4]
        if cur:
            side = "left" if self.tools[0] == 0 else ("right" if self.tools[1] == 0 else None)
            if side is None:
                return []
            acts += [("move_m", self.MANIP_POS[side], 20.0), ("give", side)]
        if n:
            side = "left" if self.tools[0] == n else ("right" if self.tools[1] == n else None)
            if side:
                acts += [("move_m", self.MANIP_POS[side], 20.0), ("take", side, n)]
        acts += [("sys", self.dest_sys)]
        return acts

    def _load_program(self, lines: list[str]):
        """Программа → действия, как convert_g_code_to_stepperframes + участки шлюза."""
        acts = []
        x = y = z = 0.0
        vel = 10.0
        first_move = True
        for line in lines:
            vals = line.split()
            if not vals:
                continue
            h = vals[0]
            if "G1" in h or "G0" in h:
                for tok in vals[1:]:
                    try:
                        v = float(tok[1:])
                    except ValueError:
                        continue
                    if tok[0] == "X": x = v
                    elif tok[0] == "Y": y = v
                    elif tok[0] == "Z": z = v
                    elif tok[0] == "F": vel = v / 60.0
                if first_move:
                    acts.append(("start_chunk",))
                    first_move = False
                acts.append(("move", [x, y, z], vel))
            elif "G4" in h:
                s = 0.0
                for tok in vals[1:]:
                    try:
                        s += float(tok[1:]) / (1000.0 if tok[0] == "P" else 1.0)
                    except ValueError:
                        pass
                acts.append(("wait", s))
            elif "G54" in h:
                for tok in vals[1:]:
                    if tok[:1] == "X":
                        acts.append(("plate", int(float(tok[1:]))))
                first_move = True
            elif "T" in h:
                try:
                    acts.append(("tool", int(float(h[1:]))))
                except ValueError:
                    pass
                first_move = True
        self.queue = acts

    def _busy(self) -> bool:
        return bool(self.homing_left > 0 or self.queue or any(self.v))

    def _step(self):
        dt = 0.02
        if self.homing_left > 0:
            self.homing_left -= dt
            if self.homing_left <= 0:
                self.m = [0.0, 0.0, 60.0]
        if self.board2_left > 0:
            self.board2_left -= dt
        self.m = [self.m[i] + self.v[i] * dt for i in range(3)]
        running = waiting = 0
        while self.queue:
            a = self.queue[0]
            kind = a[0]
            if kind == "start_chunk":                     # участок: ноль — по текущей СК
                self.cur_off = list(self.m) if self.cur_sys == "CURRENT" else self._offset(self.cur_sys)
                self.queue.pop(0)
                continue
            if kind == "sys":
                self._set_cur(a[1]); self.queue.pop(0); continue
            if kind == "plate":
                self.plate = a[1]; self.queue.pop(0); continue
            if kind == "lasers":
                self.lasers = a[1]; self.queue.pop(0); continue
            if kind == "give":
                i = 0 if a[1] == "left" else 1
                self.tools[i], self.tools[4] = self.tools[4], 0
                self.queue.pop(0); continue
            if kind == "take":
                i = 0 if a[1] == "left" else 1
                self.tools[i], self.tools[4] = 0, a[2]
                self.queue.pop(0); continue
            if kind == "tool":
                self.queue[0:1] = self._tool_actions(a[1])
                continue
            if kind == "wait":
                if self.wait_left <= 0:
                    self.wait_left = a[1]
                self.wait_left -= dt
                waiting = 1
                if self.wait_left <= 0:
                    self.queue.pop(0)
                break
            if kind in ("move", "move_m"):
                tgt = a[1] if kind == "move_m" else [a[1][i] + self.cur_off[i] for i in range(3)]
                d = [tgt[i] - self.m[i] for i in range(3)]
                dist = math.sqrt(sum(c * c for c in d))
                step = min(a[2] * self.k_vel, 40.0) * dt
                running = 1
                if dist <= step:
                    self.m = list(tgt)
                    self.queue.pop(0)
                else:
                    self.m = [self.m[i] + d[i] / dist * step for i in range(3)]
                break
            self.queue.pop(0)
        target = self.setpoint if self.heater else 23.0
        self.temp += (target - self.temp) * 0.004
        self._tick += 1
        send = self._tick % 3
        kinem = int(self._busy() and not waiting)
        flags1 = f"{kinem}{kinem}{running}{waiting}0"
        b2 = int(self.board2_left > 0)
        flags2 = f"{b2}0000"
        cur = [self.m[i] - self.cur_off[i] for i in range(3)]
        dest_off = self._offset(self.dest_sys)
        tool = [self.m[i] - dest_off[i] for i in range(3)]
        coords = tuple(cur + list(self.m) + tool)
        tools = "".join(str(t) for t in self.tools)
        if send == 0:
            v1 = [self.delta, self.lasers, "00000000", int(self.temp * 100), self._tick * 2000, "000", "01101101", 0]
            v2 = [0, "00", "00000000", int(self.temp * 100), self._tick * 2000, "000", "11111111", 0]
        elif send == 1:
            v1 = [int(c * 800) for c in self.m] + [0] * 5
            v2 = [0] * 8
        else:
            v1 = [0] * 8
            v2 = [0] * 8
        lines = [format_state_line(1, send, v1, self.line_num[0], 0, flags1, coords, tools, self.cur_sys, self.comma),
                 format_state_line(2, send, v2, self.line_num[1], 0, flags2)]
        self.link._ingest(lines)


class PrinterLink(QtCore.QObject):
    """Единая точка общения интерфейса с принтером."""

    connected_changed = QtCore.pyqtSignal(bool)
    state_changed = QtCore.pyqtSignal(int, object)       # номер платы, BoardState (≤40 Гц)
    raw_lines = QtCore.pyqtSignal(list)                  # последние сырые строки (пачкой)
    command_sent = QtCore.pyqtSignal(str)
    upload_progress = QtCore.pyqtSignal(int, int)        # отправлено, всего
    blocked = QtCore.pyqtSignal(str)                     # команда не отправлена: идёт печать
    homed_changed = QtCore.pyqtSignal(bool)
    zero_changed = QtCore.pyqtSignal()

    def __init__(self, settings: config.UserSettings):
        super().__init__()
        self.settings = settings
        self.states: dict[int, BoardState] = {1: BoardState(board=1), 2: BoardState(board=2)}
        self.connected = False
        self.homed = False
        self._lock = threading.Lock()
        self._dirty: set[int] = set()
        self._raw = collections.deque(maxlen=300)
        self._conn_pending: bool | None = None
        self._upload = [0, 0]
        self._upload_dirty = False
        self._homed_event = False
        self.last_rx: dict[int, float] = {}   # плата → время последней телеметрии (monotonic)
        self._home_wait = 0.0           # момент отправки Home (0 — не ждём)
        self._home_seen_move = False
        self._worker: _TcpWorker | None = None
        self._sim: _Simulator | None = None
        self.motion_lock = lambda: False               # подставляет Ctx: «идёт печать»
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._pump)
        self._timer.start(UI_PERIOD_MS)

    # --- жизненный цикл ---
    def start(self):
        self.stop()
        if self.settings.demo:
            self._sim = _Simulator(self)
            self._set_connected(True)
        else:
            self._worker = _TcpWorker(self, self.settings.host, int(self.settings.port))
            self._worker.start()

    def stop(self):
        if self._worker:
            self._worker.stop()
            self._worker = None
        if self._sim:
            self._sim.timer.stop()
            self._sim.deleteLater()
            self._sim = None
        self._set_connected(False)
        self._pump()

    def drain(self, timeout: float = 1.0):
        """Дождаться отправки очереди команд (перед закрытием программы)."""
        w = self._worker
        if not w or not self.connected:
            return
        end = time.monotonic() + timeout
        while not w.out_q.empty() and time.monotonic() < end:
            time.sleep(0.01)
        time.sleep(0.05)

    @property
    def is_demo(self) -> bool:
        return self._sim is not None

    # --- отправка ---
    def send(self, cmd: str, log: bool = True, delay: float = 0.0, on_sent=None) -> bool:
        """Отправить команду. False — не отправлена: идёт печать, а команда её прервала бы."""
        if self.motion_lock() and any(motion_command(c) for c in cmd.split("\n")):
            self.blocked.emit(cmd)
            return False
        self._track(cmd)
        if log:
            self.command_sent.emit(cmd)
        if self._sim:
            for c in cmd.split("\n"):
                self._sim.handle(c)
            if on_sent:
                on_sent()
        elif self._worker:
            self._worker.out_q.put((cmd, delay, on_sent))
        return True

    def send_many(self, cmds, log: bool = True) -> bool:
        """Отправить по порядку; если команда заблокирована — остальные тоже не уходят."""
        for c in cmds:
            if not self.send(c, log):
                return False
        return True

    def _track(self, cmd: str):
        """Учесть команды, которые меняют то, что пульт показывает сам: Home и ноль."""
        c = " ".join(cmd.split())
        if c.startswith("main M612"):
            self.zero_changed.emit()
        if c.startswith("num1 M589 X"):
            with self._lock:
                self._home_wait = time.monotonic()
                self._home_seen_move = False
            self._set_homed(False)
        elif c in (Cmd.STOP_ALL, "num1 M589 S"):
            with self._lock:
                self._home_wait = 0.0

    def send_saved_settings(self):
        """После подключения: настройки пульта, которые шлюз не хранит (сбрасываются при его перезапуске)."""
        s = self.settings
        self.send_many([Cmd.jog_speed(s.jog_speed), Cmd.speed_factor(s.speed_pct),
                        Cmd.extrusion_factor(s.flow_pct), Cmd.autocalibration(s.autocalibration),
                        Cmd.cover_lift(s.cover_lift)])

    def flush(self):
        """Выбросить всё, что ещё не ушло (например, недогруженную программу)."""
        if self._worker:
            self._worker.flush()
        with self._lock:
            self._upload = [0, 0]
            self._upload_dirty = True

    def upload_program(self, lines: list[str]) -> int:
        """Залить программу в шлюз (main M596 …) и запустить (main M597 0).

        Строки уходят пачками по UPLOAD_BATCH с паузой UPLOAD_DELAY_S — так шлюз не
        получит разорванных строк. Прогресс — сигнал upload_progress.
        """
        clean = [ln.split(";")[0].strip() for ln in lines]
        clean = [ln for ln in clean if ln]
        total = len(clean)
        with self._lock:
            self._upload = [0, total]
            self._upload_dirty = True

        def sent(k):
            def f():
                with self._lock:
                    self._upload[0] += k
                    self._upload_dirty = True
            return f

        self.send_many(Cmd.PROGRAM_PREPARE)
        for i in range(0, total, UPLOAD_BATCH):
            chunk = clean[i:i + UPLOAD_BATCH]
            body = "\n".join(Cmd.program_line(ln) for ln in chunk)
            self.send(body, log=False, delay=UPLOAD_DELAY_S, on_sent=sent(len(chunk)))
        self.command_sent.emit(f"… {total} строк программы (main M596 …)")
        self.send(Cmd.PROGRAM_START)
        return total

    # --- приём (фоновый поток или симулятор) ---
    def _ingest(self, lines):
        with self._lock:
            for ln in lines:
                ln = ln.strip()
                if not ln:
                    continue
                self._raw.append(ln)
                idx = ln.find("st")
                if idx < 0:
                    continue
                try:
                    board = int(ln[idx + 2: idx + 3])
                except ValueError:
                    continue
                prev = self.states.get(board)
                st = parse_state_line(ln, prev)
                if not st:
                    continue
                if board == 1 and self._home_wait:
                    # прошивка больше не сообщает «идёт базирование»: Home выполнен, когда
                    # моторы кинематики после команды поехали и остановились
                    if st.moving_kinem:
                        self._home_seen_move = True
                    elif self._home_seen_move:
                        self._home_wait = 0.0
                        self._homed_event = True
                    elif time.monotonic() - self._home_wait > HOME_TIMEOUT_S:
                        self._home_wait = 0.0
                self.states[board] = st
                self.last_rx[board] = time.monotonic()
                self._dirty.add(board)

    def _set_connected(self, ok: bool):
        with self._lock:
            self._conn_pending = ok

    def _pump(self):
        """Раз в UI_PERIOD_MS: передать накопленное в интерфейс."""
        with self._lock:
            conn = self._conn_pending
            self._conn_pending = None
            dirty = sorted(self._dirty)
            self._dirty.clear()
            states = {b: self.states[b] for b in dirty}
            raw = list(self._raw)
            self._raw.clear()
            homed_event = self._homed_event
            self._homed_event = False
            up = list(self._upload) if self._upload_dirty else None
            self._upload_dirty = False
        if conn is not None and conn != self.connected:
            self.connected = conn
            if not conn:
                self._set_homed(False)
            else:
                self.send_saved_settings()
            self.connected_changed.emit(conn)
        if homed_event:
            self._set_homed(True)
        for b, st in states.items():
            self.state_changed.emit(b, st)
        if raw:
            self.raw_lines.emit(raw)
        if up is not None:
            self.upload_progress.emit(*up)

    def _set_homed(self, v: bool):
        if v != self.homed:
            self.homed = v
            self.homed_changed.emit(v)

    # --- координаты (все три вида присылает шлюз) ---
    def reset_zero(self):
        """Ноль ручной СК активной головки — в ноль машины: текущей точке присвоить её машинные координаты."""
        st = self.states[1]
        self.send(Cmd.set_position(st.mx, st.my, st.mz))

    def prog_xyz(self, st: BoardState | None = None) -> tuple[float, float, float]:
        """Сопло в текущей системе координат (в ней шлюз исполняет программу)."""
        st = st or self.states[1]
        return st.x, st.y, st.z

    def machine_xyz(self, st: BoardState | None = None) -> tuple[float, float, float]:
        st = st or self.states[1]
        return st.mx, st.my, st.mz

    def tool_xyz(self, st: BoardState | None = None) -> tuple[float, float, float]:
        st = st or self.states[1]
        return st.tx, st.ty, st.tz

    # --- удобные геттеры ---
    @property
    def main(self) -> BoardState:
        return self.states[1]

    def bed_temp(self) -> float:
        return self.states.get(config.TEMP_SOURCE_BOARD, BoardState()).temp_cur * config.TEMP_SCALE
