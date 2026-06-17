@echo off
REM Start the time tracker. Closes only via Task Manager
REM or by running stop_tracker.bat.

cd /d "%~dp0"

REM Use the Python launcher (resilient to version upgrades).
REM Falls back to the Python 3.13 install path if py.exe isn't on PATH.
where py >nul 2>&1
if %ERRORLEVEL%==0 (
    set "PY=py -3"
) else (
    set "PY=C:\Users\bentl\AppData\Local\Programs\Python\Python313\python.exe"
)

echo Starting Otishi Time Tracker...
start "Otishi Time Tracker" %PY% main.py
echo.
echo Tracker running. Dashboard: http://localhost:5555
echo To stop it, run: stop_tracker.bat
echo.
timeout /t 4 >nul
start "" http://localhost:5555
