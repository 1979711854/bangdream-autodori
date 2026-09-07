@echo off
rem ===== autodori GUI launcher (源码版,忽略 autodori_gui.exe) =====
rem 改了 gui.py / ui_widgets.py / ui_theme.py 之后请用这个启动,
rem run_gui.bat 会优先跑打包好的 exe,看不到源码改动。
cd /d "%~dp0"
".venv\Scripts\python.exe" gui.py
if errorlevel 1 pause
