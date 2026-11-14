"""Tests für Wiederholungen und Volten im Notentext: Lage des Wiederholungsanfangs, Zuordnung der im
Bild gefundenen Zeichen zu den Takten (auch bei Mehrtaktpausen), ergänzter Anfang, Anpassung für den
Notensatz. Braucht homr (wie der Notenleser); die Seite wird gezeichnet."""
from __future__ import annotations

import os
import sys
import xml.etree.ElementTree as ET

import pytest

pytest.importorskip("homr")
cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HIER, ".."))
sys.path.insert(0, os.path.join(HIER, "..", "..", "notenleser"))

AB = 20
Y = (300, 700)                                   # oberste Linie der beiden Notenzeilen
STRICHE = (80, 400, 720, 1040, 1360, 1680, 1920)


def _seite():
    bild = np.full((1100, 2000), 255, np.uint8)
    for y0 in Y:
        for k in range(5):
            cv2.line(bild, (80, y0 + k * AB), (1920, y0 + k * AB), 0, 2)
        for x in STRICHE:
            cv2.line(bild, (x, y0), (x, y0 + 4 * AB), 0, 2)
    return bild


def _zeichen(bild, x, y0, anfang):
    dick, duenn, px = ((x, x + 9), (x + 16, x + 18), x + 30) if anfang else ((x + 9, x + 18), (x, x + 2), x - 12)
    cv2.rectangle(bild, (dick[0], y0), (dick[1], y0 + 4 * AB), 0, -1)
    cv2.rectangle(bild, (duenn[0], y0), (duenn[1], y0 + 4 * AB), 0, -1)
    for dy in (1.5, 2.5):
        cv2.circle(bild, (px, int(y0 + dy * AB)), 4, 0, -1)


def _volta(bild, x0, x1, y0, rechts=True):
    y = y0 - 70
    cv2.line(bild, (x0, y), (x1, y), 0, 3)
    cv2.line(bild, (x0, y), (x0, y + 2 * AB), 0, 3)
    if rechts:
        cv2.line(bild, (x1, y), (x1, y + 2 * AB), 0, 3)


def _takt(nr, x, y, inhalt="", pause=None, umbruch=False):
    """Ein Takt mit einer Note bei (x, y); pause=N: Beginn einer Mehrtaktpause über N Takte."""
    kopf = '<print new-system="yes"/>' if umbruch else ""
    if pause:
        return (f'<measure number="{nr}">{kopf}{inhalt}<attributes><measure-style><multiple-rest>{pause}</multiple-rest>'
                f'<!-- imgpos: {x}, {y} --></measure-style></attributes>'
                '<note><rest measure="yes"/><duration>4</duration><voice>1</voice></note></measure>')
    if x is None:
        return f'<measure number="{nr}"><note><rest measure="yes"/><duration>4</duration><voice>1</voice></note>{inhalt}</measure>'
    return (f'<measure number="{nr}">{kopf}{inhalt}<note><pitch><step>C</step><octave>5</octave></pitch><duration>4</duration>'
            f'<voice>1</voice><type>whole</type><!-- imgpos: {x}, {y} --></note></measure>')


def _partitur(takte):
    text = ('<score-partwise version="4.0"><part-list><score-part id="P1"><part-name>T</part-name></score-part>'
            '</part-list><part id="P1">' + "".join(takte) + "</part></score-partwise>")
    # Kommentare behalten: darin steht die Lage der Noten im Bild
    return ET.fromstring(text, parser=ET.XMLParser(target=ET.TreeBuilder(insert_comments=True)))


def _striche(wurzel):
    """{Taktnummer: [Kürzel]} – |: und :| mit Ort, Volten als V<Nummer><Art>."""
    aus = {}
    for takt in wurzel.iter("measure"):
        for b in takt.findall("barline"):
            ort = b.get("location", "right")[0]
            for r in b.findall("repeat"):
                aus.setdefault(int(takt.get("number")), []).append(("|:" if r.get("direction") == "forward" else ":|") + ort)
            for e in b.findall("ending"):
                aus.setdefault(int(takt.get("number")), []).append(f"V{e.get('number')}{e.get('type')[:5]}{ort}")
    return {k: sorted(v) for k, v in aus.items()}


def test_wiederholungsanfang_steht_links():
    import notenleser as nl
    nl.ZUSTAND.update(protokoll=[])
    wurzel = _partitur([_takt(1, 200, 340),
                        _takt(2, 500, 340, '<barline location="right"><repeat direction="forward"/></barline>'),
                        _takt(3, 800, 340) .replace("</measure>", '<barline location="right"><repeat direction="backward"/></barline></measure>')])
    nl.wiederholungen_richten(wurzel)
    assert _striche(wurzel) == {2: ["|:l"], 3: [":|r"]}
    erster = wurzel.find("part").findall("measure")[1][0]
    assert erster.tag == "barline" and [k.tag for k in erster] == ["bar-style", "repeat"]


def test_zeichen_und_volten_nach_bild():
    """Zeile 1: |: vor einer Mehrtaktpause (Takte 2–3), 1. Volte über Takt 5 mit :|, 2. Volte Takt 6.
    Zeile 2: 1. Volte über einer Mehrtaktpause (Takte 8–10) mit :|, 2. Volte Takt 11. homrs Lesart
    (falscher Ort, Volte ohne Anfang, falsche Nummer) wird ersetzt."""
    import bildpruefung as bp
    import notenleser as nl
    bild = _seite()
    _zeichen(bild, 400, Y[0], anfang=True)
    _zeichen(bild, 1672, Y[0], anfang=False)
    _volta(bild, 1362, 1676, Y[0])
    _volta(bild, 1684, 1900, Y[0], rechts=False)
    _zeichen(bild, 1032, Y[1], anfang=False)
    _volta(bild, 402, 1036, Y[1])
    _volta(bild, 1044, 1340, Y[1], rechts=False)
    nl.ZUSTAND.update(protokoll=[], seite=bp.Seite(bild))
    falsch = '<barline location="right"><ending number="3" type="stop"/></barline>'
    wurzel = _partitur([
        _takt(1, 240, 340),
        _takt(2, 560, 340, pause=2), _takt(3, None, None),
        _takt(4, 1200, 340, falsch),
        _takt(5, 1520, 340),
        _takt(6, 1800, 340),
        _takt(7, 240, 740, umbruch=True),
        _takt(8, 720, 740, pause=3), _takt(9, None, None), _takt(10, None, None),
        _takt(11, 1200, 740),
        _takt(12, 1520, 740), _takt(13, 1800, 740),
    ])
    nl.wiederholungen_nach_bild(wurzel)
    assert _striche(wurzel) == {
        2: ["|:l"],                                   # Anfang am ersten Takt der Mehrtaktpause
        5: [":|r", "V1startl", "V1stopr"],
        6: ["V2discor", "V2startl"],
        7: ["|:l"],                                   # im Bild nicht gesetzt: ergänzt, neuer Teil nach der 2. Volte
        8: ["V1startl"],
        10: [":|r", "V1stopr"],                       # Ende am letzten Takt der Mehrtaktpause
        11: ["V2discor", "V2startl"],
    }
    assert any("ergänzt" in z for z in nl.ZUSTAND["protokoll"])


def test_satz_volta_ende_bei_mehrtaktpause():
    """Für Verovio wandert das Ende einer Volte vom letzten an den ersten Takt der Mehrtaktpause."""
    import satz
    xml = ET.tostring(_partitur([
        _takt(1, 240, 340, '<barline location="left"><ending number="1" type="start"/></barline>', pause=3),
        _takt(2, None, None),
        _takt(3, None, None, '<barline location="right"><bar-style>light-heavy</bar-style><ending number="1" type="stop"/>'
                             '<repeat direction="backward"/></barline>'),
        _takt(4, 1200, 340, '<barline location="left"><ending number="2" type="start"/></barline>'),
    ]), encoding="unicode")
    neu = ET.fromstring(satz.fuer_verovio(xml))
    assert _striche(neu) == {1: ["V1startl", "V1stopr"], 3: [":|r"], 4: ["V2startl"]}
    assert satz.fuer_verovio(satz.fuer_verovio(xml)) == satz.fuer_verovio(xml)          # zweimal anwenden ändert nichts
    ohne = ET.tostring(_partitur([_takt(1, 240, 340), _takt(2, 500, 340)]), encoding="unicode").replace(
        "<part-name>T</part-name>", "<part-name />")
    assert satz.fuer_verovio(ohne) is ohne
