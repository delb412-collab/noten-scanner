"""Notenleser: homr (Notenerkennung, AGPL) mit eigenen Ergänzungen, im selben Prozess ausgeführt.

Ergänzungen:
  1. Schlüssel-Vorgabe: Steht am Zeilenanfang kein üblicher Schlüssel, bekommt der Decoder den
     Schlüssel der vorigen Zeile als erstes Zeichen vorgegeben (sonst liest er Töne in einem
     erfundenen Alt-/Tenor-/Bassschlüssel).
  2. Taktprüfung mit Alternativen: Geht ein Takt nicht auf, wird die Zeile ab der unsichersten
     passenden Stelle mit dem zweitwahrscheinlichsten Notenwert neu gelesen. Übernommen wird das
     nur, wenn der Takt danach aufgeht und in der Zeile nicht mehr Takte falsch sind als vorher.
  3. Ungültige Zeichen (Note ohne Tonhöhe) werden durch die beste gültige Alternative ersetzt.
  4. Tonartwechsel mitten in der Zeile: Vorzeichen der alten Tonart werden für den Rest der Zeile
     auf die neue Tonart umgerechnet.
  5. Leere Takte (entstehen an Wiederholungszeichen/Volten) werden entfernt.
"""
import sys, time, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collections import Counter, defaultdict
from fractions import Fraction
import numpy as np
import xml.etree.ElementTree as ET

from homr.main import ProcessingConfig, detect_staffs_in_image
from homr.system_repair import repair_systems
from homr.staff_parsing import parse_staffs
from homr.music_xml_generator import XmlGeneratorArguments, generate_xml
from homr.transformer.configs import Config
from homr.transformer import decoder_inference as di
from homr.transformer import staff2score as s2s
from homr.transformer.vocabulary import EncodedSymbol, nonote, empty

ZUSTAND = {"schluessel": None, "aktiv": True, "protokoll": []}
UEBLICHE_SCHLUESSEL = {"clef_G2", "clef_F4"}
TAKTSTRICHE = ("barline", "doublebarline", "bolddoublebarline", "repeatStart", "repeatEnd", "repeatEndStart")
ABSCHNITT = ("doublebarline", "bolddoublebarline", "repeatStart", "repeatEnd", "repeatEndStart")


def _softmax(v):
    v = v.astype(np.float64)
    v = np.exp(v - v.max())
    return v / v.sum()


def _top(p, inv, k):
    idx = np.argsort(p)[::-1][:k]
    return [(inv[int(i)], float(p[i])) for i in idx]


def protokoll(text):
    ZUSTAND["protokoll"].append(text)


def _zugabe(standard_erstes=True):
    """Prüfungen am Bild sind eine Zugabe: Ein Fehler darin darf die Erkennung nie abbrechen. Die
    Funktion gibt dann ihr erstes Argument (die Zeichenliste) unverändert bzw. None zurück."""
    def huelle(funktion):
        def aufruf(*args, **kwargs):
            try:
                return funktion(*args, **kwargs)
            except Exception as fehler:
                meldung = f"Prüfung {funktion.__name__} übersprungen: {type(fehler).__name__}: {fehler}"
                if meldung not in ZUSTAND["protokoll"]:
                    protokoll(meldung)
                return args[0] if standard_erstes and args else None
        aufruf.__name__ = funktion.__name__
        aufruf.__doc__ = funktion.__doc__
        return aufruf
    return huelle


# ----------------------------------------------------------------------------------------------
# Decoder: gieriges Dekodieren (wie homr) + Vorgaben + Alternativen
# ----------------------------------------------------------------------------------------------
def generate(self, start_tokens, nonote_tokens, vorgabe=None, schluessel_vorgabe=True, **kwargs):
    """vorgabe: Liste von Token-Tupeln (rhythm, pitch, lift, articulation, slur, position), die für
    die ersten Schritte statt der eigenen Wahl genommen werden (Lehrerzwang)."""
    vorgabe = vorgabe or []
    if len(start_tokens.shape) == 1:
        start_tokens = start_tokens[None, :]
    out = {k: (start_tokens if k == "r" else nonote_tokens) for k in "rplas"}
    cache, kv_input_names, kv_output_names = self.init_cache()
    output_names = self.output_names + kv_output_names
    context = kwargs["context"]
    context_reduced = kwargs["context"][:, :1]
    symbols = []
    for step in range(self.max_seq_len):
        context = context if step == 0 else context_reduced
        self.io_binding.bind_cpu_input("rhythms", out["r"][:, -1:])
        self.io_binding.bind_cpu_input("pitchs", out["p"][:, -1:])
        self.io_binding.bind_cpu_input("lifts", out["l"][:, -1:])
        self.io_binding.bind_cpu_input("articulations", out["a"][:, -1:])
        self.io_binding.bind_cpu_input("slurs", out["s"][:, -1:])
        self.io_binding.bind_cpu_input("context", context)
        self.io_binding.bind_cpu_input("cache_len", np.array([step], dtype=np.int64))
        for name, cache_val in zip(kv_input_names, cache, strict=True):
            self.io_binding.bind_ortvalue_input(name, cache_val)
        for name in output_names:
            self.io_binding.bind_output(name, "cpu", self.device_id)
        self.net.run_with_iobinding(iobinding=self.io_binding)
        outputs = self.io_binding.get_outputs()
        cache = outputs[7:]
        rp, pp, lp, posp, ap, sp, attention = (o.numpy() for o in outputs[:7])
        p_r, p_p, p_l = _softmax(rp[0, -1]), _softmax(pp[0, -1]), _softmax(lp[0, -1])
        wahl = [int(p_r.argmax()), int(p_p.argmax()), int(p_l.argmax()),
                int(ap[0, -1].argmax()), int(sp[0, -1].argmax()), int(posp[0, -1].argmax())]
        vorgegeben = False
        if step < len(vorgabe):
            wahl, vorgegeben = list(vorgabe[step]), True
        elif (step == 0 and schluessel_vorgabe and ZUSTAND["aktiv"] and ZUSTAND["schluessel"]
              and self.inv_rhythm_vocab[wahl[0]] not in UEBLICHE_SCHLUESSEL):
            c = self.config
            wahl = [c.rhythm_vocab[ZUSTAND["schluessel"]], c.pitch_vocab[empty], c.lift_vocab[empty],
                    c.articulation_vocab[empty], c.slur_vocab[empty], c.position_vocab["upper"]]
            vorgegeben = True
            protokoll(f"Schlüssel {ZUSTAND['schluessel']} am Zeilenanfang vorgegeben")
        if wahl[0] == self.eos_token:
            break
        symbol = EncodedSymbol(
            rhythm=self.inv_rhythm_vocab[wahl[0]], pitch=self.inv_pitch_vocab[wahl[1]],
            lift=self.inv_lift_vocab[wahl[2]], articulation=self.inv_articulation_vocab[wahl[3]],
            slur=self.inv_slur_vocab[wahl[4]], position=self.inv_position_vocab[wahl[5]],
            coordinates=attention)
        symbol.alt_rhythm = _top(p_r, self.inv_rhythm_vocab, 8)
        symbol.alt_pitch = _top(p_p, self.inv_pitch_vocab, 4)
        symbol.alt_lift = _top(p_l, self.inv_lift_vocab, 3)
        symbol.p_rhythm = float(p_r[wahl[0]])
        symbol.tokens = tuple(wahl)
        symbol.vorgegeben = vorgegeben
        symbols.append(symbol)
        for k, w in zip("rplas", wahl[:5]):
            out[k] = np.concatenate((out[k], np.array([[w]], dtype=np.int64)), axis=-1)
    return symbols


di.ScoreDecoder.generate = generate

import homr.main as _hm
import homr.music_xml_generator as _mxg
_bildposition_original = _mxg.build_image_position


def _bildposition_mit_sicherheit(xml, symbol):
    """Wie homr (imgpos-Kommentar), dazu ein Kommentar mit der Sicherheit des Decoders und
    eventuellen automatischen Korrekturen – die App markiert damit unsichere Takte."""
    _bildposition_original(xml, symbol)
    r = getattr(symbol, "p_rhythm", None)
    p = next((w for t, w in getattr(symbol, "alt_pitch", []) if t == symbol.pitch), None)
    teile = []
    if r is not None:
        teile.append(f"r={r:.2f}")
    if p is not None:
        teile.append(f"p={p:.2f}")
    if getattr(symbol, "kopf_relativ", None) is not None:
        teile.append(f"k={symbol.kopf_relativ:.2f}")
    if getattr(symbol, "faulenzer", None):
        teile.append("faulenzer=" + symbol.faulenzer)
    if getattr(symbol, "korrektur", None):
        teile.append("korrektur=" + symbol.korrektur)
    if getattr(symbol, "unsicher", None):
        teile.append("unsicher=" + symbol.unsicher)
    if teile:
        xml.append(ET.Comment(" sicher: " + "; ".join(teile) + " "))


_mxg.build_image_position = _bildposition_mit_sicherheit
_ganztakt_original = _mxg.build_measure_rest
_mehrtakt_original = _mxg.build_multi_measure_rest


def _ganztaktpause_mit_position(symbol, state):
    note = _ganztakt_original(symbol, state)
    _bildposition_mit_sicherheit(note, symbol)
    return note


def _mehrtaktpause_mit_position(symbol, attributes):
    _mehrtakt_original(symbol, attributes)
    stil = attributes.find("measure-style")
    if stil is not None and not any(k.tag is ET.Comment for k in stil):
        _bildposition_mit_sicherheit(stil, symbol)


_mxg.build_measure_rest = _ganztaktpause_mit_position
_mxg.build_multi_measure_rest = _mehrtaktpause_mit_position
_predict_symbols_original = _hm.predict_symbols


def _predict_symbols_merken(debug, predictions):
    ZUSTAND["kopf_maske"] = (predictions.notehead > 0).astype(np.uint8)
    return _predict_symbols_original(debug, predictions)


_hm.predict_symbols = _predict_symbols_merken

import homr.staff_parsing as _sp
_staff_bild_original = _sp.prepare_staff_image


def _staff_bild_merken(*args, **kwargs):
    """Wie homr; merkt sich die Abbildung Zeilenbild -> Seite, damit die Prüfungen am Bild schon
    während des Lesens einer Zeile wissen, wo ein Zeichen auf der Seite steht."""
    staff_image, transformed_staff, staff_to_page = _staff_bild_original(*args, **kwargs)
    ZUSTAND["zeile_zu_seite"] = staff_to_page
    return staff_image, transformed_staff, staff_to_page


_sp.prepare_staff_image = _staff_bild_merken


def _bildpos(s):
    """Lage eines Zeichens im Eingabebild (so wie homr sie später als imgpos ausgibt) oder None."""
    abbildung, zu_eingabe = ZUSTAND.get("zeile_zu_seite"), ZUSTAND.get("zu_eingabe")
    if abbildung is None or zu_eingabe is None:
        return None
    mitte = _sp._get_symbol_center(s)
    if mitte is None:
        return None
    x, y = zu_eingabe(abbildung(mitte))
    return float(x), float(y)


def _taktlaengen(voice):
    """Wie homr (Taktlänge je Taktangabe = Median der Takte bis zur nächsten Taktangabe), aber
    Mehrtaktpausen und Ganztaktpausen zählen nicht mit – homr rechnet sie als ganze Note, das
    verfälscht in 2/4- oder 3/4-Stücken die Taktart."""
    gefiltert = []
    for akkord in voice:
        if any(x.rhythm.startswith("rest_") and x.rhythm.endswith("m") or x.rhythm == "measureRest"
               for x in akkord.symbols):
            continue
        gefiltert.append(akkord)
    return _taktlaengen_original(gefiltert)


_taktlaengen_original = _mxg.find_division_and_time_signature_nominators
_mxg.find_division_and_time_signature_nominators = _taktlaengen

import homr.transformer.vocabulary as _voc


def _triolen_pruefen(akkorde):
    """Ersatz für homrs _fix_over_eager_tuplets: homr entfernt Triolen in jedem Takt, der kürzer ist
    als der typische Takt des ganzen Stücks – bei Taktwechseln (Triole im 2/4-Takt eines 4/4-Stücks)
    und Auftakten falsch. Hier: typische Taktlänge je Taktart-Abschnitt, und Triolen fallen nur weg,
    wenn der Takt danach genau diese Länge hat."""
    takte = _voc._group_into_measures(akkorde)
    abschnitte, aktuell = [], []
    for takt in takte:
        if any(a and a[0].rhythm.startswith("timeSignature") for a in takt) and aktuell:
            abschnitte.append(aktuell)
            aktuell = []
        aktuell.append(takt)
    if aktuell:
        abschnitte.append(aktuell)
    ergebnis = []
    for abschnitt in abschnitte:
        dauern = [_voc._get_duration_of_measure(t) for t in abschnitt
                  if not any(s.rhythm.endswith("m") and s.rhythm.startswith("rest") or s.rhythm == "measureRest"
                             for a in t for s in a)]
        dauern = [d for d in dauern if d > 0]
        typisch = sorted(dauern)[len(dauern) // 2] if dauern else Fraction(0)
        for takt in abschnitt:
            d = _voc._get_duration_of_measure(takt)
            if 0 < d < typisch:
                ohne = _voc._remove_tuplets(takt)
                if _voc._get_duration_of_measure(ohne) == typisch:
                    protokoll("Triolen entfernt (Takt geht ohne sie auf)")
                    takt = ohne
            ergebnis.append(takt)
    return _voc._flatten_measures(ergebnis)


_voc._fix_over_eager_tuplets = _triolen_pruefen


# ----------------------------------------------------------------------------------------------
# Taktprüfung je Zeile
# ----------------------------------------------------------------------------------------------
def _dauer(s):
    if s.rhythm.startswith(("note", "rest")) and "G" not in s.rhythm.split("_")[1]:
        return s.get_duration().fraction
    return Fraction(0)


def _ist_vorschlag(s):
    return s.rhythm.startswith("note") and "G" in s.rhythm.split("_")[1]


def takte_der_zeile(symbole):
    """Liste von (start, ende) Indexbereichen je Takt (ende = Index des Taktstrichs oder len)."""
    takte, start = [], 0
    for i, s in enumerate(symbole):
        if s.rhythm.startswith(TAKTSTRICHE):
            takte.append((start, i)); start = i + 1
    if start < len(symbole):
        takte.append((start, len(symbole)))
    return takte


def takt_dauer(symbole, a, e):
    """Dauer eines Taktes; Akkorde ("chord" verbindet gleichzeitige Noten) zählen einmal (kürzeste)."""
    gesamt, i, mehrstimmig, mehrtakt = Fraction(0), a, False, False
    while i < e:
        s = symbole[i]
        if s.rhythm.startswith("rest_") and s.rhythm.endswith("m"):
            mehrtakt = True
        if s.rhythm.startswith(("note", "rest")):
            d = _dauer(s)
            j = i
            while j + 2 < e and symbole[j + 1].rhythm == "chord":
                j += 2
                if symbole[j].position.endswith("2") or symbole[j].position.startswith("lower"):
                    mehrstimmig = True
                dj = _dauer(symbole[j])
                if dj > 0 and (d == 0 or dj < d):
                    d = dj
            gesamt += d
            i = j + 1
        else:
            i += 1
    zeichen = [x for x in symbole[a:e] if x.rhythm.startswith(("note", "rest"))]
    if len(zeichen) == 1 and zeichen[0].rhythm == "rest_1":
        mehrtakt = True                                # Ganztaktpause: füllt jeden Takt, gleich welcher Taktart
    if any(x.rhythm == "measureRest" for x in symbole[a:e]):
        mehrtakt = True
    return gesamt, mehrstimmig, mehrtakt


def taktart_abschnitte(symbole, takte):
    """Je Takt: Nummer des Taktart-Abschnitts in der Zeile (0 = vor der ersten Taktangabe der
    Zeile) und ob direkt vor seinen Noten eine Taktangabe steht."""
    abschnitt, nach, nr = [], [], 0
    for a, e in takte:
        erste = next((i for i in range(a, e) if symbole[i].rhythm.startswith(("note", "rest"))), e)
        neu = any(symbole[i].rhythm.startswith("timeSignature") for i in range(a, erste))
        if neu:
            nr += 1
        abschnitt.append(nr)
        nach.append(neu)
    return abschnitt, nach


def _haeufigste(werte):
    return Counter(werte).most_common(1)[0] if werte else (None, 0)


def soll_festlegen(symbole):
    """Soll-Taktdauer je Taktart-Abschnitt der Zeile (gemerkt in ZUSTAND["zeilen_soll"]).
    Abschnitt 0 (vor der ersten Taktangabe der Zeile) übernimmt die Taktdauer vom Ende der vorigen
    Zeile – außer mindestens drei Takte sagen deutlich etwas anderes. Ein Abschnitt nach einer
    Taktangabe nimmt die häufigste Dauer seiner Takte (ohne den ersten Takt nach der Angabe; ist das
    der einzige Takt, gilt seine Dauer). Ohne Vorgänger zählt wie bisher auch die Seite mit."""
    takte = takte_der_zeile(symbole)
    abschnitt, nach = taktart_abschnitte(symbole, takte)
    dauern = [takt_dauer(symbole, a, e) for a, e in takte]
    seite = ZUSTAND.setdefault("taktdauern", Counter())
    geerbt = ZUSTAND.get("soll_zuletzt")
    soll = {}
    letzter = len(takte) - 1
    for nr in sorted(set(abschnitt)):
        ks = [k for k in range(len(takte)) if abschnitt[k] == nr]
        gueltig = [k for k in ks if dauern[k][0] > 0 and not dauern[k][1] and not dauern[k][2]]
        if not gueltig:
            soll[nr] = geerbt if nr == 0 else None
            continue
        innen = [k for k in gueltig if 0 < k < letzter and not nach[k]]
        werte = [dauern[k][0] for k in (innen or gueltig)]
        haeufig, anzahl = _haeufigste(werte)
        seite.update(dauern[k][0] for k in innen)
        if nr == 0:
            if geerbt is not None and not (anzahl >= 3 and haeufig != geerbt):
                soll[nr] = geerbt
            elif anzahl >= 3 or not seite:
                soll[nr] = haeufig
            elif seite:
                soll[nr] = seite.most_common(1)[0][0]
            else:
                soll[nr] = haeufig if anzahl >= 2 else None
        else:
            ohne_ersten = [dauern[k][0] for k in gueltig if not nach[k]]
            alle = [dauern[k][0] for k in gueltig]
            h1, n1 = _haeufigste(ohne_ersten)
            h2, n2 = _haeufigste(alle)
            vorher = soll.get(nr - 1, geerbt)
            if n1 >= 2:
                soll[nr] = h1
            elif n2 >= 2:
                soll[nr] = h2
            elif len(alle) == 1:
                soll[nr] = alle[0]                     # einziger Takt bestimmt die Taktart
            elif vorher in alle:
                soll[nr] = vorher
            else:
                soll[nr] = None                        # unklar: lieber nichts ändern
    ZUSTAND["zeilen_soll"] = soll
    if ZUSTAND.get("debug"):
        protokoll(f"  [debug] Zeile {ZUSTAND.get('zeile')}: Soll je Abschnitt {soll} (geerbt {geerbt}), Takte " +
                  " ".join(f"{d[0]}{'*' if n else ''}" for d, n in zip(dauern, nach)))
    return soll


def takt_soll(symbole):
    """Soll-Dauer je Takt der Zeile (None = unbekannt)."""
    takte = takte_der_zeile(symbole)
    abschnitt, _ = taktart_abschnitte(symbole, takte)
    soll = ZUSTAND.get("zeilen_soll") or {}
    return [soll.get(nr) for nr in abschnitt]


def soll_merken(symbole):
    """Taktdauer am Zeilenende für die nächste Zeile merken."""
    werte = [x for x in takt_soll(symbole) if x is not None]
    if werte:
        ZUSTAND["soll_zuletzt"] = werte[-1]


def schlechte_takte(symbole, soll=None):
    """Takte, deren Dauer nicht zur Taktart passt (soll: Liste je Takt, sonst aus der Zeile).
    Erlaubt sind zu kurze Takte als Auftakt (erster Takt der Seite, Beginn eines neuen Teils,
    erster Takt nach einer Taktangabe, erster Takt nach einem Doppelstrich/Wiederholungszeichen)
    und als Ergänzung eines Auftakts vor einem Doppelstrich/Wiederholungszeichen. Der erste Takt
    nach einer Taktangabe mitten auf der Seite wird gar nicht geändert: seine Dauer bestimmt die
    neue Taktart (oft gilt sie nur für diesen einen Takt).
    Rückgabe: (Takt-Nr., Anfang, Ende, Dauer, Grenze, Soll)."""
    takte = takte_der_zeile(symbole)
    if soll is None:
        soll = takt_soll(symbole)
    _, nach = taktart_abschnitte(symbole, takte)
    auftakte = ZUSTAND.setdefault("auftakte", set())
    schlecht = []
    for k, (a, e) in enumerate(takte):
        sk = soll[k] if k < len(soll) else None
        d, mehr, mt = takt_dauer(symbole, a, e)
        if sk is None or d == 0 or mehr or mt or d == sk:
            continue
        vorher = symbole[a - 1].rhythm if a > 0 else ""
        nachher = symbole[e].rhythm if e < len(symbole) else ""
        j = a - 1
        while j > 0 and symbole[j].rhythm.startswith("volta"):
            j -= 1
            vorher = symbole[j].rhythm
        seitenanfang = ZUSTAND.get("zeile", 0) == 0 and k == 0
        neuer_teil = (k == 0 and (ZUSTAND.get("zeile", 0) == 0
                                  or ZUSTAND.get("vorige_zeile_ende", "").startswith(ABSCHNITT))) or nach[k]
        offen = e >= len(symbole)                      # Zeile endet ohne Taktstrich
        if d < sk and (neuer_teil or (vorher.startswith(ABSCHNITT) and not nachher.startswith(ABSCHNITT))):
            if not nach[k] or seitenanfang:
                auftakte.add(d)                        # Auftakt
            continue
        if nach[k] and not seitenanfang:
            continue                                   # Taktwechsel: Dauer bestimmt die neue Taktart
        if d < sk and offen:
            continue
        if d < sk and nachher.startswith(ABSCHNITT) and (sk - d) in auftakte:
            continue                                   # Ergänzung eines Auftakts
        grenze = d < sk and (vorher.startswith(ABSCHNITT) or nachher.startswith(ABSCHNITT))
        schlecht.append((k, a, e, d, grenze, sk))
    return schlecht


def _gleiche_art(alt, orig, p=1.0):
    """Notenwert-Alternative derselben Art (Note bleibt Note, Pause bleibt Pause). Vorschlagsnoten
    dürfen zu normalen Noten werden und umgekehrt (dann nur bei deutlicher Wahrscheinlichkeit).
    Mehrtaktpausen werden nicht vorgeschlagen."""
    if alt.endswith("m"):
        return False
    if "G" in alt.split("_")[-1] and (not orig.startswith("note") or p < 0.1):
        return False
    return alt.split("_")[0] == orig.split("_")[0] and alt != orig


def zeile_reparieren(decoder, context, symbole, start, nonote):
    """Taktprüfung mit Neulesen. Gibt die (eventuell) verbesserte Zeichenliste zurück."""
    if not any(x is not None for x in takt_soll(symbole)):
        return symbole
    for _durchgang in range(8):
        schlecht = schlechte_takte(symbole)
        if not schlecht:
            break
        verbessert = False
        for k, a, e, d, grenze, soll in schlecht:
            mindest = 0.1 if grenze else 0.03          # an Teil-Grenzen kann ein kurzer Takt richtig sein
            kandidaten = []
            for i in range(a, e):
                s = symbole[i]
                if not s.rhythm.startswith(("note", "rest")) or not hasattr(s, "alt_rhythm"):
                    continue
                for tok, p in s.alt_rhythm:
                    if _gleiche_art(tok, s.rhythm, p) and p >= mindest:
                        kandidaten.append((p / max(s.p_rhythm, 1e-6), p, i, tok))
            kandidaten.sort(reverse=True)
            if ZUSTAND.get("debug"):
                protokoll(f"  [debug] Zeile {ZUSTAND.get('zeile')} Takt {k}: Dauer {d} statt {soll}; Kandidaten: " +
                          ", ".join(f"{symbole[i].rhythm}->{tok}:{p:.2f}" for _, p, i, tok in kandidaten[:8]))
            bester = None
            for verh, p, i, tok in kandidaten[:8]:
                c = decoder.config
                neu_tok = (c.rhythm_vocab[tok],) + symbole[i].tokens[1:]
                vorgabe = [s.tokens for s in symbole[:i]] + [neu_tok]
                neu = decoder.generate(start, nonote, vorgabe=vorgabe, schluessel_vorgabe=False, context=context)
                nt = takte_der_zeile(neu)
                if k >= len(nt):
                    continue
                dk = takt_dauer(neu, *nt[k])[0]
                if dk == takt_soll(neu)[k] and len(schlechte_takte(neu)) < len(schlecht):
                    bester = (neu, i, tok, p)
                    break
            if bester:
                neu, i, tok, p = bester
                protokoll(f"Takt korrigiert: {symbole[i].rhythm} {symbole[i].pitch} -> {tok} (Wahrscheinlichkeit {p:.2f})")
                neu[i].korrektur = "Notenwert über die Taktprüfung geändert"
                symbole = neu
                verbessert = True
                break
            # Achtelpause, die im Bild wie eine Viertelpause aussieht?
            if soll - d == Fraction(1, 8):
                neu = pause_nach_bild(symbole, a, e, decoder.config)
                if neu is not None:
                    symbole = neu
                    verbessert = True
                    break
            # Kein Neulesen half: überzählige Pause entfernen, wenn der Takt dann genau aufgeht
            for i in (range(a, e) if not grenze else []):
                s = symbole[i]
                if s.rhythm.startswith("rest_") and d - _dauer(s) == soll and not (
                        i + 1 < len(symbole) and symbole[i + 1].rhythm == "chord") and not (
                        i > 0 and symbole[i - 1].rhythm == "chord"):
                    protokoll(f"Überzählige Pause entfernt: {s.rhythm}")
                    symbole = symbole[:i] + symbole[i + 1:]
                    for x in symbole[max(a, i - 1):i + 1]:
                        if x.rhythm.startswith(("note", "rest")):
                            x.korrektur = "Überzählige Pause entfernt"
                    verbessert = True
                    break
            if verbessert:
                break
            neu = werte_angleichen(symbole, a, e, d, soll, decoder.config)
            if neu is not None:
                symbole = neu
                verbessert = True
                break
        if not verbessert:
            break
    return symbole


@_zugabe(standard_erstes=False)
def pause_nach_bild(symbole, a, e, config):
    """Takt um eine Achtel zu kurz: Sieht genau eine als Achtelpause gelesene Pause im Bild wie eine
    Viertelpause aus (deutlich höher als die Achtelpausen der Seite), wird sie zur Viertelpause."""
    seite = ZUSTAND.get("seite")
    if seite is None:
        return None
    import bildpruefung
    achtel = ZUSTAND.setdefault("achtelpausen", [])
    kandidaten = []
    for i in range(a, e):
        x = symbole[i]
        if x.rhythm != "rest_8" or (i + 1 < len(symbole) and symbole[i + 1].rhythm == "chord") or \
                (i > 0 and symbole[i - 1].rhythm == "chord"):
            continue
        pos = _bildpos(x)
        form = bildpruefung.pausen_form(seite, *pos) if pos else None
        if form is None:
            continue
        kandidaten.append((form[0], i))
    if not kandidaten:
        return None
    hoechste = max(kandidaten)
    vergleich = achtel + [h for h, i in kandidaten if (h, i) != hoechste]
    mittel = float(np.median(vergleich)) if vergleich else 2.1
    hoch = [(h, i) for h, i in kandidaten if h >= 2.6 and h >= 1.2 * mittel]
    if ZUSTAND.get("debug"):
        protokoll(f"  [debug] Pausenform: Achtelpausen-Höhen {[round(h, 2) for h, _ in kandidaten]}, "
                  f"Vergleich {mittel:.2f}")
    if not hoch:
        return None
    h, i = max(hoch)
    alt = symbole[i]
    x = _symbol(config, "rest_4", alt.pitch, alt.lift, alt.articulation, alt.slur, alt.position, alt)
    x.alt_rhythm = getattr(alt, "alt_rhythm", [])
    x.korrektur = "Viertelpause (Form im Bild)"
    if len(hoch) > 1:
        x.unsicher = "mehrere Pausen kämen in Frage"
    protokoll(f"Pause am Bild geprüft: Achtelpause ist eine Viertelpause (Höhe {h:.1f} Linienabstände)")
    neu = list(symbole)
    neu[i] = x
    return neu


VERDOPPELT = {"64": "32", "32": "16", "16": "8", "8": "4", "4": "2"}
HALBIERT = {v: k for k, v in VERDOPPELT.items()}
TRIOLE = {"2": "3", "4": "6", "8": "12", "16": "24"}


def werte_angleichen(symbole, a, e, d, soll, config):
    """Ist ein Takt zu kurz (zu lang) und gibt es genau eine Gruppe gleich langer, aufeinander
    folgender Noten, deren Verdopplung (Halbierung) den Takt genau füllt, wird das übernommen.
    Typischer Fall: zwei Achtel unter einem Balken als Sechzehntel gelesen."""
    diff = soll - d
    def einzeln(i):
        return not (i + 1 < len(symbole) and symbole[i + 1].rhythm == "chord") and \
            not (i > 0 and symbole[i - 1].rhythm == "chord")
    laeufe, i = [], a
    while i < e:
        x = symbole[i]
        if x.rhythm.startswith("note") and not _ist_vorschlag(x) and einzeln(i):
            j = i
            while j + 1 < e and symbole[j + 1].rhythm == x.rhythm and einzeln(j + 1):
                j += 1
            laeufe.append((i, j))
            i = j + 1
        else:
            i += 1
    loesungen = []
    for i, j in laeufe:
        n, wert = j - i + 1, symbole[i].rhythm.split("_")[1]
        if n < 2:
            continue
        summe = _dauer(symbole[i]) * n
        if diff > 0 and summe == diff and wert in VERDOPPELT:
            loesungen.append((i, j, "note_" + VERDOPPELT[wert]))
        elif diff < 0 and summe / 2 == -diff and wert in HALBIERT:
            loesungen.append((i, j, "note_" + HALBIERT[wert]))
        if diff < 0 and n % 3 == 0 and summe / 3 == -diff and wert in TRIOLE:
            loesungen.append((i, j, "note_" + TRIOLE[wert]))
    if len(loesungen) != 1:
        return None
    i, j, neu_rhythmus = loesungen[0]
    neu = list(symbole)
    for k in range(i, j + 1):
        alt = symbole[k]
        x = _symbol(config, neu_rhythmus, alt.pitch, alt.lift, alt.articulation, alt.slur, alt.position, alt)
        x.alt_rhythm, x.alt_lift = getattr(alt, "alt_rhythm", []), getattr(alt, "alt_lift", [])
        x.korrektur = "Notenwerte über die Taktlänge angeglichen"
        neu[k] = x
    protokoll(f"Notenwerte angeglichen: {j - i + 1}× {symbole[i].rhythm} -> {neu_rhythmus}")
    return neu


@_zugabe(standard_erstes=False)
def achtelpausen_sammeln(symbole):
    """Höhen sicher gelesener Achtelpausen der Seite (Vergleich für pause_nach_bild)."""
    seite, achtel = ZUSTAND.get("seite"), ZUSTAND.setdefault("achtelpausen", [])
    if seite is None or len(achtel) >= 30:
        return
    import bildpruefung
    for x in symbole:
        if x.rhythm == "rest_8" and getattr(x, "p_rhythm", 0) >= 0.8 and len(achtel) < 30:
            pos = _bildpos(x)
            form = bildpruefung.pausen_form(seite, *pos) if pos else None
            if form is not None:
                achtel.append(form[0])


def _pause(config, rhythmus, vorlage):
    """Pausenzeichen mit Lage der Vorlage; Rhythmen außerhalb des Vokabulars (z. B. rest_12m)
    bekommen für das Neulesen das nächstliegende Zeichen des Vokabulars."""
    s = EncodedSymbol(rhythmus, empty, empty, empty, empty, vorlage.position if vorlage.position != nonote else "upper",
                      coordinates=getattr(vorlage, "coordinates", None))
    token = config.rhythm_vocab.get(rhythmus, config.rhythm_vocab["rest_10m"])
    s.tokens = (token, config.pitch_vocab[empty], config.lift_vocab[empty], config.articulation_vocab[empty],
                config.slur_vocab[empty], config.position_vocab.get(s.position, config.position_vocab["upper"]))
    s.p_rhythm = 1.0
    s.alt_rhythm = getattr(vorlage, "alt_rhythm", [])
    return s


@_zugabe()
def mehrtaktpausen_bild(symbole, config):
    """Mehrtaktpausen am Bild prüfen: Ist die einzige Pause eines Taktes ein Mehrtaktbalken (dicker
    Balken auf der Mittellinie), gilt die Zahl darüber – auch wenn homr eine andere Zahl oder gar
    eine gewöhnliche Pause gelesen hat. „1“ bedeutet eine Ganztaktpause."""
    seite = ZUSTAND.get("seite")
    if seite is None:
        return symbole
    import bildpruefung
    neu = list(symbole)
    for a, e in takte_der_zeile(symbole):
        idx = [i for i in range(a, e) if symbole[i].rhythm.startswith(("note", "rest"))]
        if len(idx) != 1 or not symbole[idx[0]].rhythm.startswith("rest") or any(
                symbole[i].rhythm == "chord" for i in range(a, e)):
            continue
        s = symbole[idx[0]]
        pos = _bildpos(s)
        if pos is None:
            continue
        zwischen = ZUSTAND.setdefault("mehrtakt_cache", {})
        schluessel = (round(pos[0] / 4), round(pos[1] / 4))
        if schluessel not in zwischen:
            zwischen[schluessel] = bildpruefung.mehrtaktpause_bei(seite, *pos)
        erg = zwischen[schluessel]
        if erg is None:
            continue
        n = erg[0]
        homr_n = int(s.rhythm[5:-1]) if s.rhythm.endswith("m") else None
        unsicher = None
        if n is None:
            if homr_n is not None:
                continue                               # Zahl unlesbar: homr behalten
            n, unsicher = 1, "Mehrtaktpause, Anzahl im Bild nicht lesbar"
        rhythmus = "rest_1" if n == 1 else f"rest_{n}m"
        if rhythmus == s.rhythm:
            continue
        x = _pause(config, rhythmus, s)
        x.korrektur = f"Mehrtaktpause im Bild gelesen: {n}"
        if unsicher:
            x.unsicher = unsicher
        neu[idx[0]] = x
        protokoll(f"Mehrtaktpause im Bild: {n} Takt(e) statt {s.rhythm}")
    return neu


def pausen_ohne_tonhoehe(symbole, config):
    """homr macht aus einer Pause eine Note, wenn der Decoder für die Pause trotzdem eine Tonhöhe
    ausgibt (die Tonhöhe wird getrennt vorhergesagt). Eine Pause hat keine Tonhöhe."""
    neu = []
    for x in symbole:
        if x.rhythm.startswith("rest") and x.pitch not in (nonote, empty):
            y = _pause(config, x.rhythm, x)
            y.p_rhythm = getattr(x, "p_rhythm", 1.0)
            if getattr(x, "p_rhythm", 1.0) < 0.5:
                y.unsicher = f"Pause oder Note {x.pitch}?"
            protokoll(f"Pause mit Tonhöhe {x.pitch}: als Pause gelesen")
            neu.append(y)
        else:
            neu.append(x)
    return neu


def _lage(s):
    """Lage eines Zeichens im Eingabebild: selbst gesetzt (Faulenzer) oder aus der Aufmerksamkeit."""
    return getattr(s, "image_coordinates", None) or _bildpos(s)


def _faulenzer_zeichen(config, art, x, y):
    """Platzhalter für einen Faulenzer-Takt: Ganztaktpause mit Kennzeichen. Fürs Neulesen steht sie
    als Ganze Pause im Vokabular; beim Schreiben werden die Noten des Vortakts eingesetzt."""
    z = EncodedSymbol("measureRest", position="upper")
    z.image_coordinates = (float(x), float(y))
    z.tokens = (config.rhythm_vocab["rest_1"], config.pitch_vocab[empty], config.lift_vocab[empty],
                config.articulation_vocab[empty], config.slur_vocab[empty], config.position_vocab["upper"])
    z.p_rhythm = 1.0
    z.faulenzer = art
    z.korrektur = "Faulenzer im Bild erkannt" if art == "1" else "Zweitakt-Faulenzer im Bild erkannt"
    return z


def _strich(config, vorlage=None):
    t = EncodedSymbol("barline", nonote, nonote, nonote, nonote, nonote)
    t.tokens = (config.rhythm_vocab["barline"], config.pitch_vocab[nonote], config.lift_vocab[nonote],
                config.articulation_vocab[nonote], config.slur_vocab[nonote], config.position_vocab[nonote])
    t.p_rhythm = 1.0
    return t


def _inhalt(s):
    """Zeichen, das zum Inhalt eines Taktes gehört (nicht Taktstrich, Schlüssel, Tonart, Volte)."""
    return not s.rhythm.startswith(TAKTSTRICHE) and not s.rhythm.startswith(("clef", "keySignature", "volta")) \
        and s.rhythm != "newline"


def _faulenzer_an(symbole, f, config, ab):
    x_f, y_f = f["x"], f["y"]
    lage = [_lage(s) for s in symbole]
    von, bis = f.get("von", x_f - 4 * ab), f.get("bis", x_f + 4 * ab)
    for s, p in zip(symbole, lage):                    # schon eingesetzt
        if getattr(s, "faulenzer", None) and p is not None and von - ab < p[0] < bis + ab:
            return symbole
    # alles, was homr zwischen den Taktstrichen um das Zeichen gelesen hat (Zweitakt-Faulenzer: beide
    # Takte). Die Lage aus homrs Aufmerksamkeit ist ungenau: maßgeblich ist die Reihenfolge – alles
    # zwischen dem ersten und dem letzten Zeichen, das sicher im Bereich liegt.
    rand = 0.3 * ab
    # Taktangaben an dieser Stelle sind immer Fehllesungen des Faulenzers (sie stehen oft auf dem Strich)
    innen = [i for i, p in enumerate(lage) if p is not None and symbole[i].rhythm != "newline"
             and (von + rand < p[0] < bis - rand
                  or (symbole[i].rhythm.startswith("timeSignature") and von - 0.5 * ab < p[0] < bis))]
    if innen:
        i0, i1 = min(innen), max(innen)
        while i0 >= 2 and symbole[i0 - 1].rhythm == "chord":
            i0 -= 2
        while i1 + 2 < len(symbole) and symbole[i1 + 1].rhythm == "chord":
            i1 += 2
        weg = {i for i in range(i0, i1 + 1) if not symbole[i].rhythm.startswith(("clef", "keySignature", "volta"))}
        stelle = i0
    else:
        weg = set()
        stelle = next((i for i, p in enumerate(lage) if p is not None and p[0] > x_f), len(symbole))
    nah = [i for i in weg if _inhalt(symbole[i])]
    links = [s for i, s in enumerate(symbole[:stelle]) if i not in weg]
    rechts = [s for i, s in enumerate(symbole[stelle:], stelle) if i not in weg]
    if f["taktstrich"]:
        einsatz = [_faulenzer_zeichen(config, "2a", (von + x_f) / 2, y_f), _strich(config),
                   _faulenzer_zeichen(config, "2b", (x_f + bis) / 2, y_f)]
    else:
        einsatz = [_faulenzer_zeichen(config, "1", x_f, y_f)]
    vorher = next((s for s in reversed(links) if s.rhythm != "chord"), None)
    if vorher is not None and vorher.rhythm.startswith(("note", "rest", "measureRest", "timeSignature")):
        einsatz = [_strich(config)] + einsatz           # homr hat den Taktstrich davor übersehen
    danach = next((s for s in rechts if s.rhythm != "chord"), None)
    if danach is not None and not danach.rhythm.startswith(TAKTSTRICHE) and danach.rhythm != "newline":
        einsatz = einsatz + [_strich(config)]           # … oder den danach
    striche_neu = sum(1 for z in einsatz if z.rhythm == "barline") - sum(
        1 for i in weg if symbole[i].rhythm.startswith(TAKTSTRICHE)) - (1 if f["taktstrich"] else 0)
    protokoll(f"Faulenzer im Bild erkannt{' (zwei Takte)' if f['taktstrich'] else ''}: "
              f"{len(nah)} Zeichen von homr ersetzt" + (", Taktstrich ergänzt" if striche_neu > 0 else ""))
    return links + einsatz + rechts


@_zugabe()
def faulenzer_einsetzen(symbole, config):
    """Faulenzer (Taktwiederholung) aus der Bildprüfung in die Zeichen der Zeile einsetzen: homr hat
    dafür kein Zeichen und liest dort Pausen, Unsinn oder gar eine Taktangabe – oder lässt den Takt
    ganz aus. Der Takt wird durch einen Platzhalter ersetzt, fehlende Taktstriche werden ergänzt."""
    funde, seite = ZUSTAND.get("faulenzer") or [], ZUSTAND.get("seite")
    if not funde or seite is None:
        return symbole
    systeme = [seite.system_bei(*p) for p in (_bildpos(s) for s in symbole) if p is not None]
    if not systeme:
        return symbole
    system = Counter(systeme).most_common(1)[0][0]
    for f in sorted((f for f in funde if f["system"] == system), key=lambda f: -f["x"]):
        symbole = _faulenzer_an(symbole, f, config, seite.ab)
    return symbole


def ganztaktpausen(symbole):
    """Einzelne Ganze Pause in einem Takt = Pause über den ganzen Takt (in jeder Taktart). homr
    würde sie als vier Viertel schreiben – im 2/4- oder 3/4-Takt wäre der Takt dann zu lang."""
    neu = list(symbole)
    for a, e in takte_der_zeile(symbole):
        idx = [i for i in range(a, e) if symbole[i].rhythm.startswith(("note", "rest"))]
        if len(idx) == 1 and symbole[idx[0]].rhythm == "rest_1" and not any(
                symbole[i].rhythm == "chord" for i in range(a, e)):
            alt = symbole[idx[0]]
            x = EncodedSymbol("measureRest", position=alt.position if alt.position != nonote else "upper",
                              coordinates=getattr(alt, "coordinates", None))
            for attr in ("korrektur", "unsicher", "p_rhythm", "alt_rhythm"):
                if hasattr(alt, attr):
                    setattr(x, attr, getattr(alt, attr))
            neu[idx[0]] = x
    return neu


def nachlauf_entfernen(symbole):
    """Nach dem Schlussstrich (dünn-dick) stehen oft Text, Logo oder Verlagsangaben auf den
    Notenlinien. Der Decoder liest daraus unsichere Noten. Sind die Zeichen nach dem letzten
    Schlussstrich im Mittel unsicher, werden sie verworfen."""
    letzte = max((i for i, x in enumerate(symbole) if x.rhythm == "bolddoublebarline"), default=None)
    if letzte is None:
        return symbole
    rest = [x for x in symbole[letzte + 1:] if x.rhythm.startswith(("note", "rest"))]
    if not rest:
        return symbole
    mittel = sum(getattr(x, "p_rhythm", 1.0) for x in rest) / len(rest)
    if mittel >= 0.7:
        return symbole
    ende = letzte + 1
    while ende < len(symbole) and symbole[ende].rhythm == "voltaStop":
        ende += 1
    protokoll(f"Zeichen nach dem Schlussstrich verworfen ({len(rest)} unsichere Noten/Pausen, "
              f"mittlere Sicherheit {mittel:.2f})")
    return symbole[:ende]


def einsame_pause_fuellen(symbole, config):
    """Zweistimmiger Takt (z. B. Stichnoten): Besteht eine Stimme nur aus einer Pause, während die
    andere den Takt füllt, ist es eine Ganztaktpause."""
    neu = list(symbole)
    for (a, e), soll in zip(takte_der_zeile(symbole), takt_soll(symbole)):
        if soll is None:
            continue
        st1 = [i for i in range(a, e) if symbole[i].rhythm.startswith(("note", "rest"))
               and not symbole[i].position.endswith("2")]
        st2 = [i for i in range(a, e) if symbole[i].rhythm.startswith(("note", "rest"))
               and symbole[i].position.endswith("2")]
        if not st2 or len(st1) != 1 or not symbole[st1[0]].rhythm.startswith("rest"):
            continue
        p = symbole[st1[0]]
        if _dauer(p) == soll or sum(_dauer(symbole[i]) for i in st2) != soll:
            continue
        for tok in ("rest_1", "rest_2.", "rest_2", "rest_1.", "rest_4.", "rest_4"):
            if EncodedSymbol(tok, empty, empty, empty, empty, "upper").get_duration().fraction == soll:
                x = _symbol(config, tok, p.pitch, p.lift, p.articulation, p.slur, p.position, p)
                x.korrektur = "Ganztaktpause neben der zweiten Stimme"
                neu[st1[0]] = x
                protokoll(f"Ganztaktpause neben zweiter Stimme: {p.rhythm} -> {tok}")
                break
    return neu


def _symbol(config, rhythm, pitch, lift, articulation, slur, position, vorlage=None):
    s = EncodedSymbol(rhythm, pitch, lift, articulation, slur, position,
                      coordinates=getattr(vorlage, "coordinates", None))
    s.tokens = (config.rhythm_vocab[rhythm], config.pitch_vocab[pitch], config.lift_vocab[lift],
                config.articulation_vocab[articulation], config.slur_vocab[slur], config.position_vocab[position])
    s.p_rhythm = 1.0
    return s


def ungueltige_ersetzen(symbole, config):
    """Note ohne Tonhöhe: wahrscheinlichste Tonhöhe, sonst beste Alternative ohne Tonhöhe
    (Tonart, Schlüssel) – oder das Zeichen fällt weg."""
    ergebnis = []
    for s in symbole:
        if s.rhythm.startswith("note") and s.pitch in (nonote, empty):
            tonhoehe = next((t for t, p in getattr(s, "alt_pitch", []) if t not in (nonote, empty) and p >= 0.05), None)
            ersatz = next((t for t, p in getattr(s, "alt_rhythm", [])[1:]
                           if t.startswith(("keySignature", "clef")) and p >= 0.05), None)
            if ersatz and getattr(s, "p_rhythm", 1) < 0.5:
                tonhoehe = None
            if tonhoehe:
                lift = s.lift if s.lift != nonote else next(
                    (t for t, p in getattr(s, "alt_lift", []) if t != nonote), empty)
                neu = _symbol(config, s.rhythm, tonhoehe, lift, s.articulation if s.articulation != nonote else empty,
                              s.slur if s.slur != nonote else empty, s.position if s.position != nonote else "upper", s)
                neu.alt_rhythm = s.alt_rhythm
                neu.unsicher = f"Tonhöhe unklar, {tonhoehe} angenommen"
                protokoll(f"Note ohne Tonhöhe: {tonhoehe} angenommen")
                ergebnis.append(neu)
            elif ersatz:
                if ersatz.startswith("key"):
                    neu = _symbol(config, ersatz, nonote, nonote, nonote, nonote, nonote, s)
                else:
                    neu = _symbol(config, ersatz, empty, empty, empty, empty, "upper", s)
                protokoll(f"Unlesbares Zeichen als {ersatz} gelesen")
                ergebnis.append(neu)
            else:
                protokoll("Unlesbares Zeichen (Note ohne Tonhöhe) entfernt")
            continue
        ergebnis.append(s)
    return ergebnis


KREUZE = "FCGDAEB"


def tonart_alter(fifths, step):
    if fifths > 0:
        return 1 if step in KREUZE[:fifths] else 0
    if fifths < 0:
        return -1 if step in KREUZE[::-1][:-fifths] else 0
    return 0


LIFT_ALTER = {"#": 1, "##": 2, "b": -1, "bb": -2, "N": 0, empty: 0}
ALTER_LIFT = {1: "#", 2: "##", -1: "b", -2: "bb", 0: empty}


def tonartwechsel_anwenden(symbole, tonart_vorher):
    """Ein Tonartwechsel mitten in der Zeile: Der Decoder behält oft die Vorzeichen der alten
    Tonart bei. Noten, deren Vorzeichen genau der alten Tonart entspricht, bekommen das der neuen."""
    tonart, alt, gewechselt = tonart_vorher, None, False
    erstes = True
    for s in symbole:
        if s.rhythm.startswith("keySignature"):
            neu = int(s.rhythm.split("_")[1])
            if not erstes and neu != tonart:
                alt, gewechselt = tonart, True
            tonart = neu
            continue
        if s.rhythm.startswith(("note", "rest", "barline")):
            erstes = False
        if gewechselt and s.rhythm.startswith("note") and s.pitch not in (nonote, empty):
            step = s.pitch[0]
            ist = LIFT_ALTER.get(s.lift)
            if ist is not None and ist == tonart_alter(alt, step) != tonart_alter(tonart, step):
                s.lift = ALTER_LIFT[tonart_alter(tonart, step)]
                s.tonart_korrigiert = True
                s.korrektur = "Vorzeichen an den Tonartwechsel angepasst"
    return symbole, tonart


def abschnitt_anfang_pruefen(decoder, context, symbole, start, nonote):
    """Nach einem Doppelstrich mitten in der Zeile beginnt oft ein neuer Teil (Trio) mit neuem
    Schlüssel und/oder neuer Tonart. Erkennt der Decoder das nur unsicher (Schlüssel oder Tonart nur
    als Alternative), wird ab dort mit dieser Vorgabe neu gelesen – dann liest er die folgenden
    Vorzeichen passend zur neuen Tonart."""
    c = decoder.config
    i = 1
    while i < len(symbole):
        if not symbole[i - 1].rhythm.startswith(ABSCHNITT):
            i += 1
            continue
        s = symbole[i]
        ungueltig = s.rhythm.startswith("note") and s.pitch in (nonote, empty)
        # nur wenn direkt nach dem Strich eine unsichere Note/Pause steht (keine Volte, kein Taktstrich,
        # und kein Schlüssel/keine Tonart in den nächsten Zeichen)
        naechste = [x.rhythm for x in symbole[i:i + 4]]
        if ZUSTAND.get("debug"):
            protokoll(f"  [debug] Abschnitt? Zeile {ZUSTAND.get('zeile')}: {naechste} p={getattr(s, 'p_rhythm', 1):.2f} "
                      f"alt={[(t, round(p, 2)) for t, p in getattr(s, 'alt_rhythm', [])[:5]]}")
        if (not s.rhythm.startswith(("note", "rest")) or any(r.startswith("keySignature") for r in naechste)
                or (getattr(s, "p_rhythm", 1) >= 0.5 and not ungueltig)):
            i += 1
            continue
        kandidaten = [t for t, p in getattr(s, "alt_rhythm", []) if
                      (t.startswith("clef") and p >= 0.03) or (t.startswith("keySignature") and p >= 0.05)]
        neu = None
        for tok in kandidaten:
            if tok.startswith("clef"):
                neu_tok = (c.rhythm_vocab[tok], c.pitch_vocab[empty], c.lift_vocab[empty],
                           c.articulation_vocab[empty], c.slur_vocab[empty], c.position_vocab["upper"])
            else:
                neu_tok = (c.rhythm_vocab[tok], 0, 0, 0, 0, 0)
            vorgabe = [x.tokens for x in symbole[:i]] + [neu_tok]
            versuch = decoder.generate(start, nonote, vorgabe=vorgabe, schluessel_vorgabe=False, context=context)
            if len(schlechte_takte(versuch)) > len(schlechte_takte(symbole)):
                continue
            neu = versuch
            folge = [x.rhythm for x in neu[i:i + 3]]
            protokoll(f"Neuer Abschnitt erkannt: {', '.join(folge)}")
            break
        if neu is not None:
            symbole = neu
        i += 1
    return symbole


def hoeflichkeit_am_zeilenende(symbole):
    """Am Zeilenende nach einem Doppelstrich stehen oft Vorzeichen/Schlüssel des nächsten Teils
    (Vorankündigung). Der Decoder liest sie als kurze Noten. Ein solcher unvollständiger Rest-Takt
    ohne abschließenden Taktstrich wird entfernt."""
    takte = takte_der_zeile(symbole)
    if len(takte) < 2:
        return symbole
    a, e = takte[-1]
    if e < len(symbole) and len(takte) >= 3 and takt_dauer(symbole, a, e)[0] == 0:
        a, e = takte[-2]                               # letzter "Takt" ist nur der Schlussstrich
    if a == 0 or not symbole[a - 1].rhythm.startswith(ABSCHNITT):
        return symbole
    soll_je = takt_soll(symbole)
    soll = soll_je[takte.index((a, e))]
    d = takt_dauer(symbole, a, e)[0]
    noten = [x for x in symbole[a:e] if x.rhythm.startswith(("note", "rest"))]
    if soll is not None and 0 < d < soll and len(noten) <= 2 and not any(x.rhythm.endswith("m") for x in noten):
        protokoll(f"Vorankündigung am Zeilenende entfernt: {' '.join(x.rhythm + ' ' + x.pitch for x in noten)}")
        return symbole[:a]
    return symbole


def _neue_predict(self, image):
    x = s2s._transform(image=image)
    start_token = np.array([[1]], dtype=np.int64)
    nonote_token = np.array([[0]], dtype=np.int64)
    context = self.encoder.generate(x)
    context_dtype = np.float16 if self.decoder.fp16 else np.float32
    if context.dtype != context_dtype:
        context = context.astype(context_dtype)
    out = self.decoder.generate(start_token, nonote_token, context=context)
    if ZUSTAND.get("reparatur", True):
        cfg = self.decoder.config
        out = nachlauf_entfernen(out)
        out = faulenzer_einsetzen(out, cfg)
        out = pausen_ohne_tonhoehe(out, cfg)
        out = mehrtaktpausen_bild(out, cfg)
        soll_festlegen(out)
        achtelpausen_sammeln(out)
        out = abschnitt_anfang_pruefen(self.decoder, context, out, start_token, nonote_token)
        out = ungueltige_ersetzen(out, cfg)
        out = pausen_ohne_tonhoehe(out, cfg)
        out = mehrtaktpausen_bild(out, cfg)
        out = zeile_reparieren(self.decoder, context, out, start_token, nonote_token)
        out = ungueltige_ersetzen(out, cfg)
        out = faulenzer_einsetzen(out, cfg)
        out = pausen_ohne_tonhoehe(out, cfg)
        out = mehrtaktpausen_bild(out, cfg)
        out = hoeflichkeit_am_zeilenende(out)
        out = einsame_pause_fuellen(out, cfg)
        soll_merken(out)
        out = ganztaktpausen(out)
    for s in out:
        if s.rhythm in UEBLICHE_SCHLUESSEL:
            ZUSTAND["schluessel"] = s.rhythm
    ZUSTAND["zeile"] = ZUSTAND.get("zeile", 0) + 1
    striche = [x.rhythm for x in out if x.rhythm.startswith(TAKTSTRICHE)]
    ZUSTAND["vorige_zeile_ende"] = striche[-1] if striche else ""
    return out


s2s.Staff2Score.predict = _neue_predict


# ----------------------------------------------------------------------------------------------
# Seite lesen
# ----------------------------------------------------------------------------------------------
def erkennen(bildpfad, schluessel_vorgabe=True, reparatur=True):
    """Liest eine (bereinigte) Notenseite. Ergebnis: Stimmen als Zeichenlisten."""
    import os
    ZUSTAND.update(schluessel=None, aktiv=schluessel_vorgabe, protokoll=[], reparatur=reparatur, zeile=0,
                   taktdauern=Counter(), auftakte=set(), vorige_zeile_ende="", soll_zuletzt=None,
                   zeilen_soll={}, achtelpausen=[], mehrtakt_cache={}, seite=None, zeile_zu_seite=None, faulenzer=[], kopf={},
                   debug=bool(os.environ.get("NOTENLESER_DEBUG")))
    if reparatur:
        try:
            import cv2
            import bildpruefung
            ZUSTAND["seite"] = bildpruefung.Seite(cv2.imread(bildpfad, 0))
            ZUSTAND["faulenzer"] = bildpruefung.faulenzer_suchen(ZUSTAND["seite"])
        except Exception as fehler:                   # Bildprüfungen sind eine Zugabe
            protokoll(f"Bildprüfung nicht möglich: {fehler}")
    config = ProcessingConfig(enable_debug=False, enable_cache=False, write_staff_positions=False,
                              read_staff_positions=False, selected_staff=-1, transformer_use_gpu=False,
                              segnet_use_gpu=False, coreml_encoder=False, title_detection=False)
    multi_staffs, image, debug, title_future, _, to_input_image = detect_staffs_in_image(bildpfad, config)
    ZUSTAND["zu_eingabe"] = to_input_image
    multi_staffs = repair_systems(debug, multi_staffs)
    tconfig = Config()
    tconfig.use_gpu_inference = False
    tconfig.use_coreml_encoder = False
    stimmen = parse_staffs(debug, multi_staffs, image, config=tconfig, page_to_input_image=to_input_image)
    debug.clean_debug_files_from_previous_runs()
    ablage = os.environ.get("NOTENLESER_MASKE")              # nur zum Entwickeln: Notenkopf-Maske sichern
    if ablage and ZUSTAND.get("kopf_maske") is not None:
        np.savez_compressed(os.path.join(ablage, os.path.splitext(os.path.basename(bildpfad))[0] + "_kopf.npz"),
                            maske=ZUSTAND["kopf_maske"], p0=to_input_image((0.0, 0.0)),
                            p1=to_input_image((1000.0, 1000.0)))
    if not reparatur:
        return stimmen
    # Tonartwechsel je Zeile nachziehen
    neue = []
    for stimme in stimmen:
        ergebnis, zeile, tonart = [], [], 0
        for s in stimme + [EncodedSymbol("newline")]:
            if s.rhythm == "newline":
                zeile, tonart = tonartwechsel_anwenden(zeile, tonart)
                ergebnis.extend(zeile)
                ergebnis.append(s)
                zeile = []
            else:
                zeile.append(s)
        neue.append(ergebnis[:-1])
    n = sum(1 for st in neue for s in st if getattr(s, "tonart_korrigiert", False))
    if n:
        protokoll(f"Tonartwechsel: {n} Vorzeichen auf die neue Tonart umgerechnet")
    neue = [taktvorzeichen_pruefen(st) for st in neue]
    import bildpruefung
    if bildpruefung.OCR_FEHLER:
        protokoll(f"Texterkennung (Pausenzahlen) nicht verfügbar: {bildpruefung.OCR_FEHLER}")
    try:
        neue = akkorde_pruefen(neue, bildpfad)
    except Exception as fehler:                       # Gegenprobe ist eine Zugabe – nie daran scheitern
        protokoll(f"Gegenprobe am Bild übersprungen: {fehler}")
    return artikulationen_pruefen(neue, bildpfad)


# Artikulationen: Kürzel der Bildprüfung -> homr-Namen. Den Keil (sz) entscheidet das Bild nicht: ein
# etwas länglicher Punkt im alten Druck sähe sonst wie ein Keil aus.
ART_HOMR = {"st": "staccato", "te": "tenuto", "ak": "accent", "ma": "accentStrong"}
ART_KURZ = {"staccato", "staccatissimo", "tenuto"}
ART_AKZENT = {"accent", "accentStrong"}
ART_NAME = {"staccato": "Staccato", "staccatissimo": "Keil", "tenuto": "Tenuto", "accent": "Akzent",
            "accentStrong": "Marcato"}


def _artikulation_teile(s):
    a = s.articulation or ""
    return [t for t in a.split("_") if t and t != nonote]


@_zugabe()
def artikulationen_pruefen(stimmen, bildpfad):
    """Staccato, Tenuto, Keil, Akzent und Marcato am Bild prüfen. homr verwechselt oft Punkt und Strich
    und übersieht Zeichen unter dem System oder unter einem Akzent („≥“). Zeigt das Bild an einem
    Notenkopf klar ein Zeichen, entscheidet es: Punkt/Strich/Keil ersetzen homrs Lesart dieser Gruppe,
    Akzent/Marcato ebenso. Findet das Bild nichts, bleibt homrs Lesart – das Bild übersieht eher ein
    Zeichen, als dass es eines erfindet. Andere Zeichen (Fermate, Triller, Atemzeichen …) bleiben."""
    import cv2
    import bildpruefung
    seite = ZUSTAND.get("seite")
    if seite is None:
        seite = bildpruefung.Seite(cv2.imread(bildpfad, 0))
    finder = bildpruefung.Zeichenfinder(seite, kopf=_kopfanteil(seite))
    ab = seite.ab
    zaehler = Counter()
    for stimme in stimmen:
        schl_je, zeile_von, hinweis = _zeilen_hinweise(stimme)
        kandidaten = []
        for g in _gruppen(stimme):
            noten = [i for i in g if stimme[i].rhythm.startswith("note") and stimme[i].pitch not in (nonote, empty)
                     and not _ist_vorschlag(stimme[i]) and zeile_von[i] in hinweis
                     and getattr(stimme[i], "image_coordinates", None) is not None]
            if not noten or any("dynamic" in (stimme[i].articulation or "") for i in noten):
                continue
            x = float(np.median([float(stimme[i].image_coordinates[0]) for i in noten]))
            ys = [finder.kp.soll_y(stimme[i].image_coordinates[0], hinweis[zeile_von[i]], stimme[i].pitch, schl_je[i])
                  for i in noten]
            kandidaten.append((noten, x, min(ys), max(ys), finder.kopf_x(x, min(ys))))
        # Fallen zwei Noten auf denselben Kopf im Bild, entscheidet das Bild nur für die nähere
        unsicher = set()
        for a, b in zip(range(len(kandidaten)), range(1, len(kandidaten))):
            na, xa, ya, _, ka = kandidaten[a]
            nb, xb, yb, _, kb = kandidaten[b]
            if abs(ka - kb) < 0.6 * ab and abs(ya - yb) < 0.6 * ab:
                unsicher.add(a if abs(ka - xa) > abs(kb - xb) else b)
        for j, (noten, x, y_oben, y_unten, _) in enumerate(kandidaten):
            if j in unsicher:
                continue
            funde = finder.zeichen_bei(x, y_oben, y_unten)
            bild = {ART_HOMR[a] for a, _, _ in funde if a in ART_HOMR}
            alle = [t for i in noten for t in _artikulation_teile(stimme[i])]
            alt = set(alle)
            neu = set(alle)
            if bild & ART_KURZ:
                neu = (neu - ART_KURZ) | (bild & ART_KURZ)
            if bild & ART_AKZENT:
                neu = (neu - ART_AKZENT) | (bild & ART_AKZENT)
            if neu == alt:
                continue
            haupt = stimme[noten[0]]
            andere = [t for t in dict.fromkeys(alle) if t in neu and t not in ART_KURZ | ART_AKZENT]
            haupt.articulation = "_".join(sorted(set(andere) | (neu & (ART_KURZ | ART_AKZENT)))) or empty
            for i in noten[1:]:
                rest = [t for t in _artikulation_teile(stimme[i]) if t not in ART_KURZ | ART_AKZENT]
                stimme[i].articulation = "_".join(rest) or empty
            weg, dazu = alt - neu, neu - alt
            for a in sorted(weg & ART_KURZ):
                for b in sorted(dazu & ART_KURZ):
                    zaehler[f"{ART_NAME[b]} statt {ART_NAME[a]}"] += 1
            for a in sorted(weg & ART_AKZENT):
                for b in sorted(dazu & ART_AKZENT):
                    zaehler[f"{ART_NAME[b]} statt {ART_NAME[a]}"] += 1
            for gruppe in (ART_KURZ, ART_AKZENT):
                if not weg & gruppe:
                    for b in sorted(dazu & gruppe):
                        zaehler[f"{ART_NAME[b]} ergänzt"] += 1
            if ZUSTAND.get("debug"):
                protokoll(f"  [debug] Artikulation {haupt.pitch} {haupt.rhythm}: {sorted(alt)} -> {sorted(neu)}")
    if zaehler:
        protokoll("Artikulationen am Bild geprüft: " + ", ".join(f"{n}× {t}" for t, n in zaehler.most_common()))
    return stimmen


def taktvorzeichen_pruefen(stimme):
    """Ein Vorzeichen gilt bis zum Taktende für denselben Ton (gleiche Stufe und Oktave). Liest der
    Decoder eine spätere Note desselben Tons wieder ohne dieses Vorzeichen, obwohl er das Vorzeichen
    selbst für möglich hielt, wird es übernommen."""
    tonart, gesehen = 0, {}
    for s in stimme:
        r = s.rhythm
        if r.startswith("keySignature"):
            tonart, gesehen = int(r.split("_")[1]), {}
            continue
        if r == "newline" or r.startswith(TAKTSTRICHE):
            gesehen = {}
            continue
        if not r.startswith("note") or s.pitch in (nonote, empty) or _ist_vorschlag(s):
            continue
        k = (s.pitch[0], s.pitch[1:], s.position.endswith("2"))
        alter = LIFT_ALTER.get(s.lift, 0)
        aus_tonart = tonart_alter(tonart, s.pitch[0])
        if k in gesehen and gesehen[k] != aus_tonart and alter == aus_tonart:
            vorher = gesehen[k]
            alt = dict(getattr(s, "alt_lift", []))
            p_kand = max((p for t, p in alt.items() if LIFT_ALTER.get(t) == vorher), default=0.0)
            if ZUSTAND.get("debug"):
                protokoll(f"  [debug] Taktvorzeichen: {s.pitch} {s.lift} nach Vorzeichen {vorher:+d}; "
                          f"Alternativen {alt}")
            if p_kand >= 0.15:
                s.lift = ALTER_LIFT[vorher]
                s.korrektur = "Vorzeichen gilt bis Taktende"
                alter = vorher
                protokoll(f"Taktvorzeichen übernommen: {s.pitch} -> Vorzeichen {vorher:+d}")
        gesehen[k] = alter
    return stimme


def _gruppen(stimme):
    """Indizes gleichzeitig klingender Noten (durch "chord" verbunden)."""
    gruppen, i = [], 0
    while i < len(stimme):
        if stimme[i].rhythm.startswith(("note", "rest")):
            g = [i]
            while i + 2 < len(stimme) and stimme[i + 1].rhythm == "chord":
                i += 2
                g.append(i)
            gruppen.append(g)
        i += 1
    return gruppen


def _kopfanteil(seite):
    """Anteil markierter Notenkopf-Pixel (Segmentierungsnetz von homr) in einem kopfgroßen Fenster um
    (x, y) im Eingabebild, bestes Fenster in waagrechter Richtung ±suche Linienabstände – als Funktion
    anteil(x, y, suche=1.2); None, wenn keine Maske vorliegt."""
    import cv2
    maske, zu_eingabe = ZUSTAND.get("kopf_maske"), ZUSTAND.get("zu_eingabe")
    if maske is None or zu_eingabe is None:
        return None
    # Abbildung Eingabebild -> Maske (affin: Skalierung + Verschiebung)
    p0, p1 = zu_eingabe((0.0, 0.0)), zu_eingabe((1000.0, 1000.0))
    sx, sy = (p1[0] - p0[0]) / 1000.0, (p1[1] - p0[1]) / 1000.0
    integral = cv2.integral(maske)
    ab_m = seite.ab / sx
    h, w = maske.shape

    def anteil(x, y, suche=1.2):
        xm, ym = (x - p0[0]) / sx, (y - p0[1]) / sy
        y0, y1 = int(max(0, ym - 0.35 * ab_m)), int(min(h, ym + 0.35 * ab_m))
        beste = 0.0
        for xc in np.arange(xm - suche * ab_m, xm + suche * ab_m + 1, max(1.0, ab_m / 8)):
            x0, x1 = int(max(0, xc - 0.5 * ab_m)), int(min(w, xc + 0.5 * ab_m))
            if x1 <= x0 or y1 <= y0:
                continue
            summe = integral[y1, x1] - integral[y0, x1] - integral[y1, x0] + integral[y0, x0]
            beste = max(beste, summe / ((x1 - x0) * (y1 - y0)))
        return float(beste)
    return anteil


def _zeilen_hinweise(stimme):
    """Schlüssel je Zeichen, Zeilennummer je Zeichen und mittlere Notenhöhe (y) je Zeile."""
    schluessel, schl_je, zeile_von, z = "clef_G2", [], [], 0
    for x in stimme:
        if x.rhythm.startswith("clef"):
            schluessel = x.rhythm
        if x.rhythm == "newline":
            z += 1
        schl_je.append(schluessel)
        zeile_von.append(z)
    hinweis = {}
    for zz in set(zeile_von):
        ys = [x.image_coordinates[1] for x, q in zip(stimme, zeile_von) if q == zz and
              x.rhythm.startswith("note") and getattr(x, "image_coordinates", None) is not None]
        if ys:
            hinweis[zz] = float(np.median(ys))
    return schl_je, zeile_von, hinweis


def akkorde_pruefen(stimmen, bildpfad):
    """Gegenprobe am Bild: Das Segmentierungsnetz von homr markiert alle Notenköpfe der Seite.
    Liegt an der Stelle einer Akkord-/Zweitstimmennote kein markierter Notenkopf (sondern z. B. ein
    Hals, ein Tenutostrich oder eine Dynamikangabe), wird die Note entfernt. Die Schwelle wird an
    den Einzelnoten derselben Seite geeicht."""
    import cv2
    import bildpruefung
    seite = ZUSTAND.get("seite")
    if seite is None:
        seite = bildpruefung.Seite(cv2.imread(bildpfad, 0))
    anteil = _kopfanteil(seite)
    if anteil is None:
        return stimmen
    pruefer = bildpruefung.Kopfpruefer.__new__(bildpruefung.Kopfpruefer)
    pruefer.seite = seite

    def kopf(x, schluessel, y_hinweis):
        pos = getattr(x, "image_coordinates", None)
        if pos is None or x.pitch in (nonote, empty):
            return None
        y = pruefer.soll_y(pos[0], y_hinweis, x.pitch, schluessel)
        return anteil(pos[0], y)

    ergebnis = []
    for stimme in stimmen:
        schl_je, zeile_von, hinweis = _zeilen_hinweise(stimme)
        gruppen = _gruppen(stimme)
        eich = []
        for g in gruppen:
            for i in g:
                if stimme[i].rhythm.startswith("note") and zeile_von[i] in hinweis and not _ist_vorschlag(stimme[i]):
                    f = kopf(stimme[i], schl_je[i], hinweis[zeile_von[i]])
                    if f is not None:
                        stimme[i].kopf_anteil = f
                        if len(g) == 1:
                            eich.append(f)
        if len(eich) < 5:
            ergebnis.append(stimme)
            continue
        m = float(np.median(eich))
        for x in stimme:
            if getattr(x, "kopf_anteil", None) is not None:
                x.kopf_relativ = x.kopf_anteil / max(m, 1e-6)
        # Zeilen mit echter Mehrstimmigkeit (viele Akkorde) nicht anfassen
        akkordanteil = {}
        for zz in set(zeile_von):
            gz = [g for g in gruppen if zeile_von[g[0]] == zz and stimme[g[0]].rhythm.startswith("note")]
            akkordanteil[zz] = sum(1 for g in gz if len(g) > 1) / max(1, len(gz))
        weg = set()
        for g in gruppen:
            noten = [i for i in g if stimme[i].rhythm.startswith("note")]
            if len(noten) < 2 or zeile_von[g[0]] not in hinweis or akkordanteil[zeile_von[g[0]]] >= 0.3:
                continue
            werte = {i: kopf(stimme[i], schl_je[i], hinweis[zeile_von[i]]) for i in noten}
            werte = {i: f for i, f in werte.items() if f is not None}
            sicher = [i for i, f in werte.items() if f >= 0.5 * m]
            for i, f in werte.items():
                if f < 0.2 * m and sicher and i not in sicher:
                    weg.add(i)
                    protokoll(f"Gegenprobe: kein Notenkopf für {stimme[i].pitch} ({stimme[i].rhythm}) "
                              f"im Akkord – entfernt (Kopf-Anteil {f:.2f}, Einzelnoten {m:.2f})")
                elif ZUSTAND.get("debug"):
                    protokoll(f"  [debug] Akkordnote {stimme[i].pitch} {stimme[i].rhythm}: Kopf-Anteil {f:.2f} (Einzelnoten {m:.2f})")
        if not weg:
            ergebnis.append(stimme)
            continue
        for g in gruppen:
            if any(i in weg for i in g):
                for i in g:
                    if i not in weg:
                        stimme[i].korrektur = "Akkordnote ohne Notenkopf im Bild entfernt"
        neu = []
        for i, x in enumerate(stimme):
            if i in weg or (x.rhythm == "chord" and ((i + 1 in weg) or (i - 1 in weg))):
                continue
            neu.append(x)
        ergebnis.append(neu)
    return ergebnis


def _faulenzer_art(takt):
    for note in takt.findall("note"):
        for k in note:
            if k.tag is ET.Comment and "faulenzer=" in (k.text or ""):
                treffer = __import__("re").search(r"faulenzer=(\w+)", k.text)
                if treffer:
                    return treffer.group(1)
    return None


def _attribute(takt):
    attr = takt.find("attributes")
    if attr is None:
        attr = ET.Element("attributes")
        pos = 1 if len(takt) and takt[0].tag == "print" else 0
        takt.insert(pos, attr)
    return attr


def faulenzer_aufloesen(wurzel):
    """Faulenzer-Takte (Platzhalter aus faulenzer_einsetzen): Die Noten des wiederholten Taktes werden
    eingesetzt – MusicXML verlangt sie auch in Wiederholungstakten (Abspielen, Transponieren, andere
    Notenprogramme). Eintakt-Faulenzer werden als Taktwiederholung gekennzeichnet (Anzeige wie im
    Original); Zweitakt-Faulenzer werden ausgeschrieben, weil der Notensatz sie aus MusicXML nicht
    richtig zeichnet."""
    import copy
    for teil in wurzel.iter("part"):
        takte = teil.findall("measure")
        arten = [_faulenzer_art(t) for t in takte]
        for k, (takt, art) in enumerate(zip(takte, arten)):
            if not art:
                continue
            zurueck = 1 if art == "1" else 2
            alte = [el for el in takt if el.tag in ("note", "backup", "forward")]
            kommentare = [c for el in alte if el.tag == "note" for c in el if c.tag is ET.Comment]
            if k < zurueck:                            # kein Takt davor: Ganztaktpause bleibt
                arten[k] = None
                continue
            quelle = takte[k - zurueck] if art in ("1", "2a") else takte[k - 2]
            pos = list(takt).index(alte[0]) if alte else len(takt)
            for el in alte:
                takt.remove(el)
            kopien = []
            for el in quelle:
                if el.tag not in ("note", "backup", "forward"):
                    continue
                c = copy.deepcopy(el)
                for kom in [x for x in c if x.tag is ET.Comment]:
                    c.remove(kom)
                for tie in c.findall("tie"):
                    c.remove(tie)
                notations = c.find("notations")
                if notations is not None:
                    for x in list(notations):
                        if x.tag in ("tied", "slur"):      # Bögen nicht über die Taktgrenze kopieren
                            notations.remove(x)
                kopien.append(c)
            erste = next((c for c in kopien if c.tag == "note"), None)
            if erste is not None:
                for kom in kommentare:
                    erste.append(kom)
                if art != "1":
                    for kom in [x for x in erste if x.tag is ET.Comment and "faulenzer=" in (x.text or "")]:
                        kom.text = kom.text.replace("korrektur=Zweitakt-Faulenzer im Bild erkannt",
                                                    "korrektur=Zweitakt-Faulenzer im Bild erkannt und ausgeschrieben")
            for i, c in enumerate(kopien):
                takt.insert(pos + i, c)
            protokoll("Faulenzer: Noten des " + ("Vortakts" if art == "1" else "Takts zwei davor") + " eingesetzt")
        k = 0
        while k < len(takte):                          # Eintakt-Faulenzer als Taktwiederholung kennzeichnen
            if arten[k] != "1":
                k += 1
                continue
            ende = k
            while ende + 1 < len(takte) and arten[ende + 1] == "1":
                ende += 1
            stil = ET.SubElement(_attribute(takte[k]), "measure-style")
            ET.SubElement(stil, "measure-repeat", type="start", slashes="1").text = "1"
            if ende + 1 < len(takte):
                stil = ET.SubElement(_attribute(takte[ende + 1]), "measure-style")
                ET.SubElement(stil, "measure-repeat", type="stop")
            k = ende + 1
    return wurzel


# Reihenfolge der Angaben in <barline> (MusicXML): Strichart, Volte, Wiederholung
_STRICH_ORDNUNG = {"bar-style": 0, "ending": 1, "repeat": 2}


def _taktstrich(takt, ort):
    """<barline location=ort> des Takts holen oder anlegen (links als erstes Element, rechts als letztes)."""
    for b in takt.findall("barline"):
        if b.get("location", "right") == ort:
            return b
    b = ET.Element("barline", location=ort)
    if ort == "left":
        takt.insert(1 if len(takt) and takt[0].tag == "print" else 0, b)
    else:
        takt.append(b)
    return b


def _strich_setzen(takt, ort, wiederholung=None, volte=None, stil=None):
    """Wiederholung ("forward"/"backward"), Volte ((Nummer, Art)) und Strichart in den Taktstrich eintragen."""
    b = _taktstrich(takt, ort)
    if stil:
        for alt in b.findall("bar-style"):
            b.remove(alt)
        ET.SubElement(b, "bar-style").text = stil
    if volte:
        ET.SubElement(b, "ending", number=str(volte[0]), type=volte[1])
    if wiederholung and not any(r.get("direction") == wiederholung for r in b.findall("repeat")):
        ET.SubElement(b, "repeat", direction=wiederholung)
    b[:] = sorted(b, key=lambda el: _STRICH_ORDNUNG.get(el.tag, 3))
    return b


def wiederholungen_richten(wurzel):
    """homr legt bei einem Wiederholungsanfang einen neuen Takt an, schreibt das Zeichen aber als
    rechten Taktstrich hinein. Notenprogramme zeigen und spielen dann ein Wiederholungsende am Ende
    dieses Takts. Der Anfang gehört an den linken Rand des Takts."""
    n = 0
    for teil in wurzel.iter("part"):
        for takt in teil.findall("measure"):
            for b in list(takt.findall("barline")):
                vor = [r for r in b.findall("repeat") if r.get("direction") == "forward"]
                if b.get("location", "right") != "right" or not vor:
                    continue
                for r in vor:
                    b.remove(r)
                if len(b) == 0:
                    takt.remove(b)
                _strich_setzen(takt, "left", wiederholung="forward", stil="heavy-light")
                n += 1
    if n:
        protokoll(f"{n} Wiederholungsanfänge an den linken Taktrand gesetzt")
    return wurzel


def _takt_einheiten(takte, seite):
    """Takte mit ihrer Lage auf der Seite: Liste {erster, letzter, system, x0, x1} in Lesereihenfolge.
    Eine Mehrtaktpause (N Takte in der Datei, ein Zeichen im Bild) ist eine Einheit. Die Zeile eines
    Takts steht in der Datei (Zeilenumbrüche); nur wenn deren Zahl nicht zu den Notensystemen im Bild
    passt, entscheidet die Höhe der Noten (bei eng gesetzten Seiten liegt eine tiefe Note näher am
    nächsten System)."""
    import bildpruefung
    mitte_x = seite.bild.shape[1] / 2
    lesefolge = sorted(range(len(seite.systeme)), key=lambda i: float(seite.systeme[i][2]["verlauf"](mitte_x)))
    zeilen, z = [], 0
    for takt in takte:
        if any(p_.get("new-system") == "yes" for p_ in takt.findall("print")):
            z += 1
        zeilen.append(z)
    nach_datei = z + 1 == len(lesefolge)
    einheiten, k = [], 0
    while k < len(takte):
        takt = takte[k]
        laenge = 1
        mr = takt.find("attributes/measure-style/multiple-rest")
        if mr is not None and (mr.text or "").strip().isdigit():
            laenge = max(1, min(int(mr.text), len(takte) - k))
        orte = []
        for el in list(takt.iter("measure-style")) + list(takt.iter("note")):
            pos = bildpruefung._imgpos(el)
            if pos is not None and len(pos) >= 2:
                orte.append(pos[:2])
        if orte:
            xs = [x for x, _ in orte]
            ys = sorted(y for _, y in orte)
            system = lesefolge[zeilen[k]] if nach_datei else seite.system_bei(float(np.median(xs)), float(ys[len(ys) // 2]))
            einheiten.append(dict(erster=takt, letzter=takte[k + laenge - 1], x0=float(min(xs)), x1=float(max(xs)),
                                  system=system))
        k += laenge
    return einheiten


def _einheiten_je_system(takte, seite):
    """Takt-Einheiten je Notensystem, von links nach rechts, mit den Grenzen zwischen den Takten
    (links, rechts: etwa die Lage der Taktstriche)."""
    ab = seite.ab
    je_system = {}
    for e in sorted(_takt_einheiten(takte, seite), key=lambda e: e["x0"]):
        je_system.setdefault(e["system"], []).append(e)
    for liste in je_system.values():
        for i, e in enumerate(liste):
            e["links"] = (liste[i - 1]["x1"] + e["x0"]) / 2 if i else e["x0"] - 4 * ab
            e["rechts"] = (e["x1"] + liste[i + 1]["x0"]) / 2 if i + 1 < len(liste) else e["x1"] + 4 * ab
    return je_system


@_zugabe()
def wiederholungen_nach_bild(wurzel):
    """Wiederholungszeichen und Volta-Klammern nach dem Bild setzen. homr übersieht davon einiges
    (Wiederholungsende, Anfang einer Volte) und nummeriert Volten falsch; im Bild sind die Zeichen
    eindeutig: dicker Taktstrich mit zwei Punkten, Klammerlinie mit Haken und Zahl. Findet das Bild
    Zeichen einer Art, ersetzen sie homrs Lesart dieser Art; findet es keine, bleibt homrs Lesart."""
    seite = ZUSTAND.get("seite")
    if seite is None:
        return wurzel
    import bildpruefung
    zeichen = bildpruefung.wiederholungen_suchen(seite)
    volten = bildpruefung.volten_suchen(seite)
    if not zeichen and not volten:
        return wurzel
    ab = seite.ab
    mitte_x = seite.bild.shape[1] / 2
    lesefolge = sorted(range(len(seite.systeme)), key=lambda k: float(seite.systeme[k][2]["verlauf"](mitte_x)))
    rang = {k: i for i, k in enumerate(lesefolge)}
    gesetzt = Counter()
    for teil in wurzel.iter("part"):
        takte = teil.findall("measure")
        je_system = _einheiten_je_system(takte, seite)
        if not je_system:
            continue

        def nachbar(system, schritt):
            """Erste bzw. letzte Einheit der nächsten (vorigen) Zeile, in der es Takte gibt."""
            i = rang[system] + schritt
            while 0 <= i < len(lesefolge):
                liste = je_system.get(lesefolge[i])
                if liste:
                    return liste[0] if schritt > 0 else liste[-1]
                i += schritt
            return None

        if zeichen:
            for takt in takte:
                for b in list(takt.findall("barline")):
                    hatte = b.findall("repeat")
                    for r in hatte:
                        b.remove(r)
                    if hatte:                           # Strichart des Wiederholungszeichens nicht behalten
                        for st in b.findall("bar-style"):
                            if (st.text or "") in ("heavy-light", "light-heavy"):
                                b.remove(st)
                    if len(b) == 0:
                        takt.remove(b)
            for z in zeichen:
                liste = je_system.get(z["system"], [])
                if z["art"] == "anfang":
                    ziel = next((e for e in liste if e["x0"] > z["x"] - 0.5 * ab), None) or nachbar(z["system"], 1)
                    if ziel is not None:
                        _strich_setzen(ziel["erster"], "left", wiederholung="forward", stil="heavy-light")
                        gesetzt["Wiederholungsanfang"] += 1
                else:
                    ziel = next((e for e in reversed(liste) if e["x1"] < z["x"] + 0.5 * ab), None) or nachbar(z["system"], -1)
                    if ziel is not None:
                        _strich_setzen(ziel["letzter"], "right", wiederholung="backward", stil="light-heavy")
                        gesetzt["Wiederholungsende"] += 1
        if volten:
            for takt in takte:
                for b in list(takt.findall("barline")):
                    for el in b.findall("ending"):
                        b.remove(el)
                    if len(b) == 0:
                        takt.remove(b)
            spannen = []
            for v in sorted(volten, key=lambda v: (rang[v["system"]], v["x0"])):
                liste = je_system.get(v["system"], [])
                def anteil(e):
                    schnitt = min(e["rechts"], v["x1"]) - max(e["links"], v["x0"])
                    return schnitt / max(1.0, e["rechts"] - e["links"])
                unter = [e for e in liste if anteil(e) >= 0.5]
                if not unter and liste:
                    beste = max(liste, key=anteil)
                    unter = [beste] if anteil(beste) > 0.15 else []
                if unter:
                    spannen.append((v, unter[0], unter[-1]))
            index = {id(t): i for i, t in enumerate(takte)}
            vorige = None                               # (Nummer, Index des letzten Takts)
            for v, erste, letzte in spannen:
                nummer = v.get("nummer")
                if nummer is None:                      # Zahl unlesbar: aus der Folge der Klammern
                    nummer = vorige[0] + 1 if vorige and index[id(erste["erster"])] == vorige[1] + 1 else 1
                ende_wdh = any(r.get("direction") == "backward" for b in letzte["letzter"].findall("barline")
                               for r in b.findall("repeat"))
                art = "stop" if ende_wdh or v.get("haken_rechts", 0) >= 0.8 else "discontinue"
                _strich_setzen(erste["erster"], "left", volte=(nummer, "start"))
                _strich_setzen(letzte["letzter"], "right", volte=(nummer, art))
                vorige = (nummer, index[id(letzte["letzter"])])
                gesetzt["Volte"] += 1
    if gesetzt:
        protokoll("Wiederholungen am Bild gelesen: " + ", ".join(f"{n}× {t}" for t, n in gesetzt.items()))
    return wiederholungsanfang_ergaenzen(wurzel)


@_zugabe()
def texte_einsetzen(wurzel):
    """Was homr nicht liest, aber zum Spielen gebraucht wird: Vortrags- und Tempoangaben („Trio“, „Fine“,
    „D.C. al Fine“, „dolce“) und eingerahmte Studierzeichen. Die Texterkennung liest die ganze Seite
    einmal; jede Angabe kommt an die Note, über oder unter der sie steht. Aus dem Kopf der Seite werden
    Titel und Stimme als Vorschlag gemerkt (ZUSTAND["kopf"])."""
    seite = ZUSTAND.get("seite")
    ZUSTAND["kopf"] = {}
    if seite is None:
        return wurzel
    import bildpruefung
    texte = bildpruefung.texte_lesen(seite)
    if texte:
        ZUSTAND["kopf"] = bildpruefung.kopf_lesen(seite, texte)
    angaben = bildpruefung.angaben_lesen(seite, texte) if texte else []
    zeichen = bildpruefung.studierzeichen_suchen(seite)
    if not angaben and not zeichen:
        return wurzel
    ab = seite.ab
    gesetzt = Counter()

    def einfuegen(einheit, x, element):
        """Vor die Note setzen, bei der die Angabe steht (die erste ab x), sonst vor die erste Note."""
        takt = einheit["erster"]
        noten = [el for el in takt if el.tag == "note"]
        ziel = next((el for el in noten if (bildpruefung._imgpos(el) or (-1e9,))[0] >= x - 1.5 * ab), None)
        ziel = ziel if ziel is not None else (noten[0] if noten else None)
        takt.insert(list(takt).index(ziel) if ziel is not None else len(takt), element)

    def richtung(platz, kind):
        d = ET.Element("direction", placement=platz)
        ET.SubElement(d, "direction-type").append(kind)
        return d

    for teil in wurzel.iter("part"):
        je_system = _einheiten_je_system(teil.findall("measure"), seite)
        if not je_system:
            continue
        for z in zeichen:                               # Studierzeichen stehen über einem Taktstrich
            liste = je_system.get(z["system"])
            if not liste:
                continue
            einheit = min(liste, key=lambda e: abs((e["links"] if e is not liste[0] else e["x0"] - 2.5 * ab) - z["x"]))
            kind = ET.Element("rehearsal")
            kind.text = z["text"]
            einheit["erster"].insert(_einfuege_stelle(einheit["erster"]), richtung("above", kind))
            gesetzt["Studierzeichen"] += 1
        for a in sorted(angaben, key=lambda a: a["abstand"]):   # das Nächste zur Zeile zuerst: so stapelt der Satz
            liste = je_system.get(a["system"])
            if not liste:
                continue
            einheit = next((e for e in liste if e["links"] <= a["x"] + 0.5 * ab < e["rechts"]), None)
            if einheit is None:
                einheit = liste[0] if a["x"] < liste[0]["x0"] else liste[-1]
            kind = ET.Element("words", {"font-weight": "bold"} if a["fett"] else {"font-style": "italic"})
            kind.text = a["text"]
            einfuegen(einheit, a["x"], richtung("above" if a["oben"] else "below", kind))
            gesetzt["Textangabe"] += 1
        break                                           # Texte nur einmal (erste Stimme)
    if gesetzt:
        protokoll("Texte am Bild gelesen: " + ", ".join(f"{n}× {t}" for t, n in gesetzt.items()))
    return wurzel


def _einfuege_stelle(takt):
    """Stelle im Takt hinter Zeilenumbruch, linkem Taktstrich und Angaben (Schlüssel, Tonart, Taktart)."""
    i = 0
    while i < len(takt) and (takt[i].tag in ("print", "attributes") or (takt[i].tag == "barline" and takt[i].get("location") == "left")):
        i += 1
    return i


def _hat_wiederholung(takt, richtung):
    return any(r.get("direction") == richtung for b in takt.findall("barline") for r in b.findall("repeat"))


def wiederholungsanfang_ergaenzen(wurzel):
    """Alte Drucke lassen den Wiederholungsanfang weg, wenn der neue Teil direkt auf die zweite Volte
    (oder das vorige Wiederholungsende) folgt. Notenprogramme springen dann zum Anfang des vorigen Teils
    zurück und spielen ihn noch einmal. Fehlt zwischen zwei Wiederholungsenden der Anfang, wird er dort
    ergänzt, wo der neue Teil beginnt."""
    for teil in wurzel.iter("part"):
        takte = teil.findall("measure")
        beginn = None                                   # Takt nach dem vorigen Wiederholungsende (und seiner 2. Volte)
        anfang_gesehen = False
        k = 0
        while k < len(takte):
            takt = takte[k]
            if _hat_wiederholung(takt, "forward"):
                anfang_gesehen = True
            if _hat_wiederholung(takt, "backward"):
                if not anfang_gesehen and beginn is not None and beginn <= k:
                    _strich_setzen(takte[beginn], "left", wiederholung="forward", stil="heavy-light")
                    protokoll(f"Wiederholungsanfang in Takt {takte[beginn].get('number')} ergänzt (im Druck nicht gesetzt)")
                # Ende der Volte, die direkt folgt
                j = k + 1
                if j < len(takte) and any(e.get("type") == "start" for b in takte[j].findall("barline") for e in b.findall("ending")):
                    while j < len(takte) - 1 and not any(e.get("type") in ("stop", "discontinue") for b in takte[j].findall("barline")
                                                         if b.get("location", "right") == "right" for e in b.findall("ending")):
                        if j - k >= 8:
                            break
                        j += 1
                    j += 1
                beginn = j
                anfang_gesehen = False
            k += 1
    return wurzel


def leere_takte_entfernen(wurzel):
    """Takte ohne Noten (homr erzeugt sie an Wiederholungszeichen und Volten) entfernen;
    ihre Angaben (Taktart, Wiederholung, Volte, Zeilenumbruch) wandern in den Nachbartakt."""
    entfernt = 0
    for teil in wurzel.iter("part"):
        takte = list(teil.findall("measure"))
        weg = set()
        for k, takt in enumerate(takte):
            if takt.find("note") is not None or takt.find("attributes/measure-style") is not None:
                continue
            naechster = next((t for t in takte[k + 1:] if id(t) not in weg), None)
            vorheriger = next((t for t in reversed(takte[:k]) if id(t) not in weg), None)
            position = 0
            for kind in list(takt):
                if kind.tag == "barline" and kind.get("location", "right") == "right" and vorheriger is not None:
                    vorheriger.append(kind)
                elif naechster is not None:
                    naechster.insert(position, kind)
                    position += 1
            teil.remove(takt)
            weg.add(id(takt))
            entfernt += 1
        for nr, takt in enumerate(teil.findall("measure"), 1):
            takt.set("number", str(nr))
    if entfernt:
        protokoll(f"{entfernt} leere Takte entfernt")
    return wurzel


def mehrtaktpausen_auffuellen(wurzel):
    """MusicXML verlangt bei einer Mehrtaktpause über N Takte auch N Takte in der Datei (die
    Angabe <multiple-rest> fasst sie nur in der Darstellung zusammen). homr schreibt nur einen Takt –
    Notenprogramme würden dann die folgenden N-1 Takte verschlucken."""
    for teil in wurzel.iter("part"):
        divisions, beats, beat_type = 1, 4, 4
        for takt in list(teil.findall("measure")):
            for attr in takt.findall("attributes"):
                if attr.findtext("divisions"):
                    divisions = int(attr.findtext("divisions"))
                t = attr.find("time")
                if t is not None:
                    beats, beat_type = int(t.findtext("beats")), int(t.findtext("beat-type"))
            mr = takt.find("attributes/measure-style/multiple-rest")
            if mr is None:
                continue
            n = int(mr.text)
            dauer = str(int(round(divisions * beats * 4 / beat_type)))
            if takt.find("note") is None:
                note = ET.SubElement(takt, "note")
                ET.SubElement(note, "rest", measure="yes")
                ET.SubElement(note, "duration").text = dauer
                ET.SubElement(note, "voice").text = "1"
                rechte = [b for b in takt.findall("barline") if b.get("location", "right") == "right"]
                for b in rechte:                       # Taktstrich rechts bleibt am Ende
                    takt.remove(b)
                    takt.append(b)
            for note in takt.findall("note"):
                if note.find("rest") is not None:
                    note.find("rest").set("measure", "yes")
                    if note.find("duration") is not None:
                        note.find("duration").text = dauer
                    if note.find("type") is not None:
                        note.remove(note.find("type"))
            rechts = [b for b in takt.findall("barline") if b.get("location", "right") == "right"]
            for b in rechts:
                takt.remove(b)
            pos = list(teil).index(takt)
            letzter = takt
            for k in range(1, n):
                neu = ET.Element("measure", number="0")
                note = ET.SubElement(neu, "note")
                ET.SubElement(note, "rest", measure="yes")
                ET.SubElement(note, "duration").text = dauer
                ET.SubElement(note, "voice").text = "1"
                teil.insert(pos + k, neu)
                letzter = neu
            for b in rechts:
                letzter.append(b)
        for nr, takt in enumerate(teil.findall("measure"), 1):
            takt.set("number", str(nr))
    return wurzel


def xml_aufraeumen(wurzel):
    """Unvollständige Tremolo-Paare entfernen und unsinnige Taktartwechsel (z. B. 1/2 statt 2/4,
    gleiche Taktlänge wie bisher) streichen."""
    for teil in wurzel.iter("part"):
        noten = list(teil.iter("note"))
        for i, note in enumerate(noten):
            for orn in note.iter("ornaments"):
                for tr in list(orn.findall("tremolo")):
                    typ = tr.get("type", "single")
                    partner = None
                    if typ == "start" and i + 1 < len(noten):
                        partner = next(iter(noten[i + 1].iter("tremolo")), None)
                        ok = partner is not None and partner.get("type") == "stop"
                    elif typ == "stop" and i > 0:
                        partner = next(iter(noten[i - 1].iter("tremolo")), None)
                        ok = partner is not None and partner.get("type") == "start"
                    else:
                        ok = typ == "single"
                    if not ok:
                        orn.remove(tr)
                        protokoll("Unvollständiges Tremolo entfernt")
        taktarten_ordnen(teil)
    return wurzel


def boegen_richten(wurzel):
    """Bindebögen ohne Anfang oder Ende reparieren. homr liest jede Zeile für sich; geht ein Bogen über den
    Zeilenwechsel oder wird eine Marke übersehen, bleibt der Bogen offen – und der Notensatz zieht ihn dann
    bis zum nächsten losen Ende über die halbe Seite. Ein offener Bogen wird so lang wie die geschlossenen
    Bögen an gleicher Stelle in gleich gebauten Takten; gibt es kein Vorbild, reicht er bis zur nächsten Note
    anderer Höhe. Geht beides nicht (Pause dazwischen, Stückende), fällt die Marke weg. Ein Paar, das über
    mehr als sechs Takte oder über Pausen hinweg in einen fernen Takt reicht, gilt als zwei lose Marken.
    Außerdem: Trägt eine Note Ende und Anfang eines Haltebogens, steht das Ende zuerst."""
    geschlossen = entfernt = 0
    for teil in wurzel.iter("part"):
        folge = []                                      # (Takt, Stelle im Takt, Note) ohne Akkord-Nebentöne
        bauart = {}
        for takt in teil.findall("measure"):
            eigene = [n for n in takt.findall("note") if n.find("chord") is None]
            bauart[takt] = tuple(("P" if n.find("rest") is not None else "g" if n.find("grace") is not None else "")
                                 + (n.findtext("type") or "") + "." * len(n.findall("dot")) for n in eigene)
            folge += [(takt, i, n) for i, n in enumerate(eigene)]
        for _, _, n in folge:
            notation = n.find("notations")
            if notation is None:
                continue
            gehalten = notation.findall("tied")
            if [t.get("type") for t in gehalten] == ["start", "stop"]:
                stelle = list(notation).index(gehalten[0])
                notation.remove(gehalten[1])
                notation.insert(stelle, gehalten[1])
            if n.find("rest") is not None:              # eine Pause trägt keinen Bogen
                for b in notation.findall("slur"):
                    notation.remove(b)
                    entfernt += 1

        def marken(g, art):
            notation = folge[g][2].find("notations")
            return [b for b in notation.findall("slur") if b.get("type") == art] if notation is not None else []

        def ton(g):
            n = folge[g][2]
            return None if n.find("rest") is not None else (n.findtext("pitch/step"), n.findtext("pitch/octave"), n.findtext("pitch/alter"))

        nummer = {takt: k for k, takt in enumerate(teil.findall("measure"))}

        def glaubhaft(a, z):
            """Anfang und Ende gehören zusammen: nicht über viele Takte, nicht über Pausen hinweg in die Ferne
            (sonst wären zwei lose Marken, zwischen denen zufällig keine dritte steht, ein Riesenbogen)."""
            weite = nummer[folge[z][0]] - nummer[folge[a][0]]
            return weite <= 6 and (weite < 2 or all(ton(g) is not None for g in range(a + 1, z)))

        paare, lose_anfaenge, lose_enden, offen = [], [], [], None
        for g in range(len(folge)):
            if marken(g, "stop"):
                if offen is None:
                    lose_enden.append(g)
                elif glaubhaft(offen, g):
                    paare.append((offen, g))
                else:
                    lose_anfaenge.append(offen)
                    lose_enden.append(g)
                offen = None
            if marken(g, "start"):
                if offen is not None:
                    lose_anfaenge.append(offen)
                offen = g
        if offen is not None:
            lose_anfaenge.append(offen)
        if not lose_anfaenge and not lose_enden:
            continue
        vorbild_anfang, vorbild_ende = defaultdict(Counter), defaultdict(Counter)
        for a, z in paare:
            vorbild_anfang[(bauart[folge[a][0]], folge[a][1])][z - a] += 1
            vorbild_ende[(bauart[folge[z][0]], folge[z][1])][z - a] += 1

        def moeglich(a, z):
            """Ein Bogen a–z: nur Noten, höchstens in den Nachbartakt, keine fremde Marke dazwischen."""
            if not (0 <= a < z < len(folge)) or any(ton(g) is None for g in range(a, z + 1)):
                return False
            if any(marken(g, "start") or marken(g, "stop") for g in range(a + 1, z)):
                return False
            takte = list(dict.fromkeys(folge[g][0] for g in range(a, z + 1)))
            return len(takte) <= 2 and len({ton(g) for g in range(a, z + 1)}) > 1

        def marke_setzen(g, art):
            n = folge[g][2]
            notation = n.find("notations")
            if notation is None:
                notation = ET.SubElement(n, "notations")
            notation.insert(0 if art == "stop" else len(notation), ET.Element("slur", type=art, number="1"))

        def weg(g, art):
            for b in marken(g, art):
                folge[g][2].find("notations").remove(b)

        for a in lose_anfaenge:
            spannen = [sp for sp, _ in vorbild_anfang[(bauart[folge[a][0]], folge[a][1])].most_common()] + [1, 2]
            ziel = next((a + sp for sp in spannen if moeglich(a, a + sp) and not marken(a + sp, "stop")), None)
            if ziel is None:
                weg(a, "start")
                entfernt += 1
            else:
                marke_setzen(ziel, "stop")
                geschlossen += 1
        for z in lose_enden:
            spannen = [sp for sp, _ in vorbild_ende[(bauart[folge[z][0]], folge[z][1])].most_common()] + [1, 2]
            ziel = next((z - sp for sp in spannen if moeglich(z - sp, z) and not marken(z - sp, "start")), None)
            if ziel is None:
                weg(z, "stop")
                entfernt += 1
            else:
                marke_setzen(ziel, "start")
                geschlossen += 1
    if geschlossen or entfernt:
        protokoll(f"Offene Bindebögen: {geschlossen} geschlossen, {entfernt} lose Marken entfernt")
    return wurzel


def _takt_laenge(takt, divisions):
    """Tatsächliche Länge eines Taktes (in Ganzen) aus den Notendauern; None bei Ganztakt- und
    Mehrtaktpausen (die passen sich jeder Taktart an)."""
    if takt.find("attributes/measure-style/multiple-rest") is not None:
        return None
    stimmen = {}
    for n in takt.findall("note"):
        if n.find("chord") is not None or n.find("grace") is not None:
            continue
        r = n.find("rest")
        if r is not None and r.get("measure") == "yes":
            return None
        d = n.findtext("duration")
        if d is None:
            continue
        v = n.findtext("voice") or "1"
        stimmen[v] = stimmen.get(v, 0) + int(d)
    if not stimmen:
        return None
    return Fraction(max(stimmen.values()), 4 * divisions)


def schoene_taktart(laenge, nenner):
    """Übliche Schreibweise für eine Taktlänge (in Ganzen), bevorzugt mit dem gegebenen Nenner:
    1/2 -> 2/4, 3/8 -> 3/8, 1 -> 4/4 (bei Nenner 4)."""
    for n in (nenner, 4, 8, 2, 16):
        b = laenge * n
        if b.denominator == 1 and b >= 2:
            return int(b), n
    return None, None


def _zeit_setzen(takt, beats, beat_type):
    """<time> im Takt setzen oder anlegen (Reihenfolge nach MusicXML: divisions, key, time, …)."""
    attr = takt.find("attributes")
    if attr is None:
        attr = ET.Element("attributes")
        takt.insert(0, attr)
    zeit = attr.find("time")
    if zeit is None:
        zeit = ET.Element("time")
        pos = sum(1 for k in attr if k.tag in ("footnote", "level", "divisions", "key"))
        attr.insert(pos, zeit)
        ET.SubElement(zeit, "beats")
        ET.SubElement(zeit, "beat-type")
    zeit.find("beats").text = str(beats)
    zeit.find("beat-type").text = str(beat_type)


def taktarten_ordnen(teil):
    """homr liest von der Taktangabe nur den Nenner und schätzt den Zähler aus den Takten. Daraus:
      - „1/2“ o. ä. -> übliche Schreibweise (2/4),
      - gleiche Länge wie eine frühere Taktart -> deren Schreibweise (2/2 nach 4/4 -> 4/4),
      - Wechsel für nur einen Takt (2/4-Takt im 4/4): der Takt bekommt seine eigene Taktart, der
        nächste wieder die alte (homr übersieht die Rückkehr oft),
      - doppelte Angaben ohne Änderung entfallen."""
    takte = teil.findall("measure")
    divisions, laengen = 1, []
    for t in takte:
        for attr in t.findall("attributes"):
            if attr.findtext("divisions"):
                divisions = int(attr.findtext("divisions"))
        laengen.append(_takt_laenge(t, divisions))
    bisher, aktuell = [], None
    for k, takt in enumerate(takte):
        zeit = takt.find("attributes/time")
        if zeit is None:
            continue
        b, bt = int(zeit.findtext("beats")), int(zeit.findtext("beat-type"))
        laenge = Fraction(b, bt)
        if b < 2:
            b, bt = schoene_taktart(laenge, bt)
            if b is None:
                b, bt = int(zeit.findtext("beats")), int(zeit.findtext("beat-type"))
        frueher = next(((fb, fbt) for fb, fbt in reversed(bisher) if Fraction(fb, fbt) == laenge), None)
        if frueher:
            b, bt = frueher
        L = laengen[k]
        nach_abschnitt = k > 0 and any(b_.find("bar-style") is not None or b_.find("repeat") is not None
                                       for b_ in takte[k - 1].findall("barline"))
        if (k > 0 and L is not None and L != laenge and L >= Fraction(1, 4) and not nach_abschnitt
                and k + 1 < len(takte) and laengen[k + 1] == laenge
                and takte[k + 1].find("attributes/time") is None):
            nb, nbt = schoene_taktart(L, bt)
            if nb:
                _zeit_setzen(takt, nb, nbt)
                _zeit_setzen(takte[k + 1], b, bt)
                protokoll(f"Taktwechsel nur für einen Takt: {nb}/{nbt}, danach wieder {b}/{bt}")
                bisher += [(nb, nbt), (b, bt)]
                aktuell = (nb, nbt)                    # der nächste Takt bringt (b, bt) zurück
                continue
        if aktuell == (b, bt):
            for attr in takt.findall("attributes"):
                if attr.find("time") is zeit:
                    attr.remove(zeit)
            protokoll(f"Doppelte Taktangabe {b}/{bt} entfernt")
            continue
        _zeit_setzen(takt, b, bt)
        bisher.append((b, bt))
        aktuell = (b, bt)


def als_musicxml(stimmen, titel="", leere_weg=True, bild=None):
    """MusicXML aus den gelesenen Stimmen. (bild wird nicht mehr gebraucht – Mehrtaktpausen werden
    schon beim Lesen am Bild geprüft; der Parameter bleibt für ältere Aufrufer.)"""
    wurzel = generate_xml(XmlGeneratorArguments(), stimmen, titel)
    wurzel = wiederholungen_richten(wurzel)
    if leere_weg:
        wurzel = leere_takte_entfernen(wurzel)
        wurzel = faulenzer_aufloesen(wurzel)
        wurzel = xml_aufraeumen(wurzel)
        wurzel = boegen_richten(wurzel)
    if leere_weg:
        wurzel = mehrtaktpausen_auffuellen(wurzel)
        wurzel = takte_nummerieren(wurzel)
        wurzel = wiederholungen_nach_bild(wurzel)
        wurzel = texte_einsetzen(wurzel)
    wurzel = vorzeichen_setzen(wurzel)
    return ET.tostring(wurzel, encoding="unicode", xml_declaration=True)


VORZEICHEN_NAME = {1: "sharp", -1: "flat", 0: "natural", 2: "double-sharp", -2: "flat-flat"}
NACH_VORZEICHEN = ("time-modification", "stem", "notehead", "notehead-text", "staff", "beam", "notations",
                   "lyric", "play", "listen")


def vorzeichen_setzen(wurzel):
    """Sichtbare Vorzeichen (<accidental>) eintragen. homr schreibt nur die Tonhöhe (<alter>) – der
    Notensatz (Verovio) zeichnet dann kein Vorzeichen, ein Fis in C-Dur stünde als F da. Regeln:
    Vorzeichen der Tonart gelten immer, ein Vorzeichen im Takt gilt für diesen Ton (gleiche Stufe
    und Oktave) bis zum Taktende; eine übergebundene Note bekommt keines. Dabei wird auch die von
    MusicXML verlangte Reihenfolge step, alter, octave hergestellt."""
    for teil in wurzel.iter("part"):
        fifths = 0
        for takt in teil.findall("measure"):
            stand = {}                                 # (Notensystem, Stufe, Oktave) -> alter
            for el in takt:
                if el.tag == "attributes":
                    k = el.find("key/fifths")
                    if k is not None and (k.text or "").strip().lstrip("-").isdigit():
                        fifths = int(k.text)
                    continue
                if el.tag != "note":
                    continue
                for alt in el.findall("accidental"):
                    el.remove(alt)
                p = el.find("pitch")
                if p is None:
                    continue
                schritt, okt, a = p.find("step"), p.find("octave"), p.find("alter")
                if schritt is None or okt is None:
                    continue
                if a is not None:                      # Reihenfolge step, alter, octave
                    p.remove(a)
                    p.insert(list(p).index(okt), a)
                try:
                    alter = int(float(a.text)) if a is not None and a.text else 0
                except ValueError:
                    continue
                schluessel = (el.findtext("staff") or "1", schritt.text, okt.text)
                gilt = stand.get(schluessel, tonart_alter(fifths, schritt.text))
                gebunden = any(t.get("type") == "stop" for t in el.findall("tie"))
                if gebunden:
                    continue
                if alter != gilt and alter in VORZEICHEN_NAME:
                    zeichen = ET.Element("accidental")
                    zeichen.text = VORZEICHEN_NAME[alter]
                    pos = next((i for i, k in enumerate(el) if k.tag in NACH_VORZEICHEN), len(el))
                    el.insert(pos, zeichen)
                stand[schluessel] = alter
    return wurzel


def takte_nummerieren(wurzel):
    """Takte fortlaufend nummerieren. Ein Auftakt am Anfang (kürzer als die Taktart) ist Takt 0
    (implicit), damit die Nummern zu den gedruckten passen."""
    for teil in wurzel.iter("part"):
        takte = teil.findall("measure")
        if not takte:
            continue
        divisions, beats, beat_type = 1, 4, 4
        for attr in takte[0].findall("attributes"):
            if attr.findtext("divisions"):
                divisions = int(attr.findtext("divisions"))
            t = attr.find("time")
            if t is not None:
                beats, beat_type = int(t.findtext("beats")), int(t.findtext("beat-type"))
        laenge = _takt_laenge(takte[0], divisions)
        auftakt = len(takte) > 1 and laenge is not None and 0 < laenge < Fraction(beats, beat_type)
        for k, takt in enumerate(takte):
            takt.set("number", str(k if auftakt else k + 1))
            takt.attrib.pop("implicit", None)
        if auftakt:
            takte[0].set("implicit", "yes")
    return wurzel


def zeichen_text(stimmen):
    zeilen = []
    for v, stimme in enumerate(stimmen):
        for s in stimme:
            alt = getattr(s, "alt_rhythm", None)
            alt_t = " | " + ", ".join(f"{t}:{p:.2f}" for t, p in alt[:4]) if alt else ""
            zeilen.append(f"{v} {s.rhythm:<16} {s.pitch:<4} {s.lift:<3} {s.position:<6}{' VORGABE' if getattr(s, 'vorgegeben', False) else ''}{alt_t}")
    return "\n".join(zeilen)


if __name__ == "__main__":
    import os
    ziel_ordner = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--ziel=")), "ausgabe")
    os.makedirs(ziel_ordner, exist_ok=True)
    roh = "--roh" in sys.argv
    for pfad in [a for a in sys.argv[1:] if not a.startswith("--")]:
        t = time.time()
        stimmen = erkennen(pfad, schluessel_vorgabe="--ohne-vorgabe" not in sys.argv and not roh, reparatur=not roh)
        name = os.path.splitext(os.path.basename(pfad))[0]
        import cv2
        xml = als_musicxml(stimmen, leere_weg=not roh, bild=None if roh else cv2.imread(pfad, 0))
        open(os.path.join(ziel_ordner, name + ".musicxml"), "w").write(xml)
        open(os.path.join(ziel_ordner, name + ".zeichen.txt"), "w").write(zeichen_text(stimmen))
        open(os.path.join(ziel_ordner, name + ".protokoll.txt"), "w").write("\n".join(ZUSTAND["protokoll"]))
        print(f"{name}: {time.time() - t:.1f} s, {len(ZUSTAND['protokoll'])} Eingriffe", file=sys.stderr)
