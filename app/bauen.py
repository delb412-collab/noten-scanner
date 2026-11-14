#!/usr/bin/env python3
"""Baut die Handy-App (Web-App) in den Ordner app/www – diesen Ordner auf GitHub Pages
(oder einen anderen Webspace mit https) hochladen.

    python app/bauen.py
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import tarfile
import urllib.request
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw

PROJEKT = Path(__file__).resolve().parents[1]
QUELLE = PROJEKT / "app" / "quelle"
ZIEL = PROJEKT / "app" / "www"
# Nur diese Module braucht die App (kein PC-Teil wie Stapelverarbeitung, Fenster, img2pdf)
MODULE = ["__init__.py", "verarbeitung.py", "seite.py", "linien.py", "pdf.py", "web.py"]
# Notensatz im Browser (Verovio, LGPL) – wird beim Bauen von npm geholt und zwischengespeichert
VEROVIO_VERSION = "6.4.0"
VEROVIO_DATEI = PROJEKT / "app" / "vendor" / f"verovio-{VEROVIO_VERSION}-toolkit-wasm.js"


def verovio_holen() -> Path:
    if not VEROVIO_DATEI.exists():
        url = f"https://registry.npmjs.org/verovio/-/verovio-{VEROVIO_VERSION}.tgz"
        print("Lade Verovio", VEROVIO_VERSION, "…")
        with urllib.request.urlopen(url, timeout=120) as antwort:
            daten = antwort.read()
        with tarfile.open(fileobj=io.BytesIO(daten), mode="r:gz") as archiv:
            inhalt = archiv.extractfile("package/dist/verovio-toolkit-wasm.js").read()
        VEROVIO_DATEI.parent.mkdir(parents=True, exist_ok=True)
        VEROVIO_DATEI.write_bytes(inhalt)
    return VEROVIO_DATEI


def version() -> str:
    text = (PROJEKT / "notenscanner" / "__init__.py").read_text(encoding="utf-8")
    basis = re.search(r'__version__ = "([^"]+)"', text).group(1)
    pruefsumme = hashlib.sha256()
    for datei in sorted(QUELLE.iterdir()) + [PROJEKT / "notenscanner" / m for m in MODULE]:
        if datei.is_file():
            pruefsumme.update(datei.read_bytes())
    return f"{basis}-{pruefsumme.hexdigest()[:8]}"


def symbol(groesse: int) -> Image.Image:
    """App-Symbol: Notenblatt mit Linien und Note auf blauem Grund."""
    s = groesse / 512
    bild = Image.new("RGB", (groesse, groesse), "#1d3557")
    malen = ImageDraw.Draw(bild)
    malen.rounded_rectangle([118 * s, 70 * s, 394 * s, 442 * s], radius=18 * s, fill="#ffffff")
    for i in range(5):
        y = (190 + i * 30) * s
        malen.line([150 * s, y, 362 * s, y], fill="#1d3557", width=max(1, round(6 * s)))
    malen.ellipse([196 * s, 286 * s, 252 * s, 326 * s], fill="#1d3557")
    malen.line([248 * s, 306 * s, 248 * s, 160 * s], fill="#1d3557", width=max(1, round(9 * s)))
    malen.polygon([(248 * s, 160 * s), (300 * s, 196 * s), (300 * s, 220 * s), (248 * s, 186 * s)], fill="#1d3557")
    return bild


def bauen() -> Path:
    v = version()
    if ZIEL.exists():
        shutil.rmtree(ZIEL)
    (ZIEL / "icons").mkdir(parents=True)
    for datei in QUELLE.iterdir():
        if datei.suffix in {".html", ".js", ".css", ".webmanifest"}:
            text = (datei.read_text(encoding="utf-8").replace("__VERSION__", v).replace("__VEROVIO__", VEROVIO_VERSION)
                    .replace("__SERVER__", os.environ.get("SERVER_ADRESSE", "")))
            (ZIEL / datei.name).write_text(text, encoding="utf-8")
    with zipfile.ZipFile(ZIEL / "notenscanner.zip", "w", zipfile.ZIP_DEFLATED) as archiv:
        for modul in MODULE:
            archiv.write(PROJEKT / "notenscanner" / modul, f"notenscanner/{modul}")
    gross = symbol(512)
    gross.save(ZIEL / "icons" / "icon-512.png")
    gross.resize((192, 192), Image.LANCZOS).save(ZIEL / "icons" / "icon-192.png")
    gross.resize((180, 180), Image.LANCZOS).save(ZIEL / "icons" / "apple-touch-icon.png")
    (ZIEL / "verovio").mkdir()
    shutil.copy(verovio_holen(), ZIEL / "verovio" / "verovio-toolkit-wasm.js")
    (ZIEL / ".nojekyll").write_text("")
    print(f"Web-App {v} gebaut: {ZIEL}")
    return ZIEL


if __name__ == "__main__":
    bauen()
