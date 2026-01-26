@echo off
setlocal enabledelayedexpansion

set ROOT_DIR=%~dp0
cd /d "%ROOT_DIR%"

where python >nul 2>nul
if errorlevel 1 (
  echo Python not found. Install Python 3 and ensure it is on PATH.
  exit /b 1
)

if "%1" NEQ "--no-install" (
  python -m pip install -r requirements.txt
)

set LLM_MAILER_SSL=adhoc
set PORT=7999

python -m llm_mailer.web_app
