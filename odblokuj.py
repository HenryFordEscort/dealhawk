#!/usr/bin/env python3
"""Odblokowuje rowery zdławione STARĄ regułą re-listingu (do 01.09.2026).

Po co to w ogóle istnieje: reguła 1 z CLAUDE.md. Stan mieszka w plikach, nie
w kodzie — a wpis w `seen.json` jest TERMINALNY. Naprawa `find_relisting` nie
przywraca do gry ani jednego roweru, którego stara reguła już zdławiła: przy
kolejnym skanie bot widzi wpis, nie ma w nim ani `score`, ani `cena_odrzut`,
więc idzie `continue` i milczy dalej. Na zawsze, niezależnie od ceny.

Co robi: usuwa z `seen.json` wpisy tych ogłoszeń, które
  1. stara reguła uznała za powtórkę,
  2. nowa reguła za powtórkę NIE uznaje (jawna sprzeczność w tytułach albo
     brak dowodu tożsamości po odebraniu przebiegu roli dowodu).
Usunięty wpis znaczy tyle, że bot potraktuje ogłoszenie jak nowe, gdy je
jeszcze raz zobaczy na półce — czyli przeczyta stronę i policzy je od nowa.
Ogłoszenia martwe odpadną same, na `stan_odczytu == "usuniete"`.

Czego NIE robi: nie tyka `history.jsonl` ani `market.jsonl` (dzienniki są
append-only), nie wysyła powiadomień, nie zmienia ocen. Domyślnie chodzi
NA SUCHO i tylko wypisuje, co by zrobił.

    python odblokuj.py                          # na sucho, tylko pewne
    python odblokuj.py --niepewne               # też te, których nie da się orzec
    python odblokuj.py --od 2026-08-20          # tylko od tej daty
    python odblokuj.py --niepewne --zrob        # faktycznie zapisz seen.json

Ogłoszenia sprzed kilku tygodni i tak są martwe — odblokowanie ich nic nie
kosztuje i nic nie daje. `--od` jest po to, żeby zacząć od tych, które jeszcze
mogą wrócić na półkę.
"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import tracker as t

SEEN = Path("seen.json")
MARKET = Path("market.jsonl")


def stary_find_relisting(index, title, price_num, mileage_num, loc=None):
    """Reguła sprzed 01.09.2026 — tu wyłącznie po to, żeby rozpoznać JEJ
    ofiary. Do niczego innego nie wolno jej używać."""
    model = t.dedup_key(title)
    if not model or not price_num:
        return None
    tytul = t._tytul_znormalizowany(title)
    for wpis in index:
        m, p, km, d = wpis[0], wpis[1], wpis[2], wpis[3]
        stara_loc = wpis[4] if len(wpis) > 4 else None
        stary_tytul = wpis[5] if len(wpis) > 5 else None
        if m != model or abs(p - price_num) > price_num * t.DEDUP_PRICE_PCT:
            continue
        km_zgodny = (km is not None and mileage_num is not None
                     and abs(km - mileage_num) <= t.DEDUP_KM_TOL)
        loc_zgodna = bool(loc and stara_loc and loc.strip() == stara_loc.strip())
        tytul_zgodny = bool(stary_tytul and tytul == stary_tytul
                            and len(tytul) >= t.DEDUP_TYTUL_MIN)
        if km_zgodny or loc_zgodna or tytul_zgodny:
            return d
    return None


def przeszlo_filtry_z_tytulu(tytul):
    """Czy ogłoszenie dożyłoby do kroku re-listingu. Kolejność w pętli jest
    twarda: re-listing jest OSTATNI, więc rower odrzucony wcześniej (śmieć,
    nie fully, analogowy, nisza) zginął gdzie indziej i nie jest ofiarą
    tej wpadki. Bez tego warunku odblokowywalibyśmy śmieci."""
    return (not t.is_junk(tytul) and t.is_fully(tytul)
            and t.is_electric(tytul) and t.is_premium_brand(tytul))


def main(zrob=False, podejrzane=False, od=None):
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

    # Indeks tak jak go widział bot: WYŁĄCZNIE oferty ocenione.
    ocenione = []
    for v in seen.values():
        if not isinstance(v, dict) or v.get("score") is None:
            continue
        if not v.get("title") or not v.get("price_num"):
            continue
        ocenione.append((v["date"], t.dedup_key(v["title"]), v["price_num"],
                         v.get("mileage_num"), v.get("loc"),
                         t._tytul_znormalizowany(v["title"]), v["title"]))

    do_zdjecia, powody = [], []
    for ad_id, wpis in seen.items():
        if not isinstance(wpis, dict) or set(wpis.keys()) - {"date"}:
            continue                        # nie goły odrzut sprzed zmiany
        r = rynek.get(ad_id)
        if not r or not r.get("t") or not r.get("p"):
            continue
        if not t.cena_w_widelkach(r["p"]) or not przeszlo_filtry_z_tytulu(r["t"]):
            continue
        dzien = wpis.get("date") or r.get("ts")
        if od and dzien < od:
            continue
        cut = (date.fromisoformat(dzien) - timedelta(days=t.DEDUP_DAYS)).isoformat()
        # krotka w kształcie indeksu: (klucz, cena, przebieg, data,
        # miejscowość, tytuł znormalizowany, tytuł surowy)
        idx = [(o[1], o[2], o[3], o[0], o[4], o[5], o[6])
               for o in ocenione if cut <= o[0] < dzien]
        if t.find_relisting(idx, r["t"], r["p"], r.get("km"), r.get("loc")):
            continue                        # nowa reguła też mówi: powtórka
        pewne = stary_find_relisting(idx, r["t"], r["p"], r.get("km"), r.get("loc"))
        if pewne:
            do_zdjecia.append(ad_id)
            powody.append(("pewne", dzien, ad_id, r["p"], r["t"][:60]))
            continue
        # PRZEBIEGU W DZIENNIKU RYNKU NIE MA, gdy sprzedawca nie wpisał go
        # w TYTUŁ — a stara reguła sądziła po przebiegu ze STRONY ogłoszenia,
        # którego nikt nie zapisał. Tą dziurą wypadł rower z 26.08.2026
        # (3492497177): w market.jsonl stoi bez `km`, więc powtórki nie da się
        # tu odtworzyć, choć to właśnie ona go zabiła.
        # Zostaje warunek KONIECZNY starej reguły: ten sam model i cena
        # w tolerancji. To za mało, żeby orzec winę, i w zupełności dość,
        # żeby dać rowerowi drugie podejście — kosztuje jedno pobranie strony.
        if r.get("km") is None and podejrzane:
            for m, p_, _km, _d, _loc, _tn, _ts in idx:
                # `_km is not None` zawęża realnie: dowód z przebiegu wymagał
                # przebiegu po OBU stronach, więc wpis w indeksie bez przebiegu
                # nie mógł nim zdławić niczego. Bez tego warunku podejrzanych
                # było 1 334, z nim schodzi to o rząd wielkości.
                if (_km is not None and m == t.dedup_key(r["t"])
                        and abs(p_ - r["p"]) <= r["p"] * t.DEDUP_PRICE_PCT):
                    do_zdjecia.append(ad_id)
                    powody.append(("niepewne", dzien, ad_id, r["p"], r["t"][:60]))
                    break

    powody.sort(key=lambda x: (x[0], x[1]))
    ile_pewnych = sum(1 for x in powody if x[0] == "pewne")
    print(f"wpisów w seen.json:          {len(seen)}")
    print(f"ofert ocenionych w indeksie: {len(ocenione)}")
    print(f"DO ODBLOKOWANIA:             {len(do_zdjecia)}"
          f"  (pewnych {ile_pewnych}, niepewnych {len(do_zdjecia) - ile_pewnych})\n")
    for rodzaj, dzien, ad_id, p, tytul in powody:
        print(f"  [{rodzaj:>8}] {dzien}  {p:>5} €  {ad_id}  {tytul}")

    if not zrob:
        print("\n(na sucho — nic nie zapisano; uruchom z --zrob)")
        return
    for ad_id in do_zdjecia:
        seen.pop(ad_id, None)
    SEEN.write_text(json.dumps(seen, ensure_ascii=False), encoding="utf-8")
    print(f"\nzapisano seen.json — zdjęto {len(do_zdjecia)} wpisów")


if __name__ == "__main__":
    _od = None
    if "--od" in sys.argv:
        _od = sys.argv[sys.argv.index("--od") + 1]
    main(zrob="--zrob" in sys.argv, podejrzane="--niepewne" in sys.argv, od=_od)
