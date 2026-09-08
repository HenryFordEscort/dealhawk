#!/usr/bin/env python3
"""Testy kanału najlepszych ofert.

Każdy test tutaj pilnuje błędu, który NAPRAWDĘ zaszedł przy budowie tego
modułu 02.09.2026, i każdy PADA na wersji sprzed poprawki (reguła 2). Nazwy
w komentarzach mówią, co poszło nie tak - bez tego za pół roku wyglądają jak
pieczątki.

    TELEGRAM_BOT_TOKEN=dummy TELEGRAM_CHAT_ID=0 python test_najlepsze.py
"""
import json
import os
import re
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("TELEGRAM_CHAT_ID", "0")

import najlepsze as N

bledy = []


def sprawdz(warunek, opis):
    if warunek:
        print(f"  ok   {opis}")
    else:
        print(f"  BLAD {opis}")
        bledy.append(opis)


def _topowe(*wpisy):
    """Atrapa listy topowych modeli w kształcie, jaki daje load_topowe."""
    out = []
    for w in wpisy:
        out.append((w,
                    re.compile(r"\b" + re.escape(w["marka"]) + r"\b", re.I),
                    re.compile(w["wz"], re.I)))
    out.sort(key=lambda x: -len(x[0]["model"]))
    return out


def wpis(marka, model, pietro="wysoka", wz=None, mediana=4000, x=1.9):
    return {"marka": marka, "model": model, "pietro": pietro,
            "wz": wz or (model + r"(?![a-z0-9])"), "mediana": mediana,
            "x_marka": x, "silnik": "mieszany"}


# === 1. WZORCE MODELI =======================================================
# Wpadka: "powerfly+" wpisane wprost do regexpa znaczy "powerfl i co najmniej
# jedno y", więc ZWYKŁY Trek Powerfly 4 (mediana 1 900 €) wchodził na kanał
# jako topowy Powerfly+ (3 299 €). Osiem z dziesięciu przykładów w pierwszym
# pomiarze było przez to fałszywych.
def test_wzorzec_nie_lapie_bazowego_modelu():
    top = _topowe(wpis("trek", "powerfly+", wz=r"powerfly\+(?![a-z0-9])"))
    sprawdz(N.pietro_modelu("Trek Powerfly+ FS 9 2023", top) is not None,
            "Powerfly+ rozpoznany")
    sprawdz(N.pietro_modelu("Trek Powerfly 4 E-MTB", top) is None,
            "zwykły Powerfly 4 NIE jest topowym Powerfly+")


# Wpadka: nazwy modeli składam po wyrzuceniu słów pospolitych, więc "stereo
# one44" pochodzi ze "Stereo HYBRID ONE44" - między członami stoi słowo.
# Wzorzec sklejony samą spacją nie trafiał w NIC: 0 dopasowań na 811 ofertach.
def test_wzorzec_przeskakuje_slowo_w_srodku():
    top = _topowe(wpis("cube", "stereo one44",
                       wz=r"stereo[\s\S]{0,24}?one44(?![a-z0-9])"))
    sprawdz(N.pietro_modelu("Cube Stereo Hybrid ONE44 HPC Race", top) is not None,
            "'Stereo HYBRID ONE44' rozpoznane mimo słowa w środku")
    sprawdz(N.pietro_modelu("Cube Stereo Hybrid 120 Pro 625", top) is None,
            "bazowy Stereo 120 nie udaje ONE44")


# === 2. CZYJ JEST ROWER =====================================================
# Wpadka: prawdziwe ogłoszenie "Mondraker Chaser e MTB Fully ÄHNLICH Cube
# Stereo Hybrid 160" weszło na kanał jako topowy Cube. Sprzedawca porównywał
# swój rower do sławniejszego. Sprytu ze słowami porównania nie da się tu
# obronić, bo "ähnlich" stoi PRZED marką, a "wie neu" jest w co drugim
# niemieckim tytule i znaczy zupełnie co innego.
def test_marka_pierwsza_w_tytule_wygrywa():
    top = _topowe(wpis("cube", "stereo 160", "gorna_polka",
                       wz=r"stereo[\s\S]{0,24}?160(?![a-z0-9])"))
    sprawdz(N.pietro_modelu(
        "Mondraker Chaser e MTB Fully ähnlich Cube Stereo Hybrid 160", top) is None,
        "Mondraker porównany do Cube'a NIE dostaje piętra Cube'a")
    sprawdz(N.pietro_modelu("CUBE STEREO HYBRID HPC 160 RACE Fully", top) is not None,
            "prawdziwy Cube Stereo 160 dalej wchodzi")
    sprawdz(N.pietro_modelu("Wie neu! Cube Stereo Hybrid 160 HPC", top) is not None,
            "'wie neu' w tytule nie blokuje prawdziwego Cube'a")


# === 3. GRUPA PORÓWNAWCZA ===================================================
# Wpadka: `olx_query_for` czyta z tytułu, więc ten sam rower trafiał raz do
# "cube stereo hybrid 160", a raz do "cube stereo hybrid" - zależnie od tego,
# czy sprzedawca wcisnął "HPC" przed numer. W jednym biegu wyszły dwie różne
# mediany dla jednego modelu (2 900 € przy 122 rowerach obok 3 790 € przy 302)
# i procent "pod medianą" przestał cokolwiek znaczyć.
def test_grupa_porownawcza_jest_ta_sama_mimo_innego_zapisu():
    top = _topowe(wpis("cube", "stereo 160", "gorna_polka",
                       wz=r"stereo[\s\S]{0,24}?160(?![a-z0-9])"))
    a = N.klucz_grupy("Cube Stereo Hybrid 160 Race 750", top)
    b = N.klucz_grupy("CUBE STEREO HYBRID HPC 160 RACE E-Bike Fully", top)
    sprawdz(a == b == "cube stereo 160",
            f"oba zapisy trafiają do jednej grupy (dostałem {a!r} i {b!r})")


# === 4. WETA ================================================================
# Wpadka: "Cube Stereo Hybrid 160 Race 500 27.5" za 900 € (68% pod medianą!)
# przeszedł weto baterii, bo w tytule stoi gołe "500" bez jednostki, a
# `is_small_battery` wymaga literalnego "Wh". To był najtańszy rower w całym
# wyborze i tani dokładnie dlatego, że ma małą baterię.
def test_weto_lapie_gola_pojemnosc_baterii():
    top = _topowe(wpis("cube", "stereo 160", "gorna_polka",
                       wz=r"stereo[\s\S]{0,24}?160(?![a-z0-9])"))
    por = {"cube stereo 160": {"ceny": sorted([2500] * 20), "km": [], "lata": []}}
    # 400 Wh: gołe, bez jednostki w tytule. Ścisły czytnik z DealHawka tego
    # nie widzi, luźniejszy owszem - i o to w tym wecie chodzi.
    oferta = {"title": "Cube Stereo Hybrid 160 Race 400 27.5", "price_num": 900,
              "mileage_num": 500}
    wchodzi, powody, weta = N.ocen(oferta, top, por)
    sprawdz(not wchodzi, "rower z baterią 400 Wh nie wchodzi na kanał")
    sprawdz(any("400" in w for w in weta), f"weto mówi o baterii (dostałem {weta})")
    # 500 Wh NIE jest wetem i to jest ŚWIADOME: SMALL_BATTERY_WH w tracker.py
    # wynosi 500, więc granica należy do dopuszczalnych. Nie wymyślam tu
    # własnego, ostrzejszego progu - byłaby to liczba wzięta z głowy.
    graniczna = {"title": "Cube Stereo Hybrid 160 Race 500 27.5",
                 "price_num": 900, "mileage_num": 500}
    sprawdz(not N.ocen(graniczna, top, por)[2],
            "500 Wh to granica dopuszczalna, nie weto (za tracker.py)")


# Wpadka: pierwsza wersja odrzucała CAŁY rower, gdy jego rocznik był poniżej
# mediany modelu. Wylatywało tak kilkadziesiąt zdrowych ofert, w tym Cube
# Stereo Hybrid 160 HPC z 2021 i Scott Patron eRide 910 z 2022 - czyli rowery,
# o które w tym kanale chodzi. Rocznik ma unieważniać argument "tanio",
# a nie kasować roweru: siedzi już w wycenie (`year_factor`, 7,2% na rok).
def test_stary_rocznik_nie_kasuje_roweru_a_tylko_argument_ceny():
    top = _topowe(wpis("cube", "stereo one44", "szczyt",
                       wz=r"stereo[\s\S]{0,24}?one44(?![a-z0-9])"))
    por = {"cube stereo one44": {"ceny": sorted([4000] * 20),
                                 "km": sorted([1000] * 20),
                                 "lata": sorted([2024] * 20)}}
    stary = {"title": "Cube Stereo Hybrid ONE44 HPC", "price_num": 1500,
             "mileage_num": 300, "year": 2020}
    wchodzi, powody, weta = N.ocen(stary, top, por)
    sprawdz(wchodzi, "stary szczytowy model dalej wchodzi (piętro go wnosi)")
    sprawdz(not any(p["kod"] == "tanio" for p in powody),
            "ale niska cena NIE jest liczona jako powód - tłumaczy ją rocznik")


# === 5. PRÓG WEJŚCIA ========================================================
# "szczyt" wnosi wagę 2 i wystarcza sam. Niższe piętro wnosi 1 i potrzebuje
# drugiego powodu. Bez tego kanał robi się zwykłym DealHawkiem: samo "górna
# półka" ma 82 trafienia na 30 dni, czyli 2,7 dziennie i to bez żadnej oceny
# ceny ani przebiegu.
def test_pietro_szczyt_wystarcza_a_gorna_polka_nie():
    szczyt = _topowe(wpis("cube", "stereo one44", "szczyt",
                          wz=r"stereo[\s\S]{0,24}?one44(?![a-z0-9])"))
    polka = _topowe(wpis("cube", "stereo 160", "gorna_polka",
                         wz=r"stereo[\s\S]{0,24}?160(?![a-z0-9])"))
    # Ceny MUSZĄ mieć rozrzut. Atrapa "dwadzieścia razy ta sama cena" jest
    # zdegenerowana: każda cena wypada wtedy w percentylu 0 i argument "tanio"
    # zapala się zawsze, więc test nie sprawdzał tego, co miał sprawdzać.
    por = {"cube stereo one44": {"ceny": list(range(3000, 5000, 100)),
                                 "km": [], "lata": []},
           "cube stereo 160": {"ceny": list(range(2000, 3000, 50)),
                               "km": [], "lata": []}}
    a = {"title": "Cube Stereo Hybrid ONE44 HPC 750", "price_num": 4000,
         "mileage_num": 900}
    b = {"title": "Cube Stereo Hybrid 160 HPC 750", "price_num": 2500,
         "mileage_num": 900}
    sprawdz(N.ocen(a, szczyt, por)[0], "szczyt wchodzi sam")
    sprawdz(not N.ocen(b, polka, por)[0], "górna półka sama NIE wystarcza")


# === 6. WIADOMOŚĆ ===========================================================
# Wpadka: tytuł w seen.json jest JUŻ raz zakodowany przez parser strony
# ("dustyolive&#39;n&#39;gold"). Zakodowany drugi raz wychodził na ekranie
# jako "&amp;#39;". Druga wpadka w tej samej linijce: `.replace(",", " ")`
# puszczony na całym zdaniu zjadał przecinki w tekście, więc "(szacunek,
# nie pomiar)" stawało się "(szacunek nie pomiar)".
def test_wiadomosc_nie_koduje_dwa_razy_i_nie_zjada_przecinkow():
    oferta = {"title": "Cube Stereo ONE44 dustyolive&#39;n&#39;gold",
              "price": "2.500 € VB", "price_num": 2500, "mileage": "1.100 km",
              "profit": 4329, "olx_median": 13700, "url": "http://x"}
    powody = [{"kod": "model_szczyt", "tekst": "test", "waga": 2}]
    m = N.zbuduj_wiadomosc(oferta, powody)
    sprawdz("&amp;#39;" not in m, "apostrof nie jest kodowany dwa razy")
    sprawdz("(szacunek, nie pomiar)" in m, "przecinek w tekście przetrwał")


# === 7. BEZPIECZNIKI ========================================================
# Wpadka do uniknięcia: wystartowanie kanału oznaczałoby jednorazową lawinę
# kilkudziesięciu rowerów z ostatniego miesiąca, w tym dawno sprzedanych.
def test_pierwszy_bieg_nic_nie_wysyla():
    wyslane = []
    stary_wyslij, stary_plik = N.wyslij, N.WYSLANE_FILE
    stary_chat = N.BEST_CHAT_ID
    try:
        with tempfile.TemporaryDirectory() as d:
            N.WYSLANE_FILE = Path(d) / "best_wyslane.json"
            N.BEST_CHAT_ID = "123"
            N.wyslij = lambda *a, **k: wyslane.append(a) or True
            stary_seen = N.T.load_seen
            dzis = date.today().isoformat()
            N.T.load_seen = lambda: {
                "1": {"title": "Cube Stereo Hybrid ONE44 HPC", "price_num": 2000,
                      "price": "2.000 €", "mileage_num": 100, "score": 90,
                      "date": dzis, "url": "http://x"}}
            try:
                N.main()
                sprawdz(not wyslane, "pierwszy bieg nie wysłał ani jednej wiadomości")
                sprawdz(N.WYSLANE_FILE.exists(),
                        "pierwszy bieg zapisał stan, więc drugi już zadziała")
            finally:
                N.T.load_seen = stary_seen
    finally:
        N.wyslij, N.WYSLANE_FILE, N.BEST_CHAT_ID = stary_wyslij, stary_plik, stary_chat


# Moduł jest DODATKIEM. Bez drugiego czatu ma milczeć i kończyć się zerem,
# żeby lokalne biegi i krok w Actions nie wywracały się na jego braku.
def test_bez_zmiennej_srodowiskowej_modul_milczy():
    stary = N.BEST_CHAT_ID
    try:
        N.BEST_CHAT_ID = None
        sprawdz(N.main() == 0, "bez TELEGRAM_BEST_CHAT_ID kończy się zerem")
    finally:
        N.BEST_CHAT_ID = stary


# ŚCIEŻKA W DOMYŚLNYM ARGUMENCIE. Ten błąd wyszedł DWA RAZY przy budowie tego
# modułu: `def f(plik=WYSLANE_FILE)` wiąże wartość w chwili definicji modułu,
# więc podmiana zmiennej modułowej nie ma żadnego skutku. Za pierwszym razem
# zapis stanu szedł w stare miejsce, za drugim uciszyło to alarm o braku pliku
# modeli - alarm był napisany, przetestowany i MARTWY. Test pilnuje wzorca,
# a nie jednej funkcji, bo to pułapka języka i wróci trzeci raz.
def test_zadna_funkcja_nie_ma_sciezki_w_domyslnym_argumencie():
    import inspect
    zle = []
    for nazwa, fn in vars(N).items():
        if not inspect.isfunction(fn) or fn.__module__ != N.__name__:
            continue
        for arg, dom in (inspect.signature(fn).parameters.items()):
            if isinstance(dom.default, Path):
                zle.append(f"{nazwa}({arg}=...)")
    sprawdz(not zle, f"żadna funkcja nie ma Path w domyślnym argumencie "
                     f"(znalazłem: {zle})")


# CICHA AWARIA. Zmierzone 08.09.2026: bez topowe_modele.json kanał wybiera
# 0 ofert na 30 dni zamiast 40. Właściciel widzi pustą skrzynkę i myśli
# "słaby tydzień", a naprawdę na runnerze brakuje jednego pliku.
def test_brak_pliku_modeli_krzyczy_zamiast_milczec():
    wys = []
    st = (N.wyslij, N.WYSLANE_FILE, N.TOPOWE_FILE, N.BEST_CHAT_ID)
    try:
        with tempfile.TemporaryDirectory() as d:
            N.wyslij = lambda t, chat_id=None: wys.append(t) or True
            N.BEST_CHAT_ID = "-1001"
            N.WYSLANE_FILE = Path(d) / "w.json"
            N.WYSLANE_FILE.write_text("{}")          # nie pierwszy bieg
            N.TOPOWE_FILE = Path(d) / "nie_ma.json"
            stary_seen = N.T.load_seen
            N.T.load_seen = lambda: {}
            try:
                N.main()
                sprawdz(len(wys) == 1 and "topowe_modele.json" in wys[0],
                        "brak pliku modeli daje alarm, a nie ciszę")
                N.main()
                sprawdz(len(wys) == 1, "alarm leci RAZ, nie co bieg")
            finally:
                N.T.load_seen = stary_seen
    finally:
        N.wyslij, N.WYSLANE_FILE, N.TOPOWE_FILE, N.BEST_CHAT_ID = st


# OBNIŻKA NIE ZMIENIA POLA `date` we wpisie: tracker aktualizuje cenę
# i przebieg, a datę zostawia z pierwszego spotkania. Wpis wypadał więc z okna
# świeżości i przez zwykłą ścieżkę nie wracał NIGDY - a "bot widział ten rower
# drożej, właśnie stanial" to dokładnie zdarzenie, dla którego ten kanał
# powstał. Klucz stanu to "id@cena", nie samo "id", żeby ta sama przecena
# nie brzęczała dwa razy, a kolejna, niższa, owszem.
def test_obnizka_ceny_wraca_na_kanal():
    wys = []
    st = (N.wyslij, N.WYSLANE_FILE, N.BEST_CHAT_ID, N.T.HISTORY_FILE, N.T.load_seen)
    try:
        with tempfile.TemporaryDirectory() as d:
            dzis = date.today().isoformat()
            h = Path(d) / "h.jsonl"
            h.write_text(json.dumps({"ts": dzis, "ev": "drop",
                                     "id": "ABC", "p": 2100}) + "\n")
            N.wyslij = lambda tekst, chat_id=None: wys.append(tekst) or True
            N.BEST_CHAT_ID = "-1001"
            N.WYSLANE_FILE = Path(d) / "w.json"
            N.T.HISTORY_FILE = h
            # rower wysłany już wcześniej w pełnej cenie, wpis sprzed miesiąca
            N.WYSLANE_FILE.write_text(json.dumps({"ABC": {"d": dzis}}))
            N.T.load_seen = lambda: {"ABC": {
                "title": "Cube Stereo Hybrid ONE44 HPC SLX", "price": "2.600 €",
                "price_num": 2600, "mileage_num": 800, "score": 50,
                "date": "2026-08-01", "url": "http://x", "profit": 4000}}
            N.main()
            sprawdz(len(wys) == 1, "przeceniony rower wraca mimo starej daty wpisu")
            sprawdz(wys and "zszedł z ceny" in wys[0],
                    "wiadomość mówi wprost, że to obniżka")
            N.main()
            sprawdz(len(wys) == 1, "ta sama obniżka NIE brzęczy drugi raz")
            h.write_text(json.dumps({"ts": dzis, "ev": "drop",
                                     "id": "ABC", "p": 1900}) + "\n")
            N.main()
            sprawdz(len(wys) == 2, "kolejna, niższa obniżka owszem")
    finally:
        (N.wyslij, N.WYSLANE_FILE, N.BEST_CHAT_ID,
         N.T.HISTORY_FILE, N.T.load_seen) = st


# Reguła 7 w duchu: nagła powódź to awaria progu, nie hojny rynek.
def test_sufit_na_bieg():
    sprawdz(N.MAX_NA_BIEG <= 10,
            f"sufit wiadomości na bieg jest niski (jest {N.MAX_NA_BIEG})")


if __name__ == "__main__":
    for nazwa, fn in sorted(globals().items()):
        if nazwa.startswith("test_") and callable(fn):
            print(f"\n{nazwa}")
            fn()
    print()
    if bledy:
        print(f"PADŁO: {len(bledy)}")
        for b in bledy:
            print(f"  - {b}")
        sys.exit(1)
    print("wszystkie testy kanału najlepszych przechodzą")
