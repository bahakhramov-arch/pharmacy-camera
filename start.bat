@echo off
chcp 65001 >nul
rem Запуск учёта. Если программа упала или пропала сеть - перезапуск через 10 сек.
rem Журнал работы: logs\run.log
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
if not exist logs mkdir logs
:loop
echo ==== %date% %time% start >> logs\run.log
python -u pharmacy_attendance.py >> logs\run.log 2>&1
echo ==== %date% %time% stopped, restart in 10 sec >> logs\run.log
timeout /t 10 /nobreak >nul
goto loop
