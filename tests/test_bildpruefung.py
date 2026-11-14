"""Tests für die Bildprüfungen des Notenlesers (notenleser/bildpruefung.py), die ohne homr laufen:
Faulenzer (Taktwiederholung) und Pausenform. Die Seiten werden hier gezeichnet."""
from __future__ import annotations

import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "notenleser"))
import bildpruefung as bp  # noqa: E402

AB = 20                      # Linienabstand in Pixeln
SYSTEME = [300, 700, 1100]   # y der obersten Linie


def _seite():
    bild = np.full((1500, 2000), 255, np.uint8)
    for y0 in SYSTEME:
        for k in range(5):
            cv2.line(bild, (80, y0 + k * AB), (1920, y0 + k * AB), 0, 2)
        for x in (80, 400, 720, 1040, 1360, 1680, 1920):              # Taktstriche
            cv2.line(bild, (x, y0), (x, y0 + 4 * AB), 0, 2)
    return bild


def _faulenzer(bild, x, y0, schraeg=1, winkel=55, dicke=8):
    """Faulenzer mit Mitte auf der Mittellinie: Schrägstrich(e) und zwei Punkte."""
    import math
    mitte = y0 + 2 * AB
    laenge = 2.1 * AB
    dx, dy = math.cos(math.radians(winkel)) * laenge / 2, math.sin(math.radians(winkel)) * laenge / 2
    for k in range(schraeg):
        versatz = (k - (schraeg - 1) / 2) * 0.9 * AB
        cv2.line(bild, (int(x + versatz - dx), int(mitte + dy)), (int(x + versatz + dx), int(mitte - dy)), 0, dicke)
    breite = (schraeg - 1) * 0.45 * AB
    cv2.circle(bild, (int(x - breite - 0.75 * AB), int(mitte - 0.45 * AB)), 4, 0, -1)
    cv2.circle(bild, (int(x + breite + 0.75 * AB), int(mitte + 0.45 * AB)), 4, 0, -1)


def _note(bild, x, y, hohl=False, punkt=False):
    cv2.ellipse(bild, (x, y), (12, 8), -25, 0, 360, 0, 2 if hohl else -1)
    cv2.line(bild, (x + 11, y - 3), (x + 11, y - 3 * AB), 0, 2)
    if punkt:
        cv2.circle(bild, (x + 24, y - 4), 3, 0, -1)


def test_faulenzer_gefunden_und_nichts_anderes():
    bild = _seite()
    y0 = SYSTEME[0]
    _faulenzer(bild, 560, y0)                                # Eintakt, steil (alter Druck)
    _faulenzer(bild, 880, SYSTEME[1], winkel=28, dicke=7)    # flach (handschriftliche Notenschrift)
    _faulenzer(bild, 1040, SYSTEME[2], schraeg=2)            # Zweitakt auf dem Taktstrich
    # Störer: punktierte halbe Noten, Viertelnoten, Achtelpause-ähnliche Striche
    for x in (450, 1150, 1500):
        _note(bild, x, y0 + 3 * AB, hohl=True, punkt=True)
    for x in (1100, 1200, 1300):
        _note(bild, x, SYSTEME[1] + 2 * AB)
    cv2.line(bild, (300, SYSTEME[2] + 3 * AB), (312, SYSTEME[2] + AB), 0, 2)
    seite = bp.Seite(bild)
    funde = sorted(bp.faulenzer_suchen(seite), key=lambda f: (f["y"], f["x"]))
    assert [(round(f["x"] / 10) * 10, f["taktstrich"]) for f in funde] == [(560, False), (880, False), (1040, True)]
    eintakt = funde[0]
    assert 395 <= eintakt["von"] <= 405 and 715 <= eintakt["bis"] <= 725     # Ausdehnung bis zu den Taktstrichen
    zweitakt = funde[2]
    assert 715 <= zweitakt["von"] <= 725 and 1355 <= zweitakt["bis"] <= 1365  # beide Takte


def test_keine_faulenzer_auf_normaler_seite():
    bild = _seite()
    for y0 in SYSTEME:
        for x in range(150, 1880, 70):
            _note(bild, x, y0 + (x // 70 % 5) * AB // 2 + AB, hohl=(x // 70) % 3 == 0, punkt=(x // 70) % 4 == 0)
    assert bp.faulenzer_suchen(bp.Seite(bild)) == []


def test_linienzone_lueckenlos():
    """Notenlinien genau auf einer halben Pixelzeile: Die Linienentfernung darf keine Zeile auslassen."""
    bild = np.full((400, 600), 255, np.uint8)
    for k in range(5):
        bild[100 + k * 20:102 + k * 20, 20:580] = 0                         # 2 px dick, Mitte auf .5
    seite = bp.Seite(bild)
    ohne = bp.Kopfpruefer(seite).ohne
    assert ohne[95:190, 40:560].sum() == 0


# ------------------------------------------------------------------------------------------------
# Artikulationen (Zeichenfinder)
# ------------------------------------------------------------------------------------------------
Y0 = 300                                    # oberste Linie; Linien bei 300 (F5) … 380 (E4)
NOTE_Y = {"F5": 300, "E5": 310, "D5": 320, "C5": 330, "B4": 340, "A4": 350, "G4": 360, "F4": 370, "E4": 380}


def _system():
    bild = np.full((700, 2400), 255, np.uint8)
    for k in range(5):
        cv2.line(bild, (40, Y0 + k * AB), (2360, Y0 + k * AB), 0, 2)
    return bild


def _kopf(bild, x, ton, hoch=True):
    y = NOTE_Y[ton]
    cv2.ellipse(bild, (x, y), (11, 8), -25, 0, 360, 0, -1)
    if hoch:
        cv2.line(bild, (x + 10, y - 3), (x + 10, y - 3 * AB), 0, 2)
    else:
        cv2.line(bild, (x - 10, y + 3), (x - 10, y + 3 * AB), 0, 2)
    return y


def _akzent(bild, x, y):
    """Akzent ">" wie im Notensatz: dünne Arme, etwa 1,2 × 0,9 Linienabstände."""
    cv2.line(bild, (x - 12, y - 9), (x + 12, y), 0, 2)
    cv2.line(bild, (x - 12, y + 9), (x + 12, y), 0, 2)


def test_artikulationen_erkannt():
    bild = _system()
    faelle = {}
    # Punkt, Strich, Akzent unter Noten mit Hals nach oben
    y = _kopf(bild, 120, "A4"); cv2.circle(bild, (120, 370), 4, 0, -1); faelle["punkt"] = (120, y)
    y = _kopf(bild, 300, "A4"); cv2.line(bild, (288, 370), (312, 370), 0, 5); faelle["strich"] = (300, y)
    y = _kopf(bild, 480, "A4"); _akzent(bild, 480, 400); faelle["akzent"] = (480, y)
    # „≥“: Akzent über Strich, zusammengewachsen, über einer Note mit Hals nach unten
    y = _kopf(bild, 660, "C5", hoch=False); _akzent(bild, 660, 271); cv2.rectangle(bild, (647, 280), (673, 284), 0, -1)
    faelle["akzent_strich"] = (660, y)
    # Punkt an der untersten Linie (beim Entfernen der Linie bleibt ein Stummel hängen)
    y = _kopf(bild, 840, "G4"); cv2.circle(bild, (840, 386), 5, 0, -1); faelle["punkt_an_linie"] = (840, y)
    # Strich unter dem System bei einer Note im System (weiter weg als sonst)
    y = _kopf(bild, 1020, "C5"); cv2.line(bild, (1008, 400), (1032, 400), 0, 5); faelle["strich_weit"] = (1020, y)
    # Keil unter dem System: Spitze zur Note
    y = _kopf(bild, 1200, "A4")
    cv2.fillPoly(bild, [np.array([[1200, 388], [1196, 406], [1204, 406]], np.int32)], 0); faelle["keil"] = (1200, y)
    # Punkt dicht am Kopf (im Zwischenraum direkt darunter)
    y = NOTE_Y["A4"]                                             # großer Kopf, Punkt im Zwischenraum darunter
    cv2.ellipse(bild, (1380, y), (12, 11), -20, 0, 360, 0, -1)
    cv2.line(bild, (1391, y - 3), (1391, y - 3 * AB), 0, 2)
    cv2.circle(bild, (1380, 370), 5, 0, -1); faelle["punkt_dicht"] = (1380, y)
    # kräftiger, kurzer Strich
    y = _kopf(bild, 1560, "A4"); cv2.rectangle(bild, (1549, 366), (1571, 375), 0, -1); faelle["strich_dick"] = (1560, y)
    seite = bp.Seite(bild)
    zf = bp.Zeichenfinder(seite)
    arten = {name: {a for a, _, _ in zf.zeichen_bei(x, y, y)} for name, (x, y) in faelle.items()}
    assert arten["punkt_dicht"] == {"st"}
    assert arten["strich_dick"] == {"te"}
    assert arten["punkt"] == {"st"}
    assert arten["strich"] == {"te"}
    assert arten["akzent"] == {"ak"}
    assert arten["akzent_strich"] == {"ak", "te"}
    assert arten["punkt_an_linie"] == {"st"}
    assert arten["strich_weit"] == {"te"}
    assert "sz" in arten["keil"] and "st" not in arten["keil"]


def test_keine_falschen_punkte():
    """Punktreihe (alter Druck), Punkt eines Textes und Fermatenpunkt sind kein Staccato; ein Bogen
    ist weder Strich noch Akzent."""
    bild = _system()
    faelle = {}
    y = _kopf(bild, 120, "B4", hoch=False)                     # vier Punkte im Bogen
    for dx, dy in ((-15, 4), (-5, 0), (5, 0), (15, 4)):
        cv2.circle(bild, (120 + dx, 284 + dy), 3, 0, -1)
    faelle["punktreihe"] = (120, y)
    y = _kopf(bild, 300, "B4", hoch=False)                     # „t.“ – Buchstabe direkt neben dem Punkt
    cv2.line(bild, (292, 272), (292, 290), 0, 3); cv2.line(bild, (287, 278), (297, 278), 0, 2)
    cv2.circle(bild, (300, 287), 3, 0, -1)
    faelle["textpunkt"] = (300, y)
    y = _kopf(bild, 480, "B4", hoch=False)                     # Fermate: Bogen mit Punkt
    cv2.ellipse(bild, (480, 282), (18, 12), 0, 180, 360, 0, 3); cv2.circle(bild, (480, 279), 3, 0, -1)
    faelle["fermate"] = (480, y)
    y = _kopf(bild, 660, "A4")                                  # Bogen unter zwei Noten
    _kopf(bild, 760, "A4")
    cv2.ellipse(bild, (710, 372), (52, 12), 0, 20, 160, 0, 3)
    faelle["bogen"] = (660, y)
    seite = bp.Seite(bild)
    zf = bp.Zeichenfinder(seite)
    arten = {name: {a for a, _, _ in zf.zeichen_bei(x, y, y)} for name, (x, y) in faelle.items()}
    assert "st" not in arten["punktreihe"]
    assert "st" not in arten["textpunkt"]
    assert "st" not in arten["fermate"]
    assert not arten["bogen"] & {"st", "te", "ak", "ma"}


# ------------------------------------------------------------------------------------------------
# Wiederholungszeichen und Volta-Klammern
# ------------------------------------------------------------------------------------------------
def _wiederholung(bild, x, y0, anfang=True, auf_linien=False):
    """Dicker und dünner Strich mit zwei Punkten. auf_linien: Punkte auf den Linien (alter Druck)."""
    dick, duenn = (x, x + 9), (x + 16, x + 18)
    if not anfang:
        dick, duenn = (x + 9, x + 18), (x, x + 2)
    cv2.rectangle(bild, (dick[0], y0), (dick[1], y0 + 4 * AB), 0, -1)
    cv2.rectangle(bild, (duenn[0], y0), (duenn[1], y0 + 4 * AB), 0, -1)
    px = x + 30 if anfang else x - 12
    for dy in ((2.0, 3.0) if auf_linien else (1.5, 2.5)):
        cv2.circle(bild, (px, int(y0 + dy * AB)), 4, 0, -1)


def _volta(bild, x0, x1, y0, hoch=3.5, rechts=True):
    y = int(y0 - hoch * AB)
    cv2.line(bild, (x0, y), (x1, y), 0, 3)
    cv2.line(bild, (x0, y), (x0, y + 2 * AB), 0, 3)
    if rechts:
        cv2.line(bild, (x1, y), (x1, y + 2 * AB), 0, 3)


def test_wiederholungen_und_volten_gefunden():
    bild = _seite()
    a, b, c = SYSTEME
    _wiederholung(bild, 400, a, anfang=True)                   # neuer Druck: Punkte in den Zwischenräumen
    _wiederholung(bild, 1352, a, anfang=False)
    _wiederholung(bild, 720, b, anfang=True, auf_linien=True)  # alter Druck: Punkte auf den Linien
    _wiederholung(bild, 1672, b, anfang=False, auf_linien=True)
    _volta(bild, 1042, 1356, a)                                # 1. Volte über dem Takt vor dem Wiederholungsende
    _volta(bild, 1364, 1676, a, rechts=False)                  # 2. Volte, rechts offen
    seite = bp.Seite(bild)
    zeichen = sorted((f["system"], f["art"], round(f["x"] / 20) * 20) for f in bp.wiederholungen_suchen(seite))
    oben, mitte = seite.system_bei(1000, a + 2 * AB), seite.system_bei(1000, b + 2 * AB)
    assert zeichen == sorted([(oben, "anfang", 400), (oben, "ende", 1360), (mitte, "anfang", 720), (mitte, "ende", 1680)])
    volten = sorted((round(f["x0"] / 10) * 10, round(f["x1"] / 10) * 10, f["haken_rechts"] >= 0.8) for f in bp.volten_suchen(seite))
    assert volten == [(1040, 1360, True), (1360, 1680, False)]


def test_keine_falschen_wiederholungen_und_volten():
    """Kein Wiederholungszeichen: Schlussstrich, Doppelstrich, punktierter Zweiklang vor einem Taktstrich,
    Faulenzer. Keine Volte: Balken einer Notengruppe, Rahmen einer Tempoangabe, kurze Triolenklammer."""
    bild = _seite()
    a, b, c = SYSTEME
    cv2.rectangle(bild, (1040 - 12, a), (1040 - 10, a + 4 * AB), 0, -1)       # Doppelstrich
    cv2.rectangle(bild, (1920 - 14, c), (1920, c + 4 * AB), 0, -1)            # Schlussstrich (dünn + dick)
    cv2.rectangle(bild, (1920 - 22, c), (1920 - 20, c + 4 * AB), 0, -1)
    for y in (a + int(1.5 * AB), a + int(2.5 * AB)):                           # Zweiklang mit Punkten vor dem Strich
        cv2.ellipse(bild, (690, y), (11, 8), -25, 0, 360, 0, -1)
        cv2.circle(bild, (708, y), 3, 0, -1)
    _faulenzer(bild, 560, b)
    # Balken über vier Noten, über dem System (Hälse hängen daran)
    cv2.rectangle(bild, (460, b - 4 * AB), (640, b - 4 * AB + 9), 0, -1)
    for x in (460, 520, 580, 640):
        cv2.line(bild, (x, b - 4 * AB), (x, b - AB), 0, 2)
    # Rahmen einer Tempoangabe über einem Taktstrich
    cv2.rectangle(bild, (720, c - 5 * AB), (980, c - 3 * AB), 0, 3)
    # kurze Triolenklammer
    cv2.line(bild, (1100, c - 3 * AB), (1180, c - 3 * AB), 0, 2)
    cv2.line(bild, (1100, c - 3 * AB), (1100, c - 3 * AB + 12), 0, 2)
    cv2.line(bild, (1180, c - 3 * AB), (1180, c - 3 * AB + 12), 0, 2)
    seite = bp.Seite(bild)
    assert bp.wiederholungen_suchen(seite) == []
    assert bp.volten_suchen(seite) == []
