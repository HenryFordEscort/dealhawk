#!/usr/bin/env python3
"""Jednorazowa diagnostyka: czy sortowanie po DACIE działa i ile kryje strona 1.

NICZEGO NIE ZMIENIA. Pobiera, mierzy, wypisuje. Stan obu botów jest przed
uruchomieniem przekierowany do katalogu tymczasowego, więc nawet gdyby któraś
funkcja chciała coś zapisać, nie dotknie plików produkcyjnych.

PO CO TO JEST
=============
Zapytania kluczowe Kleinanzeigen sortują się po TRAFNOŚCI, nie po świeżości.
Zmierzone 19.09.2026 na 8 dniach: ogłoszenie z półki bot łapie po medianie
2 minut, a złapane wyłącznie zapytaniem po 93 minutach (p90 437 min). Półka
nie pokazuje ogłoszeń, którym sprzedawca nie ustawił typu - takich było
185 na 28 400 (0,65% wolumenu, ale 12% powiadomień z Niemiec).

Sortowanie po dacie kosztuje ZERO dodatkowych żądań - to człon adresu. Ale
stoi na założeniu o cudzym serwisie, a takich w tym repo nie wolno wdrażać
na słowo. Stąd ten pomiar.

DWIE PUŁAPKI, KTÓRYCH TEN SKRYPT UNIKA Z ROZMYSŁEM
==================================================
1. ZIGNOROWANY PARAMETR WYGLĄDA IDENTYCZNIE. Strona wróci z kodem 200
   i pełną listą, tylko posortowaną po staremu. Dlatego nie sprawdzamy, czy
   się pobrała - porównujemy ROZKŁAD WIEKU ogłoszeń ze starego i nowego
   adresu, pobranych w tej samej minucie.
2. NIE PISZEMY WŁASNEGO PARSERA. Wszystko idzie przez `tracker.fetch_listings`
   i `otomoto_tracker._fetch_page`, czyli dokładnie ten kod, który czyta
   produkcja. Porównywanie wzorca z własną kopią tego samego wzorca wyszło
   w tym repo dwa razy tego samego dnia i za każdym razem dało wynik idealny
   i fałszywy.

TRZECIE PYTANIE, NIEZALEŻNE OD SORTOWANIA: ile minut rynku pokrywa strona 1
pełnej kategorii rowerów. Od tej liczby zależy, czy trzecia półka (ogłoszenia
bez ustawionego typu) ma sens przy skanie co ~64 s, czy przecieka.

Budżet żądań: 3 frazy x 2 + 2 strony kategorii + 2 Otomoto = 10.
"""
import json
import os
import statistics
import sys
import tempfile
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Stan botów NA BOK, zanim cokolwiek zaimportujemy i zawołamy. Ta sama
# ostrożność co przy narzędziu z 15.09, które przy imporcie po cichu zrobiło
# `git pull` na żywym repo.
_PIASEK = Path(tempfile.mkdtemp(prefix="diagnoza-"))

import tracker as t                                            # noqa: E402

t.SEEN_FILE = _PIASEK / "seen.json"
t.MARKET_FILE = _PIASEK / "market.jsonl"
t.FEED_STATE_FILE = _PIASEK / "feed_stan.json"
t.STATE_FILE = getattr(t, "STATE_FILE", _PIASEK / "stan.json")
t.BLACKBOX_DIR = _PIASEK / "blackbox"

ODSTEP_S = 8          # łagodnie: dławienie Kleinanzeigen jest per adres IP
RAPORT = Path("diagnoza") / f"sortowanie-{datetime.now(timezone.utc):%Y-%m-%dT%H%M}.json"

# Człon sortowania po dacie w adresie Kleinanzeigen. Stoi PRZED frazą, bo
# tak serwis składa filtry w ścieżce (`/s-<filtry>/<fraza>/k0`).
SORT_DATA = "sortierung:SORTIERUNG_DATUM"


def _adres_stary(slug):
    return f"https://www.kleinanzeigen.de/s-preis:{t.MIN_PRICE}:{t.MAX_PRICE}/{slug}/k0"


def _adres_nowy(slug):
    return (f"https://www.kleinanzeigen.de/s-{SORT_DATA}/"
            f"preis:{t.MIN_PRICE}:{t.MAX_PRICE}/{slug}/k0")


def _wiek_min(l, teraz):
    """Wiek ogłoszenia w minutach, liczony kodem produkcyjnym."""
    p = l.get("posted")
    if not p:
        return None
    try:
        return t.ad_age_minutes(p, teraz)
    except Exception:
        return None


def zmierz_ka(nazwa, adres):
    """Jedno pobranie listy Kleinanzeigen + metryki. Nic nie zapisuje."""
    teraz = datetime.now(timezone.utc)
    listings, stats = t.fetch_listings({"name": nazwa, "url": adres})
    wieki = [w for w in (_wiek_min(l, teraz) for l in listings) if w is not None]
    out = {
        "adres": adres,
        "status": stats.get("status"),
        "kafelkow": stats.get("blocks"),
        "ogloszen": len(listings),
        "z_data": len(wieki),
        "title_rate": stats.get("title_rate"),
        "price_rate": stats.get("price_rate"),
    }
    if wieki:
        w = sorted(wieki)
        out.update({
            "wiek_min_najmlodsze": round(w[0]),
            "wiek_min_mediana": round(statistics.median(w)),
            "wiek_min_najstarsze": round(w[-1]),
            "rozpietosc_min": round(w[-1] - w[0]),
            "pierwsze_5_wiekow": [round(x) for x in
                                  [_wiek_min(l, teraz) or -1 for l in listings[:5]]],
        })
    return out


def zmierz_otomoto(nazwa, adres):
    import otomoto_tracker as ot
    teraz = datetime.now(timezone.utc)
    edges = ot._fetch_page(adres)
    wieki = []
    for e in edges:
        n = e.get("node", e)
        c = n.get("createdAt") or ""
        try:
            dt = datetime.fromisoformat(c.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            wieki.append((teraz - dt).total_seconds() / 60)
        except Exception:
            pass
    out = {"adres": adres, "edges": len(edges), "z_data": len(wieki)}
    if wieki:
        w = sorted(wieki)
        out.update({
            "wiek_min_najmlodsze": round(w[0]),
            "wiek_min_mediana": round(statistics.median(w)),
            "wiek_min_najstarsze": round(w[-1]),
            "pierwsze_5_wiekow": [round((teraz - datetime.fromisoformat(
                (e.get("node", e).get("createdAt") or "").replace("Z", "+00:00")
                )).total_seconds() / 60) for e in edges[:5]
                if e.get("node", e).get("createdAt")],
        })
    return out


def main():
    raport = {"kiedy": datetime.now(timezone.utc).isoformat(), "kleinanzeigen": {},
              "kategoria": {}, "otomoto": {}}

    # --- A. SORTOWANIE ZAPYTAŃ KLUCZOWYCH -------------------------------
    # Trzy frazy o różnej szerokości: wąska modelowa, średnia, bardzo szeroka.
    # Szeroka jest tu najważniejsza - przy sortowaniu po dacie to ONA może się
    # wysypywać ze strony 1 szybciej niż co 23 minuty, czyli lekarstwo byłoby
    # gorsze od choroby.
    for slug in ("cube-stereo-hybrid", "trek-rail", "emtb"):
        print(f"\n=== {slug} ===", flush=True)
        para = {}
        for etykieta, adres in (("trafnosc", _adres_stary(slug)),
                                ("data", _adres_nowy(slug))):
            para[etykieta] = zmierz_ka(f"{slug}/{etykieta}", adres)
            print(f"  {etykieta:9s} {json.dumps(para[etykieta], ensure_ascii=False)}",
                  flush=True)
            time.sleep(ODSTEP_S)
        raport["kleinanzeigen"][slug] = para

    # --- B. PEŁNA KATEGORIA ROWERÓW, ILE MINUT KRYJE STRONA -------------
    for n in (1, 2):
        adres = ("https://www.kleinanzeigen.de/s-fahrraeder/"
                 + ("" if n == 1 else f"seite:{n}/") + "c217")
        print(f"\n=== cała kategoria, strona {n} ===", flush=True)
        raport["kategoria"][f"strona{n}"] = zmierz_ka(f"kategoria s.{n}", adres)
        print(f"  {json.dumps(raport['kategoria'][f'strona{n}'], ensure_ascii=False)}",
              flush=True)
        time.sleep(ODSTEP_S)

    # --- C. OTOMOTO ------------------------------------------------------
    try:
        import otomoto_tracker as ot
        baza = ot.SEARCHES[0]["url"]
        for etykieta, adres in (
                ("trafnosc", baza),
                ("data", baza + "&search%5Border%5D=created_at_first%3Adesc")):
            print(f"\n=== otomoto / {etykieta} ===", flush=True)
            raport["otomoto"][etykieta] = zmierz_otomoto(etykieta, adres)
            print(f"  {json.dumps(raport['otomoto'][etykieta], ensure_ascii=False)}",
                  flush=True)
            time.sleep(ODSTEP_S)
    except Exception as e:
        raport["otomoto"]["blad"] = str(e)
        print(f"otomoto: {e}", flush=True)

    RAPORT.parent.mkdir(exist_ok=True)
    RAPORT.write_text(json.dumps(raport, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n>>> raport: {RAPORT}", flush=True)
    print(json.dumps(raport, ensure_ascii=False, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
