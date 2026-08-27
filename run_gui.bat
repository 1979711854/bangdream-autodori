@echo off
rem ===== autodori GUI launcher =====
cd /d "%~dp0"
if exist "autodori_gui.exe" (
    start "" "autodori_gui.exe"
) else (
    ".venv\Scripts\python.exe" gui.py
)
