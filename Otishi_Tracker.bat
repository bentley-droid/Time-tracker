@echo off
REM Smart launcher — starts the tracker if it isn't already running, then opens the dashboard.
REM Safe to click anytime: if the tracker is already up, it just opens your browser.

cd /d "%~dp0"

REM Check whether anything is listening on port 5555. PowerShell returns exit 0 if yes.
powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 5555 -State Listen -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"

if %ERRORLEVEL% NEQ 0 (
    echo Tracker not running — starting it...
    REM Launch python detached so closing this window doesn't kill the tracker.
    start "Otishi Time Tracker" /MIN py -3 main.py
    REM Wait for the dashboard to bind to the port
    powershell -NoProfile -Command "1..15 | ForEach-Object { if (Get-NetTCPConnection -LocalPort 5555 -State Listen -ErrorAction SilentlyContinue) { exit 0 }; Start-Sleep -Milliseconds 500 }; exit 1" >nul
)

REM Open the dashboard in the default browser
start "" http://localhost:5555/today
