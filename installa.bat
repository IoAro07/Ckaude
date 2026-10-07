@echo off
chcp 65001 >nul
rem Installa le librerie di dwg2c4d (serve Python 3.10 o piu' recente da python.org, con "Add Python to PATH" spuntato).
rem Fai doppio clic su questo file, dalla cartella del progetto. Si puo' rifare quando vuoi, anche dopo un aggiornamento.
python --version >nul 2>nul
if errorlevel 1 (
  echo Python non trovato. Installalo da https://www.python.org/downloads/ e spunta "Add Python to PATH".
  pause
  exit /b 1
)
cd /d "%~dp0"
rem Cartelle di costruzione lasciate da installazioni precedenti: se restano, pip puo' reinstallare il codice vecchio.
if exist build rmdir /s /q build
for /d %%D in (src\*.egg-info) do rmdir /s /q "%%D"
python -m pip install --upgrade ezdxf shapely numpy matplotlib
if errorlevel 1 (
  echo.
  echo L'installazione delle librerie non e' riuscita: copia il messaggio qui sopra.
  pause
  exit /b 1
)
python -m pip install --upgrade --force-reinstall --no-deps .
if errorlevel 1 (
  echo.
  echo L'installazione non e' riuscita: copia il messaggio qui sopra.
  pause
  exit /b 1
)
echo.
echo Fatto. Trascina un file .dwg o .dxf su converti.bat.
pause
