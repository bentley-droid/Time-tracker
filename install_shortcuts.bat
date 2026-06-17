@echo off
REM One-time setup: drops "Otishi Time Tracker" shortcuts on your Desktop and Start Menu,
REM both pointing at the smart launcher. Pin to taskbar from either with right-click.

setlocal
set "SCRIPT_DIR=%~dp0"
REM Strip trailing backslash for cleaner paths
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

set "TARGET=%SCRIPT_DIR%\Otishi_Tracker.bat"
REM Pick a Windows-stock icon (stopwatch-ish). imageres.dll index 16 is the modern clock face.
set "ICON=%SystemRoot%\System32\imageres.dll,16"
set "SHORTCUT_NAME=Otishi Time Tracker.lnk"

echo Creating shortcuts...
powershell -NoProfile -Command ^
  "$ws = New-Object -ComObject WScript.Shell;" ^
  "$desktop = [Environment]::GetFolderPath('Desktop');" ^
  "$startmenu = (Join-Path ([Environment]::GetFolderPath('StartMenu')) 'Programs');" ^
  "foreach ($p in @($desktop, $startmenu)) {" ^
  "  $linkPath = Join-Path $p '%SHORTCUT_NAME%';" ^
  "  $s = $ws.CreateShortcut($linkPath);" ^
  "  $s.TargetPath = '%TARGET%';" ^
  "  $s.WorkingDirectory = '%SCRIPT_DIR%';" ^
  "  $s.IconLocation = '%ICON%';" ^
  "  $s.WindowStyle = 7;" ^
  "  $s.Description = 'Open the Otishi Time Tracker dashboard (auto-starts if not running)';" ^
  "  $s.Save();" ^
  "  Write-Host ('  created: ' + $linkPath);" ^
  "}"

echo.
echo Done. You now have:
echo   - "Otishi Time Tracker" icon on your Desktop
echo   - "Otishi Time Tracker" in your Start Menu (search for it)
echo.
echo To pin to taskbar:
echo   1. Right-click the desktop icon
echo   2. Choose "Show more options" (Windows 11) ^> "Pin to taskbar"
echo      (or in Windows 10, "Pin to taskbar" appears directly)
echo.
echo To remove later: just delete the shortcut files.
echo.
pause
