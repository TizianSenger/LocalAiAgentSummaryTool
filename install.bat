@echo off
echo ============================================================
echo  StudyScript AI - Installation
echo ============================================================
echo.

:: ── Python-Befehl ermitteln ──────────────────────────────────────────────────
:: Windows hat haeufig keinen "python"-Alias (Microsoft Store Stub),
:: dafuer aber den "py"-Launcher. Wir probieren: py -> python3 -> python
echo [0/4] Suche Python-Installation...

py --version >nul 2>&1
if not errorlevel 1 (
    set PYTHON_CMD=py
    goto run_install
)

python3 --version >nul 2>&1
if not errorlevel 1 (
    set PYTHON_CMD=python3
    goto run_install
)

python --version >nul 2>&1
if not errorlevel 1 (
    set PYTHON_CMD=python
    goto run_install
)

echo FEHLER: Python nicht gefunden.
echo Bitte Python von https://python.org herunterladen und installieren.
pause
exit /b 1

:run_install
echo Gefunden: %PYTHON_CMD%
%PYTHON_CMD% --version

:: ── Pillow vorab installieren ─────────────────────────────────────────────────
:: pip's Dependency-Resolver stuft Pillow auf 10.x zurueck, das kein
:: Python-3.14-Binary hat. Vorab-Installation von >=12.0.0 verhindert das.
echo.
echo [1/4] Installiere Pillow (Python 3.14 kompatibel)...
%PYTHON_CMD% -m pip install "Pillow>=12.0.0"
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
%PYTHON_CMD% -m pip install -r backend\requirements.txt
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
