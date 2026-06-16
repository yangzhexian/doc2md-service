@echo off
REM ─────────────────────────────────────────────────────────────────────────────
REM Install docs2md to start automatically on Windows login
REM ─────────────────────────────────────────────────────────────────────────────
REM This script:
REM   1. Creates a VBS launcher that runs start.bat silently
REM   2. Places a shortcut in the Windows Startup folder
REM
REM Usage:
REM   scripts\install-autostart.bat                  default port 8000
REM   scripts\install-autostart.bat 9090             custom port
REM ─────────────────────────────────────────────────────────────────────────────
setlocal enabledelayedexpansion

set SCRIPT_DIR=%~dp0
set PROJECT_DIR=%SCRIPT_DIR%..
set PORT=8000
if not "%~1"=="" set PORT=%~1

echo ==^> Installing docs2md autostart service...
echo     Project dir : %PROJECT_DIR%
echo     Port        : %PORT%

REM ── 1. Check prerequisites ────────────────────────────────────────────────
if not exist "%PROJECT_DIR%\converter_service.py" (
    echo ERROR: converter_service.py not found in %PROJECT_DIR%
    exit /b 1
)

REM ── 2. Create VBS launcher with correct paths ─────────────────────────────
echo ==^> Creating VBS launcher...
set VBS_PATH=%PROJECT_DIR%\scripts\docs2md-launcher.vbs

REM ── 3. Get the Startup folder path ────────────────────────────────────────
echo ==^> Creating shortcut in Startup folder...
set STARTUP_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
set SHORTCUT_PATH=%STARTUP_DIR%\docs2md.lnk

REM ── 4. Create shortcut using PowerShell ───────────────────────────────────
powershell -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%SHORTCUT_PATH%'); $s.TargetPath = 'wscript.exe'; $s.Arguments = '\"%VBS_PATH%\"'; $s.WorkingDirectory = '%PROJECT_DIR%'; $s.WindowStyle = 7; $s.Description = 'Document to Markdown Converter'; $s.Save()"

if %ERRORLEVEL% neq 0 (
    echo ERROR: Failed to create shortcut.
    exit /b 1
)

REM ── 5. Start the service now ──────────────────────────────────────────────
echo ==^> Starting service...
start "" /B wscript.exe "%VBS_PATH%"

REM ── 6. Wait and verify ────────────────────────────────────────────────────
echo ==^> Waiting for service to start...
timeout /t 5 /nobreak >nul

echo.
echo ╔════════════════════════════════════════════════════════════════╗
echo ║  docs2md autostart installed!                                 ║
echo ║                                                               ║
echo ║  Service will start automatically on login.                   ║
echo ║  API:      http://127.0.0.1:%PORT%                                  ║
echo ║  API docs: http://127.0.0.1:%PORT%/docs                             ║
echo ║                                                               ║
echo ║  To remove autostart, delete:                                  ║
echo ║    %SHORTCUT_PATH%                     ║
echo ╚════════════════════════════════════════════════════════════════╝
echo.

echo ==^> Testing health endpoint...
curl -s http://127.0.0.1:%PORT%/health 2>nul || echo     (Service may still be starting — check in a few seconds)
