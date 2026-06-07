@echo off
echo ============================================================
echo  StudyScript AI - Installation
echo ============================================================
echo.

:: ── Python-Version pruefen ───────────────────────────────────────────────────
echo [0/4] Pruefe Python-Version...
python --version
if errorlevel 1 (
    echo FEHLER: Python nicht gefunden. Bitte Python 3.11-3.13 installieren.
    pause
    exit /b 1
)

:: ── Pillow vorab installieren (verhindert Downgrade auf cp314-inkompatible Version) ──
:: pip's Dependency-Resolver kann Pillow auf eine Version zurueckstufen, die kein
:: Python-3.14-Binary hat. Durch vorheriges Installieren von >=12.0.0 bleibt es fest.
echo.
echo [1/4] Installiere Pillow (Python 3.14 kompatibel)...
pip install "Pillow>=12.0.0"
if errorlevel 1 (
    echo FEHLER: Pillow konnte nicht installiert werden.
    echo Versuche: pip install "Pillow>=12.0.0" manuell auszufuehren.
    pause
    exit /b 1
)

:: ── Python Backend ──────────────────────────────────────────────────────────
echo.
echo [2/4] Installiere Python-Abhaengigkeiten...
echo       (marker-pdf laedt beim ersten Start GPU-Modelle herunter ~2-4 GB)
echo.

pip install -r backend\requirements.txt
if errorlevel 1 (
    echo.
    echo FEHLER: pip install fehlgeschlagen.
    echo.
    echo Moegliche Ursachen:
    echo   - Python-Version zu neu (3.14+ hat weniger vorgefertigte Binaries)
    echo   - Empfohlen: Python 3.11 oder 3.12 fuer beste Kompatibilitaet
    echo.
    echo Versuche manuell:
    echo   pip install "Pillow>=12.0.0"
    echo   pip install -r backend\requirements.txt
    pause
    exit /b 1
)

:: ── Electron Frontend ────────────────────────────────────────────────────────
echo.
echo [3/4] Installiere Electron (Node.js benoetigt)...
cd frontend
call npm install
if errorlevel 1 (
    echo FEHLER: npm install fehlgeschlagen.
    echo Stelle sicher, dass Node.js installiert ist: https://nodejs.org
    cd ..
    pause
    exit /b 1
)
cd ..

:: ── Datenverzeichnis ─────────────────────────────────────────────────────────
echo.
echo [4/4] Erstelle Datenverzeichnis...
if not exist "data" mkdir data

echo.
echo ============================================================
echo  Installation abgeschlossen!
echo.
echo  Starten mit:  start.bat
echo  Oder manuell:
echo    cd frontend
echo    npm start
echo ============================================================
echo.
pause
