@echo off
rem Karelia voice guide: Telegram bot + mini app. Double-click to start, Ctrl+C to stop.
cd /d "%~dp0"
title Karelia guide bot

if not exist ".venv\Scripts\python.exe" (
  echo First start: creating virtual environment and installing packages...
  python -m venv .venv || goto :error
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :error
)

".venv\Scripts\python.exe" run.py
echo.
pause
exit /b

:error
echo.
echo Python 3.11 or newer is required: https://www.python.org/downloads/
pause
