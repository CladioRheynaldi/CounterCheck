@echo off
cd /d "%~dp0"
".venv-whisper\Scripts\python.exe" scripts\prepare_whisper_data.py
if errorlevel 1 goto failed
".venv-whisper\Scripts\python.exe" scripts\train_whisper.py --model clean
if errorlevel 1 goto failed
".venv-whisper\Scripts\python.exe" scripts\train_whisper.py --model overlap
if errorlevel 1 goto failed
".venv-whisper\Scripts\python.exe" scripts\evaluate_whisper.py
if errorlevel 1 goto failed
".venv-whisper\Scripts\python.exe" scripts\measure_whisper_streaming.py
if errorlevel 1 goto failed
echo Experiments finished. See reports\whisper\EVALUATION.md and streaming.json.
pause
exit /b 0
:failed
echo Experiment failed. Read the error above; incomplete measurements are not fabricated.
pause
exit /b 1
