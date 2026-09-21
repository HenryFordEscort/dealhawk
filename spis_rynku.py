#!/usr/bin/env python3
"""Spis rynku OLX - ile ogloszen stoi dzis w kazdym wycinku rynku.

PO CO TO ISTNIEJE (ustalone 20.09.2026). Wlasciciel chcial wiedziec, jak duzy
jest polski rynek rowerow elektrycznych i w ktora strone idzie. Okazalo sie,
ze tego drugiego NIE DALO SIE policzyc z niczego, co bot mial:

  - `olx_watch.json` siega 10 tygodni, ma w srodku 10 dni ciszy (11-20.08)
    i zmieniony w trakcie zbieracz. Przyrost "4,7 -> 21,1 nowych ofert na
    dzien" wyszedl rowno w 26 z 30 zapytan (mediana mnoznika 4,7x). Rynek tak
    nie rosnie. Tak rosnie narzedzie pomiarowe.
  - Rozklad daty wystawienia AKTYWNYCH ofert (lipiec 107, sierpien 201,
    wrzesien 177) wygladal jak wzrost, a jest krzywa przezycia: starsze
    ogloszenia zdazyly zejsc. To pomiar tego, co zostalo, nie tego, co bylo.

Trendu nie da sie odtworzyc wstecz. Da sie tylko zaczac mierzyc i czekac.
Ten plik jest tym zaczeciem: raz na dobe pyta OLX-a "ile ogloszen masz w tym
wycinku" i dopisuje liczbe do dziennika. Po kwartale jest krzywa, po roku
sezonowosc. Jedno zapytanie na wycinek, kilkadziesiat na dobe.

ZASADA (ta sama, co w dozorcy): dziennik trzyma WYLACZNIE zmierzone liczby.
Zadnych udzialow, dynamik ani "rynek rosnie" - to wnioski, liczy je
`trend()` przy czytaniu i wolno je przeliczyc od zera, gdy regula okaze sie
zla. Nieudane zapytanie NIE zapisuje zera: brak wpisu znaczy "nie wiem",
a zero znaczyloby "rynek pusty" i wygladaloby jak zapasc.

Pliki:
  spis_rynku.jsonl  - dziennik, append-only, NIGDY nie kasowany

Uruchom:
  python3 spis_rynku.py            # zmierz i dopisz (raz na dobe wystarczy)
  python3 spis_rynku.py --raport   # pokaz, co z dziennika wynika
"""
import json
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from olx import olx_get

SPIS_FILE = Path("spis_rynku.jsonl")

# Wersja definicji wycinkow. Kazda zmiana listy PRZEKROJE podnosi ten numer,
# zeby przy czytaniu bylo widac, ze szereg zmienil sens. Dokladnie brak takiego
# znacznika sprawil, ze przyrost zapytan w `olx_watch.json` przez miesiac
# udawal przyrost rynku.
SCHEMA = 1

# Identyfikatory kategorii OLX, odczytane 20.09.2026 ze stron kategorii
# (parametr `category_id=...&suggest_filter` w linkach podpowiedzi).
KAT = {
    "rowery_wszystkie": 461,
    "elektryczne": 1649,
    "gorskie": 1651,
    "miejskie": 1650,
    "dzieciece": 1681,
    "crossowe": 1648,
    "trekkingowe": 1653,
    "szosowe": 1652,
    "gravel": 4242,
}

EB = KAT["elektryczne"]

# Wycinek = (id, opis, parametry API). ID jest KLUCZEM SZEREGU i nie wolno go
# zmieniac: pod nim leza wszystkie pomiary z przeszlosci. Zmiana definicji
# pytania przy zachowanym id zafalszowalaby historie po cichu - dlatego nowe
# pytanie dostaje nowe id, a stare zostaje albo przestaje byc mierzone.
PRZEKROJE = []

for _nazwa, _cid in KAT.items():
    PRZEKROJE.append((f"kat_{_nazwa}", f"kategoria: {_nazwa}", {"category_id": _cid}))

# Kto wystawia. Udzial firm decyduje o tym, z kim sie konkuruje: firma daje
# gwarancje, faktyre i raty, prywatny nie. Zmierzone 20.09.2026: 31% ofert
# w obserwowanej niszy to firmy.
PRZEKROJE += [
    ("eb_prywatne", "e-rowery od osob prywatnych", {"category_id": EB, "owner_type": "private"}),
    ("eb_firmy", "e-rowery od firm", {"category_id": EB, "owner_type": "business"}),
    ("eb_nowe", "e-rowery nowe", {"category_id": EB, "filter_enum_state[0]": "new"}),
    ("eb_uzywane", "e-rowery uzywane", {"category_id": EB, "filter_enum_state[0]": "used"}),
]

# Pasma cenowe. Te same granice, co w SEGMENT_BANDS w tracker.py, zeby spis
# rynku i pomiar plynnosci mowily o tych samych polkach.
PASMA = [(0, 3000), (3000, 5000), (5000, 8000), (8000, 12000),
         (12000, 16000), (16000, 25000), (25000, None)]
for _lo, _hi in PASMA:
    _p = {"category_id": EB, "filter_float_price:from": _lo}
    if _hi:
        _p["filter_float_price:to"] = _hi
    PRZEKROJE.append((f"eb_cena_{_lo}_{_hi or 'wyzej'}",
                      f"e-rowery {_lo}-{_hi or 'wyzej'} zl", _p))

# Nasza polka, osobno w rozbiciu na sprzedawce: to bezposrednia konkurencja
# przy sprzedazy i jedyny wycinek, w ktorym bot realnie dziala.
PRZEKROJE += [
    ("eb_8k_prywatne", "e-rowery 8k+ od prywatnych",
     {"category_id": EB, "filter_float_price:from": 8000, "owner_type": "private"}),
    ("eb_8k_firmy", "e-rowery 8k+ od firm",
     {"category_id": EB, "filter_float_price:from": 8000, "owner_type": "business"}),
]

# Silniki i marki. Fraza szuka po tytule i opisie, wiec to POSZLAKA, nie spis:
# ogloszenie bez slowa "bosch" w tresci nie wpadnie, a ogloszenie czesci wpadnie.
# Do trendu wystarcza, bo blad jest ten sam kazdego dnia i znosi sie w roznicy.
for _q in ("bosch", "shimano", "bafang", "yamaha", "brose"):
    PRZEKROJE.append((f"eb_silnik_{_q}", f"e-rowery, fraza '{_q}'",
                      {"category_id": EB, "query": _q}))
for _q in ("cube", "specialized", "trek", "ktm", "scott", "haibike", "giant", "kross"):
    PRZEKROJE.append((f"eb_marka_{_q}", f"e-rowery, fraza '{_q}'",
                      {"category_id": EB, "query": _q}))

API = "https://www.olx.pl/api/v1/offers/"


def zmierz_wycinek(params: dict, timeout: int = 25):
    """Ile ogloszen OLX ma w tym wycinku. None = nie wiadomo, NIE zero.

    Liczba idzie z `metadata.visible_total_count`, czyli z licznika OLX-a,
    a nie ze zliczania kafelkow. Roznica jest istotna: API oddaje najwyzej
    1000 rekordow (`total_elements` zatrzymuje sie na 1000), wiec zliczanie
    ofert dawaloby sufit 1000 na kazdym wiekszym wycinku i caly rynek
    wygladalby na rowno tysiac ogloszen."""
    q = dict(params)
    q["offset"] = 0
    q["limit"] = 1           # tresc ofert nas tu nie interesuje, tylko licznik
    adres = API + "?" + "&".join(f"{k}={v}" for k, v in q.items())
    r = olx_get(adres, timeout=timeout)
    if r is None or r.status_code != 200:
        return None
    try:
        n = (r.json().get("metadata") or {}).get("visible_total_count")
    except Exception:
        return None
    return n if isinstance(n, int) else None


def zmierzone_dzis(dzien: str) -> set:
    """Wycinki, ktore maja JUZ pomiar z tego dnia.

    Dzieki temu skrypt wolno odpalac czesciej niz raz na dobe: powtorzony
    przebieg nie dubluje pomiarow, ale DOMYKA te, ktore ranem padly. To
    wazne, bo cron GitHuba spoznia sie nieregularnie (zmierzone 15.09.2026
    na Otomoto: mediana 3,6 h opoznienia, p90 5,7 h)."""
    if not SPIS_FILE.exists():
        return set()
    out = set()
    for linia in SPIS_FILE.read_text(encoding="utf-8").splitlines():
        if not linia.strip():
            continue
        try:
            r = json.loads(linia)
        except json.JSONDecodeError:
            continue
        if r.get("dzien") == dzien and isinstance(r.get("n"), int):
            out.add(r.get("id"))
    return out


def spisz(dzien: str = None, pauza: float = 1.0, tylko_brakujace: bool = True) -> dict:
    """Mierzy wszystkie wycinki i dopisuje do dziennika. Zwraca podsumowanie."""
    dzien = dzien or date.today().isoformat()
    juz = zmierzone_dzis(dzien) if tylko_brakujace else set()
    do_zrobienia = [p for p in PRZEKROJE if p[0] not in juz]
    wiersze, udane, puste = [], 0, 0
    for ident, opis, params in do_zrobienia:
        n = zmierz_wycinek(params)
        if n is None:
            puste += 1                 # brak wpisu = "nie wiem"; zera NIE pisemy
        else:
            udane += 1
            wiersze.append({
                "ts": datetime.now().strftime("%Y-%m-%dT%H:%M"),
                "dzien": dzien, "id": ident, "n": n, "schema": SCHEMA,
                # zapytanie w dzienniku, zeby po roku dalo sie sprawdzic, CO
                # dokladnie zmierzono, bez grzebania w historii gita
                "zapytanie": {k: v for k, v in params.items()},
            })
        time.sleep(pauza)
    if wiersze:
        with SPIS_FILE.open("a", encoding="utf-8") as f:
            for w in wiersze:
                f.write(json.dumps(w, ensure_ascii=False) + "\n")
    return {"dzien": dzien, "zmierzone": udane, "nieudane": puste,
            "pominiete_bo_juz_sa": len(juz), "wycinkow": len(PRZEKROJE)}


def czytaj() -> dict:
    """Dziennik -> {id_wycinka: {dzien: n}}. Przy dwoch pomiarach tego samego
    dnia zostaje PIERWSZY: pomiar z ustalonej godziny jest porownywalny,
    a dobrany pozniej "lepszy" psulby szereg."""
    out = {}
    if not SPIS_FILE.exists():
        return out
    for linia in SPIS_FILE.read_text(encoding="utf-8").splitlines():
        if not linia.strip():
            continue
        try:
            r = json.loads(linia)
        except json.JSONDecodeError:
            continue
        if not (isinstance(r.get("n"), int) and r.get("id") and r.get("dzien")):
            continue
        out.setdefault(r["id"], {}).setdefault(r["dzien"], r["n"])
    return out


def _najblizszy(seria: dict, cel: date, luz_dni: int = 3):
    """Pomiar z okolic danego dnia. Luz, bo dzien moze wypasc na awarii OLX-a."""
    naj = None
    for d, n in seria.items():
        try:
            dd = date.fromisoformat(d)
        except ValueError:
            continue
        odl = abs((dd - cel).days)
        if odl <= luz_dni and (naj is None or odl < naj[0]):
            naj = (odl, d, n)
    return (naj[1], naj[2]) if naj else (None, None)


def trend(okresy=(7, 28, 91)) -> list:
    """WNIOSEK z dziennika: o ile zmienil sie kazdy wycinek. Liczony przy
    czytaniu, nigdy nie zapisywany.

    Zwraca liste slownikow. `zmiana_pct` = None znaczy "nie wiem" - albo nie
    ma jeszcze pomiaru z tamtego dnia, albo baza byla zerowa. Bot ma mowic
    "nie wiem", a nie dzielic przez brak danych."""
    dane = czytaj()
    dzis = date.today()
    out = []
    for ident, opis, _ in PRZEKROJE:
        seria = dane.get(ident) or {}
        if not seria:
            continue
        ostatni_dzien = max(seria)
        teraz = seria[ostatni_dzien]
        rek = {"id": ident, "opis": opis, "n": teraz, "dzien": ostatni_dzien,
               "pomiarow": len(seria), "zmiany": {}}
        for dni in okresy:
            d, n = _najblizszy(seria, date.fromisoformat(ostatni_dzien) - timedelta(days=dni))
            rek["zmiany"][dni] = {"od": n, "dzien": d,
                                  "pct": round((teraz - n) / n * 100, 1) if n else None}
        out.append(rek)
    _ = dzis
    return out


def dni_pomiarow() -> int:
    """Ile roznych dni ma w dzienniku jakikolwiek pomiar. To jedyna uczciwa
    miara tego, czy o trendzie wolno juz cokolwiek powiedziec."""
    dane = czytaj()
    return len({d for seria in dane.values() for d in seria})


def raport() -> str:
    """Tekst na Telegram: stan rynku i zmiana. HTML, bo tak mowi reszta bota."""
    z = lambda v: f"{int(v):,}".replace(",", " ")
    t = {r["id"]: r for r in trend()}
    dni = dni_pomiarow()
    if not t:
        return ("🧮 <b>Spis rynku OLX</b>\nDziennik jest pusty. Uruchom "
                "<code>python3 spis_rynku.py</code>, zeby zrobic pierwszy pomiar.")
    L = ["🧮 <b>Rynek rowerow elektrycznych w PL (OLX)</b>"]

    eb = t.get("kat_elektryczne")
    wsz = t.get("kat_rowery_wszystkie")
    if eb:
        L.append(f"\n<b>{z(eb['n'])}</b> ogloszen stoi teraz w kategorii e-rowery")
        if wsz and wsz["n"]:
            L.append(f"to {eb['n'] / wsz['n'] * 100:.1f}% wszystkich rowerow "
                     f"na OLX ({z(wsz['n'])})")

    # Trend wolno pokazac tylko wtedy, gdy jest z czego. Ponizej dwoch tygodni
    # pomiarow kazda zmiana to szum tygodnia, a nie tendencja.
    if dni < 14:
        L.append(f"\n<i>ⓘ Trendu jeszcze nie ma: {dni} dni pomiarow. "
                 f"Pierwsza sensowna zmiana tydzien do tygodnia bedzie po 14 dniach, "
                 f"sezonowosc po roku.</i>")
    else:
        L.append("\n<b>Zmiana liczby ogloszen</b>")
        for ident in ("kat_elektryczne", "eb_prywatne", "eb_firmy",
                      "eb_8k_prywatne", "eb_silnik_bosch"):
            r = t.get(ident)
            if not r:
                continue
            czesci = []
            for dni_ok in (7, 28, 91):
                p = (r["zmiany"].get(dni_ok) or {}).get("pct")
                czesci.append(f"{dni_ok}d: {p:+.1f}%" if p is not None else f"{dni_ok}d: ?")
            L.append(f"· {r['opis']}: {z(r['n'])} ({', '.join(czesci)})")

    L.append("\n<b>Podaz wg polki cenowej</b>")
    for _lo, _hi in PASMA:
        r = t.get(f"eb_cena_{_lo}_{_hi or 'wyzej'}")
        if r:
            L.append(f"· {_lo}-{_hi or 'wyzej'} zl: {z(r['n'])}")

    pryw, firm = t.get("eb_prywatne"), t.get("eb_firmy")
    if pryw and firm and (pryw["n"] + firm["n"]):
        L.append(f"\nPrywatni {z(pryw['n'])} / firmy {z(firm['n'])} "
                 f"({firm['n'] / (pryw['n'] + firm['n']) * 100:.0f}% oferty firmowe)")

    L.append(f"\n<i>ⓘ To liczba OGLOSZEN STOJACYCH, nie sprzedanych. Ile rowerow "
             f"schodzi, OLX nie publikuje i nie da sie tego stad policzyc. "
             f"Pomiarow w dzienniku: {dni} dni.</i>")
    return "\n".join(L)


if __name__ == "__main__":
    if "--raport" in sys.argv:
        print(raport().replace("<b>", "").replace("</b>", "")
              .replace("<i>", "").replace("</i>", "")
              .replace("<code>", "").replace("</code>", ""))
    else:
        print(json.dumps(spisz(), ensure_ascii=False))
