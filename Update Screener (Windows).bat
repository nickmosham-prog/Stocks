@echo off
rem Double-click to download the latest version of the screener.
rem Stop the screener first, then start it again afterwards.
setlocal
cd /d "%~dp0"
title Stock Screener update

curl.exe -s -o NUL --max-time 2 "http://127.0.0.1:8000/api/status" >nul 2>nul
if errorlevel 1 goto :check_git
echo The screener is running. Stop it first: click its window,
echo press Control+C, then Y. Then run this file again.
goto :end

:check_git
git --version >nul 2>nul
if not errorlevel 1 goto :pull
echo Git isn't installed. Install "Git for Windows" from git-scm.com, then try again.
goto :end

:pull
echo Downloading the latest version...
git pull --ff-only
if errorlevel 1 goto :failed

if not exist ".venv\Scripts\python.exe" goto :no_venv
echo Installing anything new it needs...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :failed
echo.
echo Up to date. Double-click "Start Screener (Windows).bat" to start it.
goto :end

:no_venv
echo.
echo Downloaded. The screener isn't set up yet: double-click "Setup Screener (Windows).bat".
goto :end

:failed
echo.
echo The update did not finish. Take a screenshot of this window and send it to Claude.

:end
echo.
pause
