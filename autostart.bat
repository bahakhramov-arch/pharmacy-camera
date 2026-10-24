@echo off
rem Run the program in background (run_hidden.vbs) 1 minute after Windows logon.
schtasks /Create /F /SC ONLOGON /DELAY 0001:00 /TN "PharmacyCamera" /TR "wscript.exe \"%~dp0run_hidden.vbs\""
if errorlevel 1 (echo FAILED. Right-click this file - Run as administrator.) else (echo OK: the program will start automatically after Windows logon.)
pause
