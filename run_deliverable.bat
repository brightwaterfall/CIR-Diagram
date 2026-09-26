@echo off
REM Build schematic + result.zip for the client project layout:
REM   <project>\final_ptl_output.cir
REM   <project>\spice-cir-diagram\   (this folder)
REM   <project>\result.zip           (created)

cd /d "%~dp0"

where py >nul 2>&1 && set PY=py || set PY=python

%PY% -m pip install -q -r requirements.txt
if errorlevel 1 (
  echo pip install failed. Install Python 3.10+ and retry.
  pause
  exit /b 1
)

%PY% -m cir_diagram.cli "..\74181_optimized_ptl.cir" -o "output\final_ptl_full.svg" --mode single --max-per-row 80
if errorlevel 1 (
  echo Build failed.
  pause
  exit /b 1
)

echo.
echo Success. Deliverable: "%~dp0..\result.zip"
pause
