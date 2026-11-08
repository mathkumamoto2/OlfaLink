@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
 echo Setup is needed. Run Setup_Windows.bat first.
 pause
 exit /b 1
)
".venv\Scripts\python.exe" run_local.py
if errorlevel 1 (
 echo OlfaLink stopped with an error. Check the messages above.
 pause
)
