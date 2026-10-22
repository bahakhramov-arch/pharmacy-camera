@echo off
chcp 65001 >nul
rem Автозапуск start.bat при входе в Windows (через 1 минуту, чтобы успела подняться сеть).
schtasks /Create /F /SC ONLOGON /DELAY 0001:00 /TN "PharmacyCamera" /TR "\"%~dp0start.bat\""
if errorlevel 1 (echo Не получилось. Запустите этот файл правой кнопкой - "Запуск от имени администратора".) else (echo Готово: учёт будет запускаться сам при входе в Windows.)
pause
