@echo off
rem One-time setup: creates a private Python environment in .venv and installs
rem the libraries Vox Lupine needs. Safe to run again to repair/update.
setlocal
cd /d "%~dp0"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo Python was not found.
  echo Install Python 3.11 or newer from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" in the installer, then run setup.bat again.
  pause
  exit /b 1
)

echo Using: %PY%
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
if errorlevel 1 (
  echo Python 3.11 or newer is required.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating .venv ...
  %PY% -m venv .venv || (echo Could not create the environment. & pause & exit /b 1)
)

echo Installing libraries ...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt || (echo Install failed. & pause & exit /b 1)

".venv\Scripts\python.exe" -c "import tkinter, pedalboard, sounddevice, soundfile, numpy" || (
  echo A library failed to load. If the error mentions tkinter, reinstall Python
  echo with the "tcl/tk and IDLE" option enabled.
  pause
  exit /b 1
)

echo.
echo Setup complete. Start the app with "Vox Lupine.bat".
pause
