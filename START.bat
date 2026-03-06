@echo off
title Marketing Dronor - Command Center
color 0A
chcp 65001 >nul

echo.
echo  ========================================
echo       MARKETING DRONOR - LAUNCHER
echo  ========================================
echo.

:: Go to project folder
cd /d "%~dp0"

:: Check virtual environment
if not exist "venv" (
    echo [1/5] Creating virtual environment...
    python -m venv venv
) else (
    echo [1/5] Virtual environment found
)

:: Activate venv
echo [2/5] Activating virtual environment...
call venv\Scripts\activate.bat

:: Install dependencies
echo [3/5] Installing dependencies...
pip install -q flask psycopg2-binary bcrypt tweepy anthropic playwright 2>nul

:: Check config
if not exist "infra\config.py" (
    echo.
    echo  [!] WARNING: infra\config.py not found!
    echo  [!] Copy infra\config.example.py to infra\config.py
    echo  [!] and fill in your credentials.
    echo.
    pause
    exit /b 1
)

echo [4/5] Starting Command Center...
echo.
echo  ========================================
echo   Command Center: http://localhost:5555
echo  ========================================
echo.

:: Open browser automatically
echo [5/5] Opening browser...
start http://localhost:5555

:: Start server
cd command_center
python cc_backend.py

pause