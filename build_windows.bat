@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Build MediaDupFinder

where py >nul 2>nul
if errorlevel 1 goto :no_python

py -3.8 -c "import sys; assert sys.version_info[:2] == (3, 8)" >nul 2>nul
if errorlevel 1 goto :no_python38

set "MDF_BUILD_ENV=%CD%\.venv-build"
if not exist "%MDF_BUILD_ENV%\Scripts\python.exe" (
  echo [1/5] Creating isolated Python 3.8 build environment...
  py -3.8 -m venv "%MDF_BUILD_ENV%"
  if errorlevel 1 goto :failed
)

call "%MDF_BUILD_ENV%\Scripts\activate.bat"
echo [2/5] Installing pinned build tools...
python -m pip install --upgrade "pip<25"
if errorlevel 1 goto :failed
python -m pip install -r requirements-build.txt
if errorlevel 1 goto :failed

echo [3/5] Running automated tests...
set "PYTHONPATH=%CD%\src"
python -m unittest discover -s tests -v
if errorlevel 1 goto :failed

echo [4/5] Building Windows executable...
python -m PyInstaller --clean --noconfirm MediaDupFinder.spec
if errorlevel 1 goto :failed

echo Verifying packaged executable startup...
start "" /wait "%CD%\dist\MediaDupFinder.exe" --startup-check
if errorlevel 1 goto :failed

for /f %%A in ('python -c "import struct; print('x64' if struct.calcsize('P') == 8 else 'x86')"') do set "MDF_ARCH=%%A"
echo [5/5] Creating release ZIP...
python scripts\package_release.py --arch %MDF_ARCH%
if errorlevel 1 goto :failed

echo.
echo Build completed successfully.
echo EXE: %CD%\dist\MediaDupFinder.exe
echo ZIP: %CD%\release\MediaDupFinder-v1.7.0-Windows-%MDF_ARCH%.zip
pause
exit /b 0

:no_python
echo Python Launcher was not found. Install Python 3.8.10 first.
pause
exit /b 1

:no_python38
echo Python 3.8 was not found. Win7-compatible builds require Python 3.8.x.
pause
exit /b 1

:failed
echo.
echo Build failed. Review the error shown above.
pause
exit /b 1
