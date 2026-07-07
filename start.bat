@echo off
REM -----------------------------------------------------------------
REM Document to Markdown Converter -- one-click start script (Windows)
REM -----------------------------------------------------------------
REM Launches the service as a background process with no CMD window.
REM Uses pythonw.exe so absolutely no console window appears.
REM
REM Usage:
REM   start.bat                  default port 8000
REM   start.bat 9090             custom port
REM -----------------------------------------------------------------

setlocal enabledelayedexpansion
cd /d "%~dp0"

set PORT=8000
if not "%~1"=="" set PORT=%~1

REM pythonw.exe is a GUI executable (subsystem:windows).  CMD launches
REM it and returns immediately.  The service runs in the background
REM with no console window at all.  Close this CMD window safely.
start "" pythonw.exe "%~dp0src\launcher.py" %PORT%
