#!/usr/bin/env python3
"""Czyta dziennik dozorcy i składa z niego ŻYCIE każdego roweru.

Dziennik `zdarzenia/*.jsonl` zapisuje surowe fakty ("oferta X była widoczna
21.08 o 14:00 za 12 900 zł"). Tu i tylko tu robi się z nich wnioski — czy
rower zszedł, po ilu dniach i za ile był wystawiony na końcu. Dzięki temu
po zmianie reguł wszystko przelicza się od zera, bez zbierania danych od nowa.

DWA KROKI, celowo rozdzielone:
  1. zloz_oferty()  — zdarzenia → jedna oferta = jeden wpis
  2. scal_rowery()  — oferty → jeden ROWER = jeden wpis (reguła 5)

Uruchom: python3 zycie_ofert.py
"""
import json
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from dozorca import _czas, powod_zniknienia

ZDARZENIA_DIR = Path("zdarzenia")

# Ile wspólnych zdjęć każe uznać dwie oferty za ten sam rower. JEDNO to za
# mało: sklepy wstawiają to samo zdjęcie katalogowe pod różne egzemplarze.
# Dwa te same PLIKI to już własne zdjęcia tego samego roweru.
WSPOLNYCH_ZDJEC = 2

# Poniżej tylu rowerów nie podajemy mediany, tylko "za mało danych".
# Mediana z trzech obserwacji to nie pomiar, to anegdota.
N_MIN = 10


def wczytaj_dziennik(katalog: Path = ZDARZENIA_DIR) -> list:
    """Wszystkie zdarzenia ze wszystkich miesięcy, w kolejności zapisu."""
    zd = []
    for plik in sorted(katalog.glob("olx-*.jsonl")):
        for linia in plik.open(encoding="utf-8"):
            linia = linia.strip()
            if not linia:
                continue
            try:
                zd.append(json.loads(linia))
            except Exception:
                continue          # jedna uszkodzona linia nie psuje reszty
    return zd


def zloz_oferty(zdarzenia: list) -> dict:
    """Zdarzenia → {id_oferty: co o niej wiemy}. Czysta funkcja.

    `cena_pierwsza` to cena z pierwszego widzenia, `cena_ostatnia` — z ostatniego.
    To NIE to samo: 33 z 41 zmian ceny w pierwszych trzech dniach zbierania to
    były obniżki. Kto sprzedaje, ten zbija zanim zejdzie, więc do porównań
    rynkowych idzie cena OSTATNIA."""
    of = {}
    for z in zdarzenia:
        oid = z.get("id")
        if not oid:
            continue
        r = of.setdefault(oid, {"id": oid, "obnizki": 0, "podwyzki": 0})
        ev = z.get("ev")
        if ev == "nowa":
            r.setdefault("pierwszy_raz", z["ts"])
            r.setdefault("cena_pierwsza", z.get("p"))
            r["cena_ostatnia"] = z.get("p")
            for k in ("q", "tytul", "loc", "odcisk", "url"):
                if z.get(k) is not None:
                    r.setdefault(k, z[k])
        elif ev == "cena":
            if z.get("p") is not None and z.get("p_stara") is not None:
                if z["p"] < z["p_stara"]:
                    r["obnizki"] += 1
                elif z["p"] > z["p_stara"]:
                    r["podwyzki"] += 1
            r["cena_ostatnia"] = z.get("p")
        elif ev == "fakty":
            for k in ("wystawiono", "wazne_do", "odswiezono", "sprzedawca",
                      "firma", "miasto", "wojewodztwo", "zdjecia"):
                if z.get(k) is not None:
                    r[k] = z[k]
        elif ev == "znikla":
            r["znikla_ts"] = z["ts"]
            if z.get("p") is not None:
                r["cena_ostatnia"] = z["p"]
            for k in ("wystawiono", "wazne_do", "sprzedawca", "firma", "odcisk"):
                if z.get(k) is not None:
                    r.setdefault(k, z[k])
    return of


def tytul_slug(tytul: str) -> str:
    """Tytuł sprowadzony do samych liter i cyfr — drobna zmiana w interpunkcji
    czy emoji nie ma robić z tego samego roweru dwóch."""
    # Ucięcie na 60 znakach — ZMIERZONE na 489 ofertach z 24.08.2026:
    # przy 60 znakach scaleń 2, przy pełnym tytule 1, przy 40 aż 21
    # (mediana tytułu to 49 znaków, więc 40 tnie w środek nazwy modelu).
    return "".join(c for c in (tytul or "").lower()
                   if c.isalnum() or c in "ąćęłńóśźż")[:60]


def _klucz_scalenia(oferty: list) -> dict:
    """Które oferty to ten sam rower. Zwraca {id_oferty: id_roweru}.

    DWIE DROGI, bo żadna sama nie wystarcza:

    1. Wspólne ZDJĘCIA (>= WSPOLNYCH_ZDJEC tych samych plików). Dowód mocny,
       ale NIESPRAWDZONY: na 448 ofertach z 24.08.2026 nie było ANI JEDNEJ
       pary wspólnych plików, co sugeruje, że OLX nadaje nowy identyfikator
       przy każdym wgraniu. Zostaje, bo nic nie kosztuje i jeśli zadziała,
       jest najmocniejszym dowodem. Nie polegać na nim, dopóki nie złapie.

    2. Ten sam SPRZEDAWCA i ten sam tytuł. To jest droga, która realnie łapie
       wznowienia. Świadomie NIE bierze pod uwagę ceny — wznawiający zwykle
       najpierw zbija, więc odcisk z ceną (dozorca.odcisk) pęka dokładnie na
       tych, których miał łapać.

    Ryzyko drogi 2: sklep z dwoma identycznymi rowerami dostanie jeden wpis.
    Wybrane świadomie — reguła 5 mówi liczyć rowery, nie ogłoszenia, więc
    lepiej policzyć o jeden za mało niż dać jednemu sklepowi dwa głosy."""
    rodzic = {}

    def znajdz(x):
        while rodzic.get(x, x) != x:
            rodzic[x] = rodzic.get(rodzic[x], rodzic[x])
            x = rodzic[x]
        return x

    def polacz(a, b):
        ra, rb = znajdz(a), znajdz(b)
        if ra != rb:
            rodzic[max(ra, rb)] = min(ra, rb)

    po_zdjeciu = defaultdict(list)
    po_sprzedawcy = defaultdict(list)
    for o in oferty:
        rodzic.setdefault(o["id"], o["id"])
        for f in (o.get("zdjecia") or []):
            po_zdjeciu[f].append(o["id"])
        slug = tytul_slug(o.get("tytul"))
        if o.get("sprzedawca") and len(slug) >= 10:
            po_sprzedawcy[(o["sprzedawca"], slug)].append(o["id"])

    wspolne = Counter()
    for ids in po_zdjeciu.values():
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                wspolne[(a, b) if a < b else (b, a)] += 1
    for (a, b), ile in wspolne.items():
        if ile >= WSPOLNYCH_ZDJEC:
            polacz(a, b)

    for ids in po_sprzedawcy.values():
        for b in ids[1:]:
            polacz(ids[0], b)
    return {oid: znajdz(oid) for oid in rodzic}


def scal_rowery(oferty: dict) -> list:
    """Oferty → lista ROWERÓW (reguła 5: liczymy rowery, nie ogłoszenia).

    Rower wystawiony trzy razy przez tego samego sprzedawcę to jeden rower
    z trzema ogłoszeniami, a nie trzy sprzedaże."""
    mapa = _klucz_scalenia(list(oferty.values()))
    grupy = defaultdict(list)
    for oid, o in oferty.items():
        grupy[mapa.get(oid, oid)].append(o)
    rowery = []
    for klucz, grupa in grupy.items():
        grupa.sort(key=lambda o: o.get("pierwszy_raz") or "")
        pierwsza, ostatnia = grupa[0], grupa[-1]
        r = {"rower": klucz, "ogloszen": len(grupa),
             "ids": [o["id"] for o in grupa],
             "pierwszy_raz": pierwsza.get("pierwszy_raz"),
             "cena_pierwsza": pierwsza.get("cena_pierwsza"),
             "cena_ostatnia": ostatnia.get("cena_ostatnia"),
             "obnizki": sum(o.get("obnizki", 0) for o in grupa),
             "znikla_ts": ostatnia.get("znikla_ts"),
             "zyje": "znikla_ts" not in ostatnia}
        for k in ("q", "tytul", "wystawiono", "wazne_do", "sprzedawca",
                  "firma", "miasto", "wojewodztwo"):
            for o in grupa:
                if o.get(k) is not None:
                    r.setdefault(k, o[k])
        r["powod"] = (None if r["zyje"]
                      else powod_zniknienia(r.get("wazne_do"), r["znikla_ts"]))
        r["dni_na_rynku"] = _dni(r.get("wystawiono"), r.get("znikla_ts"))
        rowery.append(r)
    return rowery


def _dni(od, do):
    """Ile dni między dwoma znacznikami. None, gdy któregoś brak — i wtedy
    ma zostać None, a nie 0. Zero znaczyłoby "zeszło tego samego dnia"."""
    a, b = _czas(od), _czas(do)
    return None if (a is None or b is None) else max(0, (b - a).days)


def podsumuj(rowery: list) -> dict:
    """Co z tych żyć wynika. Każda liczba niesie n, na ilu rowerach stoi."""
    zeszly = [r for r in rowery if not r["zyje"]]
    powody = Counter(r["powod"] for r in zeszly)
    zdjete = [r for r in zeszly if r["powod"] == "zdjeta"]
    dni = [r["dni_na_rynku"] for r in zdjete if r["dni_na_rynku"] is not None]
    out = {"rowerow": len(rowery),
           "ogloszen": sum(r["ogloszen"] for r in rowery),
           "zyje": sum(1 for r in rowery if r["zyje"]),
           "zeszlo": len(zeszly),
           "zdjete": powody["zdjeta"], "wygasle": powody["wygasla"],
           "nie_wiadomo": powody[None],
           "n_dni": len(dni)}
    out["dni_mediana"] = statistics.median(dni) if len(dni) >= N_MIN else None
    return out


def _wiek_zywych(rowery, teraz=None):
    teraz = teraz or datetime.now(timezone.utc)
    out = []
    for r in rowery:
        w = _czas(r.get("wystawiono"))
        if r["zyje"] and w:
            out.append((teraz - w).days)
    return out


def raport() -> str:
    zd = wczytaj_dziennik()
    if not zd:
        return "Dziennik pusty — dozorca jeszcze nic nie zebrał."
    oferty = zloz_oferty(zd)
    rowery = scal_rowery(oferty)
    p = podsumuj(rowery)
    L = [f"Dziennik: {len(zd)} zdarzeń → {p['ogloszen']} ogłoszeń → "
         f"{p['rowerow']} rowerów",
         f"  scalone jako wznowienia: {p['ogloszen'] - p['rowerow']} ogłoszeń",
         "",
         f"Na rynku teraz: {p['zyje']}",
         f"Zeszło z rynku:  {p['zeszlo']}"]
    if p["zeszlo"]:
        L += [f"   zdjęte przez sprzedawcę (mogły się sprzedać): {p['zdjete']}",
              f"   wygasłe same (nikt nie kupił):                {p['wygasle']}",
              f"   nie wiadomo:                                  {p['nie_wiadomo']}"]
    L.append("")
    if p["dni_mediana"] is not None:
        L.append(f"Czas do zejścia: mediana {p['dni_mediana']:.0f} dni "
                 f"(ZMIERZONE na {p['n_dni']} rowerach)")
    else:
        L.append(f"Czas do zejścia: NIE WIEM — {p['n_dni']} rowerów z pełną datą, "
                 f"trzeba co najmniej {N_MIN}.")
    wiek = _wiek_zywych(rowery)
    if wiek:
        dlugo = sum(1 for d in wiek if d > 90)
        L += ["",
              f"Wiek ofert stojących na rynku (n={len(wiek)}): "
              f"mediana {statistics.median(wiek):.0f} dni, najstarsza {max(wiek)}",
              f"   wiszących ponad 90 dni: {dlugo} ({dlugo/len(wiek)*100:.0f}%)"]
    firmy = Counter(r.get("firma") for r in rowery if r.get("firma") is not None)
    if firmy:
        ile = firmy[True] + firmy[False]
        L.append(f"   wystawionych przez firmy: {firmy[True]} z {ile} "
                 f"({firmy[True]/ile*100:.0f}%)")
    return "\n".join(L)


if __name__ == "__main__":
    print(raport())
    sys.exit(0)
