"""Tests für die Schnittstelle der Handy-App (notenscanner/web.py) und den PDF-Schreiber.
Sollten zusätzlich mit den Versionen laufen, die im Browser (Pyodide 0.27.7) stecken:
numpy 2.0.2, opencv-python 4.10.0.84, Python 3.12."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from notenscanner.pdf import pdf_aus_seiten  # noqa: E402
from notenscanner.web import pdf_erstellen, seite_bereinigen, seite_entpacken  # noqa: E402
from tests import testdaten as td  # noqa: E402

pdfium = pytest.importorskip("pypdfium2")


def _rgba(bgr: np.ndarray) -> tuple[bytes, int, int]:
    rgba = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGBA)
    return rgba.tobytes(), rgba.shape[1], rgba.shape[0]


@pytest.fixture(scope="module")
def foto():
    seite, _ = td.einfaches_notenblatt(np.random.default_rng(1))
    bild, _ = td.handyfoto(seite, np.random.default_rng(2), winkel=3.0)
    # wie in der App: vor der Übergabe auf höchstens 3200 px verkleinert
    f = 3200 / max(bild.shape[:2])
    return seite, cv2.resize(bild, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)


def _bilddaten(pdf: bytes, nummer: int = 0) -> np.ndarray:
    """Liest das nummer-te Seitenbild direkt aus dem PDF (ohne Rendern) – prüft Verlustfreiheit."""
    import re
    import zlib
    treffer = list(re.finditer(rb"/Width (\d+) /Height (\d+) /ColorSpace /DeviceGray /BitsPerComponent (\d+)"
                               rb" /Filter /FlateDecode /Length (\d+) >>\nstream\n", pdf))[nummer]
    w, h, bits, laenge = (int(g) for g in treffer.groups())
    roh = np.frombuffer(zlib.decompress(pdf[treffer.end():treffer.end() + laenge]), np.uint8)
    if bits == 1:
        return np.unpackbits(roh.reshape(h, -1), axis=1)[:, :w] * 255
    hoch, tief = roh.reshape(h, -1) >> 4, roh.reshape(h, -1) & 15
    return (np.stack([hoch, tief], axis=2).reshape(h, -1)[:, :w] * 17).astype(np.uint8)


def _seiten_lesen(pdf: bytes):
    dokument = pdfium.PdfDocument(pdf)
    groessen = [(round(s.get_width() / 72 * 25.4), round(s.get_height() / 72 * 25.4)) for s in dokument]
    bilder = [np.asarray(s.render(scale=300 / 72, grayscale=True).to_pil().convert("L")) for s in dokument]
    return groessen, bilder


def test_seite_bereinigen_und_pdf(foto):
    vorlage, bild = foto
    gepackt, vorschau, vb, vh, infos = seite_bereinigen(*_rgba(bild), json.dumps({"modus": "glatt"}))
    infos = json.loads(infos)
    assert infos["blatt_gefunden"] and infos["notensysteme"] == 9 and infos["dpi"] == 300
    seite, dpi = seite_entpacken(gepackt)
    assert seite.shape == (3508, 2480) and dpi == 300
    assert td.bewerten(seite, vorlage)["f1"] > 0.97
    assert (vb, vh) == (700, 990) and len(vorschau) == vb * vh
    assert len(gepackt) < 600_000

    pdf = pdf_erstellen([gepackt, gepackt], "Böhmischer Traum – 1. Trompete")
    groessen, bilder = _seiten_lesen(pdf)
    assert groessen == [(210, 297), (210, 297)]
    assert np.array_equal(_bilddaten(pdf, 1), seite)               # verlustfrei gespeichert
    gerendert = bilder[0][:3508, :2480].astype(int)
    assert np.mean(np.abs(gerendert - seite.astype(int))) < 6     # und lesbar dargestellt
    assert len(pdf) < 2 * 400_000                                  # 4-Bit + Flate: klein genug


def test_reines_schwarz_weiss_wird_1_bit_pdf(foto):
    _, bild = foto
    gepackt, _, _, _, _ = seite_bereinigen(*_rgba(bild), json.dumps({"modus": "hart", "dpi": 200}))
    seite, dpi = seite_entpacken(gepackt)
    assert seite.shape == (2339, 1654) and dpi == 200 and set(np.unique(seite)) <= {0, 255}
    pdf = pdf_erstellen([gepackt], "Test")
    assert b"/BitsPerComponent 1" in pdf and len(pdf) < 150_000
    groessen, bilder = _seiten_lesen(pdf)
    assert groessen == [(210, 297)]
    assert np.array_equal(_bilddaten(pdf), seite)
    gerendert = cv2.resize(bilder[0], (1654, 2339), interpolation=cv2.INTER_AREA)
    assert np.mean(np.abs(gerendert.astype(int) - seite.astype(int))) < 8


def test_pdf_ungerade_breite_und_querformat():
    rng = np.random.default_rng(3)
    quer = (rng.integers(0, 16, (1001, 1415)) * 17).astype(np.uint8)   # ungerade Breite, 16 Stufen
    pdf = pdf_aus_seiten([quer], dpi=120, titel="Quer")
    groessen, bilder = _seiten_lesen(pdf)
    assert groessen == [(300, 212)]
    assert np.array_equal(_bilddaten(pdf), quer)


class _JsProxy:
    """Nachbildung dessen, was Pyodide für ein Uint8Array aus dem Browser übergibt."""
    def __init__(self, daten: bytes):
        self._daten = daten

    def to_py(self):
        return memoryview(bytearray(self._daten))


def test_daten_aus_dem_browser(foto):
    """Die Funktionen müssen auch mit Pyodide-Proxies (statt bytes) umgehen können."""
    _, bild = foto
    rgba, w, h = _rgba(bild)
    gepackt, *_ = seite_bereinigen(_JsProxy(rgba), w, h, json.dumps({"dpi": 200}))
    pdf = pdf_erstellen([_JsProxy(gepackt), _JsProxy(gepackt)], "Test")
    assert pdf.startswith(b"%PDF") and pdf.count(b"/Type /Page ") == 2


def test_seite_als_png_ist_gueltig_und_verlustfrei(foto):
    import io
    from PIL import Image
    from notenscanner.web import seite_als_png
    gepackt, *_ = seite_bereinigen(*_rgba(foto[1]), "{}")
    png = seite_als_png(gepackt)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    bild = Image.open(io.BytesIO(png))
    original, dpi = seite_entpacken(gepackt)
    assert bild.mode == "L" and bild.size == (original.shape[1], original.shape[0])
    assert round(bild.info["dpi"][0]) == dpi
    assert (np.asarray(bild) == original).all()
