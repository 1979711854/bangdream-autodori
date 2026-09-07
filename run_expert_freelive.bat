@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" src\autodori.py --mode main --difficulty expert --livemode freelive
pause
