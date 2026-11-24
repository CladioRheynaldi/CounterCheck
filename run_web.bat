@echo off
cd /d "%~dp0"
if not exist ".venv-whisper\Scripts\python.exe" (
  echo Run setup_whisper.bat first.
  pause
  exit /b 1
)
echo CounterCheck website: http://127.0.0.1:8866
echo Keep this window open while using the website. Press Ctrl+C to stop the local server.
".venv-whisper\Scripts\python.exe" -m countercheck_web.server
if errorlevel 1 pause
