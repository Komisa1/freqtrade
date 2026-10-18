@echo off
REM Doble clic para arrancar el bot en modo simulacion (Windows)
cd /d %~dp0
.venv\Scripts\python.exe -m memebot run
pause
