"""Erzeugt künstliche Testfotos für den Browser-Test der App (keine echten Noten nötig)."""
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

HIER = Path(__file__).resolve().parent
sys.path.insert(0, str(HIER.parents[1]))
from tests import testdaten as td  # noqa: E402

(HIER / "fotos").mkdir(exist_ok=True)
for nummer, (winkel, art) in enumerate(((3.0, ""), (-2.0, "_exif6"), (5.0, ""), (2.0, "_quer")), start=1):
    seite, _ = td.einfaches_notenblatt(np.random.default_rng(nummer))
    foto, _ = td.handyfoto(seite, np.random.default_rng(10 + nummer), winkel=winkel)
    bild = Image.fromarray(cv2.cvtColor(foto, cv2.COLOR_BGR2RGB))
    ziel = HIER / "fotos" / f"foto{nummer}{art}.jpg"
    if art == "_exif6":          # Pixel quer gespeichert, EXIF sagt "um 90° drehen" (wie beim iPhone)
        exif = Image.Exif()
        exif[0x0112] = 6
        bild.rotate(90, expand=True).save(ziel, quality=88, exif=exif)
    elif art == "_quer":         # Blatt quer fotografiert (Notenlinien senkrecht)
        bild.rotate(90, expand=True).save(ziel, quality=88)
    else:
        bild.save(ziel, quality=88)
    print(ziel.name)
