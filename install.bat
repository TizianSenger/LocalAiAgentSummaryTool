@echo off
echo ============================================================
echo  StudyScript AI - Installation
echo ============================================================
echo.

:: ── Python Backend ──────────────────────────────────────────────────────────
echo [1/3] Installiere Python-Abhaengigkeiten...
echo       (marker-pdf wird GPU-Modelle herunterladen ~2-4 GB)
echo.

pip install -r backend\requirements.txt
if errorlevel 1 (
    echo FEHLER: pip install fehlgeschlagen. Stelle sicher, dass Python installiert ist.
    pause
    exit /b 1
)

echo.
echo [2/3] Installiere Electron (Node.js benoetigt)...
cd frontend
call npm install
if errorlevel 1 (
    echo FEHLER: npm install fehlgeschlagen. Stelle sicher, dass Node.js installiert ist.
    cd ..
    pause
    exit /b 1
)
cd ..

echo.
echo [3/3] Erstelle Datenverzeichnis...
if not exist "data" mkdir data

echo.
echo ============================================================
echo  Installation abgeschlossen!
echo.
echo  Starten mit:  start.bat
echo  Oder manuell:
echo    Terminal 1:  cd backend ^&^& python main.py
echo    Terminal 2:  cd frontend ^&^& npm start
echo ============================================================
echo.
pause
