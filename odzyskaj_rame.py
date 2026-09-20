#!/usr/bin/env python3
"""Odzyskuje obserwowane rowery zdławione regułą XL, zanim wyszła z `is_junk`.

Po co to istnieje: reguła 1 z CLAUDE.md. Rozdzielenie „za duża rama" od
„śmieć" (20.09.2026) nie wskrzesza samo z siebie ANI JEDNEGO wpisu, bo wpis
w `seen.json` jest terminalny, a `smiec` nie stoi w `POWODY_PO_CENIE`.
Bot przy następnym spotkaniu widzi wpis, nie ma w nim `score`, idzie
`continue` i milczy dalej. Na zawsze.

Wpadka, od której to powstało: właściciel przysłał link do CUBE Stereo
Hybrid 160 HPC TM 750 XL, 894 km, 2 990 € (3517638486) z pytaniem „dlaczego
to nie przyszło". To model z jego listy życzeń, a wypadł jako „śmieć" na
wzorcu `\bxl\b`, który opisuje ROZMIAR, nie części.

Bierzemy WYŁĄCZNIE rowery z listy życzeń. Zwykły rower w XL nadal ma
odpadać - poprawka rozdzieliła etykiety, nie poszerzyła rynku.

    python odzyskaj_rame.py --od 2026-09-15          # na sucho
    python odzyskaj_rame.py --od 2026-09-15 --zrob   # faktycznie zapisz

**PODAWAJ `--od`.** Odblokowanie ogłoszenia sprzed kilku tygodni nic nie
kosztuje i nic nie daje - ono już nie żyje. Zmierzone 20.09.2026: z ośmiu
obserwowanych rowerów w XL tylko trzy mają w ogóle zapisany powód, a dwa
są z ostatniej doby.
"""
import json
import sys
from pathlib import Path

import odblokuj
import tracker as t

SEEN = Path("seen.json")


def ofiara_reguly_xl(tytul):
    """Czy ten rower wypadł WYŁĄCZNIE przez rozmiar ramy, a jest obserwowany.

    Kolejność w pętli jest twarda: śmieć sprawdzany jest PO cenie, więc rower
    odrzucony wcześniej zginął gdzie indziej i nie jest ofiarą tej wpadki.
    Sprawdzamy więc, że DZIŚ nie odpadłby już na niczym innym."""
    if not tytul:
        return False
    if not t.obserwowany(tytul):
        return False                    # zwykły rower w XL ma dalej odpadać
    if not t.za_duza_rama(tytul):
        return False
    return (not t.is_junk(tytul) and t.is_fully(tytul)
            and t.is_electric(tytul))


def main(zrob=False, od=None):
    # NARZĘDZIE PRZEPISUJE CAŁY `seen.json`, więc przy stanie podzielonym na
    # kawałki zlałoby je w jeden plik i zdublowało wpisy. Ma wtedy STANĄĆ,
    # a nie po cichu zepsuć stan dedupu - ta sama decyzja co w `odblokuj.py`
    # i `odzyskaj_silnik.py`.
    _kawalki = [k for k in t.seen_kawalki() if k.name != SEEN.name]
    if _kawalki:
        sys.exit(f"STOP: stan jest w kawałkach ({', '.join(k.name for k in _kawalki)}), "
                 f"a to narzędzie umie zapisać tylko {SEEN.name}. Przerób je najpierw.")
    seen = t.load_seen()

    rynek = {}
    # Przez `market_wiersze`, bo `market.jsonl` to dziś tylko najstarszy
    # kawałek dziennika - sam plik dałby ułamek danych, a wynik nadal
    # wyglądałby wiarygodnie (reguła 7).
    for linia in t.market_wiersze():
        try:
            r = json.loads(linia)
        except Exception:
            continue
        if isinstance(r, dict) and r.get("id"):
            rynek[r["id"]] = r          # ostatnie spotkanie wygrywa

    do_wznowienia, opis = [], []
    for ad_id, wpis in seen.items():
        if not isinstance(wpis, dict) or wpis.get("powod") != "smiec":
            continue
        r = rynek.get(ad_id)
        if not r or not ofiara_reguly_xl(r.get("t")):
            continue
        dzien = wpis.get("date") or r.get("ts")
        if od and dzien < od:
            continue
        do_wznowienia.append((ad_id, r))
        opis.append((dzien, ad_id, r.get("p") or 0, (r.get("t") or "")[:58]))

    opis.sort()
    print(f"wpisów w seen.json:  {len(seen)}")
    print(f"DO ODZYSKANIA:       {len(do_wznowienia)}\n")
    for dzien, ad_id, p, tytul in opis:
        print(f"  {dzien}  {p:>5} €  {ad_id}  {tytul}")

    if not zrob:
        print("\n(na sucho - nic nie zapisano; uruchom z --zrob)")
        return do_wznowienia
    # ZALEGŁY ODCZYT, nie skasowanie - skasowany wpis to dopiero pozwolenie
    # na wejście, a wejść nie ma jak: półka pokazuje świeże, a zapytanie
    # kluczowe wraca do tej samej frazy dopiero po ~110 minutach. Funkcja
    # POŻYCZONA z `odblokuj`, nie przepisana - dwie kopie rozjechałyby się
    # przy pierwszej poprawce.
    for ad_id, r in do_wznowienia:
        seen[ad_id] = odblokuj.wpis_do_ponownego_odczytu(ad_id, r)
    # zapis PRZEZ bota, nie własny json.dumps: inaczej plik wraca do repo
    # jako jedna linia i każdy następny commit bota jest nieczytelny
    t.save_seen(seen)
    print(f"\nzapisano seen.json - {len(do_wznowienia)} rowerów czeka "
          f"na odczyt wprost po adresie")
    return do_wznowienia


if __name__ == "__main__":
    _od = None
    if "--od" in sys.argv:
        _od = sys.argv[sys.argv.index("--od") + 1]
    main(zrob="--zrob" in sys.argv, od=_od)
