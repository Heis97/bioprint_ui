"""Расчёт ручной экструзии: объём и расход шприца → ход и скорость поршня (ось E).

1 мкл = 1 мм³; площадь поршня — по внутреннему диаметру шприца (config.SYRINGE_DIAMETERS).
Поршень двигает прошивка напрямую (M587 I7): M — скорость, мм/с; S — ход относительно
текущего положения, мм. Ход точно соответствует объёму (шаги = ход × шагов на мм).
"""
from __future__ import annotations

import math

from . import config


def syringe_diameter(ml: int) -> float:
    return config.SYRINGE_DIAMETERS.get(int(ml), config.SYRINGE_DIAMETERS[5])


def piston_area(ml: int) -> float:
    """Площадь поршня, мм²."""
    d = syringe_diameter(ml)
    return math.pi * d * d / 4


def travel_mm(volume_ul: float, ml: int) -> float:
    """Ход поршня для объёма, мм."""
    return volume_ul / piston_area(ml)


def speed_mm_s(rate_ml_min: float, ml: int) -> float:
    """Скорость поршня для расхода, мм/с (1 мл/мин = 1000/60 мм³/с)."""
    return rate_ml_min * 1000.0 / 60.0 / piston_area(ml)


def duration_s(volume_ul: float, rate_ml_min: float) -> float:
    """Время выдавливания объёма при расходе, с (без разгона и торможения)."""
    return volume_ul / (rate_ml_min * 1000.0 / 60.0) if rate_ml_min > 0 else 0.0


def fmt_duration(s: float) -> str:
    if s < 60:
        return f"{s:.1f} с"
    m, sec = divmod(int(round(s)), 60)
    return f"{m} мин {sec:02d} с" if m < 60 else f"{m // 60} ч {m % 60:02d} мин"
