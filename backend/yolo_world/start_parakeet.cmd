@echo off
cd /d "%~dp0"
if exist ".venv-parakeet\Scripts\python.exe" (
  ".venv-parakeet\Scripts\python.exe" -u parakeet_asr.py %*
) else (
  python -u parakeet_asr.py %*
)
if errorlevel 1 pause
