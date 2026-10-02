@echo off
cd /d "%~dp0"
if not exist "backend\.venv\Scripts\python.exe" (
  echo Run powershell -File scripts\setup-intent-demo.ps1 first.
  exit /b 1
)
backend\.venv\Scripts\python.exe scripts\intent_demo.py start
