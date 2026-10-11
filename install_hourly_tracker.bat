@echo off
rem Optional: saves your live view counts every hour while the PC is on (Windows Task Scheduler). Run it once.
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Run setup.bat first.
  pause
  exit /b 1
)
schtasks /Create /F /SC HOURLY /TN "YouTube Live Tracker" /TR "\"%~dp0.venv\Scripts\pythonw.exe\" \"%~dp0scripts\live_tracker.py\""
echo.
echo To remove it later:  schtasks /Delete /TN "YouTube Live Tracker" /F
pause
