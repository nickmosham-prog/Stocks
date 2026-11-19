@echo off
rem Double-click to start the screener on Windows.
rem Keeps the PC awake while it runs and opens the dashboard when it is ready.
setlocal
cd /d "%~dp0"
title Stock Screener
set "URL=http://127.0.0.1:8000"

if exist ".venv\Scripts\python.exe" goto :check_running
echo The screener isn't set up in this folder yet.
echo Double-click "Setup Screener (Windows).bat" first, then try again.
goto :end

:check_running
curl.exe -s -o NUL --max-time 2 "%URL%/api/status" >nul 2>nul
if errorlevel 1 goto :start
echo The screener is already running. Opening the dashboard.
start "" "%URL%"
goto :end

:start
rem Start moomoo OpenD if it is installed. Harmless if it isn't.
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\windows\open_opend.ps1"

rem Open the dashboard once the screener answers (up to about 10 minutes,
rem since the first start of the day may refresh price history first).
start "" /min powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "scripts\windows\wait_and_open.ps1" -Url "%URL%"

set "PYTHONUTF8=1"
echo.
echo Starting the screener. Leave this window open.
echo To stop it: click this window and press Control+C, then Y.
echo.
".venv\Scripts\python.exe" main.py
echo.
echo The screener has stopped.

:end
echo.
pause
