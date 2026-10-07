@echo off
chcp 65001 >nul
rem Installa dwg2c4d (serve Python 3.10 o piu' recente da python.org, con "Add Python to PATH" spuntato)
rem Fai doppio clic su questo file, dalla cartella del progetto.
python --version >nul 2>nul
if errorlevel 1 (
  echo Python non trovato. Installalo da https://www.python.org/downloads/ e spunta "Add Python to PATH".
  pause
  exit /b 1
)
python -m pip install --upgrade "%~dp0"
if errorlevel 1 (
  echo.
  echo L'installazione non e' riuscita: copia il messaggio qui sopra.
  pause
  exit /b 1
)
echo.
echo Fatto. Trascina un file .dwg o .dxf su converti.bat.
pause
