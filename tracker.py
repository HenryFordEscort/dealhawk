import re
import os
import math
import json
import time
import html as html_mod
import logging
import statistics
import cloudscraper

# Wspólny klient OLX — ten sam plik obsługuje bota rowerowego i samochodowego,
# żeby poprawka trafiała od razu do obu. Boty NIE importują się nawzajem.
from olx import (OLX_HEADERS, OLX_RELAY_KEY, OLX_RELAY_URL, olx_diag,
                 olx_diag_reset, olx_get, parse_olx_cards, przekaznik_zyje,
                 zglos_pusta_strone)
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# Kleinanzeigen podaje czas wystawienia w czasie niemieckim, a runner GitHuba
# chodzi na UTC — bez przeliczenia każdy wiek ogłoszenia byłby o 2 h zawyżony.
try:
    from zoneinfo import ZoneInfo
    TZ_DE = ZoneInfo("Europe/Berlin")
except Exception:                       # brak bazy stref (goły obraz) — CEST
    TZ_DE = timezone(timedelta(hours=2))

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
# WYŁĄCZONE ŚWIADOMIE (23.08.2026): bot nie ma kosztować ani grosza.
# Czytanie przebiegu przez Claude Haiku kosztowało ~35 zł/mies. przy ~300
# ogłoszeniach dziennie, a na sprawdzonej próbce i tak pudłowało — Cannondale
# Moterra miał w opisie "10.328 km", model zwrócił null i rower po 10 tys. km
# przeszedł jako kandydat. Wzorce znajdują ten przebieg bez trudu.
# Aby wrócić: ustaw sekret ANTHROPIC_API_KEY w GitHubie i dopisz go do
# .github/workflows/tracker.yml — kod jest gotowy i nietknięty.
ANTHROPIC_API_KEY = os.environ.get("DEALHAWK_ANTHROPIC_KEY")

MIN_PRICE = 800
MAX_PRICE = 2500
MAX_MILEAGE = 3000

# --- TRYB PĘTLI (opcjonalny, domyślnie WYŁĄCZONY) --------------------------
# 0 = jeden skan i koniec — tak chodzi produkcja, bo tempo nadaje zewnętrzny
# wyzwalacz co 5 minut. Powyżej zera bieg żyje tyle minut i skanuje kanał sam
# co PETLA_ODSTEP_S; przydatne, gdyby kiedyś wyzwalacz zniknął i trzeba było
# zejść poniżej 5 minut. UWAGA: nie włączać razem z wyzwalaczem co 5 minut —
# przy cancel-in-progress każde wyzwolenie ubijałoby trwającą pętlę.
PETLA_MINUT = int(os.environ.get("DEALHAWK_PETLA_MINUT", "0"))
PETLA_ODSTEP_S = int(os.environ.get("DEALHAWK_ODSTEP_S", "60"))
KLUCZOWE_CO_MIN = 5    # 23 zapytania kluczowe są drogie — nie co minutę
PUSH_CO_MIN = 5        # jak często commitować seen.json bez powiadomień
_ostatni_push = 0.0

SKIP_KEYWORDS = [
    "defekt", "bastler", "ersatzteile", "ersatzteil", "rahmen only",
    "schlachtfest", "unfall", "unfallschaden", "wasserschaden",
    "ohne motor", "ohne akku", "motor defekt", "akku defekt",
    # nie-fully / miejskie
    "hardtail", "hartail", "trekking", "city bike", "citybike",
    "lastenrad", "lastenfahrrad", "cargo", "faltrad", "klapprad", "faltbar",
    "tiefeinsteiger", "tiefeinstieg", "cityrad", "cruiser", "gravel",
    # same ramy
    "frameset", "frame only", "nur rahmen",
]

# Krótkie/ryzykowne słowa — wymagają granicy słowa, żeby nie łapać
# "Rahmengröße", "Cross Country", nazw modeli itp.
SKIP_PATTERNS = [
    r'\bht\b',            # hardtail w skrócie
    r'\brahmen\b',        # sama rama (ale NIE Rahmengröße/Rahmenhöhe)
    r'\bcross\b(?![\s-]?country)',  # rower crossowy (ale NIE Cross-Country)
    r'\burban\b',
    r'\bcomfort\b',
    r'\btouring\b',
    r'\bxxl\b', r'\bxl\b',  # za duże ramy
]

# Jeśli tytuł ZACZYNA SIĘ od jednego z tych słów → sprzedaje część, nie cały rower
PART_TITLE_PREFIXES = [
    "motor", "akku", "gabel", "bremse", "kurbel", "kassette",
    "schaltwerk", "sattelstütze", "sattelstutze", "antrieb",
    "display", "ladegerät", "ladegerat", "ladekabel",
]

# Słowa które potwierdzają że to fully (wymagane dla ogólnych wyszukiwań)
FULLY_KEYWORDS = [
    "fully", "full suspension", "full-suspension", " fs ", "fs,", "fs)",
    "stereo hybrid", "levo", "rail", "powerfly", "strike", "patron",
    "genius", "macina lycan", "macina kapoho", "spectral", "torque",
    "nduro", "allmtn", "e-asx", "wild fs", "eone-sixty", "strive",
]

ELECTRIC_KEYWORDS = [
    # "e bike" bez granicy lapalo sie na "VerkaufE BIKE" i "MeinE BIKE" —
    # w niemieckich tytulach to nagminne. Odwrotnie: "eRide" pisane bez
    # lacznika (tak brandujе Scott) NIE bylo rozpoznawane wcale.
    r"\be[- ]bike\b", r"ebike", r"elektro", r"pedelec", r"bosch",
    r"shimano steps", r"yamaha", r"brose", r"fazua", r"\bakku\b", r"\bwh\b",
    r"\blevo\b", r"\btrek rail\b", r"powerfly", r"macina", r"\bstrike\b",
    r"\bpatron\b",
    # nazwy modeli które SĄ elektryczne z definicji (bez tego "Cube Stereo
    # Hybrid 120 Pro 625" bez słowa Wh/Bosch był błędnie odrzucany jako analog)
    r"stereo hybrid", r"kenevo", r"\be-mtb\b", r"\bemtb\b", r"e-mountainbike",
    # \b konieczne: bez niego "e fully" lapalo sie na "Mountainbik-E FULLY",
    # bo w niemieckim mnostwo slow konczy sie na "e". Tak przeszedl zwykly
    # Scott Ramson 600 (26 cali, bez silnika) jako rzekomy elektryk.
    r"\be[- ]fully\b", r"genius e-?ride", r"\be-?ride\b", r"\d{3}\s*wh",
]

# --- REGION Z KODU POCZTOWEGO ----------------------------------------------
# "28307 Osterholz" nic nie mówi o tym, gdzie po ten rower jechać. Niemiecki
# kod pocztowy niesie tę informację w dwóch pierwszych cyfrach, więc zamiast
# nazwy wsi pokazujemy land — i zaznaczamy te, które leżą przy naszej granicy,
# bo to jedyna rzecz zmieniająca decyzję o dojeździe.
_REGIONY = [
    (0, 1, "Saksonia"), (2, 2, "Saksonia (przy granicy)"),
    (3, 3, "Brandenburgia (przy granicy)"), (4, 5, "Saksonia"),
    (6, 6, "Saksonia-Anhalt"), (7, 7, "Turyngia"), (8, 9, "Saksonia"),
    (10, 14, "Berlin"), (15, 15, "Brandenburgia (przy granicy)"),
    (16, 16, "Brandenburgia"), (17, 17, "Meklemburgia (przy granicy)"),
    (18, 19, "Meklemburgia"), (20, 22, "Hamburg"), (23, 25, "Szlezwik-Holsztyn"),
    (26, 27, "Dolna Saksonia"), (28, 28, "Brema"), (29, 31, "Dolna Saksonia"),
    (32, 33, "Nadrenia Płn.-Westfalia"), (34, 36, "Hesja"),
    (37, 38, "Dolna Saksonia"), (39, 39, "Saksonia-Anhalt"),
    (40, 48, "Nadrenia Płn.-Westfalia"), (49, 49, "Dolna Saksonia"),
    (50, 53, "Nadrenia Płn.-Westfalia"), (54, 56, "Nadrenia-Palatynat"),
    (57, 59, "Nadrenia Płn.-Westfalia"), (60, 65, "Hesja"), (66, 66, "Saara"),
    (67, 67, "Nadrenia-Palatynat"), (68, 79, "Badenia-Wirtembergia"),
    (80, 87, "Bawaria"), (88, 88, "Badenia-Wirtembergia"),
    (89, 97, "Bawaria"), (98, 99, "Turyngia"),
]


def region_z_plz(loc: str):
    """Land z niemieckiego kodu pocztowego. None, gdy kodu nie ma.

    Granice landów nie pokrywają się idealnie z kodami, więc na styku dwóch
    landów wynik bywa przybliżony — ale rząd wielkości "jak daleko jechać"
    jest zawsze poprawny, a to jedyne, po co ta informacja tu jest."""
    m = re.match(r'\s*(\d{2})', loc or "")
    if not m:
        return None
    n = int(m.group(1))
    for lo, hi, nazwa in _REGIONY:
        if lo <= n <= hi:
            return nazwa
    return None


def is_fully(title: str) -> bool:
    t = title.lower()
    return any(kw in t for kw in FULLY_KEYWORDS)

def is_electric(title: str) -> bool:
    t = title.lower()
    return any(re.search(kw, t) for kw in ELECTRIC_KEYWORDS)

# Marki z wysokim resale value w Polsce — tylko te dostają powiadomienia.
# Niszowa marka przechodzi wyjątkowo, gdy cena jest mocno poniżej mediany.
# Canyon: nowsze modele (Strive:ON, Torque:ON od ~2023) mają Boscha —
# filtr silnika i tak odsiewa wersje na Shimano EP8
PREMIUM_BRANDS = ["cube", "trek", "specialized", "scott", "ktm", "canyon"]
NICHE_MIN_DISCOUNT_PCT = 30


def is_premium_brand(title: str) -> bool:
    t = title.lower()
    return any(b in t for b in PREMIUM_BRANDS)

# Słowa sugerujące dobry stan
GOOD_CONDITION = [
    "neuwertig", "wie neu", "kaum gefahren", "wenig gefahren",
    "top zustand", "sehr gut", "unbenutzt", "ovp", "originalverpackt",
]

def url(query):
    slug = query.replace(" ", "-")
    return f"https://www.kleinanzeigen.de/s-preis:{MIN_PRICE}:{MAX_PRICE}/{slug}/k0"

SEARCHES = [
    # --- Ogólne terminy na fully / e-mtb ---
    {"name": "e-bike fully",           "url": url("e-bike-fully")},
    {"name": "ebike fully",            "url": url("ebike-fully")},
    {"name": "elektro fully",          "url": url("elektro-fully")},
    {"name": "e-mtb fully",            "url": url("e-mtb-fully")},
    {"name": "emtb",                   "url": url("emtb")},
    {"name": "e-mountainbike fully",   "url": url("e-mountainbike-fully")},
    {"name": "pedelec fully",          "url": url("pedelec-fully")},
    {"name": "elektrofahrrad fully",   "url": url("elektrofahrrad-fully")},
    # --- Marki ---
    {"name": "Cube Stereo Hybrid",     "url": url("cube-stereo-hybrid")},
    {"name": "Cube Stereo E",          "url": url("cube-stereo-e")},
    {"name": "Trek Rail",              "url": url("trek-rail")},
    {"name": "Trek Powerfly FS",       "url": url("trek-powerfly-fs")},
    {"name": "KTM Macina Lycan",       "url": url("ktm-macina-lycan")},
    {"name": "KTM Macina Kapoho",      "url": url("ktm-macina-kapoho")},
    {"name": "KTM Macina fully",       "url": url("ktm-macina-fully")},
    {"name": "Scott Strike E-Ride",    "url": url("scott-strike-e-ride")},
    {"name": "Scott Patron",           "url": url("scott-patron")},
    {"name": "Scott Genius E-Ride",    "url": url("scott-genius-e-ride")},
    {"name": "Canyon Strive ON",       "url": url("canyon-strive")},
    {"name": "Canyon Torque ON",       "url": url("canyon-torque-on")},
    {"name": "Specialized Levo",       "url": url("specialized-levo")},
    {"name": "Specialized Turbo Levo", "url": url("specialized-turbo-levo")},
]

TRANSPORT_PLN = 300  # do recznej korekty przed zakupem

SEEN_FILE = Path("seen.json")
scraper = cloudscraper.create_scraper()
# niemiecka wersja strony niezależnie od tego, gdzie stoi runner —
# od tego zależą etykiety dat ("Heute"/"Gestern"), które czyta parser
scraper.headers.update({"Accept-Language": "de-DE,de;q=0.9"})
_eur_pln_cache = None


def get_eur_pln() -> float:
    global _eur_pln_cache
    if _eur_pln_cache:
        return _eur_pln_cache
    try:
        r = requests.get("https://api.exchangerate-api.com/v4/latest/EUR", timeout=10)
        _eur_pln_cache = r.json()["rates"]["PLN"]
        return _eur_pln_cache
    except Exception:
        return 4.25  # fallback


# Wzorce znanych modeli — do precyzyjnego zapytania OLX (marka+model,
# nie ogólne "e-bike fully" które porównuje jabłka z gruszkami)
MODEL_PATTERNS = [
    r'cube stereo hybrid\s*\d*',
    r'cube stereo\s*\d*',
    r'trek rail\s*\d*',
    r'trek powerfly(?:\s*fs)?\s*\d*',
    r'specialized (?:turbo )?(?:levo|kenevo)(?:\s*sl)?',
    r'scott strike(?:\s*e-?ride)?',
    r'scott patron',
    r'scott genius(?:\s*e-?ride)?',
    r'ktm macina\s+\w+',
    r'canyon (?:strive|torque|spectral|neuron)',   # bez ':on' — czysty slug do OLX
]

OLX_MIN_SAMPLES = 5  # poniżej tylu ofert mediana to loteria — nie liczymy zysku


def olx_query_for(title: str, fallback: str) -> str:
    """Wyciąga markę+model z tytułu; jak się nie da — nazwa wyszukiwania."""
    t = title.lower()
    for p in MODEL_PATTERNS:
        m = re.search(p, t)
        if m:
            return m.group(0).strip()
    return fallback


CURRENT_YEAR = date.today().year


def extract_year(text):
    """Wyciąga rocznik roweru (2015-2026) z tytułu/opisu. None gdy brak."""
    if not text:
        return None
    yr = r'20(?:1[5-9]|2[0-6])'
    # 1. z kontekstem — najpewniejsze
    m = re.search(rf'(?:modelljahr|modell|baujahr|bj\.?|mj\.?|jahrgang|aus|von|rok)\s*[:.]?\s*({yr})', text, re.I)
    if m:
        return int(m.group(1))
    # 2. "2023er"
    m = re.search(rf'\b({yr})er\b', text, re.I)
    if m:
        return int(m.group(1))
    # 3. goły rok (w nawiasie lub samodzielny)
    m = re.search(rf'\b({yr})\b', text)
    if m:
        return int(m.group(1))
    return None


# --- WIEK OGŁOSZENIA -------------------------------------------------------
# Karta wyniku na Kleinanzeigen niesie czas wystawienia ("Heute, 00:41") i bot
# dotąd go wyrzucał. Bez tego "nowe" znaczyło tylko "pierwszy raz je widzę" —
# ogłoszenie, które weszło do wyników 17 h po wystawieniu (bo sprzedawca zbił
# cenę do widełek albo poprawił opis), wyglądało jak świeże. Ogłoszenie wiekowe
# to inna decyzja: rower był już widziany przez cały rynek.
AD_TIME_PATTERN = re.compile(r'aditem-main--top--right"[^>]*>(.*?)</div>', re.S)

# Powyżej tylu minut ogłoszenie nie jest już "świeże". Skan idzie co 5 minut,
# a kanał sam nadrabia przerwy cofaniem się wstecz, więc 30 minut znaczy, że
# coś zawiodło: albo stanął wyzwalacz, albo roweru w ogóle nie było w kanale.
SWIEZOSC_MIN = 30


def parse_ad_time(raw, now=None):
    """'Heute, 00:41' / 'Gestern, 18:12' / '21.08.2026' -> datetime (strefa DE).

    Zwraca None, gdy formatu nie da się odczytać — brak daty nigdy nie może
    wywrócić skanu. Dla samej daty (bez godziny) zwraca północ, więc wiek jest
    znany tylko z dokładnością do doby."""
    if not raw:
        return None
    txt = re.sub(r'<[^>]+>', ' ', raw)
    txt = re.sub(r'\s+', ' ', txt).strip()
    if not txt:
        return None
    now = now or datetime.now(TZ_DE)
    m = re.search(r'\b(\d{1,2})[:.](\d{2})\b', txt)
    low = txt.lower()
    if m and ("heute" in low or "gestern" in low):
        godz, minuty = int(m.group(1)), int(m.group(2))
        if godz > 23 or minuty > 59:
            return None
        dzien = now.date() - (timedelta(days=1) if "gestern" in low else timedelta(0))
        return datetime(dzien.year, dzien.month, dzien.day, godz, minuty, tzinfo=TZ_DE)
    m = re.search(r'\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b', txt)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return datetime(y, mo, d, tzinfo=TZ_DE)
        except ValueError:
            return None
    return None


def ad_age_minutes(posted, now=None):
    """Ile minut temu wystawiono ogłoszenie. None gdy nie znamy czasu."""
    if not posted:
        return None
    now = now or datetime.now(TZ_DE)
    return (now - posted).total_seconds() / 60.0


def format_age(minutes) -> str:
    """Wiek ogłoszenia po ludzku. '?' gdy Kleinanzeigen nie podało czasu."""
    if minutes is None:
        return "nie podano"
    if minutes < 0:                      # zegar runnera rozjechany ze stroną
        return "przed chwilą"
    if minutes < 60:
        return f"{int(minutes)} min temu"
    if minutes < 48 * 60:
        h, m = divmod(int(minutes), 60)
        return f"{h} h {m} min temu" if m else f"{h} h temu"
    return f"{int(minutes // 1440)} dni temu"


def olx_search_url(query: str) -> str:
    slug = query.lower().replace(" ", "-")
    return f"https://www.olx.pl/sport-hobby/rowery/q-{slug}/"


# === JEDNA BRAMKA ALARMOWA =================================================
# Użytkownik nie jest techniczny i dostawał osiem różnych alarmów, w tym pary
# "nie działa"/"działa" w odstępie minuty — bo każdy alarm szedł ZBOCZEM, więc
# mrugnięcie sieci wystarczało za powód. Teraz liczy się TRWANIE: dopóki awaria
# nie utrzyma się AWARIA_PROG_MIN, nie dowiaduje się o niej wcale. Potem jedno
# zdanie po ludzku, bez żargonu, i jedno "już działa" na koniec. Szczegóły
# techniczne zostają w logu i pod komendą /status — na żądanie, nie z automatu.
# Nieprzeczytana strona ogłoszenia NIE JEST faktem "brak danych".
# Serwer po drugiej stronie bywa chwilowo niedostępny i to nie jest w naszej
# mocy — w naszej mocy jest nie zapisać takiej chwili jako wiedzy o rowerze.
# Ogłoszenie z nieudanym odczytem trafia do kolejki i wraca w kolejnych
# skanach po własny adres, aż się przeczyta. Kanał kategorii go już nie odda:
# znacznik czasu przesunął się dalej, więc bez tej kolejki byłby stracony.
ODCZYT_PROBY = 3           # prób w jednym podejściu (2 s, 4 s przerwy)
ODCZYT_PODEJSC = 8         # podejść w kolejnych skanach zanim odpuścimy
ODCZYT_NA_SKAN = 6         # ile zaległych czytamy w jednym skanie (budżet ruchu)
ODCZYT_WAZNE_H = 36        # po tylu godzinach rower i tak jest już nieświeży

AWARIA_PROG_MIN = 60
_problemy = []

# Czujka cichej zmiany układu strony. 23.08 Kleinanzeigen podmieniło stronę
# ogłoszenia: id ceny z "viewad-price" na "vip-ad-price", a zdjęcia z
# data-imgsrc na bloki JSON-LD. Bot czytał dalej opis, więc NIC nie krzyczało —
# tylko album był pusty, a cena ze strony nie działała. Od teraz brak zdjęć albo
# brak ceny na WSZYSTKICH przeczytanych stronach naraz jest traktowany jak
# awaria: to nie przypadek, to zmiana układu.
CZUJKA_MIN = 5              # poniżej tylu odczytów cisza nic nie znaczy
_czujka = {"czytane": 0, "ze_zdjeciami": 0, "z_cena": 0}


def zlicz_odczyt(zdjecia, cena) -> None:
    _czujka["czytane"] += 1
    if zdjecia:
        _czujka["ze_zdjeciami"] += 1
    if cena:
        _czujka["z_cena"] += 1


def sprawdz_uklad(licznik=None) -> None:
    """Wywoływane raz na skan, przed oceną zdrowia."""
    c = licznik if licznik is not None else _czujka
    if c["czytane"] < CZUJKA_MIN:
        return
    if not c["ze_zdjeciami"]:
        zglos_problem("uklad", f"zero zdjęć na {c['czytane']} stronach")
    if not c["z_cena"]:
        zglos_problem("uklad", f"zero cen na {c['czytane']} stronach")


def zglos_problem(rodzaj: str, szczegol: str = "") -> None:
    """Odnotowuje awarię w trakcie skanu. Sam nie wysyła NICZEGO."""
    if rodzaj not in _problemy:
        _problemy.append(rodzaj)
    log.error(f"awaria [{rodzaj}] {szczegol}")


def _stan(zmiana=None):
    """Czyta/zapisuje wspólny plik stanu (i tak commitowany do repo)."""
    stan = {}
    if PARSE_STATE_FILE.exists():
        try:
            stan = json.loads(PARSE_STATE_FILE.read_text())
        except Exception:
            stan = {}
    if zmiana is not None:
        stan.update(zmiana)
        try:
            PARSE_STATE_FILE.write_text(json.dumps(stan))
        except Exception as e:
            log.error(f"zapis stanu: {e}")
    return stan


def opisz_awarie(rodzaje) -> str:
    """Awaria po ludzku — co z tego wynika DLA NIEGO, nie co się zepsuło."""
    slepy = "slepy" in rodzaje
    olx = "olx" in rodzaje
    if "uklad" in rodzaje and not slepy:
        return ("🔕 <b>DealHawk — ogłoszenia zmieniły wygląd</b>\n\n"
                "Rowery przychodzą normalnie, ale przestałem wyciągać ze strony "
                "zdjęcia albo cenę. To znaczy, że Kleinanzeigen przebudowało "
                "stronę i muszę się dostroić.\n\n"
                "Nic nie zgubisz — link w wiadomości działa jak zwykle.")
    if slepy and olx:
        tresc = ("Nie mogę pobrać ogłoszeń z Niemiec ani sprawdzić cen na OLX. "
                 "Nowe rowery na razie nie przyjdą.")
    elif slepy:
        tresc = ("Nie mogę pobrać listy ogłoszeń z Niemiec. "
                 "Nowe rowery na razie nie przyjdą.")
    else:
        tresc = ("Nie mam dostępu do OLX. Rowery z Niemiec przychodzą normalnie, "
                 "ale wyceny liczę ze starszych danych.")
    return (f"🔕 <b>DealHawk — coś nie działa od godziny</b>\n\n{tresc}\n\n"
            "Próbuję dalej co 5 minut, zwykle mija samo. "
            "Odezwę się, gdy wróci. Szczegóły: napisz <code>/status</code>")


def ocen_zdrowie(rodzaje):
    """Wywoływane RAZ na skan, na końcu. Jedyne miejsce, które alarmuje."""
    try:
        stan = _stan()
        od = stan.get("awaria_od")
        zgloszona = bool(stan.get("awaria_zgloszona"))
        if not rodzaje:
            if od or zgloszona:
                _stan({"awaria_od": None, "awaria_zgloszona": False})
            if zgloszona:
                send_telegram("✅ <b>DealHawk — już działa.</b>")
                log.info("Awaria zakończona — wysłano potwierdzenie")
            return
        teraz = time.time()
        if not od:
            _stan({"awaria_od": teraz})
            log.warning(f"awaria {rodzaje} — zegar ruszył, cisza do "
                        f"{AWARIA_PROG_MIN} min")
            return
        trwa_min = (teraz - od) / 60
        if trwa_min >= AWARIA_PROG_MIN and not zgloszona:
            _stan({"awaria_zgloszona": True})
            send_telegram(opisz_awarie(rodzaje))
            log.error(f"awaria {rodzaje} trwa {int(trwa_min)} min — zgłoszona")
        else:
            log.warning(f"awaria {rodzaje} trwa {int(trwa_min)} min "
                        f"({'już zgłoszona' if zgloszona else 'jeszcze cisza'})")
    except Exception as e:
        log.error(f"ocen_zdrowie error: {e}")


def alarm_olx_martwy(kontekst: str, szczegoly: str = "") -> None:
    """OLX nie oddaje ofert. Cisza jest gorsza od fałszywego alarmu —
    poprzednio kosztowała 11 dni niezebranych danych — ale alarm idzie przez
    wspólną bramkę, więc chwilowa wpadka nie budzi nikogo."""
    d = olx_diag()
    zglos_problem("olx", f"{kontekst} | {d} | {szczegoly}")


def fetch_olx_offers(query: str, pages: int = 2) -> dict:
    """Zwraca {url_oferty: cena} z pierwszych `pages` stron wyników OLX
    (więcej próbki = lepsze filtrowanie do porównywalnych)."""
    out = {}
    slug = query.lower().replace(" ", "-")
    for page in range(1, pages + 1):
        url = f"https://www.olx.pl/sport-hobby/rowery/q-{slug}/"
        if page > 1:
            url += f"?page={page}"
        r = olx_get(url)
        if r is None or r.status_code != 200:
            break
        cards = parse_olx_cards(r.text)
        if not cards:
            zglos_pusta_strone()      # 200 bez kart = podejrzenie blokady
            break                     # (albo po prostu koniec wyników)
        out.update({c["url"]: c["price"] for c in cards})
    return out


# --- Pełna dokładność OLX: strukturalny przebieg/stan ze strony oferty. ---
# Strona waży ~2 MB, więc cache per-URL (pobieramy raz, potem tylko nowe oferty).
OLX_DETAILS_FILE = Path("olx_details.json")
OLX_DETAILS_KEEP_DAYS = 60
_olx_details_cache = None


def load_olx_details() -> dict:
    global _olx_details_cache
    if _olx_details_cache is None:
        if OLX_DETAILS_FILE.exists():
            try:
                _olx_details_cache = json.loads(OLX_DETAILS_FILE.read_text())
            except Exception:
                _olx_details_cache = {}
        else:
            _olx_details_cache = {}
    return _olx_details_cache


# === SPECYFIKACJA Z OPISU: osprzęt, amortyzator, rama =========================
# Cena zależy nie tylko od rocznika i baterii — "Trek Rail 5" i "Rail 9.8" to ten
# sam model w wyszukiwarce i dwa razy inna cena. Te dane SĄ w opisach (amortyzator
# 94%, osprzęt 94%, rama 89% zbadanych ofert), a bot je wyrzucał, choć strony i tak
# pobierał. Drabinki jakości siedzą w wiedza_sprzet.json — do wglądu i poprawek
# właściciela. Tu ustalamy CO jest lepsze; ILE to warte policzy rynek.
SPEC_KB_FILE = Path("wiedza_sprzet.json")
_spec_kb_cache = None

# Słowa, które muszą stać BLISKO nazwy, żeby uznać ją za grupę napędową.
# Bez tego "Cube Stereo Hybrid 140 SLX" (wersja Cube'a) udaje grupę Shimano SLX.
# Konteksty po polsku I po niemiecku — ten sam parser czyta oferty z OLX
# (wycena) i z Kleinanzeigen (zakup), więc musi rozumieć oba rynki.
_GRUPA_KONTEKST = ["shimano", "sram", "naped", "napęd", "osprzet", "osprzęt",
                   "przerzutka", "przerzutki", "grupa", "kaseta", "korba", "manetka",
                   "antrieb", "schaltung", "schaltwerk", "kassette", "kurbel"]
_SKOK_KONTEKST = ["skok", "travel", "amortyz", "zawieszen", "widelec", "przod", "przód",
                  "federweg", "federgabel", "gabel", "dampfer", "dämpfer"]
# "XT" bywa i grupą napędową, i hamulcem — rozstrzyga sąsiedztwo
_HAMULCE_KONTEKST = ["hamulc", "hamulec", "brake", "tarcz", "zacisk", "klocki",
                     "bremse", "bremsen", "scheibenbrems"]


def load_spec_kb() -> dict:
    """Wczytuje drabinki jakości sprzętu z pliku. Brak pliku = brak wiedzy
    (bot nie zgaduje — woli nie wiedzieć niż skłamać)."""
    global _spec_kb_cache
    if _spec_kb_cache is None:
        try:
            _spec_kb_cache = json.loads(SPEC_KB_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            log.error(f"load_spec_kb error: {e}")
            _spec_kb_cache = {}
    return _spec_kb_cache


def _ma_kontekst(text: str, pos: int, slowa: list, okno: int = 45) -> bool:
    """Czy w promieniu `okno` znaków wokół pozycji stoi któreś ze słów?"""
    frag = text[max(0, pos - okno):pos + okno]
    return any(s in frag for s in slowa)


def _najlepszy_z_drabinki(d: str, drabinka: dict, wymagany_kontekst=None,
                          zakazany_kontekst=None):
    """Znajduje najwyżej stojący w drabince komponent wymieniony w opisie.
    `wymagany_kontekst` — nazwa liczy się tylko obok tych słów.
    `zakazany_kontekst` — i NIE liczy się obok tamtych. Bez tego drugiego
    "hamulce Shimano XT" wpada jednocześnie do hamulców i do napędu."""
    best = None
    for nazwa, ranga in (drabinka or {}).items():
        if nazwa.startswith("_") or not isinstance(ranga, int):
            continue
        for m in re.finditer(r'(?:^|[^\w])' + re.escape(nazwa) + r'(?:[^\w]|$)', d):
            if wymagany_kontekst and not _ma_kontekst(d, m.start(), wymagany_kontekst):
                continue
            if zakazany_kontekst and _ma_kontekst(d, m.start(), zakazany_kontekst, 25):
                continue
            if best is None or ranga > best[1]:
                best = (nazwa, ranga)
            break
    return best


# --- ROZMIAR RAMY ----------------------------------------------------------
# Rozmiar decyduje, czy rower da się w ogóle sprzedać — na L kupca szuka się
# tygodniami, a XS potrafi nie znaleźć go wcale. Do 22.08.2026 bot szukał go
# POLSKIMI słowami ("rozmiar", "rama") w NIEMIECKICH opisach, więc pole było
# puste zawsze: 0 trafień na 12 żywych ogłoszeń, z których 6 podawało rozmiar
# wprost. Formy zebrane z żywych danych:
#   "Gr. L" · "Rahmengröße S" · "Rahmenhöhe: XL" · "Rahmenhöhe von 53 cm"
#   "Rahmengröße M 50cm" · "Rahmengröße: 57 cm"
_GR = r'gr(?:o|ö|oe)(?:ss|ß|s)?e'          # große / grösse / grosse / groesse
# Ta sama funkcja czyta opisy NIEMIECKIE (Kleinanzeigen) i POLSKIE (OLX) —
# parse_spec_fields wołane jest na obu — więc etykiety muszą być w dwóch językach.
_RAMA_ETYKIETA = (r'(?:rahmen\s?h(?:o|ö|oe)he|rahmen\s?' + _GR +
                  r'|\brh\b|' + _GR + r'|\bgr\.'
                  r'|rozmiar(?:\s+ramy)?|\brama\b|\bramy\b)')
_RAMA_LITERY = ("xs", "s", "m", "l", "xl", "xxl")
_RAMA_CM_MIN, _RAMA_CM_MAX = 33, 65      # poza tym zakresem to nie jest rama


def rozmiar_ramy(title: str, desc: str):
    """Rozmiar ramy z tytułu/opisu ("M", "53 cm", "M / 50 cm"). None gdy brak.

    Zwraca WYŁĄCZNIE to, czego jest pewna. Liczby spoza zakresu ramy odpadają,
    bo w tych samych zdaniach siedzą koła (29 Zoll), opony (2,6") i waga
    (130 kg) — złapanie któregoś z nich byłoby gorsze niż brak odpowiedzi."""
    tekst = re.sub(r'\s+', ' ', f"{title or ''} {desc or ''}").lower()
    litera = cm = None
    for m in re.finditer(_RAMA_ETYKIETA + r'\s*(?::|von|,)?\s*([^,;.|]{0,24})', tekst):
        ogon = m.group(1)
        if "zoll" in ogon:                 # to rozmiar koła, nie ramy
            continue
        if litera is None:
            ml = re.match(r'\s*(xs|xxl|xl|s|m|l)\b', ogon)
            if ml and ml.group(1) in _RAMA_LITERY:
                litera = ml.group(1).upper()
        if cm is None:
            mc = re.search(r'(\d{2})\s*(?:cm\b|$|\s)', ogon)
            if mc and _RAMA_CM_MIN <= int(mc.group(1)) <= _RAMA_CM_MAX:
                cm = int(mc.group(1))
        if litera and cm:
            break
    if litera and cm:
        return f"{litera} / {cm} cm"
    if litera:
        return litera
    if cm:
        return f"{cm} cm"
    return None


def parse_spec_fields(desc: str) -> dict:
    """Wyciąga z opisu: amortyzator (+wersja), skok, osprzęt, ramę, rozmiar,
    generację silnika. Czysta funkcja — testowalna bez sieci. Zwraca WYŁĄCZNIE
    to, czego jest pewna: brak dopasowania = brak klucza, nigdy zgadywanie."""
    if not desc:
        return {}
    d = desc.lower()
    kb = load_spec_kb()
    out = {}

    w = _najlepszy_z_drabinki(d, kb.get("amortyzator_przod"))
    if w:
        out["widelec"], out["widelec_rank"] = w
    elif re.search(r'rock\s?shox|rockshox|\bfox\b', d):
        out["widelec"] = "nieznany model"      # marka jest, model nie — uczciwie

    w = _najlepszy_z_drabinki(d, kb.get("wersja_amortyzatora"))
    if w:
        out["wersja"], out["wersja_rank"] = w

    # skok w mm: tylko z kontekstem, inaczej złapiemy rozmiar koła albo opony
    for m in re.finditer(r'(\d{3})\s*mm', d):
        v = int(m.group(1))
        if 100 <= v <= 220 and _ma_kontekst(d, m.start(), _SKOK_KONTEKST):
            out["skok_mm"] = v
            break

    # napęd: nazwa obok "shimano/przerzutka", ale NIE obok "hamulce"
    w = _najlepszy_z_drabinki(d, kb.get("osprzet"), _GRUPA_KONTEKST, _HAMULCE_KONTEKST)
    if w:
        out["osprzet"], out["osprzet_rank"] = w

    w = _najlepszy_z_drabinki(d, kb.get("rama"))
    if w:
        out["rama"], out["rama_rank"] = w

    r = rozmiar_ramy("", desc)
    if r:
        out["rozmiar"] = r

    # hamulce: te same nazwy co grupy napędowe (XT!), więc również z kontekstem
    w = _najlepszy_z_drabinki(d, kb.get("hamulce"), _HAMULCE_KONTEKST)
    if w:
        out["hamulce"], out["hamulce_rank"] = w

    # generacja silnika Bosch — mocna wskazówka o roczniku (Gen4 = 2020+)
    m = re.search(r'\bgen\.?\s?([2-5])\b', d)
    if m:
        out["bosch_gen"] = int(m.group(1))
    elif "smart system" in d:
        out["bosch_gen"] = 5

    # --- ŁĄCZNY POZIOM WYPOSAŻENIA (1-6) ---------------------------------
    # Pojedyncze cechy mają dziurawe pokrycie (widelec tylko 28% ofert), ale
    # "choć jedna" to już 74%. A że mocno się powtarzają (widelec vs rama:
    # korelacja 0.89 — znając widelec, znasz i ramę), nie ma sensu trzymać ich
    # osobno. Składamy w jeden wskaźnik z tego, co akurat jest w opisie.
    #
    # HAMULCÓW tu NIE MA celowo: po ich dodaniu drabinka przestawała być
    # monotoniczna (poziom 6 wychodził tańszy od 5). Zostają jako informacja,
    # ale do wskaźnika nie wchodzą — sprawdzone na 108 ofertach, nie zgadnięte.
    skladniki = [(out[k] / mx * 6) for k, mx in
                 (("osprzet_rank", 6), ("widelec_rank", 8),
                  ("rama_rank", 2)) if out.get(k)]
    if skladniki:
        out["poziom"] = round(sum(skladniki) / len(skladniki))
        out["poziom_n"] = len(skladniki)     # na ilu cechach oparty = pewność
    return out


def _parse_detail_fields(h: str) -> dict:
    """Wyciąga strukturalne pola ze strony oferty OLX: przebieg, stan,
    a z OPISU (nie z boilerplate strony!) rocznik, baterię i specyfikację."""
    out = {}
    m = re.search(r'Przebieg[^\d]{0,10}(\d[\d\s]*)\s*km', h)
    if m:
        v = int(m.group(1).replace(" ", ""))
        if 0 < v <= 60000:
            out["km"] = v
    m = re.search(r'Stan[:•\s]{1,4}(Nowe|Używane|Jak nowe|Bardzo dobry|Dobry)', h)
    if m:
        out["stan"] = m.group(1)
    # rocznik i Wh TYLKO z treści opisu — cała strona ma lata w stopce/skryptach
    dm = re.search(r'"description":"((?:[^"\\]|\\.)*)"', h)
    if dm:
        desc = dm.group(1)
        ym = (re.search(r'(?:rok(?:u|iem)?|rocznik|model(?:l?jahr)?|bj\.?)\D{0,8}(20(?:1[5-9]|2[0-6]))', desc, re.I)
              or re.search(r'\b(20(?:1[5-9]|2[0-6]))\s*r(?:\b|ok)', desc, re.I)
              # "t2021" / "mj2022" — rocznik sklejony z literą, spotykane w opisach
              or re.search(r'\b[a-z]{1,2}(20(?:1[5-9]|2[0-6]))\b', desc, re.I))
        if ym:
            out["y"] = int(ym.group(1))
        whm = re.search(r'(\d{3})\s*wh\b', desc, re.I)
        if whm and 300 <= int(whm.group(1)) <= 1000:
            out["wh"] = int(whm.group(1))
        out.update(parse_spec_fields(desc))
    return out


def fetch_olx_detail(url: str) -> dict:
    """Pobiera stronę oferty OLX → strukturalny przebieg/stan/rocznik/bateria."""
    r = olx_get(url)
    if r is None or r.status_code != 200:
        return {}
    return _parse_detail_fields(r.text)


# Części zatruwają pulę cen (ładowarka 550 zł liczona jak rower!) i "sprzedaże"
PART_SLUG_WORDS = ["bateria", "akumulator", "ladowarka", "wyswietlacz", "display",
                   "silnik", "sztyca", "widelec", "amortyzator", "przerzutka", "kaseta"]


def _is_shop_slug(url: str) -> bool:
    """Sklep/komis (raty, F-VAT) = zwykle nowy rower — zawyża porównanie z używanym."""
    slug = url.split("/d/oferta/")[-1].lower()
    return bool(re.search(r'\braty\b|f-?vat|leasing', slug))


def olx_relevant_offers(query: str, offers: dict) -> dict:
    """Filtruje wyniki OLX do ofert FAKTYCZNIE dotyczących modelu.
    1) Słowa modelu muszą być w slugu W KOLEJNOŚCI, blisko siebie i blisko
       początku — odrzuca keyword-stuffing ('...trek-enduro-focus-trail-jam-
       mtb-rail' w ogonie tytułu Cube'a wpadało do wyników Trek Rail).
    2) Części (ładowarki/baterie/wyświetlacze): słowo części na starcie sluga
       ALBO słowo części + cena <30% mediany puli."""
    tokens = [t for t in query.lower().split() if t and not t.isdigit()]

    def find_tok(slug, tok):
        # token jako CAŁY człon sluga (granice na '-') — 'rail' nie może
        # matchować wewnątrz 'trail'
        m = re.search(r'(?:^|-)' + re.escape(tok) + r'(?=-|$)', slug)
        if not m:
            return None
        i = m.start() + (1 if m.group(0).startswith('-') else 0)
        return (i, i + len(tok))

    # zwarte okno: wszystkie słowa modelu blisko siebie (dowolna kolejność —
    # 'levo turbo' vs 'turbo levo' to ten sam rower), blisko początku tytułu
    window = sum(len(t) for t in tokens) + len(tokens) + 14
    stage1 = {}
    for url, price in offers.items():
        slug = url.split("/d/oferta/")[-1].lower()
        spans = [find_tok(slug, t) for t in tokens]
        if any(s is None for s in spans):
            continue
        first = min(s[0] for s in spans)
        last = max(s[1] for s in spans)
        if first > 45 or (last - first) > window:
            continue
        head = slug[:24]
        if any(w in head for w in PART_SLUG_WORDS) and not slug.startswith("rower"):
            continue
        stage1[url] = price
    if not stage1:
        return stage1
    med = statistics.median(list(stage1.values()))
    out = {}
    for url, price in stage1.items():
        slug = url.split("/d/oferta/")[-1].lower()
        if price < 0.3 * med and any(w in slug for w in PART_SLUG_WORDS):
            continue  # tanie + słowo części = część
        out[url] = price
    return out


def parse_olx_slug(url: str):
    """Wyciąga (rok, przebieg_km, bateria_wh) z URL-a oferty OLX — bez dodatkowych
    zapytań. Kotwice (rok/r, km, wh) minimalizują false-posity. None gdy brak."""
    slug = url.split("/d/oferta/")[-1].lower()
    ym = re.search(r'(?:^|-)(20(?:1[5-9]|2[0-6]))(?:r\b|-rok|-|$)', slug)
    km = re.search(r'(\d{2,5})-?km\b', slug)
    wh = re.search(r'(\d{3})-?wh\b', slug)
    year = int(ym.group(1)) if ym else None
    kmv = int(km.group(1)) if km and 10 <= int(km.group(1)) <= 30000 else None
    whv = int(wh.group(1)) if wh and 300 <= int(wh.group(1)) <= 1000 else None
    return year, kmv, whv


def trimmed_median(vals, trim=0.15):
    """Mediana po odcięciu skrajnych `trim` z obu stron — zabija outliery
    (części, premium-warianty, scamy, inny model w wynikach)."""
    if not vals:
        return None
    s = sorted(vals)
    k = int(len(s) * trim)
    core = s[k:len(s) - k] if len(s) > 2 * k + 1 else s
    return int(statistics.median(core))


def wh_class(wh):
    """Klasa baterii (S<550, M<700, L) — 500/625/750 Wh to duża różnica ceny."""
    if not wh:
        return None
    return "S" if wh < 550 else "M" if wh < 700 else "L"


# === CENNIK CECH: ile rynek realnie płaci za rocznik, baterię, wyposażenie ====
# Zastępuje łamane "pasma podobieństwa", które traktowały nieznany atrybut jak
# pasujący (stąd stary Cube 2018/400 Wh wyceniany jak model 2023/750 Wh).
# Współczynniki NIE są wymyślone — liczą się z zebranych ofert (rynek_pl.jsonl)
# i lądują w cennik_cech.json wraz z liczbą ofert, na których się opierają.
CENNIK_FILE = Path("cennik_cech.json")
RYNEK_FILE = Path("rynek_pl.jsonl")
_cennik_cache = None


def _regresja_1d(xs, ys):
    """Najprostsza regresja liniowa y = a*x + b. Zwraca (a, b) albo None.
    Bez zależności zewnętrznych — wszystko ma być sprawdzalne gołym okiem."""
    n = len(xs)
    if n < 5:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    war = sum((x - mx) ** 2 for x in xs)
    if war <= 0:
        return None
    a = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / war
    return a, my - a * mx


# Cechy: (klucz, jak przeliczyć ofertę na liczbę, opis dla właściciela)
#
# DLACZEGO NIE MA TU GENERACJI SILNIKA (sprawdzone 21.08.2026, 599 ofert):
# Pomysł był taki, żeby wywnioskować rocznik z tego, co oferta podaje częściej
# niż rok. Dwie ślepe uliczki, obie odrzucone na podstawie danych, nie przeczuć:
#
#  1. rocznik z BATERII — błędne koło. Bateria już jest wyceniana poniżej, więc
#     jej wpływ liczyłby się drugi raz, przemycony przez rocznik.
#  2. GENERACJA SILNIKA jako osobna cecha — wygląda obiecująco (przy tej samej
#     baterii 750 Wh: Gen4 to rocznik ~2023, Gen5 ~2024; surowo Gen5 jest droższy,
#     14 900 vs 13 999 zł). Ale współczynnik liczy się PO odjęciu wpływu baterii,
#     rocznika i wyposażenia — a nowszy silnik chodzi w parze z nimi wszystkimi.
#     Po odliczeniu zostaje sam szum: wyszło -6,4%, czyli nowszy silnik rzekomo
#     obniża cenę, a wycena odwracała się (Gen5 tańszy od Gen4). Odrzucone.
#
# Wniosek: rocznik, bateria i wyposażenie wyczerpują to, co da się tu wycisnąć.
# Kolejne cechy z tej rodziny będą powtarzać te same informacje.
CECHY = [
    ("poziom", lambda r: r.get("poziom"), "stopień wyposażenia (1-6)"),
    ("wh", lambda r: (r["wh"] / 100) if r.get("wh") else None, "każde 100 Wh baterii"),
    ("y", lambda r: r.get("y"), "każdy rocznik nowszy"),
    ("km", lambda r: (r["km"] / 1000) if r.get("km") is not None else None,
     "każde 1000 km przebiegu"),
]


def odduplikuj(rows):
    """Ten sam rower wystawiony wielokrotnie liczy się RAZ. Bez tego cennik
    ustala sklep, który najgłośniej spamuje (realny przypadek: 40 z 46
    obserwacji '500 Wh' to była jedna oferta jednego sprzedawcy)."""
    # Ta sama OFERTA wraca do dziennika przy każdym skanie: w rynek_pl.jsonl
    # było 1427 wierszy na 417 adresów, czyli każdy rower liczony 3-4 razy.
    # Klucz po sprzedawcy tego nie łapał, bo w tym pliku pola `sprzedawca`
    # w ogóle nie ma — warunek `is not None` czynił funkcję bezczynną.
    # Zostawiamy OSTATNIĄ obserwację oferty, bo niesie aktualną cenę.
    po_url, kolejnosc = {}, []
    reszta, widziane = [], set()
    for r in rows:
        url = r.get("url")
        if url:
            if url not in po_url:
                kolejnosc.append(url)
            po_url[url] = r
            continue
        klucz = (r.get("sprzedawca"), r.get("model"), r.get("cena"))
        if klucz[0] is not None and klucz in widziane:
            continue
        widziane.add(klucz)
        reszta.append(r)
    return [po_url[u] for u in kolejnosc] + reszta


def zbuduj_cennik(rows):
    """Liczy, ile rynek dopłaca za każdą cechę. Metoda: kolejno dla każdej cechy
    regresja na logarytmie ceny (współczynnik = zmiana procentowa), licząc tylko
    oferty, które TĘ cechę podają — dzięki temu dziurawe dane nie wykluczają
    oferty z całej analizy. Zwraca dict gotowy do zapisu w cennik_cech.json."""
    rows = odduplikuj([r for r in rows if (r.get("cena") or 0) > 500])
    if len(rows) < 20:
        return None
    reszty = {i: math.log(r["cena"]) for i, r in enumerate(rows)}
    baza = statistics.median(reszty.values())
    for i in reszty:
        reszty[i] -= baza
    cennik = {}
    for klucz, konwersja, opis in CECHY:
        wartosci = [konwersja(r) for r in rows if konwersja(r) is not None]
        if len(wartosci) < 5:
            continue
        # ŚRODEK = typowy rower na rynku. Wszystko liczymy jako odchyłkę od niego,
        # dzięki czemu "cecha nieznana" znaczy po prostu "typowa", a nie zero.
        srodek = statistics.median(wartosci)
        pary = [(konwersja(r) - srodek, reszty[i]) for i, r in enumerate(rows)
                if konwersja(r) is not None]
        wynik = _regresja_1d([x for x, _ in pary], [y for _, y in pary])
        if not wynik:
            continue
        a, b = wynik
        cennik[klucz] = {"wspolczynnik": round(a, 4), "srodek": srodek,
                         "zmiana_ceny_pct": round((math.exp(a) - 1) * 100, 1),
                         "n_ofert": len(pary), "opis": opis}
        for i, r in enumerate(rows):        # zdejmij wyjaśnioną część i licz dalej
            x = konwersja(r)
            if x is not None:
                reszty[i] -= a * (x - srodek) + b
    return {"_opis": "Ile rynek doplaca za kazda ceche. Policzone z ofert OLX, "
                     "nie wymyslone. 'zmiana_ceny_pct' = o tyle % zmienia sie "
                     "cena na kazda jednostke cechy.",
            "data": date.today().isoformat(), "n_ofert": len(rows),
            "cena_bazowa": int(math.exp(baza)), "cechy": cennik}


def load_cennik():
    global _cennik_cache
    if _cennik_cache is None:
        try:
            _cennik_cache = json.loads(CENNIK_FILE.read_text(encoding="utf-8"))
        except Exception:
            _cennik_cache = {}
    return _cennik_cache


_rynek_cache = None


def oferty_z_rynku(query: str, max_wiek_dni: int = 21):
    """Oferty PL z ZAPISANEGO rynku (rynek_pl.jsonl) zamiast z sieci.

    OLX blokuje serwerownię GitHuba — HTTP 403, potwierdzone 21.08.2026, zarówno
    strona, jak i API. Runner NIE MOŻE więc pobierać ofert na żywo. Zbieranie robi
    maszyna z normalnym łączem i wrzuca wynik do repo; tutaj tylko go czytamy.
    Dzięki temu wycena działa mimo blokady — po prostu na danych sprzed doby."""
    global _rynek_cache
    if _rynek_cache is None:
        _rynek_cache = []
        try:
            for line in RYNEK_FILE.open(encoding="utf-8"):
                try:
                    _rynek_cache.append(json.loads(line))
                except Exception:
                    continue
        except Exception:
            pass
    if not _rynek_cache:
        return []
    granica = (date.today() - timedelta(days=max_wiek_dni)).isoformat()
    po_url = {}
    for r in _rynek_cache:                  # ostatni zapis danej oferty wygrywa
        if r.get("ts", "") >= granica and r.get("cena") and r.get("url"):
            po_url[r["url"]] = r
    if not po_url:
        return []
    trafne = olx_relevant_offers(query, {u: r["cena"] for u, r in po_url.items()})
    return [po_url[u] for u in trafne]


def _mnoznik(rec, cennik):
    """Ile razy droższy jest ten rower od TYPOWEGO na rynku — z jego znanych
    cech. Cecha nieznana = zakładamy typową (odchyłka zero), więc oferta bez
    podanego rocznika nie wywraca porównania. Zwraca (mnożnik, ile_cech)."""
    cechy = (cennik or {}).get("cechy") or {}
    log_sum, znane = 0.0, 0
    for klucz, konwersja, _ in CECHY:
        x = konwersja(rec)
        c = cechy.get(klucz) or {}
        if x is None or "srodek" not in c or "wspolczynnik" not in c:
            continue                    # cennik w starym/ułomnym formacie — pomijamy
        log_sum += c["wspolczynnik"] * (x - c["srodek"])
        znane += 1
    return math.exp(log_sum), znane


def wycen_z_cennikiem(oferty, ref, cennik=None):
    """Wycena przez PRZELICZENIE każdej oferty na specyfikację naszego roweru.
    Zamiast szukać bliźniaka (i udawać, że nieznany atrybut pasuje), bierzemy
    cenę oferty i korygujemy ją o różnicę cech — jak rzeczoznawca.

    oferty: lista dictów z 'cena' + rozpoznanymi cechami
    ref:    dict z cechami wycenianego roweru
    Zwraca dict albo None, gdy nie da się wycenić rzetelnie."""
    cennik = cennik if cennik is not None else load_cennik()
    if not (cennik or {}).get("cechy") or not oferty:
        return None
    m_ref, znane_ref = _mnoznik(ref, cennik)
    if not znane_ref:
        return None                     # o naszym rowerze nie wiemy NIC — nie zgadujemy
    przeliczone = []
    for o in oferty:
        if not o.get("cena"):
            continue
        m_o, znane_o = _mnoznik(o, cennik)
        if not znane_o:
            continue                    # o tej ofercie nie wiemy nic — pomijamy
        przeliczone.append(o["cena"] * m_ref / m_o)
    if len(przeliczone) < 4:
        return None
    s = sorted(przeliczone)
    return {"cena": trimmed_median(przeliczone), "n": len(przeliczone),
            "widelki": (int(s[len(s) // 4]), int(s[3 * len(s) // 4])),
            "cech_znanych": znane_ref,
            "pewnosc": "wysoka" if znane_ref >= 3 and len(przeliczone) >= 12
                       else "srednia" if znane_ref >= 2 and len(przeliczone) >= 6
                       else "niska"}


def olx_comparable_price(offers: dict, ref_year=None, ref_km=None, ref_wh=None, details=None):
    """Cena OLX dopasowana do KONKRETNEGO roweru (rocznik/przebieg/bateria).
    Strukturalne dane ze stron ofert (details) wygrywają nad zgadywaniem
    z URL-a. Nowe/sklepowe oferty wykluczane, dopóki starcza używanych
    (nasz rower z DE jest używany — nówka z ratami zawyża porównanie).
    Degradacja łagodna: od najostrzejszego pasa do całej populacji.
    Zwraca (cena, etykieta_metody, liczba_ofert_w_pasie)."""
    if not offers:
        return None, None, 0
    parsed = []
    for url, price in offers.items():
        y, k, w = parse_olx_slug(url)
        d = (details or {}).get(url) or {}
        if d.get("km") is not None:
            k = d["km"]              # strukturalny przebieg ze strony oferty
        if d.get("y"):
            y = d["y"]               # rocznik z opisu oferty
        if d.get("wh"):
            w = d["wh"]              # bateria z opisu oferty
        is_new = d.get("stan") == "Nowe" or _is_shop_slug(url)
        parsed.append({"p": price, "y": y, "k": k, "w": w, "new": is_new})
    ref_cls = wh_class(ref_wh)

    def band(pool, use_km, use_year, use_wh):
        out = []
        for o in pool:
            # wykluczamy tylko gdy ZNAMY atrybut po obu stronach i się różni
            if use_year and ref_year and o["y"] and abs(o["y"] - ref_year) > 1:
                continue
            if use_wh and ref_cls and o["w"] and wh_class(o["w"]) != ref_cls:
                continue
            if use_km and ref_km and o["k"] and abs(o["k"] - ref_km) > 1000:
                continue
            out.append(o["p"])
        return out

    used = [o for o in parsed if not o["new"]]
    ladders = ([(used, ", używane")] if len(used) >= 4 else []) + [(parsed, "")]
    for pool, suffix in ladders:
        for vals, label in [
            (band(pool, True, True, True),   "rok±1, przebieg, bateria"),
            (band(pool, False, True, True),  "rok±1, bateria"),
            (band(pool, False, True, False), "rok±1"),
            (band(pool, False, False, True), "bateria"),
        ]:
            if len(vals) >= 4:
                return trimmed_median(vals), label + suffix, len(vals)
        if suffix and len(band(pool, False, False, False)) >= 4:
            # żaden pas atrybutów nie zadziałał, ale mamy dość używanych
            vals = band(pool, False, False, False)
            return trimmed_median(vals), "używane (przycięte)", len(vals)
    allp = [o["p"] for o in parsed]
    return trimmed_median(allp), "cały model (przycięte)", len(allp)


def olx_compare_str(query: str, offers: dict, comparable=None) -> str:
    """Mini-porównywarka: zakres cen modelu na OLX + cena porównywalna + link."""
    if not offers:
        return ""
    prices = sorted(offers.values())
    med = prices[len(prices) // 2]
    line = (
        f"\n🇵🇱 OLX \"{query}\": {len(prices)} ofert · "
        f"{prices[0]:,}–{prices[-1]:,} zł · mediana {med:,} zł".replace(",", " ")
    )
    if comparable and comparable[0]:
        cp, method, n = comparable
        line += f"\n🎯 Porównywalne ({method}, {n} ofert): ~{cp:,} zł".replace(",", " ")
    demand = get_demand_price(query)
    if demand:
        line += f" · 💸 realnie schodzą po ~{demand:,} zł".replace(",", " ")
    line += f"\n🔍 {olx_search_url(query)}"
    return line


OLX_WATCH_FILE = Path("olx_watch.json")
DEMAND_MAX_AGE_DAYS = 14   # świeżość ceny popytu
SOLD_FAST_DAYS = 14        # oferta znikła w <= tyle dni = realnie sprzedana po tej cenie
LIQUIDITY_MAX_DAYS = 45    # dłużej = prawdopodobnie porzucone ogłoszenie, nie sprzedaż
LIQUIDITY_MIN_SAMPLES = 5  # tyle sprzedaży trzeba by płynność była wiarygodna

_olx_watch_cache = None


def olx_offer_gone(url: str):
    """Czy oferta OLX naprawdę zniknęła (sprzedana/usunięta)?
    Wymaga POZYTYWNEGO dowodu śmierci — frazy typu 'nieaktualne' siedzą
    w pakiecie tłumaczeń KAŻDEJ strony OLX (to zatruło nam 394 fałszywe
    'sprzedaże' z medianą 0 dni). Zwraca True/False/None (nie wiadomo)."""
    r = olx_get(url, timeout=10, allow_redirects=False)
    if r is None:
        return None
    if r.status_code in (404, 410):
        return True
    if r.status_code in (301, 302, 308):
        return True       # przekierowanie na kategorię = oferta zdjęta
    if r.status_code != 200:
        return None       # 403/429 = blokada, a nie śmierć oferty
    return _judge_olx_dead(r.text)


def _judge_olx_dead(h: str):
    """Ocena treści strony oferty: True=martwa, False=żywa, None=nie wiadomo."""
    if re.search(r'status\\?":\\?"active', h) or "schema.org/InStock" in h:
        return False      # twardy dowód życia
    m = re.search(r'status\\?":\\?"(\w+)', h)
    if m and m.group(1) in ("removed_by_user", "outdated", "expired", "finished",
                            "disabled", "moderated", "removed"):
        return True
    return None           # brak dowodu w żadną stronę — nie zgadujemy


def load_olx_watch() -> dict:
    global _olx_watch_cache
    if _olx_watch_cache is None:
        if OLX_WATCH_FILE.exists():
            try:
                _olx_watch_cache = json.loads(OLX_WATCH_FILE.read_text())
            except Exception:
                _olx_watch_cache = {}
        else:
            _olx_watch_cache = {}
    return _olx_watch_cache


def get_liquidity(query: str):
    """Medianowy czas sprzedaży modelu w PL (dni) z własnej obserwacji OLX.
    None gdy za mało danych."""
    w = load_olx_watch().get(query)
    if not w:
        return None
    days = [s["days"] for s in w.get("sold_fast", [])
            if isinstance(s, dict) and isinstance(s.get("days"), int) and 0 <= s["days"] <= LIQUIDITY_MAX_DAYS]
    if len(days) < LIQUIDITY_MIN_SAMPLES:
        return None
    return int(statistics.median(days))


def olx_sell_forecast(query: str, asking_price=None):
    """Odpowiada 'ile ma stać / czy się sprzeda' dla modelu — z realnego cyklu
    życia ofert OLX. Zwraca dict albo None gdy za mało danych.
    - clearing: mediana ceny DOMYKAJĄCEJ (za ile realnie schodzi)
    - days: mediana dni do sprzedaży
    - sell_through: % ofert które faktycznie zeszły (vs wygasłe)
    - drop_pct: o ile średnio sprzedawcy zbijają cenę przed sprzedażą
    - verdict: ocena twojej ceny wywoławczej vs cena domykająca"""
    w = load_olx_watch().get(query)
    if not w:
        return None
    sold = [s for s in w.get("sold_fast", []) if isinstance(s, dict)]
    clearing = [s["price"] for s in sold if s.get("price")]
    if len(clearing) < LIQUIDITY_MIN_SAMPLES:
        return None
    out = {
        "clearing": int(statistics.median(clearing)),
        "days": get_liquidity(query),
        "sell_through": w.get("sell_through_pct"),
        "drop_pct": w.get("typical_drop_pct"),
        "n": len(clearing),
    }
    if asking_price:
        cm = out["clearing"]
        diff = (asking_price - cm) / cm * 100
        if diff <= 2:
            out["verdict"] = f"✅ cena OK — na poziomie ceny domykającej ({cm:,} zł)".replace(",", " ")
        elif diff <= 8:
            out["verdict"] = f"🟡 lekko za wysoko (+{diff:.0f}% vs {cm:,} zł) — obserwujący czekają na obniżkę".replace(",", " ")
        else:
            out["verdict"] = f"🔴 za wysoko (+{diff:.0f}% vs {cm:,} zł) — zbij by sprzedać".replace(",", " ")
    return out


# === SILNIK DYNAMICZNEJ WYCENY SPRZEDAŻY (bot do sprzedaży, punkt 1) ===========
# Odpowiada: "mam TEN rower — za ile go wystawić, za ile realnie zejdzie, w ile
# dni?" Łączy poziom rynku (olx_comparable_price, dopasowany do rocznika/przebiegu/
# baterii) z realnym cyklem życia ofert (olx_sell_forecast: cena domykająca, dni,
# % sprzedaży, typowy zjazd z ceny). Bez danych o realnych sprzedażach schodzi
# łagodnie do szacunku z cen wywoławczych i uczciwie to oznacza.
DROP_DEFAULT_PCT = 10  # zakładany zjazd z ceny gdy brak danych o realnym zbijaniu


def oferty_z_cechami(offers, details):
    """Łączy {url: cena} z rozpoznanymi cechami w listę dla cennika cech.
    Dane ze strony oferty biją zgadywanie z adresu URL."""
    out = []
    for url, price in (offers or {}).items():
        y, k, w = parse_olx_slug(url)
        rec = dict((details or {}).get(url) or {})
        rec["cena"] = price
        rec.setdefault("y", y)
        rec.setdefault("wh", w)
        if rec.get("km") is None and k is not None:
            rec["km"] = k
        out.append(rec)
    return out


def build_price_reco(offers, details, forecast, ref_year=None, ref_km=None,
                     ref_wh=None, mode="balans", ref_poziom=None):
    """Czysta funkcja (bez sieci — testowalna). Z gotowych danych OLX liczy
    rekomendację ceny. Zwraca dict albo None gdy za mało ofert.
    mode: 'szybko' (na cenie domykającej), 'balans' (zapas na negocjacje),
    'max' (górna półka rynku, dłużej)."""
    # Najpierw cennik cech: przelicza KAŻDĄ ofertę na naszą specyfikację zamiast
    # szukać bliźniaka. Gdy brak cennika (albo za mało cech) — stara metoda pasm.
    wyc = wycen_z_cennikiem(oferty_z_cechami(offers, details),
                            {"y": ref_year, "km": ref_km, "wh": ref_wh,
                             "poziom": ref_poziom})
    if wyc:
        market, n = wyc["cena"], wyc["n"]
        method = f"cennik cech — {wyc['cech_znanych']} cech znanych"
        widelki = wyc["widelki"]
    else:
        cp, method, n = olx_comparable_price(offers, ref_year, ref_km, ref_wh, details)
        if not cp:
            return None
        market, widelki = cp, None
    if forecast and forecast.get("clearing"):
        clearing = forecast["clearing"]
        clearing_est = False
        days = forecast.get("days")
        sell_through = forecast.get("sell_through")
        drop = forecast.get("drop_pct") or DROP_DEFAULT_PCT
    else:
        # brak zarejestrowanych sprzedaży → schodzą zwykle ~DROP% pod wywoławczą
        drop = DROP_DEFAULT_PCT
        clearing = int(market * (1 - drop / 100))
        clearing_est = True
        days = None
        sell_through = None

    if mode == "szybko":
        listing = clearing
    elif mode == "max":
        listing = max(market, int(clearing * (1 + drop / 100)))
    else:  # balans — pół typowego zjazdu jako pole do negocjacji
        listing = int(clearing * (1 + drop / 200))
    room = max(0, listing - clearing)

    if forecast and n >= 8:
        conf = "wysoka"
    elif (forecast and n >= LIQUIDITY_MIN_SAMPLES) or n >= 8:
        conf = "średnia"
    else:
        conf = "niska"

    return {"n": n, "method": method, "market": market, "clearing": clearing,
            "clearing_est": clearing_est, "listing": listing, "room": room,
            "days": days, "sell_through": sell_through, "drop_pct": round(drop),
            "mode": mode, "confidence": conf, "widelki": widelki}


def format_price_reco(query, r, ref_year=None, ref_km=None, ref_wh=None) -> str:
    """Składa wiadomość Telegram (HTML) z rekomendacji wyceny."""
    z = lambda v: f"{int(v):,}".replace(",", " ")
    if not r:
        return (f"🤷 Za mało ofert OLX dla „<b>{query}</b>”, żeby wycenić rzetelnie.\n"
                f"Sprawdź pisownię modelu albo podaj ogólniej (np. sama marka+model).")
    spec = []
    if ref_year: spec.append(str(ref_year))
    if ref_km is not None: spec.append(f"{z(ref_km)} km")
    if ref_wh: spec.append(f"{ref_wh} Wh")
    spec = f" ({', '.join(spec)})" if spec else ""
    lines = [f"💰 <b>Wycena sprzedaży: {query}{spec}</b>",
             f"<i>pewność: {r['confidence']} · {r['n']} porównywalnych "
             f"({r['method']})</i>", ""]
    rozrzut = ""
    if r.get("widelki"):
        rozrzut = f" <i>(typowo {z(r['widelki'][0])}–{z(r['widelki'][1])})</i>"
    lines.append(f"📊 Rynek (wywoławcze): ~{z(r['market'])} zł{rozrzut}")
    tag = " <i>(szacunek — brak zarejestrowanych sprzedaży)</i>" if r["clearing_est"] else ""
    lines.append(f"💸 Realnie schodzą po: ~{z(r['clearing'])} zł{tag}")
    if r["days"]:
        st = f" · sprzedaje się {r['sell_through']}%" if r["sell_through"] else ""
        lines.append(f"⏱ Czas sprzedaży: ~{r['days']} dni{st}")
    lines += ["", f"🎯 <b>Wystaw za: {z(r['listing'])} zł</b>"]
    if r["mode"] == "szybko":
        lines.append("⚡ Na cenie domykającej — zejdzie najszybciej, bez zbijania.")
    elif r["mode"] == "max":
        lines.append("⛰ Górna półka rynku — poczekasz dłużej, ale wyciśniesz maksa.")
    else:
        lines.append(f"⚖️ Zostawione ~{z(r['room'])} zł zapasu na negocjacje — "
                     f"kupiec „wywalczy” obniżkę i poczuje, że wygrał.")
    return "\n".join(lines)


def price_reco_for(query, ref_year=None, ref_km=None, ref_wh=None, mode="balans",
                   max_detail_fetch=8):
    """Wersja z siecią: ściąga oferty OLX, dobiera brakujące szczegóły dla kilku
    najbliższych ofert (koszt OK — wywoływane ręcznie), liczy rekomendację."""
    offers = olx_relevant_offers(query, fetch_olx_offers(query, pages=2))
    if not offers:
        return None
    details = dict(load_olx_details())          # kopia — nie brudzimy cache na dysku
    fetched = 0
    for url in list(offers):
        if fetched >= max_detail_fetch:
            break
        if url in details:
            continue
        d = fetch_olx_detail(url)
        if d:
            details[url] = d
        fetched += 1
    forecast = olx_sell_forecast(query)
    return build_price_reco(offers, details, forecast, ref_year, ref_km, ref_wh, mode)


# === ODCZYT SEGMENTÓW: gdzie rynek realnie kupuje, a gdzie martwa strefa =======
# Hipoteza właściciela: rynek PL jest barbell — góra bierze nówki na raty, dół
# bierze złom za 1500, a używka premium za ~6k leży w martwym środku. To NIE
# hipoteza do wierzenia — agregujemy CAŁY zebrany cykl życia ofert OLX (sprzedane
# vs wygasłe) w półki cenowe i pokazujemy sprzedawalność liczbowo. Wtedy w martwej
# strefie po prostu NIE kupujemy.
SEGMENT_BANDS = [(0, 3000, "do 3k zł"), (3000, 5000, "3–5k zł"),
                 (5000, 8000, "5–8k zł"), (8000, 12000, "8–12k zł"),
                 (12000, 16000, "12–16k zł"), (16000, 10**9, "16k+ zł")]
SEGMENT_MIN_SAMPLES = 4  # mniej ofert w paśmie = statystyka niewiarygodna


SEGMENT_HORYZONT = 30      # w tylu dniach pytamy "sprzedało się czy nie?"
RELISTING_MIN_DNI = 2      # zniknięcie szybsze niż to = podejrzenie wznowienia


def segment_liquidity(watch=None, horyzont=SEGMENT_HORYZONT, dzis=None):
    """Ile % ofert schodzi w `horyzont` dni — wg półki cenowej.

    POPRAWKA ISTOTNA: wcześniej liczyliśmy tylko oferty, które zniknęły, przez
    co wychodziło 100% sprzedaży wszędzie. Oferty, które WCIĄŻ WISZĄ, to nie
    brak danych — to informacja, że się nie sprzedały. Teraz wchodzą do
    mianownika, gdy wiszą już dłużej niż horyzont. Te młodsze pomijamy, bo
    o nich naprawdę jeszcze nic nie wiadomo (nie zgadujemy w żadną stronę)."""
    watch = watch if watch is not None else load_olx_watch()
    dzis = dzis or date.today()
    bands = {lbl: {"zeszlo": [], "nie_zeszlo": 0, "za_wczesnie": 0, "podejrzane": 0}
             for _, _, lbl in SEGMENT_BANDS}

    def band_for(price):
        for lo, hi, lbl in SEGMENT_BANDS:
            if lo <= price < hi:
                return lbl
        return None

    def wiek(o):
        try:
            return (dzis - date.fromisoformat(o["first"])).days
        except Exception:
            return None

    for w in watch.values():
        if not isinstance(w, dict):
            continue
        for s in w.get("sold_fast", []):            # potwierdzone zniknięcia
            if not (isinstance(s, dict) and s.get("price")):
                continue
            lbl = band_for(s["price"])
            if not lbl:
                continue
            d = s.get("days")
            if isinstance(d, int) and d < RELISTING_MIN_DNI:
                bands[lbl]["podejrzane"] += 1       # pewnie wznowienie, nie sprzedaż
            elif isinstance(d, int) and d <= horyzont:
                bands[lbl]["zeszlo"].append(s)
            else:
                bands[lbl]["nie_zeszlo"] += 1       # zeszło, ale po terminie
        for s in w.get("expired", []):              # wisiała bardzo długo
            if isinstance(s, dict) and s.get("price") and band_for(s["price"]):
                bands[band_for(s["price"])]["nie_zeszlo"] += 1
        for o in (w.get("offers") or {}).values():  # WCIĄŻ WISZĄCE — sedno poprawki
            if not (isinstance(o, dict) and o.get("price")):
                continue
            lbl = band_for(o["price"])
            if not lbl:
                continue
            v = wiek(o)
            if v is None or v < horyzont:
                bands[lbl]["za_wczesnie"] += 1      # za wcześnie na ocenę
            else:
                bands[lbl]["nie_zeszlo"] += 1       # wisi dłużej niż horyzont

    out = []
    for _, _, lbl in SEGMENT_BANDS:
        b = bands[lbl]
        n_ok, n_nie = len(b["zeszlo"]), b["nie_zeszlo"]
        total = n_ok + n_nie
        days = [s["days"] for s in b["zeszlo"] if isinstance(s.get("days"), int)]
        prices = [s["price"] for s in b["zeszlo"] if s.get("price")]
        out.append({"band": lbl, "n": total, "sold": n_ok, "expired": n_nie,
                    "za_wczesnie": b["za_wczesnie"], "podejrzane": b["podejrzane"],
                    "sell_through": round(n_ok / total * 100) if total else None,
                    "days": int(statistics.median(days)) if days else None,
                    "clearing": int(statistics.median(prices)) if prices else None})
    return out


def format_segments(rows) -> str:
    """Składa wiadomość Telegram (HTML) z tabelą sprzedawalności wg półki."""
    have = [r for r in rows if r["n"] >= SEGMENT_MIN_SAMPLES]
    if not have:
        return ("📊 <b>Sprzedawalność wg półki cenowej</b>\n"
                "Za mało zebranych danych o cyklu życia ofert — bot dopiero je "
                "zbiera (trzeba kilku–kilkunastu dni obserwacji OLX).")
    z = lambda v: f"{int(v):,}".replace(",", " ")
    lines = [f"📊 <b>Ile schodzi w {SEGMENT_HORYZONT} dni — wg półki cenowej</b>",
             "<i>oferty wciąż wiszące liczone jako niesprzedane</i>", ""]
    for r in rows:
        if r["n"] < SEGMENT_MIN_SAMPLES:
            continue
        st = r["sell_through"]
        icon = "🟢" if st >= 60 else "🟡" if st >= 35 else "🔴"
        d = f"~{r['days']} dni" if r["days"] else "—"
        lines.append(f"{icon} <b>{r['band']}</b>: schodzi {st}% · {d} · próbka {r['n']}")
    best = max(have, key=lambda r: r["sell_through"])
    worst = min(have, key=lambda r: r["sell_through"])
    lines += ["", f"✅ Najlepiej schodzi: <b>{best['band']}</b> ({best['sell_through']}%)"]
    if worst["band"] != best["band"]:
        lines.append(f"⛔ Najwolniej: <b>{worst['band']}</b> ({worst['sell_through']}%) "
                     f"— tu kapitał stoi najdłużej")
    # uczciwie: co jeszcze zaburza obraz
    podejrzane = sum(r.get("podejrzane", 0) for r in rows)
    wczesnie = sum(r.get("za_wczesnie", 0) for r in rows)
    if podejrzane or wczesnie:
        uwagi = []
        if podejrzane:
            uwagi.append(f"{podejrzane} zniknięć w <{RELISTING_MIN_DNI} dni pominięto "
                         f"(pewnie wznowienia, nie sprzedaże)")
        if wczesnie:
            uwagi.append(f"{wczesnie} ofert wisi za krótko, by je ocenić")
        lines.append(f"\n<i>ⓘ {'; '.join(uwagi)}</i>")
    return "\n".join(lines)


def annual_roi(profit_pln, buy_price_eur, liquidity_days):
    """Roczny zwrot z zaangażowanego kapitału. None gdy brak danych.
    ROI = zysk / kapitał × (365 / dni_do_sprzedaży)."""
    if profit_pln is None or not buy_price_eur or not liquidity_days:
        return None
    invested = buy_price_eur * get_eur_pln() + TRANSPORT_PLN
    if invested <= 0:
        return None
    return profit_pln / invested * (365 / max(liquidity_days, 1))


def get_demand_price(query: str):
    """Cena POPYTU: mediana ofert OLX które znikły szybko (= realne transakcje),
    a nie cen życzeniowych z wiszących ogłoszeń. None gdy brak świeżych danych."""
    w = load_olx_watch().get(query)
    if not w or not w.get("demand_median"):
        return None
    try:
        updated = date.fromisoformat(w["updated"])
        if (date.today() - updated).days <= DEMAND_MAX_AGE_DAYS:
            return w["demand_median"]
    except Exception:
        pass
    return None


HISTORY_MIN_SAMPLES = 5


HISTORY_YEAR_MIN_SAMPLES = 3  # dla porównania w obrębie tego samego rocznika


# --- Dziennik historii (#1 + #4): append-only, jedna linijka na ofertę. ---
# NIGDY nie kasowany ani nadpisywany — rośnie w nieskończoność. To trwały
# zapis rynku (nie do skopiowania przez konkurencję). Bogaty zestaw pól,
# żeby w przyszłości dało się liczyć deprecjację, spread DE↔PL i sezonowość.
HISTORY_FILE = Path("history.jsonl")
_history_cache = None


def append_history(model, price_num, ad_id=None, mileage_num=None, year=None,
                   olx_median=None, profit=None, buy_price=None, ev=None):
    """Dopisuje 1 wpis do dziennika finalistów (append-only, nigdy nie nadpisuje)."""
    if not model or not price_num:
        return
    try:
        rec = {"ts": date.today().isoformat(), "m": model, "p": int(price_num),
               "kurs": round(get_eur_pln(), 3)}                   # kurs EUR/PLN w tym momencie
        if ev:                  rec["ev"] = ev                    # typ zdarzenia (np. "drop")
        if ad_id:               rec["id"] = ad_id                 # referencja do ogłoszenia
        if mileage_num is not None: rec["km"] = mileage_num       # przebieg
        if year:                rec["y"] = year                   # rocznik
        if olx_median:          rec["olx"] = int(olx_median)      # cena PL w tym momencie (spread!)
        if buy_price:           rec["buy"] = int(buy_price)       # realna cena po negocjacji
        if profit is not None:  rec["profit"] = int(profit)       # szacowany zysk
        with HISTORY_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        log.error(f"append_history error: {e}")


# --- Log CAŁEGO rynku (#1 z audytu): każde widziane ogłoszenie, przed filtrami. ---
# ~500 wpisów/dzień zamiast ~10 — prawdziwe rozkłady cen, deprecjacja, geografia.
# Dane wyłącznie z listy (bez pobierania podstron) — koszt ~zero.
MARKET_FILE = Path("market.jsonl")


def log_market(listing, search_name):
    """Zapisuje 1 ogłoszenie do surowego logu rynku (append-only)."""
    try:
        title = listing.get("title", "")
        rec = {"ts": date.today().isoformat(), "id": listing["id"], "t": title,
               "p": listing.get("price_num"), "s": search_name,
               "kurs": round(get_eur_pln(), 3)}
        model = olx_query_for(title, None)
        if model:
            rec["m"] = model
        y = extract_year(title)
        if y:
            rec["y"] = y
        km = parse_mileage(_extract_mileage(title, ""))   # przebieg tylko z tytułu
        if km is not None:
            rec["km"] = km
        if listing.get("loc"):
            rec["loc"] = listing["loc"]
        # czas wystawienia + ile minut minęło, zanim bot je zobaczył — bez tego
        # w logu nie da się odróżnić wolnego bota od ogłoszenia, które weszło
        # do wyników z opóźnieniem, i każda skarga na spóźnienie jest zgadywanką
        if listing.get("posted"):
            rec["wyst"] = listing["posted"].isoformat()
            if listing.get("age_min") is not None:
                rec["op"] = int(listing["age_min"])
        with MARKET_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        log.error(f"log_market error: {e}")


def _load_history():
    global _history_cache
    if _history_cache is None:
        _history_cache = []
        if HISTORY_FILE.exists():
            try:
                for line in HISTORY_FILE.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line:
                        _history_cache.append(json.loads(line))
            except Exception:
                _history_cache = []
    return _history_cache


def price_trend(model, days=21):
    """Trend ceny modelu z własnego dziennika: % zmiany mediany
    (świeższa połowa okna vs starsza). None gdy za mało danych."""
    if not model:
        return None
    try:
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        mid = (date.today() - timedelta(days=days // 2)).isoformat()
        older, newer = [], []
        for r in _load_history():
            if r.get("m") != model or r.get("ts", "") < cutoff:
                continue
            (newer if r["ts"] >= mid else older).append(r["p"])
        if len(older) < 4 or len(newer) < 4:
            return None
        o, n = statistics.median(older), statistics.median(newer)
        if o <= 0:
            return None
        return int((n - o) / o * 100)
    except Exception:
        return None


def build_price_history(seen: dict) -> dict:
    """Cennik referencyjny per model z własnej historii skanów (seen.json).
    Trzyma (cena, rocznik) — porównanie może być zawężone do rocznika."""
    hist = {}
    for ad_id, v in seen.items():
        if not isinstance(v, dict):
            continue
        title, price = v.get("title"), v.get("price_num")
        if not title or not price:
            continue
        key = olx_query_for(title, None)
        if key:
            yr = v.get("year") or extract_year(title)
            hist.setdefault(key, []).append((price, yr))
    return hist


def price_history_signal(title: str, price_num, year, hist: dict):
    """Porównanie ceny z historią modelu — najpierw w obrębie rocznika,
    fallback do całego modelu. Zwraca (linia_wiadomości|None, bonus_score)."""
    if not price_num:
        return None, 0
    key = olx_query_for(title, None)
    if not key:
        return None, 0
    entries = hist.get(key, [])
    same_year = [p for p, y in entries if year and y == year]
    if len(same_year) >= HISTORY_YEAR_MIN_SAMPLES:
        prices, label = same_year, f'"{key}" {year}'
    elif len(entries) >= HISTORY_MIN_SAMPLES:
        prices, label = [p for p, _ in entries], f'"{key}"'
    else:
        return None, 0
    mn, med = min(prices), int(statistics.median(prices))
    if price_num <= mn:
        return (f"\n🏆 NAJTAŃSZY {label} z {len(prices)} ofert (mediana {med} €)!", 15)
    pct = int((med - price_num) / med * 100)
    if pct >= 15:
        return (f"\n📊 {pct}% taniej niż mediana {label} ({med} € z {len(prices)} ofert)", 8)
    return None, 0


# Silnik marży negocjacyjnej — kalibracja z realnego rynku:
# rower 2.500 € "VB" schodzi ~10% (200-300 €) już na etapie wiadomości.
NEGO_BASE_VB = 0.10       # baza dla ceny "VB" (do negocjacji)
NEGO_BASE_OPEN = 0.05     # brak znacznika — trochę luzu i tak jest
NEGO_BASE_FIXED = 0.02    # "Festpreis" — mur, ale czasem drgnie
NEGO_MAX = 0.18           # sufit realnego zejścia zdalnie
# Targ na miejscu, przy oględzinach — ETAP DRUGI, po tym co ustalone zdalnie.
# Publicznych danych o nim NIE MA (sprawdzone 23.08.2026): jedyny duży pomiar
# targowania się to eBay Best Offer (88 mln ofert, sprzedaż po 73% ceny przy
# negocjacji), ale to targ przez formularz, przed spotkaniem, i przy średnim
# przedmiocie za 95 $. Najbliższy odpowiednik to niemieckie poradniki o
# prywatnym zakupie auta: realnie ~10%, powyżej 20% "nikt nie traktuje
# poważnie". Stąd 10% — jako ZAŁOŻENIE, nie pomiar. Do podmiany na własne
# dane, gdy uzbiera się kilkanaście zakupów.
NEGO_NA_MIEJSCU = 0.10
NEGO_MAX_LACZNIE = 0.22   # sufit dla obu etapów razem


def negotiation_headroom(price_num, price_str, desc):
    """Szacowany luz negocjacyjny (%) — ile realnie zejdziesz z ceny wywoławczej.
    Zwraca (procent 0-0.18, lista powodów)."""
    if not price_num:
        return 0.0, []
    text = f"{price_str} {desc or ''}".lower()
    # "Festpreis" = mur: żadne bonusy się nie doliczają
    if re.search(r'festpreis|fixpreis|preis ist fix|nicht verhandel|keine verhandlung|nachlass ausgeschlossen', text):
        return NEGO_BASE_FIXED, ["Festpreis (mur)"]

    reasons = []
    if re.search(r'\bvb\b|verhandlungsbasis|verhandelbar|preis verhandel', text):
        pct = NEGO_BASE_VB
        reasons.append("VB")
    else:
        pct = NEGO_BASE_OPEN
    # sygnały motywacji sprzedawcy → większy luz
    if re.search(r'muss weg|schnell verkauf|keine zeit|zeitmangel|umzug|umständehalber|'
                 r'aus platzgr|brauche.{0,10}geld|neuanschaffung|kein bedarf|nicht mehr genutzt', text):
        pct += 0.04
        reasons.append("presja sprzedawcy")
    # okrągła cena = miejsce zostawione na negocjację
    if price_num >= 1000 and price_num % 100 == 0:
        pct += 0.02
        reasons.append("okrągła cena")
    return min(pct, NEGO_MAX), reasons


def realistic_buy_price(price_num, price_str, desc):
    """Cena, którą warto ZAPROPONOWAĆ zdalnie (zaokrąglona do 10 €).

    To jeszcze nie jest cena, którą zapłacisz — patrz `cena_po_ogledzinach`."""
    if not price_num:
        return None, 0.0, []
    pct, reasons = negotiation_headroom(price_num, price_str, desc)
    return int(round(price_num * (1 - pct) / 10) * 10), pct, reasons


def cena_po_ogledzinach(price_num, pct_zdalny, reasons=None):
    """Cena, którą realnie zapłacisz: targ zdalny PLUS targ na miejscu.

    Dwa etapy składają się mnożnikowo, a nie przez dodawanie — kto zbił 10%
    wiadomością, nie zbije drugich 10% w garażu od tej samej kwoty. Sprzedawca,
    który napisał "Festpreis", jest twardy również na żywo, więc dostaje ułamek
    tego luzu. Zwraca (cena, laczny_procent)."""
    if not price_num:
        return None, 0.0
    na_miejscu = (NEGO_NA_MIEJSCU * 0.4 if "Festpreis (mur)" in (reasons or [])
                  else NEGO_NA_MIEJSCU)
    laczny = 1 - (1 - pct_zdalny) * (1 - na_miejscu)
    laczny = min(laczny, NEGO_MAX_LACZNIE)
    return int(round(price_num * (1 - laczny) / 10) * 10), laczny


def mileage_factor(km) -> float:
    """Korekta wartości roweru względem przebiegu vs mediany rynkowej (~1500km)."""
    if km is None:
        return 1.0   # brak danych = zakładamy średni stan
    if km < 300:     return 1.15  # prawie nowy +15%
    if km < 800:     return 1.08  # bardzo mało używany +8%
    if km < 1500:    return 1.03  # mało używany +3%
    if km < 2500:    return 0.95  # średni przebieg -5%
    return          0.85          # duży przebieg -15%


def year_factor(model_year) -> float:
    """Korekta wartości roweru względem rocznika vs typowego roweru na rynku
    wtórnym (~3 lata). Mediana OLX miesza roczniki — bez tego rower 2024 i 2018
    o tej samej nazwie dostawałyby tę samą wycenę odsprzedaży."""
    if not model_year:
        return 1.0
    ref = CURRENT_YEAR - 3            # typowy wiek roweru w medianie OLX
    factor = 1.0 + 0.08 * (model_year - ref)   # ~8% na rok
    return max(0.70, min(1.30, factor))


def cena_sprzedazy_realna(price_pl_pln):
    """Ile realnie dostaniesz, a nie ile wystawisz.

    SYMETRIA: skoro po stronie zakupu zakładamy, że utargujesz swoje przy
    oględzinach, to po stronie sprzedaży trzeba założyć to samo — Twój kupujący
    też przyjedzie i też będzie zbijał. Bez tego model jest optymistyczny
    dwustronnie i dokładnie tak powstała pomyłka 4x na Cube (szacunek ~5 500 zł
    wobec 1 300 zł realnego zysku).

    Dotyczy to również "ceny domykającej": to mediana OSTATNICH CEN Z OGŁOSZEŃ,
    które szybko znikły (summary.py) — czyli też cena wywoławcza. Targ kupującego
    odbył się już po niej i nigdzie nie jest zapisany."""
    if not price_pl_pln:
        return None
    return int(price_pl_pln * (1 - NEGO_NA_MIEJSCU))


def calc_profit(price_de_eur: int, price_pl_pln: int, km=None, year=None,
                juz_skorygowana: bool = False) -> int:
    """Zysk z odsprzedaży w PL. `juz_skorygowana=True` gdy cena PL pochodzi
    z cennika cech — jest wtedy PRZELICZONA na ten konkretny rower i ponowne
    mnożenie przez ręczne mileage_factor/year_factor liczyłoby korektę
    drugi raz (raz z rynku, raz z sufitu).

    `price_pl_pln` to cena WYWOŁAWCZA — targ kupującego zdejmuje z niej
    cena_sprzedazy_realna()."""
    kurs = get_eur_pln()
    koszt_de = price_de_eur * kurs
    adjusted_pl = (price_pl_pln if juz_skorygowana
                   else price_pl_pln * mileage_factor(km) * year_factor(year))
    return int(cena_sprzedazy_realna(adjusted_pl) - koszt_de - TRANSPORT_PLN)


def max_profitable_mileage(price_de_eur: int, price_pl_pln: int, min_profit: int = 500, year=None) -> str:
    """Zwraca max przebieg przy którym deal jest opłacalny (zysk >= min_profit PLN)."""
    kurs = get_eur_pln()
    # ta sama symetria co w calc_profit: liczy się to, co DOSTANIESZ
    price_pl_pln = cena_sprzedazy_realna(price_pl_pln * year_factor(year))
    koszt_de = price_de_eur * kurs + TRANSPORT_PLN + min_profit
    needed_factor = koszt_de / price_pl_pln

    if needed_factor <= 0.85:
        return "do 3.000 km"
    if needed_factor <= 0.95:
        return "do 2.500 km"
    if needed_factor <= 1.03:
        return "do 1.500 km"
    if needed_factor <= 1.08:
        return "do 800 km"
    if needed_factor <= 1.15:
        return "do 300 km"
    return "nieopłacalne nawet nowy"


SEEN_MAX_AGE_DAYS = 90


def load_seen() -> dict:
    if SEEN_FILE.exists():
        data = json.loads(SEEN_FILE.read_text())
        # migracja ze starego formatu (lista ID) do nowego (dict)
        if isinstance(data, list):
            return {ad_id: {} for ad_id in data}
        return data
    return {}


def prune_seen(seen: dict) -> dict:
    """Usuwa wpisy starsze niż SEEN_MAX_AGE_DAYS — ogłoszenia dawno wygasły,
    a plik commitowany co 5 min nie może rosnąć w nieskończoność."""
    cutoff = (date.today() - timedelta(days=SEEN_MAX_AGE_DAYS)).isoformat()
    today = date.today().isoformat()
    pruned = {}
    for ad_id, v in seen.items():
        if not isinstance(v, dict):
            continue
        # legacy wpisy bez daty dostają dzisiejszą (zaczyna im tykać zegar)
        if not v.get("date"):
            v = dict(v, date=today)
        if v["date"] >= cutoff:
            pruned[ad_id] = v
    removed = len(seen) - len(pruned)
    if removed:
        log.info(f"Usunięto {removed} wpisów starszych niż {SEEN_MAX_AGE_DAYS} dni")
    return pruned


def save_seen(seen: dict):
    SEEN_FILE.write_text(json.dumps(seen, ensure_ascii=False, indent=2))


DEDUP_DAYS = 14        # okno w którym re-listing tego samego roweru = duplikat
DEDUP_PRICE_PCT = 0.03  # cena może się nieznacznie zmienić przy ponownym wystawieniu
DEDUP_KM_TOL = 300      # tolerancja przebiegu (nasze odczyty i edycje sprzedawcy)


def dedup_key(title):
    """Klucz do dedupu: rozpoznany model, a gdy nieznany — znormalizowany tytuł.
    Bez fallbacku dwa identyczne ogłoszenia modelu spoza listy szły podwójnie."""
    model = olx_query_for(title, None)
    if model:
        return model
    t = re.sub(r'[^a-z0-9]+', ' ', (title or "").lower()).strip()
    return t or None


def build_recent_index(seen: dict) -> list:
    """Lista (klucz, cena, przebieg, data) z powiadomionych ofert z 14 dni —
    do tolerancyjnego wykrywania re-listingów (sztywne kubełki gubiły granice)."""
    cutoff = (date.today() - timedelta(days=DEDUP_DAYS)).isoformat()
    idx = []
    for v in seen.values():
        if not isinstance(v, dict) or v.get("score") is None:
            continue
        if v.get("date", "") < cutoff:
            continue
        key = dedup_key(v.get("title", ""))
        if key and v.get("price_num"):
            idx.append((key, v["price_num"], v.get("mileage_num"), v.get("date")))
    return idx


def find_relisting(index: list, title, price_num, mileage_num):
    """Zwraca datę pierwotnego ogłoszenia jeśli to re-listing, inaczej None.
    Dopasowanie: ten sam klucz + cena ±3% + przebieg ±300 km (lub brak danych)."""
    model = dedup_key(title)
    if not model or not price_num:
        return None
    for m, p, km, d in index:
        if m != model:
            continue
        if abs(p - price_num) > price_num * DEDUP_PRICE_PCT:
            continue
        if km is not None and mileage_num is not None and abs(km - mileage_num) > DEDUP_KM_TOL:
            continue
        return d
    return None


ALBUM_MAX = 10          # twardy limit Telegrama na jeden album


def send_telegram_album(adresy) -> bool:
    """Reszta zdjęć jako album POD wiadomością, BEZ dźwięku.

    Telegram nie pozwala doczepić przycisków do albumu, więc kolejność jest
    odwrotna niż mogłoby się wydawać: najpierw leci wiadomość ze zdjęciem
    głównym i przyciskami (to ona robi powiadomienie), a dopiero potem reszta
    galerii — cicho, żeby jeden rower nie brzęczał w telefonie dwa razy."""
    adresy = [a for a in (adresy or []) if a][:ALBUM_MAX]
    if len(adresy) < 2:
        return False
    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMediaGroup"
    media = [{"type": "photo", "media": a} for a in adresy]
    try:
        r = requests.post(api_url, json={"chat_id": TELEGRAM_CHAT_ID, "media": media,
                                         "disable_notification": True}, timeout=20)
        if r.ok:
            return True
        log.warning(f"sendMediaGroup {r.status_code}: {r.text[:120]}")
    except Exception as e:
        log.warning(f"sendMediaGroup błąd: {str(e)[:80]}")
    return False


def send_telegram(text: str, klawiatura=None):
    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    if klawiatura:
        payload["reply_markup"] = klawiatura
    for attempt in range(3):
        try:
            r = requests.post(api_url, json=payload, timeout=10)
            if r.status_code == 429:
                retry_after = r.json().get("parameters", {}).get("retry_after", 3)
                log.warning(f"Telegram rate limit, czekam {retry_after}s")
                time.sleep(retry_after + 1)
                continue
            r.raise_for_status()
            return
        except Exception as e:
            log.error(f"Telegram error (próba {attempt + 1}/3): {e}")
            time.sleep(2)


# === KOMENDY Z TELEGRAMA (kanał wejścia dla bota do sprzedaży) ================
# Bot dotąd tylko WYSYŁAŁ. Tu czyta odpowiedzi właściciela (getUpdates), żeby dało
# się odpytać go z telefonu: „/wycen cube stereo hybrid 2018 2300 400”.
# Offset pilnuje, by nie przetwarzać tej samej wiadomości dwa razy. Wszystko w
# try/except — komendy to dodatek, nigdy nie mogą wywalić głównego skanu.
TELEGRAM_OFFSET_FILE = Path("telegram_offset.json")


# --- JEDNA GOTOWA WIADOMOŚĆ DO SPRZEDAWCY ----------------------------------
# Zasada: JEDNA wiadomość, nie trzy. Sprzedawca odpowiada raz, a Ty kopiujesz
# raz. Pytamy wyłącznie o to, czego bot NIE wyczytał — pytanie o przebieg
# podany w ogłoszeniu wygląda na niechlujstwo i zniechęca do odpowiedzi.
#
# Tekst jedzie w przycisku "kopiuj" (Telegram: copy_text), więc zajmuje jedną
# linijkę ekranu zamiast bloku cytatu. Limit API to 256 znaków — przy jego
# przekroczeniu odpadają najpierw uprzejmości, potem oferta, NIGDY pytania.
PRZYCISK_MAX = 256

_PYTANIA_DE = {
    "przebieg": "wie viele km es gelaufen ist",
    "rama": "welche Rahmengröße es hat",
    "bateria": "wie viel Wh der Akku hat",
}


def wiadomosc_do_sprzedawcy(braki):
    """Pierwszy kontakt: pytania o braki. BEZ ceny — patrz `wiadomosc_oferta`."""
    pytania = [_PYTANIA_DE[b] for b in ("przebieg", "rama", "bateria") if b in (braki or [])]
    baza = "Hallo! Ist das Rad noch verfügbar?"
    if not pytania:
        return f"{baza} Ich hole kurzfristig mit Bargeld ab. Danke!"
    if len(pytania) == 1:
        srodek = f" Können Sie mir sagen, {pytania[0]}?"
    else:
        srodek = f" Können Sie mir sagen, {', '.join(pytania[:-1])} und {pytania[-1]}?"
    for ogon in (" Ich hole kurzfristig mit Bargeld ab. Danke!", " Danke!", ""):
        if len(baza + srodek + ogon) <= PRZYCISK_MAX:
            return baza + srodek + ogon
    return (baza + srodek)[:PRZYCISK_MAX]


def wiadomosc_oferta(buy_price, po_pytaniach: bool):
    """Propozycja ceny — ZAWSZE osobna wiadomość, wysyłana dopiero gdy sprzedawca
    odpisze.

    Zasada od użytkownika (22.08): oferta doklejona do pierwszego kontaktu
    potrafi zabić rozmowę, zanim się zacznie — jeśli kwota wyda się za niska,
    sprzedawca po prostu nie odpowie i nie dowiesz się nawet o przebiegu.
    Najpierw wyciągamy informacje, targujemy się później.

    `po_pytaniach` zmienia otwarcie: gdy pytania już poszły, to kontynuacja
    rozmowy, a nie zaczepka do nieznajomego."""
    if not buy_price:
        return None
    if po_pytaniach:
        return (f"Danke für die Infos! Wären {buy_price} € möglich? "
                f"Ich könnte kurzfristig mit Bargeld abholen.")[:PRZYCISK_MAX]
    return (f"Hallo! Ist das Rad noch verfügbar? Wären {buy_price} € möglich? "
            f"Ich hole kurzfristig mit Bargeld ab. Danke!")[:PRZYCISK_MAX]


TELEGRAM_PODPIS_MAX = 1024   # limit Telegrama na podpis pod zdjęciem


def klawiatura_kopiuj(przyciski):
    """Przyciski, które po tapnięciu wrzucają gotowy tekst do schowka.

    `przyciski` to lista par (napis, tekst). Każdy w osobnym rzędzie, żeby
    napis się nie ucinał — to i tak jedna linijka ekranu na przycisk, zamiast
    kilku linijek bloku cytatu. Telegram nazywa to copy_text; limit 256 znaków."""
    rzedy = [[{"text": napis, "copy_text": {"text": tekst[:PRZYCISK_MAX]}}]
             for napis, tekst in (przyciski or []) if tekst]
    return {"inline_keyboard": rzedy} if rzedy else None


def send_telegram_photo(foto_url: str, caption: str, klawiatura=None) -> bool:
    """Wysyła zdjęcie roweru z podpisem. False = nie poszło, trzeba tekstem.

    Zdjęcie w wiadomości jest kilka razy większe niż podgląd linka, a na
    ekranie blokady widać je razem z pierwszą linijką podpisu — czyli zyskiem
    i ceną. Cała ozdoba jest jednak podporządkowana zasadzie: powiadomienie
    nie może zginąć przez to, że obrazek się nie pobrał."""
    if not foto_url:
        return False
    if len(caption) > TELEGRAM_PODPIS_MAX:
        return False
    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "photo": foto_url,
               "caption": caption, "parse_mode": "HTML"}
    if klawiatura:
        payload["reply_markup"] = klawiatura
    for proba in range(2):
        try:
            r = requests.post(api_url, json=payload, timeout=15)
            if r.status_code == 429:
                time.sleep(r.json().get("parameters", {}).get("retry_after", 3) + 1)
                continue
            if r.ok:
                return True
            # najczęściej: Telegram nie pobrał obrazka z serwera Kleinanzeigen
            log.warning(f"sendPhoto {r.status_code}: {r.text[:120]}")
            return False
        except Exception as e:
            log.warning(f"sendPhoto błąd: {str(e)[:80]}")
            time.sleep(1)
    return False


# --- TŁUMACZENIE OPISU (za darmo) ------------------------------------------
# MyMemory: bez klucza, bez rejestracji, 5 tys. słów na dobę anonimowo —
# przy ~20 powiadomieniach dziennie (~1,6 tys. słów) mieści się z zapasem.
#
# UWAGA NA JAKOŚĆ: w teście 22.08 przetłumaczyło "Nur 2000 km gelaufen" jako
# "Spacerowaliśmy niecałe 2000 km". Dlatego opis jest w wiadomości WYŁĄCZNIE
# prozą-ozdobnikiem, a każda liczba, na której podejmujesz decyzję (przebieg,
# bateria, rocznik, cena), pochodzi z własnego parsera bota i stoi osobno.
# Nigdy nie przenosić liczb z tłumaczenia do linijek decyzyjnych.
TLUMACZ_URL = "https://api.mymemory.translated.net/get"
TLUMACZ_MAX_ZNAKOW = 480     # limit pojedynczego zapytania


def tlumacz_opis(tekst: str):
    """Niemiecki opis → polski. None, gdy się nie udało (wtedy oryginał)."""
    if not tekst:
        return None
    czysty = re.sub(r'\s+', ' ', tekst).strip()[:TLUMACZ_MAX_ZNAKOW]
    if len(czysty) < 20:
        return None
    try:
        r = requests.get(TLUMACZ_URL, params={"q": czysty, "langpair": "de|pl"},
                         timeout=15)
        r.raise_for_status()
        d = r.json()
        if d.get("responseStatus") not in (200, "200"):
            log.warning(f"tłumacz: status {d.get('responseStatus')}")
            return None
        out = (d.get("responseData") or {}).get("translatedText") or ""
        out = re.sub(r'\s+', ' ', out).strip()
        # serwis czasem oddaje wejście bez zmian albo komunikat o limicie
        if not out or out.upper().startswith("MYMEMORY WARNING") or out == czysty:
            return None
        return out
    except Exception as e:
        log.warning(f"tłumacz błąd: {str(e)[:80]}")
        return None


def read_telegram_commands() -> list:
    """Zwraca listę nowych tekstów od właściciela (z jego czatu). Aktualizuje
    offset w pliku. Bezpieczne — każdy błąd łyka i zwraca []."""
    try:
        offset = 0
        if TELEGRAM_OFFSET_FILE.exists():
            offset = json.loads(TELEGRAM_OFFSET_FILE.read_text()).get("offset", 0)
        r = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates",
            params={"offset": offset, "timeout": 0}, timeout=15)
        data = r.json()
        if not data.get("ok"):
            return []
        texts, max_id = [], offset - 1
        for upd in data.get("result", []):
            max_id = max(max_id, upd.get("update_id", max_id))
            msg = upd.get("message") or upd.get("channel_post") or {}
            if str((msg.get("chat") or {}).get("id")) != str(TELEGRAM_CHAT_ID):
                continue
            t = (msg.get("text") or "").strip()
            if t:
                texts.append(t)
        if data.get("result"):
            TELEGRAM_OFFSET_FILE.write_text(json.dumps({"offset": max_id + 1}))
        return texts
    except Exception as e:
        log.error(f"read_telegram_commands error: {e}")
    return []


def parse_wycen_command(text: str):
    """'/wycen cube stereo hybrid 2018 2300 400 szybko' →
    (query, year, km, wh, mode). Liczby rozpoznawane po zakresie: 2015-2026=rok,
    300-1000=bateria(Wh), reszta=przebieg. 'szybko'/'max' ustawia tryb.
    None gdy to nie komenda wyceny."""
    text = text.strip()
    if not re.match(r'/?wyce[nń]\b', text, re.I):
        return None
    body = re.sub(r'^/?wyce[nń]\s*', '', text, flags=re.I)
    mode = "balans"
    if re.search(r'\bszybk', body, re.I):
        mode = "szybko"
    elif re.search(r'\b(max|maks)', body, re.I):
        mode = "max"
    body = re.sub(r'\b(szybk\w*|maks\w*|max\w*)\b', ' ', body, flags=re.I)
    nums = [int(x) for x in re.findall(r'\d{2,5}', body)]
    year = next((x for x in nums if 2015 <= x <= 2026), None)
    wh = next((x for x in nums if 300 <= x <= 1000), None)
    km = next((x for x in nums if x != year and x != wh), None)
    query = re.sub(r'\s+', ' ', re.sub(r'\d{2,5}', ' ', body)).strip().lower()
    if not query:
        return None
    return query, year, km, wh, mode


def handle_status() -> str:
    """Raport zdrowia liczony NA RUNNERZE — czyli w tym samym środowisku, w
    którym bot faktycznie pracuje. Odpowiada na pytanie „czy OLX nas wpuszcza"
    bez grzebania w logach GitHub Actions."""
    z = lambda v: f"{int(v):,}".replace(",", " ")
    L = ["🩺 <b>DealHawk — stan zdrowia</b>", ""]

    r = olx_get("https://www.olx.pl/sport-hobby/rowery/q-rower-elektryczny/", timeout=20)
    st = r.status_code if r is not None else "brak odpowiedzi"
    kart = len(parse_olx_cards(r.text)) if (r is not None and r.status_code == 200) else 0
    kb = (len(r.text) // 1024) if r is not None else 0
    L.append(f"🇵🇱 <b>OLX (strona): {'wpuszcza ✅' if kart else 'BLOKUJE 🚫'}</b>\n"
             f"   HTTP {st} · {kart} kafelków · {z(kb)} kB")
    try:
        ra = olx_get("https://www.olx.pl/api/v1/offers/?offset=0&limit=40"
                     "&query=cube+stereo+hybrid", timeout=20)
        rek = len((((ra.json() or {}).get("data")) or [])) if (
            ra is not None and ra.status_code == 200) else 0
        L.append(f"🔌 <b>OLX (API): {'działa ✅' if rek else 'BLOKUJE 🚫'}</b>\n"
                 f"   HTTP {ra.status_code if ra is not None else '-'} · {rek} rekordów")
    except Exception as e:
        L.append(f"🔌 OLX (API): błąd — {str(e)[:60]}")

    # Diagnostyka przekaźnika — bez niej "brak odpowiedzi" niczego nie mówi
    if OLX_RELAY_URL:
        kod = olx_diag().get("przekaznik_status")
        opis = {401: "ODRZUCA KLUCZ — sekret OLX_RELAY_KEY w GitHubie różni się\n"
                     "   od zmiennej KLUCZ w Workerze (uwaga na spację/enter na końcu)",
                429: "przekroczony limit zapytań na minutę",
                503: "Worker nie ma ustawionej zmiennej KLUCZ",
                403: "adres poza dozwoloną listą ścieżek"}.get(kod)
        if przekaznik_zyje() is False:
            L.append("🛰 <b>Przekaźnik: NIE ODPOWIADA</b> 🚫\n"
                     "   sprawdź Workera na dash.cloudflare.com")
        elif kod:
            L.append(f"🛰 <b>Przekaźnik: żyje, ale odmawia (HTTP {kod})</b>\n   {opis}")
        else:
            L.append("🛰 <b>Przekaźnik: OK</b> ✅")
    else:
        L.append("🛰 <b>Przekaźnik: NIE SKONFIGUROWANY</b>\n"
                 "   brak sekretów OLX_RELAY_URL / OLX_RELAY_KEY w GitHubie")

    try:
        ph = json.loads(PARSE_STATE_FILE.read_text())
    except Exception:
        ph = {}
    if ph.get("title_rate") is not None:
        L.append(f"🇩🇪 <b>Kleinanzeigen: {'✅' if ph.get('ok', True) else '⚠️'}</b>\n"
                 f"   odczyt tytułów {int(ph['title_rate'] * 100)}%, "
                 f"cen {int(ph.get('price_rate', 0) * 100)}%")
    # Kanał decyduje o tym, jak SZYBKO przychodzą rowery. Gdy leży, ogłoszenia
    # nadal płyną z zapytań kluczowych, tylko wolniej i w losowej kolejności —
    # dlatego to nie jest alarm, ale musi być widoczne na żądanie.
    kanal = ph.get("kanal")
    if kanal:
        wszystkie_ok = all(cz.strip().endswith(": ok") for cz in kanal.split("·"))
        L.append(f"⚡ <b>Półki nowości: {'✅' if wszystkie_ok else '⚠️'}</b>")
        for kan in KANALY:
            zn = load_feed_znacznik(kan["typ"])
            stan_kan = next((cz.strip() for cz in kanal.split("·")
                             if cz.strip().startswith(kan["nazwa"])), "")
            dobry = stan_kan.endswith(": ok")
            L.append(f"   {'✅' if dobry else '⚠️'} {kan['nazwa']}: "
                     + ("czyta po kolei" if dobry else stan_kan.split(": ", 1)[-1])
                     + (f", ostatnie {format_age(ad_age_minutes(zn))}" if zn else ""))
        if not wszystkie_ok:
            L.append("   (rowery i tak przychodzą, tylko wolniej)")

    L.append("")
    watch = load_olx_watch()
    dat = [v.get("updated") for v in watch.values() if isinstance(v, dict) and v.get("updated")]
    ofert = sum(len(v.get("offers") or {}) for v in watch.values() if isinstance(v, dict))
    L.append(f"📅 Obserwacja OLX: {max(dat) if dat else 'brak'} "
             f"({len(watch)} modeli, {ofert} ofert)")
    cen = load_cennik()
    if cen.get("cechy"):
        L.append(f"💰 Cennik cech: {cen.get('data', '?')} ({cen.get('n_ofert', 0)} ofert)")
    try:
        L.append(f"👁 Dozorca: śledzi {len(json.loads(Path('olx_stan.json').read_text()))} ofert")
    except Exception:
        L.append("👁 Dozorca: jeszcze nie ruszył")
    return "\n".join(L)


def handle_wycen(query, year, km, wh, mode) -> str:
    """Odpytuje silnik wyceny i składa odpowiedź (z siecią)."""
    r = price_reco_for(query, year, km, wh, mode)
    return format_price_reco(query, r, year, km, wh)


def process_telegram_commands():
    """Przetwarza komendy z Telegrama (/wycen, /segmenty). Odporne na błędy."""
    for cmd in read_telegram_commands():
        try:
            if re.match(r'/?(status|zdrowie|dziala)', cmd.strip(), re.I):
                log.info(f"komenda /status: {cmd}")
                send_telegram(handle_status())
                continue
            if re.match(r'/?(segment|rynek)', cmd.strip(), re.I):
                log.info(f"komenda /segmenty: {cmd}")
                send_telegram(format_segments(segment_liquidity()))
                continue
            parsed = parse_wycen_command(cmd)
            if parsed:
                log.info(f"komenda /wycen: {cmd}")
                send_telegram(handle_wycen(*parsed))
        except Exception as e:
            log.error(f"process_telegram_commands błąd dla '{cmd}': {e}")
            send_telegram("⚠️ Nie udało się przetworzyć. Komendy:\n"
                          "<code>/wycen model rok przebieg bateria</code> — wycena sprzedaży\n"
                          "<code>/segmenty</code> — sprzedawalność wg półki cenowej")


def parse_price(price_str: str) -> object:
    m = re.search(r'[\d.,]+', price_str.replace(".", "").replace(",", ""))
    if m:
        try:
            return int(m.group())
        except ValueError:
            pass
    return None


def parse_mileage(mileage_str: str) -> object:
    if not mileage_str or mileage_str == "brak danych":
        return None
    m = re.search(r'[\d.,]+', mileage_str.replace(".", "").replace(",", ""))
    if m:
        try:
            return int(m.group())
        except ValueError:
            pass
    return None


def score_listing(listing: dict, median_price) -> int:
    score = 0
    title_lower = listing["title"].lower()

    # 1. Cena vs mediana wyszukiwania (0-40 pkt)
    price_num = listing.get("price_num")
    if price_num and median_price:
        discount_pct = (median_price - price_num) / median_price * 100
        score += max(0, min(40, int(discount_pct * 1.5)))

    # 2. Przebieg (0-30 pkt)
    km = listing.get("mileage_num")
    if km is not None:
        score += max(0, int(30 - (km / 100)))
    else:
        score += 15  # brak danych = neutralne

    # 3. Stan (0-15 pkt)
    for kw in GOOD_CONDITION:
        if kw in title_lower:
            score += 15
            break

    # 4. Marka z dobrym resale value w PL (0-15 pkt)
    for brand in PREMIUM_BRANDS:
        if brand in title_lower:
            score += 15
            break

    return score


def is_junk(title: str) -> bool:
    t = title.lower().strip()
    if any(kw in t for kw in SKIP_KEYWORDS):
        return True
    if any(re.search(p, t) for p in SKIP_PATTERNS):
        return True
    first_word = t.split()[0] if t.split() else ""
    return first_word in PART_TITLE_PREFIXES


MOTOR_BRANDS = [
    "bosch",
    "specialized turbo", "specialized kenevo", "specialized levo",
]

def has_known_motor(title: str, description_text) -> bool:
    """Zwraca True jeśli tytuł lub opis zawiera markę silnika elektrycznego.
    description_text=None (błąd pobrania) → kredyt zaufania, nie odrzucamy."""
    if description_text is None:
        return True
    combined = (title + " " + description_text).lower()
    return any(brand in combined for brand in MOTOR_BRANDS)


def is_too_worn(mileage_num) -> bool:
    if mileage_num is None:
        return False
    return mileage_num > MAX_MILEAGE


SMALL_BATTERY_WH = 500  # poniżej = słaba odsprzedaż w PL (kupujący patrzą na zasięg)


def battery_wh(title, desc):
    """Największa pojemność baterii w Wh z tytułu+opisu (zakres 200-1000)."""
    text = f"{title} {desc or ''}"
    vals = [int(m) for m in re.findall(r'(\d{3,4})\s*wh\b', text, re.I)]
    vals = [v for v in vals if 200 <= v <= 1000]
    return max(vals) if vals else None


def is_small_battery(title, desc) -> bool:
    """Model 'SL' (Super Light) lub bateria <500 Wh — lekki rower, ale w PL
    trudny do odsprzedaży (mały zasięg = mała pula kupujących)."""
    t = title.lower()
    if re.search(r'levo sl|kenevo sl|\bsl comp\b|\bsl expert\b', t):
        return True
    wh = battery_wh(title, desc)
    return wh is not None and wh < SMALL_BATTERY_WH


# Zdjecia galerii siedza w atrybucie data-imgsrc. NIE wolno brac wszystkich
# obrazkow ze strony: sprawdzone 23.08.2026 — na stronie Scotta Ransome bylo
# 13 zdjec, z czego 3 jego, a 10 CUDZYCH ROWEROW z sekcji "moze cie
# zainteresuje" (Bulls, Canyon, Orbea...). Wyslanie ich jako zdjec tego
# ogloszenia oznaczaloby dojazd po rower, ktorego na fotce w ogole nie ma.
_ZDJ = r'https://img\.kleinanzeigen\.de/api/v1/prod-ads/images/[0-9a-f]{2}/[0-9a-f-]{36}'
GALERIA_WZ = re.compile(r'data-imgsrc="(' + _ZDJ + r')')
# Drugi układ strony, spotkany 23.08 na żywo: bez data-imgsrc, za to z dużym
# zdjęciem jako tłem. Oba wzorce biorą TYLKO zdjęcia z galerii — na dole strony
# siedzą jeszcze "podobne ogłoszenia" (imagebox srpimagebox) z cudzymi rowerami,
# a te mają zwykłe src= i nie mogą tu wpaść.
GALERIA_TLO = re.compile(r"galleryimage-large--cover[^>]*?background-image:\s*url\('(" + _ZDJ + r")")


# Trzeci układ (zmierzony 23.08 — tego dnia PRZEWAŻAŁ): zero data-imgsrc,
# zero galleryimage; zdjęcia siedzą wyłącznie w blokach JSON-LD "ImageObject".
# Bez tego czytnika album był pusty, a bot wysyłał samą miniaturę z listy.
GALERIA_JSON = re.compile(r'"contentUrl":\s*"(' + _ZDJ + r')')


def _zdjecia_z_json(html: str) -> list:
    """Zdjęcia z bloków JSON-LD. Bierzemy tylko te opisane jako należące do
    strony (`representativeOfPage`) — inne ImageObject na stronie opisują
    cudze ogłoszenia."""
    adresy = []
    for kawalek in html.split('"ImageObject"')[1:]:
        kawalek = kawalek[:4000]
        if "representativeOfPage" not in kawalek:
            continue
        m = GALERIA_JSON.search(kawalek)
        if m:
            adresy.append(m.group(1))
    return adresy


def galeria_ze_strony(html: str) -> list:
    """Adresy zdjęć NALEŻĄCYCH do tego ogłoszenia, w kolejności z galerii.

    Kleinanzeigen serwuje stronę ogłoszenia w kilku układach — zmierzone
    23.08, wariant zmienia się z sesji na sesję. Każdy trzyma zdjęcia gdzie
    indziej, więc czytamy po kolei, aż któryś odpowie."""
    html = html or ""
    # "Podobne ogłoszenia" na dole strony to CUDZE rowery (10 z 13 zdjęć na
    # stronie potrafi należeć do kogoś innego) — ucinamy stronę przed nimi.
    ciach = html.find("srpimagebox")
    gora = html[:ciach] if ciach > 0 else html
    adresy = (list(GALERIA_WZ.findall(gora))
              or list(GALERIA_TLO.findall(gora))
              or _zdjecia_z_json(gora))
    return [f"{u}?rule=$_59.AUTO" for u in dict.fromkeys(adresy)]


# Kleinanzeigen podaje stronę ogłoszenia w dwóch układach — stary ma
# id="viewad-price", nowy id="vip-ad-price". Zmierzone 23.08: na nowym
# układzie odczyt ceny ze strony milczał. To ten sam rodzaj cichej awarii,
# co brak przebiegu, więc oba układy muszą być obsłużone jawnie.
CENA_ZE_STRONY = re.compile(r'id="(?:viewad-price|vip-ad-price)"[^>]*>\s*([^<]+)')
STRONA_OGLOSZENIA = re.compile(r'id="(?:viewad-price|vip-ad-price)"|'
                               r'id="viewad-description-text"')


# Rezerwacja. Znaleziona 23.08 po uwadze użytkownika ("przecież pierwsze
# zdjęcie jest reserviert"): sprzedawca stempluje RESERVIERT na zdjęciu, czego
# bez AI nie odczytamy, ale Kleinanzeigen dokłada do tego własną plakietkę przy
# galerii. Pułapka: plakietka jest TYLKO w jednym z układów strony (tym z
# galerią w JSON-LD). W drugim nie ma o rezerwacji ani słowa — i wtedy uczciwa
# odpowiedź brzmi "nie wiem", a nie "wolne".
REZERWACJA_WZ = re.compile(r'badge-unavailable|icon-reserved-flag', re.I)
# Część sprzedawców pisze to wprost w tytule albo opisie — to darmowy dodatek
# do plakietki. "nicht reserviert" i "keine Reservierung" znaczą coś odwrotnego.
REZERWACJA_TEKST = re.compile(r'(?<!nicht )(?<!keine )reserviert', re.I)


def czy_zarezerwowane(html: str, title: str = "", desc: str = ""):
    """True / False / None. None = ten układ strony nic o tym nie mówi."""
    if REZERWACJA_TEKST.search(title or "") or REZERWACJA_TEKST.search(desc or ""):
        return True
    if REZERWACJA_WZ.search(html or ""):
        return True
    # Plakietka jedzie razem z galerią w JSON-LD; brak plakietki jest dowodem
    # na "wolne" tylko w tym układzie, bo tylko on ją w ogóle pokazuje.
    if '"representativeOfPage"' in (html or ""):
        return False
    return None


def fetch_listing_details(url: str, title: str = "", proba: int = 1) -> tuple:
    """Pobiera stronę ogłoszenia. Zwraca (przebieg, opis, cena|None, zdjęcia, stan, meta).

    `stan` to jedyna uczciwa odpowiedź na pytanie "czy my to w ogóle
    przeczytaliśmy":
        "ok"       — strona wczytana, opis mamy, brak przebiegu = fakt
        "usuniete" — ogłoszenie zdjęte (404/410), nie ma czego czytać
        "blad"     — NIE UDAŁO SIĘ pobrać; niczego o tym rowerze nie wiemy

    Rozróżnienie jest sednem. Sprawdzone 23.08: z 18 powiadomień 7 miało
    zapisane "brak danych", choć przebieg stoi w opisie (m.in. Cannondale
    Moterra z 10 328 km). Strona pobiera się dziś bez problemu — tamte odczyty
    padły na chwilowej awarii, a bot zapisał to jako fakt "sprzedawca nie
    podał" i puścił złom dalej, bo brak przebiegu przepuszcza filtr zużycia.
    Przy "blad" wolno tylko jedno: spróbować jeszcze raz (patrz do_odczytania)."""
    try:
        r = scraper.get(url, timeout=15)
        if r.status_code in (404, 410):
            return "brak danych", None, None, [], "usuniete", {}
        r.raise_for_status()
        r.encoding = "utf-8"
        html = r.text

        # Cena ze strony ogłoszenia (ratunek gdy lista jej nie miała)
        price_m = CENA_ZE_STRONY.search(html)
        detail_price = " ".join(price_m.group(1).split()) if price_m else None

        # Wyciągnij opis
        desc_match = re.search(
            r'id="viewad-description-text"[^>]*>(.*?)</p>',
            html, re.DOTALL | re.IGNORECASE
        )
        if not desc_match:
            desc_match = re.search(
                r'class="[^"]*ad-description[^"]*"[^>]*>(.*?)</(?:div|section)>',
                html, re.DOTALL | re.IGNORECASE
            )
        # Pusty opis wolno uznać za fakt tylko wtedy, gdy to NA PEWNO strona
        # ogłoszenia. Bez tego sprawdzenia zmiana układu HTML albo strona
        # przejściowa ("zbyt wiele żądań") wyglądałaby jak rower bez opisu
        # i cicho przepadła — a to dokładnie ten błąd, który naprawiamy.
        if not desc_match and not STRONA_OGLOSZENIA.search(html):
            # Zdjęte ogłoszenie NIE oddaje 404 — sprawdzone 23.08 na żywym
            # przykładzie: HTTP 200, ten sam adres, a w środku strona
            # kategorii z komunikatem "nicht mehr verfügbar". Bez tego
            # rozpoznania bot dobijałby się do nieistniejącego roweru osiem
            # razy i na koniec zawracał głowę alarmem o rowerze, którego nie ma.
            if re.search(r"nicht mehr verf(?:ü|ue)gbar|wurde gel(?:ö|oe)scht",
                         html, re.IGNORECASE):
                return "brak danych", None, None, [], "usuniete", {}
            raise ValueError("strona bez opisu i bez ceny — to nie ogłoszenie")

        desc_html = desc_match.group(1) if desc_match else ""
        desc_text = re.sub(r'<[^>]+>', ' ', desc_html)
        zdjecia = galeria_ze_strony(html)
        meta = {"zarezerwowane": czy_zarezerwowane(html, title, desc_text)}

        # 1. Claude Haiku (gdy klucz API ustawiony) — czyta opis jak człowiek
        llm = llm_extract_mileage(title, desc_text)
        if llm is not None:
            _, km = llm
            if km:
                return _format_km(km), desc_text, detail_price, zdjecia, "ok", meta
            # Haiku mówi "nie ma przebiegu" — to NIE JEST dowód, że go nie ma.
            # Cannondale Moterra (23.08) miał w opisie "10.328 km", model tego
            # nie zwrócił, a bot zapisał "brak danych" i puścił dalej rower po
            # 10 tys. km, bo is_too_worn(None) przepuszcza. Odpowiedź "nie wiem"
            # nie może wyłączać drugiego czytnika — regex dostaje swoją szansę.
            mileage = _extract_mileage(title, desc_text)
            if mileage != "brak danych":
                log.info(f"Przebieg pominięty przez model, znaleziony wzorcem: {mileage}")
            return mileage, desc_text, detail_price, zdjecia, "ok", meta

        # 2. Fallback: reguły regex
        mileage = _extract_mileage(title, desc_text)
        return mileage, desc_text, detail_price, zdjecia, "ok", meta

    except Exception as e:
        log.error(f"Listing fetch error ({proba}/{ODCZYT_PROBY}): {e}")
        if proba < ODCZYT_PROBY:           # ponowna próba, po dłuższym oddechu
            time.sleep(2 * proba)
            return fetch_listing_details(url, title, proba + 1)
    # None = fetch się nie udał (odróżnialne od pustego opisu)
    return "brak danych", None, None, [], "blad", {}


def _format_km(km: int) -> str:
    return f"{km:,} km".replace(",", ".")


def llm_extract_mileage(title: str, desc_text: str):
    """Czyta przebieg z tytułu+opisu przez Claude Haiku.
    Zwraca ("ok", km|None) przy powodzeniu, None przy błędzie (→ fallback regex)."""
    if not ANTHROPIC_API_KEY:
        return None
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5",
                "max_tokens": 100,
                "system": (
                    "Czytasz niemieckie ogłoszenia sprzedaży rowerów elektrycznych. "
                    "Wyciągnij CAŁKOWITY PRZEBIEG roweru w km (Laufleistung/Kilometerstand/gefahren). "
                    "NIE myl przebiegu z zasięgiem akumulatora (Reichweite) ani pojemnością (Wh). "
                    "Jeśli ogłoszenie dotyczy kilku rowerów, podaj przebieg najmniejszy. "
                    "Jeśli przebieg nie jest podany, zwróć null."
                ),
                "output_config": {
                    "format": {
                        "type": "json_schema",
                        "schema": {
                            "type": "object",
                            "properties": {
                                "mileage_km": {"type": ["integer", "null"]},
                            },
                            "required": ["mileage_km"],
                            "additionalProperties": False,
                        },
                    }
                },
                "messages": [
                    {"role": "user", "content": f"Tytuł: {title}\n\nOpis: {desc_text[:3000]}"}
                ],
            },
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        text = next(b["text"] for b in data["content"] if b["type"] == "text")
        km = json.loads(text).get("mileage_km")
        if km is None:
            return ("ok", None)
        if isinstance(km, int) and 0 < km <= 50000:
            return ("ok", km)
        return ("ok", None)
    except Exception as e:
        log.error(f"LLM mileage error: {e}")
    return None


def _extract_mileage(title: str, desc_text: str) -> str:
    # 1. Przebieg zadeklarowany w TYTULE — najbardziej wiarygodne źródło
    #    ("Nur 800km", "Erst 516 km", "2337km")
    t = re.search(
        r'(nur|erst)?\s*(\d[\d.,]*)\s*km\b',
        title, re.IGNORECASE
    )
    if t:
        before = title[max(0, t.start() - 25):t.start()].lower()
        # "nur/erst" przed liczbą = na pewno przebieg; bez tego prefiksu
        # odrzucamy gdy w pobliżu Reichweite/Akku (to zasięg, nie przebieg)
        explicit = bool(t.group(1))
        if explicit or not re.search(r'reichweite|bis\s*(?:zu)?$|akku', before):
            raw = t.group(2).replace(".", "").replace(",", "")
            if raw.isdigit() and 10 <= int(raw) <= 25000:
                return _format_km(int(raw))

    # 2. Atrybut/deklaracja przebiegu w OPISIE — słowo kluczowe musi być
    #    BLISKO liczby (max 40 znaków), żeby nie łączyć odległych fragmentów
    if desc_text:
        attr = re.search(
            r'(?:Kilometerstand|Laufleistung|km[\s-]?Stand|Tachostand|km)\s*[:=]\s*'
            r'(\d[\d.,]*)\s*km|'
            r'(?:Kilometerstand|Laufleistung|km[\s-]?Stand|Tachostand)[^\d]{0,40}(\d[\d.,]*)\s*km',
            desc_text, re.IGNORECASE
        )
        if attr:
            raw = (attr.group(1) or attr.group(2)).replace(".", "").replace(",", "")
            if raw.isdigit() and 10 <= int(raw) <= 25000:
                return _format_km(int(raw))

        # 3. System punktowy — TYLKO w tekście opisu, nigdy w pełnym HTML
        RANGE_CONTEXT = [
            "reichweite", "wh", "akku", "batterie", "kapazität",
            "ladung", "range", "motorleistung",
            # zaobserwowane 23.08 na LEMMO ONE: "kann eine Fahrt von 100 km
            # unterstützen" to zasięg opisany zdaniem, a nie słowem kluczowym
            "fahrt von", "unterstütz", "je ladung", "pro ladung",
            "auf einer", "bis zu", "schafft", "weit kommen",
            # "190km auf Eco und 73 auf Turbo" to zasieg w trybie wspomagania,
            # a nie przebieg — ten sam opis podawal prawdziwe "Km: 5250km"
            "auf eco", "auf turbo", "im eco", "im turbo", "modus", "tour-modus",
        ]

        # Zwroty, ktorymi Niemcy podaja przebieg BEZ slowa "gefahren".
        # Bez nich gubilismy prawdziwe odczyty: "hat 1100km", "mit 113 km",
        # "nur 188km auf dem Buckel" — wszystkie z zywych ogloszen 23.08.
        PRZED_LICZBA = r"(?:hat|mit|nur|erst|knapp|gerade)\s*$"
        PO_LICZBIE = r"^\s*(?:auf dem buckel|auf der uhr|drauf|runter|gelaufen|gefahren)"

        candidates = []
        for m in re.finditer(r'(\d[\d.,]*)\s*km\b', desc_text, re.IGNORECASE):
            raw = m.group(1).replace(".", "").replace(",", "")
            if not raw.isdigit():
                continue
            km = int(raw)
            if not (50 <= km <= 25000):
                continue

            # szerokie okno dla słów przebiegu, WĄSKIE dla kary zasięgu —
            # "Reichweite" stoi zawsze tuż przy liczbie, a "Akku" z listy
            # komponentów obok nie może kasować prawdziwego przebiegu
            ctx = desc_text[max(0, m.start() - 120):m.end() + 120].lower()
            ctx_near = desc_text[max(0, m.start() - 40):m.end() + 40].lower()

            score = 5

            mileage_ctx = bool(re.search(
                r'gefahren|gelaufen|laufleistung|kilometerstand|tachostand|tacho|km.?stand|insgesamt|bisher|gesamt',
                ctx
            ))
            # Zwrot tuż PRZED liczbą albo tuż PO niej — dowód na przebieg, ale
            # SŁABSZY niż słowo zasięgu obok. "Reichweite ca. 120 km" ma i jedno,
            # i drugie, więc pierwszeństwo musi mieć zasięg, inaczej wracamy do
            # mylenia zasięgu z przebiegiem.
            zasieg_obok = any(kw in ctx_near for kw in RANGE_CONTEXT)
            tuz_przed = desc_text[max(0, m.start() - 14):m.start()].lower()
            tuz_po = desc_text[m.end():m.end() + 22].lower()
            if not mileage_ctx and not zasieg_obok and (
                    re.search(PRZED_LICZBA, tuz_przed) or re.search(PO_LICZBIE, tuz_po)):
                mileage_ctx = True
            if mileage_ctx:
                score += 15
            else:
                for kw in RANGE_CONTEXT:
                    if kw in ctx_near:
                        score -= 20
                        break
                else:
                    # Liczba bez ŻADNEGO kontekstu przebiegu przechodziła
                    # domyślnie i tak "100 km" z opisu zasięgu robiło z roweru
                    # prawie nowy. Bez dowodu przyjmujemy ją tylko wtedy, gdy
                    # jest za duża na zasięg jednego ładowania (>1500 km).
                    if km < 1500:
                        score -= 20

            if km in (400, 500, 600, 625, 630, 700, 750, 800, 1000):
                score -= 10

            candidates.append((score, km))

        if candidates:
            best = max(candidates, key=lambda x: x[0])
            if best[0] > 0:
                return _format_km(best[1])

    # 4. Brak opisu / brak liczb → uczciwe "brak danych", NIE zgadujemy z HTML
    return "brak danych"


# --- Samonaprawiający parser: pule wzorców w kolejności od najlepszego. ---
# Gdy Kleinanzeigen zmieni layout i pierwszy wzorzec przestanie łapać,
# kolejny automatycznie przejmuje robotę (a monitor skuteczności alarmuje).
TITLE_PATTERNS = [
    r'href="(/s-anzeige/[^"]+)">([^<\n]+)</a>',
    r'href="(/s-anzeige/[^"]+)"[^>]*class="[^"]*ellipsis[^"]*"[^>]*>([^<]+)',
    r'href="(/s-anzeige/[^"]+)"[^>]*>\s*([^<\n]{5,})',
]
PRICE_PATTERNS = [
    r'"adlist--item--price">([^<]+)<',
    r'class="aditem-main--middle--price-shipping--price">\s*([^\n<]+)',
    r'>(\d[\d.]*\s*€(?:\s*VB)?)<',
]
PARSE_HEALTH_MIN_RATE = 0.5    # poniżej = prawdopodobna zmiana layoutu
PARSE_HEALTH_MIN_BLOCKS = 10   # nie alarmuj przy garstce ofert (naturalne wahania)
REPLAY_DIR = os.environ.get("DEALHAWK_REPLAY_DIR")  # tryb odtwarzania (czarna skrzynka)


def _match_pool(patterns, block):
    """Próbuje wzorce po kolei; zwraca (match, indeks) pierwszego trafienia."""
    for i, p in enumerate(patterns):
        m = re.search(p, block)
        if m:
            return m, i
    return None, None


def fetch_listings(search: dict):
    """Parsuje listę per-blok ogłoszenia. Zwraca (lista_ofert, statystyki_zdrowia)."""
    results = []
    seen_ids = set()
    stats = {"name": search["name"], "blocks": 0, "title_hits": 0, "price_hits": 0,
             "time_hits": 0, "html": None, "status": None}
    try:
        if REPLAY_DIR:  # odtwarzanie zapisanego HTML zamiast sieci
            fp = Path(REPLAY_DIR) / (re.sub(r'[^\w]+', "_", search["name"]) + ".html")
            html = fp.read_text(encoding="utf-8") if fp.exists() else ""
            stats["status"] = 200
        else:
            # CIASTKA CZYSZCZONE PRZED KAŻDYM ŻĄDANIEM. Zmierzone 22.08.2026:
            # pierwsze żądanie w sesji dostaje pełną stronę (27 kart z datami),
            # a każde następne — wariant BEZ bloku z datą wystawienia.
            # Bot robił 23 żądania w jednej sesji, więc 22 z nich czytały
            # okrojoną wersję strony. Z czyszczeniem: 5/5 pełnych odpowiedzi.
            scraper.cookies.clear()
            r = scraper.get(search["url"], timeout=15)
            stats["status"] = r.status_code   # do diagnozy: awaria serwisu vs zmiana HTML
            r.raise_for_status()
            r.encoding = "utf-8"  # bez tego wariant odpowiedzi bez charset psuje umlauty
            html = r.text

        blocks = re.split(r'(?=data-adid=")', html)
        for block in blocks:
            id_m = re.match(r'data-adid="(\d+)"', block)
            if not id_m:
                continue
            ad_id = id_m.group(1)
            if ad_id in seen_ids:
                continue
            seen_ids.add(ad_id)
            stats["blocks"] += 1

            tm, _ = _match_pool(TITLE_PATTERNS, block)
            if tm:
                stats["title_hits"] += 1
                href, title = tm.group(1), tm.group(2).strip()
            else:
                href, title = f"/s-anzeige/{ad_id}", "Brak tytułu"

            pm, _ = _match_pool(PRICE_PATTERNS, block)
            if pm:
                stats["price_hits"] += 1
                price_str = pm.group(1).strip()
            else:
                price_str = "brak ceny"

            # lokalizacja (PLZ + miasto) — do ekonomii transportu / geografii
            # Po kodzie pocztowym musi stać NAZWA (wielka litera), nie cyfry.
            # Bez tego wzorzec łapał współrzędne ze ścieżki SVG i do dziennika
            # trafiało "09163 10.1363 5.62761 12.0003" — 34% wpisów market.jsonl.
            lm = re.search(r'\b(\d{5})\s+([A-ZÄÖÜ][^<\n\d]{1,38})', block)
            loc = None
            if lm:
                loc = lm.group(1) + " " + re.sub(r'\s+', ' ', lm.group(2)).strip()

            # czas wystawienia — jest w karcie, tylko brakuje go płatnym
            # "Top-Anzeigen" na górze listy (stąd tolerancja na None)
            am = AD_TIME_PATTERN.search(block)
            posted = parse_ad_time(am.group(1)) if am else None
            if posted:
                stats["time_hits"] += 1

            # Miniatura roweru — siedzi w tej samej karcie. Wymuszamy wariant
            # 960x720 (~160 kB): Telegram pokazuje go DUŻO większy jako zdjęcie
            # niż jako podgląd linka, a to jest pierwsza rzecz, którą widzisz.
            im = re.search(r'https://img\.kleinanzeigen\.de/api/v1/prod-ads/images/[^"?\s\\]+',
                           block)
            foto = f"{im.group(0)}?rule=$_59.AUTO" if im else None

            results.append({
                "id": ad_id,
                "title": title,
                "price": price_str,
                "price_num": parse_price(price_str),
                "loc": loc,
                "foto": foto,
                "posted": posted,
                "age_min": ad_age_minutes(posted),
                "url": f"https://www.kleinanzeigen.de{href}",
            })

        # zachowaj HTML tylko gdy ten search wygląda na zepsuty (czarna skrzynka)
        if stats["blocks"] >= PARSE_HEALTH_MIN_BLOCKS:
            rate = min(stats["title_hits"], stats["price_hits"]) / stats["blocks"]
            if rate < PARSE_HEALTH_MIN_RATE:
                stats["html"] = html

    except Exception as e:
        log.error(f"Scrape error [{search['name']}]: {e}")
    return results, stats


# === KANAŁ KATEGORII =======================================================
# Zapytania kluczowe ("e-mtb fully") to loteria: Kleinanzeigen dopasowuje je
# rozmyto, także po opisie, i ustawia wyniki po TRAFNOŚCI. Ogłoszenie może
# wejść do takiego zbioru wiele godzin po wystawieniu — tak zginął Scott
# Ransome 22.08 (wystawiony 00:41, zauważony 17:31).
#
# Kanał kategorii jest inny: "Typ: Elektrofahrräder" to pole z formularza
# sprzedawcy, nie zgadywanka po tytule, a lista jest posortowana PO DACIE.
# Żadnych filtrów w URL-u — cena i reszta kryteriów sprawdzane w kodzie,
# tak samo jak w bocie samochodowym. Jedna strona ≈ 10 minut ogłoszeń.
#
# DWIE PÓŁKI, nie jedna. Rubrykę „Typ" wybiera sprzedawca i myli się regularnie:
# Specialized Levo FSR wystawiony 22.08 o 21:41 miał zaznaczone „Mountainbikes",
# więc na półce e-bike'ów go nie było i złapało go dopiero pytanie po nazwie —
# 34 minuty zamiast 2. Obie rubryki, w które trafiają e-MTB, są teraz pod
# obserwacją. Koszt: jedno żądanie na skan więcej.
FEED_BAZA = "https://www.kleinanzeigen.de/s-fahrraeder/{strona}c217+fahrraeder.type_s:{typ}"
KANALY = [
    {"typ": "ebike", "nazwa": "kanał e-bike"},
    {"typ": "mountainbike", "nazwa": "kanał MTB"},
]


def feed_url(typ: str, n: int = 1) -> str:
    return FEED_BAZA.format(strona="" if n == 1 else f"seite:{n}/", typ=typ)
FEED_MAX_STRON = 12        # ~2 h ogłoszeń — zapas na najdłuższą zaobserwowaną
                           # przerwę w harmonogramie GitHuba (55 min)
FEED_MARGINES_MIN = 3      # ile cofnąć się za znacznik, na styk zegarów
FEED_STATE_FILE = Path("feed_stan.json")


# Ponawianie uderza w ten sam adres, który właśnie jest dławiony — trzy próby
# potrafiły zaszkodzić bardziej niż pomóc. Jedna powtórka wystarcza na blip.
FEED_PROBY = 2

# --- OSZCZĘDZANIE ZAPYTAŃ --------------------------------------------------
# Bot wysyłał 24 zapytania co 5 minut, ponad 7 tys. dziennie z jednego adresu,
# i Kleinanzeigen zaczęło mu oddawać podstawioną stronę (32 kafelki, bez dat,
# w losowej kolejności) zamiast prawdziwej listy. Kanał zastępuje te zapytania
# JEDNYM, więc reszta chodzi teraz rotacyjnie: po kilka na skan, każde ląduje
# w kolejce mniej więcej raz na godzinę. Zapytania kluczowe i tak są tylko
# zapasem na rowery, których sprzedawca nie oznaczył jako e-bike.
# Zeszło z 2 na 1, żeby zapłacić za drugą półkę (MTB). Zmierzone 22.08:
# przy 5 żądaniach na skan kanał zaczął padać co drugi raz i mediana wykrycia
# poszła z 3 na 12 minut. Zapytania kluczowe są tylko zapasem — półki łapały
# 19 z 19 ogłoszeń — więc to one mają ustąpić miejsca, nie kanał.
KLUCZOWE_NA_SKAN = 1       # gdy kanał żyje — ruch trzymany przy ziemi
KLUCZOWE_AWARYJNE = 8      # gdy kanał leży mimo oszczędzania — trzeba nadrobić
KANAL_CIERPLIWOSC = 12     # tyle skanów (~1 h) dajemy hipotezie o dławieniu


def wybierz_kluczowe(ile, idx):
    """Kolejny kawałek listy zapytań, z zawijaniem. Zwraca (zapytania, nowy_idx)."""
    if ile <= 0 or not SEARCHES:
        return [], idx
    ile = min(ile, len(SEARCHES))
    start = idx % len(SEARCHES)
    wybrane = [SEARCHES[(start + i) % len(SEARCHES)] for i in range(ile)]
    return wybrane, (start + ile) % len(SEARCHES)


def pobierz_z_datami(search):
    """fetch_listings, ale nie odpuszcza stronie bez dat wystawienia.

    Kleinanzeigen bywa kapryśne: potrafi oddać wariant strony bez bloku
    z datą (a czasem zupełnie inny zestaw kafelków). Bez daty nie da się
    cofać po kanale, więc prosimy ponownie, ze świeżymi ciastkami.
    Zmierzone: z czyszczeniem ciastek 8/8 poprawnych.

    TYLKO DLA KANAŁU. Objęcie tym 23 zapytań kluczowych wydłużyło bieg
    z 0,5 do 5 minut i biegi zaczęły się kasować nawzajem — a tam data
    jest wyłącznie ozdobą, bo świeżość i tak pilnuje kanał."""
    listings, stats = fetch_listings(search)
    for proba in range(2, FEED_PROBY + 1):
        if REPLAY_DIR or stats["blocks"] < 5 or stats["time_hits"] > 0:
            break
        # Gdy poprzedni skan już oberwał podstawioną stroną, ponawianie jest
        # nie tylko bezcelowe — dokłada żądań DOKŁADNIE tam, gdzie serwis
        # właśnie przykręca kurek. Zmierzone 22.08: przy dwóch półkach zły
        # skan robił 4 żądania zamiast 2 i kanał zaczął padać co drugi raz.
        if _stan().get("kanal_zle", 0) > 0:
            log.warning(f"[{search['name']}] strona bez dat — nie ponawiam, "
                        f"kanał już dławiony")
            break
        log.warning(f"[{search['name']}] strona bez dat — próba {proba}/{FEED_PROBY}")
        time.sleep(2.0)
        scraper.cookies.clear()
        listings, stats = fetch_listings(search)
    return listings, stats


def strona_zepsuta(stats) -> bool:
    """Czy to podstawiona strona-śmieć zamiast prawdziwej listy?

    Kleinanzeigen pod obciążeniem oddaje HTTP 200, właściwy adres i właściwy
    tytuł, ale w środku losowy zestaw starych ogłoszeń i kafelki BEZ daty
    wystawienia (zmierzone 22.08.2026: 32 kafelki, 0 dat, najstarsze id
    sprzed lat). Taka odpowiedź wygląda dla parsera na zdrowy rynek, więc
    jedynym pewnym rozpoznaniem jest brak dat na całej stronie."""
    return stats["blocks"] >= 5 and stats["time_hits"] == 0


def cena_w_widelkach(price_num) -> bool:
    """Nieznana cena NIE jest odrzuceniem — ratuje ją strona ogłoszenia."""
    return price_num is None or MIN_PRICE <= price_num <= MAX_PRICE


def _czas_z_zapisu(s):
    """Czas wystawienia z seen.json z powrotem na datę. None gdy go nie było."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def zapisz_nieodczytane(seen, listing, prev, stan, today, nazwa_zrodla=None,
                       teraz=None):
    """Zapamiętuje NIEUDANY odczyt strony — bez oceny i bez powiadomienia.

    To jest sedno gwarancji: chwilowa awaria pobierania nie ma prawa zostać
    zapisana jako wiedza o rowerze. Wpis nie ma `score`, więc pętla potraktuje
    go w następnym skanie jak nowe ogłoszenie, a `url` pozwala wrócić po stronę
    bez pośrednictwa półki (znacznik czasu już go minął).

    Zwraca numer podejścia, albo None gdy ogłoszenie zostało zdjęte."""
    ad_id = listing["id"]
    if stan == "usuniete":
        # 404/410 — ogłoszenia nie ma. To jest fakt, a nie awaria: nie ma
        # czego czytać i nie ma czego kupować.
        seen[ad_id] = {"date": today}
        return None
    stare = prev if isinstance(prev, dict) else {}
    n = stare.get("nieodczytane", 0) + 1
    seen[ad_id] = {
        "date": today,
        "title": listing.get("title", ""),
        "price": listing.get("price"),
        "price_num": listing.get("price_num"),
        "foto": listing.get("foto"),
        "loc": listing.get("loc"),
        # czas wystawienia z karty — inaczej ponowione ogłoszenie skłamałoby
        # w wiadomości, że wieku "nie podano", i zgubiłoby własny alarm o
        # spóźnieniu; wiek liczymy na nowo przy każdym podejściu
        "posted": (listing["posted"].isoformat()
                   if listing.get("posted") else None),
        "url": listing.get("url"),
        "search": stare.get("search") or nazwa_zrodla,
        "nieodczytane": n,
        "od": stare.get("od") or (teraz or datetime.now(TZ_DE)).isoformat(),
    }
    return n


def do_odczytania(seen, teraz=None):
    """Zaległe ogłoszenia do ponownego przeczytania — [(id, wpis), ...].

    Najświeższe naprzód, bo starszy rower i tak jest już mniej wart uwagi.
    Lista jest krótka z rozmysłem: żądania do Kleinanzeigen to twarda waluta
    (patrz KLUCZOWE_NA_SKAN), więc zaległości nie mogą wypchnąć bieżącego
    skanu. Wypadają wpisy bez adresu, po ODCZYT_PODEJSC próbach i starsze niż
    ODCZYT_WAZNE_H."""
    teraz = teraz or datetime.now(TZ_DE)
    czeka = []
    for ad_id, w in seen.items():
        if not isinstance(w, dict) or not w.get("nieodczytane") or not w.get("url"):
            continue
        if w["nieodczytane"] >= ODCZYT_PODEJSC:
            continue
        try:
            od = datetime.fromisoformat(w["od"])
        except (KeyError, TypeError, ValueError):
            continue
        if (teraz - od).total_seconds() > ODCZYT_WAZNE_H * 3600:
            continue
        czeka.append((od, ad_id, w))
    czeka.sort(key=lambda x: x[0], reverse=True)
    return [(ad_id, w) for _, ad_id, w in czeka[:ODCZYT_NA_SKAN]]


def wpis_jako_ogloszenie(ad_id, w):
    """Zaległy wpis z seen.json z powrotem w kształt ogłoszenia z listy."""
    return {
        "id": ad_id,
        "title": w.get("title", ""),
        "price": w.get("price"),
        "price_num": w.get("price_num"),
        "loc": w.get("loc", ""),
        "foto": w.get("foto"),
        "posted": _czas_z_zapisu(w.get("posted")),
        "age_min": ad_age_minutes(_czas_z_zapisu(w.get("posted"))),
        "url": w["url"],
        "ponowienie": w.get("nieodczytane", 0),
    }


def wraca_po_przecenie(prev, price_num):
    """Czy to rower odrzucony kiedyś TYLKO przez cenę, który wszedł w widełki?

    Zwraca dawną cenę albo None. To jedyna droga powrotu dla takiego
    ogłoszenia — ścieżka 'obniżki' obsługuje wyłącznie rowery, które
    wcześniej przeszły filtry i mają zapisany score."""
    if not isinstance(prev, dict):
        return None
    stara = prev.get("cena_odrzut")
    if not stara or price_num is None:
        return None
    if not (MIN_PRICE <= price_num <= MAX_PRICE):
        return None
    return stara


def load_feed_znacznik(typ: str = "ebike"):
    """Czas najnowszego ogłoszenia z poprzedniego skanu TEJ półki.

    Każda półka ma własny znacznik — to osobne listy i cofanie się po jednej
    nic nie mówi o drugiej. Klucz `ostatnie` to format sprzed dołożenia drugiej
    półki; czytamy go jako znacznik e-bike'ów, żeby wdrożenie nie zaczynało
    od zera i nie zassało godzin historii."""
    try:
        stan = json.loads(FEED_STATE_FILE.read_text())
        raw = stan.get(typ) or (stan.get("ostatnie") if typ == "ebike" else None)
        if not raw:
            return None
        dt = datetime.fromisoformat(raw)
        return dt if dt.tzinfo else dt.replace(tzinfo=TZ_DE)
    except Exception:
        return None


def save_feed_znacznik(dt, typ: str = "ebike"):
    if not dt:
        return
    try:
        stan = {}
        if FEED_STATE_FILE.exists():
            try:
                stan = json.loads(FEED_STATE_FILE.read_text())
            except Exception:
                stan = {}
        stan[typ] = dt.isoformat()
        stan.pop("ostatnie", None)          # stary klucz już niepotrzebny
        FEED_STATE_FILE.write_text(json.dumps(stan))
    except Exception as e:
        log.error(f"zapis znacznika kanału: {e}")


def fetch_feed(od=None, typ="ebike", nazwa=None):
    """Czyta jedną półkę wstecz, aż dojdzie do ogłoszeń starszych niż `od`.

    Zwraca (ogłoszenia, statystyki, dosiegl). `dosiegl=False` znaczy, że
    limit stron skończył się, ZANIM domknęliśmy lukę — czyli część ogłoszeń
    przepadła i trzeba o tym krzyknąć, a nie milczeć."""
    nazwa = nazwa or f"kanał {typ}"
    wynik, widziane, stats_all = [], set(), []
    dosiegl = od is None          # pierwszy bieg: bierzemy tylko stronę 1
    zepsuty = False
    prog = (od - timedelta(minutes=FEED_MARGINES_MIN)) if od else None

    for n in range(1, FEED_MAX_STRON + 1):
        url = feed_url(typ, n)
        karty, stats = pobierz_z_datami({"name": f"{nazwa} s.{n}", "url": url})
        stats_all.append(stats)
        if strona_zepsuta(stats):
            # Nie wolno tego policzyć jako "obejrzany rynek": znacznik
            # zostałby przesunięty, a prawdziwe ogłoszenia z tego okna
            # przepadłyby na zawsze. Lepiej zgłosić awarię i spróbować za minutę.
            log.error(f"[{nazwa}] strona {n}: podstawiona lista bez dat — "
                      f"skan uznany za nieudany")
            zepsuty = True
            break
        if not karty:
            break
        nowe = [l for l in karty if l["id"] not in widziane]
        for l in nowe:
            widziane.add(l["id"])
        wynik.extend(nowe)
        if prog is None:
            break
        czasy = [l["posted"] for l in karty if l["posted"]]
        if czasy and min(czasy) < prog:
            dosiegl = True
            break
        if not czasy:
            # strona bez ani jednej daty — dalsze cofanie się jest ślepe
            log.warning(f"[{nazwa}] strona {n} bez dat — przerywam cofanie")
            break

    if zepsuty:
        wynik = []                # nic z tego biegu nie jest wiarygodne
        dosiegl = False
    czasy = [l["posted"] for l in wynik if l["posted"]]
    stats = {"name": nazwa, "typ": typ,
             "zepsuty": zepsuty,
             "blocks": sum(s["blocks"] for s in stats_all),
             "title_hits": sum(s["title_hits"] for s in stats_all),
             "price_hits": sum(s["price_hits"] for s in stats_all),
             "time_hits": sum(s["time_hits"] for s in stats_all),
             "html": next((s["html"] for s in stats_all if s["html"]), None),
             "status": stats_all[-1]["status"] if stats_all else None,
             "stron": len(stats_all),
             "najnowsze": max(czasy) if czasy else None}
    return wynik, stats, dosiegl


def stars(score: int) -> str:
    if score >= 80:
        return "🔥🔥🔥"
    if score >= 60:
        return "🔥🔥"
    if score >= 40:
        return "🔥"
    return ""


def persist_seen_git():
    """Commituje i pushuje seen.json NATYCHMIAST (przed wysyłką powiadomień).
    Dzięki temu przerwany run nigdy nie powoduje duplikatów — najwyżej
    brak powiadomienia. Działa tylko na GitHub Actions."""
    if not os.environ.get("GITHUB_ACTIONS"):
        return
    import subprocess
    def run(*args):
        return subprocess.run(args, capture_output=True, text=True).returncode == 0
    run("git", "config", "user.name", "DealHawk Bot")
    run("git", "config", "user.email", "bot@dealhawk")
    # każdy plik OSOBNO — brakująca ścieżka (np. blackbox) nie może przerwać
    # dodawania pozostałych (git add wielu ścieżek pęka gdy jedna nie istnieje)
    for path in ("seen.json", "history.jsonl", "market.jsonl", "parser_health.json",
                 "feed_stan.json", "blackbox"):
        run("git", "add", path)
    if subprocess.run(["git", "diff", "--staged", "--quiet"]).returncode == 0:
        return  # brak zmian
    run("git", "commit", "-m", "update seen.json")
    for _ in range(3):
        if run("git", "pull", "--rebase") and run("git", "push"):
            log.info("seen.json zapisany do repo przed wysyłką powiadomień")
            return
        time.sleep(5)
    log.error("Nie udało się wypchnąć seen.json przed wysyłką!")


PARSE_STATE_FILE = Path("parser_health.json")


def check_parser_health(all_stats):
    """Monitor #2: sumuje skuteczność ekstrakcji per pole w całym runie.
    Gdy spadnie poniżej progu → alert na Telegram + zapis HTML (czarna skrzynka)."""
    try:
        blocks = sum(s["blocks"] for s in all_stats)
        if blocks < PARSE_HEALTH_MIN_BLOCKS:
            return
        title_rate = sum(s["title_hits"] for s in all_stats) / blocks
        price_rate = sum(s["price_hits"] for s in all_stats) / blocks

        # stan poprzedni — alertujemy tylko przy PRZEJŚCIU zdrowe→chore (raz)
        prev = {}
        if PARSE_STATE_FILE.exists():
            try:
                prev = json.loads(PARSE_STATE_FILE.read_text())
            except Exception:
                prev = {}
        was_ok = prev.get("ok", True)
        now_ok = title_rate >= PARSE_HEALTH_MIN_RATE and price_rate >= PARSE_HEALTH_MIN_RATE
        prev.update({  # update, nie nadpisanie — plik trzyma też stan feed_ok
            "ok": now_ok, "title_rate": round(title_rate, 2),
            "price_rate": round(price_rate, 2), "checked": date.today().isoformat(),
        })
        PARSE_STATE_FILE.write_text(json.dumps(prev))

        if not now_ok and was_ok:
            # czarna skrzynka: zapisz HTML zepsutych wyszukiwań
            broken = [s for s in all_stats if s.get("html")]
            Path("blackbox").mkdir(exist_ok=True)
            for s in broken[:2]:
                safe = re.sub(r'[^\w]+', '_', s['name'])
                fn = f"blackbox/{safe}-{date.today().isoformat()}.html"
                try:
                    Path(fn).write_text(s["html"], encoding="utf-8")
                except Exception:
                    pass
            log.error(f"Parser drift: title={title_rate:.2f} price={price_rate:.2f} "
                      f"— HTML w blackbox/")
        if not now_ok:
            zglos_problem("slepy", f"parser: tytuł {int(title_rate*100)}%, "
                                   f"cena {int(price_rate*100)}%")
    except Exception as e:
        log.error(f"check_parser_health error: {e}")


def diagnose_empty_scan(all_stats) -> str:
    """Z kodów HTTP wnioskuje PRZYCZYNĘ pustego skanu. Zwraca gotową wiadomość.
    Lekcja z 2026-07-28: awaria Akamai (503 wszędzie) była alertowana jako
    'zmiana HTML' — zła diagnoza kosztuje debugowanie nie tego, co trzeba."""
    statuses = [s.get("status") for s in all_stats]
    n = len(statuses) or 1
    n_5xx = sum(1 for st in statuses if isinstance(st, int) and st >= 500)
    n_4xx = sum(1 for st in statuses if isinstance(st, int) and 400 <= st < 500)
    n_net = sum(1 for st in statuses if st is None)
    n_200 = sum(1 for st in statuses if st == 200)
    if n_5xx >= n * 0.5:
        return ("⏸ <b>DealHawk — Kleinanzeigen leży (5xx).</b>\n\n"
                f"{n_5xx}/{n} wyszukiwań dostało błąd serwera — to awaria "
                "po ICH stronie, nie parsera. Nic nie rób, bot sam wznowi "
                "skan i da znać, gdy serwis wstanie.")
    if n_4xx >= n * 0.5:
        return ("🚫 <b>DealHawk — Kleinanzeigen blokuje scraper (4xx).</b>\n\n"
                f"{n_4xx}/{n} wyszukiwań odrzuconych. Prawdopodobnie antybot "
                "(IP runnera / fingerprint). Jeśli potrwa — trzeba zmienić "
                "sposób pobierania.")
    if n_200 >= n * 0.5:
        return ("🚨 <b>DealHawk — zmiana HTML!</b>\n\n"
                f"{n_200}/{n} wyszukiwań zwróciło stronę (200), ale zero "
                "ogłoszeń do sparsowania. Kleinanzeigen zmieniło strukturę — "
                "wzorce parsera wymagają naprawy.")
    return ("🌐 <b>DealHawk — problemy sieciowe.</b>\n\n"
            f"{n_net}/{n} wyszukiwań bez odpowiedzi (timeout/DNS). "
            "Możliwa awaria po drodze — obserwuję.")


# Warianty wejścia do OLX — sprawdzamy, czy blokada jest na całą domenę,
# czy tylko na główny serwis. Zapytania idą raz dziennie, po jednym na wariant.
KANAREK_WPADKI_DO_ALARMU = 2   # jedna nieudana proba = zwykle timeout, nie awaria

SONDA_WEJSCIA = [
    ("www_api", "https://www.olx.pl/api/v1/offers/?offset=0&limit=5&query=cube"),
    ("www_api", "https://www.olx.pl/api/v1/offers/?offset=0&limit=5&query=cube"),
    ("m_html", "https://m.olx.pl/sport-hobby/rowery/q-cube-stereo-hybrid/"),
    ("apex", "https://olx.pl/sport-hobby/rowery/q-cube-stereo-hybrid/"),
    ("oferta", "https://www.olx.pl/d/oferta/rower-CID767-ID1abc.html"),
    ("sitemap", "https://www.olx.pl/sitemap.xml"),
]


def diagnoza_dostepu_olx(status, kart, dlugosc) -> str:
    """Rozróżnia TRZY różne awarie, bo każda wymaga czego innego:
    padł przekaźnik / OLX blokuje mimo przekaźnika / zmienił się layout."""
    kb = dlugosc // 1024
    if OLX_RELAY_URL:
        if przekaznik_zyje() is False:
            return ("🔌 <b>DealHawk — padł przekaźnik Cloudflare!</b>\n\n"
                    "Sam Worker nie odpowiada. Możliwe przyczyny: skasowany,\n"
                    "przekroczony darmowy limit (100 tys./dobę) albo awaria Cloudflare.\n\n"
                    "<b>Co zrobić:</b> zajrzyj na dash.cloudflare.com → Workers.\n"
                    "<i>Monitorowanie rynku PL stoi.</i>")
        # status=None/0 znaczy, że odpowiedź nie doszła do OLX-a wcale —
        # czyli odrzucił nas sam Worker. Kod odmowy mówi, dlaczego.
        if status in (None, 0):
            d = olx_diag()
            kod = d.get("przekaznik_status")
            if kod is None:
                # Nie było ODMOWY — połączenie w ogóle nie doszło do skutku.
                # Realny przypadek z 21.08: timeout przy przesyłaniu strony
                # ważącej 3,5 MB wyglądał jak "przekaźnik odmawia, kod None".
                return ("⏱ <b>DealHawk — przekaźnik nie odpowiedział w czasie.</b>\n\n"
                        f"Rodzaj awarii: <code>{d.get('ostatni_wyjatek', 'nieznany')}</code>\n"
                        "Zwykle chwilowe — wolne łącze albo OLX mieli stronę.\n"
                        "<i>Bot spróbuje ponownie za 5 minut.</i>")
            powod = {
                401: "ZŁY KLUCZ — sekret <code>OLX_RELAY_KEY</code> w GitHubie\n"
                     "musi być IDENTYCZNY jak zmienna <code>KLUCZ</code> w Workerze.",
                429: "PRZEKROCZONY LIMIT zapytań na minutę. Albo boty oszalały,\n"
                     "albo ktoś zna Twój klucz i z niego korzysta — wymień go.",
                503: "BRAK KLUCZA w konfiguracji Workera (zmienna <code>KLUCZ</code>).",
                403: "Worker odrzucił adres — poza dozwoloną listą ścieżek OLX.",
            }.get(kod, f"kod odmowy: {kod}")
            return ("🔑 <b>DealHawk — przekaźnik odmawia.</b>\n\n"
                    f"{powod}\n<i>Monitorowanie rynku PL stoi.</i>")
        return (f"🚫 <b>DealHawk — OLX blokuje MIMO przekaźnika.</b>\n\n"
                f"HTTP {status}, kafelków {kart}, {kb} kB.\n"
                "Cloudflare też trafił na czarną listę — trzeba zmienić drogę.\n"
                "<i>Monitorowanie rynku PL stoi.</i>")
    return ("🚫 <b>DealHawk — OLX nas nie wpuszcza!</b>\n\n"
            f"Zapytanie kontrolne: HTTP {status}, kafelków {kart}, {kb} kB.\n"
            "Nie ustawiono przekaźnika — bot pyta OLX wprost, a serwerownia\n"
            "GitHuba jest zablokowana.\n"
            "<i>(dokładnie to działo się po cichu od 10 sierpnia)</i>")


def olx_kanarek():
    """Jedno zapytanie kontrolne do OLX przy KAŻDYM przebiegu trackera (co 5 min).
    Dzięki temu blokada wychodzi na jaw w 5 minut, a nie po 11 dniach ciszy jak
    w sierpniu 2026. Wynik ląduje w parser_health.json — pliku i tak commitowanym
    — więc widać go z zewnątrz, bez dostępu do logów runnera."""
    try:
        # LEKKIE zapytanie kontrolne: API (~50 kB) zamiast strony (3,5 MB).
        # Poprzednio kanarek ciągnął przez przekaźnik 3,5 MB CO 5 MINUT — czyli
        # ~1 GB dziennie — i regularnie łapał timeout, wywołując fałszywe alarmy.
        r = olx_get("https://www.olx.pl/api/v1/offers/?offset=0&limit=5"
                    "&query=rower+elektryczny", timeout=25)
        status = r.status_code if r is not None else None
        kart, dlugosc = 0, (len(r.text) if r is not None else 0)
        if r is not None and r.status_code == 200:
            try:
                kart = len(((r.json() or {}).get("data")) or [])
            except Exception:
                kart = 0
        zdrowy = kart > 0

        stan = {}
        if PARSE_STATE_FILE.exists():
            try:
                stan = json.loads(PARSE_STATE_FILE.read_text())
            except Exception:
                stan = {}
        poprzedni = stan.get("olx") or {}
        bylo_ok = poprzedni.get("ok", True)
        # Licznik wpadek z rzędu. Jedna nieudana próba to najczęściej chwilowy
        # timeout, nie awaria — alarmowanie po niej to wilk, który nie przyszedł.
        wpadki = 0 if zdrowy else poprzedni.get("wpadki", 0) + 1
        stan["olx"] = {"ok": zdrowy, "status": status, "kart": kart,
                       "kb": dlugosc // 1024, "wpadki": wpadki,
                       "kiedy": date.today().isoformat()}

        # SONDA WEJŚĆ (raz dziennie): czy blokada obejmuje KAŻDĄ drogę do OLX,
        # czy tylko www? Jeśli którekolwiek wejście przejdzie z serwerowni,
        # mamy darmowe rozwiązanie — bez proxy i bez trzymania laptopa włączonego.
        if (stan.get("sonda") or {}).get("kiedy") != date.today().isoformat():
            wyniki = {}
            for nazwa, u in SONDA_WEJSCIA:
                try:
                    rr = olx_get(u, timeout=15)
                    wyniki[nazwa] = rr.status_code if rr is not None else "brak"
                except Exception as e:
                    wyniki[nazwa] = f"exc:{str(e)[:25]}"
                time.sleep(0.4)
            stan["sonda"] = {"kiedy": date.today().isoformat(), "wyniki": wyniki}
        PARSE_STATE_FILE.write_text(json.dumps(stan))

        # Stanem jest LICZNIK wpadek, a nie flaga ok/nie-ok. Przy fladze druga
        # nieudana próba miała już bylo_ok=False i alarm nigdy by nie poleciał.
        if not zdrowy and wpadki >= KANAREK_WPADKI_DO_ALARMU:
            # diagnoza zostaje w logu — na Telegram idzie wspólna, prosta
            # wiadomość, i dopiero gdy awaria utrzyma się przez godzinę
            zglos_problem("olx", re.sub("<[^>]+>", "",
                                        diagnoza_dostepu_olx(status, kart, dlugosc)
                                        .splitlines()[0]))
        elif not zdrowy:
            log.warning(f"OLX kanarek: wpadka {wpadki} — jeszcze bez alarmu")
    except Exception as e:
        log.error(f"olx_kanarek error: {e}")


def check_feed_health(all_stats, total_found):
    """Alert gdy CAŁY skan pusty — z trafną diagnozą przyczyny i bez spamu:
    wiadomość tylko przy przejściu działa→nie działa (raz, nie co 5 minut)
    oraz jedna, gdy skan wróci. Stan w parser_health.json (klucz feed_ok)."""
    try:
        state = {}
        if PARSE_STATE_FILE.exists():
            try:
                state = json.loads(PARSE_STATE_FILE.read_text())
            except Exception:
                state = {}
        now_ok = total_found > 0
        state["feed_ok"] = now_ok
        PARSE_STATE_FILE.write_text(json.dumps(state))
        if now_ok:
            return
        # diagnoza (serwis leży / blokada / zmiana HTML) trafia do logu;
        # do użytkownika idzie jedno proste zdanie z bramki, po godzinie
        msg = re.sub("<[^>]+>", "", diagnose_empty_scan(all_stats).splitlines()[0])
        zglos_problem("slepy", f"pusty skan: {msg}")
    except Exception as e:
        log.error(f"check_feed_health error: {e}")


def main(tylko_feed=False):
    process_telegram_commands()   # najpierw odpowiedz na /wycen z telefonu
    _problemy.clear()             # awarie zbierane w trakcie tego skanu
    seen = prune_seen(load_seen())
    new_count = 0
    total_found = 0
    today = date.today().isoformat()
    olx_cache = {}
    price_hist = build_price_history(seen)
    recent_index = build_recent_index(seen)
    pending_msgs = []
    all_stats = []
    zrodla = []

    # 1. PÓŁKI KATEGORII — źródło odpowiedzialne za czas reakcji. Każda czytana
    #    wstecz aż do własnego znacznika, więc przerwa w harmonogramie opóźnia
    #    powiadomienie, ale niczego nie gubi.
    kanal_zle = _stan().get("kanal_zle", 0)
    feed_ids, opisy_kanalow, padly = set(), [], 0
    for kan in KANALY:
        znacznik = load_feed_znacznik(kan["typ"])
        listings, stats, dosiegl = fetch_feed(znacznik, kan["typ"], kan["nazwa"])
        all_stats.append(stats)
        # Ten sam rower może siedzieć tylko w jednej rubryce, ale gdyby
        # Kleinanzeigen kiedyś pokazało go w obu, nie chcemy dwóch wiadomości.
        listings = [l for l in listings if l["id"] not in feed_ids]
        feed_ids.update(l["id"] for l in listings)
        total_found += len(listings)
        log.info(f"[{kan['nazwa']}] {len(listings)} ogłoszeń z {stats['stron']} stron"
                 f"{'' if dosiegl else ' — LUKA, limit stron'}")

        if stats.get("zepsuty"):
            padly += 1
            opisy_kanalow.append(f"{kan['nazwa']}: podstawiona strona bez dat")
            log.error(f"[{kan['nazwa']}] podstawiona lista bez dat — nieczynny")
        elif not dosiegl and znacznik:
            opisy_kanalow.append(f"{kan['nazwa']}: luka poza limitem stron")
            log.error(f"[{kan['nazwa']}] nie domknięto luki od "
                      f"{format_age(ad_age_minutes(znacznik))}")
        else:
            opisy_kanalow.append(f"{kan['nazwa']}: ok")
            if stats["najnowsze"]:
                # Znacznik przesuwamy od razu po udanym odczycie TEJ półki —
                # awaria drugiej nie może cofnąć postępu pierwszej.
                save_feed_znacznik(stats["najnowsze"], kan["typ"])

        # Mediana liczona osobno dla półki i tylko z rowerów porównywalnych
        # (fully + elektryk) — siedzą tam też miejskie i dziecięce, a one
        # zaniżyłyby próg "okazji" dla marek niszowych.
        ceny = [l["price_num"] for l in listings
                if l["price_num"] and is_fully(l["title"]) and is_electric(l["title"])]
        zrodla.append(({"name": kan["nazwa"]}, listings,
                       statistics.median(ceny) if ceny else None))

    # Padnięcie półek NIE jest ślepotą, dopóki zapytania kluczowe oddają
    # ogłoszenia — bot działa wtedy gorzej, nie wcale, a wiadomość "nie przyjdą
    # nowe rowery" byłaby po prostu nieprawdziwa. O ślepocie decyduje na końcu
    # check_feed_health, po policzeniu WSZYSTKICH źródeł. Licznik rośnie tylko
    # gdy padły OBIE półki — jedna czynna wystarcza, żeby rowery płynęły.
    kanal_zle = kanal_zle + 1 if padly == len(KANALY) else 0
    _stan({"kanal": " · ".join(opisy_kanalow), "kanal_zle": kanal_zle})

    # 2. ZAPYTANIA KLUCZOWE — zapas. Łapią to, czego sprzedawca nie oznaczył
    #    jako e-bike, oraz rowery, które weszły w widełki po edycji ogłoszenia.
    if not tylko_feed:
        # Ile zapytań w tym skanie: mało, dopóki wierzymy, że oszczędzanie
        # ruchu odblokuje kanał. Gdy kanał leży mimo tego dłużej niż
        # KANAL_CIERPLIWOSC skanów, hipoteza była zła — wracamy do większej
        # liczby zapytań, żeby rowery nie przestały płynąć przez moją teorię.
        ile = KLUCZOWE_NA_SKAN if kanal_zle < KANAL_CIERPLIWOSC else KLUCZOWE_AWARYJNE
        wybrane, nowy_idx = wybierz_kluczowe(ile, _stan().get("kluczowe_idx", 0))
        _stan({"kluczowe_idx": nowy_idx})
        log.info(f"zapytania kluczowe: {len(wybrane)} z {len(SEARCHES)} "
                 f"(kanał zły od {kanal_zle} skanów)")
        for search in wybrane:
            # bez ponawiania — patrz pobierz_z_datami: tu data jest ozdobą,
            # a 23 zapytania z odczekiwaniem rozdymały bieg do 5 minut
            listings, stats = fetch_listings(search)
            all_stats.append(stats)
            total_found += len(listings)
            log.info(f"[{search['name']}] znaleziono {len(listings)} ogłoszeń")
            # Data w karcie jest podstawą oceny świeżości — gdy Kleinanzeigen zmieni
            # układ HTML, ma to wyjść w logu, a nie zniknąć po cichu jak wycena 10.08
            if stats["blocks"] >= 5 and stats["time_hits"] == 0:
                log.warning(f"[{search['name']}] żadna karta nie ma daty — "
                            f"zmiana HTML? wiek ogłoszeń przestał działać")
            prices_in_search = [l["price_num"] for l in listings if l["price_num"]]
            zrodla.append((search, listings,
                           statistics.median(prices_in_search) if prices_in_search else None))

    # 3. ZALEGŁE — ogłoszenia, których strony nie udało się przeczytać.
    #    Idą po własny adres, bo półka ich już nie odda: jej znacznik czasu
    #    przesunął się dalej. Dokładamy je do źródła, z którego przyszły, żeby
    #    odziedziczyły tę samą medianę cen — inaczej marka niszowa wypadłaby
    #    tylko dlatego, że nie ma z czym porównać ceny.
    zalegle = do_odczytania(seen)
    if zalegle:
        wg_nazwy = {}
        for ad_id, w in zalegle:
            wg_nazwy.setdefault(w.get("search") or "zaległe odczyty", []).append(
                wpis_jako_ogloszenie(ad_id, w))
        indeks = {s["name"]: i for i, (s, _, _) in enumerate(zrodla)}
        for nazwa, lst in wg_nazwy.items():
            if nazwa in indeks:
                s, l, m = zrodla[indeks[nazwa]]
                juz = {x["id"] for x in l}
                zrodla[indeks[nazwa]] = (s, l + [x for x in lst if x["id"] not in juz], m)
            else:
                zrodla.append(({"name": nazwa}, lst, None))
        log.info("zaległe odczyty: " + ", ".join(
            f"{w.get('title','?')[:30]} (podejście {w['nieodczytane'] + 1})"
            for _, w in zalegle))

    for search, listings, median_price in zrodla:
        for listing in listings:
            prev = seen.get(listing["id"])
            # Rower widziany wcześniej, ale odrzucony WYŁĄCZNIE przez cenę,
            # który właśnie wszedł w widełki. Dla nas to pierwszy moment,
            # w którym jest ofertą — więc idzie pełną ścieżką nowego ogłoszenia.
            przecena_z = wraca_po_przecenie(prev, listing["price_num"])
            if przecena_z:
                prev = None
            # Ogłoszenie, którego strony wcześniej NIE UDAŁO SIĘ przeczytać,
            # wraca jak nowe — bo o tym rowerze nadal nic nie wiemy. Wpis bez
            # `score` to zapis nieudanej próby, a nie ocena roweru.
            if (prev is not None and isinstance(prev, dict)
                    and prev.get("nieodczytane") and prev.get("score") is None):
                prev = None
            if prev is not None:
                # KAŻDA zmiana ceny do dziennika, także drobna. Powiadomienie
                # leci dopiero od 5%, ale próg powiadomienia nie może decydować
                # o tym, co WIEMY: bez tego zapisu nie da się policzyć, ile
                # sprzedawcy realnie opuszczają przed sprzedażą, a to jedyna
                # publicznie dostępna droga do ceny domykającej.
                if (isinstance(prev, dict) and listing["price_num"]
                        and prev.get("price_num")
                        and listing["price_num"] != prev["price_num"]):
                    append_history(olx_query_for(listing["title"], None),
                                   listing["price_num"], ad_id=listing["id"],
                                   year=prev.get("year"), ev="cena")
                # Obniżka ceny na ogłoszeniu, które wcześniej przeszło filtry
                if (isinstance(prev, dict) and prev.get("score") is not None
                        and listing["price_num"] and prev.get("price_num")
                        and listing["price_num"] < prev["price_num"] * 0.95):
                    # świeża weryfikacja przebiegu — dane w bazie mogą być stare/błędne
                    fresh_mileage, _, _, _, stan_sw, _meta_sw = fetch_listing_details(
                        listing["url"], listing["title"])
                    old_price = prev["price_num"]
                    if stan_sw == "ok":
                        fresh_num = parse_mileage(fresh_mileage)
                        prev["mileage"] = fresh_mileage
                        prev["mileage_num"] = fresh_num
                    else:
                        # Nieudany odczyt nie może skasować tego, co już wiemy —
                        # zostaje przebieg z pierwszego, udanego czytania.
                        fresh_mileage = prev.get("mileage", "brak danych")
                        fresh_num = prev.get("mileage_num")
                        log.warning(f"Obniżka: nie odczytano strony, "
                                    f"zostaje znany przebieg {fresh_mileage}")
                    prev["price"] = listing["price"]
                    prev["price_num"] = listing["price_num"]
                    if is_too_worn(fresh_num):
                        log.info(f"Obniżka pominięta (przebieg {fresh_mileage}): {listing['title'][:50]}")
                        continue
                    pending_msgs.append((
                        -1,   # obniżki idą przodem — okazja jest świeża, nie rower
                        f"📉 <b>DealHawk — obniżka ceny!</b>\n\n"
                        f"📌 <b>{html_mod.escape(listing['title'])}</b>\n"
                        f"💰 {old_price} € → <b>{listing['price']}</b>\n"
                        f"🚵 {fresh_mileage}\n"
                        f"🔗 {listing['url']}",
                        listing.get("foto"), None, []))
                    # trajektoria obniżki do dziennika finalistów
                    append_history(olx_query_for(listing["title"], None), listing["price_num"],
                                   ad_id=listing["id"], mileage_num=fresh_num, ev="drop")
                    log.info(f"Obniżka {old_price} -> {listing['price_num']}: {listing['title'][:50]}")
                continue

            # LOG RYNKU — każde nowe ogłoszenie, PRZED filtrami cenowymi
            # i jakościowymi (przecenione już tam jest z pierwszego spotkania).
            #
            # WYJĄTEK: półka "Mountainbikes" to w 96% zwykłe rowery bez silnika.
            # Trafiła tu tylko po to, żeby wyłapać e-MTB, które sprzedawca
            # źle otagował. Logowanie jej w całości zalało dziennik: 8 826
            # wierszy jednego dnia, czyli 47% całego pliku od czerwca. To nie
            # jest "nasz rynek", tylko szum — więc z tej półki zapisujemy
            # wyłącznie to, co wygląda na elektryk.
            if not przecena_z and (search["name"] != "kanał MTB"
                                   or is_electric(listing["title"])):
                log_market(listing, search["name"])

            # WIDEŁKI CENOWE W KODZIE. Kanał kategorii nie ma filtra ceny
            # w URL-u i to jest celowe: rower za 3000 € ma być ZOBACZONY
            # i zapamiętany, żeby po przecenie do 2300 € dało się go rozpoznać.
            # Brak ceny na liście przepuszczamy — ratuje ją strona ogłoszenia.
            if not cena_w_widelkach(listing["price_num"]):
                log.info(f"Pominięto (cena {listing['price_num']} € poza widełkami): "
                         f"{listing['title'][:50]}")
                seen[listing["id"]] = {"date": today, "cena_odrzut": listing["price_num"]}
                continue

            if is_junk(listing["title"]):
                log.info(f"Pominięto (śmieć): {listing['title'][:50]}")
                seen[listing["id"]] = {"date": today}
                continue

            if not is_fully(listing["title"]):
                log.info(f"Pominięto (nie fully): {listing['title'][:50]}")
                seen[listing["id"]] = {"date": today}
                continue

            if not is_electric(listing["title"]):
                log.info(f"Pominięto (analogowy): {listing['title'][:50]}")
                seen[listing["id"]] = {"date": today}
                continue

            # Marka spoza whitelisty PL → tylko przy wyjątkowej okazji cenowej
            if not is_premium_brand(listing["title"]):
                discount_ok = (
                    listing["price_num"] and median_price
                    and (median_price - listing["price_num"]) / median_price * 100 >= NICHE_MIN_DISCOUNT_PCT
                )
                if not discount_ok:
                    log.info(f"Pominięto (niszowa marka bez okazji): {listing['title'][:50]}")
                    seen[listing["id"]] = {"date": today}
                    continue

            mileage, desc_text, detail_price, zdjecia, stan_odczytu, meta = \
                fetch_listing_details(listing["url"], listing["title"])

            # NIE PRZECZYTALIŚMY strony — więc nic o tym rowerze nie orzekamy.
            # Ani "brak przebiegu", ani oceny, ani powiadomienia. Wpis wraca do
            # kolejki i przyjdzie po swój adres w kolejnym skanie.
            if stan_odczytu != "ok":
                n = zapisz_nieodczytane(seen, listing, prev, stan_odczytu,
                                        today, search["name"])
                if n is None:
                    log.info(f"Ogłoszenie zdjęte: {listing['title'][:50]}")
                elif n >= ODCZYT_PODEJSC:
                    # Ostatnie podejście. Strona nie wstaje, a rower jest
                    # w widełkach — więc zamiast po cichu go zgubić, mówimy
                    # wprost, czego nie wiemy, i oddajemy link do ręki.
                    log.error(f"Nie odczytano po {n} podejściach: {listing['url']}")
                    pending_msgs.append((
                        -1,
                        f"⚠️ <b>DealHawk — nie mogę odczytać ogłoszenia</b>\n\n"
                        f"📌 <b>{html_mod.escape(listing['title'])}</b>\n"
                        f"💰 {listing['price']}\n\n"
                        f"Strona nie wstaje od {n} prób — nie znam ani przebiegu, "
                        f"ani stanu. <b>Nic o tym rowerze nie liczę.</b>\n"
                        f"Zerknij sam, jest w widełkach:\n{listing['url']}",
                        listing.get("foto"), None, []))
                else:
                    log.warning(f"Odczyt nieudany ({n}/{ODCZYT_PODEJSC}), "
                                f"wróci w kolejnym skanie: {listing['title'][:50]}")
                continue

            zlicz_odczyt(zdjecia, detail_price)
            zarezerwowany = meta.get("zarezerwowane")
            mileage_num = parse_mileage(mileage)

            # Ratunek ceny ze strony ogłoszenia gdy lista jej nie dała
            if not listing["price_num"] and detail_price:
                listing["price"] = detail_price
                listing["price_num"] = parse_price(detail_price)
                # cena znana dopiero teraz — widełki trzeba sprawdzić ponownie
                if not cena_w_widelkach(listing["price_num"]):
                    log.info(f"Pominięto (cena ze strony {listing['price_num']} € "
                             f"poza widełkami): {listing['title'][:50]}")
                    seen[listing["id"]] = {"date": today, "cena_odrzut": listing["price_num"]}
                    continue

            if not has_known_motor(listing["title"], desc_text):
                log.info(f"Pominięto (brak marki silnika): {listing['title'][:50]}")
                seen[listing["id"]] = {"date": today}
                continue

            if is_too_worn(mileage_num):
                log.info(f"Pominięto (za duży przebieg {mileage}): {listing['title'][:50]}")
                seen[listing["id"]] = {"date": today}
                continue

            # Mała bateria / model SL → jak nisza: tylko przy wyjątkowej okazji
            small_battery = is_small_battery(listing["title"], desc_text)
            if small_battery:
                discount_ok = (
                    listing["price_num"] and median_price
                    and (median_price - listing["price_num"]) / median_price * 100 >= NICHE_MIN_DISCOUNT_PCT
                )
                if not discount_ok:
                    log.info(f"Pominięto (mała bateria/SL bez okazji): {listing['title'][:50]}")
                    seen[listing["id"]] = {"date": today}
                    continue

            # Re-listing? Ten sam rower pod nowym ID w ostatnich 14 dni → pomiń
            relisted_from = find_relisting(recent_index, listing["title"], listing["price_num"], mileage_num)
            if relisted_from:
                log.info(f"Pominięto (re-listing z {relisted_from}): {listing['title'][:50]}")
                seen[listing["id"]] = {"date": today}
                continue

            model_year = extract_year(listing["title"]) or extract_year(desc_text)

            listing["mileage"] = mileage
            listing["mileage_num"] = mileage_num
            sc = score_listing(listing, median_price)

            # Sygnał z własnego cennika historycznego modelu (per rocznik)
            hist_line, hist_bonus = price_history_signal(
                listing["title"], listing["price_num"], model_year, price_hist)
            sc = min(100, sc + hist_bonus)

            # Szacowany zysk z odsprzedazy w Polsce — zapytanie per model
            olx_query = olx_query_for(listing["title"], search["name"])
            if olx_query not in olx_cache:
                try:
                    olx_cache[olx_query] = olx_relevant_offers(olx_query, fetch_olx_offers(olx_query))
                except Exception as e:
                    log.error(f"OLX fetch error [{olx_query}]: {e}")
                    olx_cache[olx_query] = {}
            olx_offers = olx_cache[olx_query]

            # === ILE TEN ROWER JEST WART W POLSCE ===============================
            # Cennik cech przelicza KAŻDĄ polską ofertę na specyfikację tego
            # konkretnego roweru z Niemiec — zamiast szukać bliźniaka i udawać,
            # że nieznany atrybut pasuje (to kosztowało zakup Cube'a 2018).
            de_wh = battery_wh(listing["title"], desc_text)   # bateria niemieckiego roweru
            de_spec = parse_spec_fields(desc_text)            # osprzęt z niemieckiego opisu
            # Rozmiar ramy liczony RAZ: trafia i do wiadomości, i do listy
            # braków, o które pytamy sprzedawcę — muszą się zgadzać co do joty.
            rama_txt = de_spec.get("rozmiar") or rozmiar_ramy(listing["title"], desc_text)
            olx_price, olx_price_label, comparable = None, "OLX", None
            pewnosc_wyceny = None   # zmierzone: "niska" myli się 2x w 14% wycen
            skorygowana = False
            # Oferty do porównania: najpierw na żywo, a gdy OLX blokuje runnera
            # (HTTP 403) — z rynku zapisanego w repo. Bez tego wycena po prostu
            # znika, tak jak zniknęła po cichu 10.08.
            porownawcze = oferty_z_cechami(olx_offers, load_olx_details())
            zrodlo = "na żywo"
            if len(porownawcze) < OLX_MIN_SAMPLES:
                z_repo = oferty_z_rynku(olx_query)
                if len(z_repo) >= OLX_MIN_SAMPLES:
                    porownawcze, zrodlo = z_repo, "rynek z repo"
            if len(porownawcze) >= OLX_MIN_SAMPLES:
                wyc = wycen_z_cennikiem(
                    porownawcze,
                    {"y": model_year, "km": mileage_num, "wh": de_wh,
                     "poziom": de_spec.get("poziom")})
                if wyc:
                    olx_price, skorygowana = wyc["cena"], True
                    pewnosc_wyceny = wyc["pewnosc"]
                    olx_price_label = (f"cennik cech ({wyc['cech_znanych']} cech, "
                                       f"{wyc['n']} ofert, {zrodlo})")
                    # Cennik stoi na cenach WYWOŁAWCZYCH. Gdy znamy realny poziom
                    # domykający tego modelu, ścinamy o zaobserwowaną różnicę.
                    demand = get_demand_price(olx_query)
                    if demand:
                        wywolawcza = statistics.median(olx_offers.values())
                        if wywolawcza > 0:
                            hair = demand / wywolawcza
                            if 0.6 <= hair <= 1.05:
                                olx_price = int(olx_price * hair)
                                olx_price_label += f", −{(1 - hair) * 100:.0f}% do domykającej"
            if not olx_price:                     # brak cennika → jak dotąd
                olx_price = get_demand_price(olx_query)
                olx_price_label = "cena popytu OLX" if olx_price else "OLX"
                if not olx_price and len(olx_offers) >= OLX_MIN_SAMPLES:
                    comparable = olx_comparable_price(olx_offers, model_year, mileage_num,
                                                      de_wh, details=load_olx_details())
                    if comparable[0]:
                        olx_price = comparable[0]
                        olx_price_label = f"OLX {comparable[1]}"

            # Realna cena zakupu po negocjacji — zysk liczymy OD NIEJ, nie od wywoławczej
            buy_price, nego_pct, nego_reasons = realistic_buy_price(
                listing["price_num"], listing["price"], desc_text)

            olx_line = olx_compare_str(olx_query, olx_offers, comparable)
            # Zysk liczymy od tego, co REALNIE zapłacisz (po targu na miejscu),
            # a proponujemy w wiadomości cenę zdalną — bo tyle wypada napisać
            # nieznajomemu, resztę zbija się dopiero stojąc przy rowerze.
            cena_realna, nego_laczny = cena_po_ogledzinach(
                listing["price_num"], nego_pct, nego_reasons)
            profit = (calc_profit(cena_realna, olx_price, mileage_num, model_year,
                                  juz_skorygowana=skorygowana)
                      if buy_price and olx_price else None)

            # Płynność (dni do sprzedaży w PL) i ROI roczne z zaangażowanego kapitału
            liquidity_days = get_liquidity(olx_query)
            roi_annual = annual_roi(profit, cena_realna, liquidity_days)

            seen[listing["id"]] = {
                "title": listing["title"],
                "price": listing["price"],
                "price_num": listing["price_num"],
                "buy_price": buy_price,
                "nego_pct": nego_pct,
                "mileage": mileage,
                "mileage_num": mileage_num,
                "year": model_year,
                "url": listing["url"],
                "search": search["name"],
                "date": today,
                "score": sc,
                "profit": profit,
                "olx_median": olx_price,
                "liquidity_days": liquidity_days,
                "roi_annual": roi_annual,
            }
            # ten run może mieć własne dublety — dołóż do indeksu
            recent_index.append((dedup_key(listing["title"]), listing["price_num"], mileage_num, today))

            # dziennik historii (append-only, nigdy kasowany) — trwały zapis rynku
            model_key = olx_query_for(listing["title"], None)
            append_history(model_key, listing["price_num"], ad_id=listing["id"],
                           mileage_num=mileage_num, year=model_year, olx_median=olx_price,
                           profit=profit, buy_price=buy_price)
            trend = price_trend(model_key)

            new_count += 1
            rating = stars(sc)

            discount_str = ""
            if median_price and listing["price_num"]:
                pct = int((median_price - listing["price_num"]) / median_price * 100)
                discount_str = f" ({pct:+d}% vs DE)"

            profit_str = ""
            if profit is not None:
                emoji = "🟢" if profit > 500 else "🟡" if profit > 0 else "🔴"
                profit_str = f"\n{emoji} Zysk PL: ~{profit:+,.0f} zł ({olx_price_label}: {olx_price:,} zł, transport osobno)"
            elif olx_price and buy_price and mileage == "brak danych":
                max_km = max_profitable_mileage(buy_price, olx_price, year=model_year)
                profit_str = f"\n⚠️ Brak przebiegu — opłacalne jeśli {max_km}"

            # Marża negocjacyjna — realna cena zakupu, nie wywoławcza
            nego_str = ""
            if buy_price and nego_pct >= 0.03 and buy_price < listing["price_num"]:
                off = listing["price_num"] - buy_price
                why = f" ({', '.join(nego_reasons)})" if nego_reasons else ""
                nego_str = f"\n🎯 Realnie ~{buy_price:,} € po negocjacji (−{off} €, luz {int(nego_pct*100)}%{why})".replace(",", " ")

            # Płynność + zwrot z kapitału — jak szybko i z jakim zyskiem wraca kasa
            liq_str = ""
            if liquidity_days:
                speed = "szybki obrót" if liquidity_days <= 14 else "średni" if liquidity_days <= 30 else "wolny — kapitał zamrożony"
                liq_str = f"\n⚡ Płynność PL: ~{liquidity_days} dni do sprzedaży ({speed})"
                if profit is not None and buy_price:
                    invested = buy_price * get_eur_pln() + TRANSPORT_PLN
                    if invested > 0:
                        liq_str += f"\n💹 Zwrot: {profit / invested * 100:+.0f}% z kapitału w tym czasie"

            year_str = f"  📅 {model_year}" if model_year else ""

            # Trend cen modelu z własnego dziennika (rynek DE)
            trend_str = ""
            if trend is not None and abs(trend) >= 8:
                if trend < 0:
                    trend_str = f"\n📉 Ceny modelu {trend}% / 3 tyg (rynek DE tanieje — dobry moment)"
                else:
                    trend_str = f"\n📈 Ceny modelu +{trend}% / 3 tyg (rynek DE drożeje)"

            # Czego bot NIE wyczytał — o to i tylko o to zapytamy sprzedawcy.
            braki = []
            if mileage == "brak danych":
                braki.append("przebieg")
            if not rama_txt:
                braki.append("rama")
            if not de_wh:
                braki.append("bateria")
            festpreis = "Festpreis (mur)" in (nego_reasons or [])
            oferta_eur = (buy_price if buy_price and nego_pct >= 0.06
                          and buy_price < listing["price_num"] and not festpreis
                          else None)
            # DWA osobne przyciski: najpierw wyciągasz informacje, targujesz się
            # dopiero gdy sprzedawca odpisze. Oferta doklejona do pierwszego
            # kontaktu potrafi zabić rozmowę — za niska kwota i cisza w odpowiedzi.
            do_skopiowania = [
                (("📋 Kopiuj pytanie" if braki else "📋 Kopiuj zapytanie"),
                 wiadomosc_do_sprzedawcy(braki)),
            ]
            if oferta_eur:
                do_skopiowania.append(
                    (f"💶 Potem: oferta {oferta_eur} €",
                     wiadomosc_oferta(oferta_eur, po_pytaniach=bool(braki))))
            przycisk = klawiatura_kopiuj(do_skopiowania)
            # Zdjęcie główne bierzemy z galerii ogłoszenia, a miniatura z listy
            # jest zapasem — galeria bywa pusta, gdy strona się nie pobrała.
            glowne = zdjecia[0] if zdjecia else listing.get("foto")
            reszta = zdjecia[1:ALBUM_MAX] if len(zdjecia) > 1 else []

            niche_str = ""
            if not is_premium_brand(listing["title"]):
                niche_str = "\n💎 Niszowa marka — przeszła tylko dzięki wyjątkowej cenie (sprawdź płynność na OLX!)"
            if small_battery:
                niche_str += "\n🔋 Mała bateria/SL — trudniejsza i wolniejsza odsprzedaż w PL"

            # Wiek ogłoszenia wprost w wiadomości. Cichy alarm, gdy rower wisi
            # od godzin: nie jesteśmy pierwsi, więc "nikt nie odpisuje" ma
            # wtedy inną przyczynę niż cena, a decyzja o dojeździe też.
            age = listing.get("age_min")
            if age is None:
                age_str = "\n🕐 Wystawione: nie podano (płatne Top-Anzeige)"
            else:
                age_str = f"\n🕐 Wystawione: {format_age(age)}"
                if age > SWIEZOSC_MIN and not przecena_z:
                    # Bot ma się przyznać do spóźnienia SAM, w wiadomości —
                    # inaczej regres wróci po cichu, jak 22.08. Ale przyczyna
                    # bywa różna i trzeba ją rozróżnić, bo tylko jedna jest
                    # do naprawy po naszej stronie.
                    if search["name"] in {k["nazwa"] for k in KANALY}:
                        age_str += ("\n🐌 <b>BOT SIĘ SPÓŹNIŁ</b> — było w kanale, "
                                    "a nie zauważył od razu; to do naprawy, zgłoś")
                        log.error(f"SPÓŹNIENIE {format_age(age)} — {listing['url']}")
                    else:
                        age_str += ("\n🔎 Poza kanałem — sprzedawca nie oznaczył "
                                    "roweru jako e-bike, złapane zapytaniem")
                        log.warning(f"spoza kanału, {format_age(age)} — {listing['url']}")
            if przecena_z:
                # nie spóźnienie, tylko nowa informacja: sprzedawca zmiękł
                age_str += (f"\n📉 <b>PRZECENIONE</b> — bot widział to za "
                            f"{przecena_z} €, teraz weszło w widełki")

            safe_title = html_mod.escape(listing["title"])

            # === WIADOMOŚĆ =================================================
            # Pierwsza linijka to WYNIK FINANSOWY, bo tyle widać na ekranie
            # blokady telefonu, zanim cokolwiek otworzysz. Nazwa roweru druga.
            # Dalej idzie od najważniejszego: rynek, zakup, wyjątki, opis.
            # NAZWA ROWERU NA CZELE. Wcześniej pierwszą linijką był szacowany
            # zysk — a to najmniej pewna liczba w całej wiadomości: sierpniowy
            # Cube wyceniony na ~5 500 zł sprzedał się z zyskiem 1 300 (pomyłka
            # ~4x). Stawianie zgadywanki przed faktami to fałszywa pewność.
            # Na wierzchu jest więc to, co wiemy na pewno: co, za ile, kiedy.
            naglowek = [f"kupno {listing['price']}"]
            if age is not None:
                naglowek.append(format_age(age))
            L = [f"<b>{safe_title}</b>", "  ·  ".join(naglowek), ""]

            # Rezerwacja — od razu pod nazwą, bo zmienia sens całej wiadomości.
            # Sprzedawcy stemplują RESERVIERT na zdjęciu (tego bez AI nie
            # odczytamy), ale Kleinanzeigen dokłada własną plakietkę — widoczną
            # niestety tylko w jednym z układów strony. Dlatego są trzy
            # odpowiedzi, nie dwie, i "nie wiem" też jest wypisane wprost.
            # Użytkownik (23.08): "nie potrzebuję funkcjonalności z infem czy
            # zarezerwowany, chcę być tym pierwszym, który go zarezerwuje".
            # Linijka "nie wiem" poszła precz — była szumem przy każdym rowerze.
            # Zostaje jedno zdanie i tylko przy PEWNOŚCI, bo pojechać po rower,
            # który ma już kupca, to strata dnia, a nie ciekawostka.
            if zarezerwowany is True:
                L.append("🔒 <b>ZAREZERWOWANY</b> — ktoś był pierwszy.\n")

            # Przebieg, RAMA i BATERIA na wierzchu: to trzy rzeczy, które
            # decydują, czy rower da się odsprzedać. Rozmiar bywa ważniejszy
            # od ceny — na L kupca szuka się tygodniami, XS potrafi nie
            # znaleźć go wcale. Czego nie wiemy, tego nie zmyślamy: brakujące
            # pole po prostu znika z linijki.
            fakty = [x for x in (mileage if mileage != "brak danych" else None,
                                 f"rama {rama_txt}" if rama_txt else None,
                                 f"{de_wh} Wh" if de_wh else None,
                                 str(model_year) if model_year else None,
                                 region_z_plz(listing.get("loc"))) if x]
            if fakty:
                L.append(" · ".join(fakty))
            if olx_price:
                # Wystawisz za tyle, dostaniesz mniej — Twój kupujący też
                # przyjedzie i też będzie zbijał. Obie kwoty na wierzchu,
                # żeby zysk nie brał się znikąd.
                dostaniesz = cena_sprzedazy_realna(olx_price)
                _sp = lambda n: f"{n:,}".replace(",", "\u00a0")   # spacja tysięcy
                rynek = (f"W PL wystawisz za ~{_sp(olx_price)} zł, "
                         f"dostaniesz ~{_sp(dostaniesz)} zł")
                if liquidity_days:
                    rynek += f" · schodzą w ~{liquidity_days} dni"
                L.append(rynek)
            if buy_price and nego_pct >= 0.03 and buy_price < listing["price_num"]:
                # Dwie kwoty, bo to dwa etapy: tyle piszesz, tyle celujesz na miejscu.
                def _zl(n):                      # spacja jako separator tysięcy
                    return f"{n:,}".replace(",", "\u00a0")
                cel = (f", na miejscu celuj w {_zl(cena_realna)} €"
                       if cena_realna and cena_realna < buy_price else "")
                L.append(f"Zaproponuj {_zl(buy_price)} €{cel}")
            elif mileage == "brak danych":
                # Do tego miejsca docierają WYŁĄCZNIE przeczytane strony —
                # nieudany odczyt wraca do kolejki i nigdy tu nie dochodzi.
                # Więc "brak danych" znaczy tu dokładnie tyle, ile mówi:
                # sprzedawca przebiegu nie napisał (albo ma go na zdjęciu
                # licznika, czego bez AI nie odczytamy).
                L.append("⚠️ Sprzedawca nie podał przebiegu — zapytaj przed dojazdem")

            # Wyjątki: to, co zmienia decyzję. Nigdy nie ucinane.
            for wyjatek in (
                    f"📉 <b>PRZECENIONE</b> — bot widział to za {przecena_z} €" if przecena_z else None,
                    ("🐌 <b>BOT SIĘ SPÓŹNIŁ</b> — zgłoś to"
                     if (age is not None and age > SWIEZOSC_MIN and not przecena_z
                         and search["name"] in {k["nazwa"] for k in KANALY}) else None),
                    ("🔎 Poza kanałem — sprzedawca nie oznaczył go jako e-bike"
                     if (age is not None and age > SWIEZOSC_MIN and not przecena_z
                         and search["name"] not in {k["nazwa"] for k in KANALY}) else None),
                    "🏆 " + hist_line.strip().lstrip("🏆").strip() if hist_line and "NAJTAŃSZ" in hist_line.upper() else None,
                    "💎 Niszowa marka — przeszła tylko ceną, sprawdź zbyt" if not is_premium_brand(listing["title"]) else None,
                    "🔋 Mała bateria — wolniejsza odsprzedaż w PL" if small_battery else None):
                if wyjatek:
                    L.append(wyjatek)

            # SZACUNEK ZYSKU — w każdym ogłoszeniu, ale na samym dole i zawsze
            # podpisany. Zmierzone 23.08 na 502 wycenach bez przecieku: mediana
            # błędu 20%, a przy pewności "niska" co siódma myli się ponad
            # dwukrotnie. Dlatego pewność jedzie razem z kwotą — bez niej liczba
            # udaje wiedzę. Gdy nie ma z czego liczyć, bot mówi to wprost,
            # zamiast milczeć albo zgadywać.
            if profit is not None:
                znak = "🔥" if profit > 500 else "🟡" if profit > 0 else "🔴"
                # Brak etykiety pewności = wycena NIE przeszła przez cennik cech,
                # tylko przez prostsze porównanie. Taka liczba wygląda tak samo,
                # więc musi się przedstawić — inaczej udaje mocniejszą, niż jest.
                ogon = (f" (pewność: {pewnosc_wyceny})" if pewnosc_wyceny
                        else " (szacunek zgrubny)")
                L.append(f"{znak} <i>Szacowany zysk: ~{profit:+,.0f} zł{ogon}</i>"
                         .replace(",", " "))
            elif not olx_price:
                L.append("❔ <i>Zysku nie liczę — za mało podobnych ofert na OLX</i>")
            else:
                L.append("❔ <i>Zysku nie liczę — brak danych do porównania</i>")

            # Opis po polsku — OZDOBNIK. Żadna liczba decyzyjna z niego nie
            # pochodzi (patrz tlumacz_opis), więc wolno go przyciąć albo pominąć.
            szkielet = "\n".join(L) + f"\n\n{listing['url']}"
            zapas = TELEGRAM_PODPIS_MAX - len(szkielet) - 20
            opis_pl = tlumacz_opis(desc_text) if zapas > 120 else None
            if opis_pl:
                if len(opis_pl) > zapas:
                    opis_pl = opis_pl[:zapas].rsplit(" ", 1)[0] + "…"
                L += ["", "<i>„" + html_mod.escape(opis_pl) + "”</i>"]

            L += ["", listing["url"]]
            msg = "\n".join(L)
            klucz = -1 if przecena_z else (age if age is not None else 10 ** 9)
            pending_msgs.append((klucz, msg, glowne, przycisk, reszta))
            log.info(f"Nowe (score {sc}, wiek {format_age(age)}): {listing['title']}")

    if new_count == 0:
        log.info("Brak nowych ogłoszeń.")

    # Alert zdrowia: 0 ogłoszeń we WSZYSTKICH wyszukiwaniach — z diagnozą
    # przyczyny (serwis leży / blokada / zmiana HTML) i bez spamu co 5 min
    check_feed_health(all_stats, total_found)

    # Monitor zdrowia parsera (#2) — alert przy spadku skuteczności odczytu
    check_parser_health(all_stats)

    # Kanarek OLX i pełna diagnostyka tylko w biegu z zapytaniami kluczowymi —
    # w pętli co minutę byłoby to tysiące zbędnych zapytań na dobę
    if not tylko_feed:
        olx_kanarek()

    # 1. Zapisz bazę (plik + git) — DOPIERO POTEM wysyłka.
    # Przerwany run = co najwyżej brak powiadomienia, nigdy duplikat.
    # W pętli push idzie tylko przy powiadomieniach albo co PUSH_CO_MIN —
    # inaczej byłby commit co minutę, a bez pushu przy powiadomieniu
    # ubity bieg wysłałby te same rowery drugi raz.
    save_seen(seen)
    global _ostatni_push
    if pending_msgs or (time.time() - _ostatni_push) > PUSH_CO_MIN * 60:
        persist_seen_git()
        _ostatni_push = time.time()

    # 2. Wyślij zaległe powiadomienia (odstęp — limit Telegrama ~1 msg/s).
    # Najświeższe idą pierwsze: przy paczce kilku ogłoszeń liczy się minuta,
    # a przy 1,2 s odstępu kolejność wysyłki jest realną przewagą.
    pending_msgs.sort(key=lambda x: x[0])
    for i, (_, m, foto, przycisk, reszta) in enumerate(pending_msgs):
        if i:
            time.sleep(1.2)
        # zdjęcie roweru z podpisem; gdy się nie da — zwykły tekst, byle
        # powiadomienie doszło (ozdobnik nigdy nie może zjeść treści)
        if not send_telegram_photo(foto, m, przycisk):
            send_telegram(m, przycisk)
        # reszta galerii pod spodem, po cichu — jeden rower, jedno brzęknięcie
        if reszta:
            time.sleep(0.5)
            send_telegram_album(reszta)

    # 3. Na samym końcu: JEDYNE miejsce, które może zaalarmować o awarii.
    # Rowery mają pierwszeństwo przed narzekaniem bota na własne zdrowie.
    sprawdz_uklad()
    ocen_zdrowie(_problemy)


if __name__ == "__main__":
    if PETLA_MINUT <= 0:
        main()                       # pojedynczy skan (dawne zachowanie, testy)
    else:
        # Pętla w jednym biegu — tryb na wypadek, gdyby tempo trzeba było
        # nadawać z wnętrza biegu zamiast z zewnątrz. Bieg ma wtedy trwać
        # dłużej niż odstęp wyzwalacza, żeby kanał nie został bez opieki.
        koniec = time.time() + PETLA_MINUT * 60
        ostatnie_kluczowe = 0.0
        log.info(f"Pętla: {PETLA_MINUT} min, skan co {PETLA_ODSTEP_S} s, "
                 f"zapytania kluczowe co {KLUCZOWE_CO_MIN} min")
        while time.time() < koniec:
            start = time.time()
            kluczowe = (start - ostatnie_kluczowe) >= KLUCZOWE_CO_MIN * 60
            try:
                main(tylko_feed=not kluczowe)
                if kluczowe:
                    ostatnie_kluczowe = start
            except Exception:
                # jeden wywrócony skan nie może położyć całego biegu —
                # następny ruszy za minutę od tego samego znacznika
                log.exception("Skan przerwany błędem, próbuję dalej")
            spij = PETLA_ODSTEP_S - (time.time() - start)
            if spij > 0 and time.time() + spij < koniec:
                time.sleep(spij)
