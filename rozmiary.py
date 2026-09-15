#!/usr/bin/env python3
"""Przeglądanie wysłanych ofert po ROZMIARZE RAMY. Nic nie pobiera.

SKĄD TO SIĘ WZIĘŁO: właściciel jedzie po rower konkretnego rozmiaru i chce
w czacie przejrzeć "tylko najnowsze L", innego dnia "tylko M". Kanał sypie
wszystkim naraz (40-60 powiadomień dziennie), a rozmiar decyduje o zbycie
w Polsce mocniej niż cena - na L kupca szuka się tygodniami, XS potrafi nie
znaleźć go wcale.

CZYTAMY WYŁĄCZNIE `seen.json` i `de_stan.json`, oba do odczytu. Zero żądań do
Kleinanzeigen - ta sama zasada co w `najlepsze.py` i z tego samego powodu:
dławienie jest per adres IP i zmierzone, a komenda z telefonu nie ma prawa
zjeść czasu reakcji, czyli jedynej przewagi tego bota.

POKAZUJEMY TO, CO BOT JUŻ WYSŁAŁ. Wpis w `seen.json` ma `score` tylko wtedy,
gdy przeszedł pełną ocenę i poszedł na Telegram (`tracker.main`). Odrzuty mają
`powod`, nieudane odczyty `nieodczytane` - jednych i drugich tu nie ma, bo
właściciel ich nigdy nie widział, więc nie ma czego "przeglądać".

NIE ZGADUJEMY ROZMIARU. Oferta bez czytelnego rozmiaru ląduje na liście razem
z pewnymi trafieniami - właściciel 15.09.2026: "lepiej kilka wiecej
przegladnac niz ominac". Odsiewamy WYŁĄCZNIE te, o których wiemy, że są innym
rozmiarem. Rozmiar w centymetrach zostaje w grupie "bez info" świadomie: ten
sam numer znaczy co innego u Cube'a i u Specialized (ta sama decyzja co
w `najlepsze.py`), ale wypisujemy go, żeby właściciel mógł ocenić sam.

Uruchom: TELEGRAM_BOT_TOKEN=dummy TELEGRAM_CHAT_ID=0 python3 rozmiary.py L 3
"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import tracker as T

SEEN = Path("seen.json")
DE_STAN = Path("de_stan.json")

# Rozmiary, które w ogóle mogą paść. XL i XXL są tu dla porządku, ale w puli
# jest ich garść: `is_junk` odrzuca "xl" w TYTULE (to filtr na za duże ramy),
# więc dochodzą tylko te, które mają rozmiar wyłącznie w opisie.
LITERY = ("XS", "S", "M", "L", "XL", "XXL")
DNI_DOMYSLNIE = 3
DNI_MAX = 30


def _wczytaj(sciezka):
    """(słownik, czy_udalo_sie). Awaria pliku nie ma prawa wywrócić komendy,
    ale NIE WOLNO jej pokazać jako "brak ofert" - to reguła 7. Pusty wynik
    z nieczytelnego `seen.json` wygląda dokładnie tak samo jak spokojny rynek,
    a znaczy coś zupełnie innego."""
    try:
        return json.loads(sciezka.read_text(encoding="utf-8")), True
    except Exception:
        return {}, False


def zyje(ad_id, stan_de):
    """True / False / None. None znaczy 'dozorca jeszcze nie sprawdził'.

    Ta sama wiedza i to samo źródło co w `/dojrzale`. Świeże ogłoszenia będą
    tu prawie zawsze None - dozorca objeżdża zbiór po 25 sztuk co 2 h - i tak
    ma być: nie udajemy wiedzy, której nie mamy."""
    rec = stan_de.get(str(ad_id))
    if not rec:
        return None
    if rec.get("zdjete"):
        return False
    return True if rec.get("ostatni_zywy") else None


def oferty(dni=DNI_DOMYSLNIE, seen=None, dzis=None):
    """(oferty z ostatnich `dni` dni OD NAJNOWSZEJ, czy plik dał się wczytać).

    `seen.json` nie zapisuje godziny, tylko datę. Kolejność w obrębie dnia
    bierzemy więc z kolejności wpisów w pliku - bot dopisuje je w miarę
    znajdowania, więc to jest realna kolejność odkrycia, a nie zgadywanka."""
    czytelny = True
    if seen is None:
        seen, czytelny = _wczytaj(SEEN)
    dzis = dzis or date.today()
    granica = (dzis - timedelta(days=max(1, dni) - 1)).isoformat()
    out = []
    for kolejnosc, (ad_id, w) in enumerate(seen.items()):
        if not isinstance(w, dict) or w.get("score") is None:
            continue                       # odrzut albo nieudany odczyt
        if not w.get("title") or not w.get("url"):
            continue
        if (w.get("date") or "") < granica:
            continue
        out.append({
            "id": ad_id,
            "tytul": w["title"],
            "url": w["url"],
            "cena": w.get("price"),
            "cena_num": w.get("price_num"),
            "przebieg": w.get("mileage") if w.get("mileage") != "brak danych" else None,
            "rocznik": w.get("year"),
            "zysk": w.get("profit"),
            "wh": w.get("wh"),
            "loc": w.get("loc"),
            "data": w.get("date"),
            # Rozmiar liczony JEDNYM czytnikiem z tracker.py - tym samym,
            # który wpisał `rama` do pliku. Druga kopia reguły rozjechałaby
            # się z pierwszą po pierwszej poprawce.
            "rama": T.rama_oferty(w),
            "litera": T.litera_ramy(w),
            "kolejnosc": kolejnosc,
        })
    out.sort(key=lambda o: (o["data"] or "", o["kolejnosc"]), reverse=True)
    return out, czytelny


def licznik(lista):
    """{'L': 4, ..., 'cm': 1, 'brak': 61} - z czego składa się okno."""
    c = {l: 0 for l in LITERY}
    c["cm"], c["brak"] = 0, 0
    for o in lista:
        if o["litera"]:
            c[o["litera"]] += 1
        elif o["rama"]:
            c["cm"] += 1                   # "53 cm" - wiemy coś, ale nie literę
        else:
            c["brak"] += 1
    return c


def podziel(lista, litera):
    """(pewne, bez_info, odsiane) dla jednego rozmiaru.

    To jest cała reguła tej funkcjonalności i dlatego stoi w jednym miejscu:
    na liście ląduje rower o TYM rozmiarze albo o ŻADNYM znanym. Rower
    o znanym innym rozmiarze nie ma jak przejść."""
    pewne = [o for o in lista if o["litera"] == litera]
    bez_info = [o for o in lista if o["litera"] is None]
    odsiane = [o for o in lista if o["litera"] and o["litera"] != litera]
    return pewne, bez_info, odsiane


def zbierz(litera=None, dni=DNI_DOMYSLNIE, seen=None, stan_de=None, dzis=None):
    """Komplet do wyświetlenia. `litera=None` to przegląd wszystkich rozmiarów.

    Zdjęte ogłoszenia wypadają z listy, ale ZOSTAJĄ POLICZONE - właściciel ma
    wiedzieć, ile rowerów już przepadło, a nie oglądać po cichu krótszą listę.
    """
    dni = max(1, min(DNI_MAX, int(dni or DNI_DOMYSLNIE)))
    lista, czytelny = oferty(dni=dni, seen=seen, dzis=dzis)
    if stan_de is None:
        stan_de, _ = _wczytaj(DE_STAN)     # brak dozorcy to tylko brak wiedzy
    zywe, zdjete = [], 0
    for o in lista:
        stan = zyje(o["id"], stan_de)
        if stan is False:
            zdjete += 1
            continue
        o["zyje"] = stan
        zywe.append(o)
    c = licznik(zywe)
    wynik = {
        "litera": litera,
        "dni": dni,
        "awaria": None if czytelny else "seen.json",
        "w_oknie": len(zywe),
        "zdjete": zdjete,
        "licznik": c,
        "z_rozmiarem": sum(c[l] for l in LITERY),
    }
    if litera:
        pewne, bez_info, odsiane = podziel(zywe, litera)
        wynik.update(pewne=pewne, bez_info=bez_info, odsiane=len(odsiane))
    return wynik


if __name__ == "__main__":
    arg_litera = None
    arg_dni = DNI_DOMYSLNIE
    for a in sys.argv[1:]:
        if a.isdigit():
            arg_dni = int(a)
        elif a.upper() in LITERY:
            arg_litera = a.upper()
    w = zbierz(arg_litera, arg_dni)
    c = w["licznik"]
    print(f"OKNO: {w['dni']} dni, {w['w_oknie']} wyslanych ofert "
          f"({w['zdjete']} juz zdjetych pominietych)")
    print("ROZMIARY: " + "  ".join(f"{l}={c[l]}" for l in LITERY)
          + f"  cm={c['cm']}  bez_info={c['brak']}")
    if not arg_litera:
        print("\nPodaj rozmiar (L / M / S ...), zeby zobaczyc liste.")
        sys.exit(0)
    print(f"\nPEWNE {arg_litera}: {len(w['pewne'])}   "
          f"BEZ INFO: {len(w['bez_info'])}   ODSIANE: {w['odsiane']}\n")
    for grupa, oznaczenie in ((w["pewne"], f"[{arg_litera}]"), (w["bez_info"], "[ ? ]")):
        for o in grupa:
            rama = o["rama"] or "rozmiar nie podany"
            print(f"{oznaczenie} {o['tytul'][:70]}")
            print(f"      {rama} | {o['cena']} | {o['przebieg'] or 'przebieg ?'} "
                  f"| {o['rocznik'] or '?'} | {o['data']}")
            print(f"      {o['url']}")
        print()
