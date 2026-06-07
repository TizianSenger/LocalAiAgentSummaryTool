@echo off
echo ============================================================
echo  StudyScript AI - Installation
echo ============================================================
echo.

:: ── Python 3.12 bevorzugt (beste Paketkompatibilitaet fuer marker-pdf etc.) ──
echo [0/4] Suche Python-Installation...

py -3.11 --version >nul 2>&1
if not errorlevel 1 (
    set PYTHON_CMD=py -3.11
    echo Gefunden: Python 3.11 (empfohlen)
    py -3.11 --version
    goto run_install
)

py -3.12 --version >nul 2>&1
if not errorlevel 1 (
    set PYTHON_CMD=py -3.12
    echo Gefunden: Python 3.12
    py -3.12 --version
    goto run_install
)

py -3.13 --version >nul 2>&1
if not errorlevel 1 (
    set PYTHON_CMD=py -3.13
    echo Gefunden: Python 3.13
    py -3.13 --version
    goto run_install
)

:: Fallback: irgendeine Python-Version
py --version >nul 2>&1
if not errorlevel 1 (
    set PYTHON_CMD=py
    echo WARNUNG: Python 3.12 nicht gefunden - verwende Standard-Python.
    echo Fuer beste Kompatibilitaet: https://www.python.org/downloads/release/python-3128/
    echo.
    py --version
    goto run_install
)

python3 --version >nul 2>&1
if not errorlevel 1 (
    set PYTHON_CMD=python3
    python3 --version
    goto run_install
)

echo FEHLER: Python nicht gefunden.
echo Bitte Python 3.12 installieren: https://www.python.org/downloads/release/python-3128/
pause
exit /b 1

:run_install

:: ── Python Backend ────────────────────────────────────────────────────────────
echo.
echo [1/4] Installiere Python-Abhaengigkeiten...
echo       (marker-pdf laedt beim ersten Start GPU-Modelle herunter ~2-4 GB)
echo.
%PYTHON_CMD% -m pip install -r backend\requirements.txt
if errorlevel 1 (
    echo.
    echo FEHLER: pip install fehlgeschlagen.
    echo Tipp: Python 3.12 installieren von https://www.python.org/downloads/release/python-3128/
    pause
    exit /b 1
)

:: ── Electron Frontend ─────────────────────────────────────────────────────────
echo.
echo [2/4] Installiere Electron (Node.js benoetigt)...
cd frontend
call npm install
if errorlevel 1 (
    echo FEHLER: npm install fehlgeschlagen.
    echo Node.js installieren: https://nodejs.org
    cd ..
    pause
    exit /b 1
)
cd ..

:: ── Datenverzeichnis ──────────────────────────────────────────────────────────
echo.
echo [3/4] Erstelle Datenverzeichnis...
if not exist "data" mkdir data

echo.
echo ============================================================
echo  Installation abgeschlossen!
echo  Starten mit: start.bat
echo ============================================================
echo.
pause
