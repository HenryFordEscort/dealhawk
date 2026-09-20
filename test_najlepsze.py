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
    # Piętro "szczyt" wystarcza samo TYLKO przy bieżącej generacji - dlatego
    # atrapa ma rocznik. Bez niego nazwa modelu to połowa argumentu.
    a = {"title": "Cube Stereo Hybrid ONE44 HPC 750", "price_num": 4000,
         "mileage_num": 900, "year": N.NOWY_ROCZNIK_OD}
    # Górna półka ze STAREGO rocznika nie wystarcza. Wariant z bieżącą
    # generacją, który WCHODZI, sprawdza osobny test niżej - te dwa razem
    # opisują całą regułę i nie wolno poprawiać jednego bez drugiego.
    b = {"title": "Cube Stereo Hybrid 160 HPC 750", "price_num": 2500,
         "mileage_num": 900, "year": N.NOWY_ROCZNIK_OD - 4}
    sprawdz(N.ocen(a, szczyt, por)[0], "szczyt z bieżącej generacji wchodzi sam")
    sprawdz(not N.ocen(b, polka, por)[0],
            "górna półka ze starego rocznika NIE wystarcza")


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


# WYWROTKA W ŚRODKU PĘTLI NIE MOŻE KASOWAĆ PAMIĘCI O JUŻ WYSŁANYCH.
# Zgłoszone 18.09.2026: "wyslales dwa razy te same kilka ogloszen". Stary kod
# zapisywał stan RAZ, po całej pętli, a w pętli siedzi żądanie sieciowe
# (`czy_zyje`) i pauza 1,2 s na ofertę, przy suficie ogniwa 8 minut. Bieg jest
# jednym z SIEDMIU ogniw matrycy, a krok ma `continue-on-error: true`, więc
# cicha wywrotka znaczyła, że następne ogniwo wysyła to samo drugi raz.
def test_wywrotka_w_srodku_petli_nie_gubi_juz_wyslanych():
    stary_wyslij, stary_plik, stary_chat = N.wyslij, N.WYSLANE_FILE, N.BEST_CHAT_ID
    stary_zyje, stary_seen = N.czy_zyje, N.T.load_seen
    stary_sleep = N.time.sleep
    try:
        with tempfile.TemporaryDirectory() as d:
            N.WYSLANE_FILE = Path(d) / "best_wyslane.json"
            N.WYSLANE_FILE.write_text("{}")      # nie pierwszy bieg
            N.BEST_CHAT_ID = "123"
            N.czy_zyje = lambda url: "zyje"
            N.time.sleep = lambda s: None
            dzis = date.today().isoformat()
            N.T.load_seen = lambda: {
                str(i): {"title": "Cube Stereo Hybrid ONE44 HPC", "price_num": 2000,
                         "price": "2.000 €", "mileage_num": 100, "score": 90,
                         "date": dzis, "url": f"http://x/{i}"}
                for i in range(1, 5)}
            poszlo = []

            def wybuchowy(*a, **k):
                if len(poszlo) >= 2:
                    raise RuntimeError("runner padł w środku pętli")
                poszlo.append(a)
                return True

            N.wyslij = wybuchowy
            try:
                N.main()
            except RuntimeError:
                pass                              # dokładnie to robi produkcja
            stan = json.loads(N.WYSLANE_FILE.read_text())
            sprawdz(len(poszlo) == 2, f"dwie wiadomości zdążyły pójść ({len(poszlo)})")
            # WŁASNOŚĆ, nie implementacja: zapisanych musi być CO NAJMNIEJ tyle,
            # ile poszło. Wtedy żadna wysłana wiadomość nie wróci w następnym
            # ogniwie - a to jedyne, co właściciel widzi na telefonie.
            sprawdz(len(stan) >= len(poszlo),
                    f"zapisanych ({len(stan)}) >= wysłanych ({len(poszlo)}), "
                    f"więc NIC nie pójdzie drugi raz")
            # Zapis PRZED wysyłką: oferta, na której proces padł, też ma ślad.
            sprawdz(len(stan) > len(poszlo),
                    f"oferta przerwana w locie też oznaczona ({len(stan)} "
                    f"wobec {len(poszlo)}) - zgubić jest taniej niż zdublować")
    finally:
        N.wyslij, N.WYSLANE_FILE, N.BEST_CHAT_ID = stary_wyslij, stary_plik, stary_chat
        N.czy_zyje, N.T.load_seen, N.time.sleep = stary_zyje, stary_seen, stary_sleep


# ZGUBIONA WIADOMOŚĆ MA BYĆ WIDAĆ BEZ TELEGRAMA (18.09.2026).
# Wpadka tego dnia: token bota umarł, `wyslij` wracało z False przy każdej
# ofercie, moduł zapisywał "nie ponawiam" do logu i kończył się ZEREM. Krok
# w Actions świecił na zielono, a właściciel nie dostał nic i dowiedział się
# o awarii dopiero wtedy, gdy sam zapytał, czemu jest cisza.
#
# Alarm o zerwanej drodze nie może jechać tą drogą. Krok ma
# `continue-on-error: true`, więc biegu to nie zatrzyma - ale czerwony
# znacznik przy kroku zostaje i jest jedynym śladem poza Telegramem.
def test_zgubiona_wysylka_konczy_sie_kodem_1():
    stary_wyslij, stary_plik, stary_chat = N.wyslij, N.WYSLANE_FILE, N.BEST_CHAT_ID
    stary_zyje, stary_seen = N.czy_zyje, N.T.load_seen
    stary_sleep = N.time.sleep
    try:
        with tempfile.TemporaryDirectory() as d:
            N.WYSLANE_FILE = Path(d) / "best_wyslane.json"
            N.WYSLANE_FILE.write_text("{}")      # nie pierwszy bieg
            N.BEST_CHAT_ID = "123"
            N.czy_zyje = lambda url: "zyje"
            N.time.sleep = lambda s: None
            dzis = date.today().isoformat()
            N.T.load_seen = lambda: {
                str(i): {"title": "Cube Stereo Hybrid ONE44 HPC", "price_num": 2000,
                         "price": "2.000 €", "mileage_num": 100, "score": 90,
                         "date": dzis, "url": f"http://x/{i}"}
                for i in range(1, 4)}

            N.wyslij = lambda *a, **k: False     # dokładnie martwy token
            sprawdz(N.main() == 1,
                    "wszystkie wysyłki padły - bieg wychodzi czerwony")

            # A gdy dochodzą, ma być cicho. Czujka, która krzyczy zawsze,
            # zamienia się w tło i przestaje cokolwiek znaczyć.
            N.WYSLANE_FILE.write_text("{}")
            N.wyslij = lambda *a, **k: True
            sprawdz(N.main() == 0,
                    "udane wysyłki kończą się zerem, bez fałszywego alarmu")
    finally:
        N.wyslij, N.WYSLANE_FILE, N.BEST_CHAT_ID = stary_wyslij, stary_plik, stary_chat
        N.czy_zyje, N.T.load_seen, N.time.sleep = stary_zyje, stary_seen, stary_sleep


# PRZYCISK PEŁNEJ OFERTY POD WIADOMOŚCIĄ NA KANALE (18.09.2026).
# Właściciel: "mialo byc na bestdealhawku ponizej powiadomienia przycisk do
# skopiowania oferty, nie ma". Kliknięcia z tego kanału trafiają do kolejki
# JEGO bota, której `tracker` nie czyta - więc przycisk musi być obsłużony tutaj.
def test_przycisk_oferty_pod_wiadomoscia_na_kanale():
    import oferta as O
    kl = N.klawiatura_pod_oferta("3515700088")
    plaskie = [b for rzad in kl["inline_keyboard"] for b in rzad]
    dane = [b["callback_data"] for b in plaskie]
    sprawdz(any(d.startswith("of|") for d in dane), "jest przycisk pełnej oferty")
    sprawdz(sum(1 for d in dane if d.startswith("zl|")) == len(N.POWODY_ODRZUTU),
            "przyciski odrzutu nietknięte")
    # KLUCZ OBNIŻKI MA OGON "@cena" - z nim komenda odbiłaby się o walidację.
    kl2 = N.klawiatura_pod_oferta("3511112265@1200")
    of2 = [b["callback_data"] for rzad in kl2["inline_keyboard"] for b in rzad
           if b["callback_data"].startswith("of|")]
    sprawdz(of2 == ["of|3511112265"], f"klucz przeceny obcięty do numeru ({of2})")
    sprawdz(any(b["callback_data"] == "zl|3511112265@1200|zuzyty"
                for rzad in kl2["inline_keyboard"] for b in rzad),
            "odrzut zostaje przy PEŁNYM kluczu, bo oznacza konkretną wiadomość")
    # OBIEG ZAMKNIĘTY: przycisk → komenda → ten sam numer.
    cmd = O.komenda_z_przycisku(of2[0])
    sprawdz(O.parse_oferta_command(cmd)[0] == "3511112265",
            "przycisk → komenda → ten sam numer ogłoszenia")


def test_komenda_oferta_dziala_na_kanale():
    odpowiedzi = []
    stary_wyslij = N.wyslij
    try:
        N.wyslij = lambda t, chat_id=None, **k: odpowiedzi.append(t) or True
        N.obsluz_komende("/oferta 9999999999", "123")
        sprawdz(odpowiedzi and "Nie mam ogłoszenia" in odpowiedzi[0],
                "kanał odpowiada na /oferta, a nie odsyła do drugiego bota")
        odpowiedzi.clear()
        N.obsluz_komende("/cokolwiek", "123")
        sprawdz(odpowiedzi and "oferta" in odpowiedzi[0].lower(),
                "pomoc wymienia nową komendę")
    finally:
        N.wyslij = stary_wyslij


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
            N.wyslij = lambda t, chat_id=None, klawiatura=None: wys.append(t) or True
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
            N.wyslij = lambda tekst, chat_id=None, klawiatura=None: wys.append(tekst) or True
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


# === POPRAWKI PO PIERWSZYM DNIU PRACY KANAŁU (09.09.2026) ===================
# Właściciel: "przyszło coś fajnie bo poniżej ceny średniej rynkowej ale to
# jest złom totalnie zużyty (...) interesują nas nowo dodane topowe wersje".
# Rower, o którym mowa: "CUBE Stereo Hybrid 160 HPC SL 625" za 1 500 €,
# bez przebiegu i bez rocznika w ogłoszeniu.

# Najczęstszy powód, dla którego rower jest bardzo tani, to ZUŻYCIE. Kiedy nie
# znamy ani przebiegu, ani rocznika, nie da się tego wykluczyć, więc niska
# cena mówi "nie wiem", a nie "okazja". Zmierzone: 17 z 82 wyborów (21%)
# stało wyłącznie na cenie przy zerowej wiedzy o stanie.
def test_tania_oferta_bez_wiedzy_o_stanie_nie_wchodzi():
    top = _topowe(wpis("cube", "stereo one44", "szczyt",
                       wz=r"stereo[\s\S]{0,24}?one44(?![a-z0-9])"))
    por = {"cube stereo one44": {"ceny": list(range(3000, 5000, 100)),
                                 "km": list(range(200, 2200, 100)),
                                 "lata": []}}
    slepy = {"title": "Cube Stereo Hybrid ONE44 HPC", "price_num": 1500,
             "price": "1.500 €"}                     # ani przebiegu, ani rocznika
    wchodzi, powody, _ = N.ocen(slepy, top, por)
    sprawdz(not any(p["kod"] == "tanio" for p in powody),
            "sama niska cena przy nieznanym stanie NIE jest powodem")
    # z przebiegiem ta sama cena jest już dowodem okazji
    znany = dict(slepy, mileage_num=400)
    sprawdz(any(p["kod"] == "tanio" for p in N.ocen(znany, top, por)[1]),
            "ta sama cena PRZY ZNANYM przebiegu liczy się normalnie")


# "Górna półka" to modele pospolite (sam Cube Stereo 160 ma 328 sztuk na
# rynku), więc samo bycie nim niczego nie dowodzi. Wszystkie oferty, które
# właściciel uznał pierwszego dnia za dobre, były ze "szczytu" albo "wysokiej";
# złom przyszedł z "górnej półki".
def test_gorna_polka_nie_wpuszcza_sama():
    top = _topowe(wpis("cube", "stereo 160", "gorna_polka",
                       wz=r"stereo[\s\S]{0,24}?160(?![a-z0-9])"))
    por = {"cube stereo 160": {"ceny": list(range(2000, 3000, 50)),
                               "km": list(range(200, 2200, 100)), "lata": []}}
    zlom = {"title": "CUBE Stereo Hybrid 160 HPC SL 625", "price_num": 1500,
            "price": "1.500 €", "mileage_num": 900}
    sprawdz(not N.ocen(zlom, top, por)[0],
            "górna półka plus sama niska cena to za mało")


# "Interesują nas nowo dodane topowe wersje" - rocznik z bieżącej generacji
# wnosi własną wagę. Granica jest LICZONA od dzisiaj, nie wpisana na sztywno,
# bo inaczej za dwa lata plik chwaliłby rowery czteroletnie.
def test_swieza_generacja_liczy_sie_jako_powod():
    top = _topowe(wpis("scott", "patron", "wysoka", wz=r"patron(?![a-z0-9])"))
    por = {"scott patron": {"ceny": list(range(3000, 5000, 100)),
                            "km": [], "lata": []}}
    nowy = {"title": "Scott Patron eRide 920", "price_num": 4000,
            "price": "4.000 €", "year": N.NOWY_ROCZNIK_OD}
    stary = dict(nowy, year=N.NOWY_ROCZNIK_OD - 3)
    sprawdz(N.ocen(nowy, top, por)[0], "świeża generacja plus wysokie piętro wchodzi")
    sprawdz(not N.ocen(stary, top, por)[0], "ta sama oferta ze starym rocznikiem nie")
    sprawdz(N.NOWY_ROCZNIK_OD >= 2024,
            f"granica świeżości liczona od dzisiaj (jest {N.NOWY_ROCZNIK_OD})")


# 2,6% ofert nie ma ceny w ogóle ("VB", "brak ceny"). Właściciel dostał
# pierwszego dnia wiadomość z nagłówkiem "kupno VB", co w kanale o okazjach
# cenowych jest gorsze niż przyznanie się do niewiedzy.
def test_brak_ceny_jest_powiedziany_wprost():
    oferta = {"title": "Cube Stereo Hybrid ONE44", "price": "VB",
              "price_num": None, "url": "http://x"}
    m = N.zbuduj_wiadomosc(oferta, [{"kod": "model_szczyt", "tekst": "t", "waga": 2}])
    sprawdz("kupno VB" not in m, "nie wypisujemy 'kupno VB'")
    sprawdz("nie podał ceny" in m, "mówimy wprost, że ceny nie ma")
    normalna = {"title": "x", "price": "2.500 € VB", "price_num": 2500, "url": "http://x"}
    sprawdz("kupno 2.500 € VB" in N.zbuduj_wiadomosc(normalna, []),
            "prawdziwa cena dalej wyświetla się normalnie")


# TRZECIE WCIELENIE TEJ SAMEJ PUŁAPKI (12.09.2026). Weszło tędy
# "Specialized Levo Turbo 29 Zoll Gr. M" za 950 € - bez rocznika, bez
# przebiegu, wyłącznie na tym, że "Levo" jest na liście topowych modeli.
# Piętro "szczyt" wnosi wagę 2, więc wpuszczało SAMO, omijając warunek
# postawiony wcześniej przy cenie. Reguła jest teraz wspólna dla WSZYSTKICH
# dróg wejścia, nie doklejana do pojedynczych powodów.
def test_bez_zadnego_faktu_o_stanie_nic_nie_wchodzi():
    top = _topowe(wpis("specialized", "levo", "szczyt", wz=r"levo(?![a-z0-9])"))
    por = {"specialized levo": {"ceny": list(range(2000, 4000, 100)),
                                "km": [], "lata": []}}
    slepy = {"title": "Specialized Levo Turbo 29 Zoll Gr. M", "price_num": 950,
             "price": "950 € VB"}                 # ani rocznika, ani przebiegu
    wchodzi, powody, weta = N.ocen(slepy, top, por)
    sprawdz(not wchodzi, "sam topowy model bez ŻADNEGO faktu o stanie nie wchodzi")
    sprawdz(any("rocznik" in w for w in weta),
            f"weto mówi wprost, czego nie wiemy (dostałem {weta})")
    sprawdz(N.ocen(dict(slepy, mileage_num=600), top, por)[0],
            "ten sam rower ZE ZNANYM przebiegiem wchodzi normalnie")


# MOJA WŁASNA NADGORLIWOŚĆ z 09.09.2026, cofnięta 12.09. Odebrałem wtedy
# "górnej półce" wagę w całości na podstawie SZEŚCIU obserwacji z pierwszego
# dnia. Sześć obserwacji to szum. Koszt, zmierzony na 298 ofertach z czterech
# dni: kanał przestał wysyłać m.in. Cube Stereo Hybrid 160 HPC SLX 750
# z rocznika 2026 za 2 899 €, czyli ofertę, po jaką ten kanał powstał.
# Tych dwóch rowerów nie dzieliło piętro, tylko ROCZNIK.
def test_gorna_polka_z_biezacej_generacji_wchodzi():
    top = _topowe(wpis("cube", "stereo 160", "gorna_polka",
                       wz=r"stereo[\s\S]{0,24}?160(?![a-z0-9])"))
    # Przebiegi grupy DOBRANE Z ROZMYSLEM: 948 i 990 km leza w srodku
    # rozkladu, wiec ani jeden, ani drugi rower nie dostaje punktu za niski
    # przebieg. Dzieki temu test sprawdza dokladnie to, co ma - sam ROCZNIK -
    # a nie przypadkowy drugi sygnal. Pierwsza wersja atrapy miala przebiegi
    # od 500 km i zlom lapal sie w dolnym kwartylu, wiec przechodzil.
    por = {"cube stereo 160": {"ceny": list(range(2000, 3000, 50)),
                               "km": list(range(100, 2100, 100)), "lata": []}}
    swiezy = {"title": "CUBE Stereo Hybrid 160 HPC SLX 750", "price_num": 2899,
              "price": "2.899 €", "year": N.NOWY_ROCZNIK_OD, "mileage_num": 948}
    stary = {"title": "Cube Stereo Hybrid 160 HPC SL 625", "price_num": 800,
             "price": "800 €", "mileage_num": 990}      # bez rocznika
    sprawdz(N.ocen(swiezy, top, por)[0],
            "pospolity model Z BIEŻĄCEJ generacji wchodzi")
    sprawdz(not N.ocen(stary, top, por)[0],
            "ten sam model bez rocznika i bardzo tani NIE wchodzi")


# SAMA NAZWA TOPOWEGO MODELU NIE WYSTARCZA PRZY STARYM ROWERZE.
# Właściciel przysłał konkret (12.09.2026): "Specialized Levo women Gr. S von
# 2018, Motor neu" za 1 450 €, wysłane wyłącznie na powodzie `model_szczyt`.
# Ośmioletni rower, bez przebiegu, z WYMIENIONYM silnikiem (oryginalny padł),
# wersja damska rozmiar S. Piętro mówi, JAKIM modelem rower jest, nie w jakim
# jest stanie.
def test_topowy_model_ze_starego_rocznika_nie_wchodzi_sam():
    top = _topowe(wpis("specialized", "levo", "szczyt", wz=r"levo(?![a-z0-9])"))
    # `lata` MUSZĄ być wypełnione: bez nich nie działa mechanizm, który
    # unieważnia argument "tanio", gdy niską cenę tłumaczy stary rocznik.
    # Pierwsza wersja atrapy miała pustą listę i test przechodził odwrotnie,
    # niż w produkcji.
    por = {"specialized levo": {"ceny": list(range(2000, 4000, 100)),
                                "km": [],
                                "lata": [N.NOWY_ROCZNIK_OD] * 20}}
    stary = {"title": "Specialized Levo women Gr. S von 2018, Motor neu",
             "price_num": 1450, "price": "1.450 €", "year": 2018}
    swiezy = dict(stary, year=N.NOWY_ROCZNIK_OD,
                  title="Specialized Levo Comp Carbon")
    sprawdz(not N.ocen(stary, top, por)[0],
            "topowy model z 2018 nie wchodzi na samej nazwie")
    sprawdz(N.ocen(swiezy, top, por)[0],
            "ten sam model z bieżącej generacji wchodzi")


# "Wysylasz o 20 ogloszenie ktore jest usuniete" (12.09.2026). Wiadomość
# o rowerze, którego już nie ma, kosztuje zaufanie do całego kanału.
# 'nieznane' NIE blokuje wysyłki: nieudany odczyt to awaria sieci, a nie
# dowód zniknięcia - ta sama zasada co w dozorca_de.ocen_strone.
def test_zdjete_ogloszenie_nie_idzie_na_kanal():
    sprawdz(N.czy_zyje(None) == "nieznane", "brak adresu to 'nieznane'")
    sprawdz(N.czy_zyje("https://www.willhaben.at/iad/x") == "nieznane",
            "willhaben ma inny układ strony, nie zgadujemy")
    wys = []
    st = (N.wyslij, N.czy_zyje, N.WYSLANE_FILE, N.BEST_CHAT_ID, N.T.load_seen)
    try:
        with tempfile.TemporaryDirectory() as d:
            dzis = date.today().isoformat()
            N.wyslij = lambda tekst, chat_id=None, klawiatura=None: wys.append(tekst) or True
            N.BEST_CHAT_ID = "-1001"
            N.WYSLANE_FILE = Path(d) / "w.json"
            N.WYSLANE_FILE.write_text("{}")
            N.T.load_seen = lambda: {"X": {
                "title": "Cube Stereo Hybrid ONE44 HPC", "price": "2.500 €",
                "price_num": 2500, "mileage_num": 300, "year": N.NOWY_ROCZNIK_OD,
                "score": 90, "date": dzis, "url": "https://www.kleinanzeigen.de/s-anzeige/x/1"}}
            N.czy_zyje = lambda url: "zdjete"
            N.main()
            sprawdz(not wys, "zdjęte ogłoszenie nie zostało wysłane")
            sprawdz("X" in N.load_wyslane(),
                    "zapisane jako załatwione, żeby nie wracało co bieg")
    finally:
        (N.wyslij, N.czy_zyje, N.WYSLANE_FILE,
         N.BEST_CHAT_ID, N.T.load_seen) = st


# ROZMIAR RAMY (12.09.2026). Właściciel: "wypierdol S size w ogóle, według
# mnie to jest niesprzedawalne, M też średnio ale niech będzie, głównie chodzi
# o L". To wiedza o polskim rynku ZBYTU, nie o rowerze: rama S stoi miesiącami,
# więc zysk na papierze nic nie znaczy.
def test_rama_s_nie_wchodzi_a_l_dostaje_premie():
    top = _topowe(wpis("cube", "stereo one44", "szczyt",
                       wz=r"stereo[\s\S]{0,24}?one44(?![a-z0-9])"))
    por = {"cube stereo one44": {"ceny": list(range(3000, 5000, 100)),
                                 "km": list(range(200, 2200, 100)), "lata": []}}
    baza = {"title": "Cube Stereo Hybrid ONE44 HPC", "price_num": 4000,
            "price": "4.000 €", "year": N.NOWY_ROCZNIK_OD, "mileage_num": 900}
    mala = dict(baza, rama="S")
    wchodzi, _, weta = N.ocen(mala, top, por)
    sprawdz(not wchodzi, "rama S nie wchodzi, choćby reszta była idealna")
    sprawdz(any("S" in w for w in weta), f"weto nazywa powód (dostałem {weta})")
    sprawdz(not N.ocen(dict(baza, rama="XS"), top, por)[0], "XS też nie")
    sprawdz(N.ocen(dict(baza, rama="M"), top, por)[0], "M przechodzi")
    duza = N.ocen(dict(baza, rama="L / 50 cm"), top, por)
    sprawdz(duza[0] and any(p["kod"] == "rama" for p in duza[1]),
            "L przechodzi I dostaje własny powód")


# Rozmiar czytany z POLA `rama` (tracker zapisuje je z tytułu I opisu), a nie
# z samego tytułu. Zmierzone: z tytułu rozmiar da się odczytać w 14% ofert.
# Centymetry zostają jako "nie wiem" z rozmysłu - ten sam numer znaczy co
# innego u Cube'a i u Specialized, a pomyłka kosztuje tu dobry rower.
def test_skad_czytany_jest_rozmiar():
    sprawdz(N.rozmiar_ramy({"rama": "L / 50 cm"}) == "L", "litera z pola `rama`")
    sprawdz(N.rozmiar_ramy({"rama": "47 cm"}) is None,
            "same centymetry to 'nie wiem', nie zgadujemy")
    sprawdz(N.rozmiar_ramy({"title": "Cube Stereo Gr. XS Fully"}) == "XS",
            "zapas: litera z tytułu, gdy pola nie ma")
    sprawdz(N.rozmiar_ramy({"title": "Cube Stereo Hybrid 160"}) is None,
            "brak rozmiaru to None, a nie domysł")


# PRZYCISK "TO SZROT" (13.09.2026). Przez tydzień poprawiałem regułę cztery
# razy i za każdym razem właściciel musiał przysłać LINK, a dwa razy i tak
# trafiłem obok. Jedno kliknięcie w chwili, gdy jest wkurzony, jest oznaczonym
# przykładem, a nie anegdotą.
def test_przyciski_odrzutu():
    k = N.klawiatura_odrzutu("3509028603")
    guziki = [b for rzad in k["inline_keyboard"] for b in rzad]
    sprawdz(len(guziki) == len(N.POWODY_ODRZUTU),
            "każdy powód ma swój przycisk")
    sprawdz(all(len(b["callback_data"].encode()) <= 64 for b in guziki),
            "callback_data mieści się w limicie Telegrama (64 bajty)")
    sprawdz(all(b["callback_data"].startswith("zl|3509028603|") for b in guziki),
            "w danych przycisku siedzi id ogłoszenia, żeby dało się je potem skleić")


# ODPYTUJEMY WYŁĄCZNIE WŁASNEGO BOTA. Gdyby kanał chodził na tokenie
# DealHawka, dwa procesy czytałyby tę samą kolejkę getUpdates z przesuwanym
# wskaźnikiem, a Telegram po odczycie kasuje starsze wpisy - więc raz jeden,
# raz drugi gubiłby zdarzenia, losowo i po cichu.
def test_nie_scigamy_sie_z_dealhawkiem_o_zdarzenia():
    st = N.BEST_BOT_TOKEN
    try:
        N.BEST_BOT_TOKEN = N.T.TELEGRAM_BOT_TOKEN
        sprawdz(N.czytaj_odrzuty({}) == 0,
                "przy WSPÓLNYM tokenie nie odpytujemy wcale")
        N.BEST_BOT_TOKEN = None
        sprawdz(N.czytaj_odrzuty({}) == 0, "bez tokenu też nie")
    finally:
        N.BEST_BOT_TOKEN = st


# WIADOMOŚĆ DO BOTA KANAŁU NIE MOŻE ZNIKAĆ (13.09.2026). Właściciel napisał
# "/dojrzale" do bota o nazwie BestDealHawk - naturalny odruch - a kod po
# cichu ją połknął i przesunął wskaźnik, bo szukał wyłącznie kliknięć
# w przyciski. Zgłosił to słowami "napisalem do dealhawka bez reakcji".
# Rozpoznane po tym, że `best_offset.json` drgnął, a `telegram_offset.json`
# DealHawka stał od godziny.
def test_bot_kanalu_odpowiada_na_komendy():
    wys = []
    st = N.wyslij
    try:
        N.wyslij = lambda t, chat_id=None, klawiatura=None: wys.append((chat_id, t)) or True
        N.obsluz_komende("/dojrzale", 999)
        sprawdz(len(wys) == 1 and wys[0][0] == 999,
                "odpowiedź leci do TEGO czatu, z którego przyszła komenda")
        sprawdz("schodzi z ceny" in wys[0][1] or "Nikt teraz" in wys[0][1],
                "/dojrzale działa też na bocie kanału")
        wys.clear()
        N.obsluz_komende("/dojrzałe", 999)
        sprawdz(wys, "polskie 'ł' rozumiane także tutaj")
        wys.clear()
        N.obsluz_komende("/cosbezsensu", 999)
        sprawdz(len(wys) == 1 and "dojrzale" in wys[0][1],
                "nieznana komenda ODPOWIADA i mówi, co ten bot umie")
    finally:
        N.wyslij = st


# KLIKNIĘCIE POD OBNIŻKĄ ZAPISYWAŁO SIĘ PUSTE (15.09.2026). Obniżka ma klucz
# "id@cena", a `seen.json` trzyma samo id, więc oba pierwsze kliknięcia
# właściciela trafiły do odrzuty.jsonl bez tytułu, ceny i rocznika.
def test_klikniecie_pod_obnizka_ma_komplet_kontekstu():
    wpisy = []
    st = (N.BEST_BOT_TOKEN, N._api, N.ODRZUTY_FILE, N.OFFSET_FILE)
    try:
        with tempfile.TemporaryDirectory() as d:
            N.BEST_BOT_TOKEN = "osobny-token-testowy"
            N.ODRZUTY_FILE = Path(d) / "odrzuty.jsonl"
            N.OFFSET_FILE = Path(d) / "off.json"
            N._api = lambda metoda, **kw: ({"ok": True, "result": [{
                "update_id": 1, "callback_query": {
                    "id": "q", "data": "zl|3511112265@1200|zuzyty"}}]}
                if metoda == "getUpdates" else {"ok": True})
            seen = {"3511112265": {"title": "Cube Stereo Hybrid 140",
                                   "price_num": 1200, "year": 2021}}
            N.czytaj_odrzuty(seen)
            wpisy = [json.loads(l) for l in N.ODRZUTY_FILE.read_text().splitlines()]
    finally:
        N.BEST_BOT_TOKEN, N._api, N.ODRZUTY_FILE, N.OFFSET_FILE = st
    sprawdz(len(wpisy) == 1, "kliknięcie zapisane")
    sprawdz(wpisy and wpisy[0].get("title") == "Cube Stereo Hybrid 140",
            "kliknięcie pod OBNIŻKĄ ma tytuł, a nie puste pola")
    sprawdz(wpisy and wpisy[0].get("year") == 2021, "i rocznik")


# DRABINKA SPECIALIZED (15.09.2026). Właściciel: "podaj hierarchię modeli
# speca, te absolutnie topowe muszą się znaleźć w powiadomieniach". Ogólny
# generator tego nie łapał: ucinał "S-" z "S-Works", sklejał Comp z Comp Alloy
# i nie widział generacji, a generacja decyduje (Gen 4 S-Works 11 594 € wobec
# Gen 3 5 599 €).
def test_drabinka_wersji_specialized():
    przypadki = [
        ("Specialized S-Works Turbo Levo Gen 4", "szczyt"),
        ("Specialized Turbo Levo Pro Carbon 2023", "szczyt"),
        ("Specialized Turbo Levo Expert Gen 3", "wysoka"),
        ("Specialized Turbo Levo 3 Comp Alloy 700Wh", "gorna_polka"),
        ("Specialized Turbo Levo Comp Carbon 2024", "gorna_polka"),
        ("Specialized Turbo Levo Alloy Gen 3", None),
        ("Specialized Levo Expert FSR 6Fattie", None),     # Gen 1 - za stary
    ]
    for tytul, oczek in przypadki:
        w = N.pietro_specialized(tytul)
        sprawdz((w or {}).get("pietro") == oczek,
                f"{tytul[:40]} -> {oczek} (dostałem {(w or {}).get('pietro')})")


# PUŁAPKA KOLEJNOŚCI: "Comp Alloy" zawiera słowo "comp". Sprawdzany w złej
# kolejności wpadałby do Comp na karbonie, który w obu generacjach stoi wyżej
# (Gen 3: 3 699 € wobec 3 099 €).
def test_comp_alloy_to_nie_comp():
    sprawdz(N.specialized("Specialized Turbo Levo 3 Comp Alloy")[1] == "Comp Alloy",
            "Comp Alloy rozpoznany jako Comp Alloy, nie jako Comp")
    sprawdz(N.specialized("Specialized Turbo Levo Comp 2023")[1] == "Comp",
            "zwykły Comp dalej jako Comp")


# Specialized pisze rozmiary S1-S6, nie literami. Bez przeliczenia rama L
# (S4, 138 tytułów Levo) nie dostawała premii, a rama S (S2, 25 tytułów)
# prześlizgiwała się przez weto.
def test_rozmiary_specialized():
    sprawdz(N.rozmiar_ramy({"title": "Specialized Turbo Levo Expert S4"}) == "L",
            "S4 u Specialized to L")
    sprawdz(N.rozmiar_ramy({"title": "Specialized Turbo Levo Comp S2"}) == "S",
            "S2 u Specialized to S, więc łapie się na weto")
    sprawdz(N.rozmiar_ramy({"title": "Cube Stereo Hybrid S4 Race"}) != "L",
            "S4 poza Specialized NIE jest tłumaczone na L")


# WYSOKI PRZEBIEG TŁUMACZY NISKĄ CENĘ (17.09.2026). Właściciel oznaczył
# "zużyty": Cube Stereo Hybrid 160 SL, rama L, 2 668 km, 1 700 €. Wchodził,
# bo był tani, a przebieg był ZNANY - tyle że znany i zły. Test sprawdza obie
# strony: ten sam rower z niskim przebiegiem dalej dostaje argument "tanio".
def test_wysoki_przebieg_uniewaznia_argument_ceny():
    top = _topowe(wpis("cube", "stereo 160", "gorna_polka",
                       wz=r"stereo[\s\S]{0,24}?160(?![a-z0-9])"))
    por = {"cube stereo 160": {"ceny": list(range(2000, 3000, 50)),
                               "km": list(range(100, 2100, 100)), "lata": []},
           "__wszystkie__": {"ceny": [], "km": list(range(100, 2100, 100)), "lata": []}}
    zuzyty = {"title": "Cube Stereo Hybrid 160 SL", "price_num": 1700,
              "price": "1.700 €", "mileage_num": 2668, "rama": "L"}
    wchodzi, powody, _ = N.ocen(zuzyty, top, por)
    sprawdz(not any(p["kod"] == "tanio" for p in powody),
            "przy przebiegu w górnym kwartylu niska cena NIE jest argumentem")
    sprawdz(not wchodzi, "zużyty rower nie wchodzi na samej premii za ramę L")
    swiezy = dict(zuzyty, mileage_num=300)
    sprawdz(any(p["kod"] == "tanio" for p in N.ocen(swiezy, top, por)[1]),
            "ta sama cena przy niskim przebiegu dalej jest argumentem")


# "Dużo jak na nowy model" to nie "zajechany". Złapane przez istniejący test
# obniżek: Cube ONE44 z 800 km wypadał w górnym kwartylu swojej grupy (654 km),
# bo ONE44 to rowery prawie nowe - i weto odbierało mu argument ceny.
def test_przebieg_wysoki_tylko_na_tle_modelu_nie_jest_zuzyciem():
    top = _topowe(wpis("cube", "stereo one44", "szczyt",
                       wz=r"stereo[\s\S]{0,24}?one44(?![a-z0-9])"))
    por = {"cube stereo one44": {"ceny": list(range(3000, 5000, 100)),
                                 "km": list(range(50, 1050, 50)), "lata": []},
           "__wszystkie__": {"ceny": [], "km": list(range(100, 2100, 100)), "lata": []}}
    one44 = {"title": "Cube Stereo Hybrid ONE44 HPC", "price_num": 2100,
             "price": "2.100 €", "mileage_num": 800}
    sprawdz(any(p["kod"] == "tanio" for p in N.ocen(one44, top, por)[1]),
            "ONE44 z 800 km zachowuje argument ceny, choć to dużo jak na ONE44")


# Zapas dla weta, gdy grupa jest za mała: rozkład wszystkich grup. Działa
# WYŁĄCZNIE w stronę zaostrzenia - premii za niski przebieg nie daje.
def test_zapas_dla_weta_przebiegu_tylko_zaostrza():
    top = _topowe(wpis("cube", "stereo 160", "gorna_polka",
                       wz=r"stereo[\s\S]{0,24}?160(?![a-z0-9])"))
    por = {"cube stereo 160": {"ceny": list(range(2000, 3000, 50)), "km": [], "lata": []},
           "__wszystkie__": {"ceny": [], "km": list(range(100, 2100, 100)), "lata": []}}
    zuzyty = {"title": "Cube Stereo Hybrid 160", "price_num": 1700, "mileage_num": 2668}
    sprawdz(not any(p["kod"] == "tanio" for p in N.ocen(zuzyty, top, por)[1]),
            "mała grupa: wysoki przebieg sprawdzany na tle wszystkich")
    nowy = dict(zuzyty, mileage_num=150)
    sprawdz(not any(p["kod"] == "przebieg" for p in N.ocen(nowy, top, por)[1]),
            "zapas NIE daje premii za niski przebieg (to luzowałoby kryterium)")


# PRZEBIEGI Z OPISÓW (17.09.2026). Dziennik rynku zapisuje przebieg tylko
# z tytułu. Przy Levo Comp Alloy Gen 3 z 577 km grupa miała DWA przebiegi
# i sygnał milczał. seen.json trzyma przebieg z całej strony ogłoszenia.
def test_grupa_bierze_przebiegi_z_opisow():
    top = _topowe(wpis("cube", "stereo 160", "gorna_polka",
                       wz=r"stereo[\s\S]{0,24}?160(?![a-z0-9])"))
    dzis = date.today()
    with tempfile.TemporaryDirectory() as d:
        plik = Path(d) / "m.jsonl"
        polka = next(iter(N.T.NAZWY_POLEK))
        with plik.open("w", encoding="utf-8") as f:
            for i in range(15):              # 15 rowerów, przebiegu w tytule brak
                f.write(json.dumps({"ts": dzis.isoformat(), "s": polka, "id": f"a{i}",
                                    "t": f"Cube Stereo Hybrid 160 Race nr{i}",
                                    "p": 2000 + i * 10}) + "\n")
        seen = {f"a{i}": {"title": f"Cube Stereo Hybrid 160 Race nr{i}",
                          "mileage_num": 500 + i * 50, "date": dzis.isoformat()}
                for i in range(15)}
        bez = N.zbuduj_porownanie(top, dzis=dzis, plik=plik)
        z = N.zbuduj_porownanie(top, dzis=dzis, plik=plik, seen=seen)
    sprawdz(len(bez["cube stereo 160"]["km"]) == 0,
            "bez seen.json grupa nie ma żadnego przebiegu (tak było)")
    sprawdz(len(z["cube stereo 160"]["km"]) == 15,
            "z seen.json grupa dostaje przebiegi odczytane z opisów")


# Reguła 7 w duchu: nagła powódź to awaria progu, nie hojny rynek.
def test_sufit_na_bieg():
    sprawdz(N.MAX_NA_BIEG <= 10,
            f"sufit wiadomości na bieg jest niski (jest {N.MAX_NA_BIEG})")


# GOŁY WKLEJONY LINK DZIAŁA TYLKO TUTAJ (19.09.2026). Właściciel wkleił sam
# adres ogłoszenia i nie stało się nic: `parse_oferta_command` żąda słowa
# "oferta" albo "/of" na POCZĄTKU. Komentarz w `tracker.py` twierdził przy
# tym, że komenda "odzywa się na wklejony link" - i był nieprawdą, czyli tą
# samą klasą wpadki co "komentarz opisujący zasadę to NIE jest zasada".
# Decyzja właściciela: "chce zeby to dzialalo tylko na bestdealhawku".
def test_goly_link_rozpoznany():
    import oferta as O
    ka = "https://www.kleinanzeigen.de/s-anzeige/cube-stereo/3517059558-217-2032"
    wh = "https://www.willhaben.at/iad/kaufen-und-verkaufen/d/e-bike-fully-1557351473/"
    sprawdz(O.komenda_z_linku(ka) == f"/oferta {ka}",
            "link Kleinanzeigen zamienia się na tę samą komendę, co wpisana palcem")
    sprawdz(O.komenda_z_linku(wh) == f"/oferta {wh}",
            "link willhaben tak samo")
    sprawdz(O.komenda_z_linku(f"zobacz {ka} co myslisz") == f"/oferta {ka}",
            "link w środku zdania też - właściciel pisze z telefonu")
    # GOŁA LICZBA TO NIE OGŁOSZENIE. W czacie bywa kwotą, a rozpoznanie jej
    # zamieniłoby każdą wpisaną cenę w wiadomość do obcego człowieka.
    sprawdz(O.komenda_z_linku("2200") is None, "goła kwota NIE jest linkiem")
    sprawdz(O.komenda_z_linku("3517059558") is None,
            "gołe dziesięć cyfr też nie - żądamy pełnego adresu")
    sprawdz(O.komenda_z_linku("https://www.kleinanzeigen.de/s-fahrraeder/c217") is None,
            "adres PÓŁKI to nie ogłoszenie")
    sprawdz(O.komenda_z_linku("https://www.olx.pl/oferta/rower-CID767-ID123") is None,
            "OLX to strona SPRZEDAŻY, nie ma do kogo pisać oferty kupna")
    sprawdz(O.komenda_z_linku("/oferta 3517059558") is None,
            "gotowa komenda nie przechodzi tędy drugi raz")
    sprawdz(O.komenda_z_linku("") is None and O.komenda_z_linku(None) is None,
            "pusty tekst nie wywraca się")


# CAŁA DROGA, NIE SAM ROZBIÓR. Rozpoznanie linku jest bezużyteczne, jeśli
# pętla czytająca kolejkę nadal odrzuca wiadomość bez ukośnika - a właśnie
# tak było do 19.09.2026 (`startswith("/")`). To ta sama klasa co "alarm
# działał, kompensacja nie" z 01.09: sprawdzać trzeba SKUTEK.
def test_goly_link_z_kolejki_kanalu_daje_odpowiedz():
    wys = []
    st = (N.BEST_BOT_TOKEN, N._api, N.ODRZUTY_FILE, N.OFFSET_FILE, N.wyslij)
    link = "https://www.kleinanzeigen.de/s-anzeige/cube/3517059558-217-2032"
    try:
        with tempfile.TemporaryDirectory() as d:
            N.BEST_BOT_TOKEN = "osobny-token-testowy"
            N.ODRZUTY_FILE = Path(d) / "odrzuty.jsonl"
            N.OFFSET_FILE = Path(d) / "off.json"
            N.wyslij = lambda t, chat_id=None, klawiatura=None: wys.append((chat_id, t)) or True
            N._api = lambda metoda, **kw: ({"ok": True, "result": [{
                "update_id": 1,
                "message": {"chat": {"id": 777}, "text": link}}]}
                if metoda == "getUpdates" else {"ok": True})
            N.czytaj_odrzuty({})
    finally:
        (N.BEST_BOT_TOKEN, N._api, N.ODRZUTY_FILE,
         N.OFFSET_FILE, N.wyslij) = st
    sprawdz(len(wys) == 1, f"goły link dostaje ODPOWIEDŹ (dostał {len(wys)})")
    sprawdz(wys and wys[0][0] == 777, "odpowiedź leci do tego czatu, z którego przyszła")


# ZWYKŁA ROZMOWA MA NADAL BYĆ POMIJANA. Poluzowanie warunku o ukośnik nie
# może zamienić kanału w automat odpowiadający na każde zdanie - inaczej
# każde "dzieki" wracałoby instrukcją obsługi.
def test_zwykly_tekst_na_kanale_nadal_pomijany():
    wys = []
    st = (N.BEST_BOT_TOKEN, N._api, N.ODRZUTY_FILE, N.OFFSET_FILE, N.wyslij)
    try:
        with tempfile.TemporaryDirectory() as d:
            N.BEST_BOT_TOKEN = "osobny-token-testowy"
            N.ODRZUTY_FILE = Path(d) / "odrzuty.jsonl"
            N.OFFSET_FILE = Path(d) / "off.json"
            N.wyslij = lambda t, chat_id=None, klawiatura=None: wys.append(t) or True
            N._api = lambda metoda, **kw: ({"ok": True, "result": [{
                "update_id": 1,
                "message": {"chat": {"id": 777}, "text": "dzieki, fajny rower"}}]}
                if metoda == "getUpdates" else {"ok": True})
            N.czytaj_odrzuty({})
    finally:
        (N.BEST_BOT_TOKEN, N._api, N.ODRZUTY_FILE,
         N.OFFSET_FILE, N.wyslij) = st
    sprawdz(not wys, f"zwykłe zdanie NIE dostaje odpowiedzi (dostało {len(wys)})")


# LINK BEZ "https://" (19.09.2026, DRUGA RUNDA). Pierwsza wersja wzorca
# żądała schematu na sztywno, właściciel wkleił adres bez niego i dostał
# ciszę: "wyslalem link i cisza bez reakcji". Telegram i tak rysuje
# "www.kleinanzeigen.de/..." jako klikalny odnośnik.
def test_link_bez_schematu():
    import oferta as O
    for adres in ("www.kleinanzeigen.de/s-anzeige/cube/3517059558-217-2032",
                  "kleinanzeigen.de/s-anzeige/cube/3517059558-217-2032",
                  "m.kleinanzeigen.de/s-anzeige/cube/3517059558-217-2032",
                  "www.willhaben.at/iad/kaufen-und-verkaufen/d/e-bike-1557351473/"):
        sprawdz(O.komenda_z_linku(adres) == f"/oferta {adres}",
                f"link bez https:// rozpoznany ({adres[:38]})")
    sprawdz(O.komenda_z_linku("dzieki, fajny rower") is None,
            "zwykłe zdanie nadal NIE jest linkiem")


# CISZA JEST GORSZA OD BŁĘDU. Zmierzone 19.09.2026: wskaźnik kolejki kanału
# przeskoczył o 1 o 20:21:34, czyli bot wiadomość PRZECZYTAŁ i sam postanowił
# nic nie robić. Właściciel zobaczył wyłącznie ciszę i nie miał jak odróżnić
# "nie zrozumiałem" od "bot padł" - ta sama wpadka co "napisalem i nic".
def test_nieudany_link_dostaje_odpowiedz_zamiast_ciszy():
    wys = []
    st = (N.BEST_BOT_TOKEN, N._api, N.ODRZUTY_FILE, N.OFFSET_FILE, N.wyslij)
    try:
        with tempfile.TemporaryDirectory() as d:
            N.BEST_BOT_TOKEN = "osobny-token-testowy"
            N.ODRZUTY_FILE = Path(d) / "odrzuty.jsonl"
            N.OFFSET_FILE = Path(d) / "off.json"
            N.wyslij = lambda t, chat_id=None, klawiatura=None: wys.append((chat_id, t)) or True
            N._api = lambda metoda, **kw: ({"ok": True, "result": [{
                "update_id": 1, "message": {"chat": {"id": 777},
                "text": "https://www.olx.pl/oferta/rower-CID767-ID123"}}]}
                if metoda == "getUpdates" else {"ok": True})
            N.czytaj_odrzuty({})
    finally:
        (N.BEST_BOT_TOKEN, N._api, N.ODRZUTY_FILE,
         N.OFFSET_FILE, N.wyslij) = st
    sprawdz(len(wys) == 1, f"link, z którego nic nie wyjęliśmy, DOSTAJE odpowiedź (dostał {len(wys)})")
    sprawdz(wys and "numeru ogłoszenia" in wys[0][1],
            "odpowiedź mówi, czego zabrakło, a nie tylko 'nie rozumiem'")
    sprawdz(wys and wys[0][0] == 777, "i leci do tego czatu, z którego przyszła")


# WPIS Z KANAŁU TO TEŻ WIADOMOŚĆ (20.09.2026). `tracker` czyta
# `message` ORAZ `channel_post` od dawna, a ten czytnik znał tylko pierwszy -
# więc wpis z kanału przesuwałby wskaźnik kolejki i przepadał bez śladu.
# Dwie kopie tej samej reguły rozjechały się przy pierwszej poprawce.
def test_wpis_z_kanalu_czytany_tak_samo():
    wys = []
    st = (N.BEST_BOT_TOKEN, N._api, N.ODRZUTY_FILE, N.OFFSET_FILE, N.wyslij)
    link = "https://www.kleinanzeigen.de/s-anzeige/cube/3517059558-217-2032"
    try:
        with tempfile.TemporaryDirectory() as d:
            N.BEST_BOT_TOKEN = "osobny-token-testowy"
            N.ODRZUTY_FILE = Path(d) / "odrzuty.jsonl"
            N.OFFSET_FILE = Path(d) / "off.json"
            N.wyslij = lambda t, chat_id=None, klawiatura=None: wys.append((chat_id, t)) or True
            N._api = lambda metoda, **kw: ({"ok": True, "result": [{
                "update_id": 1,
                "channel_post": {"chat": {"id": 555}, "text": link}}]}
                if metoda == "getUpdates" else {"ok": True})
            N.czytaj_odrzuty({})
    finally:
        (N.BEST_BOT_TOKEN, N._api, N.ODRZUTY_FILE,
         N.OFFSET_FILE, N.wyslij) = st
    sprawdz(len(wys) == 1, f"link wklejony NA KANALE dostaje odpowiedź (dostał {len(wys)})")
    sprawdz(wys and wys[0][0] == 555, "odpowiedź leci do tego kanału")


# OBA CZYTNIKI MUSZĄ ZNAĆ TE SAME TYPY. Rozjazd tutaj jest niewidoczny:
# wpis przesuwa wskaźnik i przepada, a w logu nie ma ani słowa.
def test_oba_czytniki_znaja_channel_post():
    for plik in ("najlepsze.py", "tracker.py"):
        src = Path(plik).read_text(encoding="utf-8")
        sprawdz("channel_post" in src,
                f"{plik}: czyta także wpisy z kanału, nie tylko zwykłe wiadomości")


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
