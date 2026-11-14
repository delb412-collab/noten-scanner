@echo off
rem Startet den Noten-Scanner. Beim ersten Start wird alles Noetige installiert.
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo Ersteinrichtung - das dauert beim ersten Mal ein paar Minuten ...
    py -3 -m venv .venv 2>nul || python -m venv .venv
    if not exist ".venv\Scripts\python.exe" (
        echo.
        echo Python wurde nicht gefunden. Bitte Python von https://www.python.org installieren
        echo und dabei den Haken bei "Add python.exe to PATH" setzen.
        pause
        exit /b 1
    )
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo Die Installation ist fehlgeschlagen. Bitte Internetverbindung pruefen.
        rmdir /s /q .venv
        pause
        exit /b 1
    )
)
start "" ".venv\Scripts\pythonw.exe" noten_scanner_gui.pyw
