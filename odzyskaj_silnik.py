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

CZEGO TO NIE ZROBI: nie ściągnie roweru z powrotem na półkę. Bot zobaczy go
dopiero wtedy, gdy ogłoszenie samo wróci w wyniki - a półka i zapytanie
kluczowe pokazują tylko najświeższe. Zmierzone 01.09.2026: pierwsza strona
zapytania "cube stereo hybrid" sięgała 8 godzin wstecz. Ogłoszenie sprzed
dwóch dni nie wróci samo i odblokowanie go jest tylko zapasem na wypadek,
gdyby sprzedawca odświeżył ofertę. DLATEGO UŻYWAJ `--od`: odblokowanie
starych wpisów nic nie kosztuje, ale też nic nie daje.

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

    do_zdjecia, powody = [], []
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
        do_zdjecia.append(ad_id)
        powody.append(("pewne" if pewny else "nieme", dzien, ad_id, r["p"], r["t"][:60]))

    powody.sort(key=lambda x: (x[0], x[1]))
    ile_pewnych = sum(1 for x in powody if x[0] == "pewne")
    print(f"wpisów w seen.json:  {len(seen)}")
    print(f"DO ODZYSKANIA:       {len(do_zdjecia)}"
          f"  (pewnych {ile_pewnych}, niemych {len(do_zdjecia) - ile_pewnych})\n")
    for rodzaj, dzien, ad_id, p, tytul in powody:
        print(f"  [{rodzaj:>5}] {dzien}  {p:>5} €  {ad_id}  {tytul}")

    if not zrob:
        print("\n(na sucho - nic nie zapisano; uruchom z --zrob)")
        return
    for ad_id in do_zdjecia:
        seen.pop(ad_id, None)
    # zapis PRZEZ bota, nie wlasny json.dumps: inaczej plik wraca do repo
    # jako jedna linia na 8 MB i kazdy nastepny commit bota jest nieczytelny
    t.save_seen(seen)
    print(f"\nzapisano seen.json - zdjęto {len(do_zdjecia)} wpisów")


if __name__ == "__main__":
    _od = None
    if "--od" in sys.argv:
        _od = sys.argv[sys.argv.index("--od") + 1]
    main(zrob="--zrob" in sys.argv, nieme="--nieme" in sys.argv, od=_od)
