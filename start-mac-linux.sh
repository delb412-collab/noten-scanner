#!/bin/sh
# Startet den Noten-Scanner. Beim ersten Start wird alles Nötige installiert.
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
    echo "Ersteinrichtung – das dauert beim ersten Mal ein paar Minuten …"
    python3 -m venv .venv || { echo "Python 3 wurde nicht gefunden (https://www.python.org)"; exit 1; }
    .venv/bin/python -m pip install --upgrade pip
    .venv/bin/python -m pip install -r requirements.txt || { rm -rf .venv; echo "Installation fehlgeschlagen"; exit 1; }
fi
exec .venv/bin/python noten_scanner_gui.pyw
