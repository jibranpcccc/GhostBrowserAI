@echo off
echo ==============================================
echo   GhostBrowser: Compiling Windows Executable
echo ==============================================

echo [1/3] Installing PyInstaller...
pip install pyinstaller

echo [2/3] Cleaning previous builds...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist GhostBrowser.spec del /q GhostBrowser.spec

echo [3/3] Compiling...
pyinstaller --noconfirm ^
  --name "GhostBrowser" ^
  --add-data "frontend;frontend" ^
  --add-data "backend/extensions;backend/extensions" ^
  --hidden-import "playwright.async_api" ^
  --hidden-import "playwright_stealth" ^
  --hidden-import "uvicorn" ^
  --hidden-import "fastapi" ^
  --hidden-import "httpx_socks" ^
  --hidden-import "pytz" ^
  --hidden-import "faker" ^
  --icon NONE ^
  run_server.py

echo.
echo ==============================================
echo BUILD SUCCESSFUL!
echo Output located in: dist\GhostBrowser\GhostBrowser.exe
echo ==============================================
pause
