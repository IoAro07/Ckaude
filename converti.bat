@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
rem Trascina un file .dwg o .dxf su questo file: i risultati (OBJ, tabella, immagini, report)
rem vengono scritti accanto al disegno. I layer che il nome non spiega vengono letti con le proposte
rem (--accetta-proposte): il programma dice quali ha usato. Per le opzioni usa il prompt dei comandi: dwg2c4d --help
if "%~1"=="" (
  echo Trascina un file .dwg o .dxf su converti.bat
  pause
  exit /b 1
)
python -m dwg2c4d "%~1" --json-c4d --accetta-proposte
echo.
echo Controlla le immagini *_controllo_pianta.png e il file *_report.txt accanto al disegno.
pause
