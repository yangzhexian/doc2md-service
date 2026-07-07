@echo off
REM Download or update local MinerU pipeline models.
REM Usage:
REM   update.bat                auto-select source
REM   update.bat huggingface    force HuggingFace
REM   update.bat modelscope     force ModelScope

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

"%PYTHON%" "%~dp0scripts\update.py" %SOURCE%
endlocal
