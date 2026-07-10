@echo off
title GhostBrowser Backend Server
echo ==============================================
echo   GhostBrowser API Server (Visible Mode)
echo ==============================================
echo.
echo Starting server... Keep this window open!
echo Once started, you can click "Launch" in the UI.
echo.

cd /d "%~dp0"
call venv\Scripts\activate.bat
python run_server.py

pause
