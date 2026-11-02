@echo off
setlocal
cd /d "%~dp0"
where uv >nul 2>nul
if %errorlevel% equ 0 (
  uv sync --frozen --no-dev
  if errorlevel 1 exit /b 1
  uv run --no-sync python -m postscope %*
  exit /b
)
if not exist .venv\Scripts\python.exe (
  py -3 -m venv .venv
  if errorlevel 1 exit /b 1
)
.venv\Scripts\python.exe -m pip install --require-hashes -r requirements.txt
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe -m postscope %*
