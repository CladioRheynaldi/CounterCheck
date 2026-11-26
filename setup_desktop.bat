@echo off
cd /d "%~dp0"
if not exist ".venv-whisper\Scripts\python.exe" (
  call setup_whisper.bat
  if errorlevel 1 exit /b 1
)
".venv-whisper\Scripts\python.exe" -m pip install -r requirements-desktop.txt
if errorlevel 1 (
  echo Desktop setup failed. Read the error above.
  pause
  exit /b 1
)
echo Setup complete. Double-click run.bat to open CounterCheck Desktop.
pause
