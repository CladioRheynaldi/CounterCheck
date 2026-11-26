@echo off
cd /d "%~dp0"
if not exist ".venv-whisper\Scripts\pythonw.exe" (
  echo Run setup_desktop.bat first.
  pause
  exit /b 1
)
".venv-whisper\Scripts\python.exe" -c "import tkinter, sounddevice" >nul 2>&1
if errorlevel 1 (
  echo Desktop dependencies are missing. Run setup_desktop.bat first.
  pause
  exit /b 1
)
start "CounterCheck Desktop" ".venv-whisper\Scripts\pythonw.exe" -m countercheck_desktop.app
