@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
    echo The Python environment is missing.
    echo.
    echo Create it first with:
    echo   py -3.14 -m venv .venv
    echo   .venv\Scripts\python.exe -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

start "" ".venv\Scripts\pythonw.exe" "%~dp0app.pyw"
