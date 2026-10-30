#!/usr/bin/env python3
"""Doczytuje przebieg tam, gdzie stary czytnik zapisal "brak danych".

Po co to istnieje: regula 1 z CLAUDE.md. Stan mieszka w plikach, nie w kodzie.
Poprawka czytnika przebiegu z 30.10.2026 (slowo "unter", slowo "Fahrleistung"
i weto "Akku"/"Wh" kasujace prawdziwy odczyt) nie zmienia ANI JEDNEGO wpisu,
ktory juz lezy w `seen.json`: 1591 z 4086 ocenionych ogloszen ma tam
`mileage: "brak danych"`, czyli 39%. Ta liczba karmi cennik referencyjny
per model i kwartyl przebiegu w `najlepsze.py`, wiec dopoki jej nie
przeliczymy, poprawka dziala tylko na nowym ruchu.

Co robi: dla wpisow z "brak danych" pobiera strone ogloszenia JESZCZE RAZ
i zapisuje `mileage` oraz `mileage_num` z nowego czytnika.

Czego NIE robi i dlaczego:
  - NIE rusza `score`, `profit`, `buy_price` ani zadnej innej OCENY. Przebieg
    jest FAKTEM ze strony, ocena jest wnioskiem z kilkunastu liczb (kurs,
    mediana OLX, plynnosc) sprzed tygodni. Przeliczenie wniosku ze starych
    danych wygladaloby wiarygodnie i bylo falszywe (regula 7).
  - NIE zamienia wpisu na zalegly odczyt, jak to robi `odzyskaj_silnik.py`.
    Tam wpis byl TERMINALNY i rower nie mial jak wrocic do gry. Tu wpis zyje
    i ma ocene; zrzucenie `score` znaczyloby powtorne powiadomienie o 1591
    rowerach naraz, czyli lawine na telefonie.
  - Nieudany odczyt ("blad") i zdjete ogloszenie ("usuniete") NIE nadpisuja
    niczego. "Nie udalo sie pobrac" to nie to samo co "sprzedawca nie podal"
    i ta roznica jest sednem `fetch_listing_details`.

KURSOR, nie indeks (30.10.2026). Przebieg musi byc WZNAWIALNY, bo
1591 wpisow to 1591 pobran stron, a dlawienie Kleinanzeigen jest PER ADRES:
jeden adres po ~50 zadaniach dostaje strone-smiec i nie wychodzi z niej przez
20 minut (zmierzone 23.08, to samo rozpoznanie, dla ktorego tracker.yml chodzi
lancuszkiem krotkich ogniw). Sweep idzie wiec porcjami po `--ile`, a miejsce
zapisuje `przebieg_kursor.json`.

Kursor to OSTATNI PRZEROBIONY WPIS (data, numer), nie liczba przerobionych.
Indeks by nie zadzialal: wpis, ktory dostal przebieg, przestaje byc kandydatem
i cala lista sie przesuwa, wiec kolejna porcja przeskakiwalaby sasiadow. Para
(data, numer) jest stabilna i porcja bierze kandydatow STRICTLY PO kursorze.

Wpis, na ktorym odczyt sie udal, ale przebiegu w opisie NIE BYLO, tez zostaje
za kursorem - inaczej kazda porcja wracalaby do tych samych nieczytelnych
ogloszen (217 z 288 w pierwszym przebiegu).

Domyslnie chodzi NA SUCHO i bierze 25 wpisow (porcja na jedno ogniwo w CI).

    python3 odzyskaj_przebieg.py                      # na sucho, 25 wpisow
    python3 odzyskaj_przebieg.py --ile 300            # na sucho, 300 wpisow
    python3 odzyskaj_przebieg.py --ile 25 --zrob      # porcja, zapisz
    python3 odzyskaj_przebieg.py --od 2026-10-01 --zrob
    python3 odzyskaj_przebieg.py --od-zera --zrob      # skasuj kursor i zacznij
"""
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import tracker as t
import willhaben as wh

SEEN = Path("seen.json")
KURSOR = Path("przebieg_kursor.json")
ODSTEP_S = 0.8          # ten sam rzad co tempo bota; nie walimy w serwis


def wczytaj_kursor():
    """(data, numer) ostatniego przerobionego wpisu albo None.

    Bledu odczytu NIE POLYKAMY (regula 7): uszkodzony kursor zamieniony po
    cichu na "zacznij od poczatku" znaczy, ze porcja przerobi jeszcze raz
    kilkaset ogloszen, ktore juz sprawdzilismy, i zje budzet ruchu."""
    if not KURSOR.exists():
        return None
    dane = json.loads(KURSOR.read_text(encoding="utf-8"))
    k = dane.get("kursor")
    return tuple(k) if k else None


def zapisz_kursor(kursor, licznik):
    KURSOR.write_text(json.dumps({
        "kursor": list(kursor),
        "przerobionych_razem": licznik,
        "ostatni_bieg": datetime.now(t.TZ_DE).isoformat(timespec="seconds"),
    }, ensure_ascii=False, indent=1), encoding="utf-8")


def czytaj(ad_id, w):
    """(przebieg, stan) dla jednego wpisu, czytnikiem WLASCIWEGO serwisu.

    Przedrostek `wh-` w numerze to willhaben, a tam strona jest aplikacja
    Next.js i czytnik Kleinanzeigen nie widzi na niej ani opisu, ani ceny.
    Zmierzone 30.10.2026 na pierwszym przebiegu tego narzedzia: 7 z 12
    wpisow wrocilo jako "nie udalo sie pobrac", wszystkie siedem to
    willhaben. Bez tego rozgalezienia 230 wpisow z Austrii wygladalo by
    na awarie sieci i narzedzie meldowalo by poraski zamiast odczytow.

    Sam PRZEBIEG liczy ta sama funkcja po obu stronach granicy
    (`_przebieg_z_opisu`) - opis jest po niemiecku tu i tam."""
    if str(ad_id).startswith("wh-"):
        opis, _cena, _zdj, stan, _meta = wh.szczegoly(w["url"])
        if stan != "ok":
            return "brak danych", stan
        return t._przebieg_z_opisu(w.get("title", ""), opis), "ok"
    przebieg, _opis, _cena, _zdj, stan, _meta = t.fetch_listing_details(
        w["url"], w.get("title", ""))
    return przebieg, stan


def main(zrob=False, ile=25, od=None, od_zera=False):
    # To samo zabezpieczenie co w `odzyskaj_silnik.py` i z tego samego
    # powodu: narzedzie przepisuje CALY `seen.json`, wiec przy stanie
    # w kawalkach zlalo by je w jeden plik i zdublowalo wpisy.
    kawalki = [k for k in t.seen_kawalki() if k.name != SEEN.name]
    if kawalki:
        sys.exit(f"STOP: stan jest w kawalkach ({', '.join(k.name for k in kawalki)}), "
                 f"a to narzedzie umie zapisac tylko {SEEN.name}.")
    seen = t.load_seen()

    kandydaci = []
    for ad_id, w in seen.items():
        if not isinstance(w, dict) or "mileage" not in w:
            continue
        if "brak" not in str(w.get("mileage", "")):
            continue
        if not w.get("url"):
            continue
        dzien = w.get("date") or ""
        if od and dzien < od:
            continue
        kandydaci.append((dzien, ad_id))
    kandydaci.sort(reverse=True)        # od najswiezszych: te jeszcze zyja

    if od_zera and KURSOR.exists():
        KURSOR.unlink()
    kursor = None if od_zera else wczytaj_kursor()
    zostale = ([k for k in kandydaci if k < kursor] if kursor else kandydaci)
    biore = zostale[:ile]

    print(f"wpisow z ocena:            {sum(1 for w in seen.values() if isinstance(w, dict) and 'mileage' in w)}")
    print(f"z tego 'brak danych':      {len(kandydaci)}")
    print(f"kursor:                    {kursor[0] + ' ' + kursor[1] if kursor else '(brak, poczatek)'}")
    print(f"za kursorem zostalo:       {len(zostale)}")
    print(f"probuje w tym przebiegu:   {len(biore)}\n")
    if not biore:
        print("Sweep domkniety - nie ma czego dociagac. "
              "Zacznij od nowa przez --od-zera, gdy czytnik znowu sie zmieni.")
        return

    wynik = {"odczytane": 0, "nadal_brak": 0, "usuniete": 0, "blad": 0}
    zmiany = []
    ostatni = None
    for dzien, ad_id in biore:
        w = seen[ad_id]
        przebieg, stan = czytaj(ad_id, w)
        if stan != "ok":
            wynik["usuniete" if stan == "usuniete" else "blad"] += 1
        elif przebieg == "brak danych":
            wynik["nadal_brak"] += 1
        else:
            wynik["odczytane"] += 1
            zmiany.append((ad_id, przebieg, w.get("title", "")[:55]))
            if zrob:
                w["mileage"] = przebieg
                w["mileage_num"] = t.parse_mileage(przebieg)
        # KURSOR IDZIE PO KAZDYM WPISIE, TAKZE PO NIEUDANYM, i trzeba wiedziec,
        # co to znaczy. Probowalem najpierw przesuwac go tylko po udanych -
        # i to NIE DZIALALO: kursor jest jeden, wiec pozniejszy udany odczyt
        # i tak przeskakiwal wczesniejsza awarie, a komentarz obiecywal cos
        # innego, niz kod robil. Jedna pozycja nie umie zapamietac dziur.
        #
        # Co z tego wynika: nieudane pobranie zostaje w tym przejsciu
        # POMINIETE, ale NIC nie jest zapisywane jako fakt - wpis dalej ma
        # `mileage: "brak danych"`, czyli dokladnie to, co mial. Zadna awaria
        # sieci nie zamienia sie wiec w "sprzedawca nie podal". Te wpisy
        # wracaja do gry przy `--od-zera`, a licznik nizej mowi, ile ich bylo.
        ostatni = (dzien, ad_id)
        time.sleep(ODSTEP_S)

    print(f"odczytany przebieg:        {wynik['odczytane']}")
    print(f"naprawde bez przebiegu:    {wynik['nadal_brak']}")
    print(f"ogloszenie zdjete:         {wynik['usuniete']}")
    print(f"nie udalo sie pobrac:      {wynik['blad']}"
          + ("   <- pominiete, wracaja przy --od-zera" if wynik["blad"] else "")
          + "\n")
    for ad_id, przebieg, tytul in zmiany:
        zuzyty = " ZAJEZDZONY" if t.is_too_worn(t.parse_mileage(przebieg)) else ""
        print(f"  {ad_id}  {przebieg:>10}{zuzyty:>12}  {tytul}")

    if not zrob:
        print("\n(na sucho - nic nie zapisano; uruchom z --zrob)")
        return
    if ostatni:
        poprzednio = 0
        if KURSOR.exists():
            poprzednio = json.loads(KURSOR.read_text(encoding="utf-8")).get(
                "przerobionych_razem", 0)
        zapisz_kursor(ostatni, poprzednio + len(biore))
        print(f"\nkursor przesuniety na {ostatni[0]} {ostatni[1]}")
    if zmiany:
        # zapis PRZEZ bota, nie wlasny json.dumps - inaczej plik wraca
        # do repo jako jedna linia na 46 MB
        t.save_seen(seen)
        print(f"\nzapisano seen.json - {len(zmiany)} przebiegow uzupelnionych")


if __name__ == "__main__":
    _ile = int(sys.argv[sys.argv.index("--ile") + 1]) if "--ile" in sys.argv else 25
    _od = sys.argv[sys.argv.index("--od") + 1] if "--od" in sys.argv else None
    main(zrob="--zrob" in sys.argv, ile=_ile, od=_od,
         od_zera="--od-zera" in sys.argv)
