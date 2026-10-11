@echo off
title AI YouTube Analyzer
cd /d "%~dp0dashboard"
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo Run setup.bat first.
  pause
  exit /b 1
)
"%~dp0.venv\Scripts\python.exe" server.py
pause
