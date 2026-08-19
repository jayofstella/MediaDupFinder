@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "MDF_PYTHONPATH=%CD%\src"
set "PYTHONPATH=%MDF_PYTHONPATH%"

where py >nul 2>nul
if %errorlevel% equ 0 (
  py -3 -m media_dup_finder
) else (
  python -m media_dup_finder
)

if errorlevel 1 (
  echo.
  echo Program failed to start. Install Python 3.8 or newer with Tcl/Tk support.
  pause
)

