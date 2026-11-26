@echo off
cd /d "%~dp0"
python -m venv .venv-whisper
if errorlevel 1 goto failed
".venv-whisper\Scripts\python.exe" -m pip install --timeout 180 "torch==2.10.0+cu128" --index-url https://download.pytorch.org/whl/cu128
if errorlevel 1 goto failed
".venv-whisper\Scripts\python.exe" -m pip install --timeout 180 -r requirements-desktop.txt
if errorlevel 1 goto failed
".venv-whisper\Scripts\python.exe" -c "from huggingface_hub import snapshot_download; snapshot_download('openai/whisper-base.en',local_dir='models/whisper/baseline',allow_patterns=['*.json','*.txt','*.safetensors'],max_workers=2)"
if errorlevel 1 goto failed
echo Setup complete. Run run.bat to start CounterCheck Desktop.
echo To download real datasets, fine-tune and evaluate the experiments, run reproduce_whisper.bat.
pause
exit /b 0
:failed
echo Setup failed. Read the error above. CUDA build requires a compatible NVIDIA driver.
pause
exit /b 1
