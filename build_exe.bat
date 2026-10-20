@echo off
REM Build AimTuner.exe (see build_exe.py). Close AimTuner first.
cd /d "%~dp0"
python build_exe.py
pause
