"""Schlanker PDF-Schreiber für bereinigte Notenseiten – ohne Zusatzbibliotheken.

Wird von der Handy-App benutzt (im Browser stehen img2pdf/pikepdf nicht zur Verfügung).
Reine Schwarz/Weiß-Seiten werden als 1-Bit-Bild gespeichert, alle anderen als 4-Bit-
Graustufenbild (16 Stufen – mehr erzeugt die Bereinigung ohnehin nicht). Beides wird
verlustfrei mit Flate (zlib) komprimiert.
"""
from __future__ import annotations

import io
import zlib
from collections.abc import Iterable

import numpy as np


def pdf_aus_seiten(seiten: Iterable, dpi: int = 300, titel: str = "Noten") -> bytes:
    """Erstellt ein PDF aus Graustufenbildern (uint8, 0 = schwarz, 255 = weiß). Jede Seite
    ist ein Bild oder ein Paar (Bild, dpi). Die Seitengröße ergibt sich aus Bildgröße und
    dpi (A4 mit 300 dpi = 2480 x 3508)."""
    objekte: list[bytes] = []          # Objekt i hat die Nummer i + 1

    def neues_objekt(inhalt: bytes = b"") -> int:
        objekte.append(inhalt)
        return len(objekte)

    katalog = neues_objekt()
    seitenbaum = neues_objekt()
    info = neues_objekt(b"<< /Title " + _pdf_text(titel) + b" /Producer (Noten-Scanner) >>")
    seiten_nummern = []
    for seite in seiten:
        bild, seiten_dpi = seite if isinstance(seite, tuple) else (seite, dpi)
        h, w = bild.shape
        breite_pt, hoehe_pt = w * 72 / seiten_dpi, h * 72 / seiten_dpi
        bits, daten = _bild_kodieren(bild)
        bild_nr = neues_objekt(
            f"<< /Type /XObject /Subtype /Image /Width {w} /Height {h} /ColorSpace /DeviceGray "
            f"/BitsPerComponent {bits} /Filter /FlateDecode /Length {len(daten)} >>\nstream\n".encode()
            + daten + b"\nendstream")
        inhalt = f"q {breite_pt:.3f} 0 0 {hoehe_pt:.3f} 0 0 cm /Bild Do Q".encode()
        inhalt_nr = neues_objekt(f"<< /Length {len(inhalt)} >>\nstream\n".encode() + inhalt + b"\nendstream")
        seiten_nummern.append(neues_objekt(
            f"<< /Type /Page /Parent {seitenbaum} 0 R /MediaBox [0 0 {breite_pt:.3f} {hoehe_pt:.3f}] "
            f"/Resources << /XObject << /Bild {bild_nr} 0 R >> >> /Contents {inhalt_nr} 0 R >>".encode()))
    objekte[katalog - 1] = f"<< /Type /Catalog /Pages {seitenbaum} 0 R >>".encode()
    kinder = " ".join(f"{n} 0 R" for n in seiten_nummern)
    objekte[seitenbaum - 1] = f"<< /Type /Pages /Kids [{kinder}] /Count {len(seiten_nummern)} >>".encode()

    ausgabe = io.BytesIO()
    ausgabe.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    positionen = []
    for nummer, inhalt in enumerate(objekte, start=1):
        positionen.append(ausgabe.tell())
        ausgabe.write(f"{nummer} 0 obj\n".encode() + inhalt + b"\nendobj\n")
    xref = ausgabe.tell()
    ausgabe.write(f"xref\n0 {len(objekte) + 1}\n0000000000 65535 f \n".encode())
    for pos in positionen:
        ausgabe.write(f"{pos:010d} 00000 n \n".encode())
    ausgabe.write(f"trailer\n<< /Size {len(objekte) + 1} /Root {katalog} 0 R /Info {info} 0 R >>\n"
                  f"startxref\n{xref}\n%%EOF\n".encode())
    return ausgabe.getvalue()


def _bild_kodieren(bild: np.ndarray) -> tuple[int, bytes]:
    """Gibt (Bits pro Pixel, komprimierte Bilddaten) zurück."""
    werte = np.unique(bild[::7, ::7])
    if np.all((werte == 0) | (werte == 255)) and np.all((bild == 0) | (bild == 255)):
        gepackt = np.packbits(bild > 127, axis=1)            # 1 = weiß, 0 = schwarz
        return 1, zlib.compress(gepackt.tobytes(), 6)
    stufen = ((bild.astype(np.uint16) * 15 + 127) // 255).astype(np.uint8)   # 0..15
    if stufen.shape[1] % 2:
        stufen = np.pad(stufen, ((0, 0), (0, 1)), constant_values=15)
    gepackt = (stufen[:, 0::2] << 4) | stufen[:, 1::2]
    return 4, zlib.compress(gepackt.tobytes(), 6)


def _pdf_text(text: str) -> bytes:
    """PDF-Textstring, auch mit Umlauten (UTF-16 mit BOM)."""
    return b"<FEFF" + text.encode("utf-16-be").hex().upper().encode() + b">"
