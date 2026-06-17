@echo off
REM One-time setup: add a shortcut to start_tracker.bat in the Windows Startup folder.
REM This makes the tracker launch automatically when you log in.

set STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
set TARGET=%~dp0start_tracker.bat
set SHORTCUT=%STARTUP%\OtishiTimeTracker.lnk

powershell -NoProfile -Command "$s = (New-Object -ComObject WScript.Shell).CreateShortcut('%SHORTCUT%'); $s.TargetPath = '%TARGET%'; $s.WorkingDirectory = '%~dp0'; $s.WindowStyle = 7; $s.Save()"

echo.
echo Auto-start installed. Tracker will launch on next Windows login.
echo To remove: delete "%SHORTCUT%"
pause
