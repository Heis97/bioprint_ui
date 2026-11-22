"""Протокол обмена с шлюзом tcp_to_udp и платами.

Команды сверены с текущими версиями (19.10.2026):
- интерфейс oct_ui, String_line4.py — какие строки шлёт штатный интерфейс;
- шлюз tcp_to_udp, ветка test_sech, Udp_to_tcp.cs (exec_main_prog) — что означают команды «main …»;
- прошивка Marlin-2.1.2.5-sech2, string_periphery.cpp (StringPeriphery::state_cur) — формат телеметрии.

Формат отправки: "<адресат> <команда>", адресат — main (сам шлюз) / num1 / num2 (платы).
Команда без адресата шлюзом не исполняется.
Формат телеметрии: строки "st1 ..." и "st2 ..." (см. parse_state_line).
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field

from . import config

AXIS_NAMES = ["X", "Y", "Z", "I", "J", "K", "U", "E"]


# ─────────────────────────── Команды ───────────────────────────
class Cmd:
    # ── Ручное перемещение сопла (удержание кнопки) ──
    # Шлюз: M611 <n> строит отрезок 100 мм от текущей точки по направлению n
    # (0:+X 1:-X 2:+Y 3:-Y 4:+Z 5:-Z) и исполняет его со скоростью M610.
    # Пока сопло движется, шлюз новый M611 не принимает, поэтому перед ним — остановка M597 2
    # (как в String_line4._jog_bool). Отпускание кнопки — тоже M597 2.
    JOG_STOP = "main M597 2"

    @staticmethod
    def jog_start(code: int) -> list[str]:
        return ["main M597 2", f"main M611 {int(code)}"]

    @staticmethod
    def jog_xy_start(direction: int) -> list[str]:
        """direction: 0:+X 1:-X 2:+Y 3:-Y."""
        return Cmd.jog_start(direction)

    @staticmethod
    def jog_z_start(direction: int) -> list[str]:
        """direction: +1 / -1 (M611 4 / 5)."""
        return Cmd.jog_start(4 if direction > 0 else 5)

    JOG_XY_STOP = JOG_STOP
    JOG_Z_STOP = JOG_STOP

    @staticmethod
    def jog_speed(mm_s: float) -> str:
        """Скорость ручного перемещения, мм/с (в шлюзе по умолчанию 10)."""
        return f"main M610 {float(mm_s):.2f}"

    @staticmethod
    def jog_step(axis: str, dist: float, mm_s: float) -> list[str]:
        """Дискретное перемещение на dist мм по оси X/Y/Z — программой в текущей СК.

        M640 2 — система координат CURRENT (ноль — там, где сопло стоит в момент запуска),
        программа из одной строки «G1 <ось><dist>»: шлюз сам добавляет стартовую точку 0 0 0.
        После неё шлюз остаётся в CURRENT (как и после M611); перед печатью пульт шлёт M641.
        """
        return ["main M597 2", "main M640 2", "main M598 0",
                f"main M596 G1 {axis.upper()}{dist:.3f} F{mm_s * 60:.0f}", "main M597 0"]

    # ── Периферийные оси: M587 I<ось> S<скорость>, H — поиск нуля ──
    @staticmethod
    def periph_move(board: str, axis: int, direction: int) -> str:
        return f"{board} M587 I{int(axis)} S{int(config.JOG_SPEED * direction)}"

    @staticmethod
    def periph_stop(board: str, axis: int) -> str:
        return f"{board} M587 I{int(axis)} S0"

    @staticmethod
    def periph_home(board: str, axis: int) -> str:
        return f"{board} M587 I{int(axis)} H"

    # ── Сервоприводы ──
    @staticmethod
    def servo(index: int, value: int, board: str = "num1") -> str:
        """Сервоприводы гомогенизаторов — num1 I0 / I1."""
        return f"{board} M577 I{int(index)} V{int(value)}"

    @staticmethod
    def cover_servo(value: int) -> list[str]:
        """Захват крышки (плата 2): две сервы навстречу, как String_line4._servo_cover."""
        v = int(value)
        return [f"num2 M577 I0 V{v}", f"num2 M577 I1 V{180 - v}"]

    HOME_COVER = "num2 M587 I2 H"

    # ── Термостол и реле (плата 2) ──
    @staticmethod
    def heater_enable(on: bool) -> str:
        return f"num2 M579 E{1 if on else 0}"

    @staticmethod
    def heater_setpoint(t: int) -> str:
        return f"num2 M579 T{int(t)}"

    @staticmethod
    def relay(index: int, on: bool) -> str:
        return f"num2 M579 I{int(index)} S{1 if on else 0}"

    # ── ШИМ-выходы: num<плата> M581 A<0..255> I<выход> ──
    @staticmethod
    def pwm(board: int, pin: int, value: int) -> str:
        return f"num{int(board)} M581 A{int(value)} I{int(pin)}"

    @staticmethod
    def light(value: int) -> str:
        """Освещение 0..255 — как ползунок «Освещение» в String_line4."""
        return Cmd.pwm(*Cmd.LIGHT, value)

    LIGHT = (2, 1)              # освещение
    FAN_LAMINAR = (1, 2)        # вентилятор ламинара
    FAN_PSU = (1, 1)            # вентилятор источника питания

    # ── Программа ──
    # M598 0 — очистить, M596 <строка> — добавить строку, M597 0 — разобрать и запустить.
    # Прежняя первая команда «num1 M587 I7 C0» (сброс E) больше не нужна: шлюз сбрасывает E сам.
    PROGRAM_PREPARE = ["main M598 0"]

    @staticmethod
    def program_line(line: str) -> str:
        return f"main M596 {line}"

    PROGRAM_START = "main M597 0"
    # M597 2 останавливает текущий участок (num1 M589 S), но шлюз затем начинает следующий
    # участок программы (их разделяют паузы, нагрев, смена инструмента…). Полностью программу
    # останавливает только M700: шлюз шлёт «M589 S» обеим платам и пропускает остаток программы.
    PROGRAM_STOP = "main M700"
    STOP_ALL = "main M700"

    # ── Базирование, нулевая точка, настройки ──
    HOME = "num1 M589 X12"
    HOME_TEST = "num1 M589 Y40"
    MOVE_TO_ZERO = "main M615"
    LOAD_SETTINGS = "main M614"
    DELTA_CALIBRATE = "main M613 18"

    # M612 X Y Z E x_en y_en z_en e_en: текущей точке присвоить координаты программы (X, Y, Z)
    SET_ZERO = "main M612 0 0 0 0 1 1 1 1"

    @staticmethod
    def set_position(x: float, y: float, z: float) -> str:
        return f"main M612 {x:.4f} {y:.4f} {z:.4f} 0 1 1 1 0"

    # ── Калибровка стола по 3 точкам ──
    @staticmethod
    def bed_point(n: int) -> str:
        return f"main M616 {int(n)}"

    BED_COMPUTE = "main M616 5"
    BED_RESET = "main M616 -1"
    NOZZLE_CALIBRATE = "main M631"

    # ── Коэффициенты (проценты, 100 = без изменений) ──
    @staticmethod
    def extrusion_factor(percent: int) -> str:
        return f"main M617 {int(percent)}"

    @staticmethod
    def speed_factor(percent: int) -> str:
        return f"main M618 {int(percent)}"

    # ── Инструменты (головки 1–3) и манипуляторы ──
    @staticmethod
    def take_tool(n: int) -> str:
        """Взять головку n: шлюз сам отдаёт текущую в свободный манипулятор."""
        return f"main M620 {int(n)}"

    DROP_TOOL = "main M621"

    # ── Ручная экструзия: поршень (ось E платы 1) напрямую командами прошивки ──
    # M587 I7 M<v> — скорость, мм/с; M587 I7 S<ход> — сдвиг относительно текущего, мм;
    # S0 — стоп. (Строки G-кода только с E шлюз сейчас не выполняет — см. CHANGES.md.)
    E_STOP = "num1 M587 I7 S0"
    E_HOLD_TRAVEL = 100000.0        # «пока держишь»: ход заведомо больше шприца, стоп — S0

    @staticmethod
    def e_speed(mm_s: float) -> str:
        return f"num1 M587 I7 M{float(mm_s):.5f}"

    @staticmethod
    def e_move(mm: float) -> str:
        return f"num1 M587 I7 S{float(mm):.4f}"

    @staticmethod
    def set_active_tool(n: int) -> str:
        """Сообщить шлюзу, какая головка сейчас на сопле (0 — никакой)."""
        return f"main M639 {int(n)}"

    # M619 0/1 — взять/отдать левый манипулятор, 2/3 — правый
    MANIP_TAKE_LEFT = "main M619 0"
    MANIP_GIVE_LEFT = "main M619 1"
    MANIP_TAKE_RIGHT = "main M619 2"
    MANIP_GIVE_RIGHT = "main M619 3"
    HOME_MANIPULATORS = "main M630"
    HOME_HOMOGENIZERS = "main M634"     # в шлюзе — «osc», осцилляторы

    # ── Подача планшета ──
    PLATE_BRING = "main M623 1"
    PLATE_GIVE = "main M624 1"

    # ── Системы координат шлюза ──
    # MANUAL — ноль, заданный оператором («Установить 0 здесь», свой у каждой головки);
    # AUTO — ноль по калибровке сопла лазерами (M631) + положение подложки (G54);
    # INTERNAL — координаты принтера (смена головок); CURRENT — от точки, где стояло сопло.
    @staticmethod
    def autocalibration(on: bool) -> str:
        """M641 1 — печатать в AUTO (после смены головки — калибровка сопла), 0 — в MANUAL."""
        return f"main M641 {1 if on else 0}"

    @staticmethod
    def coord_system(n: int) -> str:
        """M640: 0 MANUAL, 1 AUTO, 2 CURRENT, 3 INTERNAL."""
        return f"main M640 {int(n)}"

    # ── Стол с подложкой ──
    @staticmethod
    def cover_lift(on: bool) -> str:
        """M643: снимать ли крышку при выдвижении/заборе стола."""
        return f"main M643 {1 if on else 0}"

    @staticmethod
    def table_out_flag(on: bool) -> str:
        return f"main M642 {1 if on else 0}"

    TABLE_OUT = "main M624 1"       # выдвинуть стол (отдать подложку)
    TABLE_IN = "main M623 1"        # забрать стол внутрь (в рабочее положение)

    # ── Камеры ──
    @staticmethod
    def camera_exposure(monitor: int, expo: int) -> str:
        # адресат «main» обязателен: шлюз выполняет только команды num1/num2/main
        # (в oct_ui команда шла без адресата — шлюз её молча пропускал)
        return f"main M590 {int(monitor)} {int(expo)}"


# Оси периферии: (плата, ось)
class Axis:
    TABLE_INOUT = ("num2", 0)
    TABLE_LIFT = ("num2", 2)
    HOMOG = [  # гомогенизаторы (в шлюзе и старом UI — «осцилляторы»)
        {"rot": ("num1", 4), "lift": ("num1", 3), "servo": 0},
        {"rot": ("num1", 6), "lift": ("num1", 5), "servo": 1},
    ]
    MANIP = [
        {"rot": ("num2", 4), "lift": ("num2", 3)},
        {"rot": ("num2", 6), "lift": ("num2", 5)},
    ]


RELAYS = [(0, "Насос"), (1, "Стол"), (2, "Крышка"), (3, "Сброс")]


def emergency_stop_sequence() -> list[str]:
    """Остановить программу и все оси обеих плат.

    M700 — общая остановка в шлюзе (программа + «M589 S» на обе платы).
    «M589 S» на платы дублируется напрямую: так оси встанут, даже если шлюз занят.
    Нагрев стола выключает вызывающий код (BedModel.force_off).
    """
    return [Cmd.STOP_ALL, "num1 M589 S", "num2 M589 S"]


# ─────────────────────────── Телеметрия ───────────────────────────
# Строка прошивки (string_periphery.cpp, StringPeriphery::state_cur), поля через пробел:
#   stN <N> <ring_max> <send> <флаги> v0 … v7 [0 0 0 — у платы без KINEMATIK, т. е. st2]
#   N        — номер последней принятой платой команды;
#   ring_max — сколько кадров записано в кольцевой буфер;
#   send     — какая группа v0..v7 в строке: 0, 1, 2 по кругу (3 и 4 в прошивке есть, но не шлются);
#   флаги    — 5 цифр: моторы платы движутся · моторы кинематики (X Y Z E) движутся ·
#              идёт программа из кольцевого буфера · идёт пауза (G4) · ждём температуру.
#   send 0: калибровка дельты · лазеры «A00B» · концевики (8 цифр) · температура ×100 ·
#           счётчик цикла · поиск сопла по XYZ (3 цифры) · датчики инструмента (8 цифр) · 0
#   send 1: положение 8 осей в шагах
#   send 2: кольцевой буфер — записано, выполнено, конец, строка, время, № программы кадра,
#           № текущей программы, X кадра
#   send 3: оставшиеся шаги 8 осей; send 4: датчики инструмента по отдельности
# К строкам st1 шлюз дописывает « ttttt xc yc zc xm ym zm xt yt zt СК»:
#   ttttt — головка в левом манипуляторе, в правом, в гомогенизаторе 1, 2, головка на сопле
#           (0 — нет, 1–3 — номер головки);
#   xc yc zc — сопло в текущей системе координат (той, в которой шлюз сейчас двигает);
#   xm ym zm — координаты машины;
#   xt yt zt — координаты инструмента: в системе печати (MANUAL или AUTO, M641);
#   СК — текущая система координат: MANUAL / AUTO / INTERNAL / CURRENT.
#   Формат чисел зависит от локали ПК шлюза — разбираем «12.5» и «12,5».
# Лазеры (send 0, второе поле) — две цифры: лазер 1, лазер 2; 1 — луч цел (замкнут).

SEND_NAMES = {0: "состояние", 1: "положение", 2: "буфер", 3: "шаги", 4: "датчики"}

COORD_SYSTEMS = {
    "MANUAL": "Ручная (ноль оператора)",
    "AUTO": "Авто (калибровка + подложка)",
    "INTERNAL": "Машинная (смена головки)",
    "CURRENT": "Текущая (от точки старта)",
}


COORD_SYSTEMS_SHORT = {"MANUAL": "Ручная", "AUTO": "Авто", "INTERNAL": "Машинная", "CURRENT": "Текущая"}


def coord_system_short(code: str) -> str:
    return COORD_SYSTEMS_SHORT.get(code, code or "—")


def coord_system_name(code: str) -> str:
    return COORD_SYSTEMS.get(code, code or "—")


@dataclass
class BoardState:
    board: int = 0
    line_num: int = 0          # номер последней принятой платой команды (N)
    ring_counter: int = 0      # кадров записано в кольцевой буфер
    # флаги (есть в каждой строке)
    moving: int = 0            # движется хоть один мотор платы
    moving_kinem: int = 0      # движутся моторы кинематики (дельта и E)
    ring_buf_en: int = 0       # идёт программа из кольцевого буфера
    waiting_time: int = 0      # идёт пауза G4
    waiting_temp: int = 0      # ждём температуру (M190)
    # send 0
    delta_calib: int = 0       # идёт калибровка дельты
    lasers: str = ""           # два лазерных датчика: «11» — оба луча целы
    temp_cur: float = 0.0      # температура, °C
    control_counter: int = 0
    nozzle_search: str = ""    # поиск сопла по X Y Z при калибровке (3 цифры)
    tool_sensors: str = ""     # датчики наличия головок (8 цифр, 0 — головка на месте)
    # от шлюза (только st1)
    x: float = 0.0             # текущая система координат
    y: float = 0.0
    z: float = 0.0
    mx: float = 0.0            # координаты машины
    my: float = 0.0
    mz: float = 0.0
    tx: float = 0.0            # координаты инструмента (система печати)
    ty: float = 0.0
    tz: float = 0.0
    coord_sys: str = ""        # MANUAL / AUTO / INTERNAL / CURRENT
    has_xyz: bool = False
    tools: str = ""            # 5 цифр: манип. левый, правый, гомог. 1, 2, головка на сопле
    cur_pos: list = field(default_factory=lambda: [0] * 8)
    cur_end: list = field(default_factory=lambda: [0] * 8)
    ring: list = field(default_factory=lambda: [0] * 8)
    steps: list = field(default_factory=lambda: [0] * 8)

    def copy(self) -> "BoardState":
        return copy.deepcopy(self)

    # головки по данным шлюза
    def _tool(self, i: int) -> int:
        t = self.tools
        return int(t[i]) if len(t) == 5 and t[i].isdigit() else 0

    @property
    def tool_manip_left(self) -> int:
        return self._tool(0)

    @property
    def tool_manip_right(self) -> int:
        return self._tool(1)

    @property
    def tool_homog(self) -> tuple[int, int]:
        return self._tool(2), self._tool(3)

    @property
    def tool_active(self) -> int:
        return self._tool(4)

    @property
    def tools_known(self) -> bool:
        return len(self.tools) == 5 and self.tools.isdigit()

    def laser(self, i: int) -> bool | None:
        """Лазер i (0, 1): True — луч цел (замкнут), False — перекрыт, None — нет данных."""
        t = self.lasers
        if len(t) != 2 or not t.isdigit():
            return None
        return t[i] == "1"

    def tool_place(self, n: int) -> str:
        """Где головка n: 'nozzle' / 'left' / 'right' / 'homog' / ''."""
        if not self.tools_known or n <= 0:
            return ""
        if self.tool_active == n:
            return "nozzle"
        if self.tool_manip_left == n:
            return "left"
        if self.tool_manip_right == n:
            return "right"
        if n in self.tool_homog:
            return "homog"
        return ""


def _num(tok: str) -> float:
    return float(tok.replace(",", "."))


def _digits(tok: str, n: int) -> list[int]:
    return [int(c) for c in tok[:n]] + [0] * max(0, n - len(tok))


def parse_state_line(line: str, prev: BoardState | None) -> BoardState | None:
    idx = line.find("st")
    if idx < 0:
        return None
    tokens = line[idx:].replace("'", " ").split()
    head = tokens[0] if tokens else ""
    try:
        board = int(head[2:3])
    except ValueError:
        return None
    rest = head[3:]
    values = ([rest] if rest else []) + tokens[1:]
    if len(values) < 12:
        return None
    flags = values[3]
    if len(flags) != 5 or not flags.isdigit():
        return None                    # не та версия прошивки
    st = prev.copy() if prev else BoardState()
    st.board = board
    try:
        st.line_num = int(values[0])
        st.ring_counter = int(values[1])
        send = int(values[2])
        st.moving, st.moving_kinem, st.ring_buf_en, st.waiting_time, st.waiting_temp = _digits(flags, 5)
        v = values[4:12]
        if send == 0:
            st.delta_calib = int(v[0])
            st.lasers = v[1]
            st.cur_end = _digits(v[2], 8)
            st.temp_cur = int(_num(v[3])) / 100.0
            st.control_counter = int(v[4])
            st.nozzle_search = v[5]
            st.tool_sensors = v[6]
        elif send == 1:
            st.cur_pos = [int(_num(t)) for t in v]
        elif send == 2:
            st.ring = [int(_num(t)) for t in v]
        elif send == 3:
            st.steps = [int(_num(t)) for t in v]
        elif send == 4:
            st.tool_sensors = "".join(t[:1] for t in v)
        else:
            return None
    except (ValueError, IndexError):
        return None
    # головки, координаты и СК от шлюза — только у st1 (у st2 в конце «0 0 0» от прошивки)
    if board == 1 and len(values) >= 22:
        if values[12].isdigit():
            st.tools = values[12]
        try:
            c = [_num(t) for t in values[13:22]]
            st.x, st.y, st.z, st.mx, st.my, st.mz, st.tx, st.ty, st.tz = c
            st.has_xyz = True
        except ValueError:
            pass
        if len(values) >= 23:
            st.coord_sys = values[22]
    return st


def format_state_line(board: int, send: int, vals: list, line_num: int = 0, ring: int = 0,
                      flags: str = "00000", coords: tuple | None = None, tools: str | None = None,
                      coord_sys: str = "MANUAL", comma: bool = False) -> str:
    """Строка в формате прошивки (+ хвост, как дописывает шлюз) — для симулятора и тестов.

    coords — 9 чисел: текущая СК, машина, инструмент.
    """
    vals = list(vals) + [0] * (8 - len(vals))
    s = f"st{board} {line_num} {ring} {send} {flags} " + " ".join(str(v) for v in vals[:8]) + " "
    if board != 1:
        s += "0 0 0 "
    if coords is not None:
        t = " ".join(f"{round(c, 4):g}" for c in coords)
        s += " " + (tools or "00000") + " " + (t.replace(".", ",") if comma else t) + " " + coord_sys
    return s
