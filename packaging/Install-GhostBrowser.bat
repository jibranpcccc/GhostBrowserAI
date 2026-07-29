@echo off
setlocal
title GhostBrowser Setup

cd /d "%~dp0" || exit /b 1
if not exist "%CD%\GhostBrowser.exe" (
  echo [ERROR] GhostBrowser.exe is missing. Extract the complete ZIP first.
  pause
  exit /b 1
)
if not exist "%CD%\playwright-browsers\chrome-win64\chrome.exe" (
  echo [ERROR] The bundled Chromium runtime is missing. Extract the complete ZIP first.
  pause
  exit /b 1
)

echo ==============================================
echo   GhostBrowser Portable Setup
echo ==============================================
echo Everything required to run is already bundled.
echo No Python or Chromium installation is needed.
echo.
set /p "ACCOUNT_FILE=Optional Cloudflare accounts file path (press Enter to skip): "
if defined ACCOUNT_FILE (
  "%CD%\GhostBrowser.exe" --import-accounts "%ACCOUNT_FILE%"
  if errorlevel 1 (
    echo.
    echo [ERROR] Account import failed. Check the file and try again.
    pause
    exit /b 1
  )
)

echo.
echo Starting GhostBrowser...
start "" "%CD%\GhostBrowser.exe"
exit /b 0
