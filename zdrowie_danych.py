#!/usr/bin/env python3
"""Zdrowie danych - czy pola w plikach nadal niosą prawdziwe wartości.

PO CO TO JEST: osiem razy z rzędu ten sam kształt awarii - rura produkuje
wartość, która wygląda wiarygodnie, i nic nie krzyczy. odduplikuj() po
nieistniejącym polu, regexp na sprzedawcę z zerem trafień na 1427 wierszy,
parser OLX czytający 20 z 52 kafelków, loc z fragmentem ścieżki SVG w 34%
wpisów, 69% duplikatów w zdarzeniach ceny, wykrywanie skasowanych myląc się
w 62% przypadków. Każde z nich stało miesiąc albo dłużej, zanim ktoś
przypadkiem spojrzał.

ZASADA: żadnych progów wziętych z głowy. Są dwa pytania, na które dane
odpowiadają same:
  1. Czy pole NIGDY nie zadziałało (zero wartości od zawsze)?
  2. Czy PRZESTAŁO działać (starsza połowa wierszy pełna, nowsza pusta)?
Punktem odniesienia jest własna historia pliku, nie liczba, którą ktoś wymyślił.

CZEGO NIE ROBI: nie pobiera nic z sieci, nie zapisuje do żadnego pliku
trackera, nie ocenia rowerów. Czyta i liczy. Nie może zepsuć DealHawka.

UWAGA NA PUŁAPKĘ, W KTÓRĄ SAM WDEPNĄŁEM 29.08.2026: False i 0 to WARTOŚCI,
nie brak wartości. Policzone jako puste dają kilkanaście fałszywych alarmów
na polach logicznych i licznikach.

Uruchom: python3 zdrowie_danych.py
Kod wyjścia: 0 gdy zdrowo, 1 gdy są problemy (do użycia w zadaniu cyklicznym).
"""
import collections
import json
import sys
from pathlib import Path

PUSTE = (None, "", [], {})          # False i 0 to wartości, patrz docstring

PLIKI = ["market.jsonl", "history.jsonl", "rynek_pl.jsonl", "seen.json",
         "olx_watch.json", "de_stan.json", "olx_stan.json", "olx_details.json",
         "zdarzenia/olx-2026-08.jsonl", "zdarzenia_de/de-2026-08.jsonl"]

# Pola, o których wiadomo, że są rzadkie z projektu, a nie z awarii.
# Każdy wyjątek musi mieć powód, inaczej lista wyjątków zjada czujkę.
WYJATKI = {
    "seen.json": {  # pełne dane dostają tylko finaliści, reszta to sam znacznik
        "title", "price", "price_num", "mileage", "mileage_num", "url", "search",
        "score", "profit", "olx_median", "year", "buy_price", "nego_pct",
        "liquidity_days", "roi_annual", "loc", "korekta"},
    "history.jsonl": {"bf"},        # martwe pole ze starej wersji, nic go nie pisze od 09.07.2026
}
MIN_WIERSZY = 30                     # poniżej tego każdy wniosek to szum


def wiersze(p: Path):
    if p.suffix == ".jsonl":
        with p.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        continue
        return
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return
    for v in (d.values() if isinstance(d, dict) else d):
        if isinstance(v, dict):
            yield v


def zbadaj(rows, wyjatki=frozenset()):
    """Czysta funkcja: lista rekordów -> lista problemów. Bez plików, bez sieci."""
    n = len(rows)
    if n < MIN_WIERSZY:
        return []
    pola = collections.Counter(k for r in rows for k in r)
    klucz_czasu = next((k for k in ("ts", "date", "pierwszy") if pola.get(k, 0) > n * 0.8), None)
    # Plik z JEDNEGO przebiegu nie mówi nic o tym, czy pole działa. Wszystkie
    # jego wiersze to pierwszy kontakt, więc pola wypełniane dopiero przy
    # drugim spotkaniu (jak ostatni_zywy) są puste ZGODNIE Z PRAWDĄ.
    # ZMIERZONE 29.08.2026: zdarzenia_de miało 76 wierszy z jednego przebiegu
    # i czujka zgłaszała je jako awarię. Alarm na zdrowych danych uczy
    # ignorowania alarmów, więc jest gorszy od braku alarmu.
    if klucz_czasu and len({str(r.get(klucz_czasu))[:10] for r in rows}) < 2:
        return []
    if klucz_czasu:
        rows = sorted(rows, key=lambda r: str(r.get(klucz_czasu) or ""))
        stare, nowe = rows[:n // 2], rows[n // 2:]
    else:
        stare = nowe = None

    problemy = []
    for pole in pola:
        if pole in wyjatki:
            continue
        wypelnione = [r.get(pole) for r in rows if r.get(pole) not in PUSTE]
        if not wypelnione:
            problemy.append((pole, "NIGDY NIE ZADZIALALO",
                             f"zero wartości w {n} wierszach"))
            continue
        if stare and len(stare) >= MIN_WIERSZY // 2:
            a = 100 * sum(1 for r in stare if r.get(pole) not in PUSTE) / len(stare)
            b = 100 * sum(1 for r in nowe if r.get(pole) not in PUSTE) / len(nowe)
            # "Przestało" tylko przy wyraźnym załamaniu. Wahania rzędu kilku
            # punktów to normalna zmienność ogłoszeń, nie awaria parsera.
            if a > 20 and b < a / 4:
                problemy.append((pole, "PRZESTALO DZIALAC",
                                 f"starsze wiersze {a:.0f}% wypełnione, nowsze {b:.0f}%"))
    return problemy


def main():
    katalog = Path(__file__).resolve().parent
    znaleziono = []
    for nazwa in PLIKI:
        p = katalog / nazwa
        if not p.exists():
            continue
        rows = list(wiersze(p))
        for pole, typ, detal in zbadaj(rows, WYJATKI.get(nazwa, frozenset())):
            znaleziono.append((nazwa, pole, typ, detal))

    if not znaleziono:
        print("Zdrowie danych: w porządku. Nic nie wymaga uwagi.")
        return 0

    print("ZDROWIE DANYCH: ZNALEZIONO PROBLEMY\n")
    for nazwa, pole, typ, detal in znaleziono:
        print(f"  {nazwa}")
        print(f"    pole '{pole}': {typ}")
        print(f"    {detal}\n")
    print("Każdy wpis znaczy, że jakaś liczba w wycenie stoi na pustce albo na\n"
          "wartości, która nie zmienia się mimo zmian na rynku.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
