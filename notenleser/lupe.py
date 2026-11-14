"""Ausschnitt einer Notenzeile stark vergrößert, mit den erkannten Notenlinien (rot, folgen der
Krümmung) und allen Ton-Positionen (grau) samt Tonnamen (Violinschlüssel) am linken Rand.
Aufruf: lupe.py stueck zeile x0 x1 name"""
import cv2, sys, numpy as np
sys.path.insert(0, __import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))
from notenscanner.verarbeitung import notenlinien_masse
from notenscanner.linien import notensysteme_finden
stueck, zeile, a, e, name = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5]
xml = sys.argv[6] if len(sys.argv) > 6 else None
import re
takte_pos = {}
if xml:
    import xml.etree.ElementTree as ET
    baum = ET.parse(xml, parser=ET.XMLParser(target=ET.TreeBuilder(insert_comments=True)))
    for t in baum.getroot().iter("measure"):
        for k in t.iter():
            if k.tag is ET.Comment and "imgpos" in (k.text or ""):
                x, y = (int(v) for v in re.findall(r"\d+", k.text))
                takte_pos.setdefault(t.get("number"), []).append((x, y))
b = cv2.imread(f'/tmp/omr/sauber/{stueck}.png', 0)
m = (b < 128).astype(np.uint8)
d, l = notenlinien_masse(m)[:2]
z = sorted(notensysteme_finden(m, d + l, d), key=lambda z: float(z[0]['verlauf'](b.shape[1] / 2)))[zeile - 1]
w = b.shape[1]
if a.startswith("T"):          # Taktbereich, z. B. T45 T47 -> x aus den Notenpositionen
    xs = [x for nr in takte_pos for x, y in takte_pos[nr] if int(re.sub(r"\D", "", a)) <= int(nr) <= int(re.sub(r"\D", "", e))]
    x0, x1 = min(xs) - 60, max(xs) + 70
else:
    x0, x1 = int(float(a) * w), int(float(e) * w)
xm = (x0 + x1) / 2
ys = [float(li['verlauf'](xm)) for li in z]
ab = (ys[4] - ys[0]) / 4
top, unten = int(ys[0] - 3.6 * ab), int(ys[4] + 3.2 * ab)
aus = b[top:unten, x0:x1]
f = min(7, 1300 / aus.shape[1])
g = cv2.cvtColor(cv2.resize(aus, None, fx=f, fy=f, interpolation=cv2.INTER_NEAREST), cv2.COLOR_GRAY2BGR)
rand = 70
g = cv2.copyMakeBorder(g, 0, 0, rand, 0, cv2.BORDER_CONSTANT, value=(255, 255, 255))
NAMEN = ["E4", "F4", "G4", "A4", "B4", "C5", "D5", "E5", "F5", "G5", "A5", "B5", "C6", "D6"]
UNTER = ["D4", "C4", "B3", "A3"]
for k in range(-4, 14):                  # k = Halbschritte über der untersten Linie
    pts = []
    for x in range(x0, x1, 4):
        yu = float(z[4]['verlauf'](x)); yo = float(z[0]['verlauf'](x))
        y = yu - k * (yu - yo) / 8
        pts.append((int((x - x0) * f) + rand, int((y - top) * f)))
    linie = 0 <= k <= 8 and k % 2 == 0
    farbe = (0, 0, 230) if linie else ((120, 120, 255) if k % 2 == 0 else (200, 200, 200))
    cv2.polylines(g, [np.array(pts, np.int32)], False, farbe, 1, cv2.LINE_AA)
    name_k = NAMEN[k] if k >= 0 else UNTER[-k - 1]
    cv2.putText(g, name_k, (4, pts[0][1] + 5), cv2.FONT_HERSHEY_SIMPLEX, 0.45, farbe if linie else (90, 90, 90), 1, cv2.LINE_AA)
for nr, pos in takte_pos.items():
    x = min(p[0] for p in pos)
    if x0 <= x <= x1 and any(abs(p[1] - (ys[0] + ys[4]) / 2) < 6 * ab for p in pos):
        xx = int((x - x0 - 14) * f) + rand
        cv2.line(g, (xx, 2), (xx, 26), (0, 0, 200), 2)
        cv2.putText(g, nr, (xx + 4, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 200), 2, cv2.LINE_AA)
cv2.imwrite(f'/tmp/omr/gt/{name}.png', g)
print(name, 'ok')
