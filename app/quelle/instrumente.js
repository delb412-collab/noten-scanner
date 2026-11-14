/* Noten-Scanner – Instrumente und Transposition.
 * t = Transposition vom notierten zum klingenden Ton als [Stufen, Halbtöne]
 * (B-Trompete: klingt eine große Sekunde tiefer = [-1, -2]). */
"use strict";

const Instrumente = (() => {
  const LISTE = [
    { id: "c", kurz: "C-Stimme", name: "C – klingend notiert (Flöte, Oboe, Klavier)", t: [0, 0], schluessel: ["G", 2] },
    { id: "c-bass", kurz: "Posaune/Bariton/Tuba (C, Bassschlüssel)", name: "C – Bassschlüssel (Posaune, Bariton, Tuba, Fagott)", t: [0, 0], schluessel: ["F", 4] },
    { id: "b", kurz: "B-Stimme", name: "B – Trompete, Flügelhorn, Klarinette, Sopransax", t: [-1, -2], schluessel: ["G", 2] },
    { id: "b-tief", kurz: "Tenorhorn/Bariton (B, Violinschlüssel)", name: "B tief – Tenorhorn, Bariton, Tenorsax (Violinschlüssel)", t: [-8, -14], schluessel: ["G", 2] },
    { id: "es", kurz: "Es-Stimme", name: "Es – Altsax, Es-Horn, Altklarinette", t: [-5, -9], schluessel: ["G", 2] },
    { id: "es-tief", kurz: "Baritonsax (Es)", name: "Es tief – Baritonsax", t: [-12, -21], schluessel: ["G", 2] },
    { id: "es-hoch", kurz: "Es-Klarinette", name: "Es hoch – Es-Klarinette", t: [2, 3], schluessel: ["G", 2] },
    { id: "f", kurz: "Horn in F", name: "F – Horn in F", t: [-4, -7], schluessel: ["G", 2] },
    { id: "b-bass", kurz: "Tuba in B (Violinschlüssel)", name: "B – Tuba/Bass in B (Violinschlüssel)", t: [-15, -26], schluessel: ["G", 2] },
  ];

  function finden(id) {
    return LISTE.find((i) => i.id === id) || LISTE[0];
  }

  /** Intervall [Stufen, Halbtöne] als Text für Verovio, z. B. [4, 7] -> "P5", [-1, -2] -> "-M2". */
  function intervallText(stufen, halbtoene) {
    if (stufen === 0 && halbtoene === 0) return "";
    const abwaerts = stufen < 0 || (stufen === 0 && halbtoene < 0);
    const st = Math.abs(stufen);
    const ht = abwaerts ? -halbtoene : halbtoene;
    const einfach = st % 7;
    const grund = [0, 2, 4, 5, 7, 9, 11][einfach] + 12 * Math.floor(st / 7);
    const diff = ht - grund;
    const rein = [0, 3, 4].includes(einfach);
    let q;
    if (rein) q = diff === 0 ? "P" : diff > 0 ? "A".repeat(diff) : "d".repeat(-diff);
    else q = diff === 0 ? "M" : diff === -1 ? "m" : diff > 0 ? "A".repeat(diff) : "d".repeat(-diff - 1);
    return (abwaerts ? "-" : "") + q + (st + 1);
  }

  /** Intervall, um das eine Stimme für `von` umgeschrieben werden muss, damit sie für `nach`
   * gleich klingt; oktave = zusätzliche Oktaven (+1 höher, -1 tiefer). */
  function umschreiben(vonId, nachId, oktave = 0) {
    const von = finden(vonId), nach = finden(nachId);
    const st = von.t[0] - nach.t[0] + 7 * oktave;
    const ht = von.t[1] - nach.t[1] + 12 * oktave;
    return { text: intervallText(st, ht), stufen: st, halbtoene: ht, schluessel: nach.schluessel };
  }

  /** Halbtöne, um die die Anzeige (notiert für `nach`) beim Abspielen verschoben werden muss. */
  function klingend(nachId) {
    return finden(nachId).t[1];
  }

  /** Mittellinie des Notensystems als MIDI-Ton (Violinschlüssel h', Bassschlüssel d). */
  function mittellinie(id) {
    return finden(id).schluessel[0] === "F" ? 50 : 71;
  }

  return { LISTE, finden, intervallText, umschreiben, klingend, mittellinie };
})();
