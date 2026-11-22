"""Генератор программ: решётки (прямоугольная, круглая), цилиндр, диск, STL-модель.

Координаты — от центра подложки на её поверхности (дно чашки / лунки). Программа начинается
с G54 X<i> — шлюз подставляет сдвиг подложки i (config.G54_INDEX): X/Y центра у всех
подложек общие, меняется Z. Для планшета фигура печатается в выбранных лунках.

Головки: текущая (без T), одна выбранная, своя у каждой лунки или чередование по слоям.
Смена — T<n>. Шлюз после T считает E с нуля, поэтому после каждой T генератор
начинает E заново с 0 (без G92).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import config

SHAPES = {
    "grid_rect": "Решётка ▭",
    "grid_circle": "Решётка ○",
    "cylinder": "Цилиндр",
    "disk": "Спираль",          # сплошной диск архимедовой спиралью (ТУ п. 1.1.11.2: «спираль»)
    "stl": "STL-модель",
}

TOOL_MODES = {
    "current": "Текущая",
    "single": "Одна",
    "wells": "По лункам",
    "layers": "По слоям",
}

ANGLE_SETS = {
    "0/90": [0, 90],
    "0/60/120": [0, 60, 120],
    "0/45/90/135": [0, 45, 90, 135],
}

# Подложки: (рядов, столбцов, шаг лунок, диаметр лунки) — стандарт SBS; Петри — одна «лунка»
SUBSTRATES = {
    "petri35": ("Петри 35", 1, 1, 0.0, 33.0),
    "petri60": ("Петри 60", 1, 1, 0.0, 56.0),
    "petri90": ("Петри 90", 1, 1, 0.0, 86.0),
    "plate6": ("Планшет 6", 2, 3, 39.12, 34.8),
    "plate12": ("Планшет 12", 3, 4, 26.01, 22.1),
    "plate24": ("Планшет 24", 4, 6, 19.3, 15.6),
    "plate48": ("Планшет 48", 6, 8, 13.08, 11.0),
    "plate96": ("Планшет 96", 8, 12, 9.0, 6.4),
}
ROW_NAMES = "ABCDEFGH"
# Высота стенки чашки / глубина лунки, мм — для контуров в 3D
SUBSTRATE_DEPTH = {"petri35": 10.0, "petri60": 15.0, "petri90": 15.0, "plate6": 17.4,
                   "plate12": 17.5, "plate24": 17.4, "plate48": 17.4, "plate96": 10.7}
PLATE_W, PLATE_H = 127.8, 85.5                 # планшет SBS


def well_names(sub: str) -> list[str]:
    _, rows, cols, _, _ = SUBSTRATES[sub]
    if rows * cols == 1:
        return ["центр"]
    return [f"{ROW_NAMES[r]}{c + 1}" for r in range(rows) for c in range(cols)]


def well_centers(sub: str) -> dict[str, tuple[float, float]]:
    """Центры лунок относительно центра подложки; A1 — слева вверху (+Y вверх)."""
    _, rows, cols, pitch, _ = SUBSTRATES[sub]
    if rows * cols == 1:
        return {"центр": (0.0, 0.0)}
    out = {}
    for r in range(rows):
        for c in range(cols):
            out[f"{ROW_NAMES[r]}{c + 1}"] = ((c - (cols - 1) / 2) * pitch, ((rows - 1) / 2 - r) * pitch)
    return out


@dataclass
class GenParams:
    shape: str = "grid_rect"
    width: float = 10.0          # решётка ▭, мм (X)
    length: float = 10.0         # решётка ▭, мм (Y)
    diameter: float = 10.0       # решётка ○, цилиндр, диск
    pitch: float = 1.25          # шаг между филаментами / стенками, мм
    layers: int = 4
    layer_h: float = 0.3
    angles: str = "0/90"
    contour: bool = True
    continuous: bool = True      # решётка одним непрерывным филаментом (соединения тоже печатаются)
    walls: int = 2               # цилиндр
    speed: float = 4.0           # скорость печати, мм/с
    travel_speed: float = 10.0   # скорость переходов, мм/с
    syringe_d: float = 12.06     # внутренний диаметр шприца, мм
    nozzle_d: float = 0.6        # диаметр сопла, мм
    flow: float = 100.0          # коэффициент подачи, %
    z_lift: float = 2.0          # минимальный запас над фигурой, мм (если безопасная высота ниже)
    safe_z: float = 30.0         # безопасная высота переходов между лунками и смены головки, мм
    substrate: str = "petri60"
    wells: list = field(default_factory=list)   # пусто = все лунки
    # STL
    stl_scale: float = 100.0     # масштаб, %
    perimeters: int = 2          # периметров (замкнутых обходов контура)
    # головки
    tool_mode: str = "current"   # TOOL_MODES
    tool: int = 1                # «Одна»; кисть «По лункам»
    well_tools: dict = field(default_factory=dict)          # лунка → головка
    layer_tools: list = field(default_factory=lambda: [[1, 1], [2, 1]])   # [[головка, слоёв], …] по кругу

    def tool_of(self, well: str, layer: int) -> int:
        """Головка для лунки и слоя; 0 — текущая (T не пишется)."""
        if self.tool_mode == "single":
            return int(self.tool)
        if self.tool_mode == "wells":
            return int(self.well_tools.get(well, self.tool))
        if self.tool_mode == "layers":
            pat = [(int(t), max(1, int(c))) for t, c in self.layer_tools] or [(int(self.tool), 1)]
            k = layer % sum(c for _, c in pat)
            for t, c in pat:
                if k < c:
                    return t
                k -= c
        return 0

    @property
    def strand_area(self) -> float:
        """Сечение филамента, мм²: сопло d, слой h — (d − h)·h + π·h²/4 (как в слайсерах)."""
        d, h = self.nozzle_d, self.layer_h
        if d <= h:
            return math.pi * d * d / 4
        return (d - h) * h + math.pi * h * h / 4

    @property
    def rate_ml_min(self) -> float:
        """Расход при печати, мл/мин: скорость × сечение нити × подача (1 мм³ = 1 мкл)."""
        return self.speed * self.strand_area * self.flow / 100.0 * 60.0 / 1000.0

    @property
    def e_per_mm(self) -> float:
        """Ход поршня (E, мм) на 1 мм пути сопла."""
        a_syr = math.pi * self.syringe_d ** 2 / 4
        return self.flow / 100.0 * self.strand_area / a_syr if a_syr > 0 else 0.0

    def size(self, model=None) -> float:
        """Наибольший поперечный размер фигуры, мм."""
        if self.shape == "grid_rect":
            return math.hypot(self.width, self.length)
        if self.shape == "stl":
            if model is None or not model.loaded:
                return 0.0
            w, l, _ = model.size(self.stl_scale / 100.0)
            return math.hypot(w, l)
        return self.diameter

    def n_layers(self, model=None) -> int:
        if self.shape == "stl":
            if model is None or not model.loaded:
                return 0
            _, _, h = model.size(self.stl_scale / 100.0)
            return max(1, int(math.ceil(h / self.layer_h - 1e-6)))
        return int(self.layers)


# ─────────────────────────── геометрия слоя ───────────────────────────
def _clip_rect(p, d, hw, hl):
    """Отрезок прямой p + t·d внутри прямоугольника |x|≤hw, |y|≤hl (Лян — Барски)."""
    t0, t1 = -1e9, 1e9
    for pc, dc, lim in ((p[0], d[0], hw), (p[1], d[1], hl)):
        if abs(dc) < 1e-12:
            if abs(pc) > lim + 1e-9:
                return None
            continue
        a, b = (-lim - pc) / dc, (lim - pc) / dc
        t0, t1 = max(t0, min(a, b)), min(t1, max(a, b))
    if t1 - t0 < 1e-6:
        return None
    return (p[0] + d[0] * t0, p[1] + d[1] * t0), (p[0] + d[0] * t1, p[1] + d[1] * t1)


def _clip_circle(p, d, r):
    # |p + t d|² = r², |d| = 1
    b = p[0] * d[0] + p[1] * d[1]
    c = p[0] ** 2 + p[1] ** 2 - r * r
    disc = b * b - c
    if disc <= 1e-9:
        return None
    s = math.sqrt(disc)
    t0, t1 = -b - s, -b + s
    return (p[0] + d[0] * t0, p[1] + d[1] * t0), (p[0] + d[0] * t1, p[1] + d[1] * t1)


def _circle(r: float, start_ang: float = 0.0, reverse: bool = False, seg: float = 0.5):
    n = max(24, int(math.ceil(2 * math.pi * r / seg)))
    pts = []
    for i in range(n + 1):
        a = start_ang + (-1 if reverse else 1) * 2 * math.pi * i / n
        pts.append((r * math.cos(a), r * math.sin(a)))
    return pts


def _hatch(p: GenParams, angle_deg: float, reverse: bool):
    """Параллельные филаменты под углом, обрезанные по фигуре; змейкой."""
    a = math.radians(angle_deg)
    d = (math.cos(a), math.sin(a))
    n = (-d[1], d[0])
    if p.shape == "grid_rect":
        hw, hl = p.width / 2, p.length / 2
        extent = abs(n[0]) * hw + abs(n[1]) * hl
        clip = lambda q: _clip_rect(q, d, hw, hl)
    else:
        extent = p.diameter / 2
        clip = lambda q: _clip_circle(q, d, p.diameter / 2)
    k = int(math.floor(extent / p.pitch + 1e-9))
    offsets = [i * p.pitch for i in range(-k, k + 1)]
    if reverse:
        offsets.reverse()
    segs = []
    for i, off in enumerate(offsets):
        c = clip((n[0] * off, n[1] * off))
        if c is None:
            continue
        a0, a1 = c
        if len(segs) % 2 == 1:
            a0, a1 = a1, a0
        segs.append([a0, a1])
    if not segs:
        return []
    if not p.continuous:
        return segs
    path = segs[0][:]
    for s in segs[1:]:
        path += s
    return [path]


def stl_layers(p: GenParams, model) -> list:
    """Все слои STL-модели (кэш внутри model)."""
    if model is None or not model.loaded:
        return []
    angs = ANGLE_SETS.get(p.angles, [0, 90])
    return model.layers(p.stl_scale / 100.0, p.layer_h, p.nozzle_d, int(p.perimeters), p.pitch, angs)


def layer_paths(p: GenParams, layer: int, model=None) -> list[list[tuple[float, float]]]:
    """Непрерывные линии печати одного слоя (в координатах центра фигуры)."""
    rev = layer % 2 == 1
    paths = []
    if p.shape == "stl":
        layers = stl_layers(p, model)
        if layer < len(layers):
            paths = [[(float(x), float(y)) for x, y in path] for path in layers[layer]]
        return paths
    if p.shape in ("grid_rect", "grid_circle"):
        if p.contour:
            if p.shape == "grid_rect":
                hw, hl = p.width / 2, p.length / 2
                paths.append([(-hw, -hl), (hw, -hl), (hw, hl), (-hw, hl), (-hw, -hl)])
            else:
                paths.append(_circle(p.diameter / 2, reverse=rev))
        angs = ANGLE_SETS.get(p.angles, [0, 90])
        paths += _hatch(p, angs[layer % len(angs)], reverse=(layer // len(angs)) % 2 == 1)
    elif p.shape == "cylinder":
        r0 = p.diameter / 2
        for w in range(max(1, p.walls)):
            r = r0 - w * p.pitch
            if r <= p.pitch / 2:
                break
            paths.append(_circle(r, reverse=rev))
    elif p.shape == "disk":
        r0 = p.diameter / 2
        pts = []
        if p.contour:
            paths.append(_circle(r0, reverse=rev))
            r0 -= p.pitch
        # архимедова спираль снаружи внутрь, точки через ~0,5 мм
        th, r = 0.0, r0
        while r > p.pitch / 2:
            pts.append((r * math.cos(th), r * math.sin(th)))
            dth = min(0.5 / max(r, 0.5), math.pi / 12)
            th += dth
            r = r0 - p.pitch * th / (2 * math.pi)
        if pts:
            if rev:
                pts.reverse()
            paths.append(pts)
    return paths


# ─────────────────────────── G-код ───────────────────────────
def _f(v: float) -> str:
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def generate(p: GenParams, model=None) -> str:
    names = well_names(p.substrate)
    centers = well_centers(p.substrate)
    wells = [w for w in names if w in (p.wells or names)]
    n_layers = p.n_layers(model)
    layers = [layer_paths(p, L, model) for L in range(n_layers)]
    top = p.layer_h * n_layers
    # Переходы между лунками, смена головки и конец программы — на безопасной высоте над
    # поверхностью подложки: сопло не задевает стенки лунок и края чашки.
    z_travel = max(p.safe_z, top + p.z_lift)
    fp, ft = p.speed * 60, p.travel_speed * 60
    title = SHAPES.get(p.shape, p.shape).replace("▭", "прямоуг.").replace("○", "круглая")
    g54 = config.G54_INDEX.get(p.substrate, 0)
    out = [f"; {title}; подложка {SUBSTRATES[p.substrate][0]} (G54 X{g54}); лунки: {', '.join(wells)}",
           f"; слоёв {n_layers} × {p.layer_h} мм, шаг {p.pitch} мм, скорость {p.speed} мм/с; "
           f"головки: {TOOL_MODES.get(p.tool_mode, p.tool_mode)}",
           f"; шприц Ø{p.syringe_d} мм, сопло Ø{p.nozzle_d} мм, подача {p.flow:g} % → E {p.e_per_mm:.6f} на 1 мм пути",
           "; после каждой T отсчёт E начинается с 0 — так считает шлюз",
           f"; переходы между лунками на безопасной высоте Z{_f(z_travel)} мм",
           "G90", f"G54 X{g54}"]
    st = {"e": 0.0, "x": None, "y": None, "z": None, "tool": None, "well": None}
    epm = p.e_per_mm

    def lift():
        """Вертикальный подъём на безопасную высоту (если сопло ещё не там)."""
        if st["z"] is None or abs(st["z"] - z_travel) > 1e-6:
            out.append(f"G0 Z{_f(z_travel)} F{_f(ft)}")
            st["z"] = z_travel

    def set_tool(t: int):
        if t == 0 or t == st["tool"]:
            return
        if st["x"] is not None:
            lift()
        out.append(f"T{t}")
        # после смены головки высота сопла неизвестна — следующий переход начнётся с подъёма
        st.update(tool=t, e=0.0, x=None, y=None, z=None, well=None)

    def goto_well(well: str):
        """Подъём на безопасную высоту перед переходом к другой лунке."""
        if st["well"] == well:
            return
        if st["x"] is not None:
            lift()
        out.append(f"; лунка {well}")
        st.update(well=well, x=None, y=None)

    def print_layer(L: int, well: str):
        cx, cy = centers[well]
        z = p.layer_h * (L + 1)
        first = True
        for path in layers[L]:
            if len(path) < 2:
                continue
            sx, sy = path[0][0] + cx, path[0][1] + cy
            if st["x"] is None:                              # начало / после T / новая лунка
                # вверх → над лункой → вниз: по диагонали сопло задело бы стенки лунок.
                # E явно: шлюз помнит последнее E из файла, а после T считает от нуля
                e = f"E{st['e']:.5f}"                       # на первом движении участка
                if st["z"] is None or abs(st["z"] - z_travel) > 1e-6:
                    out.append(f"G0 Z{_f(z_travel)} {e} F{_f(ft)}")
                    st["z"] = z_travel
                    e = ""
                out.append(" ".join(v for v in (f"G0 X{_f(sx)} Y{_f(sy)}", e, f"F{_f(ft)}") if v))
                out.append(f"G1 Z{_f(z)} F{_f(ft)}")
            else:
                if first:
                    out.append(f"G1 Z{_f(z)} F{_f(ft)}")
                if math.hypot(sx - st["x"], sy - st["y"]) > 1e-6:
                    out.append(f"G0 X{_f(sx)} Y{_f(sy)} F{_f(ft)}")
            st["z"] = z
            first = False
            st["x"], st["y"] = sx, sy
            for (px, py) in path[1:]:
                nx, ny = px + cx, py + cy
                d = math.hypot(nx - st["x"], ny - st["y"])
                if d < 1e-6:
                    continue
                st["e"] += d * epm
                out.append(f"G1 X{_f(nx)} Y{_f(ny)} E{st['e']:.5f} F{_f(fp)}")
                st["x"], st["y"] = nx, ny

    if p.tool_mode == "layers":
        # слой за слоем во всех лунках: смен головки — только между слоями
        for L in range(n_layers):
            for well in wells:
                set_tool(p.tool_of(well, L))
                goto_well(well)
                print_layer(L, well)
    else:
        # лунки сгруппированы по головкам: каждая головка — один раз
        order = []
        for w in wells:
            t = p.tool_of(w, 0)
            if t not in order:
                order.append(t)
        for t in order:
            set_tool(t)
            for well in (w for w in wells if p.tool_of(w, 0) == t):
                goto_well(well)
                for L in range(n_layers):
                    print_layer(L, well)
    lift()
    return "\n".join(out) + "\n"


def outline_segments(sub: str, selected: list | None = None) -> tuple[list, list]:
    """Контуры подложки для 3D: (отрезки, выделенные). Z = 0 — дно чашки / лунки."""
    _, rows, cols, pitch, well_d = SUBSTRATES[sub]
    h = SUBSTRATE_DEPTH.get(sub, 10.0)
    segs, hi = [], []

    def ring(cx, cy, r, z, mark, n=48):
        for i in range(n):
            a0, a1 = 2 * math.pi * i / n, 2 * math.pi * (i + 1) / n
            segs.append(((cx + r * math.cos(a0), cy + r * math.sin(a0), z),
                         (cx + r * math.cos(a1), cy + r * math.sin(a1), z)))
            hi.append(mark)

    def posts(cx, cy, r, mark, n=4):
        for i in range(n):
            a = 2 * math.pi * i / n + math.pi / 4
            x, y = cx + r * math.cos(a), cy + r * math.sin(a)
            segs.append(((x, y, 0.0), (x, y, h)))
            hi.append(mark)

    if rows * cols == 1:
        r = well_d / 2
        ring(0, 0, r, 0.0, True, 96)
        ring(0, 0, r, h, False, 96)
        posts(0, 0, r, False, 8)
        return segs, hi
    sel = set(selected or [])
    # корпус планшета: прямоугольник по верху лунок
    W, H = PLATE_W / 2, PLATE_H / 2
    corners = [(-W, -H), (W, -H), (W, H), (-W, H), (-W, -H)]
    for z in (0.0, h):
        for (x0, y0), (x1, y1) in zip(corners, corners[1:]):
            segs.append(((x0, y0, z), (x1, y1, z)))
            hi.append(False)
    for x, y in corners[:4]:
        segs.append(((x, y, 0.0), (x, y, h)))
        hi.append(False)
    n = 32 if rows * cols <= 24 else 20
    for name, (cx, cy) in well_centers(sub).items():
        on = name in sel
        ring(cx, cy, well_d / 2, 0.0, on, n)
        if on:
            ring(cx, cy, well_d / 2, h, False, n)
    return segs, hi


def fit_warning(p: GenParams, model=None) -> str:
    _, rows, cols, _, well_d = SUBSTRATES[p.substrate]
    if p.shape == "stl" and (model is None or not model.loaded):
        return "Откройте STL-файл"
    if p.size(model) > well_d:
        where = "в чашку" if rows * cols == 1 else "в лунку"
        return f"Фигура {p.size(model):.1f} мм не помещается {where} Ø{well_d:g} мм"
    return ""
