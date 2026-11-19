@echo off
rem Double-click once on a new Windows PC to install what the screener needs.
setlocal
cd /d "%~dp0"
title Stock Screener setup

set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && set "PY=py -3"
if not defined PY python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && set "PY=python"
if defined PY goto :venv
echo Python 3.10 or newer isn't installed, or Windows can't find it.
echo Install Python from python.org and tick "Add python.exe to PATH"
echo on the first screen of the installer. Then run this file again.
goto :end

:venv
if exist ".venv\Scripts\python.exe" goto :install
echo Creating the screener's Python folder, .venv ...
%PY% -m venv .venv
if errorlevel 1 goto :failed

:install
echo Installing what the screener needs. This takes a few minutes...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :failed

echo.
echo Setup finished.
if not exist "config\secrets.yaml" echo Reminder: copy secrets.yaml from your Mac into the config folder, or emails won't send.
if not exist "data\stocks.db" echo Reminder: copy stocks.db from your Mac into the data folder to keep your scorecard history.
echo To start the screener, double-click "Start Screener (Windows).bat".
goto :end

:failed
echo.
echo Setup did not finish. Take a screenshot of this window and send it to Claude.

:end
echo.
pause
