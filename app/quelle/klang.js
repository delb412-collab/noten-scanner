/* Noten-Scanner – Abspielen im Browser (Web Audio, ohne Klangdateien, funktioniert offline).
 * Ein einfacher Blechbläser-Klang: Sägezahn durch einen Tiefpass, der beim Anblasen aufgeht. */
"use strict";

class Spieler {
  constructor() {
    this.ctx = null;
    this.knoten = [];
    this.laeuft = false;
    this.rahmen = 0;
  }

  async vorbereiten() {
    if (!this.ctx) {
      const AC = window.AudioContext || window.webkitAudioContext;
      this.ctx = new AC();
      this.haupt = this.ctx.createGain();
      this.haupt.gain.value = 0.5;
      const komp = this.ctx.createDynamicsCompressor();
      this.haupt.connect(komp).connect(this.ctx.destination);
    }
    // iPhone: auch bei Stummschalter abspielen (wo vom Browser unterstützt)
    try { if (navigator.audioSession) navigator.audioSession.type = "playback"; } catch (_) { /* egal */ }
    if (this.ctx.state === "suspended") await this.ctx.resume();
  }

  ton(frequenz, start, dauer, lautstaerke = 0.22) {
    const c = this.ctx;
    const osc = c.createOscillator();
    osc.type = "sawtooth";
    osc.frequency.value = frequenz;
    const filter = c.createBiquadFilter();
    filter.type = "lowpass";
    filter.Q.value = 0.8;
    const huelle = c.createGain();
    const ende = start + Math.max(0.06, dauer * 0.95);
    filter.frequency.setValueAtTime(frequenz * 1.2, start);
    filter.frequency.linearRampToValueAtTime(Math.min(9000, frequenz * 7), start + 0.06);
    filter.frequency.setTargetAtTime(Math.min(6000, frequenz * 3.5), start + 0.06, 0.15);
    huelle.gain.setValueAtTime(0.0001, start);
    huelle.gain.linearRampToValueAtTime(lautstaerke, start + 0.025);
    huelle.gain.setTargetAtTime(lautstaerke * 0.75, start + 0.03, 0.12);
    huelle.gain.setTargetAtTime(0.0001, ende, 0.03);
    osc.connect(filter).connect(huelle).connect(this.haupt);
    osc.start(start);
    osc.stop(ende + 0.25);
    this.knoten.push(osc);
  }

  /** noten: [{zeit (s), dauer (s), midi, id, kurz?, lautstaerke?}], beiNote(id, an) für die Markierung.
   *  kurz: Anteil der Dauer, der klingt (Staccato); lautstaerke: z. B. für Akzente. */
  async spielen(noten, { beiNote, fertig, ab = 0 } = {}) {
    await this.vorbereiten();
    this.stoppen();
    this.laeuft = true;
    const start = this.ctx.currentTime + 0.15;
    const liste = noten.filter((n) => n.zeit >= ab).map((n) => ({ ...n, zeit: n.zeit - ab }));
    for (const n of liste) {
      this.ton(440 * Math.pow(2, (n.midi - 69) / 12), start + n.zeit, n.dauer * (n.kurz || 1), n.lautstaerke);
    }
    const ereignisse = [];
    for (const n of liste) {
      ereignisse.push({ t: n.zeit, id: n.id, an: true });
      ereignisse.push({ t: n.zeit + n.dauer * 0.95, id: n.id, an: false });
    }
    ereignisse.sort((a, b) => a.t - b.t || (a.an ? 1 : -1));
    const gesamt = liste.length ? Math.max(...liste.map((n) => n.zeit + n.dauer)) : 0;
    let i = 0;
    const schritt = () => {
      if (!this.laeuft) return;
      const jetzt = this.ctx.currentTime - start;
      while (i < ereignisse.length && ereignisse[i].t <= jetzt) {
        if (beiNote) beiNote(ereignisse[i].id, ereignisse[i].an);
        i++;
      }
      if (jetzt > gesamt + 0.2) {
        this.laeuft = false;
        if (fertig) fertig();
        return;
      }
      this.rahmen = requestAnimationFrame(schritt);
    };
    this.rahmen = requestAnimationFrame(schritt);
  }

  stoppen() {
    this.laeuft = false;
    cancelAnimationFrame(this.rahmen);
    for (const o of this.knoten) {
      try { o.stop(); } catch (_) { /* schon beendet */ }
    }
    this.knoten = [];
  }
}
