@echo off
echo ============================================================
echo  StudyScript AI - Installation
echo ============================================================
echo.

:: ── Python-Befehl ermitteln ──────────────────────────────────────────────────
:: Windows hat haeufig keinen "python"-Alias im PATH, dafuer aber den "py"-Launcher
:: oder pip ist direkt verfuegbar. Wir probieren alle gaengigen Varianten.
echo [0/4] Suche Python-Installation...

set PYTHON_CMD=

:: 1. Versuch: py-Launcher (Standard bei python.org-Installation auf Windows)
py --version >nul 2>&1
if not errorlevel 1 (
    set PYTHON_CMD=py
    echo Gefunden: py-Launcher
    py --version
    goto :found_python
)

:: 2. Versuch: python3
python3 --version >nul 2>&1
if not errorlevel 1 (
    set PYTHON_CMD=python3
    echo Gefunden: python3
    python3 --version
    goto :found_python
)

:: 3. Versuch: python
python --version >nul 2>&1
if not errorlevel 1 (
    set PYTHON_CMD=python
    echo Gefunden: python
    python --version
    goto :found_python
)

echo FEHLER: Python nicht gefunden.
echo Bitte Python von https://python.org herunterladen und installieren.
echo Empfohlen: Python 3.11 oder 3.12
pause
exit /b 1

:found_python

:: pip-Befehl ableiten (py -m pip ist zuverlaessiger als direktes "pip")
set PIP_CMD=%PYTHON_CMD% -m pip

:: ── Pillow vorab installieren ─────────────────────────────────────────────────
:: pip's Dependency-Resolver stuft Pillow sonst auf 10.x zurueck, das kein
:: Python-3.14-Binary hat. Pillow>=12.0.0 hat vorgefertigte cp314-Wheels.
echo.
echo [1/4] Installiere Pillow (Python 3.14 kompatibel)...
%PIP_CMD% install "Pillow>=12.0.0"
if errorlevel 1 (
    echo FEHLER: Pillow konnte nicht installiert werden.
    pause
    exit /b 1
)

:: ── Python Backend ────────────────────────────────────────────────────────────
echo.
echo [2/4] Installiere Python-Abhaengigkeiten...
echo       (marker-pdf laedt beim ersten Start GPU-Modelle herunter ~2-4 GB)
echo.
%PIP_CMD% install -r backend\requirements.txt
if errorlevel 1 (
    echo.
    echo FEHLER: pip install fehlgeschlagen.
    echo Tipp: Python 3.11 oder 3.12 hat die beste Paket-Kompatibilitaet.
    pause
    exit /b 1
)

:: ── Electron Frontend ─────────────────────────────────────────────────────────
echo.
echo [3/4] Installiere Electron (Node.js benoetigt)...
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
echo [4/4] Erstelle Datenverzeichnis...
if not exist "data" mkdir data

echo.
echo ============================================================
echo  Installation abgeschlossen!
echo  Starten mit: start.bat
echo ============================================================
echo.
pause
