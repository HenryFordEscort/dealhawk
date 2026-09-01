#!/usr/bin/env python3
"""Dojrzałe ogłoszenia - kto zbija cenę i nadal stoi.

CO TO ROBI: czyta dziennik i wypisuje ogłoszenia, których sprzedawca schodził
z ceną wielokrotnie. To druga strona DealHawka. Tamten pyta "co nowego", ten
pyta "kto stoi za długo".

ZASADA: ten plik NICZEGO NIE POBIERA i DO NICZEGO NIE ZAPISUJE. Czyta
history.jsonl, seen.json i market.jsonl wyłącznie do odczytu. Wnioski liczy od
zera przy każdym uruchomieniu, więc zmiana progu to przeliczenie, nie zbieranie
danych od nowa - ten sam podział co dozorca.py / zycie_ofert.py.

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
MARKET = Path("market.jsonl")


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


def zbierz(min_obnizek=2, dzis=None):
    dzis = dzis or date.today()
    hist = wczytaj_jsonl(HISTORY)
    seen = json.loads(SEEN.read_text(encoding="utf-8")) if SEEN.exists() else {}

    tytuly, lokacje = {}, {}
    for r in wczytaj_jsonl(MARKET):
        if r.get("id"):
            tytuly.setdefault(r["id"], r.get("t"))
            loc = (r.get("loc") or "").split()[0:1]      # kod pocztowy; reszta pola bywa śmieciem SVG
            if loc and loc[0].isdigit():
                lokacje.setdefault(r["id"], loc[0])

    wg_id = defaultdict(list)
    for r in hist:
        if r.get("id"):
            wg_id[r["id"]].append(r)

    wynik = []
    for ad_id, wpisy in wg_id.items():
        sc = sciezka_cen(wpisy)
        if len(sc) < 2 or ile_obnizek(sc) < min_obnizek:
            continue
        if sc[-1][1] >= sc[0][1]:
            continue                                     # netto nie staniał
        s = seen.get(ad_id) if isinstance(seen.get(ad_id), dict) else {}
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
        })
    wynik.sort(key=lambda x: -x["spadek_pct"])
    return wynik


def kafelek(b):
    sc = " -> ".join(f"{p}" for _, p in b["sciezka"])
    l = [f"🍐 {b['tytul'][:70]}", ""]
    l.append(f"   {sc} EUR   (-{b['spadek_pct']}%, {b['obnizek']} obnizek)")
    wiek = f"{b['dni_od_pierwszego']} dni" if b["dni_od_pierwszego"] is not None else "?"
    ost = f"{b['dni_od_obnizki']} dni temu" if b["dni_od_obnizki"] is not None else "?"
    l.append(f"   Widziany pierwszy raz {wiek} temu. Ostatnia obnizka {ost}.")
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
    lista = zbierz(prog)
    print(f"DOJRZALE OGLOSZENIA (min {prog} obnizki): {len(lista)}\n")
    print("UWAGA: to jest stan z dziennika. Czy ogloszenie NADAL zyje, wie tylko\n"
          "dozorca_de.py, ktory pyta strone. Tu tego nie sprawdzamy.\n")
    for b in lista:
        print(kafelek(b))
        print()
