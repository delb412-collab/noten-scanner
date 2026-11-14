"""Prüft in einer MusicXML-Datei jeden Takt: passt die Summe der Notenwerte zur Taktart?"""
import sys
from fractions import Fraction
import music21 as m21

def pruefen(pfad, zeigen=True):
    s = m21.converter.parse(pfad)
    ergebnis = []
    for teil in s.parts:
        takte = list(teil.getElementsByClass(m21.stream.Measure))
        for i, t in enumerate(takte):
            ts = t.getContextByClass(m21.meter.TimeSignature)
            soll = Fraction(ts.barDuration.quarterLength).limit_denominator(64) if ts else None
            stimmen = t.voices or [t]
            # Dauer = Summe der Elemente (ohne Akkord-Doppelzählung)
            ist2 = []
            for v in stimmen:
                summe = Fraction(0)
                for el in v.flatten().notesAndRests:
                    summe += Fraction(el.duration.quarterLength).limit_denominator(64)
                ist2.append(summe)
            ok = soll is not None and all(x == soll for x in ist2)
            rand = i == 0 or i == len(takte) - 1
            ergebnis.append((t.number, str(ts.ratioString if ts else '?'), [str(x) for x in ist2], str(soll), ok, rand))
    if zeigen:
        n = len(ergebnis); gut = sum(e[4] for e in ergebnis)
        print(f"{pfad}: {n} Takte, {gut} passen ({100*gut/max(n,1):.0f} %)")
        for e in ergebnis:
            if not e[4]:
                print(f"   Takt {e[0]:>3} ({e[1]}): Ist {e[2]} Soll {e[3]}{'  (Auftakt/Schluss?)' if e[5] else ''}")
    return ergebnis

if __name__ == "__main__":
    for p in sys.argv[1:]:
        pruefen(p)
