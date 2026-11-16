"""Przelicza rodziny_modeli.json - KLUCZ WYCENY dla zwyklych modeli.

Brat `sprawdz_modele.py`, tylko z odwrotna robota. Tamten szuka SZCZYTU
kazdej marki na kanal najlepszych. Ten szuka WSZYSTKICH rodzin, zeby zwykly
rower mial z czym sie porownac.

    python sprawdz_rodziny.py            # pokaz pokrycie i roznice
    python sprawdz_rodziny.py --odrzuty  # pokaz, co i czemu wypadlo
    python sprawdz_rodziny.py --zapisz   # przepisz rodziny_modeli.json

Kod wyjscia 1, gdy przeliczenie rozni sie od pliku - zeby zadanie cykliczne
mialo na czym sie zatrzymac.

=== PO CO TO POWSTALO (zmierzone 12.11.2026) ==============================

`olx_query_for` mial 13 wzorcow pisanych z reki (Cube Stereo, Trek Rail
i Powerfly, Specialized Levo i Kenevo, Scott Strike, Patron i Genius, KTM
Macina, Canyon). Wszystko poza nimi spadalo na `fallback`, czyli na NAZWE
KANALU - a to znaczy, ze rower byl wyceniany wzgledem WSZYSTKICH elektrykow
naraz. Dokladnie ta pomylka, ktora 17.09.2026 opisano przy S-Works
("najdrozsze rowery na rynku wycenial wzgledem wszystkich elektrykow").

Pomiar na rowerach z polek (regula 5: rowery, nie ogloszenia), okno od
market.jsonl do dzis:

    w pasmie 800-3000 EUR          88 295 rowerow
    bez klucza wyceny             72 568 (82%)
    z nich MA rozpoznawalna marke  ~52% - brakuje samego MODELU

Ten plik dokleja rodziny ZA wzorcami z reki, nigdy przed. Wzorzec z reki ma
komentarz i pomiar przy sobie (pulapka ONE22/44/55/77), wiec zostaje
pierwszy. Rodzina wchodzi tylko tam, gdzie dzis nie ma NIC.

=== CZEGO TU NIE MA ======================================================

Pieter, werdyktow o silniku i progu fully. To robi `sprawdz_modele.py`
i jego plik. Tu nie ma ANI JEDNEJ decyzji o kupowaniu - tylko odpowiedz na
pytanie "z czym porownac ten rower".
"""
import argparse
import collections
import html
import json
import re
import statistics as st
import sys
from datetime import date

import sprawdz_modele as SM
import tracker as T

# Ten sam prog co ROZRZUT_MIN_ROWEROW w tracker.py i co MIN_ROWEROW
# w sprawdz_modele.py. Nie wymyslam trzeciego.
MIN_ROWEROW = SM.MIN_ROWEROW

# SLOWO POSPOLITE UDAJE MODEL U KAZDEJ MARKI NARAZ. "Elektrofahrrad" wyszlo
# jako rodzina u DZIEWIECIU marek, "schwarz" (czarny, czyli KOLOR) u szesciu,
# "tiefeinsteiger" (rama z niskim przekrokiem) u szesciu, "ladegerät"
# (ladowarka!) u czterech. Zaden nie jest modelem czegokolwiek.
# Prog trzech marek, bo dwie moga byc przypadkiem - Cube i KTM naprawde maja
# rodziny o tej samej nazwie - a trzy to juz slowo pospolite.
MIN_MAREK_OGOLNEGO = 3

# DRUGI CZLON NAZWY MARKI. Po "riese" stoi "& Müller", wiec tokenizer robil
# z tej marki rodzine "riese müller" o 623 rowerach - czyli klucz "porownaj
# z cala marka", ktory jest dokladnie tym, czemu ten plik ma zapobiegac.
# Spojniki tez: bez nich wychodzilo "riese &amp" (1 494 rowery).
SPOJNIKI_I_MARKI = {"müller", "mueller", "&", "+", "und"}

# JEDNOSTKA PRZYLEPIONA DO LICZBY. Odsiew brata wyrzuca gole "29" i gole
# "zoll", ale nie "29zoll" - a sprzedawcy pisza to lacznie. Klucz "cube
# 29zoll" zlapal 617 rowerow w pasmie cen, czyli kola zamiast modelu.
# "rh60" i "gr52" to rozmiar ramy, "720wh" bateria, "85nm" moment silnika.
JEDNOSTKA = re.compile(
    r"^(?:\d+[.,]?\d*(?:zoll|wh|ah|v|nm|cm|mm|kg|km|gang|zahn|watt|w)"
    r"|(?:rh|gr|rg)\s*\d+)$", re.I)

# SLOWA POSPOLITE, KTORYCH NIE ZLAPALA REGULA TRZECH MAREK - bo siedza
# u jednej. Lista RECZNIE SPRAWDZONA, jak LITEROWKI_NAZW w tracker.py, i tak
# samo zbudowana z pomiaru: w nawiasie rowery w rodzinie, ktora dane slowo
# tworzylo. Wszystkie opisuja WYPOSAZENIE albo WYMIAR, nie model.
SLOWA_WYPOSAZENIA = {
    "damenfahrrad",     # (25) rower damski
    "körpergröße",      # (28) wzrost rowerzysty
    "koerpergroesse",
    "rahmenhöhe",       # (13) wysokosc ramy
    "rahmenhoehe",
    "tiefeinstieg",     # (26) rama z niskim przekrokiem
    "nabenschaltung",   # (14) przerzutka w piascie
    "riemenantrieb",    # (12) naped pasowy
    "rücktrittbremse",  # (36) hamulec torpedo, w dwoch pisowniach
    "rücktritbremse",
    "ruecktrittbremse",
    "speeddrive",       # (16) przelozenie Schlumpfa, nie model
    "suspension",       # (24) zawieszenie
    "generation",       # (16) "next generation" to nie rodzina
}

# KONCOWKI WERSJI - LISTA RECZNIE SPRAWDZONA, NIE LICZONA.
#
# Probowalem dwoch automatow i oba sie wywrocily, wiec zapisuje je tu razem
# z powodem, zeby nikt (ja za miesiac wlacznie) nie probowal trzeci raz:
#
# 1. UDZIAL GOLEJ NAZWY (gole / gole+zlozone). "cube pro" ma 5%, bo prawie
#    zawsze stoi w "reaction pro". Ale Haibike FullSeven ma 41% i HardSeven
#    47%, bo chodza jako "SDuro FullSeven" - a to PRAWDZIWE rodziny. Prog
#    rozdzielajacy jedno od drugiego nie istnieje.
# 2. LICZBA ROZNYCH GLOW ("pro" stoi po czterech rodzinach, "vado" po jednej).
#    Przepuszcza "canyon on" (po samym "pathlite"), "cannondale neo"
#    i "specialized works" - a to koncowki. Odrzuca "conway suv"
#    i "moustache trail" - a to rodziny.
#
# Wzorem jest LITEROWKI_NAZW w tracker.py: tam tez automat (odleglosc
# edycyjna) zostal odrzucony na rzecz listy sprawdzonej okiem.
#
# Liczba w nawiasie to rowery, ktore ta pozycja odbiera - cena odsiewu.
KONCOWKI_WERSJI = {
    "bulls":       {"evo"},          # (126) Lacuba EVO, Six50 EVO, Twenty9 EVO
    "cannondale":  {"neo"},          # (16)  Mavaro Neo, Quick Neo
    "canyon":      {"on"},           # (26)  Strive:ON, Torque:ON, Spectral:ON
    "cube":        {"pro", "one", "race", "allroad", "hpa", "actionteam"},
    "flyer":       {"evo"},          # (17)
    "giant":       {"e+"},           # (12)  Trance E+, Stance E+, Explore E+
    "haibike":     {"low", "x3"},    # (45)  "Low" to rama, nie model
    "kalkhoff":    {"evo"},          # (17)  Impulse Evo to NAPED
    "ktm":         {"race", "sport"},
    "scott":       {"eride", "ride"},
    "specialized": {"works", "sl"},  # (108) "works" to uciete S-Works
}


# SLOWA POSPOLITE LICZONE W PIERWSZYM PRZEBIEGU, nie wpisane z reki.
# Zasada: slowo, ktore udaje model u trzech roznych marek, nie jest
# modelem zadnej z nich - i to NA ZADNEJ POZYCJI. Bez drugiego przebiegu
# zostawalo "haibike alltrail koerpergroesse" (15 rowerow), czyli rodzina
# AllTrail rozbita na dwie grupy przez slowo "wzrost".
_OGOLNE = set()


def czy_smiec(tok: str) -> bool:
    """Odsiew `sprawdz_modele.py` plus to, czego klucz wyceny nie znosi."""
    if SM.czy_smiec(tok):
        return True
    # AMPERSAND PRZYLEPIONY DO SLOWA. "Riese&amp;Müller" po odkodowaniu
    # encji nie ma spacji, wiec tokenem jest "&müller" - i tak wychodzilo
    # 180 rowerow w rodzinie "riese &müller". PLUSA NIE OBCINAM: "powerfly+"
    # i "powerfly" to dwa rozne pietra cenowe (patrz wzorzec() w bracie).
    t = tok.strip('.,;:!?-/()"\'&').lower()
    if not t:
        return True                     # sam "&" albo sam nawias
    return (t in SPOJNIKI_I_MARKI or t in _OGOLNE
            or t in SLOWA_WYPOSAZENIA or bool(JEDNOSTKA.match(t)))


def czy_nazwa_pusta(marka: str, model: str, ile_marek) -> str:
    """Czemu ta nazwa NIE jest rodzina - albo "" , gdy jest."""
    czlony = model.split()
    pierwszy = czlony[0]
    if len(czlony) == 1 and ile_marek.get(model, 0) >= MIN_MAREK_OGOLNEGO:
        return f"slowo pospolite u {ile_marek[model]} marek"
    if pierwszy in SM.MARKI and pierwszy != marka:
        # "cube ktm" to dwie marki w jednym tytule, nie model Cube'a.
        return f"pierwszy czlon to inna marka ({pierwszy})"
    if model in KONCOWKI_WERSJI.get(marka, ()):
        return "koncowka wersji, nie rodzina (lista reczna)"
    if "&" in model or ";" in model:
        return "resztka encji HTML"
    if re.fullmatch(r"[\d.,]+", model):
        # "cube 120" i "haibike 5.0" to NUMER w rodzinie, nie rodzina. Gola
        # liczba lapie tez pojemnosci i rozmiary, ktorych odsiew nie dogonil.
        return "sama liczba, bez nazwy"
    if len(czlony) == 1 and len(pierwszy) == 1:
        # JEDNA LITERA TO NIE NAZWA. Wzorzec "c" z luka 24 znakow trafia
        # w litere z dowolnego innego slowa - ta sama pulapka, ktora przy
        # "Levo R" dala 596 dopasowan zamiast 51 (patrz wzorzec(), punkt 5).
        return "jednoliterowa nazwa"
    return ""


def _slowa_pospolite(elek):
    """Jednoczlonowe nazwy, ktore wyszly jako rodzina u >= MIN_MAREK_OGOLNEGO
    marek. Liczone w pierwszym przebiegu, bo tu nie ma jeszcze czego odjac.
    """
    widziane = collections.defaultdict(set)
    for marka in SM.MARKI:
        c, _ = SM.frazy(elek, marka, smiec=czy_smiec, odkoduj=True)
        for f, n in c.items():
            if n >= MIN_ROWEROW and " " not in f:
                widziane[f].add(marka)
    return {f for f, mk in widziane.items() if len(mk) >= MIN_MAREK_OGOLNEGO}


def zbierz(elek):
    """({marka: [wpis]}, odrzuty) - rodziny, ktore cos znacza.

    DWA PRZEBIEGI. Pierwszy znajduje slowa pospolite (patrz `_OGOLNE`),
    drugi liczy rodziny juz bez nich. Kolejnosc jest konieczna: w pierwszym
    przebiegu nie wiadomo jeszcze, ktore slowo jest pospolite.
    """
    global _OGOLNE
    _OGOLNE = set()
    _OGOLNE = _slowa_pospolite(elek)
    surowe = []
    for marka in SM.MARKI:
        wsz = [r for r in elek if re.search(r"\b" + re.escape(marka) + r"\b",
                                            r["t"], re.I)]
        if len(wsz) < MIN_ROWEROW:
            continue
        med_marki = st.median([r["p"] for r in wsz])
        c, dane = SM.frazy(elek, marka, smiec=czy_smiec, odkoduj=True)
        for f, n in c.items():
            if n >= MIN_ROWEROW:
                surowe.append({"marka": marka, "model": f, "rr": dane[f], "n": n,
                               "med": st.median([r["p"] for r in dane[f]]),
                               "med_marki": med_marki})
    # Ta sama droga, ktora `sprawdz_modele.py` sciaga nazwy tego samego roweru
    # do jednej - razem z weto na numer modelu i na uciety numer.
    zostaw = SM.odsiej_prefiksy(surowe)

    ile_marek = collections.Counter()
    widziane = collections.defaultdict(set)
    for w in zostaw:
        if " " not in w["model"]:
            widziane[w["model"]].add(w["marka"])
    for model, marki in widziane.items():
        ile_marek[model] = len(marki)

    out, odrzuty = {}, []
    for w in zostaw:
        czemu = czy_nazwa_pusta(w["marka"], w["model"], ile_marek)
        if czemu:
            odrzuty.append((w["marka"], w["model"], w["n"], czemu))
            continue
        out.setdefault(w["marka"], []).append(
            {"model": w["model"], "rowerow": w["n"], "mediana": int(w["med"]),
             "wz": SM.wzorzec(w["model"])})

    # KOLEJNOSC MA ZNACZENIE, ta sama zasada co w load_topowe: najpierw nazwa
    # najbardziej szczegolowa. Inaczej "reaction" wygralo by z "reaction slx"
    # i topowa wersja dostala by klucz podstawowej.
    # NUMER W NAZWIE WYGRYWA Z KAZDA INNA NAZWA, i to z tego samego powodu,
    # z ktorego `_dodatek_liczbowy` w bracie nie pozwala numeru pochlonac:
    # numer to cala roznica miedzy pietrami. Bez tego "Riese & Müller
    # Charger3 GT vario" dostawalo klucz "riese charger vario" (bo dluzsza
    # nazwa) zamiast "riese charger3" - a Charger3 i Charger5 to dwa rozne
    # rowery w dwoch roznych cenach.
    for lista in out.values():
        lista.sort(key=lambda w: (0 if re.search(r"\d", w["model"]) else 1,
                                  -len(w["model"].split()), -len(w["model"]),
                                  -w["rowerow"], w["model"]))
    return out, sorted(odrzuty, key=lambda x: -x[2])


def marki_po_kolei(tytul, marki):
    """Marki z tytulu, ta z poczatku pierwsza.

    Ta sama decyzja co `marka_roweru` w najlepsze.py ("ta pierwsza
    w tytule"): sprzedawca pisze najpierw marke roweru, a dopiero potem
    marke silnika, bagaznika albo drugiego roweru, ktory tez ma na sprzedaz.
    """
    trafione = []
    for marka in marki:
        m = re.search(r"\b" + re.escape(marka) + r"\b", tytul, re.I)
        if m:
            trafione.append((m.start(), marka))
    return [m for _, m in sorted(trafione)]


def pokrycie(elek, rodziny):
    """(w_pasmie, bez_klucza_bez_pliku, zyska_klucz).

    "BEZ KLUCZA" LICZONE Z WYLACZONYM PLIKIEM, i to jest cala subtelnosc tej
    funkcji. Przy pierwszym podejsciu liczylem to zywym `klucz_grupy`, wiec
    po zapisaniu pliku ta sama miara pokazywala 52% zamiast 82% - liczba
    w naglowku pliku zaczynala opisywac SIEBIE, a nie stan przed zmiana.
    """
    import najlepsze as N
    top = N.load_topowe()
    w_pasie = [r for r in elek if T.MIN_PRICE <= r["p"] <= T.MAX_PRICE]
    zapas = T._rodziny_cache
    T._rodziny_cache = {}               # pusty slownik = czytnik milczy
    try:
        bez = [r for r in w_pasie if not N.klucz_grupy(r["t"], top)]
    finally:
        T._rodziny_cache = zapas
    skomp = {m: [re.compile(w["wz"], re.I) for w in lista]
             for m, lista in rodziny.items()}
    zlapane = 0
    for r in bez:
        t = html.unescape(r["t"])
        for marka in marki_po_kolei(t, rodziny):
            koniec = re.search(r"\b" + re.escape(marka) + r"\b", t, re.I).end()
            if any(wz.search(t, koniec) for wz in skomp[marka]):
                zlapane += 1
                break
    return len(w_pasie), len(bez), zlapane


def main(zapisz=False, gadaj=False):
    elek = SM.wczytaj()
    daty = sorted(r["ts"] for r in elek)
    rodziny, odrzuty = zbierz(elek)
    ile = sum(len(v) for v in rodziny.values())
    w_pasie, bez, zlapane = pokrycie(elek, rodziny)

    print(f"rodzin: {ile} w {len(rodziny)} markach, odrzutow {len(odrzuty)}")
    print(f"pasmo {T.MIN_PRICE}-{T.MAX_PRICE} EUR: {w_pasie} rowerow, "
          f"bez klucza {bez} ({bez/max(1,w_pasie):.0%})")
    print(f"klucz zyska: {zlapane} rowerow -> pokrycie pasma "
          f"{(w_pasie-bez+zlapane)/max(1,w_pasie):.0%} zamiast "
          f"{(w_pasie-bez)/max(1,w_pasie):.0%}")
    if gadaj:
        for marka, model, n, czemu in odrzuty:
            print(f"  ODRZUT: {marka} {model} ({n} rowerow) - {czemu}")

    out = {
      "_JAK_TO_CZYTAC": [
        "KLUCZ WYCENY, czyli odpowiedz na pytanie 'z czym porownac ten rower'.",
        "Nic wiecej: zadnego pietra, zadnego werdyktu o silniku, zadnej",
        "decyzji o kupowaniu - te siedza w topowe_modele.json",
        "i silniki_bosch.json.",
        "",
        "Czytany PO wzorcach z reki (MODEL_PATTERNS w tracker.py), nigdy",
        "przed. Zaden tytul, ktory ma dzis klucz, nie dostanie tu innego:",
        "wzorzec z reki ma przy sobie pomiar i pulapke, wiec zostaje pierwszy.",
        "",
        "Kolejnosc w kazdej marce MA ZNACZENIE: najpierw nazwa najbardziej",
        "szczegolowa. Inaczej 'reaction' wygralo by z 'reaction slx'.",
        "",
        "Klucz jest tez ZAPYTANIEM do OLX - stad marka w nim zostaje.",
        "'reaction pro' bez marki to nie jest nic, czego da sie szukac.",
        "",
        "Mozesz poprawiac recznie: dopisac rodzine, usunac blednu. Nazwy",
        "koncowek wersji ('pro', 'evo', 'on') dopisuj do KONCOWKI_WERSJI",
        "w sprawdz_rodziny.py, nie tutaj - inaczej wroca przy przeliczeniu."
      ],
      "_ZMIERZONE": (f"{date.today().isoformat()} na {len(elek)} elektrykach "
                     f"(rowery, nie ogloszenia - regula 5) z market.jsonl, "
                     f"okno {daty[0]}..{daty[-1]}"),
      "_POKRYCIE": {"pasmo_eur": [T.MIN_PRICE, T.MAX_PRICE],
                    "rowerow_w_pasmie": w_pasie,
                    "bez_klucza_przed": bez, "zyska_klucz": zlapane},
      "_PROGI": {"min_rowerow": MIN_ROWEROW,
                 "min_marek_ogolnego": MIN_MAREK_OGOLNEGO},
      "_ODRZUCONE": [f"{m} {mo} ({n}) - {cz}" for m, mo, n, cz in odrzuty],
      "marki": rodziny,
    }

    sciezka = "rodziny_modeli.json"
    try:
        stare = json.load(open(sciezka, encoding="utf-8")).get("marki", {})
    except Exception:
        stare = {}
    st_par = {(m, w["model"]) for m, l in stare.items() for w in l}
    no_par = {(m, w["model"]) for m, l in rodziny.items() for w in l}
    doszly, znikly = sorted(no_par - st_par), sorted(st_par - no_par)
    for tytul, lista in (("DOSZLA", doszly), ("ZNIKLA", znikly)):
        for m, mo in lista[:40]:
            print(f"  {tytul}: {m} {mo}")
        if len(lista) > 40:
            print(f"  ... i jeszcze {len(lista) - 40} ({tytul.lower()})")
    rozne = bool(doszly or znikly)
    if not rozne:
        print("  bez zmian - plik nadal sie broni")

    if zapisz:
        json.dump(out, open(sciezka, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        print(f"  zapisane do {sciezka}")
        return 0
    if rozne:
        print("\n  (nic nie zapisano - uruchom z --zapisz)")
    return 1 if rozne else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Przelicza rodziny_modeli.json na aktualnym market.jsonl")
    ap.add_argument("--zapisz", action="store_true",
                    help="przepisz plik (domyslnie tylko pokazuje roznice)")
    ap.add_argument("--odrzuty", action="store_true",
                    help="wypisz, co i czemu wypadlo")
    a = ap.parse_args()
    sys.exit(main(zapisz=a.zapisz, gadaj=a.odrzuty))
