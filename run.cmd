@echo off
rem Launch Music DLP from this folder, using the local virtual environment
rem when it exists. Double-click this file, or run it from a terminal.
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" "main.py"
    exit /b 0
)

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" "main.py"
    exit /b %errorlevel%
)

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 "main.py"
    exit /b %errorlevel%
)

python "main.py"
exit /b %errorlevel%
