#!/usr/bin/env python3
"""Odzyskuje rowery zdławione filtrem silnika, zanim poznał rodziny modeli.

Po co to w ogóle istnieje: reguła 1 z CLAUDE.md. Stan mieszka w plikach, nie
w kodzie - a wpis w `seen.json` jest TERMINALNY. Naprawa `has_known_motor`
nie przywraca do gry ANI JEDNEGO roweru, którego stary filtr już zdławił:
przy kolejnym spotkaniu bot widzi wpis bez `score` i bez `cena_odrzut`,
idzie `continue` i milczy dalej.

Co robi: usuwa z `seen.json` wpisy tych ogłoszeń, które
  1. odpadły na braku marki silnika (`powod: obcy_silnik`) albo zamilkły
     bez powodu (wpisy sprzed 01.09.2026 - patrz GRANICA WERSJI w CLAUDE.md),
  2. należą do rodziny, którą nowa reguła zna jako Boschową,
  3. nie mają słowa "bosch" w tytule, czyli stary filtr NIE MIAŁ z czego
     ich przepuścić.
Usunięty wpis znaczy tyle, że bot potraktuje ogłoszenie jak nowe - przeczyta
stronę i policzy od nowa. Jeśli w opisie stoi jednak rywal, nowa reguła
odrzuci je ponownie, tym razem z powodem w pliku.

Wpis NIE jest kasowany, tylko zamieniany na ZALEGŁY ODCZYT: bot pobierze
ogłoszenie wprost po adresie, zamiast czekać, aż samo wróci na półkę.
Kasowanie było za słabe i to jest zmierzone (patrz CLAUDE.md): półka pokazuje
ogłoszenia świeże, a zapytanie kluczowe sortuje po trafności, więc ogłoszenie
sprzed dwóch dni nie wraca nigdy. Kolejkę obsługuje `do_odczytania`,
ODCZYT_NA_SKAN sztuk na skan - budżet ruchu zostaje nietknięty.

`--od` ma nadal sens: rower sprzed dwóch tygodni prawie na pewno jest już
sprzedany, a każdy wpis w kolejce to jedno pobranie strony.

Nie tyka `history.jsonl` ani `market.jsonl` (dzienniki są append-only), nie
wysyła powiadomień, nie zmienia ocen. Domyślnie chodzi NA SUCHO.

    python odzyskaj_silnik.py                   # na sucho, tylko pewne
    python odzyskaj_silnik.py --nieme           # też wpisy nieme sprzed 01.09
    python odzyskaj_silnik.py --od 2026-08-31   # tylko od tej daty
    python odzyskaj_silnik.py --od 2026-08-31 --zrob   # faktycznie zapisz
"""
import json
import sys
from pathlib import Path

import odblokuj
import tracker as t

SEEN = Path("seen.json")
MARKET = Path("market.jsonl")


def ofiara_filtra_silnika(tytul):
    """Czy STARY filtr musiał to zdławić, a NOWY już nie.

    Warunek "nie ma boscha w tytule" jest konieczny: jeśli marka stała
    w tytule, stary filtr przepuszczał rower i cisza miała inny powód -
    odblokowanie go byłoby strzelaniem na oślep."""
    if not tytul or "bosch" in tytul.lower():
        return False
    if not t.silnik_z_rodziny(tytul.lower()):
        return False
    if t._SILNIK_RYWAL.search(tytul.lower()):
        return False
    # Kolejność w pętli jest twarda: silnik sprawdzany jest PO cenie, śmieciu,
    # fully, elektryku i marce. Rower odrzucony wcześniej zginął gdzie indziej
    # i nie jest ofiarą tej wpadki.
    return (not t.is_junk(tytul) and t.is_fully(tytul)
            and t.is_electric(tytul) and t.is_premium_brand(tytul))


def main(zrob=False, nieme=False, od=None):
    seen = json.loads(SEEN.read_text(encoding="utf-8"))
    rynek = {}
    with MARKET.open(encoding="utf-8") as f:
        for linia in f:
            try:
                r = json.loads(linia)
            except Exception:
                continue
            if isinstance(r, dict) and r.get("id"):
                rynek[r["id"]] = r          # ostatnie spotkanie wygrywa

    do_wznowienia, powody = [], []
    for ad_id, wpis in seen.items():
        if not isinstance(wpis, dict):
            continue
        pewny = wpis.get("powod") == "obcy_silnik"
        niemy = set(wpis.keys()) <= {"date"}
        if not (pewny or (niemy and nieme)):
            continue
        r = rynek.get(ad_id)
        if not r or not r.get("p") or not t.cena_w_widelkach(r["p"]):
            continue
        if not ofiara_filtra_silnika(r.get("t")):
            continue
        dzien = wpis.get("date") or r.get("ts")
        if od and dzien < od:
            continue
        do_wznowienia.append((ad_id, r))
        powody.append(("pewne" if pewny else "nieme", dzien, ad_id, r["p"], r["t"][:60]))

    powody.sort(key=lambda x: (x[0], x[1]))
    ile_pewnych = sum(1 for x in powody if x[0] == "pewne")
    print(f"wpisów w seen.json:  {len(seen)}")
    print(f"DO ODZYSKANIA:       {len(do_wznowienia)}"
          f"  (pewnych {ile_pewnych}, niemych {len(do_wznowienia) - ile_pewnych})\n")
    for rodzaj, dzien, ad_id, p, tytul in powody:
        print(f"  [{rodzaj:>5}] {dzien}  {p:>5} €  {ad_id}  {tytul}")

    if not zrob:
        print("\n(na sucho - nic nie zapisano; uruchom z --zrob)")
        return
    # ZALEGLY ODCZYT, nie skasowanie - ten sam mechanizm co `odblokuj.py
    # --wznow` i z tego samego powodu: skasowany wpis to dopiero pozwolenie
    # na wejscie, a wejsc nie ma jak. Rower zdlawiony filtrem silnika jest
    # przewaznie starszy niz okno polki, wiec sam nie wroci. Funkcja jest
    # POZYCZONA, a nie przepisana - dwie kopie tego wpisu rozjechalyby sie
    # przy pierwszej poprawce.
    for ad_id, r in do_wznowienia:
        seen[ad_id] = odblokuj.wpis_do_ponownego_odczytu(ad_id, r)
    # zapis PRZEZ bota, nie wlasny json.dumps: inaczej plik wraca do repo
    # jako jedna linia na 8 MB i kazdy nastepny commit bota jest nieczytelny
    t.save_seen(seen)
    print(f"\nzapisano seen.json - {len(do_wznowienia)} rowerow czeka "
          f"na odczyt wprost po adresie")


if __name__ == "__main__":
    _od = None
    if "--od" in sys.argv:
        _od = sys.argv[sys.argv.index("--od") + 1]
    main(zrob="--zrob" in sys.argv, nieme="--nieme" in sys.argv, od=_od)
