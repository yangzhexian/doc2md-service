@echo off
REM -----------------------------------------------------------------
REM Document to Markdown Converter -- one-click start script (Windows)
REM -----------------------------------------------------------------
REM This script:
REM   1. Creates a Python virtual environment (if missing)
REM   2. Installs dependencies (if not already done)
REM   3. Starts the FastAPI service at http://127.0.0.1:8000
REM
REM Usage:
REM   start.bat                  default port 8000
REM   start.bat 9090             custom port
REM -----------------------------------------------------------------
setlocal enabledelayedexpansion

cd /d "%~dp0"

set VENV_DIR=%~dp0venv
set DEPS_FLAG=%VENV_DIR%\.deps_installed
set PORT=8000
if not "%~1"=="" set PORT=%~1

REM -- 1. Create virtual environment -----------------------------------------
if not exist "%VENV_DIR%\" (
    echo ==^> Creating Python virtual environment...
    python -m venv "%VENV_DIR%"
    echo     Done.
)

REM -- 2. Activate ------------------------------------------------------------
call "%VENV_DIR%\Scripts\activate.bat"

REM -- 3. Install dependencies ------------------------------------------------
if not exist "%DEPS_FLAG%" (
    echo ==^> Installing dependencies (this may take several minutes)...
    python -m pip install --upgrade pip --quiet
    pip install -r requirements.txt
    type nul > "%DEPS_FLAG%"
    echo     Done.
)

REM -- 4. Check models --------------------------------------------------------
if not exist "%~dp0mineru_models\" (
    echo.
    echo ============================================================
    echo   WARNING: mineru_models\ directory not found!
    echo   PDF conversion via MinerU will NOT work.
    echo   Download models first: mineru-models-download
    echo   Then copy them to: %~dp0mineru_models\
    echo ============================================================
    echo.
)

REM -- 5. Start service -------------------------------------------------------
echo.
echo ============================================================
echo   Document to Markdown Converter
echo   Service starting at http://127.0.0.1:%PORT%
echo   API docs: http://127.0.0.1:%PORT%/docs
echo   Press Ctrl+C to stop
echo ============================================================
echo.

uvicorn converter_service:app --host 127.0.0.1 --port %PORT%
