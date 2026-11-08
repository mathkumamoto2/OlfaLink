@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
echo =============================================
echo  OlfaLink Local Stable: first-time setup
 echo =============================================
if exist ".venv\Scripts\python.exe" goto install
py -3.11 -m venv .venv >nul 2>&1
if not errorlevel 1 goto install
py -3 -m venv .venv >nul 2>&1
if not errorlevel 1 goto install
python -m venv .venv
if errorlevel 1 goto fail
:install
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto fail
".venv\Scripts\python.exe" -m pip install -r requirements-local.txt
if errorlevel 1 goto fail
REM Use CPU-only PyTorch to avoid downloading CUDA dependencies or requiring a GPU.
".venv\Scripts\python.exe" -m pip install --index-url https://download.pytorch.org/whl/cpu "torch>=2.4,<3"
if errorlevel 1 goto fail
".venv\Scripts\python.exe" -c "import fastapi,numpy,torch;from ml.cnn_multitask import load_model;from pathlib import Path;p=Path('models/odor_intensity_multitask_v0_7.pt');load_model(p);print('LOCAL SETUP OK - CPU model ready')"
if errorlevel 1 goto fail
echo.
echo SETUP COMPLETE. Double-click Start_OlfaLink.bat
pause
exit /b 0
:fail
echo.
echo SETUP FAILED. Check the errors above.
echo Install 64-bit Python 3.11 or 3.12 and retry while online.
pause
exit /b 1
