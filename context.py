"""Общий контекст экранов: связь, задание, общие модели состояния."""
from __future__ import annotations

from PyQt5 import QtCore

from . import config
from .job import Job
from .gateway import GatewayProcess
from .printer import PrinterLink
from .protocol import Cmd


class BedModel(QtCore.QObject):
    """Термостол — одно состояние для всех экранов."""

    changed = QtCore.pyqtSignal()

    def __init__(self, link: PrinterLink, settings: config.UserSettings):
        super().__init__()
        self.link, self.settings = link, settings
        self.on = False
        self.setpoint = int(settings.bed_setpoint)

    def set_on(self, on: bool):
        if on != self.on:
            self.on = on
            self.link.send(Cmd.heater_enable(on))
            self.changed.emit()

    def force_off(self):
        """Выключить нагрев безусловно (аварийный стоп): команда уходит, даже если уже выкл."""
        self.link.send(Cmd.heater_enable(False))
        if self.on:
            self.on = False
            self.changed.emit()

    def set_setpoint(self, t: float):
        t = int(round(t))
        if t != self.setpoint:
            self.setpoint = t
            self.settings.bed_setpoint = t
            self.settings.save()
            self.link.send(Cmd.heater_setpoint(t))
            self.changed.emit()


class Ctx:
    def __init__(self, settings: config.UserSettings):
        self.settings = settings
        self.link = PrinterLink(settings)
        self.job = Job(self.link)
        self.bed = BedModel(self.link, settings)
        self.gateway = GatewayProcess(settings)
        self.link.motion_lock = lambda: self.job.active
        self.navigate = lambda name: None      # подставляет главное окно
        self.set_ui_mode = lambda mode: None   # подставляет главное окно
        self.set_theme = lambda name: None     # подставляет главное окно
