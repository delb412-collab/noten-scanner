# Notenleser (Versuchsstand)

Liest bereinigte Notenseiten und setzt sie neu (MusicXML + PDF). Baut auf
[homr](https://github.com/liebharc/homr) auf (Notenerkennung mit neuronalen Netzen, Lizenz AGPL-3.0)
und ergänzt eigene Prüfungen:

- Schlüssel am Zeilenanfang aus der Vorzeile übernehmen (statt erfundener Alt-/Tenorschlüssel)
- Taktprüfung: Geht ein Takt nicht auf, wird mit dem zweitwahrscheinlichsten Notenwert neu gelesen;
  Auftakte und ihre Ergänzungstakte werden erkannt; Sechzehntel/Achtel-Verwechslungen und
  fehlende Triolen werden über die Taktlänge korrigiert
- Taktwechsel: Die Soll-Länge gilt je Taktart-Abschnitt und wird von Zeile zu Zeile übernommen.
  Der erste Takt nach einer Taktangabe wird nie „repariert“ (z. B. ein einzelner 2/4-Takt im
  4/4-Stück); im MusicXML bekommt er seine eigene Taktart und der nächste Takt wieder die alte.
  homr liest von Taktangaben nur den Nenner – „1/2“ wird zu 2/4, gleiche Länge wie früher zu
  der früheren Schreibweise (2/2 nach 4/4 -> 4/4)
- Triolen: homrs eigene Regel (Triolen in jedem zu kurzen Takt entfernen) ist ersetzt – Triolen
  fallen nur weg, wenn der Takt danach genau die Länge seines Taktart-Abschnitts hat
- Neuer Teil mit Tonartwechsel mitten in der Zeile (Trio) wird erkannt
- Vorzeichen gelten bis zum Taktende (Prüfung mit den Alternativen des Decoders)
- Sichtbare Vorzeichen: homr schreibt nur die Tonhöhe (`<alter>`); ohne `<accidental>` zeichnet
  der Notensatz kein Vorzeichen. Sie werden nach Tonart und Taktregel eingetragen
- Pause mit Tonhöhe: homr macht daraus eine Note – hier bleibt es eine Pause
- Gegenprobe am Bild: Akkordnoten ohne Notenkopf (Hals, Tenutostrich, Dynamik) werden entfernt
- Mehrtaktpausen am Bild (schon beim Lesen, damit die Taktprüfung davon weiß): moderner Balken
  mit Zahl darüber (auch „1“), Balken auf einer anderen Linie mit Zahl im System, alte
  Kirchenpausen-Blöcke mit Zahl darunter. Die Zahl im Bild gilt vor homrs Schätzung
- Faulenzer (Taktwiederholung): homr kennt dieses Zeichen nicht und liest dort Pausen, Unsinn, eine
  falsche Taktangabe oder lässt den Takt aus. Das Zeichen wird im Bild gesucht (Schrägstrich
  zwischen einem Punkt links oben und einem rechts unten; geprüft mit fünf Notenschriften und
  echten Vereinsnoten, auch nach simuliertem Handyfoto), homrs Lesung zwischen den Taktstrichen
  ersetzt und der Takt bekommt die Noten des Vortakts (Abspielen, Transponieren, andere
  Notenprogramme). Angezeigt wird er weiter als Faulenzer. Zweitakt-Faulenzer (auf dem
  Taktstrich) werden ausgeschrieben, weil der Notensatz sie aus MusicXML nicht richtig zeichnet
- Viertel- oder Achtelpause: Geht ein Takt um eine Achtel nicht auf, entscheidet die Höhe des
  Pausenzeichens im Bild (Viertelpause ≈ 3, Achtelpause ≈ 2 Linienabstände)
- Artikulationen (Staccato, Tenuto, Akzent, Marcato) am Bild (`bildpruefung.Zeichenfinder`): homr
  verwechselt oft Punkt und Strich, liest Punkte als Keil und übersieht Striche unter dem System
  sowie den Strich unter einem Akzent („≥“). Am Notenkopf (Lage aus homrs Notenkopf-Maske, damit
  kein Vorzeichen als Kopf gilt) wird ober- und unterhalb nach kleinen Zeichen gesucht und deren
  Form bestimmt; zusammengewachsene Zeichen werden zerlegt, ein an einer Notenlinie hängender
  Linienrest wird abgeschnitten. Zeigt das Bild ein Zeichen klar, entscheidet es; findet es
  nichts, bleibt homrs Lesart. Nicht gezählt werden: Zeichen auf der Halsseite (außer Akzenten),
  Stücke auf einer Notenlinie, Hilfslinien, Bogenenden, Punktreihen und Textpunkte („rit.“),
  der Punkt einer Fermate, die Zahl einer Triolenklammer. Den Keil entscheidet das Bild nicht
  (alte Drucke haben echte Keile, Fotos lassen Punkte länglich aussehen); Fermaten bleiben bei homr
- Wiederholungszeichen und Volta-Klammern am Bild (`wiederholungen_suchen`, `volten_suchen`): homr
  schreibt den Wiederholungsanfang an den rechten Rand eines eigenen Takts (der Notensatz zeigt
  dann ein Wiederholungsende), übersieht Zeichen und nummeriert Volten falsch. Gesucht wird der
  dicke Taktstrich mit zwei Punkten daneben (auch wenn die Punkte am Strich kleben oder auf
  Linienresten sitzen) und die Klammerlinie mit Haken über der Zeile; die Zahl liest die
  Texterkennung. Findet das Bild Zeichen einer Art, ersetzen sie homrs Lesart dieser Art. Fehlt
  im Druck der Anfang zu einem Wiederholungsende, wird er hinter der vorigen 2. Volte ergänzt
  (sonst spränge die Wiederholung an den Stückanfang)
- Textangaben, Studierzeichen und Kopf (`texte_lesen`, `angaben_lesen`, `studierzeichen_suchen`,
  `kopf_lesen`): Die Texterkennung (rapidocr) liest die Seite einmal. Texte über und unter den
  Zeilen („Trio“, „Fine“, „D.C. al Fine“, „dolce“, „Lento ≈ 62“ → „Lento (62)“) kommen als
  `<words>` an die Note, bei der sie stehen; eingerahmte Buchstaben oder Zahlen als `<rehearsal>`
  an ihren Taktstrich. Nicht übernommen werden Urheber- und Verlagszeilen, die Fußzeile (auch eine
  Überschrift wie „Anmerkung“ mit Fließtext darunter), Zahlen und einzelne Buchstaben. Der größte Text über den Noten ist der Titel, ein Text mit einem
  Instrumentennamen die Stimme; daraus wird die Stimmung fürs Transponieren abgeleitet
  (`ZUSTAND["kopf"]`, der Server gibt es als `vorschlag` weiter)
- Offene Bindebögen (`boegen_richten`): homr liest jede Zeile für sich; ein Bogen über den
  Zeilenwechsel verliert sein Ende, und der Notensatz zieht ihn dann bis zur nächsten losen
  Marke über die halbe Seite. Ein offener Bogen wird so lang wie die geschlossenen Bögen an
  gleicher Stelle in gleich gebauten Takten, sonst reicht er bis zur nächsten Note anderer Höhe;
  geht beides nicht, fällt die Marke weg. Das ist eine Schätzung: der Bogen kann kürzer sein
  als im Druck
- Auftakt am Anfang ist Takt 0, damit die Taktnummern zu den gedruckten passen
- Logo/Text nach dem Schlussstrich wird verworfen, leere Takte an Wiederholungen entfernt
- Balken nach Taktart, damit Hälse einer Gruppe in dieselbe Richtung zeigen (`neusatz.py`)
- Alle Prüfungen am Bild sind eine Zugabe: schlagen sie fehl (z. B. Texterkennung nicht
  verfügbar), läuft die Erkennung ohne sie weiter und vermerkt es im Protokoll

Messung (`pruefstand.py`, Noten mit Tonhöhe, Vorzeichen und Wert; Faulenzer zählen als „simile“):
fünf Entwicklungsseiten 99,5 %, eine neue, nie zum Entwickeln benutzte Seite („Lummerland“) vorher
92,6 %, jetzt 99,3 %.

Artikulationen (Grundwahrheit mit `# Artikulationen erfasst`, Aufruf mit `artikulation`):
„How Deep“ (Entwicklung) homr 76,0 % → 100 % (96/96, 1 falsch dazu statt 6);
„Lummerland“ (Foto) homr 59,7 % (65 falsch dazu) → beim ersten Test 73,9 % → nach Anpassung an
Foto-Punkte (länglich, an der Linie hängend) 92,0 % (11 falsch dazu) → mit Punkten direkt am
Kopf und dicken Strichen 95,5 % (168/176, 5 falsch dazu). Auf den übrigen Seiten
wurde jede Änderung am Bild nachgeprüft (Tiroler: 35 Punkte statt Keil, 2 Striche statt Keil).

Wiederholungszeichen und Volta-Klammern (von Hand ausgezählt, vier Seiten mit Wiederholungen):
vorher 33 von 55 richtig, jetzt 55 von 55; dazu ein ergänzter Wiederholungsanfang (Mars, Takt 88),
der im Druck fehlt. Auf den zwei Seiten ohne Wiederholungen kein Fehlfund. Texte und
Studierzeichen: auf den vier Seiten mit Studierzeichen alle 20 gefunden (am Bild nachgezählt), auf
keiner Seite ein falsches; Titel und Stimme auf vier von sechs Seiten richtig, einmal mit Lesefehler
(„Madi“ statt „Madl“), einmal nichts (handbeschriftete Kopie). Die Textangaben (52 auf sechs Seiten)
sind nicht einzeln ausgezählt; beim Durchsehen fielen Lesefehler der Texterkennung auf („Stampfl“,
„preterablty“) und Stichnoten-Hinweise, die mitgelesen werden („Th., Hr.“). Offene Bindebögen: 10
geschlossen, 2 lose Marken entfernt.

**Wichtig zur Einordnung:** Alle Bild-Regeln (Artikulation, Wiederholungen, Volten, Texte, Bögen)
sind an diesen sechs Seiten entwickelt und an denselben Seiten gemessen. Eine unabhängige
Prüfseite gibt es dafür nicht; auf anderen Notenschriften werden die Zahlen schlechter sein.

Wegen homr steht dieser Teil unter der **AGPL-3.0**.

Benötigt: Python ≥ 3.11, `pip install git+https://github.com/liebharc/homr onnxruntime music21 verovio cairosvg pypdf rapidocr`,
einmalig `homr --init` (lädt ca. 150 MB Modelle).

    python notenleser.py --ziel=ausgabe seite1.png seite2.png
    python neusatz.py ausgabe/seite1.musicxml "Titel" "Instrument" Titel.pdf
