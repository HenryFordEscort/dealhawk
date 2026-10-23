#!/usr/bin/env python3
"""Dojrzałe ogłoszenia - kto zbija cenę i nadal stoi.

CO TO ROBI: czyta dziennik i wypisuje ogłoszenia, których sprzedawca schodził
z ceną wielokrotnie. To druga strona DealHawka. Tamten pyta "co nowego", ten
pyta "kto stoi za długo".

ZASADA: ten plik NICZEGO NIE POBIERA i DO NICZEGO NIE ZAPISUJE. Czyta
history.jsonl, seen.json, market.jsonl, de_stan.json i zdarzenia_de/ wyłącznie
do odczytu. Wnioski liczy od zera przy każdym uruchomieniu, więc zmiana progu
to przeliczenie, nie zbieranie danych od nowa - ten sam podział co dozorca.py /
zycie_ofert.py.

CO BOT JUŻ WIE O ZGONACH, a ten moduł do 23.10.2026 ignorował: dozorca zapisuje
zniknięcie DWA razy - jako zdarzenie `znikla` w `zdarzenia_de/de-*.jsonl`
(dopisywane, nigdy nie kasowane) i jako pole `zdjete` w `de_stan.json` (stan,
nadpisywalny). Osobno stoi `odpuszczone` - oferta, której dozorca przestał
pytać. Zmierzone 23.10.2026 na liście 120 ofert: 43 były martwe, a 7 z pierwszej
dziesiątki. Dziennik czytamy OBOK stanu, nie zamiast: 80 identyfikatorów ze
zdarzeń `znikla` nie ma już w ogóle wpisu w `de_stan.json`, więc stan sam
zapomina to, co dziennik pamięta.

DLACZEGO SKLEJAMY POWTÓRZONE CENY: gałąź "cena" w tracker.py nie aktualizuje
prev["price_num"] po zapisie, więc ta sama cena wpada do dziennika przy każdym
skanie. Zmierzone 29.08.2026: z 433 zdarzeń cenowych 297 (69%) to duplikaty.
Dziennika nie wolno czyścić, więc czyści go warstwa czytająca.

Uruchom: python3 dojrzale.py [min_obnizek]        (domyślnie 2)
"""
import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

HISTORY = Path("history.jsonl")
SEEN = Path("seen.json")
MARKET = Path("market.jsonl")   # LEGACY: najstarszy kawałek dziennika
DE_STAN = Path("de_stan.json")
ZDARZENIA_DE = Path("zdarzenia_de")

# PRÓG ŚWIEŻOŚCI OBNIŻKI, w dniach. Nie jest wzięty z głowy - to zmierzona
# krawędź. Udział ofert, o których bot wie, że są martwe, wg wieku ostatniej
# obniżki (23.10.2026, lista 143 pozycji z `main`):
#
#   0-8 dni temu    24 oferty   0% martwych
#   8-15 dni temu   20 ofert   10%
#   15-30 dni temu  31 ofert   22%
#   30-45 dni temu  35 ofert   71%
#   45+ dni temu    33 oferty  93%
#
# 30 dni to NAJSZERSZE okno, które nie podnosi udziału martwych wewnątrz
# siebie: 12% jak przy 25 dniach, przy 35 już 16%, przy 40 aż 27%. Powyżej
# progu martwych jest 82%. Na starszym snapshocie (120 pozycji) ta sama
# granica wychodzi tak samo: 2% poniżej, 59% powyżej.
#
# Dlatego OKNO, a nie punktowa kara za każdy dzień: wewnątrz okna o kolejności
# decyduje wielkość przeceny, bo tam świeżość nie różnicuje ryzyka (0-8 dni 0%
# wobec 15-30 dni 22%, podczas gdy za progiem jest 71%).
PROG_SWIEZOSCI_DNI = 30


def kawalki_stanu():
    """Wszystkie kawałki `seen.json`, od najstarszego. Legacy plik pierwszy,
    późniejszy kawałek przykrywa wcześniejszy wpis (przecena, dopisany rozmiar).

    Własna kopia `tracker.seen_kawalki` z tego samego powodu co przy dzienniku
    rynku: ten moduł świadomie nie importuje trackera. Gdyby czytał sam
    `seen.json`, dostałby ułamek stanu, a lista dojrzałych ofert nadal
    wyglądałaby wiarygodnie - i nikt by tego nie zauważył (reguła 7)."""
    stare = [SEEN] if SEEN.exists() else []
    katalog = SEEN.parent if str(SEEN.parent) else Path(".")
    return stare + sorted(katalog.glob(f"{SEEN.with_suffix('').name}-????-??.json"))


def kawalki_rynku():
    """Cały dziennik rynku, od najstarszego kawałka. Od 18.09.2026 jest dzielony
    na miesiące, bo git przy każdym commicie zapisywał CAŁY plik od nowa -
    dopisanie wiersza do 27,5 MB tworzyło nowy obiekt na 27,5 MB.

    Ten moduł nie importuje trackera (jest narzędziem tylko do odczytu i ma
    startować w ułamku sekundy), więc ma własny, identyczny czytnik. Gdyby
    patrzył na sam `market.jsonl`, dostałby ułamek danych, a lista dojrzałych
    ofert nadal wyglądałaby wiarygodnie - i nikt by tego nie zauważył."""
    stare = [MARKET] if MARKET.exists() else []
    return stare + sorted(Path(".").glob("market-????-??.jsonl"))


def kawalki_zdarzen_de():
    """Dziennik dozorcy DE, od najstarszego kawałka. Trzecia własna kopia
    czytnika kawałków w tym pliku i z tego samego powodu co dwie poprzednie:
    moduł świadomie nie importuje trackera ani dozorcy. Gdyby czytał jeden
    kawałek, odsiałby ułamek trupów, a lista NADAL WYGLĄDAŁABY WIARYGODNIE -
    dokładnie ta awaria, którą ten rozdział naprawia (reguła 7)."""
    if not ZDARZENIA_DE.is_dir():
        return []
    return sorted(ZDARZENIA_DE.glob("de-????-??.jsonl"))


def martwe_i_stan():
    """({id: powód}, stan_de). Powód to "znikla" / "zdjete" / "odpuszczone".

    Czytamy OBA źródła, bo mówią o różnych rzeczach i żadne nie zawiera
    drugiego:
      - `znikla` w dzienniku to zdarzenie DOPISANE i nigdy nie kasowane;
      - `zdjete` w `de_stan.json` to pole STANU, które da się nadpisać i które
        wraz z wpisem potrafi zniknąć (80 identyfikatorów ze zdarzeń `znikla`
        nie ma już wpisu w stanie, zmierzone 23.10.2026);
      - `odpuszczone` to decyzja dozorcy, że przestaje pytać - bot nie ma
        i nie będzie mieć o tej ofercie nowszej wiedzy niż teraz.

    Zero żądań sieciowych. Cała wiedza leży już na dysku."""
    martwe = {}
    for kawalek in kawalki_zdarzen_de():
        for r in wczytaj_jsonl(kawalek):
            if r.get("ev") == "znikla" and r.get("id"):
                martwe.setdefault(str(r["id"]), "znikla")
    stan = {}
    try:
        stan = json.loads(DE_STAN.read_text(encoding="utf-8"))
    except Exception:
        stan = {}                 # brak dozorcy to brak wiedzy, nie brak ofert
    for ad_id, rec in stan.items():
        if not isinstance(rec, dict):
            continue
        if rec.get("odpuszczone"):
            martwe[str(ad_id)] = "odpuszczone"       # mocniejszy powód: koniec pytań
        elif rec.get("zdjete"):
            martwe.setdefault(str(ad_id), "zdjete")
    return martwe, stan


def wczytaj_jsonl(sciezka):
    out = []
    with sciezka.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue          # jeden zepsuty wiersz nie może wywrócić raportu
    return out


def sciezka_cen(wpisy):
    """Kolejne RÓŻNE ceny ogłoszenia. Powtórzenia tej samej ceny sklejone."""
    out = []
    for r in sorted(wpisy, key=lambda x: x.get("ts", "")):
        p = r.get("p")
        if p is None:
            continue
        if not out or out[-1][1] != p:
            out.append((r.get("ts"), p))
    return out


def ile_obnizek(sciezka):
    return sum(1 for a, b in zip(sciezka, sciezka[1:]) if b[1] < a[1])


def dni_od(iso, dzis):
    try:
        return (dzis - date.fromisoformat(iso)).days
    except (TypeError, ValueError):
        return None


def kolejnosc(b):
    """Klucz sortowania: NAJPIERW okno świeżości obniżki, POTEM wielkość.

    Stare sortowanie (`-spadek_pct`) samo wypychało na czoło trupy: największy
    procent obniżki ma ten, kto schodził z ceny aż sprzedał. Zmierzone
    23.10.2026, pierwsza dziesiątka listy:

        -spadek_pct (stare)        7 trupów / 10, mediana wieku obniżki 52 dni
        okna 30 dni + spadek       0 trupów / 10, mediana 21 dni

    Odwrotnie niż czysta świeżość (`dni_od_obnizki` rosnąco) i niż iloraz
    przecena/wiek: oba wystawiają na czoło ofertę z przeceną 0%, czyli kogoś,
    kto ceny praktycznie nie ruszył, na liście, która jest wyłącznie o tym,
    kto z ceny schodzi. Średnia przecena czoła szóstki na danych z `main`:
    30% przy oknach, 31% przy starym sortowaniu, 14% przy czystej świeżości."""
    wiek = b["dni_od_obnizki"]
    if wiek is None:
        wiek = 9999            # nie znamy daty obniżki - na koniec, nie na czoło
    return (wiek // PROG_SWIEZOSCI_DNI, -b["spadek_pct"])


def zbierz_z_raportem(min_obnizek=2, dzis=None):
    """(lista, raport). `raport` mówi, czego NA LIŚCIE NIE MA i dlaczego -
    bez tego nagłówek musiałby to zmilczeć (reguła 6)."""
    dzis = dzis or date.today()
    hist = wczytaj_jsonl(HISTORY)
    seen = {}
    for _kawalek in kawalki_stanu():                  # PÓŹNIEJSZY WYGRYWA
        seen.update(json.loads(_kawalek.read_text(encoding="utf-8")))

    tytuly, lokacje = {}, {}
    for r in [w for k in kawalki_rynku() for w in wczytaj_jsonl(k)]:
        if r.get("id"):
            tytuly.setdefault(r["id"], r.get("t"))
            loc = (r.get("loc") or "").split()[0:1]      # kod pocztowy; reszta pola bywa śmieciem SVG
            if loc and loc[0].isdigit():
                lokacje.setdefault(r["id"], loc[0])

    martwe, stan_de = martwe_i_stan()

    wg_id = defaultdict(list)
    for r in hist:
        if r.get("id"):
            wg_id[r["id"]].append(r)

    wynik, odsiane = [], defaultdict(int)
    for ad_id, wpisy in wg_id.items():
        sc = sciezka_cen(wpisy)
        if len(sc) < 2 or ile_obnizek(sc) < min_obnizek:
            continue
        if sc[-1][1] >= sc[0][1]:
            continue                                     # netto nie staniał
        powod = martwe.get(str(ad_id))
        if powod:
            odsiane[powod] += 1
            continue            # bot WIE, że ta oferta nie żyje. Nie pokazujemy.
        s = seen.get(ad_id) if isinstance(seen.get(ad_id), dict) else {}
        rec_de = stan_de.get(str(ad_id)) if isinstance(stan_de.get(str(ad_id)), dict) else {}
        wynik.append({
            "id": ad_id,
            "tytul": s.get("title") or tytuly.get(ad_id) or wpisy[0].get("m", "?"),
            "sciezka": sc,
            "obnizek": ile_obnizek(sc),
            "spadek_pct": round((1 - sc[-1][1] / sc[0][1]) * 100),
            "cena": sc[-1][1],
            "pierwsza_cena": sc[0][1],
            "dni_od_pierwszego": dni_od(s.get("date") or sc[0][0], dzis),
            "dni_od_obnizki": dni_od(sc[-1][0], dzis),
            "maks": s.get("buy_price"),
            "przebieg": s.get("mileage"),
            "rocznik": s.get("year"),
            "url": s.get("url"),
            "kod": lokacje.get(ad_id),
            # KIEDY BOT OSTATNI RAZ WIDZIAŁ TĘ OFERTĘ ŻYWĄ. None znaczy
            # "nigdy nie potwierdził" i tak to nazywamy wprost - cała lista
            # stoi na dzienniku, a dziennik nie wie, co jest teraz.
            "ostatni_zywy": rec_de.get("ostatni_zywy"),
            "dni_od_zywego": dni_od((rec_de.get("ostatni_zywy") or "")[:10], dzis),
        })
    wynik.sort(key=kolejnosc)
    raport = {
        "odsiane_martwe": sum(odsiane.values()),
        "powody": dict(odsiane),
        "na_liscie": len(wynik),
    }
    return wynik, raport


def zbierz(min_obnizek=2, dzis=None):
    """Sama lista. Zostaje, bo tak ją wołają starsi czytelnicy."""
    return zbierz_z_raportem(min_obnizek=min_obnizek, dzis=dzis)[0]


def kafelek(b):
    sc = " -> ".join(f"{p}" for _, p in b["sciezka"])
    l = [f"🍐 {b['tytul'][:70]}", ""]
    l.append(f"   {sc} EUR   (-{b['spadek_pct']}%, {b['obnizek']} obnizek)")
    wiek = f"{b['dni_od_pierwszego']} dni" if b["dni_od_pierwszego"] is not None else "?"
    ost = f"{b['dni_od_obnizki']} dni temu" if b["dni_od_obnizki"] is not None else "?"
    l.append(f"   Widziany pierwszy raz {wiek} temu. Ostatnia obnizka {ost}.")
    if b.get("dni_od_zywego") is not None:
        l.append(f"   Bot potwierdzil, ze zyje: {b['dni_od_zywego']} dni temu.")
    else:
        l.append("   Bot NIGDY nie potwierdzil, ze ta oferta zyje.")
    if b["maks"]:
        roz = b["cena"] - b["maks"]
        stan = "MIESCI SIE" if roz <= 0 else f"brakuje {roz} EUR"
        l.append(f"   Twoj maks {b['maks']} EUR  |  jego cena {b['cena']} EUR  ->  {stan}")
    det = [x for x in (b["kod"], b["przebieg"], b["rocznik"]) if x]
    if det:
        l.append("   " + " | ".join(str(x) for x in det))
    if b["url"]:
        l.append(f"   {b['url']}")
    return "\n".join(l)


if __name__ == "__main__":
    prog = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    lista, raport = zbierz_z_raportem(prog)
    print(f"DOJRZALE OGLOSZENIA (min {prog} obnizki): {len(lista)}\n")
    if raport["odsiane_martwe"]:
        powody = ", ".join(f"{k}: {v}" for k, v in sorted(raport["powody"].items()))
        print(f"ODSIANE jako znane martwe: {raport['odsiane_martwe']} ({powody}).")
    print("CZEGO NIE WIEMY: to, co zostalo, to stan Z DZIENNIKA, nie potwierdzenie\n"
          "zycia. Brak zdarzenia o zgonie nie znaczy, ze oferta zyje - znaczy, ze\n"
          "dozorca jej jeszcze nie przepytal. Ten modul nie pobiera stron.\n")
    for b in lista:
        print(kafelek(b))
        print()
