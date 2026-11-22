@echo off
chcp 65001 >nul
rem Сборка Bioprint.exe и ярлык «Биопринт» на рабочем столе.
rem Запуск: двойной щелчок. Нужен установленный Python 3.9+ (тот же, что для VS Code).
cd /d "%~dp0"

set "PY=py -3"
%PY% --version >nul 2>&1 || set "PY=python"
%PY% --version >nul 2>&1 || (echo Python не найден. Установите Python с python.org и повторите. & pause & exit /b 1)

echo === 1/3 Библиотеки: PyQt5, numpy, opencv-python, pyinstaller
%PY% -m pip install --disable-pip-version-check -r requirements.txt pyinstaller || (echo Не удалось установить библиотеки. & pause & exit /b 1)

echo === 2/3 Сборка dist\Bioprint\Bioprint.exe
%PY% -m PyInstaller --noconfirm --clean bioprint.spec || (echo Сборка не удалась, см. сообщения выше. & pause & exit /b 1)

echo === 3/3 Ярлык на рабочем столе
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0make_shortcut.ps1" -Mode exe

echo.
echo Готово. Программа: %~dp0dist\Bioprint\Bioprint.exe
echo Папку dist\Bioprint можно перенести на другой ПК целиком (Python там не нужен),
echo затем создать ярлык заново: make_shortcut.ps1 -Mode exe -Exe путь\Bioprint.exe
pause
