@echo off
rem Pharmacy camera: start and auto-restart after 10 sec if it stops.
rem Log: logs\run.log
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
if not exist logs mkdir logs
:loop
echo ==== %date% %time% start >> logs\run.log
echo Program is running. Log: logs\run.log. Close this window to stop.
python -u pharmacy_attendance.py >> logs\run.log 2>&1
echo ==== %date% %time% stopped, restart in 10 sec >> logs\run.log
echo Program stopped, restart in 10 sec. See logs\run.log
timeout /t 10 /nobreak >nul
goto loop
