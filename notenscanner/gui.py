"""Grafische Oberfläche (tkinter) – für alle, die keine Kommandozeile mögen."""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import cv2
from PIL import Image, ImageTk

from . import __version__
from .cli import beschreibung
from .dateien import ALLE_ENDUNGEN, seiten_laden
from .stapel import Rueckmeldung, auftraege_sammeln, stapel_verarbeiten, standard_jobs, standard_ziel
from .verarbeitung import Einstellungen, verarbeiten

PDF_AUSWAHL = {"Ein PDF pro Ordner": "ordner", "Ein PDF pro Foto": "datei", "Kein PDF": "aus"}
ENTRAUSCHEN_AUSWAHL = {"Aus": 0, "Normal": 1, "Stark": 2, "Sehr stark": 3}
FORMAT_AUSWAHL = {"Automatisch (meist A4)": "auto", "A4": "A4", "A5": "A5", "Letter": "Letter",
                  "Originalgröße": "original"}
VORSCHAU_HOEHE = 460


class NotenScannerApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title(f"Noten-Scanner {__version__}")
        root.minsize(1000, 700)
        stil = ttk.Style()
        if stil.theme_use() == "default":
            stil.theme_use("clam")

        self.eingaben: list[Path] = []
        self.dateien: list[Path] = []      # alle gefundenen Dateien (für die Vorschau)
        self.vorschau_index = 0
        self.nachrichten: queue.Queue = queue.Queue()
        self.laeuft = False
        self.abbruch = threading.Event()
        self._bilder_tk: list[ImageTk.PhotoImage] = []

        standard = Einstellungen()
        self.ziel = tk.StringVar()
        self.auswahl_text = tk.StringVar(value="Noch nichts ausgewählt")
        self.pdf = tk.StringVar(value="Ein PDF pro Ordner")
        self.nur_pdf = tk.BooleanVar(value=False)
        self.modus = tk.StringVar(value=standard.modus)
        self.format = tk.StringVar(value="Automatisch (meist A4)")
        self.empfindlichkeit = tk.DoubleVar(value=standard.empfindlichkeit)
        self.entrauschen = tk.StringVar(value="Normal")
        self.perspektive = tk.BooleanVar(value=standard.perspektive)
        self.begradigen = tk.BooleanVar(value=standard.begradigen)
        self.drehen = tk.BooleanVar(value=standard.drehen)
        self.rand = tk.BooleanVar(value=standard.rand_saeubern)
        self.bleistift = tk.BooleanVar(value=standard.bleistift_entfernen)
        self.linien = tk.BooleanVar(value=standard.linien_reparieren)
        self.nur_neue = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="Bereit.")

        self._aufbauen()
        self.root.after(100, self._nachrichten_abholen)

    # ---------- Aufbau ----------
    def _aufbauen(self) -> None:
        links = ttk.Frame(self.root, padding=10)
        links.grid(row=0, column=0, sticky="ns")
        rechts = ttk.Frame(self.root, padding=(0, 10, 10, 10))
        rechts.grid(row=0, column=1, sticky="nsew")
        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(0, weight=1)

        # 1. Auswahl
        f = ttk.LabelFrame(links, text="1. Noten auswählen", padding=8)
        f.pack(fill="x")
        ttk.Label(f, textvariable=self.auswahl_text, wraplength=330).pack(anchor="w")
        knoepfe = ttk.Frame(f)
        knoepfe.pack(fill="x", pady=(6, 0))
        ttk.Button(knoepfe, text="Ordner wählen …", command=self._ordner_waehlen).pack(side="left")
        ttk.Button(knoepfe, text="Dateien wählen …", command=self._dateien_waehlen).pack(side="left", padx=6)

        # 2. Ziel
        f = ttk.LabelFrame(links, text="2. Speichern in", padding=8)
        f.pack(fill="x", pady=8)
        ttk.Entry(f, textvariable=self.ziel, width=38).pack(side="left", fill="x", expand=True)
        ttk.Button(f, text="…", width=3, command=self._ziel_waehlen).pack(side="left", padx=(4, 0))

        # 3. Einstellungen
        f = ttk.LabelFrame(links, text="3. Einstellungen", padding=8)
        f.pack(fill="x")
        f.columnconfigure(1, weight=1)
        zeile = 0

        def beschriftung(text: str) -> None:
            ttk.Label(f, text=text).grid(row=zeile, column=0, sticky="w", pady=2, padx=(0, 8))

        beschriftung("PDF erstellen:")
        ttk.Combobox(f, textvariable=self.pdf, values=list(PDF_AUSWAHL), state="readonly",
                     width=22).grid(row=zeile, column=1, sticky="ew")
        zeile += 1
        ttk.Checkbutton(f, text="Nur PDFs behalten (keine Einzelbilder)",
                        variable=self.nur_pdf).grid(row=zeile, column=1, sticky="w")
        zeile += 1
        beschriftung("Darstellung:")
        r = ttk.Frame(f)
        r.grid(row=zeile, column=1, sticky="w")
        ttk.Radiobutton(r, text="Glatt", value="glatt", variable=self.modus).pack(side="left")
        ttk.Radiobutton(r, text="Reines Schwarz/Weiß", value="hart", variable=self.modus).pack(side="left", padx=6)
        zeile += 1
        beschriftung("Papierformat:")
        ttk.Combobox(f, textvariable=self.format, values=list(FORMAT_AUSWAHL), state="readonly",
                     width=22).grid(row=zeile, column=1, sticky="ew")
        zeile += 1
        beschriftung("Empfindlichkeit:")
        r = ttk.Frame(f)
        r.grid(row=zeile, column=1, sticky="ew")
        wert = ttk.Label(r, width=4)
        wert.pack(side="right")
        ttk.Scale(r, from_=0.3, to=0.95, variable=self.empfindlichkeit,
                  command=lambda _=None: wert.configure(text=f"{self.empfindlichkeit.get():.2f}")
                  ).pack(side="left", fill="x", expand=True)
        wert.configure(text=f"{self.empfindlichkeit.get():.2f}")
        zeile += 1
        ttk.Label(f, text="mehr nach rechts: auch blasser Druck bleibt\nmehr nach links: mehr Schmutz verschwindet",
                  foreground="#666").grid(row=zeile, column=1, sticky="w")
        zeile += 1
        beschriftung("Flecken entfernen:")
        ttk.Combobox(f, textvariable=self.entrauschen, values=list(ENTRAUSCHEN_AUSWAHL), state="readonly",
                     width=22).grid(row=zeile, column=1, sticky="ew")
        zeile += 1
        for text, variable in (("Blatt im Foto suchen und entzerren", self.perspektive),
                               ("Schräglage korrigieren", self.begradigen),
                               ("Quer/kopfüber fotografierte Seiten drehen", self.drehen),
                               ("Notenlinien, Hälse und Taktstriche nachziehen", self.linien),
                               ("Dunkle Ränder und Schatten am Rand entfernen", self.rand),
                               ("Bleistift-Eintragungen entfernen", self.bleistift),
                               ("Bereits bearbeitete Dateien überspringen", self.nur_neue)):
            ttk.Checkbutton(f, text=text, variable=variable).grid(row=zeile, column=0, columnspan=2, sticky="w")
            zeile += 1

        # 4. Start
        f = ttk.Frame(links, padding=(0, 10, 0, 0))
        f.pack(fill="x")
        self.start_knopf = ttk.Button(f, text="▶  Alle bereinigen", command=self._start_oder_abbruch)
        self.start_knopf.pack(fill="x", ipady=4)
        self.fortschritt = ttk.Progressbar(f, mode="determinate")
        self.fortschritt.pack(fill="x", pady=6)
        ttk.Label(f, textvariable=self.status, wraplength=330).pack(anchor="w")
        self.oeffnen_knopf = ttk.Button(f, text="Ergebnisordner öffnen", command=self._ziel_oeffnen,
                                        state="disabled")
        self.oeffnen_knopf.pack(fill="x", pady=(6, 0))

        # Vorschau
        kopf = ttk.Frame(rechts)
        kopf.pack(fill="x")
        ttk.Button(kopf, text="◀", width=3, command=lambda: self._vorschau_blaettern(-1)).pack(side="left")
        ttk.Button(kopf, text="▶", width=3, command=lambda: self._vorschau_blaettern(1)).pack(side="left", padx=4)
        self.vorschau_knopf = ttk.Button(kopf, text="Vorschau berechnen", command=self._vorschau)
        self.vorschau_knopf.pack(side="left", padx=4)
        self.vorschau_name = ttk.Label(kopf, text="")
        self.vorschau_name.pack(side="left", padx=8)
        bilder = ttk.Frame(rechts)
        bilder.pack(fill="both", expand=True, pady=8)
        bilder.columnconfigure((0, 1), weight=1)
        ttk.Label(bilder, text="Vorher").grid(row=0, column=0)
        ttk.Label(bilder, text="Nachher").grid(row=0, column=1)
        self.vorher = ttk.Label(bilder, anchor="center", relief="sunken")
        self.vorher.grid(row=1, column=0, sticky="nsew", padx=(0, 4))
        self.nachher = ttk.Label(bilder, anchor="center", relief="sunken")
        self.nachher.grid(row=1, column=1, sticky="nsew", padx=(4, 0))
        bilder.rowconfigure(1, weight=1, minsize=VORSCHAU_HOEHE)

        protokoll = ttk.Frame(rechts)
        protokoll.pack(fill="both", expand=False)
        self.protokoll = tk.Text(protokoll, height=9, wrap="word", state="disabled", font=("TkFixedFont", 9))
        leiste = ttk.Scrollbar(protokoll, command=self.protokoll.yview)
        self.protokoll.configure(yscrollcommand=leiste.set)
        self.protokoll.pack(side="left", fill="both", expand=True)
        leiste.pack(side="right", fill="y")

    # ---------- Auswahl ----------
    def _ordner_waehlen(self) -> None:
        ordner = filedialog.askdirectory(title="Ordner mit Notenfotos wählen")
        if ordner:
            self._eingaben_setzen([Path(ordner)])

    def _dateien_waehlen(self) -> None:
        muster = " ".join(f"*{e} *{e.upper()}" for e in sorted(ALLE_ENDUNGEN))
        dateien = filedialog.askopenfilenames(title="Notenfotos wählen",
                                              filetypes=[("Bilder und PDFs", muster), ("Alle Dateien", "*")])
        if dateien:
            self._eingaben_setzen([Path(d) for d in dateien])

    def _ziel_waehlen(self) -> None:
        ordner = filedialog.askdirectory(title="Zielordner wählen")
        if ordner:
            self.ziel.set(ordner)

    def _eingaben_setzen(self, eingaben: list[Path]) -> None:
        self.eingaben = eingaben
        ziel = standard_ziel(eingaben)
        self.ziel.set(str(ziel))
        try:
            self.dateien = [a.quelle for a in auftraege_sammeln(eingaben, ziel)]
        except (ValueError, FileNotFoundError) as fehler:
            messagebox.showerror("Noten-Scanner", str(fehler))
            self.dateien = []
        if len(eingaben) == 1 and eingaben[0].is_dir():
            text = f"Ordner: {eingaben[0]}\n{len(self.dateien)} Datei(en) gefunden"
        else:
            text = f"{len(self.dateien)} Datei(en) ausgewählt"
        self.auswahl_text.set(text)
        self.vorschau_index = 0
        if self.dateien:
            self._vorschau()

    # ---------- Einstellungen ----------
    def _einstellungen(self) -> Einstellungen:
        e = Einstellungen(
            perspektive=self.perspektive.get(),
            begradigen=self.begradigen.get(),
            drehen=self.drehen.get(),
            modus=self.modus.get(),
            empfindlichkeit=round(self.empfindlichkeit.get(), 2),
            entrauschen=ENTRAUSCHEN_AUSWAHL[self.entrauschen.get()],
            rand_saeubern=self.rand.get(),
            bleistift_entfernen=self.bleistift.get(),
            linien_reparieren=self.linien.get(),
            format=FORMAT_AUSWAHL[self.format.get()],
        )
        e.pruefen()
        return e

    # ---------- Vorschau ----------
    def _vorschau_blaettern(self, schritt: int) -> None:
        if self.dateien:
            self.vorschau_index = (self.vorschau_index + schritt) % len(self.dateien)
            self._vorschau()

    def _vorschau(self) -> None:
        if not self.dateien:
            messagebox.showinfo("Noten-Scanner", "Bitte zuerst einen Ordner oder Dateien auswählen.")
            return
        datei = self.dateien[self.vorschau_index]
        self.vorschau_name.configure(text=f"{self.vorschau_index + 1}/{len(self.dateien)}: {datei.name}  (rechne …)")
        self.vorschau_knopf.configure(state="disabled")
        einstellungen = self._einstellungen()
        threading.Thread(target=self._vorschau_rechnen, args=(datei, einstellungen), daemon=True).start()

    def _vorschau_rechnen(self, datei: Path, einstellungen: Einstellungen) -> None:
        try:
            bild = seiten_laden(datei, einstellungen.dpi)[0]
            ergebnis = verarbeiten(bild, einstellungen, zwischenschritte=True)
            vorher = cv2.cvtColor(ergebnis.zwischenschritte["1_foto"], cv2.COLOR_BGR2RGB)
            self.nachrichten.put(("vorschau", datei, vorher, ergebnis.bild, ergebnis.infos))
        except Exception as fehler:
            self.nachrichten.put(("vorschau_fehler", datei, f"{type(fehler).__name__}: {fehler}"))

    def _vorschau_zeigen(self, datei: Path, vorher, nachher, infos: dict) -> None:
        self._bilder_tk = []
        for label, bild in ((self.vorher, vorher), (self.nachher, nachher)):
            pil = Image.fromarray(bild)
            hoehe = max(200, label.winfo_height() - 8) if label.winfo_height() > 50 else VORSCHAU_HOEHE
            breite = max(200, label.winfo_width() - 8) if label.winfo_width() > 50 else 340
            pil.thumbnail((breite, hoehe), Image.LANCZOS)
            foto = ImageTk.PhotoImage(pil)
            label.configure(image=foto)
            self._bilder_tk.append(foto)
        text = beschreibung(Rueckmeldung(datei, infos=[infos]))
        self.vorschau_name.configure(text=f"{self.vorschau_index + 1}/{len(self.dateien)}: {datei.name} – {text}")
        self.vorschau_knopf.configure(state="normal")

    # ---------- Stapel ----------
    def _start_oder_abbruch(self) -> None:
        if self.laeuft:
            self.abbruch.set()
            self.status.set("Wird nach den laufenden Bildern angehalten …")
            return
        if not self.eingaben:
            messagebox.showinfo("Noten-Scanner", "Bitte zuerst einen Ordner oder Dateien auswählen.")
            return
        try:
            einstellungen = self._einstellungen()
        except ValueError as fehler:
            messagebox.showerror("Noten-Scanner", str(fehler))
            return
        pdf = PDF_AUSWAHL[self.pdf.get()]
        if self.nur_pdf.get() and pdf == "aus":
            messagebox.showerror("Noten-Scanner", "„Nur PDFs behalten“ geht nicht zusammen mit „Kein PDF“.")
            return
        ziel = Path(self.ziel.get()) if self.ziel.get().strip() else None
        self.laeuft = True
        self.abbruch.clear()
        self.start_knopf.configure(text="■  Abbrechen")
        self.oeffnen_knopf.configure(state="disabled")
        self.fortschritt.configure(value=0, maximum=max(1, len(self.dateien)))
        self.status.set("Läuft …")
        self._protokoll(f"Start: {len(self.dateien)} Datei(en) → {ziel or standard_ziel(self.eingaben)}")
        argumente = dict(eingaben=self.eingaben, ziel=ziel, einstellungen=einstellungen, pdf=pdf,
                         nur_pdf=self.nur_pdf.get(), nur_neue=self.nur_neue.get(), jobs=standard_jobs())
        threading.Thread(target=self._stapel_rechnen, kwargs=argumente, daemon=True).start()

    def _stapel_rechnen(self, **argumente) -> None:
        try:
            z = stapel_verarbeiten(**argumente, abbrechen=self.abbruch.is_set,
                                   fortschritt=lambda f, g, r: self.nachrichten.put(("fortschritt", f, g, r)))
            self.nachrichten.put(("fertig", z))
        except Exception as fehler:
            self.nachrichten.put(("stapel_fehler", f"{type(fehler).__name__}: {fehler}"))

    def _nachrichten_abholen(self) -> None:
        try:
            while True:
                n = self.nachrichten.get_nowait()
                art = n[0]
                if art == "vorschau":
                    self._vorschau_zeigen(*n[1:])
                elif art == "vorschau_fehler":
                    self.vorschau_name.configure(text=f"{n[1].name}: {n[2]}")
                    self.vorschau_knopf.configure(state="normal")
                elif art == "fortschritt":
                    _, fertig, gesamt, r = n
                    self.fortschritt.configure(value=fertig, maximum=gesamt)
                    self.status.set(f"{fertig} von {gesamt} Dateien bearbeitet …")
                    self._protokoll(f"{r.quelle.name}: {beschreibung(r)}")
                elif art == "fertig":
                    self._fertig(n[1])
                elif art == "stapel_fehler":
                    self._fertig(None, n[1])
        except queue.Empty:
            pass
        self.root.after(100, self._nachrichten_abholen)

    def _fertig(self, z, fehler: str | None = None) -> None:
        self.laeuft = False
        self.start_knopf.configure(text="▶  Alle bereinigen")
        if fehler:
            self.status.set("Fehler – siehe Protokoll.")
            self._protokoll(f"FEHLER: {fehler}")
            messagebox.showerror("Noten-Scanner", fehler)
            return
        self.letztes_ziel = z.ziel
        self.oeffnen_knopf.configure(state="normal")
        teile = [f"{z.seiten} Seite(n) gespeichert"]
        if z.pdfs:
            teile.append(f"{len(z.pdfs)} PDF(s)")
        if z.uebersprungen:
            teile.append(f"{z.uebersprungen} übersprungen")
        if z.fehler:
            teile.append(f"{len(z.fehler)} mit Fehler")
        if self.abbruch.is_set():
            teile.insert(0, "Abgebrochen")
        text = ", ".join(teile) + "."
        self.status.set(text)
        self._protokoll(f"Fertig: {text} → {z.ziel}")

    def _ziel_oeffnen(self) -> None:
        ziel = str(getattr(self, "letztes_ziel", "") or self.ziel.get())
        if not ziel or not Path(ziel).exists():
            return
        if sys.platform.startswith("win"):
            os.startfile(ziel)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", ziel])
        else:
            subprocess.Popen(["xdg-open", ziel])

    def _protokoll(self, zeile: str) -> None:
        self.protokoll.configure(state="normal")
        self.protokoll.insert("end", zeile + "\n")
        self.protokoll.see("end")
        self.protokoll.configure(state="disabled")


def main() -> None:
    if sys.platform.startswith("win"):
        try:  # scharfe Darstellung auf hochauflösenden Bildschirmen
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    root = tk.Tk()
    NotenScannerApp(root)
    root.mainloop()
