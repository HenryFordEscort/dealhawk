#!/usr/bin/env python3
"""BestDealHawk - drugi kanał, tylko najlepsze oferty.

DealHawk wysyła 40-60 powiadomień dziennie (zmierzone 28.08-01.09.2026). To
dobrze, bo znaczy, że widzi rynek. Ale po dniu przerwy nie da się tego
przeczytać. Ten moduł wybiera z tego strumienia kilka ofert dziennie i wysyła
je na OSOBNY czat.

CZEGO TU NIE MA I BYĆ NIE MOŻE:

- Nie pobiera niczego z sieci poza wysyłką na Telegram. Czyta wyłącznie pliki,
  które DealHawk już zapisał. Dzięki temu nie dokłada ani jednego żądania do
  Kleinanzeigen, a dławienie per adres IP jest w tym repo zmierzone i realne.
- Nie dotyka `seen.json`, `history.jsonl` ani niczego, co należy do DealHawka.
  Ma własny plik stanu.
- Nie decyduje o niczym w DealHawku. Rower, który tu nie wejdzie, idzie
  normalną drogą i dostaje zwykłe powiadomienie, tak jak dziś.

Podział ról jest ten sam co `dozorca.py` (fakty) wobec `zycie_ofert.py`
(wnioski): tracker zapisuje, ten moduł wnioskuje. Dzięki temu zmiana reguły
to PRZELICZENIE (`--sucho --od`), a nie tydzień czekania na nowe dane.

Bez zmiennej TELEGRAM_BEST_CHAT_ID moduł nic nie robi i kończy się zerem -
lokalne biegi i testy mają działać bez drugiego czatu.
"""
import argparse
import html as html_mod
import contextlib
import json
import logging
import os
import re
import statistics
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

import tracker as T

log = logging.getLogger("najlepsze")

BEST_CHAT_ID = os.environ.get("TELEGRAM_BEST_CHAT_ID")
# Kanał najlepszych może chodzić na WŁASNYM bocie albo na tym samym co
# DealHawk. Domyślnie ten sam, bo bot jest tylko listonoszem: dwa kanały
# w Telegramie i tak są osobne (osobne nazwy, osobne powiadomienia, osobne
# wyciszanie), a jeden sekret mniej to jedno miejsce mniej do pomylenia.
#
# Osobny bot ma jednak dwie realne zalety i dlatego jest wspierany: inna
# ikona i nazwa na ekranie blokady, oraz to, że spalenie jednego tokenu nie
# ucisza obu kanałów naraz. Wystarczy dodać sekret TELEGRAM_BEST_BOT_TOKEN.
BEST_BOT_TOKEN = os.environ.get("TELEGRAM_BEST_BOT_TOKEN") or T.TELEGRAM_BOT_TOKEN
WYSLANE_FILE = Path("best_wyslane.json")
ODRZUTY_FILE = Path("odrzuty.jsonl")        # oznaczone przez właściciela, append-only
OFFSET_FILE = Path("best_offset.json")

# PRZYCISKI POD KAŻDĄ WIADOMOŚCIĄ. Powód: przez tydzień poprawiałem regułę
# cztery razy i za każdym razem właściciel musiał przysłać LINK, a dwa razy
# i tak trafiłem obok. Jedno kliknięcie w chwili, gdy jest wkurzony, jest
# warte więcej niż tydzień moich przemiałów - i jest oznaczonym przykładem,
# a nie anegdotą.
#
# Cztery powody wzięte WPROST z tego, co właściciel już powiedział:
#   "to jest złom totalnie zużyty"            -> zuzyty
#   "czy według ciebie to jest mega okazja?"  -> cena
#   "wypierdol S size, to jest niesprzedawalne" -> rozmiar
#   rower z 2018 wysłany na samej nazwie      -> stary
POWODY_ODRZUTU = [
    ("👎 zużyty", "zuzyty"),
    ("👎 za drogi", "cena"),
    ("👎 rozmiar", "rozmiar"),
    ("👎 za stary", "stary"),
]
TOPOWE_FILE = Path("topowe_modele.json")

# Okno porównania. Ten sam co ROZRZUT_OKNO_DNI w tracker.py i z tego samego
# powodu: rynek sprzed kwartału to inny rynek.
OKNO_DNI = 30
# Ile rowerów musi mieć grupa, żeby jej kwartyle coś znaczyły. Ten sam próg
# co ROZRZUT_MIN_ROWEROW - nie wymyślam drugiego.
MIN_ROWEROW = 12
# Poniżej tego percentyla cenowego w swojej grupie oferta jest "tania jak na
# siebie". ZAŁOŻONE, ale koszt zmierzony na 811 ofertach z 30 dni: p10 daje
# 4,3 trafienia dziennie, p15 -> 6,4, p25 -> 9,9. Wybrane najostrzejsze.
PROG_TANIO = 0.10
# Przebieg: dolny KWARTYL swojej grupy, nie mediana. Nie próg w kilometrach -
# 1500 km to mało dla rocznika 2019 i dużo dla 2025.
#
# Mediana była błędem i to widocznym dopiero na skali. "Poniżej mediany"
# spełnia z definicji POŁOWA rowerów, więc to nie sygnał, tylko rzut monetą,
# a mimo to wnosił pełną wagę. Zmierzone 08.09.2026: kombinacja
# "górna półka + przebieg pod medianą" była największym workiem na kanale
# (23 wejścia na 30 dni) i zarazem najsłabszym. Kwartyl to ta sama granica,
# na której stoi `sygnal_rozrzutu` w tracker.py przy cenie - nie wymyślam
# drugiej.
PROG_PRZEBIEG = 0.25
# "Nowy rocznik" liczony WZGLĘDEM DZISIAJ, nie wpisany na sztywno - inaczej
# za dwa lata plik po cichu zacząłby chwalić rowery czteroletnie.
NOWY_ROCZNIK_OD = T.CURRENT_YEAR - 2

# ROZMIAR RAMY. Właściciel 12.09.2026: "wypierdol S size w ogóle, według mnie
# to jest niesprzedawalne, M też średnio ale niech będzie, głównie chodzi o L".
# To jest wiedza o POLSKIM rynku zbytu, której z niemieckich ogłoszeń nie da
# się wyliczyć - i dlatego siedzi tu jako jawna lista, a nie jako wzór.
RAMY_ODRZUCANE = {"XS", "S"}          # nie do sprzedania w PL
RAMY_PREMIOWANE = {"L"}               # to, po co właściciel jeździ


def rozmiar_ramy(oferta):
    m = re.search(r"\bS([1-6])\b", oferta.get("title") or "")
    if m and "specialized" in (oferta.get("title") or "").lower() or \
       m and re.search(r"\b(levo|kenevo)\b", oferta.get("title") or "", re.I):
        return SPEC_ROZMIARY[f"S{m.group(1)}"]
    return _rozmiar_ramy_ogolny(oferta)


def _rozmiar_ramy_ogolny(oferta):
    """Litera rozmiaru albo None. Liczy to `tracker.litera_ramy`.

    Reguła stała tu od 12.09.2026 i przestała być sama: po komendę `/rozmiar`
    sięgnął po nią drugi czytelnik (`rozmiary.py`). Dwie kopie tego samego
    warunku rozjeżdżają się po pierwszej poprawce - a ta akurat decyduje,
    czy rower w ogóle pokaże się właścicielowi. Została więc w jednym
    miejscu, przy czytniku, który zapisuje pole `rama`."""
    return T.litera_ramy(oferta)
# Ile dni wstecz patrzymy przy zwykłym biegu. Dwa, nie jeden: bieg o 00:05
# musiałby inaczej zgubić wszystko, co przyszło wczoraj wieczorem.
SWIEZOSC_DNI = 2
# Sufit na jeden bieg. Gdyby reguła kiedyś zwariowała, kanał ma zamilknąć
# z alarmem, a nie wysłać 200 wiadomości.
MAX_NA_BIEG = 8


# === WCZYTYWANIE WIEDZY =====================================================

def load_topowe(plik=None):
    """Modele z górnej półki, TYLKO te, które bot w ogóle może kupić.

    Model z werdyktem `nie_bosch` zostaje w pliku (żeby nikt nie dopisał go
    drugi raz z głowy), ale tutaj wypada: filtr silnika odrzuci taki rower
    wcześniej, więc wpuszczenie go tu dołożyłoby wyłącznie ciszy.

    Kolejność MA ZNACZENIE: najbardziej szczegółowa nazwa pierwsza. Inaczej
    "stereo 160" wygrałoby ze "stereo one44" i topowy model dostałby piętro
    słabszego.
    """
    # Sciezka czytana W CHWILI WYWOLANIA, nie w domyslnym argumencie - patrz
    # `load_wyslane`. Ten sam blad popelniony tu drugi raz uciszyl alarm
    # o braku pliku modeli: alarm byl napisany, przetestowany i MARTWY, bo
    # podmiana TOPOWE_FILE nic nie zmieniala.
    plik = plik or TOPOWE_FILE
    try:
        d = json.loads(plik.read_text(encoding="utf-8"))
    except FileNotFoundError:
        log.error(f"{plik} nie istnieje - kanał najlepszych działa bez modeli")
        return []
    except Exception as e:
        log.error(f"{plik} nie do odczytania: {e}")
        return []
    kupowalne = {"bosch", "specialized", "mieszany"}
    out = []
    for w in d.get("topowe", []):
        if w.get("silnik") not in kupowalne:
            continue
        try:
            out.append((w,
                        re.compile(r"\b" + re.escape(w["marka"]) + r"\b", re.I),
                        re.compile(w["wz"], re.I)))
        except re.error as e:
            log.error(f"zły wzorzec przy {w.get('marka')} {w.get('model')}: {e}")
    out.sort(key=lambda x: -len(x[0]["model"]))
    return out


# Ile znakow wolno miedzy marka a modelem. "Cube Stereo Hybrid 160" ma tu
# jedno slowo, "CUBE STEREO HYBRID HPC 160" dwa - 40 znakow zbiera oba,
# a nie sklei konca tytulu z jego poczatkiem.
_MAX_ODSTEP = 40

# Wszystkie marki, jakie umiemy rozpoznac. Sluzy WYLACZNIE do ustalenia,
# czyj jest rower - wiec sa tu takze marki, ktorych bot nie kupuje.
_WSZYSTKIE_MARKI = None


def _marki(topowe):
    global _WSZYSTKIE_MARKI
    if _WSZYSTKIE_MARKI is None:
        z_pliku = set()
        try:
            d = json.loads(TOPOWE_FILE.read_text(encoding="utf-8"))
            for k in ("topowe", "_ODRZUCONE_BO_CHYBA_NIE_FULLY"):
                for w in d.get(k, []):
                    if isinstance(w, dict) and w.get("marka"):
                        z_pliku.add(w["marka"])
        except Exception:
            pass
        z_pliku |= set(T.PREMIUM_BRANDS)
        z_pliku |= {"mondraker", "giant", "haibike", "bulls", "conway", "focus",
                    "merida", "orbea", "ghost", "kalkhoff", "raymon", "flyer",
                    "cannondale", "lapierre", "stevens", "husqvarna", "bergamont",
                    "centurion", "corratec", "winora", "riese", "rotwild", "simplon"}
        _WSZYSTKIE_MARKI = [(m, re.compile(r"\b" + re.escape(m) + r"\b", re.I))
                            for m in z_pliku]
    return _WSZYSTKIE_MARKI


def marka_roweru(tytul, topowe):
    """Marka wymieniona w tytule jako PIERWSZA.

    Sprzedawcy porownuja swoj rower do slawniejszego: "Mondraker Chaser e MTB
    Fully AEHNLICH Cube Stereo Hybrid 160" (prawdziwe ogloszenie, zlapane
    02.09.2026). Reguly "jest marka i jest model" nie da sie tu obronic zadnym
    sprytem ze slowami porownania, bo "ähnlich" stoi PRZED marka, a "wie neu"
    jest w co drugim niemieckim tytule i znaczy co innego.

    Reguła, ktora sie broni: rower nalezy do marki wymienionej PIERWSZEJ.
    Sprzedawca zaczyna od tego, co sprzedaje.
    """
    najw = None
    for nazwa, wz in _marki(topowe):
        m = wz.search(tytul)
        if m and (najw is None or m.start() < najw[1]):
            najw = (nazwa, m.start())
    return najw[0] if najw else None


# === SPECIALIZED: WERSJA x GENERACJA =========================================
# Właściciel 15.09.2026: "podaj mi hierarchię modeli speca od najlepszych do
# średnich, i te absolutnie topowe muszą się znaleźć w powiadomieniach".
#
# Ogólny generator `topowe_modele.json` tej wiedzy NIE WYŁAPUJE i nie ma jak:
# tokenizer ucina "S-" z "S-Works" (w pliku stało samo "works"), skleja
# "Comp" z "Comp Alloy" w jedno "levo comp" i w ogóle nie widzi generacji.
# A generacja jest tu decydująca. Zmierzone 15.09.2026 na 2 333 unikalnych
# Specializedach z market.jsonl (rowery, nie ogłoszenia):
#
#                  Gen 4 (2025+)   Gen 3 (2022-24)
#     S-Works        11 594 €         5 599 €
#     Pro             9 444 €         5 449 €
#     Expert          7 990 €         4 109 €
#     Comp            6 649 €         3 699 €      <- karbon
#     Comp Alloy      5 500 €         3 099 €
#     Alloy           4 924 €         2 600 €
#
# Drabinka wersji to oficjalna kolejność Specialized, a dane ją potwierdzają
# w obu generacjach co do pozycji. PUŁAPKA, która kosztowała jeden zły pomiar:
# licząc bez podziału na generacje, "Comp Alloy" (3 399 €) wychodził DROŻSZY
# od "Comp" (2 500 €). Powód: nazwa "Comp Alloy" istnieje dopiero od Gen 3,
# a "Comp" obejmuje też rowery z 2016-2021. Reguła 3 z CLAUDE.md w czystej
# postaci - jedna cena dla roweru z 2018 i z 2025.
#
# Kolejność sprawdzania wersji MA ZNACZENIE: "Comp Alloy" zawiera oba słowa.
SPEC_WERSJE = [
    ("S-Works", r"s[\s-]?works"),
    ("Pro", r"\bpro\b"),
    ("Expert", r"\bexpert\b"),
    ("Comp Alloy", r"\bcomp\b.{0,12}\balloy\b|\balloy\b.{0,12}\bcomp\b"),
    ("Comp", r"\bcomp\b"),
    ("Alloy", r"\balloy\b"),
]
# Piętro tylko dla dwóch ostatnich generacji. Gen 1 i 2 to rowery z lat
# 2016-2021 (mediana 1 750 i 1 940 €) - nawet S-Works z tamtych lat nie jest
# "absolutnie topowy" w sensie, o który chodzi właścicielowi.
SPEC_PIETRA = {
    "S-Works": "szczyt", "Pro": "szczyt",
    "Expert": "wysoka",
    "Comp": "gorna_polka", "Comp Alloy": "gorna_polka",
}
# Gdy tytuł nie ma rocznika, generacja go przybliża - środek jej okresu,
# a dla bieżącej jej początek. Bez tego "Turbo Levo 3 Comp Alloy" z 13 cyklami
# baterii leciał jako rower bez żadnej wiedzy o wieku.
SPEC_ROK_Z_GENERACJI = {"Gen 4": 2025, "Gen 3": 2023}
# Specialized nie pisze rozmiarów literami, tylko S1-S6. Zmierzone na tytułach
# Levo w market.jsonl: S2 25 razy, S3 108, S4 138, S5 76, S6 31. Bez tego
# przeliczenia rama L u Specialized nigdy nie dostawała premii, a rama S
# prześlizgiwała się przez weto. Oficjalna tabela Specialized:
SPEC_ROZMIARY = {"S1": "XS", "S2": "S", "S3": "M", "S4": "L", "S5": "XL", "S6": "XXL"}


def specialized(tytul):
    """(rodzina, wersja, generacja) albo None, gdy to nie Levo/Kenevo."""
    t = (tytul or "").lower()
    if "specialized" not in t and not re.search(r"\b(levo|kenevo)\b", t):
        return None
    if "kenevo" in t:
        rodzina = "Kenevo SL" if re.search(r"\bsl\b", t) else "Kenevo"
    elif re.search(r"\blevo\b", t):
        rodzina = "Levo SL" if re.search(r"\bsl\b", t) else "Levo"
    else:
        return None
    wersja = next((n for n, wz in SPEC_WERSJE if re.search(wz, t, re.I)), None)
    if re.search(r"levo\s*4\b|gen\.?\s*4\b|\bg4\b", t):
        gen = "Gen 4"
    elif re.search(r"levo\s*3\b|gen\.?\s*3\b|\bg3\b", t):
        gen = "Gen 3"
    elif re.search(r"\bfsr\b|6\s*fattie", t):
        gen = "Gen 1"
    else:
        rok = T.extract_year(tytul or "")
        gen = ("Gen 4" if rok and rok >= 2025 else
               "Gen 3" if rok and rok >= 2022 else
               "Gen 2" if rok and rok >= 2019 else
               "Gen 1" if rok else None)
    return rodzina, wersja, gen


def pietro_specialized(tytul):
    """Wpis w kształcie `topowe_modele.json` dla Specializeda albo None."""
    sp = specialized(tytul)
    if not sp:
        return None
    rodzina, wersja, gen = sp
    if gen not in SPEC_ROK_Z_GENERACJI or wersja not in SPEC_PIETRA:
        return None
    return {"marka": "specialized", "model": f"{rodzina} {wersja} {gen}".lower(),
            "pietro": SPEC_PIETRA[wersja], "mediana": None, "x_marka": None,
            "silnik": "specialized", "_spec": True}


def pietro_modelu(tytul, topowe):
    """Wpis z topowe_modele.json dla tego roweru albo None.

    Trzy warunki naraz: marka roweru (ta pierwsza w tytule) zgadza sie
    z marka wpisu, model stoi PO marce i blisko niej.
    """
    spec = pietro_specialized(tytul)
    if spec:
        return spec
    czyj = marka_roweru(tytul, topowe)
    if not czyj:
        return None
    for w, wz_marki, wz_modelu in topowe:
        if w["marka"] != czyj:
            continue
        for mm in wz_marki.finditer(tytul):
            mo = wz_modelu.search(tytul, mm.end())
            if mo and (mo.start() - mm.end()) <= _MAX_ODSTEP:
                return w
    return None


def klucz_grupy(tytul, topowe):
    """Z czym porownujemy ten rower.

    Najpierw model z `topowe_modele.json`, a dopiero z jego braku `olx_query_for`.
    Powod jest zmierzony: `olx_query_for` czyta z tytulu, wiec ten sam rower
    trafia raz do "cube stereo hybrid 160", a raz do "cube stereo hybrid",
    zaleznie od tego, czy sprzedawca wcisnal "HPC" przed numer. Skutkiem byly
    dwie rozne mediany dla jednego modelu w jednym biegu (2 900 € przy 122
    rowerach obok 3 790 € przy 302) i procent "pod mediana" nie znaczyl nic.
    """
    w = pietro_modelu(tytul, topowe)
    if w:
        return f"{w['marka']} {w['model']}"
    # Specialized spoza pięter (Alloy, starsze generacje) i tak porównujemy
    # w obrębie wersji i generacji - inaczej wraca mieszanie roczników.
    sp = specialized(tytul)
    if sp and sp[1] and sp[2]:
        return f"specialized {sp[0]} {sp[1]} {sp[2]}".lower()
    return T.olx_query_for(tytul, None)


def zbuduj_porownanie(topowe, dzis=None, plik=None, seen=None):
    """{model: {"ceny": [...], "km": [...]}} z dziennika rynku.

    Trzy decyzje przepisane z `tracker.zbuduj_rozrzut`, każda z powodem:

    1. Tylko PÓŁKI (`NAZWY_POLEK`). Zapytania kluczowe mają w adresie
       `s-preis:800:3000`, więc nie widzą droższego końca rynku i kwartyl
       policzony z nich byłby zaniżony.
    2. Rowery, nie ogłoszenia (reguła 5): najpierw po `id`, potem po odcisku
       (tytuł znormalizowany, cena).
    3. Grupa to SAM MODEL, bez klasy baterii. Para (model, bateria) daje
       kwartyle dokładniejsze, ale ma je tylko 23% ofert - zmierzone
       02.09.2026 na 811 powiadomieniach. Przy takim pokryciu kanał
       najlepszych dostawałby 0,1 roweru dziennie, czyli byłby martwy.
       Sam model daje 65%. Cena tego poszerzenia jest realna (w jednym worku
       ląduje 500 Wh z 750 Wh) i płacimy ją wetami niżej: mała bateria
       i rocznik poniżej grupy nie wchodzą.
    """
    dzis = dzis or date.today()
    granica = (dzis - timedelta(days=OKNO_DNI)).isoformat()
    # DZIENNIK RYNKU JEST W KAWAŁKACH MIESIĘCZNYCH od 18.09.2026, więc bez
    # podanego `plik` idziemy po CAŁOŚCI. Samo `T.MARKET_FILE` to dziś już
    # tylko najstarszy kawałek - czytanie go w pojedynkę dałoby ułamek danych,
    # a wynik nadal wyglądałby wiarygodnie i nikt by tego nie zauważył
    # (reguła 7). `plik` zostaje, bo podstawiają go testy i tryb `--sucho`.
    po_id = {}
    try:
        with contextlib.ExitStack() as stos:
            wiersze = (stos.enter_context(open(plik, encoding="utf-8")) if plik
                       else T.market_wiersze())
            for linia in wiersze:
                try:
                    r = json.loads(linia)
                except Exception:
                    continue
                if not isinstance(r, dict):
                    continue
                if r.get("ts", "") < granica or r.get("s") not in T.NAZWY_POLEK:
                    continue
                if not isinstance(r.get("p"), int):
                    continue
                # REGUŁA 1: pole `km` w dzienniku policzył czytnik przebiegu,
                # więc jego poprawka (17.09.2026: jednostka słowem "Kilometer",
                # przecinek jako ułamek) jest pozorna, dopóki stare wiersze
                # niosą starą liczbę. Dziennika NIE przepisujemy, bo to zapis
                # faktów - przeliczamy go przy czytaniu. Odczyt jest wierny,
                # a nie zgadywany: `log_market` też bierze przebieg WYŁĄCZNIE
                # z tytułu. Zmierzone na 106 952 tytułach: 67 odczytów nowych
                # i 54 poprawione, z czego 17 stało ponad progiem zajeżdżenia
                # przy prawdziwym przebiegu kilkuset kilometrów.
                r["km"] = T.parse_mileage(T._extract_mileage(r.get("t") or "", ""))
                po_id[r.get("id") or id(r)] = r
    except FileNotFoundError:
        log.error(f"{plik} nie istnieje - nie ma z czym porównywać")
        return {}
    grupy, widziane = {}, set()
    for r in po_id.values():
        tytul = r.get("t") or ""
        odcisk = (T._tytul_znormalizowany(tytul), r["p"])
        if odcisk in widziane:
            continue
        widziane.add(odcisk)
        model = klucz_grupy(tytul, topowe)
        if not model:
            continue
        g = grupy.setdefault(model, {"ceny": [], "km": [], "lata": []})
        g["ceny"].append(r["p"])
        if r.get("km") is not None:
            g["km"].append(r["km"])
        if r.get("y"):
            g["lata"].append(r["y"])
    # PRZEBIEGI Z OPISÓW, nie tylko z tytułów (17.09.2026).
    #
    # Dziennik rynku zapisuje przebieg WYŁĄCZNIE z tytułu (`log_market`), bo
    # strony ogłoszenia na etapie półki nikt nie czyta. A sprzedawcy piszą
    # przebieg głównie w opisie. Skutek zmierzony tego dnia: sygnał niskiego
    # przebiegu mógł w ogóle zadziałać w 23 grupach na 275, a przy Levo Comp
    # Alloy Gen 3 z 577 km i 13 cyklami baterii grupa miała DWA przebiegi,
    # więc sygnał milczał.
    #
    # `seen.json` trzyma przebieg odczytany przez bota z CAŁEJ strony
    # ogłoszenia. Dokładamy go po `id` (reguła 5 - rowery, nie ogłoszenia),
    # a gdy ten sam rower jest w obu źródłach, wygrywa opis. Zmierzony zysk:
    # grup z pełną próbką 23 -> 34, Cube Stereo Hybrid 140 91 -> 272.
    #
    # Próg MIN_ROWEROW zostaje bez zmian. Levo Comp Alloy Gen 3 idzie z 2 na 8
    # i nadal milczy - obniżenie progu, żeby go wpuścić, byłoby luzowaniem
    # kryterium pod jeden rower.
    if seen:
        km_po_id = {}
        for r in po_id.values():
            if r.get("km") is not None:
                km_po_id[str(r.get("id"))] = (klucz_grupy(r.get("t") or "", topowe), r["km"])
        for ad_id, v in seen.items():
            if not isinstance(v, dict) or v.get("mileage_num") is None:
                continue
            if (v.get("date") or "") < granica:
                continue
            k = klucz_grupy(v.get("title") or "", topowe)
            if k:
                km_po_id[str(ad_id)] = (k, v["mileage_num"])
        for g in grupy.values():
            g["km"] = []
        for k, km in km_po_id.values():
            if k and k in grupy:
                grupy[k]["km"].append(km)

    for g in grupy.values():
        g["ceny"].sort()
        g["km"].sort()
        g["lata"].sort()
    # Rozkład przebiegów WSZYSTKICH grup naraz - zapas dla weta "wysoki
    # przebieg", gdy własna grupa roweru jest za mała. Klucz zaczyna się od
    # "__", więc nie zderzy się z żadną nazwą modelu. Używany WYŁĄCZNIE do
    # zaostrzania (weto), nigdy do premii za niski przebieg - premia zostaje
    # przy własnej grupie, żeby zapas nie luzował kryterium po cichu.
    wszystkie_km = sorted(km for g in grupy.values() for km in g["km"])
    if wszystkie_km:
        grupy["__wszystkie__"] = {"ceny": [], "km": wszystkie_km, "lata": []}
    return grupy


def percentyl(wartosc, posortowane):
    """Ułamek grupy TAŃSZY (mniejszy) od tej wartości. None, gdy grupa za mała."""
    if not posortowane or len(posortowane) < MIN_ROWEROW:
        return None
    return sum(1 for x in posortowane if x < wartosc) / len(posortowane)


# === DECYZJA ================================================================
# Funkcja CZYSTA, bez sieci i bez plików - żeby dało się na nią napisać test.
# W tym repo już raz kosztowało pół dnia to, że decyzja o tempie siedziała
# w środku pętli i nikt nie zauważył, że próg nie jest przekraczany nigdy.

def ocen(oferta, topowe, porownanie):
    """Czy ta oferta ma trafić na kanał najlepszych.

    Zwraca (wchodzi: bool, powody: list[str], weta: list[str]).

    Reguła w jednym zdaniu: wchodzi rower, który jest TOPOWYM MODELEM swojej
    marki, albo jest wyraźnie tańszy od swoich przy niskim przebiegu. Piętro
    "szczyt" wystarcza samo, niższe piętra potrzebują drugiego powodu.

    Powody są WYPISYWANE do wiadomości. To nie jest ozdoba: jak właściciel
    zobaczy rower, który go nie interesuje, ma od razu wiedzieć, która reguła
    go tu wpuściła, i którą kazać poprawić.
    """
    tytul = oferta.get("title") or ""
    cena = oferta.get("price_num")
    km = oferta.get("mileage_num")
    powody, weta = [], []

    # --- WETO TWARDE. Jedno, i to takie, które mówi o ODSPRZEDAŻY, nie
    # o cenie: mała bateria i wersje SL schodzą w Polsce wolno i trudno,
    # niezależnie od tego, jak dobrze wyglądają w Niemczech. DealHawk już to
    # sygnalizuje w zwykłej wiadomości, tu po prostu nie wpuszczamy takiego
    # roweru na kanał "najlepsze".
    # BATERIA czytana LUZNIEJ niz w samym DealHawku i to jest przemyslane.
    # `is_small_battery` stoi tam na `battery_wh`, ktory wymaga literalnego
    # "Wh" - bo tam brak odczytu znaczy "przepusc", wiec luzniejszy czytnik
    # dokladalby ODRZUTY. Tutaj jest odwrotnie: brak odczytu znaczy "wpusc",
    # wiec luzniejszy czytnik dokłada WIEDZE. Zmierzone: "Cube Stereo Hybrid
    # 160 Race 500 27.5" za 900 € przeszedl weto scisle, bo w tytule jest
    # gole "500" bez jednostki - a to najtanszy rower w calym wyborze i tani
    # wlasnie dlatego, ze ma mala baterie.
    # ROZMIAR RAMY jako weto. To wiedza o polskim rynku ZBYTU, nie o rowerze:
    # rama S stoi w Polsce miesiącami, więc zysk na papierze nic nie znaczy.
    rama = rozmiar_ramy(oferta)
    if rama in RAMY_ODRZUCANE:
        weta.append(f"rama {rama} - w PL praktycznie nie do sprzedania")

    wh = T.bateria_z_nazwy(tytul)
    if wh and wh < T.SMALL_BATTERY_WH:
        weta.append(f"bateria {wh} Wh - wolna odsprzedaz w PL")
    elif T.is_small_battery(tytul, ""):
        weta.append("mała bateria / SL")

    model = klucz_grupy(tytul, topowe)
    grupa = porownanie.get(model) if model else None

    # ROCZNIK NIE JEST WETEM, tylko unieważnia jeden argument.
    #
    # Pierwsza wersja odrzucała cały rower, gdy jego rocznik był poniżej
    # mediany modelu. Zmierzone na 811 ofertach z 30 dni: wylatywało tak
    # kilkadziesiąt zdrowych ofert, w tym Cube Stereo Hybrid 160 HPC z 2021
    # i Scott Patron eRide 910 z 2022 - rowery, o które w tym kanale chodzi.
    #
    # Dwa powody, dla których to było złe. Po pierwsze, mediana roczników
    # liczy się z pola `y` w market.jsonl, a to `extract_year` z TYTUŁU -
    # rocznik pisze w tytule głównie ten, kto ma świeży, więc mediana jest
    # zawyżona z definicji. Po drugie, rocznik JUŻ siedzi w wycenie
    # (`year_factor`, 7,2% na rok), więc weto liczyło go drugi raz.
    #
    # Zostaje to, co jest prawdą: gdy rower jest starszy od swojej grupy,
    # niska cena NIE JEST dowodem okazji, bo tłumaczy ją rocznik. Sam rower
    # może dalej wejść, ale musi to zrobić innym powodem.
    rok = oferta.get("year")
    if not rok:
        sp = specialized(tytul)
        if sp and sp[2] in SPEC_ROK_Z_GENERACJI:
            rok = SPEC_ROK_Z_GENERACJI[sp[2]]
    cena_wytlumaczona = False
    if grupa and rok and len(grupa["lata"]) >= MIN_ROWEROW:
        mediana_lat = statistics.median(grupa["lata"])
        if rok < mediana_lat - 1:
            cena_wytlumaczona = True

    # --- POWÓD 1: topowy model swojej marki (lista właściciela, nie statystyka)
    #
    # "GÓRNA PÓŁKA" LICZY SIĘ TYLKO Z BIEŻĄCEJ GENERACJI, i to jest poprawka
    # MOJEJ WŁASNEJ NADGORLIWOŚCI z 09.09.2026.
    #
    # Tamtego dnia odebrałem górnej półce wagę w całości, bo na SZEŚCIU
    # obserwacjach z pierwszego dnia wyszło, że dobre oferty były ze "szczytu"
    # i "wysokiej", a złom z "górnej półki". Sześć obserwacji to szum, nie
    # dane - dokładnie to, przed czym ostrzega reguła nadrzędna w CLAUDE.md.
    # Koszt tej pomyłki, zmierzony 12.09 na 298 ofertach z czterech dni:
    # kanał przestał wysyłać m.in. "Cube Stereo Hybrid 160 HPC SLX 750,
    # rocznik 2026, 948 km, 2 899 €", czyli dokładnie taką ofertę, po jaką
    # ten kanał powstał. Górna półka to 10% ruchu; odebranie jej wagi ścięło
    # pokrycie listy modeli z 19% do 9%.
    #
    # Co NAPRAWDĘ dzieliło te dwa rowery, oba "Stereo 160":
    #     dobra:  HPC SLX 750, rocznik 2026, 948 km, 2 899 €
    #     złom:   HPC SL  625, rocznik BRAK,  990 km,   800 €
    # Nie piętro. ROCZNIK. Model pospolity (sam Stereo 160 ma 328 sztuk na
    # rynku) sam z siebie niczego nie dowodzi, ale pospolity model
    # z BIEŻĄCEJ generacji owszem - i to jest dosłownie to, o co prosił
    # właściciel: "nowo dodane topowe wersje".
    # SAMA NAZWA TOPOWEGO MODELU NIE WYSTARCZA, GDY ROWER JEST STARY.
    #
    # Właściciel przysłał 12.09 konkret: "Specialized Levo women Gr. S von
    # 2018, Motor neu" za 1 450 €, wysłane wyłącznie na powodzie
    # `model_szczyt`. Ośmioletni rower, bez przebiegu w ogłoszeniu, z WYMIENIONYM
    # silnikiem (czyli oryginalny padł) i w wersji damskiej rozmiar S, czyli
    # z najwęższym rynkiem zbytu w Polsce. „Levo" na liście topowych modeli
    # wnosiło wagę 2 i wpuszczało go samo.
    #
    # Piętro mówi, JAKIM modelem rower jest, a nie w jakim jest stanie. Przy
    # bieżącej generacji to wystarcza, bo rower jest z definicji świeży.
    # Przy starszym roczniku nazwa modelu to dopiero połowa argumentu -
    # druga musi przyjść z ceny albo z przebiegu.
    wpis = pietro_modelu(tytul, topowe)
    swiezy = bool(rok and rok >= NOWY_ROCZNIK_OD)
    if wpis and (wpis["pietro"] in ("szczyt", "wysoka") or swiezy):
        waga_pietra = 2 if (wpis["pietro"] == "szczyt" and swiezy) else 1
        if wpis.get("_spec"):
            # Wpis z drabinki Specialized nie ma mediany marki - to kolejność
            # wersji, nie proporcja ceny (patrz SPEC_WERSJE).
            tekst = (f"Specialized {wpis['model'].title()} to "
                     f"{wpis['pietro'].replace('_', ' ')} w drabince wersji Specialized")
        else:
            tekst = (f"{wpis['marka'].capitalize()} {wpis['model'].upper()} to "
                     f"{wpis['pietro'].replace('_', ' ')} tej marki "
                     f"(mediana modelu {_zl(wpis['mediana'])} €, "
                     f"{wpis['x_marka']}x mediana marki)")
        powody.append({
            "kod": "model_" + wpis["pietro"],
            "tekst": tekst,
            "waga": waga_pietra,
        })

    # --- POWÓD 2: świeża generacja
    #
    # Właściciel: "interesują nas nowo dodane topowe wersje". Rocznik jest
    # jedynym twardym odczytem generacji, jaki mamy - pojemność baterii się
    # do tego nie nadaje (zmierzone: 400 Wh wychodzi nowsze niż 625, bo
    # producenci wracają do małych baterii w lekkich modelach).
    if rok and rok >= NOWY_ROCZNIK_OD:
        powody.append({
            "kod": "nowy_rocznik",
            "tekst": f"rocznik {rok}, czyli bieżąca generacja",
            "waga": 1,
        })

    # --- POWÓD 3: rozmiar, którego właściciel realnie szuka
    if rama in RAMY_PREMIOWANE:
        powody.append({
            "kod": "rama",
            "tekst": f"rama {rama}, czyli rozmiar z najszerszym zbytem w PL",
            "waga": 1,
        })

    # --- POWÓD 4: tanio jak na swój model
    #
    # NISKA CENA PRZY NIEZNANYM STANIE NIE JEST DOWODEM OKAZJI.
    # Poprawka po pierwszym dniu pracy kanału (09.09.2026). Właściciel:
    # "przyszło coś fajnie bo poniżej ceny średniej rynkowej ale to jest złom
    # totalnie zużyty". Rower, o którym mowa, kosztował 1 500 € przy medianie
    # modelu i NIE MIAŁ ANI PRZEBIEGU, ANI ROCZNIKA - sprzedawca nie podał
    # niczego poza ceną.
    #
    # Najczęstszy powód, dla którego rower jest bardzo tani, to zużycie. Gdy
    # nie znamy ani przebiegu, ani rocznika, nie da się tego wykluczyć, więc
    # niska cena mówi "nie wiem", a nie "okazja". Zmierzone na 82 wyborach
    # z 30 dni: 17 z nich (21%) stało wyłącznie na cenie przy zerowej wiedzy
    # o stanie - i to z nich pochodził złom. To ta sama zasada co przy
    # roczniku wyżej i ta sama, co w regule 6: nie ma pomiaru, nie ma liczby.
    znamy_stan = (oferta.get("mileage_num") is not None) or bool(rok)

    # WYSOKI PRZEBIEG TŁUMACZY NISKĄ CENĘ, tak samo jak stary rocznik
    # (17.09.2026). Właściciel oznaczył przyciskiem "zużyty" oferte
    # "Cube Stereo Hybrid 160 SL, rama L, 2 668 km, 1 700 €". Weszła, bo była
    # tania, a przebieg był ZNANY - tyle że znany i ZŁY: 2 668 km to prawie
    # sufit 3 000 km. `znamy_stan` pilnował tylko, czy przebieg jest podany,
    # a nie, czy jest dobry. Niska cena nie była tam okazją, tylko skutkiem
    # zużycia.
    #
    # Próg to GÓRNY kwartyl własnej grupy - lustro premii za niski przebieg,
    # która stoi na dolnym kwartylu. Żadnej liczby wziętej z głowy. Gdy grupa
    # ma za mało przebiegów, porównujemy z rozkładem wszystkich grup naraz:
    # to zaostrza (unieważnia argument), więc zapas nie może niczego po
    # cichu wpuścić.
    #
    # WYSOKI MUSI BYĆ NA DWA SPOSOBY NARAZ: na tle własnego modelu I na tle
    # wszystkich rowerów. Sam kwartyl grupy się nie broni i złapał to istniejący
    # test obniżek: Cube ONE44 z 800 km. ONE44 to rowery prawie nowe, górny
    # kwartyl ich przebiegu to 654 km, więc 800 km wypadało "wysoko" - a 800 km
    # na elektryku to żadne zużycie. Zmierzone 17.09.2026: globalny górny
    # kwartyl to 1 636 km. Dopiero oba warunki oddzielają "dużo jak na nowego
    # ONE44" od "zajechany": Cube 160 z 2 668 km jest w 93. percentylu swojej
    # grupy I powyżej globalnego kwartyla, ONE44 z 800 km tylko w pierwszym.
    km_oferty = oferta.get("mileage_num")
    wszystkie = (porownanie.get("__wszystkie__") or {}).get("km", [])
    if km_oferty is not None and len(wszystkie) >= MIN_ROWEROW:
        p_glob = percentyl(km_oferty, wszystkie)
        p_grupy = (percentyl(km_oferty, grupa["km"])
                   if grupa and len(grupa["km"]) >= MIN_ROWEROW else None)
        wysoki_glob = p_glob is not None and p_glob >= 1 - PROG_PRZEBIEG
        wysoki_grupy = p_grupy is None or p_grupy >= 1 - PROG_PRZEBIEG
        if wysoki_glob and wysoki_grupy:
            cena_wytlumaczona = True

    pc = percentyl(cena, grupa["ceny"]) if (grupa and cena) else None
    if pc is not None and pc <= PROG_TANIO and not cena_wytlumaczona and znamy_stan:
        mediana = int(statistics.median(grupa["ceny"]))
        ile = int((mediana - cena) / mediana * 100) if mediana else 0
        powody.append({
            "kod": "tanio",
            "tekst": (f"{ile}% pod medianą swojego modelu "
                      f"({_zl(mediana)} €, {len(grupa['ceny'])} rowerów "
                      f"z {OKNO_DNI} dni)"),
            "waga": 1,
        })

    # --- POWÓD 5: niski przebieg jak na swój model
    km = oferta.get("mileage_num")
    pkm = percentyl(km, grupa["km"]) if (grupa and km is not None) else None
    if pkm is not None and pkm <= PROG_PRZEBIEG:
        mediana_km = int(statistics.median(grupa["km"]))
        powody.append({
            "kod": "przebieg",
            "tekst": f"{_zl(km)} km przy medianie modelu {_zl(mediana_km)} km",
            "waga": 1,
        })

    # O ROWERZE, O KTORYM NIE WIEMY NIC, NIE MOWIMY "OKAZJA".
    #
    # Trzecie wcielenie tej samej pulapki (12.09.2026). Weszlo tedy m.in.
    # "Specialized Levo Turbo 29 Zoll Gr. M" za 950 EUR - bez rocznika, bez
    # przebiegu, wylacznie na tym, ze "Levo" jest na liscie topowych modeli.
    # Piętro "szczyt" wnosi wage 2, wiec wpuszczalo SAMO, omijajac warunek
    # znamy_stan postawiony wczesniej przy cenie.
    #
    # Regula jest teraz jedna i wspolna dla wszystkich drog wejscia: musimy
    # znac CHOC JEDEN fakt o stanie roweru - rocznik albo przebieg. Bez tego
    # nie ma czego polecac, jest tylko nazwa modelu i kwota.
    znamy_cokolwiek = (oferta.get("mileage_num") is not None) or bool(rok)
    waga = sum(p["waga"] for p in powody)
    wchodzi = waga >= 2 and not weta and znamy_cokolwiek
    # Powód wypisywany ZAWSZE, gdy brakuje wiedzy o stanie, nie tylko gdy
    # reszta wagi by wystarczyła. Inaczej log milczy akurat przy ofertach,
    # o których nie wiemy nic - a to jest najczęstsza przyczyna odrzutu.
    if not znamy_cokolwiek and powody:
        weta.append("nie znam ani rocznika, ani przebiegu")
    return wchodzi, powody, weta


# === WIADOMOŚĆ ==============================================================

def _zl(n):
    return f"{n:,}".replace(",", " ")


def zbuduj_wiadomosc(oferta, powody):
    """Krótko. Cały sens tego kanału to przeczytanie go w minutę."""
    # Tytul w seen.json jest JUZ raz zakodowany przez parser strony
    # ("dustyolive&#39;n&#39;gold"). Zakodowany drugi raz wychodzi na ekranie
    # jako "&amp;#39;". Rozkodowanie przed kodowaniem domyka to raz na zawsze.
    tytul = html_mod.escape(html_mod.unescape(oferta.get("title") or ""))
    L = [f"🏆 <b>{tytul}</b>"]

    # 2,6% ofert nie ma ceny w ogóle ("VB", "brak ceny"). Wypisywanie
    # "kupno VB" w kanale, którego cały sens to okazje cenowe, jest gorsze
    # niż przyznanie się - właściciel dostał taką wiadomość pierwszego dnia.
    cena_txt = str(oferta.get("price") or "")
    naglowek = [f"kupno {cena_txt}" if re.search(r"\d", cena_txt)
                else "⚠️ sprzedawca nie podał ceny"]
    if oferta.get("loc"):
        region = T.region_z_plz(oferta["loc"])
        naglowek.append(region or oferta["loc"])
    L.append("  ·  ".join(naglowek))
    L.append("")

    # DLACZEGO TU JEST. Najważniejsza linijka w całej wiadomości - bez niej
    # kanał jest kolejnym strumieniem, który trzeba oceniać samemu.
    for p in powody:
        L.append("✅ " + p["tekst"])

    fakty = [x for x in (
        oferta.get("mileage") if oferta.get("mileage") != "brak danych" else None,
        f"rama {oferta['rama']}" if oferta.get("rama") else None,
        f"{oferta['wh']} Wh" if oferta.get("wh") else None,
        str(oferta["year"]) if oferta.get("year") else None,
    ) if x]
    if fakty:
        L.append("")
        L.append(" · ".join(fakty))

    if oferta.get("olx_median"):
        dostaniesz = T.cena_sprzedazy_realna(oferta["olx_median"])
        linia = (f"W PL wystawisz za ~{_zl(oferta['olx_median'])} zł, "
                 f"dostaniesz ~{_zl(dostaniesz)} zł")
        if oferta.get("liquidity_days"):
            linia += f" · schodzą w ~{oferta['liquidity_days']} dni"
        L.append(linia)

    # Zysk NA KOŃCU i zawsze podpisany. To najmniej pewna liczba w całym
    # systemie (mediana błędu 20%, zapisanych realnych sprzedaży: 0), więc
    # nie ma prawa stać nad faktami ani decydować o wejściu na ten kanał.
    if oferta.get("profit") is not None:
        znak = "🔥" if oferta["profit"] > 500 else "🟡" if oferta["profit"] > 0 else "🔴"
        znak_zysku = "+" if oferta["profit"] >= 0 else "-"
        L.append(f"{znak} <i>Szacowany zysk ~{znak_zysku}{_zl(abs(oferta['profit']))} zł "
                 f"(szacunek, nie pomiar)</i>")

    L += ["", oferta.get("url", "")]
    return "\n".join(L)


def czy_zyje(url):
    """'zyje' | 'zdjete' | 'rezerwacja' | 'nieznane' - sprawdzane TUZ PRZED
    wysylka.

    Wlasciciel 12.09: "wysylasz o 20 ogloszenie ktore jest usuniete".
    DealHawk widzi ogloszenia z mediana 4 minut od wystawienia, ale nie
    zawsze: to konkretne mial 55 minut na karku, a zanim wlasciciel kliknal,
    sprzedawca zdazyl je skasowac. Wiadomosc o rowerze, ktorego juz nie ma,
    jest gorsza niz brak wiadomosci - kosztuje zaufanie do calego kanalu.
    Przy 2-5 ofertach dziennie to 2-5 zadan na dobe, czyli koszt zerowy
    wobec setek, ktore robi sam DealHawk.

    'nieznane' NIE blokuje wysylki. Nieudany odczyt to awaria sieci, a nie
    dowod zniknięcia - ta sama zasada co w `dozorca_de.ocen_strone`, gdzie
    zamiana watpliwosci w pewnosc kosztowala kiedys skasowanie danych OLX.
    """
    if not url or "kleinanzeigen.de" not in url:
        return "nieznane"          # willhaben ma inny uklad strony
    try:
        import dozorca_de
        w = dozorca_de.sprawdz_ogloszenie(url)
    except Exception as e:
        log.info(f"nie sprawdzono zywotnosci {url[:50]}: {e}")
        return "nieznane"
    if w.get("stan") != "zyje":
        return w.get("stan", "nieznane")
    return "rezerwacja" if w.get("rez") is True else "zyje"


def klawiatura_odrzutu(ad_id):
    """Dwa rzędy po dwa przyciski. `callback_data` ma u Telegrama limit
    64 bajtów, a "zl|<id>|<powod>" mieści się z zapasem."""
    rzedy, para = [], []
    for napis, kod in POWODY_ODRZUTU:
        para.append({"text": napis, "callback_data": f"zl|{ad_id}|{kod}"})
        if len(para) == 2:
            rzedy.append(para)
            para = []
    if para:
        rzedy.append(para)
    return {"inline_keyboard": rzedy}


def klawiatura_pod_oferta(ad_id):
    """Przyciski pod wiadomością na kanale: powody odrzutu PLUS pełna oferta.

    KLUCZ OBNIŻKI TRZEBA OBCIĄĆ. Na tym kanale `ad_id` bywa w postaci
    "id@cena" (ścieżka przecen, patrz `main`), a `/oferta` przyjmuje sam numer
    ogłoszenia - z ogonem komenda odbiłaby się o własną walidację i przycisk
    wyglądałby na zepsuty. Odrzut zostaje przy PEŁNYM kluczu, bo tam chodzi
    o oznaczenie konkretnej wiadomości, także przeceny.

    Awaria modułu oferty nie ma prawa zabrać przycisków odrzutu - te działają
    od 13.09 i są jedyną pętlą zwrotną, jaką ten kanał ma."""
    kl = klawiatura_odrzutu(ad_id)
    try:
        import oferta
        rzad = oferta.przycisk_oferty(str(ad_id).split("@")[0])
        if rzad:
            kl["inline_keyboard"].append(rzad)
    except Exception as e:
        log.warning(f"przycisk pełnej oferty pominięty: {e}")
    return kl


def wyslij(tekst, chat_id=None, klawiatura=None):
    """Wysyłka na DRUGI czat. Własna, bo `tracker.send_telegram` ma numer
    czatu wpisany na sztywno i nie wolno go przy okazji przestawić.

    Token bierze z BEST_BOT_TOKEN, czyli z osobnego bota, gdy właściciel taki
    ustawił, a z DealHawkowego, gdy nie."""
    chat_id = chat_id or BEST_CHAT_ID
    if not chat_id:
        return False
    url = f"https://api.telegram.org/bot{BEST_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": tekst, "parse_mode": "HTML",
               "disable_web_page_preview": False}
    if klawiatura:
        payload["reply_markup"] = klawiatura
    for proba in range(3):
        try:
            r = requests.post(url, json=payload, timeout=10)
            if r.status_code == 429:
                czekaj = r.json().get("parameters", {}).get("retry_after", 3)
                time.sleep(czekaj + 1)
                continue
            r.raise_for_status()
            return True
        except Exception as e:
            log.error(f"Telegram (próba {proba + 1}/3): {e}")
            time.sleep(2)
    return False


# === ODZEW WŁAŚCICIELA ======================================================

def _api(metoda, **payload):
    try:
        r = requests.post(f"https://api.telegram.org/bot{BEST_BOT_TOKEN}/{metoda}",
                          json=payload, timeout=10)
        return r.json()
    except Exception as e:
        log.info(f"telegram {metoda}: {e}")
        return {}


def obsluz_komende(tekst, chat_id):
    """Komenda napisana do bota kanału. Liczy ją `tracker`, my tylko odsyłamy.

    Po co w ogóle: właściciel pisze do tego bota, bo nazywa się BestDealHawk.
    Odsyłanie go do drugiego czatu byłoby stratą jego czasu, a różnica jest
    wyłącznie techniczna - oba boty czytają te same pliki.
    """
    try:
        # `/oferta` PRZED `/dojrzale`: tamten wzorzec jest luźny, a ten ma
        # własny rozbiór w module oferty - jedno miejsce, jedna reguła.
        import oferta as _of
        parsed = _of.parse_oferta_command(tekst)
        if parsed:
            log.info(f"komenda na kanale: {tekst}")
            wyslij(_of.handle_oferta(*parsed), chat_id=chat_id)
            return
        m = re.match(r"/(dojrza[lł]\w*|przecen\w*)\s*(\d)?", tekst, re.I)
        if m:
            log.info(f"komenda na kanale: {tekst}")
            wyslij(T.handle_dojrzale(int(m.group(2)) if m.group(2) else 2),
                   chat_id=chat_id)
            return
        # Cisza jest gorsza od błędu - ta sama zasada co w tracker.py.
        wyslij("Tu jest tylko kanał najlepszych ofert i przyciski pod "
               "wiadomościami.\n\nUmiem odpowiedzieć na <code>/dojrzale</code> "
               "— kto schodzi z ceny i nadal stoi, oraz na "
               "<code>/oferta 3515700088</code> — gotowa wiadomość z twardą "
               "ofertą do sprzedawcy.\n\nResztę komend "
               "(<code>/wycen</code>, <code>/status</code>, "
               "<code>/kupilem</code>) obsługuje bot DealHawka.",
               chat_id=chat_id)
    except Exception as e:
        log.error(f"obsluz_komende '{tekst}': {e}")


def czytaj_odrzuty(seen=None):
    """Zbiera kliknięcia w przyciski "to szrot" i dopisuje je do odrzuty.jsonl.

    ODPYTUJEMY WYŁĄCZNIE WŁASNEGO BOTA. Gdyby kanał chodził na tokenie
    DealHawka, dwa procesy czytałyby tę samą kolejkę `getUpdates`
    z przesuwanym wskaźnikiem, a Telegram po odczycie kasuje starsze wpisy -
    więc raz jeden, raz drugi gubiłby zdarzenia, losowo i po cichu. Przy
    osobnym bocie kolejka jest osobna i problem nie istnieje.

    Zapisujemy KOMPLET kontekstu (tytuł, cena, rocznik, przebieg, powody
    wejścia), a nie samo id. Dzięki temu plik da się czytać za pół roku bez
    sklejania go z `seen.json`, który do tego czasu zdąży się przyciąć.
    """
    if not BEST_BOT_TOKEN or BEST_BOT_TOKEN == T.TELEGRAM_BOT_TOKEN:
        return 0                       # patrz docstring - nie ścigamy się
    try:
        offset = json.loads(OFFSET_FILE.read_text()).get("offset", 0)
    except Exception:
        offset = 0
    d = _api("getUpdates", offset=offset, timeout=0)
    if not d.get("ok"):
        return 0
    seen = seen if seen is not None else {}
    zapisane, max_id = 0, offset - 1
    with ODRZUTY_FILE.open("a", encoding="utf-8") as f:
        for upd in d.get("result", []):
            max_id = max(max_id, upd.get("update_id", max_id))
            # WIADOMOŚĆ, NIE KLIKNIĘCIE. Właściciel napisał "/dojrzale" do
            # TEGO bota (nazywa się BestDealHawk, więc to naturalny odruch),
            # a kod po cichu ją połknął i przesunął wskaźnik. Zgłosił to
            # słowami "napisalem do dealhawka bez reakcji" - i miał rację,
            # tylko trafił do drugiego bota.
            #
            # Zamiast odsyłać go do innego czatu, ten bot po prostu odpowiada.
            # Komendy liczy `tracker`, więc wynik jest identyczny.
            # WPIS Z KANAŁU LICZY SIĘ TAK SAMO JAK ZWYKŁA WIADOMOŚĆ
            # (20.09.2026). `tracker.read_telegram_commands` czyta OBA typy
            # od dawna, a ten czytnik znał tylko `message` - więc gdyby bot
            # był administratorem kanału i właściciel wkleił tam link,
            # wpis przyszedłby jako `channel_post`, przesunął wskaźnik
            # kolejki i przepadł. Dwie kopie tej samej reguły rozjechały się
            # dokładnie tak, jak ostrzega akapit o `litera_ramy`.
            msg = upd.get("message") or upd.get("channel_post")
            tekst_msg = ((msg or {}).get("text") or "").strip()
            # GOŁY WKLEJONY LINK, WYŁĄCZNIE NA TYM KANALE (19.09.2026).
            # Właściciel: "chce zeby to dzialalo tylko na bestdealhawku".
            # Na telefonie link to jedyna rzecz, którą da się wyjąć
            # z powiadomienia jednym stuknięciem - dopisywanie przed nim
            # słowa "/oferta" jest dokładnie tym przepisywaniem z ekranu,
            # przez które komenda bywa martwa. `tracker` tej funkcji nie
            # woła i na DealHawku goły link ma nadal nie robić NIC;
            # pilnuje tego test sprawdzający oba końce naraz.
            if tekst_msg and not tekst_msg.startswith("/"):
                try:
                    import oferta as _of
                    goly = _of.komenda_z_linku(tekst_msg)
                    # CISZA JEST GORSZA OD BŁĘDU. Gdy wiadomość WYGLĄDA na
                    # podane ogłoszenie, a nie da się z niej zrobić komendy,
                    # mówimy to wprost. Zmierzone 19.09.2026: wskaźnik
                    # kolejki przeskoczył o 1 o 20:21:34, czyli bot
                    # wiadomość przeczytał i sam postanowił nic nie robić -
                    # a właściciel zobaczył wyłącznie ciszę i nie miał jak
                    # odróżnić "nie zrozumiałem" od "bot padł".
                    if not goly and _of.wyglada_na_probe_linku(tekst_msg):
                        wyslij("Nie wyjąłem z tego numeru ogłoszenia.\n\n"
                               "Wklej pełny adres z Kleinanzeigen albo "
                               "willhaben, albo napisz "
                               "<code>/oferta 3517059558</code> z samym "
                               "numerem.",
                               chat_id=(msg.get("chat") or {}).get("id"))
                        continue
                    tekst_msg = goly or ""
                except Exception as e:
                    log.error(f"link w czacie kanału: {e}")
                    tekst_msg = ""
            if tekst_msg.startswith("/"):
                obsluz_komende(tekst_msg, (msg.get("chat") or {}).get("id"))
                continue
            cq = upd.get("callback_query")
            if not cq:
                continue
            # PRZYCISK PEŁNEJ OFERTY. Zamienia się na tę samą komendę, którą
            # właściciel może wpisać palcem - jedna droga w kodzie, jeden
            # zestaw błędów. Bez tego przycisk na TYM kanale byłby martwy:
            # jego kliknięcia trafiają do kolejki bota kanału, której
            # `tracker` nie czyta (i czytać nie może - dwa procesy na jednym
            # `getUpdates` gubiłyby zdarzenia, patrz docstring wyżej).
            if (cq.get("data") or "").startswith("of|"):
                try:
                    import oferta as _of
                    cmd = _of.komenda_z_przycisku(cq.get("data"))
                    if cmd:
                        obsluz_komende(
                            cmd, ((cq.get("message") or {}).get("chat") or {}).get("id"))
                except Exception as e:
                    log.error(f"przycisk oferty: {e}")
                _api("answerCallbackQuery", callback_query_id=cq["id"],
                     text="Składam ofertę…")
                continue
            czesci = (cq.get("data") or "").split("|")
            if len(czesci) != 3 or czesci[0] != "zl":
                continue
            _, ad_id, powod = czesci
            # Klucz obniżki ma postać "id@cena" (patrz `main`), więc wprost
            # `seen.get` nie trafiał. Dwa pierwsze kliknięcia właściciela
            # (15.09.2026, oba na przecenie 3511112265) zapisały się przez to
            # BEZ tytułu, ceny i rocznika, czyli bezużyteczne. Szukamy po id.
            o = seen.get(ad_id.split("@")[0]) or {}
            f.write(json.dumps({
                "ts": datetime.now().isoformat(timespec="seconds"),
                "id": ad_id, "powod": powod,
                "title": o.get("title"), "price_num": o.get("price_num"),
                "year": o.get("year"), "km": o.get("mileage_num"),
                "rama": o.get("rama"), "wh": o.get("wh"),
                "profit": o.get("profit"),
            }, ensure_ascii=False) + "\n")
            zapisane += 1
            # Odpowiedź MUSI pójść, inaczej przycisk kręci się w nieskończoność
            # i wygląda na zepsuty.
            _api("answerCallbackQuery", callback_query_id=cq["id"],
                 text="Zapisane. Dzięki, to poprawia regułę.")
    if d.get("result"):
        OFFSET_FILE.write_text(json.dumps({"offset": max_id + 1}))
    if zapisane:
        log.info(f"zapisano {zapisane} oznaczeń od właściciela")
    return zapisane


# === STAN ===================================================================

# Sciezka czytana W CHWILI WYWOLANIA, nie w domyslnym argumencie. Domyslny
# argument wiaze wartosc przy DEFINICJI modulu, wiec podmiana WYSLANE_FILE
# (test, inny katalog roboczy) nie mialaby zadnego skutku i zapis szedlby
# caly czas w stare miejsce. Zlapane testem pierwszego biegu.
def load_wyslane(plik=None):
    plik = plik or WYSLANE_FILE
    try:
        return json.loads(plik.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_wyslane(stan, plik=None):
    plik = plik or WYSLANE_FILE
    plik.write_text(json.dumps(stan, ensure_ascii=False, indent=1),
                    encoding="utf-8")


# === BIEG ===================================================================

def swieze_obnizki(dzis=None, plik=None):
    """{ad_id: nowa_cena} z ogłoszeń, którym w ostatnich dniach spadła cena.

    Osobna droga, bo obniżka NIE zmienia pola `date` we wpisie (tracker
    aktualizuje cenę i przebieg, datę zostawia z pierwszego spotkania). Wpis
    wypada więc z okna świeżości i przez zwykłą ścieżkę nie wróci NIGDY.
    Zmierzone 08.09.2026: 3,3 obniżki dziennie, z czego 4 na 30 dni
    kwalifikowałyby się na ten kanał - mało, ale to dokładnie te zdarzenia,
    dla których on powstał ("bot widział ten rower drożej, właśnie stanial").

    Czytamy `history.jsonl`, bo tam obniżka jest zapisana jako FAKT
    (`ev: "drop"`) razem z nową ceną. To dziennik append-only, nigdy
    nie kasowany - patrz twarde ograniczenia w CLAUDE.md.
    """
    dzis = dzis or date.today()
    granica = (dzis - timedelta(days=SWIEZOSC_DNI)).isoformat()
    plik = plik or T.HISTORY_FILE
    out = {}
    try:
        with open(plik, encoding="utf-8") as f:
            for linia in f:
                try:
                    r = json.loads(linia)
                except Exception:
                    continue
                if (isinstance(r, dict) and r.get("ev") == "drop"
                        and r.get("id") and r.get("ts", "") >= granica):
                    out[r["id"]] = r.get("p")      # ostatnia obniżka wygrywa
    except FileNotFoundError:
        log.error(f"{plik} nie istnieje - obniżki nie będą widziane")
    return out


def kandydaci(seen, od=None, dzis=None):
    """Oferty, które DealHawk OCENIŁ (przeszły wszystkie jego filtry).

    Wpis bez `score` to odrzut albo nieudany odczyt - o takim rowerze nic nie
    wiemy i nie ma o czym rozmawiać.
    """
    dzis = dzis or date.today()
    od = od or (dzis - timedelta(days=SWIEZOSC_DNI)).isoformat()
    out = []
    for ad_id, v in seen.items():
        if not isinstance(v, dict) or v.get("score") is None:
            continue
        if (v.get("date") or "") < od:
            continue
        out.append((ad_id, v))
    out.sort(key=lambda x: x[1].get("date", ""))
    return out


def main(sucho=False, od=None, limit=MAX_NA_BIEG):
    if not BEST_CHAT_ID and not sucho:
        log.info("brak TELEGRAM_BEST_CHAT_ID - kanał najlepszych wyłączony")
        return 0

    topowe = load_topowe()
    seen = T.load_seen()
    porownanie = zbuduj_porownanie(topowe, seen=seen)
    # Kliknięcia zbierane PRZED wyborem: właściciel oznacza, my notujemy.
    # Nic z tego jeszcze nie wpływa na regułę - najpierw dane, potem wnioski,
    # ten sam podział co dozorca.py wobec zycie_ofert.py.
    czytaj_odrzuty(seen)
    wyslane = load_wyslane()

    # REGUŁA 7: brak pliku wiedzy to awaria, nie stan naturalny - a ta akurat
    # jest CICHA i dlatego najgorsza. Zmierzone 08.09.2026: bez
    # `topowe_modele.json` kanał wybiera 0 ofert na 30 dni zamiast 40, bo
    # piętro modelu wnosi wagę, bez której nic nie dobija do progu. Właściciel
    # widzi wtedy pustą skrzynkę i myśli "słaby tydzień", a naprawdę na
    # runnerze brakuje jednego pliku. Najbardziej prawdopodobna przyczyna:
    # ktoś wgrał `najlepsze.py`, a `topowe_modele.json` został na dysku.
    #
    # Alarm leci RAZ, nie co bieg: przy 84 biegach dziennie kanał zamieniłby
    # się w alarm o samym sobie.
    if not topowe:
        if not wyslane.get("_ALARM_BRAK_MODELI"):
            wyslij("⚠️ <b>BestDealHawk</b>\n\nNie widzę pliku "
                   "<code>topowe_modele.json</code> albo jest nieczytelny. "
                   "Bez niego ten kanał wybiera <b>zero</b> ofert, więc jego "
                   "cisza od teraz nic nie znaczy.\n\nNajpewniej plik nie "
                   "trafił do repozytorium razem z resztą. DealHawk działa "
                   "normalnie, to dotyczy wyłącznie tego kanału.")
            wyslane["_ALARM_BRAK_MODELI"] = True
            save_wyslane(wyslane)
        log.error("brak modeli - kanał wybrałby 0 ofert, alarm wysłany")
    elif wyslane.pop("_ALARM_BRAK_MODELI", None):
        # Plik wrócił. Mówimy o tym, bo inaczej właściciel nie wie, czy cisza
        # jest już prawdziwa, czy awaria dalej trwa.
        wyslij(f"✅ <b>BestDealHawk</b>\n\n<code>topowe_modele.json</code> "
               f"wrócił, {len(topowe)} modeli wczytanych. Kanał działa "
               f"normalnie.")
        save_wyslane(wyslane)

    # PIERWSZY BIEG NIE WYSYŁA NIC. Bez tego wystartowanie kanału oznaczałoby
    # jednorazową lawinę kilkudziesięciu rowerów z ostatniego miesiąca, w tym
    # dawno sprzedanych. Ta sama pułapka co przy `odblokuj.py`: zdjęcie blokady
    # nie znaczy, że ogłoszenie jest jeszcze aktualne.
    pierwszy = not WYSLANE_FILE.exists()

    lista = kandydaci(seen, od=od)

    # OBNIŻKI. Dokładane do listy z WŁASNYM kluczem stanu "id@cena", nie samym
    # "id". Dzięki temu rower wysłany raz w pełnej cenie może wrócić po
    # przecenie, ale ta sama przecena nie zabrzęczy dwa razy - a kolejna,
    # niższa, owszem, bo klucz się zmienia.
    obnizki = swieze_obnizki() if not od else {}
    for ad_id, nowa_cena in obnizki.items():
        v = seen.get(ad_id)
        if not isinstance(v, dict) or v.get("score") is None or not nowa_cena:
            continue
        v = dict(v)
        v["price_num"] = nowa_cena
        v["price"] = f"{nowa_cena} €"
        v["_obnizka"] = True
        lista.append((f"{ad_id}@{nowa_cena}", v))

    wybrane = []
    for ad_id, v in lista:
        if ad_id in wyslane:
            continue
        wchodzi, powody, weta = ocen(v, topowe, porownanie)
        if wchodzi:
            if v.get("_obnizka"):
                powody.insert(0, {"kod": "obnizka",
                                  "tekst": "📉 sprzedawca właśnie zszedł z ceny",
                                  "waga": 0})
            wybrane.append((ad_id, v, powody))
        elif weta and powody:
            log.info(f"weto ({', '.join(weta)}): {v.get('title','')[:60]}")

    log.info(f"kandydatów {len(lista)}, wybranych {len(wybrane)}")

    if sucho:
        print(f"\n=== SUCHO: {len(wybrane)} ofert z {len(lista)} ocenionych ===\n")
        for ad_id, v, powody in wybrane:
            print(f"--- {v.get('date')}  {v.get('price')}  [{ad_id}]")
            print(f"    {v.get('title','')[:100]}")
            for p in powody:
                print(f"    ✅ {p['tekst']}")
            print(f"    {v.get('url','')}")
            print()
        return 0

    if pierwszy:
        # Zapisujemy WSZYSTKO jako załatwione i milczymy. Od następnego biegu
        # kanał chodzi normalnie.
        save_wyslane({ad_id: {"d": v.get("date"), "start": True}
                      for ad_id, v in lista})
        log.info(f"pierwszy bieg: {len(lista)} ofert oznaczonych jako stare, "
                 f"nic nie wysłano")
        return 0

    if len(wybrane) > limit:
        # Reguła 7 w duchu: nagła powódź to awaria reguły, nie hojny rynek.
        log.error(f"reguła wybrała {len(wybrane)} ofert przy suficie {limit} "
                  f"- wysyłam {limit} najświeższych i zgłaszam")
        wyslij(f"⚠️ <b>BestDealHawk</b>\n\nReguła wybrała {len(wybrane)} ofert "
               f"w jednym biegu przy suficie {limit}. Wysyłam {limit} "
               f"najświeższych. To wygląda na rozjechany próg, nie na hojny "
               f"rynek - zgłoś to.")
        wybrane = wybrane[-limit:]

    zgubione = 0
    for i, (ad_id, v, powody) in enumerate(wybrane):
        # ŻYWOTNOŚĆ sprawdzana tuż przed wysyłką, nie przy wyborze - między
        # jednym a drugim mija cały bieg, a to wystarcza, żeby sprzedawca
        # zdjął ogłoszenie.
        stan = czy_zyje(v.get("url"))
        if stan == "zdjete":
            log.info(f"pominięte, ogłoszenie zdjęte: {v.get('title','')[:60]}")
            wyslane[ad_id] = {"d": v.get("date"), "pominiete": "zdjete"}
            save_wyslane(wyslane)
            continue
        if stan == "rezerwacja":
            log.info(f"pominięte, zarezerwowane: {v.get('title','')[:60]}")
            wyslane[ad_id] = {"d": v.get("date"), "pominiete": "rezerwacja"}
            save_wyslane(wyslane)
            continue
        if i:
            time.sleep(1.2)          # limit Telegrama ~1 wiadomość/s
        # ZAPIS PRZED WYSYŁKĄ, ta sama zasada co w `tracker.main`:
        # "Przerwany run = co najwyżej brak powiadomienia, nigdy duplikat."
        #
        # Stary kod trzymał `wyslane` w pamięci przez CAŁĄ pętlę i zapisywał
        # raz, po niej. A w tej pętli siedzi żądanie sieciowe (`czy_zyje`)
        # i pauza 1,2 s na ofertę, przy suficie ogniwa 8 minut. Bieg jest
        # jednym z SIEDMIU ogniw matrycy, a krok ma `continue-on-error: true`,
        # więc wywrotka kasowała pamięć o wszystkim, co już poszło, następne
        # ogniwo wysyłało to samo, i nie było tego widać w Actions.
        # Zgłoszone 18.09.2026: "wyslales dwa razy te same kilka ogloszen".
        # Zmierzone tego dnia: 3 z 5 ostatnich biegów skończyły się jako
        # `cancelled`, więc to nie był rzadki pech.
        #
        # Zapis PO wysyłce zamykał 99% dziury, ale nie całą: wywrotka między
        # wysłaniem a zapisem nadal dublowała tę jedną wiadomość. Przy zapisie
        # PRZED wysyłką najgorszy przypadek to jedna wiadomość, która nie
        # dojdzie - a ten rower i tak poszedł wcześniej na DealHawka, bo ten
        # kanał wybiera wyłącznie z ofert, które tamten już wysłał.
        # Zgubić jest tu taniej niż zdublować i tak samo stoi w trackerze.
        wyslane[ad_id] = {"d": v.get("date"),
                          "powody": [p["kod"] for p in powody]}
        save_wyslane(wyslane)
        if wyslij(zbuduj_wiadomosc(v, powody), klawiatura=klawiatura_pod_oferta(ad_id)):
            log.info(f"wysłane: {v.get('title','')[:60]}")
        else:
            # Oznaczone jako załatwione MIMO nieudanej wysyłki - inaczej
            # wracałoby co bieg. Rower jest już na DealHawku, więc strata
            # to brak POWTÓRZENIA, nie brak ogłoszenia.
            log.error(f"wysyłka nieudana, nie ponawiam: {v.get('title','')[:60]}")
            zgubione += 1

    save_wyslane(wyslane)
    # CZERWONY KROK ZAMIAST CISZY (18.09.2026). Ten kanał też milczał dziś
    # przez martwy token, a krok ma `continue-on-error: true`, więc biegu nie
    # zatrzyma - ale w Actions zostaje czerwony znacznik przy kroku i to
    # jedyny ślad po zgubionej wiadomości, który nie jedzie przez Telegram.
    # Rowery są ważniejsze od tego kanału i ta kolejność się nie zmienia.
    if zgubione:
        log.error(f"NIE DOSZŁO {zgubione} wiadomości na kanał najlepszych")
        return 1
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="BestDealHawk - kanał najlepszych ofert")
    ap.add_argument("--sucho", action="store_true",
                    help="wypisz, co by poszło, i nie wysyłaj niczego")
    ap.add_argument("--od", help="data początkowa YYYY-MM-DD (domyślnie 2 dni wstecz)")
    ap.add_argument("--limit", type=int, default=MAX_NA_BIEG)
    a = ap.parse_args()
    try:
        sys.exit(main(sucho=a.sucho, od=a.od, limit=a.limit))
    except Exception:
        # Ten moduł NIE MA PRAWA wywrócić biegu DealHawka. Powiadomienia
        # o rowerach są ważniejsze niż kanał najlepszych.
        log.exception("kanał najlepszych padł - DealHawk działa dalej")
        sys.exit(0)
