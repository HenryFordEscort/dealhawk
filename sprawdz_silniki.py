#!/usr/bin/env python3
"""Przelicza `silniki_bosch.json` na aktualnych danych i mowi, czy sie broni.

Po co: lista rodzin Boschowych to WIEDZA O SPRZECIE, a ta sie starzeje.
Producent moze wypuscic Cube Stereo Hybrid na innym silniku i wtedy wpis,
ktory dzis jest prawda, jutro przepuszcza obcy naped. Kod tego nie zauwazy
sam z siebie, bo filtr milczy, gdy przepuszcza.

Co robi:
  1. Dla kazdej pary marka+model z pliku liczy od nowa, ile razy ktokolwiek
     napisal przy niej "Bosch", a ile razy marke konkurencji.
  2. Krzyczy, gdy ktorykolwiek wpis przestal sie bronic (pojawil sie rywal
     albo potwierdzen zeszlo ponizej progu). Konczy sie wtedy kodem 1.
  3. Podpowiada NOWE rodziny, ktore juz spelniaja prog, a nie ma ich w pliku.

Zrodla to wlasne dzienniki: `market.jsonl` (Kleinanzeigen + willhaben)
i `rynek_pl.jsonl` (OLX, tytul siedzi w adresie). Nic nie pobiera z sieci
i nic nie zapisuje - decyzje o zmianie pliku podejmuje czlowiek.

    python sprawdz_silniki.py            # sprawdz plik
    python sprawdz_silniki.py --nowe     # dodatkowo wypisz kandydatow
"""
import collections
import json
import re
import sys
from pathlib import Path

import tracker as t

BOSCH = re.compile(r"bosch")          # oba wzorce chodza po tekscie .lower()
RYWAL = t._SILNIK_RYWAL
MARKI = ["cube", "trek", "specialized", "scott", "ktm", "canyon", "haibike",
         "giant", "focus", "bulls", "kalkhoff", "cannondale", "merida", "orbea",
         "bergamont", "ghost", "conway", "corratec", "husqvarna", "mondraker",
         "radon", "raymon", "flyer", "stevens", "winora"]
WYPELNIACZE = {
    "e", "bike", "ebike", "e-bike", "emtb", "e-mtb", "mtb", "fully", "pedelec",
    "elektro", "elektrofahrrad", "mountainbike", "e-mountainbike", "herren",
    "damen", "zoll", "gr", "gr.", "neu", "top", "zustand", "wie", "carbon",
    "rh", "gebraucht", "kaufen", "verkaufe", "mit", "und", "x", "bosch",
}


def tytuly():
    """Wszystkie znane tytuly, malymi literami, bez powtorek."""
    out = set()
    for linia in Path("market.jsonl").open(encoding="utf-8"):
        try:
            r = json.loads(linia)
        except Exception:
            continue
        if r.get("t"):
            out.add(r["t"].lower())
    plik_pl = Path("rynek_pl.jsonl")
    if plik_pl.exists():
        for linia in plik_pl.open(encoding="utf-8"):
            try:
                r = json.loads(linia)
            except Exception:
                continue
            u = r.get("url") or ""
            if u:
                # tytul OLX siedzi w ostatnim czlonie adresu
                out.add(u.rsplit("/", 1)[-1].replace("-", " ").lower())
    return out


def policz(tyt, marka, model):
    wm, wmod = t._wz_frazy(marka), t._wz_frazy(model)
    trafione = [x for x in tyt if wm.search(x) and wmod.search(x)]
    return (len(trafione),
            sum(1 for x in trafione if BOSCH.search(x)),
            sum(1 for x in trafione if RYWAL.search(x)))


def rodzina(tekst, ile_slow):
    slowa = [w.strip(".:!*-\"'") for w in re.split(r"[\s/|,()\[\]]+", tekst)]
    for i, w in enumerate(slowa):
        if w in MARKI:
            reszta = [x for x in slowa[i + 1:] if x and x not in WYPELNIACZE][:ile_slow]
            return (w, " ".join(reszta)) if reszta else None
    return None


def main(pokaz_nowe=False):
    dane = json.loads(Path("silniki_bosch.json").read_text(encoding="utf-8"))
    prog = dane.get("_PROG", {})
    min_bosch = prog.get("min_bosch", 10)
    max_rywal = prog.get("max_rywal", 0)
    tyt = tytuly()
    print(f"tytulow do sprawdzenia: {len(tyt)}")
    print(f"prog: co najmniej {min_bosch} potwierdzen, najwyzej {max_rywal} rywali\n")

    print(f"{'marka + model':<28}{'ogl.':>7}{'BOSCH':>7}{'RYWAL':>7}   stan")
    print("-" * 62)
    zle = []
    for w in dane["bosch_z_definicji"]:
        n, b, r = policz(tyt, w["marka"], w["model"])
        if r > max_rywal:
            stan, zly = "RYWAL! usun wpis", True
        elif b < min_bosch:
            stan, zly = f"za malo potwierdzen ({b})", True
        else:
            stan, zly = "ok", False
        if zly:
            zle.append((w["marka"], w["model"], n, b, r))
        print(f"{w['marka'] + ' ' + w['model']:<28}{n:>7}{b:>7}{r:>7}   {stan}")

    if pokaz_nowe:
        maja = [(t._wz_frazy(w["marka"]), t._wz_frazy(w["model"]))
                for w in dane["bosch_z_definicji"]]
        odrzucone = {(w["marka"], w["model"]) for w in dane["_ODRZUCONE_ZMIERZONYM_POMIAREM"]
                     if isinstance(w, dict)}
        g = collections.defaultdict(lambda: [0, 0, 0])
        for x in tyt:
            if any(m.search(x) and mod.search(x) for m, mod in maja):
                continue                      # juz pokryte przez plik
            for ile in (1, 2):
                k = rodzina(x, ile)
                if not k:
                    continue
                g[k][0] += 1
                if BOSCH.search(x):
                    g[k][1] += 1
                if RYWAL.search(x):
                    g[k][2] += 1
        nowe = [(k, v) for k, v in g.items()
                if v[2] <= max_rywal and v[1] >= min_bosch and k not in odrzucone]
        nowe.sort(key=lambda kv: -kv[1][1])
        print(f"\n=== KANDYDACI, ktorych nie ma w pliku ({len(nowe)}) ===")
        print("Sprawdz recznie, zanim dopiszesz: nazwa modelu bywa zwyklym slowem,")
        print("a rodzina z dwoma wersjami silnika potrafi miec zero rywali w tytulach.")
        for (marka, model), (n, b, r) in nowe[:25]:
            print(f"  {marka + ' ' + model:<28}{n:>7}{b:>7}{r:>7}")

    if zle:
        print(f"\nUWAGA: {len(zle)} wpisow przestalo sie bronic. "
              f"Popraw silniki_bosch.json.")
        return 1
    print(f"\nWszystkie {len(dane['bosch_z_definicji'])} wpisow trzyma sie danych.")
    return 0


if __name__ == "__main__":
    sys.exit(main(pokaz_nowe="--nowe" in sys.argv))
