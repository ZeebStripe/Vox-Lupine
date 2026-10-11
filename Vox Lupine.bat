@echo off
rem Opens the Vox Lupine window (no console window).
if not exist "%~dp0.venv\Scripts\pythonw.exe" (
  echo Run setup.bat first.
  pause
  exit /b 1
)
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0app.py"
