#!/bin/sh
# Stellt den Inhalt des Hugging-Face-Space zusammen (wird von der GitHub-Action genutzt).
# Aufruf: sh server/space_zusammenstellen.sh <zielordner>
set -e
ZIEL="${1:-space}"
rm -rf "$ZIEL"
mkdir -p "$ZIEL/notenleser" "$ZIEL/notenscanner"
cp server/app.py server/erkennung.py server/ablage.py server/satz.py server/aufwaermen.png \
   server/requirements.txt server/Dockerfile server/README.md "$ZIEL/"
cp notenleser/notenleser.py notenleser/bildpruefung.py "$ZIEL/notenleser/"
cp notenscanner/__init__.py notenscanner/verarbeitung.py notenscanner/linien.py notenscanner/seite.py "$ZIEL/notenscanner/"
echo "Space-Inhalt in $ZIEL zusammengestellt"
