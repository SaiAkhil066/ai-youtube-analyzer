@echo off
rem One-time setup: creates .venv, installs the Python packages, creates config.json and the keys folder.
cd /d "%~dp0"
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 (
  echo Python 3.10 or newer was not found.
  echo Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH", then run this again.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" python -m venv .venv
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo Package install failed - check your internet connection and run this again.
  pause
  exit /b 1
)
if not exist config.json copy config.example.json config.json >nul
if not exist keys mkdir keys
echo.
echo Setup done.
echo Next: put client_secret.json in the "keys" folder (README step 3), edit config.json,
echo then double-click "Open Dashboard.bat".
pause
