@echo off
REM Build schematic from CIR.cir into this working directory (flat layout).
REM No zip. No output/ folder.
REM
REM   CIR.cir
REM   cir_diagram\
REM   CIR_full.svg   (created here)

cd /d "%~dp0"

where py >nul 2>&1 && set PY=py || set PY=python

%PY% -m pip install -q -r requirements.txt
if errorlevel 1 (
  echo pip install failed. Install Python 3.10+ and retry.
  pause
  exit /b 1
)

%PY% -m cir_diagram.cli "CIR.cir" -o "CIR_full.svg" --mode single --max-per-row 80
if errorlevel 1 (
  echo Build failed.
  pause
  exit /b 1
)

echo.
echo Success. Deliverable: "%~dp0CIR_full.svg"
pause
