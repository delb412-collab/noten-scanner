"""Prüfbild: die Lesung einer MusicXML-Datei (mit imgpos-Kommentaren von homr) als Punkte auf die
Notenlinien des Originals legen. Punkt = erwartete Lage des Notenkopfs (aus Tonhöhe und Schlüssel).
Farbe: blau = ohne Versetzung, rot = ♯, grün = ♭, orange = ♮ gegen die Tonart. Darüber: Notenwert.
Aufruf: overlay.py stueck musicxml [teile]"""
import re, sys, cv2, numpy as np
import xml.etree.ElementTree as ET
sys.path.insert(0, __import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))
from notenscanner.verarbeitung import notenlinien_masse
from notenscanner.linien import notensysteme_finden

STUFEN = "CDEFGAB"
UNTEN = {("G", 2): ("E", 4), ("F", 4): ("G", 2), ("C", 3): ("F", 3), ("C", 4): ("D", 3)}
KREUZ = "FCGDAEB"
DAUER = {"whole": "1", "half": "2", "quarter": "4", "eighth": "8", "16th": "16", "32nd": "32", "64th": "64"}

def idx(step, octave): return int(octave) * 7 + STUFEN.index(step)
def tonart(fifths, step):
    if fifths > 0: return 1 if step in KREUZ[:fifths] else 0
    if fifths < 0: return -1 if step in KREUZ[::-1][:-fifths] else 0
    return 0

def lesen(pfad):
    baum = ET.parse(pfad, parser=ET.XMLParser(target=ET.TreeBuilder(insert_comments=True)))
    noten, zeile, fifths, clef = [], 0, 0, ("G", 2)
    for takt in baum.getroot().iter("measure"):
        nr = takt.get("number")
        for el in takt:
            if el.tag == "print" and el.get("new-system") == "yes": zeile += 1
            if el.tag == "attributes":
                k = el.find("key/fifths")
                if k is not None: fifths = int(k.text)
                c = el.find("clef")
                if c is not None: clef = (c.findtext("sign"), int(c.findtext("line")))
            if el.tag == "note":
                pos = None
                for kind in el:
                    if kind.tag is ET.Comment and "imgpos" in (kind.text or ""):
                        pos = tuple(int(v) for v in re.findall(r"\d+", kind.text))
                p = el.find("pitch")
                noten.append(dict(takt=nr, zeile=zeile, rest=el.find("rest") is not None,
                    step=p.findtext("step") if p is not None else None,
                    octave=int(p.findtext("octave")) if p is not None else None,
                    alter=int(p.findtext("alter") or 0) if p is not None else 0,
                    typ=DAUER.get(el.findtext("type"), "?") + "." * len(el.findall("dot")),
                    akkord=el.find("chord") is not None, vorschlag=el.find("grace") is not None,
                    pos=pos, fifths=fifths, clef=clef, stimme=el.findtext("voice")))
    return noten

def zeichnen(stueck, pfad, teile=2):
    b = cv2.imread(f'/tmp/omr/sauber/{stueck}.png', 0)
    m = (b < 128).astype(np.uint8)
    d, l = notenlinien_masse(m)[:2]
    systeme = sorted(notensysteme_finden(m, d + l, d), key=lambda z: float(z[0]["verlauf"](b.shape[1] / 2)))
    noten = lesen(pfad)
    bild = cv2.cvtColor(b, cv2.COLOR_GRAY2BGR)
    ebene = bild.copy()
    # Noten dem nächstgelegenen System zuordnen (über die y-Lage aus imgpos)
    for n in noten:
        if n["pos"] is None: n["sys"] = None; continue
        x, y = n["pos"]
        n["sys"] = int(np.argmin([abs(y - float(z[2]["verlauf"](x))) for z in systeme]))
    # Zeile (homr) -> System (Bild): Mehrheit der Noten
    from collections import Counter
    zu = {}
    for z in set(n["zeile"] for n in noten):
        c = Counter(n["sys"] for n in noten if n["zeile"] == z and n["sys"] is not None)
        if c: zu[z] = c.most_common(1)[0][0]
    for n in noten:
        if n["pos"] is None or n["zeile"] not in zu: continue
        s = systeme[zu[n["zeile"]]]
        x = n["pos"][0]
        ys = [float(li["verlauf"](x)) for li in s]
        ab = (ys[4] - ys[0]) / 4
        if n["rest"]:
            cv2.rectangle(ebene, (x - 6, int(ys[2] - 6)), (x + 6, int(ys[2] + 6)), (150, 150, 150), -1)
        else:
            u = UNTEN.get(n["clef"], ("E", 4))
            y = ys[4] - (idx(n["step"], n["octave"]) - idx(*u)) * ab / 2
            gegen = n["alter"] - tonart(n["fifths"], n["step"])
            farbe = (255, 120, 0) if n["alter"] == 0 and gegen == 0 else (0, 0, 255) if n["alter"] > 0 else (0, 160, 0) if n["alter"] < 0 else (0, 140, 255)
            if n["alter"] == 0 and gegen != 0: farbe = (0, 140, 255)
            cv2.circle(ebene, (int(x), int(round(y))), max(4, int(ab * 0.42)), farbe, -1)
        n["_x"], n["_top"] = x, ys[0]
    bild = cv2.addWeighted(ebene, 0.55, bild, 0.45, 0)
    # Beschriftung: Notenwerte (gestaffelt) und Taktnummern
    vorher = {}
    for i, n in enumerate(noten):
        if "_x" not in n: continue
        x, top = n["_x"], n["_top"]
        reihe = i % 2
        text = ("r" if n["rest"] else "") + n["typ"] + ("g" if n["vorschlag"] else "")
        cv2.putText(bild, text, (int(x) - 8, int(top - 14 - 16 * reihe)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (120, 0, 120), 1, cv2.LINE_AA)
        if n["takt"] not in vorher:
            vorher[n["takt"]] = x
            cv2.line(bild, (int(x) - 14, int(top - 50)), (int(x) - 14, int(top - 30)), (0, 0, 200), 2)
            cv2.putText(bild, n["takt"], (int(x) - 12, int(top - 52)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 200), 1, cv2.LINE_AA)
    # Ausschnitte je System
    for k, s in enumerate(systeme, 1):
        xs = [x for a, e in s[0]["abschnitte"] for x in (a, e)]
        x0, x1 = min(xs) - 60, max(xs) + 20
        y0 = int(float(s[0]["verlauf"]((x0 + x1) / 2)) - 95)
        y1 = int(float(s[4]["verlauf"]((x0 + x1) / 2)) + 75)
        breite = (x1 - x0) / teile
        for t in range(teile):
            a, e = int(x0 + t * breite - (20 if t else 0)), int(x0 + (t + 1) * breite + 20)
            aus = bild[max(0, y0):y1, max(0, a):min(b.shape[1], e)]
            f = 1500 / aus.shape[1]
            cv2.imwrite(f'/tmp/omr/overlay/{stueck}_z{k:02d}_{t + 1}.png', cv2.resize(aus, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC))
    print(stueck, len(systeme), 'Systeme;', len(noten), 'Noten/Pausen; Zeilen homr:', len(zu))

zeichnen(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 2)
