@echo off
REM Print the controller comparison from logs\runs.jsonl. Arguments pass through,
REM e.g.:  analyze.bat --runs     analyze.bat --last 5
cd /d "%~dp0"
python analyze.py %*
pause
