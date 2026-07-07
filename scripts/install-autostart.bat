@echo off
REM ----------------------------------------------------------------------------
REM Install docs2md to start automatically on Windows login
REM ----------------------------------------------------------------------------
REM Usage:
REM   scripts\install-autostart.bat                  default port 8000
REM   scripts\install-autostart.bat 9090             custom port
REM ----------------------------------------------------------------------------
setlocal enabledelayedexpansion

set SCRIPT_DIR=%~dp0
set PROJECT_DIR=%SCRIPT_DIR%..
set PORT=8000
if not "%~1"=="" set PORT=%~1
for %%R in ("%PROJECT_DIR%") do set REAL_DIR=%%~fR

set STARTUP_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
set STARTUP_BAT=%STARTUP_DIR%\docs2md.bat

echo ==^> Installing docs2md autostart service...
echo     Project dir : %REAL_DIR%
echo     Port        : %PORT%

REM -- 1. Check prerequisites ------------------------------------------------
if not exist "%REAL_DIR%\src\converter_service.py" (
    echo ERROR: converter_service.py not found in %REAL_DIR%\src
    exit /b 1
)

REM -- 2. Create startup batch file in the Startup folder --------------------
echo ==^> Creating startup file...
(
echo @echo off
echo wscript.exe "%REAL_DIR%\start.vbs" %PORT%
) > "%STARTUP_BAT%"

REM -- 3. Start the service now ----------------------------------------------
echo ==^> Starting service...
start "" wscript.exe "%REAL_DIR%\start.vbs" %PORT%

REM -- 4. Wait and verify ----------------------------------------------------
echo ==^> Waiting for service to start...
ping -n 6 127.0.0.1 >nul

echo.
echo ================================================================
echo   docs2md autostart installed!
echo.
echo   Service will start automatically on login.
echo   API:      http://127.0.0.1:%PORT%
echo   API docs: http://127.0.0.1:%PORT%/docs
echo.
echo   Startup file:
echo     %STARTUP_BAT%
echo.
echo   To remove autostart, delete the file above.
echo ================================================================
echo.

echo ==^> Testing health endpoint...
curl -s http://127.0.0.1:%PORT%/health 2>nul || echo     (Service may still be starting - check in a few seconds)
