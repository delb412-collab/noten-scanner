"""Testserver: liefert die gebaute Web-App aus und stellt eine Brücke zum echten Python-Programm
bereit (ersetzt im Test nur Pyodide). Aufruf: python server.py <port>"""
import base64, json, sys, functools
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
PROJEKT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJEKT))
from notenscanner.web import seite_bereinigen, pdf_erstellen, seite_als_png

class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass
    def do_POST(self):
        laenge = int(self.headers["Content-Length"])
        daten = self.rfile.read(laenge)
        if self.path.startswith("/__test/seite"):
            einstellungen = self.headers["X-Einstellungen"]
            if json.loads(einstellungen).get("dpi") == 300 and self.headers.get("X-Speicherfehler") == "1":
                antwort = json.dumps({"fehler": "MemoryError: Testfall"}).encode()
            else:
                g, v, vb, vh, infos = seite_bereinigen(daten, int(self.headers["X-Breite"]), int(self.headers["X-Hoehe"]), einstellungen)
                antwort = json.dumps({"seite": base64.b64encode(g).decode(), "vorschau": base64.b64encode(v).decode(),
                                      "vb": vb, "vh": vh, "infos": infos}).encode()
            self._senden(antwort, "application/json")
        elif self.path.startswith("/__test/png"):
            self._senden(seite_als_png(daten), "image/png")
        elif self.path.startswith("/__test/pdf"):
            j = json.loads(daten)
            pdf = pdf_erstellen([base64.b64decode(s) for s in j["seiten"]], j["titel"])
            self._senden(pdf, "application/pdf")
    def _senden(self, inhalt, typ):
        self.send_response(200)
        self.send_header("Content-Type", typ)
        self.send_header("Content-Length", str(len(inhalt)))
        self.end_headers()
        self.wfile.write(inhalt)

ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), functools.partial(Handler, directory=str(PROJEKT / "app" / "www"))).serve_forever()
