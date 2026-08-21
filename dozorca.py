#!/usr/bin/env python3
"""Dozorca OLX — obserwuje polski rynek i zapisuje SUROWE FAKTY.

ZASADA NACZELNA: zapisujemy to, co widzieliśmy, a nie to, co z tego wynika.
Dziennik mówi "oferta X była widoczna 21.08 o 14:00 za 12 900 zł" — nigdy
"ten rower sprzedał się w 5 dni". Wnioski (sprzedaż vs wznowienie, czas
sprzedaży, krzywe przeżycia) liczy się osobno z tego dziennika i można je
przeliczyć od zera, gdy reguły okażą się błędne.

Poprzednia katastrofa (fałszywe "100% sprzedaje się w 2 dni") wzięła się
dokładnie stąd, że zapisywaliśmy wnioski — i trzeba było skasować dane.

Pliki:
  zdarzenia/olx-RRRR-MM.jsonl  — dziennik zdarzeń, append-only, NIGDY nie kasowany
  olx_stan.json                — bieżący stan (kto żyje); ODTWARZALNY z dziennika

Uruchom: python3 dozorca.py [ile_modeli]
"""
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

import tracker

STAN_FILE = Path("olx_stan.json")
ZDARZENIA_DIR = Path("zdarzenia")
H = {"User-Agent": "Mozilla/5.0", "Accept-Language": "pl-PL"}

# Oferta potrafi wypaść z okna wyników bez powodu (ranking, stronicowanie),
# więc pojedyncze zniknięcie nic nie znaczy. Dopiero tyle przebiegów z rzędu
# bez niej każe sprawdzić stronę oferty.
BRAKI_DO_SPRAWDZENIA = 3
MAX_SPRAWDZEN_NA_PRZEBIEG = 40      # limit pobrań stron ofert (grzeczność + czas)


def teraz_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M")


def odcisk(tytul: str, cena, loc: str) -> str:
    """Odcisk palca oferty — do rozpoznania, że sprzedawca skasował ogłoszenie
    i wystawił je od nowa (wznowienie), zamiast sprzedać rower.
    Tytuł sprowadzony do samych liter i cyfr, żeby drobne zmiany nie myliły."""
    t = re.sub(r'[^a-z0-9ąćęłńóśźż]+', '', (tytul or "").lower())[:60]
    return f"{t}|{cena}|{(loc or '').lower()}"


def wykryj_zdarzenia(stan: dict, biezace: dict, zapytania_ok: set, teraz: str):
    """SERCE DOZORCY — czysta funkcja, bez sieci i bez plików (testowalna).

    stan:         {id_oferty: rekord} — co wiedzieliśmy do tej pory
    biezace:      {id_oferty: {...}} — co widać w tym przebiegu
    zapytania_ok: zapytania, które udało się pobrać. Oferty z zapytań, które
                  padły, NIE mogą być uznane za zaginione — inaczej awaria
                  sieci wyglądałaby jak masowa wyprzedaż.

    Zwraca (lista_zdarzeń, nowy_stan)."""
    zdarzenia = []
    stan = {k: dict(v) for k, v in stan.items()}      # nie modyfikujemy wejścia

    for oid, ob in biezace.items():
        stary = stan.get(oid)
        if stary is None:
            rec = {"url": ob["url"], "p": ob["p"], "p0": ob["p"],
                   "tytul": ob.get("tytul"), "loc": ob.get("loc"),
                   "q": ob.get("q"), "pierwszy": teraz, "ostatni": teraz,
                   "odcisk": odcisk(ob.get("tytul"), ob["p"], ob.get("loc"))}
            stan[oid] = rec
            zdarzenia.append({"ts": teraz, "ev": "nowa", "id": oid, "p": ob["p"],
                              "q": ob.get("q"), "tytul": ob.get("tytul"),
                              "loc": ob.get("loc"), "odcisk": rec["odcisk"],
                              "url": ob["url"]})
            continue

        if stary.pop("braki", 0):                     # była zaginiona, wróciła
            zdarzenia.append({"ts": teraz, "ev": "wrocila", "id": oid})
        if ob["p"] != stary.get("p"):
            zdarzenia.append({"ts": teraz, "ev": "cena", "id": oid,
                              "p": ob["p"], "p_stara": stary.get("p")})
            stary["p"] = ob["p"]
        stary["ostatni"] = teraz
        stan[oid] = stary

    # nieobecne w tym przebiegu — tylko z zapytań, które faktycznie się pobrały
    for oid, rec in stan.items():
        if oid in biezace or rec.get("q") not in zapytania_ok:
            continue
        rec["braki"] = rec.get("braki", 0) + 1
    return zdarzenia, stan


def do_sprawdzenia(stan: dict, limit: int = MAX_SPRAWDZEN_NA_PRZEBIEG):
    """Oferty nieobecne dostatecznie długo, by sprawdzić ich stronę.
    Najpierw te najdłużej zaginione."""
    kand = [(oid, r) for oid, r in stan.items()
            if r.get("braki", 0) >= BRAKI_DO_SPRAWDZENIA]
    kand.sort(key=lambda x: -x[1].get("braki", 0))
    return kand[:limit]


def dni_zycia(rec, teraz: str):
    """Ile dni oferta była widoczna. None gdy daty nie da się odczytać."""
    try:
        a = datetime.strptime(rec["pierwszy"], "%Y-%m-%dT%H:%M")
        b = datetime.strptime(teraz, "%Y-%m-%dT%H:%M")
        return max(0, (b - a).days)
    except Exception:
        return None


def zapisz_zdarzenia(zdarzenia):
    """Dopisuje do miesięcznego pliku. Rotacja miesięczna trzyma pliki małe,
    ale NIC nie jest kasowane — dziennik rośnie w nieskończoność."""
    if not zdarzenia:
        return
    ZDARZENIA_DIR.mkdir(exist_ok=True)
    plik = ZDARZENIA_DIR / f"olx-{datetime.now(timezone.utc):%Y-%m}.jsonl"
    with plik.open("a", encoding="utf-8") as f:
        for z in zdarzenia:
            f.write(json.dumps(z, ensure_ascii=False) + "\n")


def wczytaj_stan() -> dict:
    try:
        return json.loads(STAN_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def modele(limit):
    try:
        w = json.loads(Path("olx_watch.json").read_text())
        q = sorted(w, key=lambda k: -len((w[k] or {}).get("offers", {})))
    except Exception:
        q = []
    q = [x for x in q if x and not x[0].isupper()]
    return q[:limit] or ["cube stereo hybrid", "trek rail", "specialized turbo levo"]


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    teraz = teraz_utc()
    stan = wczytaj_stan()
    biezace, zapytania_ok = {}, set()

    for q in modele(limit):
        try:
            url = "https://www.olx.pl/sport-hobby/rowery/q-" + q.replace(" ", "-") + "/"
            html = requests.get(url, headers=H, timeout=25).text
        except Exception as e:
            print(f"  [{q}] blad pobrania: {e}")
            continue                       # NIE dodajemy do zapytania_ok
        karty = tracker.parse_olx_cards(html)
        if not karty:
            print(f"  [{q}] 0 kart — pomijam (nie uznaje za wymarcie)")
            continue
        trafne = tracker.olx_relevant_offers(q, {c["url"]: c["price"] for c in karty})
        zapytania_ok.add(q)
        for c in karty:
            if c["url"] not in trafne:
                continue
            oid = c.get("id") or c["url"]
            biezace[oid] = {"url": c["url"], "p": c["price"], "q": q,
                            "tytul": c.get("title"), "loc": c.get("loc")}
        print(f"  [{q}] {len(trafne)} ofert")
        time.sleep(0.4)

    zdarzenia, stan = wykryj_zdarzenia(stan, biezace, zapytania_ok, teraz)

    # potwierdzenie śmierci — wymaga POZYTYWNEGO dowodu (404/status nieaktywny).
    # Samo wypadniecie z wyników nigdy nie liczy sie jako zniknięcie.
    for oid, rec in do_sprawdzenia(stan):
        gone = tracker.olx_offer_gone(rec["url"])
        if gone is True:
            zdarzenia.append({"ts": teraz, "ev": "znikla", "id": oid,
                              "p": rec.get("p"), "p0": rec.get("p0"),
                              "q": rec.get("q"), "odcisk": rec.get("odcisk"),
                              "dni": dni_zycia(rec, teraz)})
            stan.pop(oid, None)
        elif gone is False:
            rec["braki"] = 0               # żyje, tylko wypadła z okna wyników
        time.sleep(0.3)

    zapisz_zdarzenia(zdarzenia)
    STAN_FILE.write_text(json.dumps(stan, ensure_ascii=False), encoding="utf-8")
    ile = {}
    for z in zdarzenia:
        ile[z["ev"]] = ile.get(z["ev"], 0) + 1
    print(f"zdarzenia: {ile or 'brak'} | obserwowanych ofert: {len(stan)}")


if __name__ == "__main__":
    main()
