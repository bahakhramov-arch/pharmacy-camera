@echo off
rem Undo lock_pc.bat: normal power button, lid and Start menu again.
rem Run as administrator.
net session >nul 2>&1
if errorlevel 1 (
  echo FAILED. Right-click this file - Run as administrator.
  pause
  exit /b 1
)

rem Windows defaults: power button = shut down (3), sleep button = sleep (1), lid = sleep (1)
for %%M in (/setacvalueindex /setdcvalueindex) do (
  powercfg %%M SCHEME_CURRENT SUB_BUTTONS PBUTTONACTION 3
  powercfg %%M SCHEME_CURRENT SUB_BUTTONS SBUTTONACTION 1
  powercfg %%M SCHEME_CURRENT SUB_BUTTONS LIDACTION 1
)
powercfg /setactive SCHEME_CURRENT

reg delete "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\Explorer" /v NoClose /f >nul 2>&1
reg delete "HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\Explorer" /v NoClose /f >nul 2>&1
reg add "HKLM\SOFTWARE\Microsoft\PolicyManager\default\Start\HidePowerButton" /v value /t REG_DWORD /d 0 /f >nul
reg add "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System" /v ShutdownWithoutLogon /t REG_DWORD /d 1 /f >nul

echo OK. Restart Windows once so the Start menu changes apply.
echo (Sleep stays off and Windows Update stays without auto-restart - better for the cameras.)
pause
