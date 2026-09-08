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
import json
import logging
import os
import re
import statistics
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import requests

import tracker as T

log = logging.getLogger("najlepsze")

BEST_CHAT_ID = os.environ.get("TELEGRAM_BEST_CHAT_ID")
WYSLANE_FILE = Path("best_wyslane.json")
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


def pietro_modelu(tytul, topowe):
    """Wpis z topowe_modele.json dla tego roweru albo None.

    Trzy warunki naraz: marka roweru (ta pierwsza w tytule) zgadza sie
    z marka wpisu, model stoi PO marce i blisko niej.
    """
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
    return T.olx_query_for(tytul, None)


def zbuduj_porownanie(topowe, dzis=None, plik=None):
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
    plik = plik or T.MARKET_FILE
    po_id = {}
    try:
        with open(plik, encoding="utf-8") as f:
            for linia in f:
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
    for g in grupy.values():
        g["ceny"].sort()
        g["km"].sort()
        g["lata"].sort()
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
    cena_wytlumaczona = False
    if grupa and rok and len(grupa["lata"]) >= MIN_ROWEROW:
        mediana_lat = statistics.median(grupa["lata"])
        if rok < mediana_lat - 1:
            cena_wytlumaczona = True

    # --- POWÓD 1: topowy model swojej marki (lista właściciela, nie statystyka)
    wpis = pietro_modelu(tytul, topowe)
    if wpis:
        powody.append({
            "kod": "model_" + wpis["pietro"],
            "tekst": (f"{wpis['marka'].capitalize()} {wpis['model'].upper()} to "
                      f"{wpis['pietro'].replace('_', ' ')} tej marki "
                      f"(mediana modelu {_zl(wpis['mediana'])} €, "
                      f"{wpis['x_marka']}x mediana marki)"),
            "waga": 2 if wpis["pietro"] == "szczyt" else 1,
        })

    # --- POWÓD 2: tanio jak na swój model
    pc = percentyl(cena, grupa["ceny"]) if (grupa and cena) else None
    if pc is not None and pc <= PROG_TANIO and not cena_wytlumaczona:
        mediana = int(statistics.median(grupa["ceny"]))
        ile = int((mediana - cena) / mediana * 100) if mediana else 0
        powody.append({
            "kod": "tanio",
            "tekst": (f"{ile}% pod medianą swojego modelu "
                      f"({_zl(mediana)} €, {len(grupa['ceny'])} rowerów "
                      f"z {OKNO_DNI} dni)"),
            "waga": 1,
        })

    # --- POWÓD 3: niski przebieg jak na swój model
    pkm = percentyl(km, grupa["km"]) if (grupa and km is not None) else None
    if pkm is not None and pkm <= PROG_PRZEBIEG:
        mediana_km = int(statistics.median(grupa["km"]))
        powody.append({
            "kod": "przebieg",
            "tekst": f"{_zl(km)} km przy medianie modelu {_zl(mediana_km)} km",
            "waga": 1,
        })

    waga = sum(p["waga"] for p in powody)
    wchodzi = waga >= 2 and not weta
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

    naglowek = [f"kupno {oferta.get('price', '?')}"]
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


def wyslij(tekst, chat_id=None):
    """Wysyłka na DRUGI czat. Własna, bo `tracker.send_telegram` ma numer
    czatu wpisany na sztywno i nie wolno go przy okazji przestawić."""
    chat_id = chat_id or BEST_CHAT_ID
    if not chat_id:
        return False
    url = f"https://api.telegram.org/bot{T.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": tekst, "parse_mode": "HTML",
               "disable_web_page_preview": False}
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
    porownanie = zbuduj_porownanie(topowe)
    seen = T.load_seen()
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

    for i, (ad_id, v, powody) in enumerate(wybrane):
        if i:
            time.sleep(1.2)          # limit Telegrama ~1 wiadomość/s
        if wyslij(zbuduj_wiadomosc(v, powody)):
            wyslane[ad_id] = {"d": v.get("date"),
                              "powody": [p["kod"] for p in powody]}
            log.info(f"wysłane: {v.get('title','')[:60]}")

    save_wyslane(wyslane)
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
