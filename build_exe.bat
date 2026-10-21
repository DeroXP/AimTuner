@echo off
REM Build Core.exe (see build_exe.py). Close Core first.
cd /d "%~dp0"
python build_exe.py
pause
