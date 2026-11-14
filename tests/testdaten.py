"""Erzeugt künstliche Test-Notenblätter und simuliert typische Handy-Scan-Fehler.

Nur für Entwicklung und Tests – das eigentliche Programm braucht diese Datei nicht.
Benötigt zusätzlich: verovio, cairosvg  (pip install verovio cairosvg)
"""
from __future__ import annotations

import cv2
import numpy as np

STUECKE = {
    "marsch": """X:1
T:Testmarsch
C:Unbekannt
M:2/4
L:1/8
K:Bb
!f! F>G AB | c2 c2 | d>c BA | B2 F2 | G>A Bc | d2 d2 | e>d cB | c4 |
!p! d>e fd | c>d ec | B>c dB | A2 F2 | G>A Bc | dc BA | B2 z2 | z4 |
|: !mf! .F.F .F.F | (GA) (Bc) | .d.d .d.d | (ed) (cB) | A>B cA | B>c dB | c2 f2 | f4 :|
!ff! [Bd]2 [ce]2 | [df]2 [Bd]2 | [ce]>[df] [eg][ce] | [df]4 | (3BcB (3ABA | G2 F2 | E>F GA | B4 |
!p! B,>C DE | F2 F2 | G>F ED | C4 | D>E FG | A2 A2 | B>A GF | G4 |
F>G AB | c>d ec | d>e fd | c>B AG | F>G AB | c2 e2 | d2 c2 | B4 |
|: .B.B .c.c | .d.d .e.e | f2 d2 | B4 | .A.A .B.B | .c.c .d.d | e2 c2 | A4 :|
!ff! [df]>[ce] [Bd][Ac] | [GB]2 [FA]2 | [EG]>[FA] [GB][Ac] | [Bd]4 | z2 [Bd]2 | [Bd]4 |]
""",
    "walzer": """X:2
T:Kleiner Walzer
C:Testkomponist
M:3/4
L:1/4
K:F
!p! C | F2 A | c2 A | G2 B | d2 B | A2 c | f2 e | d3- | d2 C |
F2 A | c2 A | G2 B | d2 B | A2 G | F2 E | F3- | F2 z |
!mf! (cd e) | f2 c | (Bc d) | e2 B | (AB c) | d2 A | G3 | z2 C |
(cd e) | f2 c | (Bc d) | e2 B | (AB c) | d2 e | f3- | f2 z |
!f! a3 | g2 f | e2 d | c3 | b3 | a2 g | f2 e | d3 |
!p! .c.c.c | .d.d.d | .e.e.e | f2 z | .c.c.c | .B.B.B | .A.A.G | F2 z |
F2 A | c2 A | G2 B | d2 B | A2 c | f2 e | d3- | d2 C |
F2 A | c2 A | G2 B | d2 B | A2 G | F2 E | F3- | F3 |]
""",
}


def rendere_stueck(name: str = "marsch", breite: int = 2480, hoehe: int = 3508,
                   skala: int = 40) -> np.ndarray:
    """Rendert ein Teststück als sauberes Graustufenbild (Weiß=255, Schwarz=0)."""
    import cairosvg
    import verovio

    tk = verovio.toolkit()
    tk.setOptions({"inputFrom": "abc", "pageWidth": 2100, "pageHeight": 2970,
                   "scale": skala, "footer": "none", "adjustPageHeight": False,
                   "pageMarginTop": 150, "pageMarginBottom": 150,
                   "pageMarginLeft": 150, "pageMarginRight": 150})
    tk.loadData(STUECKE[name])
    svg = tk.renderToSVG(1)
    png = cairosvg.svg2png(bytestring=svg.encode(), output_width=breite,
                           output_height=hoehe, background_color="white")
    bild = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_GRAYSCALE)
    return bild


def _glatte_zufallsflaeche(rng, h, w, zellen=4):
    klein = rng.random((zellen, zellen)).astype(np.float32)
    return cv2.resize(klein, (w, h), interpolation=cv2.INTER_CUBIC)


def bleistift_eintragen(seite: np.ndarray, rng) -> np.ndarray:
    """Zeichnet graue Bleistift-Eintragungen (Kreise, Zahlen, Atemzeichen)."""
    bild = seite.copy()
    h, w = bild.shape
    for _ in range(8):
        x, y = int(rng.uniform(0.15, 0.85) * w), int(rng.uniform(0.1, 0.8) * h)
        grau = int(rng.uniform(140, 175))
        art = rng.integers(0, 3)
        if art == 0:
            cv2.ellipse(bild, (x, y), (int(w * 0.03), int(w * 0.018)), 0, 0, 360, grau, 3, cv2.LINE_AA)
        elif art == 1:
            cv2.putText(bild, str(rng.integers(1, 5)), (x, y), cv2.FONT_HERSHEY_SIMPLEX, 1.6, grau, 3, cv2.LINE_AA)
        else:
            cv2.line(bild, (x, y), (x + 14, y + 38), grau, 3, cv2.LINE_AA)
    return np.minimum(bild, seite)


def kopierflecken(seite: np.ndarray, rng, anzahl: int = 400) -> np.ndarray:
    """Kleine schwarze Punkte und graue Schlieren wie bei alten Kopien."""
    bild = seite.copy()
    h, w = bild.shape
    for _ in range(anzahl):
        x, y = int(rng.integers(0, w)), int(rng.integers(0, h))
        cv2.circle(bild, (x, y), int(rng.integers(0, 3)), int(rng.integers(0, 90)), -1)
    # ein grauer Kopierer-Streifen
    x0 = int(rng.uniform(0.2, 0.8) * w)
    streifen = np.full_like(bild, 255)
    cv2.line(streifen, (x0, 0), (x0 + 30, h), 215, 6)
    return np.minimum(bild, cv2.GaussianBlur(streifen, (0, 0), 2))


def _einfaerben(seite: np.ndarray, rng) -> np.ndarray:
    """Graues Notenbild -> leicht vergilbtes Papier mit fast-schwarzer Druckfarbe (BGR)."""
    papier = np.array([rng.uniform(205, 230), rng.uniform(222, 240), rng.uniform(232, 248)], np.float32)
    tinte = np.array([38, 36, 40], np.float32)
    a = 1.0 - seite.astype(np.float32)[..., None] / 255.0
    farbig = papier * (1 - a) + tinte * a
    textur = (_glatte_zufallsflaeche(rng, *seite.shape, zellen=40) - 0.5) * 10
    return farbig + textur[..., None]


def _beleuchtung(h, w, rng, staerke=0.45, schatten=True) -> np.ndarray:
    licht = 1.0 - staerke * _glatte_zufallsflaeche(rng, h, w, zellen=3)
    if schatten:
        maske = np.zeros((h, w), np.float32)
        cx, cy = int(rng.uniform(0.2, 0.8) * w), int(rng.uniform(0.6, 1.0) * h)
        cv2.ellipse(maske, (cx, cy), (int(w * 0.35), int(h * 0.18)), rng.uniform(-30, 30), 0, 360, 1.0, -1)
        maske = cv2.GaussianBlur(maske, (0, 0), w * 0.04)
        licht *= 1.0 - 0.4 * maske
    return licht


def _holz(h, w, rng) -> np.ndarray:
    streifen = _glatte_zufallsflaeche(rng, h, w, zellen=8)
    linien = (np.sin(np.linspace(0, 60, w))[None, :] * 0.5 + 0.5).astype(np.float32)
    v = 0.6 * streifen + 0.4 * linien
    farbe = np.stack([40 + 30 * v, 75 + 40 * v, 115 + 50 * v], axis=-1)
    return farbe.astype(np.float32)


def _kamera(bild: np.ndarray, rng, unschaerfe=1.1, rauschen=5.0, jpeg=82) -> np.ndarray:
    bild = cv2.GaussianBlur(bild, (0, 0), unschaerfe)
    bild = bild + rng.normal(0, rauschen, bild.shape).astype(np.float32)
    bild = np.clip(bild, 0, 255).astype(np.uint8)
    ok, buf = cv2.imencode(".jpg", bild, [cv2.IMWRITE_JPEG_QUALITY, jpeg])
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


def handyfoto(seite: np.ndarray, rng, winkel: float | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Simuliert ein Handyfoto: Blatt schräg und perspektivisch verzerrt auf einem Holztisch,
    ungleichmäßiges Licht, Schatten, Unschärfe, Rauschen, JPEG.
    Gibt (Foto, Eckpunkte des Blattes im Foto) zurück."""
    h, w = seite.shape
    farbig = _einfaerben(seite, rng)
    fh, fw = 4032, 3024
    s = 0.72 * fw / w
    cx, cy = fw / 2 + rng.uniform(-80, 80), fh / 2 + rng.uniform(-80, 80)
    winkel = rng.uniform(-6, 6) if winkel is None else winkel
    ecken = np.array([[-w / 2, -h / 2], [w / 2, -h / 2], [w / 2, h / 2], [-w / 2, h / 2]], np.float32) * s
    rad = np.deg2rad(winkel)
    rot = np.array([[np.cos(rad), -np.sin(rad)], [np.sin(rad), np.cos(rad)]], np.float32)
    ecken = ecken @ rot.T + [cx, cy]
    ecken += rng.uniform(-0.035, 0.035, ecken.shape).astype(np.float32) * fw  # Perspektive
    ecken = ecken.astype(np.float32)
    quelle = np.array([[0, 0], [w, 0], [w, h], [0, h]], np.float32)
    M = cv2.getPerspectiveTransform(quelle, ecken)
    blatt = cv2.warpPerspective(farbig, M, (fw, fh), flags=cv2.INTER_LINEAR)
    maske = cv2.warpPerspective(np.ones((h, w), np.float32), M, (fw, fh), flags=cv2.INTER_LINEAR)[..., None]
    foto = blatt * maske + _holz(fh, fw, rng) * (1 - maske)
    foto *= _beleuchtung(fh, fw, rng)[..., None]
    return _kamera(foto, rng), ecken


def scanner_app(seite: np.ndarray, rng, winkel: float | None = None) -> np.ndarray:
    """Simuliert die Ausgabe einer Scanner-App: schon zugeschnitten, aber grauer Hintergrund,
    Schatten, leichte Schräglage, dunkler Rand und geringere Auflösung."""
    h, w = seite.shape
    farbig = _einfaerben(seite, rng)
    winkel = rng.uniform(-2.5, 2.5) if winkel is None else winkel
    M = cv2.getRotationMatrix2D((w / 2, h / 2), winkel, 1.0)
    farbig = cv2.warpAffine(farbig, M, (w, h), borderMode=cv2.BORDER_CONSTANT, borderValue=(60, 70, 80))
    rand = int(w * 0.012)
    farbig[:, :rand] = (55, 60, 70)  # dunkler Streifen vom Tisch am linken Rand
    farbig *= _beleuchtung(h, w, rng, staerke=0.5)[..., None]
    farbig = cv2.resize(farbig, (1700, int(1700 * h / w)), interpolation=cv2.INTER_AREA)
    return _kamera(farbig, rng, unschaerfe=0.9, rauschen=4.0, jpeg=78)


def ausrichten(ergebnis: np.ndarray, vorlage: np.ndarray) -> np.ndarray:
    """Richtet das Ergebnis per ECC-Homographie auf die Vorlage aus (für die Bewertung)."""
    h, w = vorlage.shape
    ergebnis = cv2.resize(ergebnis, (w, h), interpolation=cv2.INTER_AREA)
    f = 0.25
    a = cv2.GaussianBlur(cv2.resize(vorlage, None, fx=f, fy=f, interpolation=cv2.INTER_AREA), (0, 0), 1.5)
    b = cv2.GaussianBlur(cv2.resize(ergebnis, None, fx=f, fy=f, interpolation=cv2.INTER_AREA), (0, 0), 1.5)
    H = np.eye(3, dtype=np.float32)
    try:
        _, H = cv2.findTransformECC(a.astype(np.float32), b.astype(np.float32), H,
                                    cv2.MOTION_HOMOGRAPHY,
                                    (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 200, 1e-6), None, 5)
    except cv2.error:
        pass
    S = np.diag([1 / f, 1 / f, 1]).astype(np.float32)
    H = S @ H @ np.linalg.inv(S)
    return cv2.warpPerspective(ergebnis, H, (w, h), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                               borderValue=255)


def bewerten(ergebnis: np.ndarray, vorlage: np.ndarray) -> dict:
    """Vergleicht Ergebnis mit der sauberen Vorlage: Präzision, Trefferquote, F1 der schwarzen Pixel."""
    erg = ausrichten(ergebnis, vorlage) < 128
    ref = vorlage < 128
    # 1 Pixel Toleranz für kleine Lageabweichungen
    k = np.ones((3, 3), np.uint8)
    ref_d = cv2.dilate(ref.astype(np.uint8), k) > 0
    erg_d = cv2.dilate(erg.astype(np.uint8), k) > 0
    praezision = (erg & ref_d).sum() / max(erg.sum(), 1)
    trefferquote = (ref & erg_d).sum() / max(ref.sum(), 1)
    f1 = 2 * praezision * trefferquote / max(praezision + trefferquote, 1e-9)
    return {"praezision": round(float(praezision), 4), "trefferquote": round(float(trefferquote), 4),
            "f1": round(float(f1), 4), "dicke": round(float(erg.sum() / max(ref.sum(), 1)), 3)}


def einfaches_notenblatt(rng, breite: int = 2480, hoehe: int = 3508, zeilen: int = 9,
                         abstand: int = 24) -> tuple[np.ndarray, list[tuple[int, int]]]:
    """Zeichnet ein einfaches Notenblatt ohne Zusatzprogramme (für die automatischen Tests).
    Gibt das Bild und die Mittelpunkte der Staccato-Punkte zurück."""
    bild = np.full((hoehe, breite), 255, np.uint8)
    cv2.putText(bild, "Testmarsch", (breite // 2 - 260, 230), cv2.FONT_HERSHEY_TRIPLEX, 2.5, 0, 4, cv2.LINE_AA)
    links, rechts = 180, breite - 180
    punkte = []
    for zeile in range(zeilen):
        oben = 420 + zeile * 330
        for i in range(5):
            y = oben + i * abstand
            cv2.line(bild, (links, y), (rechts, y), 0, 3)
        for x in (links, rechts):
            cv2.line(bild, (x, oben), (x, oben + 4 * abstand), 0, 4)
        # vereinfachter Violinschlüssel am Zeilenanfang
        sx = links + 45
        cv2.ellipse(bild, (sx, oben + 3 * abstand), (int(0.9 * abstand), int(1.1 * abstand)), 0, 0, 360, 0, 6,
                    cv2.LINE_AA)
        cv2.line(bild, (sx + 8, oben - int(1.5 * abstand)), (sx - 4, oben + int(5.5 * abstand)), 0, 6, cv2.LINE_AA)
        cv2.circle(bild, (sx - 12, oben + int(5.3 * abstand)), int(0.4 * abstand), 0, -1, cv2.LINE_AA)
        x = links + 160
        while x < rechts - 80:
            stufe = int(rng.integers(-2, 11))
            y = oben + stufe * abstand // 2
            cv2.ellipse(bild, (x, y), (int(abstand * 0.62), int(abstand * 0.45)), -20, 0, 360, 0, -1, cv2.LINE_AA)
            cv2.line(bild, (x + int(abstand * 0.55), y), (x + int(abstand * 0.55), y - int(3.3 * abstand)), 0, 3)
            if rng.random() < 0.3:  # Staccato-Punkt unter der Note
                p = (x, y + int(1.1 * abstand))
                cv2.circle(bild, p, int(abstand * 0.2), 0, -1, cv2.LINE_AA)
                punkte.append(p)
            x += int(rng.integers(90, 160))
            if rng.random() < 0.15 and x < rechts - 100:
                cv2.line(bild, (x - 40, oben), (x - 40, oben + 4 * abstand), 0, 3)
    return bild, punkte
