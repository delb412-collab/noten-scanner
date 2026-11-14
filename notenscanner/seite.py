"""Blatterkennung: findet das Notenblatt in einem Handyfoto und entzerrt die Perspektive."""
from __future__ import annotations

import itertools

import cv2
import numpy as np

ARBEITSGROESSE = 1000  # längste Bildseite (Pixel), auf der das Blatt gesucht wird


def blatt_finden(bild_bgr: np.ndarray) -> np.ndarray | None:
    """Sucht das Blatt Papier im Foto.

    Gibt die vier Ecken (oben-links, oben-rechts, unten-rechts, unten-links) in
    Bildkoordinaten zurück – oder None, wenn kein Blatt gefunden wurde bzw. das
    Bild schon zugeschnitten ist (z. B. aus einer Scanner-App).
    """
    h, w = bild_bgr.shape[:2]
    f = min(1.0, ARBEITSGROESSE / max(h, w))
    klein = cv2.resize(bild_bgr, None, fx=f, fy=f, interpolation=cv2.INTER_AREA) if f < 1 else bild_bgr
    grau = cv2.cvtColor(klein, cv2.COLOR_BGR2GRAY)
    flaeche = grau.shape[0] * grau.shape[1]
    weich = cv2.GaussianBlur(grau, (0, 0), 2).astype(np.float32)
    gradient = (cv2.Sobel(weich, cv2.CV_32F, 1, 0) / 8, cv2.Sobel(weich, cv2.CV_32F, 0, 1) / 8)

    bester, beste_wertung = None, 0.0
    for maske in _papier_masken(klein, grau):
        konturen, _ = cv2.findContours(maske, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for kontur in sorted(konturen, key=cv2.contourArea, reverse=True)[:3]:
            if cv2.contourArea(kontur) < 0.2 * flaeche:
                break
            ecken = _viereck(kontur)
            if ecken is None:
                continue
            # Zweimal: der erste Durchgang findet die Kanten grob, der zweite genau
            ecken = _kanten_verfeinern(ecken, weich, reichweite=0.15, winkel=20)
            ecken = _kanten_verfeinern(ecken, weich, reichweite=0.04, winkel=5)
            if not _plausibel(ecken, grau, gradient):
                continue
            # Je besser alle vier Seiten auf echten Blattkanten liegen, desto besser. Der
            # Bildrand zählt nur halb – sonst gewinnt ein zu großes Viereck bis zum Bildrand.
            belegt = np.mean([_kante_belegt(ecken[i], ecken[(i + 1) % 4], gradient, rand_gewicht=0.5)
                              for i in range(4)])
            wertung = belegt * cv2.contourArea(ecken) / flaeche
            if wertung > beste_wertung:
                bester, beste_wertung = ecken, wertung
    if bester is None:
        return None
    return bester.astype(np.float32) / f


def gemessene_groesse(ecken: np.ndarray) -> tuple[float, float]:
    """Breite und Höhe des Blattes (in Pixeln des Fotos), gemittelt über gegenüberliegende Kanten."""
    ol, or_, ur, ul = ecken
    breite = (np.linalg.norm(or_ - ol) + np.linalg.norm(ur - ul)) / 2
    hoehe = (np.linalg.norm(ul - ol) + np.linalg.norm(ur - or_)) / 2
    return float(breite), float(hoehe)


def blatt_entzerren(grau: np.ndarray, ecken: np.ndarray, breite: int, hoehe: int,
                    einzug: float = 0.004) -> np.ndarray:
    """Schneidet das Blatt aus und zieht es auf ein gerades Rechteck (breite x hoehe).

    `einzug` schneidet rundherum einen kleinen Rand weg, damit kein Tisch mehr zu sehen ist.
    """
    ziel = np.float32([[0, 0], [breite, 0], [breite, hoehe], [0, hoehe]])
    ziel_innen = ziel.copy()
    ziel_innen[:, 0] = ziel[:, 0] * (1 + 2 * einzug) - breite * einzug
    ziel_innen[:, 1] = ziel[:, 1] * (1 + 2 * einzug) - hoehe * einzug
    M = cv2.getPerspectiveTransform(ecken.astype(np.float32), ziel_innen)
    return cv2.warpPerspective(grau, M, (breite, hoehe), flags=cv2.INTER_CUBIC,
                               borderMode=cv2.BORDER_REPLICATE)


def _papier_masken(klein: np.ndarray, grau: np.ndarray):
    """Zwei unabhängige Vermutungen, wo das Papier ist: über Helligkeit und über Kanten."""
    kurz = min(grau.shape)
    k_gross = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kurz // 40 * 2 + 1,) * 2)

    # 1) Papier ist hell und kaum farbig (Tische, Teppiche usw. meist nicht)
    hsv = cv2.cvtColor(klein, cv2.COLOR_BGR2HSV)
    papier = cv2.subtract(hsv[..., 2], hsv[..., 1])
    papier = cv2.GaussianBlur(papier, (5, 5), 0)
    _, hell = cv2.threshold(papier, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    hell = cv2.morphologyEx(hell, cv2.MORPH_CLOSE, k_gross)  # Noten im Blatt auffüllen
    hell = cv2.morphologyEx(hell, cv2.MORPH_OPEN, np.ones((9, 9), np.uint8))
    yield hell

    # 2) Kanten des Blattes – mit mehreren Empfindlichkeiten, damit auch ein Blatt auf
    #    hellem Tisch (wenig Kontrast) gefunden wird
    weich = cv2.GaussianBlur(grau, (5, 5), 0)
    median = float(np.median(weich))
    for unten, oben in ((0.5 * median, 1.2 * median), (30, 90), (12, 36)):
        kanten = cv2.Canny(weich, unten, oben)
        kanten = cv2.dilate(kanten, np.ones((3, 3), np.uint8), iterations=2)
        gefuellt = np.zeros_like(kanten)
        konturen, _ = cv2.findContours(kanten, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(gefuellt, konturen, -1, 255, thickness=cv2.FILLED)
        yield cv2.morphologyEx(gefuellt, cv2.MORPH_OPEN, np.ones((9, 9), np.uint8))


def _viereck(kontur: np.ndarray) -> np.ndarray | None:
    """Macht aus einer Kontur ein Viereck. Klappt das nicht direkt (z. B. bei einer
    eingeknickten Ecke), wird das größte Viereck aus den Eckpunkten der Hülle gewählt."""
    huelle = cv2.convexHull(kontur)
    umfang = cv2.arcLength(huelle, True)
    for eps in (0.01, 0.02, 0.03, 0.05):
        naeherung = cv2.approxPolyDP(huelle, eps * umfang, True)
        if len(naeherung) == 4:
            return _sortieren(naeherung.reshape(4, 2).astype(np.float32))
        if len(naeherung) < 4:
            return None
    punkte = cv2.approxPolyDP(huelle, 0.01 * umfang, True).reshape(-1, 2).astype(np.float32)
    if len(punkte) > 24:
        return None
    bestes = max(itertools.combinations(range(len(punkte)), 4),
                 key=lambda idx: cv2.contourArea(punkte[list(idx)]))
    return _sortieren(punkte[list(bestes)])


def _sortieren(p: np.ndarray) -> np.ndarray:
    """Ordnet vier Punkte: oben-links, oben-rechts, unten-rechts, unten-links."""
    summe = p.sum(axis=1)
    differenz = p[:, 1] - p[:, 0]
    return np.array([p[np.argmin(summe)], p[np.argmin(differenz)],
                     p[np.argmax(summe)], p[np.argmax(differenz)]], np.float32)


def _plausibel(ecken: np.ndarray, grau: np.ndarray, gradient) -> bool:
    """Prüft, ob das Viereck wirklich ein Blatt sein kann."""
    flaeche = grau.shape[0] * grau.shape[1]
    anteil = cv2.contourArea(ecken) / flaeche
    if not 0.2 <= anteil <= 0.96:
        return False  # zu klein – oder das Bild ist ohnehin schon zugeschnitten
    if not cv2.isContourConvex(ecken.reshape(-1, 1, 2)):
        return False
    for i in range(4):
        a, b, c = ecken[i - 1], ecken[i], ecken[(i + 1) % 4]
        v1, v2 = a - b, c - b
        cos = np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-9)
        if not 50 <= np.degrees(np.arccos(np.clip(cos, -1, 1))) <= 130:
            return False
    if min(_kante_belegt(ecken[i], ecken[(i + 1) % 4], gradient) for i in range(4)) < 0.5:
        return False
    # Das Blatt muss heller sein als seine Umgebung
    innen = np.zeros_like(grau)
    cv2.fillConvexPoly(innen, ecken.astype(np.int32), 255)
    k = np.ones((15, 15), np.uint8)
    ring = cv2.subtract(cv2.dilate(innen, k), innen)
    kern = cv2.erode(innen, k)
    if cv2.countNonZero(ring) < 0.01 * flaeche or cv2.countNonZero(kern) == 0:
        return True  # Blatt reicht fast bis an den Bildrand, kein Vergleich möglich
    return cv2.mean(grau, kern)[0] - cv2.mean(grau, ring)[0] > 8


def _kante_belegt(a: np.ndarray, b: np.ndarray, gradient, rand_gewicht: float = 1.0) -> float:
    """Wie gut liegt die Viereckseite a->b auf einer echten Blattkante? Anteil der Stellen,
    an denen es nach außen hin deutlich dunkler wird. Stellen am Bildrand (Blatt angeschnitten)
    zählen mit `rand_gewicht`."""
    gx, gy = gradient
    h, w = gx.shape
    richtung = (b - a) / (np.linalg.norm(b - a) + 1e-9)
    aussen = np.array([richtung[1], -richtung[0]])  # Ecken laufen im Uhrzeigersinn
    t = np.linspace(0.05, 0.95, 80)[:, None]
    punkte = a + t * (b - a)
    belegt = np.zeros(len(punkte), bool)
    for versatz in range(-4, 5):  # kleine Ungenauigkeit der Ecken zulassen
        p = punkte + versatz * aussen
        x = np.clip(np.round(p[:, 0]).astype(int), 0, w - 1)
        y = np.clip(np.round(p[:, 1]).astype(int), 0, h - 1)
        nach_aussen = gx[y, x] * aussen[0] + gy[y, x] * aussen[1]
        belegt |= nach_aussen < -2.0
    am_rand = (punkte[:, 0] < 4) | (punkte[:, 1] < 4) | (punkte[:, 0] > w - 5) | (punkte[:, 1] > h - 5)
    return float(np.mean(np.where(belegt, 1.0, np.where(am_rand, rand_gewicht, 0.0))))


def _kanten_verfeinern(ecken: np.ndarray, weich: np.ndarray, reichweite: float,
                       winkel: float) -> np.ndarray:
    """Richtet jede Seite des groben Vierecks an der echten Papierkante aus.

    Die grobe Form aus der Umrisssuche ist oft verfälscht, z. B. wenn neben dem Blatt eine
    glänzende Fliese fast genauso hell ist. Deshalb wird quer zu jeder Seite nach der
    Stelle gesucht, an der es von innen (Papier) nach außen (Untergrund) dunkler wird, und
    durch diese Stellen robust eine Gerade gelegt. Aus den vier Geraden ergeben sich die Ecken.
    """
    h, w = weich.shape
    geraden = []
    for i in range(4):
        a, b = ecken[i], ecken[(i + 1) % 4]
        # Liegt die Seite am Bildrand, ist das Blatt dort abgeschnitten – nichts suchen,
        # sonst wird womöglich eine Notenzeile für die Kante gehalten
        gerade = None if _am_bildrand(a, b, h, w) else _kante_suchen(a, b, weich, int(reichweite * max(h, w)), winkel)
        if gerade is None:
            richtung = (b - a) / (np.linalg.norm(b - a) + 1e-9)
            gerade = (a.astype(np.float64), richtung.astype(np.float64))
        geraden.append(gerade)
    neu = []
    for i in range(4):
        punkt = _schnittpunkt(geraden[i - 1], geraden[i])
        # Ecken dürfen etwas außerhalb des Fotos liegen (Blatt angeschnitten), aber nicht weit
        if punkt is None or not (-0.1 * w <= punkt[0] <= 1.1 * w and -0.1 * h <= punkt[1] <= 1.1 * h):
            return ecken
        neu.append(punkt)
    return np.array(neu, np.float32)


def _kante_suchen(a: np.ndarray, b: np.ndarray, weich: np.ndarray, reichweite: int, winkel: float):
    """Sucht nahe der Seite a->b die Papierkante. Gibt (Punkt, Richtung) oder None zurück."""
    laenge = np.linalg.norm(b - a)
    if laenge < 20:
        return None
    richtung = (b - a) / laenge
    aussen = np.array([richtung[1], -richtung[0]])  # Ecken laufen im Uhrzeigersinn
    t = np.linspace(0.08, 0.92, 48)
    s = np.arange(-reichweite, reichweite + 1)
    punkte = a[None, None, :] + (t[:, None, None] * (b - a)[None, None, :]
                                 + s[None, :, None] * aussen[None, None, :])
    profile = cv2.remap(weich, punkte[..., 0].astype(np.float32), punkte[..., 1].astype(np.float32),
                        cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)

    # Helligkeitssprung: Mittel innen (s-10..s-3) minus Mittel außen (s+3..s+10).
    # Breite Fenster, damit dünne Notenlinien kaum zählen, eine Papierkante aber stark.
    summe = np.concatenate([np.zeros((len(t), 1), np.float32), np.cumsum(profile, axis=1)], axis=1)
    mitte = np.arange(10, len(s) - 10)
    innen = (summe[:, mitte - 2] - summe[:, mitte - 10]) / 8
    aussen_mittel = (summe[:, mitte + 11] - summe[:, mitte + 3]) / 8
    sprung = innen - aussen_mittel

    # Pro Querschnitt bis zu drei deutliche Sprünge als Kandidaten
    kandidaten, gewichte, querschnitt = [], [], []
    for k in range(len(t)):
        zeile = sprung[k]
        spitzen = np.flatnonzero((zeile > 10) & (zeile >= np.maximum.reduce(
            [np.roll(zeile, v) for v in (-3, -2, -1, 1, 2, 3)])))
        for j in spitzen[np.argsort(zeile[spitzen])[::-1][:3]]:
            kandidaten.append(punkte[k, mitte[j]])
            gewichte.append(zeile[j])
            querschnitt.append(k)
    if len(kandidaten) < 10:
        return None
    kandidaten = np.array(kandidaten)
    gewichte = np.array(gewichte)
    querschnitt = np.array(querschnitt)

    # RANSAC über alle Punktpaare aus weit auseinanderliegenden Querschnitten
    i, j = np.triu_indices(len(kandidaten), 1)
    weit = np.abs(querschnitt[i] - querschnitt[j]) >= 12
    i, j = i[weit], j[weit]
    if len(i) == 0:
        return None
    d = kandidaten[j] - kandidaten[i]
    d /= np.linalg.norm(d, axis=1, keepdims=True) + 1e-9
    passend = np.abs(d @ richtung) > np.cos(np.radians(winkel))  # ungefähr parallel zur groben Seite
    i, j, d = i[passend], j[passend], d[passend]
    if len(i) == 0:
        return None
    normale = np.stack([-d[:, 1], d[:, 0]], axis=1)
    abstaende = np.abs(np.einsum("pk,pnk->pn", normale, kandidaten[None, :, :] - kandidaten[i][:, None, :]))
    unterstuetzung = (np.where(abstaende < 2.0, gewichte[None, :], 0)).sum(axis=1)
    beste = int(np.argmax(unterstuetzung))
    treffer = abstaende[beste] < 2.0
    if len(np.unique(querschnitt[treffer])) < 0.35 * len(t):
        return None  # keine durchgehende Kante gefunden
    vx, vy, x0, y0 = cv2.fitLine(kandidaten[treffer].astype(np.float32), cv2.DIST_HUBER, 0, 0.01, 0.01).ravel()
    return np.array([x0, y0], np.float64), np.array([vx, vy], np.float64)


def _schnittpunkt(g1, g2) -> np.ndarray | None:
    (p1, d1), (p2, d2) = g1, g2
    matrix = np.array([d1, -d2]).T
    if abs(np.linalg.det(matrix)) < 1e-6:
        return None
    t = np.linalg.solve(matrix, p2 - p1)
    return (p1 + t[0] * d1).astype(np.float32)


def _am_bildrand(a: np.ndarray, b: np.ndarray, h: int, w: int) -> bool:
    toleranz = 0.02 * max(h, w)
    return ((a[0] < toleranz and b[0] < toleranz) or (a[1] < toleranz and b[1] < toleranz)
            or (a[0] > w - 1 - toleranz and b[0] > w - 1 - toleranz)
            or (a[1] > h - 1 - toleranz and b[1] > h - 1 - toleranz))
