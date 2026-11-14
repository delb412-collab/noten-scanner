"""Schnittstelle für die Handy-App. Läuft im Browser (Pyodide) in einem Web Worker.

Die App übergibt jedes Foto als RGBA-Pixel (vom Browser bereits richtig gedreht) und
bekommt die bereinigte Seite verlustfrei gepackt (zlib) zurück, dazu eine kleine
Graustufen-Vorschau. Für das PDF werden die gepackten Seiten nacheinander wieder
ausgepackt – so liegt im (knappen) Handy-Speicher nie mehr als eine Seite offen.

Bewusst ohne PNG/JPEG-Funktionen von OpenCV: Bilder dekodiert und kodiert der Browser,
gepackt wird mit zlib aus der Python-Standardbibliothek.
"""
from __future__ import annotations

import dataclasses
import gc
import json
import struct
import zlib

import cv2
import numpy as np

from .pdf import pdf_aus_seiten
from .verarbeitung import Einstellungen, verarbeiten

VORSCHAU_BREITE = 700
KENNUNG = b"NSS1"          # gepackte Seite: Kennung, Breite, Höhe, dpi, zlib-Pixel


def seite_bereinigen(rgba, breite: int, hoehe: int, einstellungen_json: str = "{}"):
    """Bereinigt ein Foto.

    Gibt (gepackte Seite, Vorschau-Pixel, Vorschau-Breite, Vorschau-Höhe, Infos als JSON)
    zurück. Die Vorschau ist ein Graustufenbild mit 700 Pixel Breite."""
    bild = np.frombuffer(_als_puffer(rgba), np.uint8).reshape(int(hoehe), int(breite), 4)
    bgr = cv2.cvtColor(bild, cv2.COLOR_RGBA2BGR)
    del bild
    # Nur bekannte Felder: alte, auf dem Handy gespeicherte Einstellungen dürfen nicht stören
    erlaubt = {f.name for f in dataclasses.fields(Einstellungen)}
    einstellungen = Einstellungen(**{k: v for k, v in json.loads(einstellungen_json).items() if k in erlaubt})
    ergebnis = verarbeiten(bgr, einstellungen)
    del bgr
    seite = ergebnis.bild
    h, w = seite.shape
    gepackt = KENNUNG + struct.pack("<III", w, h, einstellungen.dpi) + zlib.compress(seite.tobytes(), 6)
    vh = int(round(h * VORSCHAU_BREITE / w))
    vorschau = cv2.resize(seite, (VORSCHAU_BREITE, vh), interpolation=cv2.INTER_AREA)
    infos = dict(ergebnis.infos, dpi=einstellungen.dpi)
    del ergebnis, seite
    gc.collect()
    return gepackt, vorschau.tobytes(), VORSCHAU_BREITE, vh, json.dumps(infos, default=float)


def seite_entpacken(daten) -> tuple[np.ndarray, int]:
    """Gepackte Seite -> (Graustufenbild, dpi)."""
    daten = bytes(_als_puffer(daten))
    if daten[:4] != KENNUNG:
        raise ValueError("Unbekanntes Seitenformat")
    w, h, dpi = struct.unpack("<III", daten[4:16])
    return np.frombuffer(zlib.decompress(daten[16:]), np.uint8).reshape(h, w), dpi


def pdf_erstellen(seiten, titel: str = "Noten") -> bytes:
    """Fügt die gepackten Seiten (in dieser Reihenfolge) zu einem PDF zusammen."""
    pdf = pdf_aus_seiten((seite_entpacken(s) for s in seiten), titel=titel)
    gc.collect()
    return pdf


def _als_puffer(daten):
    """Daten aus dem Browser (Pyodide-JsProxy eines Uint8Array) in einen Python-Puffer."""
    if hasattr(daten, "to_py"):
        return daten.to_py()
    return daten


def seite_als_png(gepackt) -> bytes:
    """Bereinigte Seite (gepacktes Format der App) als PNG (8 Bit Graustufen, mit dpi-Angabe) –
    so wird sie an den Server für die Notenerkennung geschickt."""
    bild, dpi = seite_entpacken(gepackt)
    return png_kodieren(bild, dpi)


def png_kodieren(bild, dpi: int = 300) -> bytes:
    """Minimaler PNG-Schreiber für Graustufenbilder (ohne Pillow, läuft auch in Pyodide)."""
    import numpy as np
    bild = np.ascontiguousarray(bild, dtype=np.uint8)
    h, w = bild.shape
    roh = np.zeros((h, w + 1), dtype=np.uint8)            # Filterbyte 0 (keiner) je Zeile
    roh[:, 1:] = bild

    def block(art: bytes, daten: bytes) -> bytes:
        return struct.pack(">I", len(daten)) + art + daten + struct.pack(">I", zlib.crc32(art + daten) & 0xFFFFFFFF)

    ppm = int(round(dpi / 0.0254))
    return (b"\x89PNG\r\n\x1a\n"
            + block(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
            + block(b"pHYs", struct.pack(">IIB", ppm, ppm, 1))
            + block(b"IDAT", zlib.compress(roh.tobytes(), 6))
            + block(b"IEND", b""))

