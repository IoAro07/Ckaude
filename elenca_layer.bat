@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set "PYTHONPATH=%~dp0src"
rem Trascina un file .dwg o .dxf su questo file: mostra i layer, come vengono letti e le proposte
if "%~1"=="" (
  echo Trascina un file .dwg o .dxf su elenca_layer.bat
  pause
  exit /b 1
)
python -m dwg2c4d "%~1" --elenca-layer
pause
