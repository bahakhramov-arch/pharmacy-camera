@echo off
rem Pharmacy camera PC: staff must not turn it off by accident.
rem Run as administrator (right-click - Run as administrator). Undo: unlock_pc.bat
rem Shut down when really needed: stop.bat, then in cmd:  shutdown /s /t 0
net session >nul 2>&1
if errorlevel 1 (
  echo FAILED. Right-click this file - Run as administrator.
  pause
  exit /b 1
)

echo [1] Power button, sleep button, lid, Start power button: do nothing
for %%M in (/setacvalueindex /setdcvalueindex) do (
  powercfg %%M SCHEME_CURRENT SUB_BUTTONS PBUTTONACTION 0
  powercfg %%M SCHEME_CURRENT SUB_BUTTONS SBUTTONACTION 0
  powercfg %%M SCHEME_CURRENT SUB_BUTTONS LIDACTION 0
)
powercfg /setactive SCHEME_CURRENT

echo [2] Never sleep or hibernate (on power)
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
powercfg /hibernate off

echo [3] Hide Shut down / Restart / Sleep in Start menu and Alt+F4
reg add "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\Explorer" /v NoClose /t REG_DWORD /d 1 /f >nul
reg add "HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\Explorer" /v NoClose /t REG_DWORD /d 1 /f >nul
reg add "HKLM\SOFTWARE\Microsoft\PolicyManager\default\Start\HidePowerButton" /v value /t REG_DWORD /d 1 /f >nul

echo [4] No power button on the lock / login screen
reg add "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System" /v ShutdownWithoutLogon /t REG_DWORD /d 0 /f >nul

echo [5] Windows Update: no automatic restart while a user is logged on
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU" /v NoAutoRebootWithLoggedOnUsers /t REG_DWORD /d 1 /f >nul
reg add "HKLM\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings" /v ActiveHoursStart /t REG_DWORD /d 6 /f >nul
reg add "HKLM\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings" /v ActiveHoursEnd /t REG_DWORD /d 23 /f >nul

echo.
echo OK. Restart Windows once (shutdown /r /t 0) so the Start menu changes apply.
echo Still needed by hand: automatic logon (netplwiz) and BIOS "power on when AC connected".
pause
