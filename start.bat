@echo off
title GhostBrowser AI - Anti-Detect Browser
color 0A
cls

echo ==============================================
echo   GhostBrowser AI - Anti-Detect Browser
echo ==============================================
echo.

cd /d "%~dp0"

:: Check Python is installed
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in PATH.
    echo.
    echo Please install Python 3.10+ from https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)

echo [1/5] Setting up virtual environment...
if not exist "venv" (
    python -m venv venv
    echo       Created new virtual environment.
) else (
    echo       Virtual environment already exists.
)

call venv\Scripts\activate.bat

echo [2/5] Installing dependencies (first run takes ~2 minutes)...
pip install -r requirements.txt --quiet --disable-pip-version-check 2>nul
if %errorlevel% neq 0 (
    echo       Retrying with verbose output...
    pip install -r requirements.txt
)

echo [3/5] Installing browser engine (Chromium)...
python -m playwright install chromium --with-deps 2>nul
if %errorlevel% neq 0 (
    python -m playwright install chromium
)

echo [4/5] Creating data directories...
if not exist "profiles_data" mkdir profiles_data
if not exist "cloudflare_accounts.txt" (
    echo # Add your Cloudflare accounts here - one per line > cloudflare_accounts.txt
    echo # Format: ACCOUNT_ID TAB API_TOKEN >> cloudflare_accounts.txt
    echo # Get free accounts at: https://dash.cloudflare.com/sign-up >> cloudflare_accounts.txt
    echo. >> cloudflare_accounts.txt
    echo       Created cloudflare_accounts.txt - add your accounts in the UI.
)

echo [5/5] Starting GhostBrowser AI...
echo.
echo ==============================================
echo   Server starting at: http://127.0.0.1:8888
echo   Keep this window open while using GhostBrowser
echo ==============================================
echo.

start "" "http://127.0.0.1:8888"
python run_server.py

pause
