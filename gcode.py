"""G-код: отправка в шлюз и модель исполнения для превью, прогресса и оценки времени.

Отправка — строго без изменений: уходят исходные строки, из которых убраны только
комментарии («; …» и «( … )») и пустые строки. Разбирает программу сам шлюз
(tcp_to_udp, PathPlanner.cs → StepperPrinter.convert_g_code_to_stepperframes).

Превью повторяет разбор шлюза, а не стандарт G-кода, чтобы показывать то, что сделает
принтер. Что понимает шлюз (команда ищется подстрокой в первом слове строки, регистр важен):
- G0 / G1 — движение (X Y Z E F; F — мм/мин);
- G90 / G91, M82 / M83, G92 — абсолютные/относительные координаты и E, смена отсчёта;
- G4 S<с> / P<мс> — пауза; G28 — базирование;
- M140 / M190 S<°C> — термостол (M190 ждёт нагрева); M106 / M107 B<плата> P<выход> S<0..255>;
- T<n> — смена головки (M620 n; в режиме «Автокалибровка» затем калибровка сопла M631);
- G54 X<i> — подложка i (сдвиг из settings_printer.json шлюза; X/Y центра не меняются, меняется Z).
Всё остальное шлюз пропускает: в том числе дуги G2/G3 (сопло пойдёт прямо к следующей точке).

Особенности шлюза, о которых пульт предупреждает (notes):
- после T и G54 шлюз начинает отсчёт E заново с нуля: если E в файле продолжает расти,
  первый отрезок после смены выдавит всё накопленное E;
- в G91 смещение прибавляется столько раз, сколько слов в строке (G1 X1 Y1 F300 → X+3);
- G4 P500 шлюз превращает в паузу 5000 (склеивает числа строкой) — надёжнее G4 S;
- M107 переключает в абсолютные координаты.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

from . import config


# ─────────────────────────── отправка ───────────────────────────
def strip_comment(raw: str) -> str:
    line = raw.split(";", 1)[0]
    if "(" in line:
        line = re.sub(r"\([^)]*\)", "", line)
    return line.strip()


def clean_lines(text: str) -> list[tuple[int, str]]:
    """[(номер строки файла с 1, строка без комментария)] — то, что уходит в шлюз."""
    out = []
    for n, raw in enumerate(text.splitlines(), start=1):
        s = strip_comment(raw)
        if s:
            out.append((n, s))
    return out


# ─────────────────────────── модель шлюза ───────────────────────────
def _parse_double(s: str) -> float | None:
    """Как double.Parse в шлюзе (точка как разделитель)."""
    try:
        return float(s)
    except ValueError:
        return None


def _words(vals: list[str], start: int = 1):
    for tok in vals[start:]:
        if len(tok) < 2:
            continue
        v = _parse_double(tok[1:])
        if v is None:
            continue
        yield tok[0], v


def _g4_ms(ms: float, s: float) -> float:
    """Пауза, как её посчитает шлюз: строка "P" + мс + (1000·с), склеенная и разобранная прошивкой."""
    def cs(v: float) -> str:                       # double.ToString() в C#
        return str(int(v)) if float(v).is_integer() else repr(float(v))
    txt = cs(ms) + cs(1000 * s)
    m = re.match(r"\d+", txt)
    return float(m.group()) if m else 0.0


@dataclass
class Report:
    """Что из программы выполнит шлюз — для экрана «Печать»."""
    src_count: int = 0                                   # строк с командами
    sent: int = 0                                        # уйдёт в шлюз (= src_count)
    moves: int = 0                                       # G0/G1
    passed: Counter = field(default_factory=Counter)     # команды, которые шлюз выполнит (кроме движений)
    dropped: Counter = field(default_factory=Counter)    # шлюз пропустит
    notes: list = field(default_factory=list)
    tools: list = field(default_factory=list)            # T по порядку
    plates: list = field(default_factory=list)           # G54 X по порядку
    gateway_points: int = 0                              # (для совместимости; пульт точки больше не вставляет)

    def note(self, text: str):
        if text not in self.notes:
            self.notes.append(text)

    def summary(self) -> str:
        s = f"{self.src_count} строк уходит в шлюз без изменений; движений {self.moves}"
        if self.passed:
            s += "; команды: " + ", ".join(f"{k}×{v}" for k, v in self.passed.most_common())
        if self.dropped:
            items = ", ".join(f"{k}×{v}" for k, v in self.dropped.most_common(6))
            more = "" if len(self.dropped) <= 6 else " …"
            s += f"; шлюз пропустит: {items}{more}"
        return s


@dataclass
class Segment:
    x0: float
    y0: float
    z0: float
    x1: float
    y1: float
    z1: float
    extrude: bool
    tool: int
    line_no: int          # индекс строки в exec_lines
    t0: float = 0.0       # оценка: время от начала программы до начала отрезка, с
    dt: float = 0.0       # оценка: длительность отрезка, с


@dataclass
class Program:
    text: str = ""
    conversion: Report | None = None
    exec_lines: list = field(default_factory=list)   # строки, которые уходят в шлюз
    src_lines: list = field(default_factory=list)    # их номера в файле
    segments: list = field(default_factory=list)
    layers: list = field(default_factory=list)
    bounds: tuple = (0.0, 0.0, 0.0, 0.0)
    zmax: float = 0.0
    extrude_len: float = 0.0
    travel_len: float = 0.0
    est_seconds: float = 0.0
    tools: set = field(default_factory=set)
    first_tool: int | None = None                    # T до первого движения (None — нет)
    first_tool_line: int = -1                        # индекс этой строки в exec_lines
    tool_changes: list = field(default_factory=list) # [(индекс строки в exec_lines, номер головки)]
    has_m190: bool = False

    def layer_index(self, z: float) -> int:
        if not self.layers:
            return 0
        return min(range(len(self.layers)), key=lambda i: abs(self.layers[i] - z))

    def segment_layer(self, seg: Segment) -> int:
        return self.layer_index(seg.z1)

    def remaining_at(self, seg_index: int, t: float) -> float:
        """Оценка оставшегося времени (при 100 % скорости), если сопло на отрезке seg_index, доля t."""
        if not self.segments:
            return 0.0
        i = max(0, min(seg_index, len(self.segments) - 1))
        s = self.segments[i]
        return max(0.0, self.est_seconds - (s.t0 + s.dt * max(0.0, min(1.0, t))))


def parse(text: str) -> Program:
    """Разобрать программу так, как её разберёт шлюз."""
    rep = Report()
    p = Program(text=text, conversion=rep)
    lines = clean_lines(text)
    p.exec_lines = [s for _, s in lines]
    p.src_lines = [n for n, _ in lines]
    rep.src_count = rep.sent = len(lines)

    # состояние разбора — как в convert_g_code_to_stepperframes
    p_offs = [0.0, 0.0, 0.0]
    e_offs = 0.0
    p_cur = [0.0, 0.0, 0.0]
    e_cur = 0.0
    vel = 10.0                      # мм/с, значение по умолчанию в шлюзе
    extr_rel = pos_rel = False
    p_abs = [0.0, 0.0, 0.0]
    e_abs = 0.0
    # исполнение
    tool = 0
    pos = None                      # где сопло (координаты программы); None — неизвестно
    e_prev = 0.0                    # E предыдущей точки
    chunk_start = True              # следующий отрезок — первый в участке шлюза (E от нуля)
    t = 0.0
    zs = set()
    minx = miny = math.inf
    maxx = maxy = -math.inf

    for idx, line in enumerate(p.exec_lines):
        vals = line.replace("  ", " ").replace("  ", " ").split(" ")
        head = vals[0]
        if pos_rel:
            p_cur = [0.0, 0.0, 0.0]
        if extr_rel or pos_rel:
            e_cur = 0.0
        known = False

        if "G1" in head or "G0" in head:
            known = True
            rep.moves += 1
            n_words = 0
            for lit, v in _words(vals):
                n_words += 1
                if lit == "X": p_cur[0] = v
                if lit == "Y": p_cur[1] = v
                if lit == "Z": p_cur[2] = v
                if lit == "E": e_cur = v
                if lit == "F": vel = v / 60.0
                if pos_rel:
                    p_abs = [p_abs[i] + p_cur[i] for i in range(3)]
                else:
                    p_abs = [p_cur[i] + p_offs[i] for i in range(3)]
                if extr_rel or pos_rel:
                    e_abs += e_cur
                else:
                    e_abs = e_cur + e_offs
            if pos_rel and n_words > 1:
                rep.note("G91: шлюз прибавляет смещение столько раз, сколько слов в строке "
                         "(G1 X1 Y1 F300 сдвинет по X на 3 мм) — используйте абсолютные координаты")
            # отрезок: в начале участка шлюз сам добавляет текущее положение сопла с E = 0
            if chunk_start and e_abs > config.E_SPIKE_WARN:
                where = f"после T{rep.tools[-1]}" if rep.tools else ("после G54" if rep.plates else "в начале программы")
                rep.note(f"{where[0].upper() + where[1:]} шлюз считает E от нуля, а в файле E = {e_abs:.3f}: первый "
                         f"отрезок выдавит это количество. После T и G54 начинайте E с 0 (без G92)")
            if pos is not None and math.dist(pos, p_abs) <= 1e-9 and abs(e_abs - e_prev) > 1e-9:
                # E без X/Y/Z: шлюз раздаёт E по длине пути сопла (comp_e_width / comp_frms_line),
                # путь нулевой — поршень не сдвинется
                rep.note("Строки только с E (втягивание, подкачка) шлюз не выполняет: E раздаётся "
                         "по пути сопла, а путь нулевой — поршень останется на месте")
            if pos is not None:
                x0, y0, z0 = pos
                x1, y1, z1 = p_abs
                d = math.dist(pos, p_abs)
                if d > 1e-9:
                    dt = d / max(vel, 0.01)
                    extrude = e_abs - e_prev > 1e-9
                    p.segments.append(Segment(x0, y0, z0, x1, y1, z1, extrude, tool, idx, t, dt))
                    t += dt
                    if extrude:
                        p.extrude_len += d
                        zs.add(round(z1, 3))
                        p.tools.add(tool)
                    else:
                        p.travel_len += d
                    for px, py in ((x0, y0), (x1, y1)):
                        minx, maxx = min(minx, px), max(maxx, px)
                        miny, maxy = min(miny, py), max(maxy, py)
                    p.zmax = max(p.zmax, z0, z1)
            pos = list(p_abs)
            chunk_start = False
            e_prev = e_abs

        if "G2" in head or "G3" in head:
            if not known:
                rep.dropped[head] += 1
                rep.note("Дуги G2/G3 шлюз не выполняет: сопло пойдёт прямо к следующей точке")
            known = True

        if "G4" in head:
            known = True
            ms = s = 0.0
            for lit, v in _words(vals):
                if lit == "S": s = v
                if lit == "P": ms = v
            wait = _g4_ms(ms, s) / 1000.0
            if ms and not s:
                rep.note(f"«{line}»: шлюз склеит числа и сделает паузу {wait:g} с — пишите G4 S<секунды>")
            t += wait
            rep.passed["G4"] += 1

        if "G28" in head:
            known = True
            rep.passed["G28"] += 1
            t += config.HOME_EST_S
            pos = None

        if "G90" in head:
            known = True
            pos_rel = False
        if "G91" in head:
            known = True
            pos_rel = True
        if "M82" in head:
            known = True
            extr_rel = False
        if "M83" in head:
            known = True
            extr_rel = True

        if "G92" in head:
            known = True
            for lit, v in _words(vals):
                if lit == "X": p_offs[0] = p_abs[0]; p_abs[0] = v; p_cur[0] = v
                if lit == "Y": p_offs[1] = p_abs[1]; p_abs[1] = v; p_cur[1] = v
                if lit == "Z": p_offs[2] = p_abs[2]; p_abs[2] = v; p_cur[2] = v
                if lit == "E": e_offs = e_abs; e_abs = v; e_cur = v
            if pos is not None:
                pos = list(p_abs)
            e_prev = e_abs

        if "M106" in head:
            known = True
            rep.passed["M106"] += 1
        if "M107" in head:
            known = True
            rep.passed["M107"] += 1
            pos_rel = False
            if any(l == "G91" for l in p.exec_lines[:idx]):
                rep.note("M107 в шлюзе включает абсолютные координаты (как G90)")

        if "M140" in head:
            known = True
            rep.passed["M140"] += 1
        if "M190" in head:
            known = True
            rep.passed["M190"] += 1
            p.has_m190 = True
            rep.note("M190 ждёт нагрева стола — время ожидания в оценку не входит")

        if "T" in head:
            known = True
            n = 0
            for lit, v in _words(vals, 0):
                if lit == "T":
                    n = int(v)
            if rep.moves == 0 and p.first_tool is None:
                p.first_tool, p.first_tool_line = n, idx
            rep.tools.append(n)
            p.tool_changes.append((idx, n))
            rep.passed["T"] += 1
            tool = n
            t += config.TOOL_CHANGE_EST_S
            chunk_start = True            # новый участок: E с нуля, старт — от места смены головки
            pos = None

        if "G54" in head:
            known = True
            plate = 0
            for lit, v in _words(vals, 0):
                if lit == "X":
                    plate = int(v)
            rep.plates.append(plate)
            rep.passed["G54"] += 1
            chunk_start = True

        if not known:
            rep.dropped[head[:8]] += 1
            if head[:1].islower():
                rep.note("Команды в нижнем регистре шлюз не распознаёт")
            elif head[:1] == "N":
                rep.note("Номера строк N… шлюз не понимает — строка будет пропущена")

    p.est_seconds = t
    if not zs:
        zs = {round(s.z1, 3) for s in p.segments}
    p.layers = sorted(zs)
    if minx is not math.inf:
        p.bounds = (minx, miny, maxx, maxy)
    if rep.tools:
        rep.note(f"Смена головки оценена в {config.TOOL_CHANGE_EST_S:g} с каждая")
    return p


def fmt_duration(sec: float) -> str:
    sec = int(round(max(0.0, sec)))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h} ч {m:02d} мин" if h else f"{m} мин {s:02d} с"
