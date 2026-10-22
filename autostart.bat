@echo off
rem Run start.bat automatically 1 minute after Windows logon.
schtasks /Create /F /SC ONLOGON /DELAY 0001:00 /TN "PharmacyCamera" /TR "\"%~dp0start.bat\""
if errorlevel 1 (echo FAILED. Right-click this file - Run as administrator.) else (echo OK: the program will start automatically after Windows logon.)
pause
