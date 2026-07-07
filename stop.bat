@echo off
REM -----------------------------------------------------------------
REM Stop the docs2md background service
REM -----------------------------------------------------------------
REM Usage:
REM   stop.bat                   stop service on default port 8000
REM   stop.bat 9090              stop service on custom port
REM -----------------------------------------------------------------
setlocal enabledelayedexpansion

cd /d "%~dp0"

set PORT=8000
if not "%~1"=="" set PORT=%~1

echo ==^> Looking for service on port %PORT%...

REM Find the PID listening on the given port using netstat
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%PORT% " ^| findstr LISTENING') do (
    set PID=%%a
)

if "%PID%"=="" (
    echo     No service found listening on port %PORT%.
    exit /b 1
)

echo     Found PID: %PID%
echo ==^> Stopping service...
taskkill /f /pid %PID% >nul 2>&1

if %ERRORLEVEL% equ 0 (
    echo     Service stopped successfully.
) else (
    echo     Failed to stop service. Try:
    echo     taskkill /f /pid %PID%
)
