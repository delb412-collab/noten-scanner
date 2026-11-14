"""Laden von Fotos/Scans und Speichern der bereinigten Seiten als PNG und PDF."""
from __future__ import annotations

import io
import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

BILD_ENDUNGEN = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp", ".heic", ".heif"}
PDF_ENDUNG = ".pdf"
ALLE_ENDUNGEN = BILD_ENDUNGEN | {PDF_ENDUNG}

try:  # iPhone-Fotos (HEIC) – optional
    import pillow_heif

    pillow_heif.register_heif_opener()
    HEIC_MOEGLICH = True
except ImportError:
    HEIC_MOEGLICH = False

Image.MAX_IMAGE_PIXELS = 300_000_000  # große Handyfotos nicht als "Bombe" ablehnen


def seiten_laden(pfad: Path, dpi: int = 300) -> list[np.ndarray]:
    """Lädt ein Foto, einen Scan oder ein PDF und gibt alle Seiten als BGR-Bilder zurück.

    Die Drehung aus den EXIF-Daten des Handys wird berücksichtigt.
    """
    endung = pfad.suffix.lower()
    if endung == PDF_ENDUNG:
        return _pdf_laden(pfad, dpi)
    if endung in (".heic", ".heif") and not HEIC_MOEGLICH:
        raise RuntimeError("Für HEIC-Fotos (iPhone) bitte zuerst 'pip install pillow-heif' ausführen")
    seiten = []
    with Image.open(pfad) as bild:
        for nummer in range(getattr(bild, "n_frames", 1)):  # mehrseitige TIFFs
            bild.seek(nummer)
            seiten.append(_zu_bgr(ImageOps.exif_transpose(bild)))
    return seiten


def _pdf_laden(pfad: Path, dpi: int) -> list[np.ndarray]:
    try:
        import pypdfium2 as pdfium
    except ImportError:
        raise RuntimeError("Für PDF-Dateien bitte zuerst 'pip install pypdfium2' ausführen") from None
    seiten = []
    dokument = pdfium.PdfDocument(str(pfad))
    try:
        for seite in dokument:
            seiten.append(_zu_bgr(seite.render(scale=dpi / 72).to_pil()))
    finally:
        dokument.close()
    return seiten


def _zu_bgr(bild: Image.Image) -> np.ndarray:
    if bild.mode in ("I;16", "I;16B", "I;16L", "I"):
        bild = Image.fromarray((np.asarray(bild, dtype=np.float32) / 256).clip(0, 255).astype(np.uint8))
    if bild.mode in ("RGBA", "LA", "P"):
        # Transparenz auf weißen Hintergrund legen
        bild = bild.convert("RGBA")
        weiss = Image.new("RGBA", bild.size, (255, 255, 255, 255))
        bild = Image.alpha_composite(weiss, bild)
    rgb = np.asarray(bild.convert("RGB"))
    return np.ascontiguousarray(rgb[..., ::-1])


def seite_speichern(bild: np.ndarray, pfad: Path, dpi: int, hart: bool) -> None:
    """Speichert eine bereinigte Seite verlustfrei als PNG (mit dpi-Angabe für den Druck)."""
    pfad.parent.mkdir(parents=True, exist_ok=True)
    seite = Image.fromarray(bild)
    if hart:
        seite = seite.convert("1", dither=Image.Dither.NONE)
    seite.save(pfad, dpi=(dpi, dpi), optimize=True)


def pdf_erstellen(seiten: list[Path], ziel: Path, titel: str | None = None) -> None:
    """Fügt Seiten-Bilder verlustfrei zu einem PDF zusammen. Reine Schwarz/Weiß-Seiten
    werden mit CCITT G4 komprimiert (wie bei Faxen/Profi-Scannern) – das ergibt sehr kleine Dateien."""
    import img2pdf

    daten = []
    for pfad in seiten:
        with Image.open(pfad) as bild:
            if bild.mode == "1":
                puffer = io.BytesIO()
                bild.save(puffer, format="TIFF", compression="group4", dpi=bild.info.get("dpi", (300, 300)))
                daten.append(puffer.getvalue())
            else:
                daten.append(pfad.read_bytes())
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ziel.write_bytes(img2pdf.convert(daten, title=titel or ziel.stem, creator="Noten-Scanner"))


def natuerlich_sortiert(pfade):
    """Sortiert so, wie Menschen es erwarten: Seite 2 vor Seite 10."""
    def schluessel(pfad: Path):
        return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(pfad))]
    return sorted(pfade, key=schluessel)
