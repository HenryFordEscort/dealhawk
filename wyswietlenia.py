#!/usr/bin/env python3
"""Wyswietlenia ogloszen OLX - pierwszy pomiar POPYTU w tym projekcie.

PO CO (16.11.2026). Wszystkie dotychczasowe instrumenty - spis rynku,
przeplyw polki, plynnosc, polka DE - patrza na PODAZ. Nawet "zdjete
ogloszenie" jest poszlaka po stronie sprzedajacego. Nie dalo sie odrozniac
dwoch zupelnie roznych diagnoz: "rower stoi, bo jest za drogi" (ludzie
wchodza i nie dzwonia) od "rower stoi, bo nikt go nie oglada" (zly tytul,
zle zdjecia, zla kategoria).

SKAD TA LICZBA. Strona oferty pokazuje publicznie licznik:
    <span data-testid="page-view-counter">Wyswietlenia: 329</span>
W HTML z serwera go NIE MA - dokleja go JavaScript po doscrollowaniu do
stopki. Dlatego wczesniej tego samego dnia zapisalem w repo blednie, ze OLX
nie pokazuje licznika nikomu poza wlascicielem ogloszenia; grep po zrodle
znajdowal same szablony tlumaczen. Podglad ruchu sieciowego przegladarki
pokazal jedno zapytanie:

    POST https://production-graphql.eu-sharedservices.olxcdn.com/graphql
    {"operationName":"PageViews","variables":{"adId":"1100315214"},
     "query":"query PageViews($adId: String!) { myAds { pageViews(adId: $adId)
              { pageViews } } }"}

Nazwa `myAds` myli: zapytanie dziala dla CUDZEGO ogloszenia i bez logowania,
bo dokladnie tak pyta strona otwarta przez anonimowego goscia.

SPRAWDZONE 16.11.2026 I ZAMKNIETE: TA DROGA NIE DZIALA. Endpoint odpowiada
HTTP 200, ale trescia:

    {"errors":[{"message":"Authorization header is missing",
                "path":["myAds"],"extensions":{"code":"UNAUTHENTICATED"}}]}

Tak samo przy golym zapytaniu, z `origin` i `referer`, z pelnym zestawem
naglowkow przegladarki i z naglowkami Apollo - cztery warianty, ta sama
odpowiedz. Przegladarka dostaje liczbe dlatego, ze aplikacja OLX-a dokłada
token, ktory ma nawet gosc niezalogowany.

DLACZEGO NIE SIEGAMY PO TEN TOKEN. Licznik na stronie jest publiczny, ale API
za nim juz nie - podszywanie sie pod klienta webowego serwisu to co innego niz
pobranie publicznej strony. Zostaje droga, ktora czyta te liczbe tak, jak
czyta ja czlowiek: wyrenderowanie strony oferty. To jest kosztowniejsze
(przegladarka zamiast jednego POST-a) i dlatego ma sens wylacznie na PROBCE,
czyli dokladnie tak, jak ten plik zaprojektowano.

STAN PLIKU: `czytaj_wyswietlenia` zostaje jako zapis tego, co zostalo
sprawdzone, i zawsze odda None. Caly pomiar (probka po pasmach, wiek, na dobe,
dziennik, raport) jest gotowy i czeka na czytnik, ktory faktycznie oddaje
liczbe - podaje sie go przez parametr `czytnik` w `zmierz()`.

CO Z TEGO LICZYMY. Sama liczba wyswietlen nic nie mowi, bo rosnie z wiekiem
ogloszenia. Miara jest WYSWIETLENIA NA DOBE ZYCIA, porownywalna miedzy
ofertami roznego wieku, i dopiero jej mediana w pasmie cenowym jest
odpowiedzia na pytanie "czy na ten przedzial ktos w ogole patrzy".
"""
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path

import requests

WYSWIETLENIA_FILE = Path("wyswietlenia.jsonl")
POLKA_STAN = Path("polka_stan.json")

GRAPHQL = "https://production-graphql.eu-sharedservices.olxcdn.com/graphql"
ZAPYTANIE = ("query PageViews($adId: String!) {\n  myAds {\n"
             "    pageViews(adId: $adId) {\n      pageViews\n    }\n  }\n}")

# Ile ofert odpytujemy na przebieg. 40 to kompromis: tyle wystarcza na mediane
# w kazdym z osmiu pasm przy kilku dniach zbierania, a jednoczesnie jest to
# ulamek ruchu, ktory polka i tak generuje codziennie (osiem zapytan listowych
# po 40 rekordow). Nie chodzi o zmierzenie wszystkich 3800 ofert - do wskaznika
# wystarczy probka powtarzana w czasie.
PROBKA_NA_PRZEBIEG = 40

# Pasma z przeplywu, zeby obie miary mowily o tych samych polkach.
PASMA = [(8000, 9000), (9000, 10000), (10000, 12000), (12000, 14000),
         (14000, 16000), (16000, 20000), (20000, 25000), (25000, None)]


def _pasmo(cena):
    if not isinstance(cena, int):
        return None
    for lo, hi in PASMA:
        if lo <= cena < (hi if hi is not None else 10 ** 9):
            return lo
    return None


def z_odpowiedzi(dane):
    """Liczba wyswietlen z odpowiedzi GraphQL albo None.

    None znaczy "nie wiem" i nigdy nie jest zamieniane na zero: zero
    wyswietlen to mocne twierdzenie o ogloszeniu, a nieudany odczyt nie
    jest zadnym twierdzeniem."""
    try:
        n = dane["data"]["myAds"]["pageViews"]["pageViews"]
    except (TypeError, KeyError):
        return None
    return n if isinstance(n, int) and n >= 0 else None


def czytaj_wyswietlenia(ad_id, sesja=None, timeout=15):
    """Jedno zapytanie o licznik. None przy kazdym niepowodzeniu."""
    klient = sesja or requests
    try:
        r = klient.post(GRAPHQL, timeout=timeout, json={
            "operationName": "PageViews",
            "variables": {"adId": str(ad_id)},
            "query": ZAPYTANIE})
        if r.status_code != 200:
            return None
        return z_odpowiedzi(r.json())
    except Exception:
        return None


def wiek_dni(wystawiono, teraz):
    """Ile dob ogloszenie zyje. None, gdy nie wiadomo od kiedy."""
    if not wystawiono:
        return None
    try:
        a = datetime.fromisoformat(str(wystawiono).replace("Z", "+00:00"))
        b = datetime.fromisoformat(str(teraz).replace("Z", "+00:00"))
    except ValueError:
        return None
    if a.tzinfo:
        a = a.replace(tzinfo=None)
    if b.tzinfo:
        b = b.replace(tzinfo=None)
    return max((b - a).days, 0)


def na_dobe(wyswietlen, wiek):
    """Wyswietlenia na dobe zycia. None, gdy wieku nie znamy.

    Wiek 0 (ogloszenie z dzisiaj) liczy sie jako jedna doba, a nie jako
    dzielenie przez zero - inaczej kazda swieza oferta wypadalaby z pomiaru
    albo dawala nieskonczonosc."""
    if not isinstance(wyswietlen, int) or wiek is None:
        return None
    return round(wyswietlen / max(wiek, 1), 1)


def probka(stan, ile=PROBKA_NA_PRZEBIEG, juz_zmierzone=()):
    """Ktore oferty odpytac. Rownomiernie po pasmach, nie "pierwsze z brzegu".

    Probka wzieta z czola slownika bylaby probka JEDNEGO pasma albo jednego
    sprzedawcy - a pytanie brzmi, jak popyt rozklada sie MIEDZY pasmami.
    Dlatego budzet dzieli sie po rowno, a pasmo, ktore nie wykorzysta swojego,
    oddaje reszte pozostalym.

    Pomijamy oferty bez daty wystawienia (nie ma jak policzyc wieku, wiec
    odczyt bylby nieporownywalny) i te juz zmierzone w tym przebiegu."""
    wg_pasm = {}
    for oid, rec in (stan or {}).items():
        if oid in juz_zmierzone or not isinstance(rec, dict):
            continue
        if not rec.get("wystawiono") or not rec.get("url"):
            continue
        p = _pasmo(rec.get("p"))
        if p is None:
            continue
        wg_pasm.setdefault(p, []).append(oid)
    for lst in wg_pasm.values():
        lst.sort()                      # powtarzalnosc: ta sama probka z tego
                                        # samego stanu, zeby dalo sie odtworzyc
    wybor, pasma = [], sorted(wg_pasm)
    if not pasma:
        return []
    na_pasmo = max(ile // len(pasma), 1)
    for p in pasma:
        wybor += wg_pasm[p][:na_pasmo]
    if len(wybor) < ile:                # niewykorzystany budzet idzie dalej
        for p in pasma:
            for oid in wg_pasm[p][na_pasmo:]:
                if len(wybor) >= ile:
                    break
                wybor.append(oid)
    return wybor[:ile]


def zmierz(stan=None, ile=PROBKA_NA_PRZEBIEG, teraz=None, czytnik=None):
    """Przechodzi probke i zwraca rekordy do dziennika. Bez zapisu."""
    teraz = teraz or datetime.now().strftime("%Y-%m-%dT%H:%M")
    if stan is None:
        stan = (json.loads(POLKA_STAN.read_text(encoding="utf-8"))
                if POLKA_STAN.exists() else {})
    czytnik = czytnik or czytaj_wyswietlenia
    out = []
    for oid in probka(stan, ile):
        rec = stan[oid]
        n = czytnik(oid)
        w = wiek_dni(rec.get("wystawiono"), teraz)
        out.append({"ts": teraz, "id": oid, "p": rec.get("p"),
                    "firma": bool(rec.get("firma")), "pasmo": _pasmo(rec.get("p")),
                    "wiek_dni": w, "wyswietlen": n, "na_dobe": na_dobe(n, w)})
    return out


def zapisz(rekordy, plik=None):
    """Dopisuje do dziennika. Jak kazdy dziennik w tym repo: tylko dopisujemy."""
    if not rekordy:
        return 0
    plik = plik or WYSWIETLENIA_FILE
    with plik.open("a", encoding="utf-8") as f:
        for r in rekordy:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(rekordy)


# Ponizej tylu odczytow mediana jest loteria i zamiast niej idzie "nie wiem".
MIN_ODCZYTOW = 10


def podsumuj(rekordy):
    """Mediana wyswietlen na dobe: calosc, pasma, prywatne vs firmy."""
    dobre = [r for r in rekordy if isinstance(r.get("na_dobe"), (int, float))]
    if len(dobre) < MIN_ODCZYTOW:
        return {"n": len(dobre), "mediana": None, "pasma": {}, "prywatne": None,
                "firmy": None, "bez_odczytu": len(rekordy) - len(dobre)}
    med = lambda v: (round(statistics.median(v), 1)
                     if len(v) >= MIN_ODCZYTOW else None)
    pasma = {}
    for lo, _ in PASMA:
        w = [r["na_dobe"] for r in dobre if r.get("pasmo") == lo]
        if len(w) >= MIN_ODCZYTOW:
            pasma[str(lo)] = {"n": len(w), "mediana": med(w)}
    return {"n": len(dobre),
            "mediana": med([r["na_dobe"] for r in dobre]),
            "pasma": pasma,
            "prywatne": med([r["na_dobe"] for r in dobre if not r.get("firma")]),
            "firmy": med([r["na_dobe"] for r in dobre if r.get("firma")]),
            "bez_odczytu": len(rekordy) - len(dobre)}


def wczytaj(plik=None):
    out = []
    try:
        for linia in (plik or WYSWIETLENIA_FILE).read_text(
                encoding="utf-8").splitlines():
            if linia.strip():
                try:
                    out.append(json.loads(linia))
                except json.JSONDecodeError:
                    continue
    except FileNotFoundError:
        pass
    return out


def raport() -> str:
    d = podsumuj(wczytaj())
    if d["mediana"] is None:
        return ("👁 <b>Wyswietlenia ofert</b>\n"
                f"Za malo odczytow na wniosek ({d['n']}, potrzeba "
                f"{MIN_ODCZYTOW}). Uruchom <code>python3 wyswietlenia.py</code>.")
    L = ["👁 <b>Wyswietlenia ofert - ile osob to oglada</b>",
         f"<i>{d['n']} odczytow z dziennika</i>", "",
         f"Mediana: <b>{d['mediana']}</b> wyswietlen na dobe zycia ogloszenia"]
    if d["prywatne"] is not None and d["firmy"] is not None:
        L.append(f"· prywatne {d['prywatne']} · firmy {d['firmy']}")
    if d["pasma"]:
        L.append("")
        L.append("<b>Wg pasma cenowego</b>")
        for lo, w in sorted(d["pasma"].items(), key=lambda x: int(x[0])):
            L.append(f"· od {int(lo) // 1000}k: <b>{w['mediana']}</b> "
                     f"na dobe (n={w['n']})")
    if d["bez_odczytu"]:
        L.append(f"\n<i>ⓘ {d['bez_odczytu']} prob bez odczytu - to NIE sa "
                 f"zera wyswietlen, tylko brak odpowiedzi.</i>")
    L.append("<i>ⓘ Licznik rosnie z wiekiem ogloszenia, wiec porownywalna jest "
             "dopiero liczba NA DOBE zycia, nie sama suma.</i>")
    return "\n".join(L)


if __name__ == "__main__":
    if "--raport" in sys.argv:
        import re as _re
        print(_re.sub(r"</?(b|i|code)>", "", raport()))
    elif "--sprawdz" in sys.argv:
        # Jedno ogloszenie, zeby zobaczyc, czy endpoint w ogole odpowiada
        # spoza przegladarki. To jest ta niesprawdzona rzecz z naglowka pliku.
        ad = sys.argv[sys.argv.index("--sprawdz") + 1]
        print(json.dumps({"id": ad, "wyswietlen": czytaj_wyswietlenia(ad)},
                         ensure_ascii=False))
    else:
        rek = zmierz()
        n = zapisz(rek)
        udane = sum(1 for r in rek if r.get("wyswietlen") is not None)
        print(json.dumps({"ok": True, "zapisane": n, "z_odczytem": udane,
                          "bez_odczytu": n - udane}, ensure_ascii=False))
