@echo off
REM Download or update local MinerU models.
REM Usage:
REM   update.bat                     pipeline models, auto-select source
REM   update.bat huggingface         force HuggingFace
REM   update.bat modelscope          force ModelScope
REM   update.bat auto all            pipeline + VLM models (hybrid backend)
REM   update.bat auto vlm            VLM model only
REM
REM model-type: pipeline (default) | vlm | all

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
set MODEL_TYPE=%~2
if "%MODEL_TYPE%"=="" set MODEL_TYPE=pipeline

"%PYTHON%" "%~dp0scripts\update.py" %SOURCE% --model-type %MODEL_TYPE%
endlocal
