@echo off
setlocal enabledelayedexpansion
title GhostBrowser Build

cd /d "%~dp0" || (
  echo [ERROR] Could not enter the project directory.
  exit /b 1
)

set "PYTHON=%CD%\venv\Scripts\python.exe"
if not exist "%PYTHON%" (
  echo [ERROR] Missing virtual environment Python: %PYTHON%
  exit /b 1
)

echo ==============================================
echo   GhostBrowser: Compiling Windows Executable
echo ==============================================
echo.

echo [1/5] Checking build dependencies...
"%PYTHON%" -c "import PyInstaller, fastapi, uvicorn, playwright, playwright_stealth, multipart, croniter, psutil, httpx, httpx_socks, pydantic, cryptography" >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Build dependencies are missing.
  echo Install them with:
  echo   "%PYTHON%" -m pip install -r requirements.txt -r requirements-build.txt
  exit /b 1
)

echo [2/5] Resolving authoritative Chromium engine identity...
set "ENGINE_IDENTITY_FILE=%CD%\artifacts\engine_identity.json"
if not exist "%CD%\artifacts" mkdir "%CD%\artifacts"
"%PYTHON%" -c "from backend.engine_resolver import get_engine_identity; id = get_engine_identity(); id.save(); print('Authoritative Engine: ' + id.executable_path + ' (v' + id.exact_version + ', SHA: ' + id.sha256[:16] + '...)')"
if errorlevel 1 (
  echo [ERROR] Authoritative Chromium engine could not be resolved.
  exit /b 1
)

echo Checking launcher syntax and importing application...
"%PYTHON%" -m py_compile "%CD%\run_server.py" "%CD%\backend\main.py"
if errorlevel 1 (
  echo [ERROR] Python syntax validation failed. Build aborted.
  exit /b 1
)
"%PYTHON%" -c "from backend.main import app; from backend.config import get_installed_chromium_path; import pathlib; p=pathlib.Path(get_installed_chromium_path()); assert p.is_file(), p"
if errorlevel 1 (
  echo [ERROR] Application import or Chromium validation failed. Build aborted.
  exit /b 1
)
"%PYTHON%" "%CD%\scripts\check_chromium_compat.py"
if errorlevel 1 (
  echo [ERROR] Chromium compatibility gate failed. Build aborted.
  exit /b 1
)

echo [3/5] Locating the validated Chromium directory...
set "CHROMIUM_PATH_FILE=%TEMP%\ghostbrowser-chromium-path-%RANDOM%.txt"
"%PYTHON%" -c "from backend.engine_resolver import get_engine_identity; print(get_engine_identity().executable_path)" > "%CHROMIUM_PATH_FILE%"
if errorlevel 1 (
  if exist "%CHROMIUM_PATH_FILE%" del /q "%CHROMIUM_PATH_FILE%"
  echo [ERROR] Could not query engine path.
  exit /b 1
)
set /p CHROMIUM_EXE=<"%CHROMIUM_PATH_FILE%"
del /q "%CHROMIUM_PATH_FILE%"
if not defined CHROMIUM_EXE (
  echo [ERROR] Could not resolve Chromium executable.
  exit /b 1
)
for %%I in ("%CHROMIUM_EXE%") do set "CHROMIUM_DIR=%%~dpI"
if not exist "%CHROMIUM_DIR%chrome.exe" (
  echo [ERROR] Resolved Chromium directory is invalid: %CHROMIUM_DIR%
  exit /b 1
)

echo [4/5] Cleaning previous build output...
if exist "%CD%\build" rmdir /s /q "%CD%\build"
if errorlevel 1 exit /b 1
if exist "%CD%\dist" rmdir /s /q "%CD%\dist"
if errorlevel 1 exit /b 1
if exist "%CD%\GhostBrowser.spec" del /q "%CD%\GhostBrowser.spec"
if errorlevel 1 exit /b 1

echo [5/5] Compiling application executable with PyInstaller...
"%PYTHON%" -m PyInstaller --noconfirm --clean --onedir ^
  --name "GhostBrowser" ^
  --add-data "frontend;frontend" ^
  --add-data "backend/extensions;backend/extensions" ^
  --hidden-import "playwright.async_api" ^
  --hidden-import "playwright_stealth" ^
  --collect-data "playwright_stealth" ^
  --hidden-import "uvicorn" ^
  --hidden-import "fastapi" ^
  --hidden-import "httpx_socks" ^
  --hidden-import "croniter" ^
  --hidden-import "cryptography" ^
  --hidden-import "backend.credential_store" ^
  --hidden-import "backend.device_cohorts" ^
  --hidden-import "backend.launch_policy" ^
  "%CD%\run_server.py"
if errorlevel 1 (
  echo [ERROR] PyInstaller failed. No successful build is being reported.
  exit /b 1
)

echo Copying the exact validated Chromium bundle...
xcopy "%CHROMIUM_DIR%*" "%CD%\dist\GhostBrowser\playwright-browsers\chrome-win64\" /E /I /H /Y >nul
if errorlevel 1 (
  echo [ERROR] Chromium could not be copied into the distribution.
  exit /b 1
)

if not exist "%CD%\dist\GhostBrowser\GhostBrowser.exe" (
  echo [ERROR] Build command returned successfully but the executable is missing.
  exit /b 1
)
set "PACKAGED_CHROME=%CD%\dist\GhostBrowser\playwright-browsers\chrome-win64\chrome.exe"
if not exist "%PACKAGED_CHROME%" (
  echo [ERROR] Build output is missing its validated Chromium executable: %PACKAGED_CHROME%
  exit /b 1
)

echo Validating packaged browser integrity against pre-build engine identity...
"%PYTHON%" "%CD%\scripts\verify_packaged_browser.py" "%PACKAGED_CHROME%"
if errorlevel 1 (
  echo [ERROR] Packaged browser failed integrity check against validated EngineIdentity. Build aborted.
  exit /b 1
)

copy /y "%CD%\packaging\Install-GhostBrowser.bat" "%CD%\dist\GhostBrowser\Install-GhostBrowser.bat" >nul
if errorlevel 1 (
  echo [ERROR] Portable installer could not be copied into the distribution.
  exit /b 1
)
copy /y "%CD%\packaging\README-FIRST.txt" "%CD%\dist\GhostBrowser\README-FIRST.txt" >nul
if errorlevel 1 (
  echo [ERROR] Portable instructions could not be copied into the distribution.
  exit /b 1
)

if exist "%CD%\dist\GhostBrowser\cloudflare_accounts.txt" (
  echo [ERROR] Secret account file was found in the build output. Delete the output before distribution.
  exit /b 1
)
if exist "%CD%\dist\GhostBrowser\cloudflare_accounts.priority.txt" (
  echo [ERROR] Priority secret account file was found in the build output. Delete the output before distribution.
  exit /b 1
)

echo Running release audit...
"%PYTHON%" "%CD%\scripts\release_audit.py"
if errorlevel 1 (
  echo [ERROR] Release audit failed. Build output must not be distributed.
  exit /b 1
)

echo.
echo ==============================================
echo BUILD SUCCESSFUL
echo Output: dist\GhostBrowser\GhostBrowser.exe
echo Validated Chromium: %PACKAGED_CHROME%
echo Account credentials and profile data were not bundled.
echo ==============================================
exit /b 0
