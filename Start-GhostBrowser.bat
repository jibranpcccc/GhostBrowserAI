@echo off
setlocal
title GhostBrowser Backend Server

cd /d "%~dp0" || (
  echo [ERROR] Could not enter the GhostBrowser project directory.
  pause
  exit /b 1
)

set "PYTHON=%CD%\venv\Scripts\python.exe"
if not exist "%PYTHON%" (
  echo [ERROR] The project virtual environment was not found:
  echo         %PYTHON%
  echo Create it and install requirements before launching.
  pause
  exit /b 1
)

echo ==============================================
echo   GhostBrowser API Server
echo ==============================================
echo.

:: Local development runs started from this batch file are allowed to create
:: profiles without a verified proxy. For production deployments, set this
:: environment variable to 1 (e.g. via the system environment) to enforce proxy.
if not defined GHOSTBROWSER_REQUIRE_PROXY set "GHOSTBROWSER_REQUIRE_PROXY=0"
echo [CONFIG] GHOSTBROWSER_REQUIRE_PROXY=%GHOSTBROWSER_REQUIRE_PROXY% (0 = local dev, proxy optional)

if defined GHOSTBROWSER_HOST set "DISPLAY_HOST=%GHOSTBROWSER_HOST%"
if not defined DISPLAY_HOST set "DISPLAY_HOST=127.0.0.1"
if defined GHOSTBROWSER_PORT set "DISPLAY_PORT=%GHOSTBROWSER_PORT%"
if not defined DISPLAY_PORT set "DISPLAY_PORT=8000"

echo [1/2] Running dependency, Chromium, and port checks...
"%PYTHON%" "%CD%\run_server.py" --check --reuse-existing
set "PREFLIGHT_EXIT=%ERRORLEVEL%"
if "%PREFLIGHT_EXIT%"=="10" (
  echo.
  echo [READY] GhostBrowser is already running at http://%DISPLAY_HOST%:%DISPLAY_PORT%/
  echo Opening the existing dashboard instead of starting a second server.
  start "" "http://%DISPLAY_HOST%:%DISPLAY_PORT%/"
  exit /b 0
)
if errorlevel 1 (
  echo.
  echo [ERROR] Preflight failed. The server was not started.
  pause
  exit /b 1
)

echo [2/2] Starting server on http://%DISPLAY_HOST%:%DISPLAY_PORT% ...
echo Keep this window open. The UI opens only after readiness succeeds.
echo.
"%PYTHON%" -u "%CD%\run_server.py"
set "SERVER_EXIT=%ERRORLEVEL%"

if not "%SERVER_EXIT%"=="0" (
  echo.
  echo [ERROR] GhostBrowser exited with code %SERVER_EXIT%.
) else (
  echo.
  echo GhostBrowser stopped normally.
)

pause
exit /b %SERVER_EXIT%
