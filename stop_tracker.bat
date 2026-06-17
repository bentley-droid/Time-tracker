@echo off
REM Stop any running tracker processes. Uses taskkill since WMIC was
REM deprecated/removed in newer Windows 11 builds.

echo Stopping Otishi Time Tracker...

REM Find python processes whose commandline contains main.py and kill them.
REM PowerShell one-liner — no WMIC dependency.
powershell -NoProfile -Command ^
  "Get-CimInstance Win32_Process | Where-Object { ($_.Name -eq 'python.exe' -or $_.Name -eq 'pythonw.exe') -and $_.CommandLine -match 'main\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Write-Host ('  killed PID ' + $_.ProcessId) }"

echo Done.
timeout /t 2 >nul
