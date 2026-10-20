@echo off
REM Launch AimTuner fullscreen. Any arguments are passed through and keep the
REM console open, e.g.:  run.bat --windowed     run.bat --bench
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
  echo Python was not found on PATH. Install Python 3.10+ and try again.
  pause
  exit /b 1
)

python -c "import glfw, OpenGL, numpy, PIL" >nul 2>&1
if errorlevel 1 (
  echo Installing dependencies...
  python -m pip install -r requirements.txt || (echo Install failed. & pause & exit /b 1)
)

REM With no arguments, hand over to pythonw so no console window sits behind the
REM game. Anything it would have printed goes to aimtuner.log.
if "%~1"=="" (
  where pythonw >nul 2>&1
  if not errorlevel 1 (
    start "" pythonw app.py
    exit /b 0
  )
)

python app.py %*
if errorlevel 1 pause
