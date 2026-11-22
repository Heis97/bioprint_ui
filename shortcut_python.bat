@echo off
chcp 65001 >nul
rem Ярлык «Биопринт» на рабочем столе без сборки exe: запускает start.py через pythonw.
rem Изменения в коде видны сразу, но на ПК должен быть Python с PyQt5.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0make_shortcut.ps1" -Mode python
pause
