@echo off
REM Download or update local MinerU 4.x models.
REM Usage:
REM   update.bat                          standard tier (small + VLM), auto source
REM   update.bat huggingface              force HuggingFace
REM   update.bat modelscope               force ModelScope
REM   update.bat auto --tier basic        small models only
REM   update.bat auto --tier standard     small + VLM (default)
REM
REM tier: basic | standard | advanced  (default: standard)

setlocal enabledelayedexpansion
cd /d "%~dp0"

if exist "venv\Scripts\python.exe" (
    set PYTHON=venv\Scripts\python.exe
) else if exist "venv\bin\python" (
    set PYTHON=venv\bin\python
) else (
    set PYTHON=python
)

set SOURCE=%~1
if "%SOURCE%"=="" set SOURCE=auto

REM Forward the remaining arguments (notably --tier).
set ARGS=
shift
:collect
if "%~1"=="" goto run
set ARGS=!ARGS! %1
shift
goto collect

:run
"%PYTHON%" "%~dp0scripts\update.py" %SOURCE% %ARGS%
endlocal
