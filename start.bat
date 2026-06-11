@echo off
title Digital Human

cd /d "%~dp0"

echo [CHECK] Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.10+
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo [CHECK] Creating venv...
    python -m venv .venv
)

call .venv\Scripts\activate.bat

echo [CHECK] Installing dependencies...
pip install -r backend\requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple -q

echo.
echo ============================================
echo   Service starting...
echo   http://localhost:8000
echo   Press Ctrl+C to stop
echo ============================================
echo.

cd backend
python app.py

pause
