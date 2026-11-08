@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
set "PY=.venv\Scripts\python.exe"
set "MARKER=.venv\.olfalink_setup_complete"

echo ==================================================
echo   OlfaLink Local v1.9.2 - CPU + Dictionary
echo   Actual AWS data or clearly marked SYNTHETIC DEMO
echo ==================================================

if exist "%PY%" goto install_if_needed
where py >nul 2>&1
if not errorlevel 1 (
    py -3.11 -m venv .venv >nul 2>&1
    if exist "%PY%" goto install_if_needed
    py -3.12 -m venv .venv >nul 2>&1
    if exist "%PY%" goto install_if_needed
)
where python >nul 2>&1
if errorlevel 1 goto no_python
python -m venv .venv
if errorlevel 1 goto no_python

:install_if_needed
if exist "%MARKER%" goto run

echo [SETUP] Installing dependencies on FIRST RUN. Internet is required.
"%PY%" -m pip install --upgrade pip
if errorlevel 1 goto fail
"%PY%" -m pip install -r requirements-local.txt
if errorlevel 1 goto fail
"%PY%" -m pip install --index-url https://download.pytorch.org/whl/cpu "torch>=2.4,<3"
if errorlevel 1 goto fail
"%PY%" -c "from pathlib import Path; from ml.cnn_multitask import load_model; load_model(Path('models/odor_intensity_multitask_v0_7.pt')); print('CPU CNN READY')"
if errorlevel 1 goto fail
echo ready> "%MARKER%"

:run
echo [START] Opening http://127.0.0.1:8765
echo [INFO] The server runs while this terminal is open. Stop with Ctrl+C.
"%PY%" run_local.py
if errorlevel 1 goto fail
exit /b 0

:no_python
echo [ERROR] Install Python 3.11 or 3.12 x64 and retry.
goto fail

:fail
echo [ERROR] Setup or launch failed. See messages above.
pause
exit /b 1
