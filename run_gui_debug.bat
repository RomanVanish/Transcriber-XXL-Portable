@echo off
rem Диагностический запуск: окно консоли остаётся открытым и показывает ошибки.
cd /d %~dp0
echo Запуск GUI в отладочном режиме (встроенный Python)...
"%~dp0runtime\python.exe" gui.py
echo.
echo Код завершения: %ERRORLEVEL%
pause
