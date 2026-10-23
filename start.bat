@echo off
rem Pharmacy camera: start and auto-restart after 10 sec if it stops.
rem Log: logs\run.log
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
if not exist logs mkdir logs

rem Internet access to the reports site (Cloudflare Tunnel), only if setup_tunnel.ps1 was run
set CF=
for %%P in ("%ProgramFiles(x86)%\cloudflared\cloudflared.exe" "%ProgramFiles%\cloudflared\cloudflared.exe") do if exist %%P set CF=%%P
if not defined CF for /f "delims=" %%P in ('where cloudflared 2^>nul') do set CF="%%P"
if defined CF if exist "%USERPROFILE%\.cloudflared\config.yml" (
  echo Starting internet access tunnel...
  start "cloudflare-tunnel" /min %CF% tunnel run
)

:loop
echo ==== %date% %time% start >> logs\run.log
echo Program is running. Log: logs\run.log. Close this window to stop.
python -u pharmacy_attendance.py >> logs\run.log 2>&1
echo ==== %date% %time% stopped, restart in 10 sec >> logs\run.log
echo Program stopped, restart in 10 sec. See logs\run.log
timeout /t 10 /nobreak >nul
goto loop
