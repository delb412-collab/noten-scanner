"""Prüfstand für die Notenerkennung.

Textformat (eine Zeile je Takt):   <id>: <Stimme 1> || <Stimme 2>
  Ton  = Tonname + Vorzeichen (klingend, also mit Tonart und Taktvorzeichen) + Oktave, z. B. F#4, Bb4, Cn5 = C
  Wert = 1 2 4 8 16 32 (+ "." für punktiert), Pause = r:4, Akkord = E4+C5:8, Vorschlag = ...:8g
  Artikulation (optional, angehängt): _st Staccato, _sz Staccatissimo (Keil), _te Tenuto, _ak Akzent (>),
  _ma Marcato (^), _fe Fermate – z. B. C5:8_st oder G4:4_te_st. Eine Grundwahrheit mit der Zeile
  "# Artikulationen erfasst" wird zusätzlich auf Artikulationen geprüft.
  "#" am Zeilenanfang = Kommentar, "Z<n>" = neue Notenzeile
Befehle:
  export <musicxml>             -> Text der Erkennung (Entwurf für die Grundwahrheit)
  vergleich <wahrheit.txt> <musicxml oder txt>   -> Kennzahlen
"""
import re, sys
import xml.etree.ElementTree as ET

ARTIKULATION = {"staccato": "st", "staccatissimo": "sz", "spiccato": "sz", "tenuto": "te", "accent": "ak",
                "strong-accent": "ma", "detached-legato": "te"}
DAUER = {"whole": "1", "half": "2", "quarter": "4", "eighth": "8", "16th": "16", "32nd": "32", "64th": "64"}
WERT = {"1": 4.0, "2": 2.0, "4": 1.0, "8": 0.5, "16": 0.25, "32": 0.125, "64": 0.0625}

def tonname(step, alter, octave):
    return step + {0: "", 1: "#", 2: "##", -1: "b", -2: "bb"}[alter] + str(octave)

def export(pfad):
    baum = ET.parse(pfad)
    zeilen = []
    ueberspringen = 0
    wiederholung = False
    for takt in baum.getroot().iter("measure"):
        if ueberspringen > 0:                      # von einer Mehrtaktpause abgedeckt
            ueberspringen -= 1
            continue
        for el in takt:
            if el.tag == "print" and el.get("new-system") == "yes":
                zeilen.append("Z")
        stimmen = {}
        reihenfolge = []
        for mr in takt.findall("attributes/measure-style/measure-repeat"):
            wiederholung = mr.get("type") == "start"   # Faulenzer bis zum Stopp
        if wiederholung:
            zeilen.append(f"{takt.get('number')}: simile:1")
            continue
        mehr = takt.find("attributes/measure-style/multiple-rest")
        if mehr is not None:                       # Mehrtaktpause
            zeilen.append(f"{takt.get('number')}: r:m{mehr.text}")
            ueberspringen = int(mehr.text) - 1
            continue
        for el in takt.iter("note"):
            v = el.findtext("voice") or "1"
            if v not in stimmen: stimmen[v] = []; reihenfolge.append(v)
            p = el.find("pitch")
            typ = DAUER.get(el.findtext("type"), "?") + "." * len(el.findall("dot"))
            if el.find("rest") is not None and el.find("rest").get("measure") == "yes" and typ == "?":
                typ = "1"                          # Ganztaktpause ohne Notenwert
            if el.find("time-modification") is not None:
                typ += "t" + el.findtext("time-modification/actual-notes")
            g = "g" if el.find("grace") is not None else ""
            arten = sorted({ARTIKULATION[a.tag] for a in el.iterfind("notations/articulations/*") if a.tag in ARTIKULATION}
                           | ({"fe"} if el.find("notations/fermata") is not None else set()))
            g += "".join("_" + a for a in arten)
            name = "r" if el.find("rest") is not None or p is None else tonname(p.findtext("step"), int(p.findtext("alter") or 0), p.findtext("octave"))
            if el.find("chord") is not None and stimmen[v]:
                ton, w = stimmen[v][-1].split(":")
                w0, *a0 = w.split("_")
                w1, *a1 = (f"{typ}{g}").split("_")
                stimmen[v][-1] = ton + "+" + name + ":" + w0 + "".join("_" + a for a in sorted(set(a0) | set(a1)))
            else:
                stimmen[v].append(f"{name}:{typ}{g}")
        zeilen.append(f"{takt.get('number')}: " + " || ".join(" ".join(stimmen[v]) for v in reihenfolge))
    # Z vor den ersten Takt einer neuen Zeile setzen
    text, z = [], 1
    text.append("Z1")
    for t in zeilen:
        if t == "Z": z += 1; text.append(f"Z{z}")
        else: text.append(t)
    return "\n".join(text)

def lesen_text(text):
    takte = []
    zeile = 0
    for roh in text.splitlines():
        s = re.sub(r"(^|\s)#(\s.*|$)", "", roh).strip()      # Kommentar: "#" frei stehend (nicht F#4)
        if not s: continue
        if re.fullmatch(r"Z\d+", s): zeile += 1; continue
        tid, inhalt = s.split(":", 1)
        inhalt = inhalt.strip()
        if inhalt == "LEER": continue            # in der Grundwahrheit: Takt gibt es nicht
        if inhalt.startswith("?"):                # unsicher: wird zugeordnet, aber nicht gewertet
            takte.append(dict(id=tid.strip(), zeile=zeile, stimmen=[["?"]], unsicher=True)); continue
        stimmen = [st.split() for st in inhalt.split("||")] if inhalt else [["leer"]]  # leerer Takt
        takte.append(dict(id=tid.strip(), zeile=zeile, stimmen=stimmen))
    return takte

def norm(token):
    """Vergleichsform: Töne eines Akkords sortiert, 'n' (Auflösung) entfernt."""
    if ":" not in token: return token
    token = token.split("_")[0]                 # Artikulationen zählen hier nicht (eigene Auswertung)
    if token == "r:m1": return "r:1"            # Mehrtaktpause „1“ = Ganztaktpause
    ton, w = token.rsplit(":", 1)
    if w.endswith("g"): w = "g"                 # Vorschlag: Notenwert zählt nicht
    toene = sorted(t.replace("n", "") for t in ton.split("+"))
    return "+".join(toene) + ":" + w

def editierdistanz(a, b):
    """Levenshtein mit Pfad; liefert (Treffer, Ersetzungen, Löschungen, Einfügungen)."""
    n, m = len(a), len(b)
    D = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1): D[i][0] = i
    for j in range(m + 1): D[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            D[i][j] = min(D[i - 1][j] + 1, D[i][j - 1] + 1, D[i - 1][j - 1] + (a[i - 1] != b[j - 1]))
    i, j, tr, er, lo, ei = n, m, 0, 0, 0, 0
    while i > 0 or j > 0:
        if i > 0 and j > 0 and D[i][j] == D[i - 1][j - 1] + (a[i - 1] != b[j - 1]):
            if a[i - 1] == b[j - 1]: tr += 1
            else: er += 1
            i, j = i - 1, j - 1
        elif i > 0 and D[i][j] == D[i - 1][j] + 1: lo += 1; i -= 1
        else: ei += 1; j -= 1
    return tr, er, lo, ei

def takt_tokens(t):
    return [norm(x) for st in t["stimmen"] for x in st]

def takt_vergleich(a, b):
    """(Treffer, Ersetzt, Fehlend, Extra) für zwei Takte. Mehrstimmig: Reihenfolge und
    Stimmzuordnung egal (Vergleich als Menge), einstimmig: Editierdistanz."""
    A, B = takt_tokens(a), takt_tokens(b)
    if len(a["stimmen"]) > 1 or len(b["stimmen"]) > 1:
        from collections import Counter
        gemeinsam = sum((Counter(A) & Counter(B)).values())
        er = min(len(A), len(B)) - gemeinsam
        return gemeinsam, er, max(0, len(A) - len(B)), max(0, len(B) - len(A))
    return editierdistanz(A, B)

def vergleich(wahr, erk, ausfuehrlich=True):
    """Takte per Ausrichtung zuordnen (Kosten = Notendistanz), dann Noten zählen."""
    W, E = wahr, erk
    n, m = len(W), len(E)
    INF = 10 ** 9
    kosten = lambda a, b: 0 if a.get("unsicher") else sum(takt_vergleich(a, b)[1:])
    leer = lambda t: len(takt_tokens(t))
    D = [[INF] * (m + 1) for _ in range(n + 1)]; P = [[None] * (m + 1) for _ in range(n + 1)]
    D[0][0] = 0
    for i in range(n + 1):
        for j in range(m + 1):
            if i < n and D[i][j] + leer(W[i]) < D[i + 1][j]: D[i + 1][j], P[i + 1][j] = D[i][j] + leer(W[i]), (i, j, "fehlt")
            if j < m and D[i][j] + max(1, leer(E[j])) < D[i][j + 1]: D[i][j + 1], P[i][j + 1] = D[i][j] + max(1, leer(E[j])), (i, j, "extra")
            if i < n and j < m:
                k = D[i][j] + kosten(W[i], E[j])
                if k < D[i + 1][j + 1]: D[i + 1][j + 1], P[i + 1][j + 1] = k, (i, j, "paar")
    paare, i, j = [], n, m
    while i or j:
        pi, pj, art = P[i][j]; paare.append((art, pi, pj)); i, j = pi, pj
    paare.reverse()
    noten = sum(len(takt_tokens(t)) for t in W if not t.get("unsicher"))
    tr = er = lo = ei = 0; takte_ok = 0; extra_takte = 0; fehlende_takte = 0; fehler = []
    for art, i, j in paare:
        if art == "paar" and W[i].get("unsicher"):
            continue
        if art == "fehlt" and W[i].get("unsicher"):
            continue
        if art == "paar":
            a, b, c, d = takt_vergleich(W[i], E[j])
            tr += a; er += b; lo += c; ei += d
            if b + c + d == 0: takte_ok += 1
            else: fehler.append(f"Takt {W[i]['id']:>4} (Zeile {W[i]['zeile']}): richtig {' '.join(takt_tokens(W[i]))}\n{'':24}gelesen {' '.join(takt_tokens(E[j]))}")
        elif art == "fehlt":
            lo += len(takt_tokens(W[i])); fehlende_takte += 1
            fehler.append(f"Takt {W[i]['id']:>4} (Zeile {W[i]['zeile']}): FEHLT ganz")
        else:
            ei += len(takt_tokens(E[j])); extra_takte += 1
            fehler.append(f"Extra-Takt {E[j]['id']} gelesen: {' '.join(takt_tokens(E[j]))}")
    n = sum(1 for t in W if not t.get("unsicher"))
    erg = dict(noten=noten, richtig=tr, ersetzt=er, fehlend=lo, extra=ei, takte=n, takte_ok=takte_ok,
               extra_takte=extra_takte, fehlende_takte=fehlende_takte)
    if ausfuehrlich:
        for f in fehler: print("  " + f)
    return erg

def artikulationen(token):
    return set(token.split("_")[1:]) if ":" in token else set()


def artikulationen_vergleichen(wahr, erk, ausfuehrlich=True):
    """Artikulationen der Noten, die in beiden gleich gelesen wurden (gleiche Takte per Ausrichtung,
    darin gleiche Noten). Rückgabe je Kürzel: richtig, fehlt, zu viel."""
    import difflib
    from collections import Counter
    paare = takt_paare(wahr, erk)
    zaehler = {"richtig": Counter(), "fehlt": Counter(), "zuviel": Counter()}
    fehler = []
    for i, j in paare:
        a = [x for st in wahr[i]["stimmen"] for x in st]
        b = [x for st in erk[j]["stimmen"] for x in st]
        sm = difflib.SequenceMatcher(a=[norm(x) for x in a], b=[norm(x) for x in b], autojunk=False)
        for block in sm.get_matching_blocks():
            for k in range(block.size):
                ta, tb = a[block.a + k], b[block.b + k]
                ga, gb = artikulationen(ta), artikulationen(tb)
                for c in ga & gb: zaehler["richtig"][c] += 1
                for c in ga - gb: zaehler["fehlt"][c] += 1
                for c in gb - ga: zaehler["zuviel"][c] += 1
                if ga != gb:
                    fehler.append(f"Takt {wahr[i]['id']:>4}: {ta:<14} gelesen {tb}")
    if ausfuehrlich:
        for f in fehler: print("  " + f)
    return zaehler


def takt_paare(W, E):
    """Zugeordnete Taktpaare (Index Wahrheit, Index Erkennung) wie in vergleich()."""
    n, m = len(W), len(E)
    INF = 10 ** 9
    kosten = lambda a, b: 0 if a.get("unsicher") else sum(takt_vergleich(a, b)[1:])
    leer = lambda t: len(takt_tokens(t))
    D = [[INF] * (m + 1) for _ in range(n + 1)]; P = [[None] * (m + 1) for _ in range(n + 1)]
    D[0][0] = 0
    for i in range(n + 1):
        for j in range(m + 1):
            if i < n and D[i][j] + leer(W[i]) < D[i + 1][j]: D[i + 1][j], P[i + 1][j] = D[i][j] + leer(W[i]), (i, j, "f")
            if j < m and D[i][j] + max(1, leer(E[j])) < D[i][j + 1]: D[i][j + 1], P[i][j + 1] = D[i][j] + max(1, leer(E[j])), (i, j, "e")
            if i < n and j < m:
                k = D[i][j] + kosten(W[i], E[j])
                if k < D[i + 1][j + 1]: D[i + 1][j + 1], P[i + 1][j + 1] = k, (i, j, "p")
    paare, i, j = [], n, m
    while i or j:
        pi, pj, art = P[i][j]
        if art == "p" and not W[pi].get("unsicher"): paare.append((pi, pj))
        i, j = pi, pj
    return list(reversed(paare))


def artikulationsbericht(name, z):
    alle = set(z["richtig"]) | set(z["fehlt"]) | set(z["zuviel"])
    r, f, x = sum(z["richtig"].values()), sum(z["fehlt"].values()), sum(z["zuviel"].values())
    teile = [f"{c}: {z['richtig'][c]} richtig, {z['fehlt'][c]} fehlen, {z['zuviel'][c]} zu viel" for c in sorted(alle)]
    print(f"{name}: Artikulationen {r}/{r + f} gefunden = {100 * r / max(1, r + f):.1f} %, {x} falsch dazu  | " + " · ".join(teile))


def bericht(name, e):
    q = 100 * e["richtig"] / max(1, e["noten"])
    f = 100 * (e["ersetzt"] + e["fehlend"] + e["extra"]) / max(1, e["noten"])
    print(f"{name}: {e['richtig']}/{e['noten']} Noten richtig = {q:.1f} %  | Fehlerquote {f:.1f} % "
          f"(falsch {e['ersetzt']}, fehlend {e['fehlend']}, zusätzlich {e['extra']}) | "
          f"Takte fehlerfrei {e['takte_ok']}/{e['takte']}, zusätzliche Takte {e['extra_takte']}, fehlende Takte {e['fehlende_takte']}")

if __name__ == "__main__":
    if sys.argv[1] == "export":
        print(export(sys.argv[2]))
    else:
        wahr = lesen_text(open(sys.argv[2]).read())
        quelle = sys.argv[3]
        erk = lesen_text(export(quelle) if quelle.endswith(".musicxml") else open(quelle).read())
        bericht(sys.argv[3].split("/")[-1], vergleich(wahr, erk, ausfuehrlich=len(sys.argv) < 5))
        if "# Artikulationen erfasst" in open(sys.argv[2]).read():
            ausf = len(sys.argv) < 5 or sys.argv[4] == "artikulation"
            artikulationsbericht(sys.argv[3].split("/")[-1], artikulationen_vergleichen(wahr, erk, ausf))
