@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" "src\autodori.py" --livemode challengelive --difficulty hard --skip-version-check %*
) else if exist "autodori.exe" (
    "autodori.exe" --livemode challengelive --difficulty hard --skip-version-check %*
) else (
    echo Python environment or autodori.exe not found. See README.md.
)
pause
