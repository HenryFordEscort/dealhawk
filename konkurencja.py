#!/usr/bin/env python3
"""Wyznacznik cenowy: ceny konkurenta jako SUFIT dla naszej wyceny.

Po co to jest. Cała wycena w `tracker.py` liczy POZIOM RYNKU - medianę
przeliczonych ofert OLX. To odpowiada na pytanie "ile to jest warte", ale nie
na pytanie, z ktorym kupujacy faktycznie siedzi przed telefonem: "u kogo
kupic ten sam rower taniej". Jesli ten sam Cube Stereo Hybrid stoi u sklepu z
autoryzowanym serwisem Boscha o 300 zl taniej niz u nas, mediana rynku jest
bez znaczenia - kupujacy pojedzie tam.

Dlatego konkurent dostaje osobna warstwe, nad wycena: nie wolno stac drozej
od jego porownywalnego roweru, chyba ze roznice w specyfikacji to UZASADNIAJA.
I tego "uzasadnia" nie oceniamy na oko - jego cene przeliczamy na NASZA
specyfikacje tym samym cennikiem cech (`cennik_cech.json`), ktorym liczy sie
cala wycena. Jesli jego rower jest slabszy (starszy, mniejsza bateria, wiekszy
przebieg), po przeliczeniu jego cena ROSNIE i mamy zmierzony zapas nad nim.
Jesli lepszy - zapas jest ujemny i widac to liczba, a nie przeczuciem.

Czego ten modul NIE robi:
- nie zmyśla ceny domykajacej ich transakcji. Ich ceny to ceny WYWOLAWCZE,
  dokladnie jak cale OLX. Nic innego nie jest publikowane.
- nie liczy, ile im schodzi i w jakim czasie. Do tego trzeba dwoch snapshotow
  w odstepie dni, a pierwszy powstaje dopiero teraz. Dziennik zdarzen
  (`konkurencja_zdarzenia.jsonl`) jest zakladany wlasnie po to, zeby ta liczba
  dala sie policzyc za kilka tygodni z ZAPISU, a nie z pamieci.
- nie dotyka filtrow zakupu ani powiadomien. Wchodzi tylko do `/wycen`.

Zalozenie, ktore trzeba znac: sufitem jest NAJTANSZY z jego porownywalnych
rowerow po przeliczeniu, a nie mediana. Kupujacy nie usrednia oferty sklepu -
widzi konkretny tanszy rower i jedzie po niego.

Modul jest czysty na import (sama biblioteka standardowa). Dostep do OLX
doczytuje sie dopiero w funkcji sieciowej, zeby testy i `/wycen` dzialaly tam,
gdzie `requests` nie ma.
"""
import json
import re
import statistics
from datetime import datetime, timezone
from pathlib import Path

# Kogo mierzymy. Nazwa z OLX-owego ID konta, bo slug "gravitybikespl" NIE
# dziala jako adres profilu (sprawdzone 23.10.2026: /oferty/uzytkownik/
# gravitybikespl/ zwraca strone bledu, a /oferty/uzytkownik/2MnMFI/ dziala).
KONKURENCI = [
    {
        "nazwa": "Gravity Bikes",
        "olx_user": "2MnMFI",        # olx.pl/oferty/uzytkownik/2MnMFI/
        "handle": "gravitybikespl",
        "www": "dhshop.pl",
        "miejsce": "Słopnice",
        # Czym konkuruje POZA cena - do wiadomosci, zeby nie mylic sufitu ceny
        # z sufitem zaufania: autoryzowany serwis Bosch i Ohlins, raport z
        # Bosch Diagnostic Tool do kazdego roweru, faktura VAT marza,
        # gwarancja rozruchowa (ich wlasne opisy ofert, 23.10.2026).
        "atuty": "serwis Bosch i Öhlins, raport Bosch DT, FV marża, gwarancja rozruchowa",
    },
]

STAN_FILE = Path("konkurencja_stan.json")
ZDARZENIA_FILE = Path("konkurencja_zdarzenia.jsonl")

# Tyle cech musi byc znanych, zeby przeliczac cene miedzy rowerami. Ta sama
# granica, przy ktorej `wycen_z_cennikiem` przestaje mowic "niska pewnosc"
# (znane >= 2) - nie nowy prog, tylko ten sam.
MIN_CECH = 2

# Ile ofert sklepu wystarcza, zeby mowic o sufcie. Jedna wystarcza, bo to nie
# statystyka rynku - to konkretny rower, ktory kupujacy moze kupic zamiast
# naszego. Zapisane wprost, zeby nikt nie "poprawil" tego na minimum
# statystyczne (OLX_MIN_SAMPLES=5), ktore tu nie ma sensu.
MIN_OFERT = 1

# Dopuszczalny wiek snapshotu, nim `/wycen` odswiezy go z sieci. ZALOZONE, nie
# zmierzone: nie wiem jeszcze, jak czesto zmieniaja stan, bo nie ma dwoch
# pomiarow. Doba, bo ich ogloszenia sa odswiezane codziennie. Jak dziennik
# zdarzen urosnie, te liczbe trzeba policzyc i ten komentarz zmienic.
MAX_WIEK_SNAPSHOTU_H = 24


def _teraz(teraz=None) -> datetime:
    return teraz or datetime.now(timezone.utc)


def cechy_z_tytulu(tytul: str) -> dict:
    """Rocznik, bateria i przebieg z TYTULU ogloszenia konkurenta.

    Dlaczego z tytulu, a nie ze strony oferty: oni pisza spec w tytule
    ("Cube Stereo Hybrid SLX 2024 rok 750Wh rozmiar [L] 29" 4gen 85 Nm"), wiec
    jedno pobranie profilu daje cechy wszystkich ofert. Strona oferty dolozy
    pozniej `poziom` i dokladny przebieg - tego z tytulu nie ma.

    Zwraca dict z kluczami y/wh/km (None = NIE WIEM) oraz `nowy`.
    Granice 300-1000 Wh jak w `parse_olx_slug`, zeby cennik cech dostawal te
    sama skale (Haibike Flyon 1640 Wh wypada poza nia swiadomie - to inna
    klasa roweru i przeliczanie go cennikiem byloby udawaniem wiedzy)."""
    t = (tytul or "").lower()
    ym = re.search(r'\b(20(?:1[5-9]|2[0-6]))\s*(?:r\b|r\.|rok\b|roku\b)', t)
    if not ym:
        # Rocznik bez kotwicy "r"/"rok" - tylko gdy nie ma watpliwosci, ze to
        # nie jest kod modelu (ONE44, TWO15, 140, 150E nie maja 4 cyfr 20xx).
        ym = re.search(r'(?:^|\s)(20(?:1[5-9]|2[0-6]))(?=\s|$)', t)
    whm = re.search(r'(\d{3,4})\s*wh\b', t)
    kmm = (re.search(r'przebieg[^0-9]{0,12}(\d{1,6})\s*km\b', t)
           or re.search(r'\b(\d{1,6})\s*km\b', t))
    wh = int(whm.group(1)) if whm else None
    if wh is not None and not (300 <= wh <= 1000):
        wh = None
    km = int(kmm.group(1)) if kmm else None
    if km is not None and not (0 <= km <= 30000):
        km = None
    return {
        "y": int(ym.group(1)) if ym else None,
        "wh": wh,
        "km": km,
        # "Nowy" na starcie tytulu to ICH slowo o stanie, nie nasz wniosek.
        # Przebiegu z tego NIE wyliczamy (0 km stoi u nich w opisie, nie w
        # tytule), bo wyliczony przebieg wchodzilby do cennika jako fakt.
        "nowy": bool(re.match(r'\s*now[ya]\b', t)),
    }


def oferta_z_kafelka(card: dict) -> dict:
    """Kafelek OLX -> rekord konkurenta z cechami. Czysta funkcja."""
    rec = {
        "url": card.get("url"),
        "id": card.get("id"),
        "tytul": card.get("title") or "",
        "cena": card.get("price"),
        "loc": card.get("loc"),
        "data_kafelka": card.get("data_kafelka"),
    }
    rec.update(cechy_z_tytulu(rec["tytul"]))
    return rec


def _tokeny(query: str) -> list:
    return [t for t in re.split(r'[\s\-]+', (query or "").lower()) if t and not t.isdigit()]


def dopasuj(oferty, query: str) -> list:
    """Ktore oferty konkurenta dotycza tego modelu.

    Dopasowanie po TYTULE (nie po slugu jak `olx_relevant_offers`), bo profil
    sprzedawcy daje tytul wprost. Kazde slowo zapytania musi wystapic jako cale
    slowo - inaczej "rail" wpada w "trail", na czym ten projekt juz raz przejechal.
    Kolejnosci NIE wymagamy ("levo turbo" to ten sam rower co "turbo levo")."""
    toks = _tokeny(query)
    if not toks:
        return []
    out = []
    for o in oferty or []:
        t = (o.get("tytul") or "").lower()
        if all(re.search(r'(?:^|[^a-z0-9])' + re.escape(tok) + r'(?:[^a-z0-9]|$)', t)
               for tok in toks):
            out.append(o)
    return out


def sufit_z_ofert(oferty, ref: dict, mnoznik) -> dict:
    """Sufit cenowy z ofert konkurenta, przeliczony na NASZA specyfikacje.

    oferty: rekordy z `oferta_z_kafelka` (po `dopasuj`)
    ref:    cechy naszego roweru (y/km/wh/poziom)
    mnoznik: funkcja rec -> (mnoznik, ile_cech_znanych); wstrzykiwana, zeby
             ten modul nie musial znac cennika ani importowac `tracker`.

    Zwraca None, gdy nie da sie rzetelnie przeliczyc - czyli "nie wiem", a nie
    "brak sufitu". Rozróżnienie jest wazne: brak odpowiedzi nie moze byc czytany
    jako zgoda na dowolna cene."""
    if not oferty:
        return None
    m_ref, znane_ref = mnoznik(ref or {})
    if znane_ref < MIN_CECH:
        return None                  # o NASZYM rowerze wiemy za malo
    poz = []
    for o in oferty:
        if not o.get("cena"):
            continue
        m_o, znane_o = mnoznik(o)
        if znane_o < MIN_CECH:
            continue                 # o ICH rowerze wiemy za malo
        poz.append(dict(o, przeliczona=int(o["cena"] * m_ref / m_o),
                        cech_znanych=znane_o))
    if len(poz) < MIN_OFERT:
        return None
    poz.sort(key=lambda r: r["przeliczona"])
    ceny = [r["przeliczona"] for r in poz]
    return {
        "sufit": ceny[0],                       # najtanszy porownywalny u nich
        "mediana": int(statistics.median(ceny)),
        "n": len(poz),
        "pozycje": poz,
        "surowe": (min(r["cena"] for r in poz), max(r["cena"] for r in poz)),
    }


def werdykt(nasza_cena, sufit: dict) -> dict:
    """Czy nasza cena miesci sie pod sufitem konkurenta.

    `zapas` dodatni = tyle zl mozemy dolozyc i nadal bedziemy tansi albo rowni.
    Ujemny = stoimy drozej od ich porownywalnego roweru i to NIE jest uzasadnione
    specyfikacja, bo specyfikacja jest juz wliczona w `przeliczona`."""
    if not sufit or nasza_cena is None:
        return None
    return {"nad_sufitem": nasza_cena > sufit["sufit"],
            "zapas": int(sufit["sufit"] - nasza_cena),
            "sufit": sufit["sufit"]}


# === POBRANIE I SNAPSHOT =====================================================

def pobierz(konkurent: dict, max_stron: int = 6, get=None, parse=None) -> list:
    """Oferty jednego konkurenta z jego profilu OLX. Wymaga sieci.

    Profil sprzedawcy renderuje te same kafelki co wyniki szukania, wiec
    parsujemy je `parse_olx_cards` - jedno miejsce na caly projekt. `get`/`parse`
    sa wstrzykiwalne, zeby test nie musial miec `requests`."""
    if get is None or parse is None:
        from olx import olx_get, parse_olx_cards      # lazy: patrz docstring modulu
        get = get or olx_get
        parse = parse or parse_olx_cards
    baza = f"https://www.olx.pl/oferty/uzytkownik/{konkurent['olx_user']}/"
    po_url = {}
    for strona in range(1, max_stron + 1):
        adres = baza if strona == 1 else f"{baza}?page={strona}"
        r = get(adres)
        if r is None or getattr(r, "status_code", None) != 200:
            break
        karty = parse(r.text)
        if not karty:
            break                    # koniec stron albo blokada - nie udajemy zera
        nowe = 0
        for c in karty:
            rec = oferta_z_kafelka(c)
            if rec["url"] and rec["url"] not in po_url:
                po_url[rec["url"]] = rec
                nowe += 1
        if not nowe:
            break                    # ta sama strona co poprzednio
    return list(po_url.values())


def load_stan(plik=None) -> dict:
    """Ostatni snapshot. Sciezka NIGDY w domyslnym argumencie (ten blad wyszedl
    w tym projekcie cztery razy)."""
    plik = Path(plik) if plik else STAN_FILE
    if not plik.exists():
        return {}
    try:
        return json.loads(plik.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def roznice(stare, nowe) -> list:
    """Co sie zmienilo miedzy snapshotami. Czysta funkcja.

    Zniknieta oferta to NIE jest potwierdzona sprzedaz - sklep moze ja zdjac,
    przepisac albo odswiezyc pod nowym adresem. Zapisujemy wiec fakt
    "zniknela", a nie wniosek "sprzedane"."""
    s = {o["url"]: o for o in (stare or []) if o.get("url")}
    n = {o["url"]: o for o in (nowe or []) if o.get("url")}
    out = []
    for u, o in n.items():
        if u not in s:
            out.append({"co": "nowa", "url": u, "tytul": o.get("tytul"),
                        "cena": o.get("cena")})
        elif s[u].get("cena") != o.get("cena"):
            out.append({"co": "cena", "url": u, "tytul": o.get("tytul"),
                        "z": s[u].get("cena"), "na": o.get("cena")})
    for u, o in s.items():
        if u not in n:
            out.append({"co": "zniknela", "url": u, "tytul": o.get("tytul"),
                        "cena": o.get("cena"), "widziana_od": o.get("pierwszy_raz")})
    return out


def snapshot(teraz=None, plik=None, zdarzenia=None, pobierak=None) -> dict:
    """Pobiera stan wszystkich konkurentow, dopisuje zdarzenia, zapisuje stan.

    Dziennik zdarzen jest DOPISYWANY i nigdy nie kasowany - tak jak
    `history.jsonl`. To jedyne zrodlo, z ktorego da sie pozniej policzyc, co im
    schodzi i po ilu dniach."""
    plik = Path(plik) if plik else STAN_FILE
    zdarzenia = Path(zdarzenia) if zdarzenia else ZDARZENIA_FILE
    pobierak = pobierak or pobierz
    t = _teraz(teraz)
    stan = load_stan(plik)
    wynik = {"czas": t.isoformat(), "konkurenci": {}}
    for k in KONKURENCI:
        klucz = k["nazwa"]
        stare = ((stan.get("konkurenci") or {}).get(klucz) or {}).get("oferty") or []
        nowe = pobierak(k)
        if not nowe:
            # Puste pobranie to najczesciej blokada OLX, nie puste polki.
            # Zostawiamy poprzedni stan - zerowanie skasowaloby wyznacznik.
            wynik["konkurenci"][klucz] = (stan.get("konkurenci") or {}).get(klucz) or {
                "oferty": [], "czas": None}
            wynik["konkurenci"][klucz]["ostatnie_pobranie_puste"] = t.isoformat()
            continue
        pierwszy = {o["url"]: o.get("pierwszy_raz") for o in stare if o.get("url")}
        for o in nowe:
            o["pierwszy_raz"] = pierwszy.get(o["url"]) or t.isoformat()
        zm = roznice(stare, nowe)
        if zm:
            with zdarzenia.open("a", encoding="utf-8") as f:
                for z in zm:
                    f.write(json.dumps({"czas": t.isoformat(), "kto": klucz, **z},
                                       ensure_ascii=False) + "\n")
        wynik["konkurenci"][klucz] = {
            "czas": t.isoformat(),
            "oferty": nowe,
            "n": len(nowe),
            "wartosc": sum(o["cena"] for o in nowe if o.get("cena")),
            "zmian": len(zm),
        }
    plik.write_text(json.dumps(wynik, ensure_ascii=False, indent=1), encoding="utf-8")
    return wynik


def oferty_ze_stanu(stan=None, plik=None) -> list:
    """Wszystkie oferty wszystkich konkurentow ze snapshotu, z nazwa sklepu."""
    stan = stan if stan is not None else load_stan(plik)
    out = []
    for nazwa, blok in (stan.get("konkurenci") or {}).items():
        for o in blok.get("oferty") or []:
            out.append(dict(o, kto=nazwa))
    return out


def wiek_snapshotu_h(stan=None, plik=None, teraz=None):
    """Ile godzin ma snapshot. None = nie ma go wcale."""
    stan = stan if stan is not None else load_stan(plik)
    czas = stan.get("czas")
    if not czas:
        return None
    try:
        t0 = datetime.fromisoformat(czas)
    except Exception:
        return None
    if t0.tzinfo is None:
        t0 = t0.replace(tzinfo=timezone.utc)
    return (_teraz(teraz) - t0).total_seconds() / 3600.0


def podsumuj(stan=None, plik=None) -> dict:
    """Liczby o stanie konkurenta: ile rowerow, za ile, mediana, widelki."""
    stan = stan if stan is not None else load_stan(plik)
    out = {}
    for nazwa, blok in (stan.get("konkurenci") or {}).items():
        ceny = sorted(o["cena"] for o in (blok.get("oferty") or []) if o.get("cena"))
        if not ceny:
            out[nazwa] = {"n": 0}
            continue
        out[nazwa] = {"n": len(ceny), "wartosc": sum(ceny),
                      "mediana": int(statistics.median(ceny)),
                      "min": ceny[0], "max": ceny[-1], "czas": blok.get("czas")}
    return out


if __name__ == "__main__":
    import sys
    if "--pokaz" in sys.argv:
        print(json.dumps(podsumuj(), ensure_ascii=False, indent=1))
    else:
        w = snapshot()
        for nazwa, blok in (w.get("konkurenci") or {}).items():
            print(f"{nazwa}: {blok.get('n')} ofert, "
                  f"{blok.get('wartosc')} zl, zmian: {blok.get('zmian')}")
