@echo off
rem Stop the pharmacy camera program started by run_hidden.vbs or start.bat.
cd /d "%~dp0"
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { ($_.Name -eq 'cmd.exe' -and $_.CommandLine -like '*start.bat*') -or ($_.Name -like 'python*' -and $_.CommandLine -like '*pharmacy_attendance*') -or $_.Name -eq 'cloudflared.exe' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
echo Program stopped. To start again: double-click run_hidden.vbs
pause
