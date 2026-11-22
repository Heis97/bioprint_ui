@echo off
chcp 65001 >nul
rem Автосъёмка всех экранов пульта для руководства пользователя.
rem Пульт запустится в демо-режиме (без принтера), сам переключит экраны и закроется
rem примерно через минуту. Снимки PNG — в папке screenshots рядом с программой.
cd /d "%~dp0"
python start.py --screenshots "%~dp0screenshots"
explorer "%~dp0screenshots"
pause
