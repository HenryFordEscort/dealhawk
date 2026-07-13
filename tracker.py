import re
import os
import json
import time
import html as html_mod
import logging
import statistics
import cloudscraper
from datetime import date, timedelta
from pathlib import Path

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
# Opcjonalny — gdy ustawiony, przebieg czyta Claude Haiku zamiast regexów
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

MIN_PRICE = 800
MAX_PRICE = 2500
MAX_MILEAGE = 3000

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
    r"e-bike", r"ebike", r"e bike", r"elektro", r"pedelec", r"bosch",
    r"shimano steps", r"yamaha", r"brose", r"fazua", r"\bakku\b", r"\bwh\b",
    r"\blevo\b", r"\btrek rail\b", r"powerfly", r"macina", r"\bstrike\b",
    r"\bpatron\b",
    # nazwy modeli które SĄ elektryczne z definicji (bez tego "Cube Stereo
    # Hybrid 120 Pro 625" bez słowa Wh/Bosch był błędnie odrzucany jako analog)
    r"stereo hybrid", r"kenevo", r"\be-mtb\b", r"\bemtb\b", r"e-mountainbike",
    r"e-fully", r"e fully", r"genius e-ride", r"e-ride", r"\d{3}\s*wh",
]

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


def olx_search_url(query: str) -> str:
    slug = query.lower().replace(" ", "-")
    return f"https://www.olx.pl/sport-hobby/rowery/q-{slug}/"


def fetch_olx_offers(query: str, pages: int = 2) -> dict:
    """Zwraca {url_oferty: cena} z pierwszych `pages` stron wyników OLX
    (więcej próbki = lepsze filtrowanie do porównywalnych)."""
    out = {}
    slug = query.lower().replace(" ", "-")
    for page in range(1, pages + 1):
        url = f"https://www.olx.pl/sport-hobby/rowery/q-{slug}/"
        if page > 1:
            url += f"?page={page}"
        try:
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "pl-PL"}, timeout=15)
            pairs = re.findall(r'"price":(\d+),"url":"(https://www\.olx\.pl/d/oferta/[^"]+)"', r.text)
            new = {u: int(p) for p, u in pairs if 500 < int(p) < 80000}
            if not new:
                break  # pusta strona = koniec wyników
            out.update(new)
        except Exception:
            break
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


def _parse_detail_fields(h: str) -> dict:
    """Wyciąga strukturalne pola ze strony oferty OLX: przebieg, stan,
    a z OPISU (nie z boilerplate strony!) także rocznik i baterię Wh."""
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
              or re.search(r'\b(20(?:1[5-9]|2[0-6]))\s*r(?:\b|ok)', desc, re.I))
        if ym:
            out["y"] = int(ym.group(1))
        whm = re.search(r'(\d{3})\s*wh\b', desc, re.I)
        if whm and 300 <= int(whm.group(1)) <= 1000:
            out["wh"] = int(whm.group(1))
    return out


def fetch_olx_detail(url: str) -> dict:
    """Pobiera stronę oferty OLX → strukturalny przebieg/stan/rocznik/bateria."""
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "pl-PL"}, timeout=15)
        return _parse_detail_fields(r.text)
    except Exception as e:
        log.error(f"fetch_olx_detail error: {e}")
    return {}


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
    """Realna cena zakupu po negocjacji (zaokrąglona do 10 €)."""
    if not price_num:
        return None, 0.0, []
    pct, reasons = negotiation_headroom(price_num, price_str, desc)
    return int(round(price_num * (1 - pct) / 10) * 10), pct, reasons


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


def calc_profit(price_de_eur: int, price_pl_pln: int, km=None, year=None) -> int:
    kurs = get_eur_pln()
    koszt_de = price_de_eur * kurs
    adjusted_pl = price_pl_pln * mileage_factor(km) * year_factor(year)
    return int(adjusted_pl - koszt_de - TRANSPORT_PLN)


def max_profitable_mileage(price_de_eur: int, price_pl_pln: int, min_profit: int = 500, year=None) -> str:
    """Zwraca max przebieg przy którym deal jest opłacalny (zysk >= min_profit PLN)."""
    kurs = get_eur_pln()
    price_pl_pln = price_pl_pln * year_factor(year)   # skoryguj o rocznik
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


def send_telegram(text: str):
    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
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


def fetch_listing_details(url: str, title: str = "") -> tuple:
    """Pobiera stronę ogłoszenia raz.
    Zwraca (mileage_str, description_text, price_str|None)."""
    try:
        r = scraper.get(url, timeout=15)
        r.raise_for_status()
        r.encoding = "utf-8"
        html = r.text

        # Cena ze strony ogłoszenia (ratunek gdy lista jej nie miała)
        price_m = re.search(r'id="viewad-price"[^>]*>\s*([^<]+)', html)
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
        desc_html = desc_match.group(1) if desc_match else ""
        desc_text = re.sub(r'<[^>]+>', ' ', desc_html)

        # 1. Claude Haiku (gdy klucz API ustawiony) — czyta opis jak człowiek
        llm = llm_extract_mileage(title, desc_text)
        if llm is not None:
            _, km = llm
            return (_format_km(km) if km else "brak danych"), desc_text, detail_price

        # 2. Fallback: reguły regex
        mileage = _extract_mileage(title, desc_text)
        return mileage, desc_text, detail_price

    except Exception as e:
        log.error(f"Listing fetch error: {e}")
    # None = fetch się nie udał (odróżnialne od pustego opisu)
    return "brak danych", None, None


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
            r'(?:Kilometerstand|Laufleistung|km[\s-]?Stand|Tachostand)[^\d]{0,40}(\d[\d.,]*)\s*km',
            desc_text, re.IGNORECASE
        )
        if attr:
            raw = attr.group(1).replace(".", "").replace(",", "")
            if raw.isdigit() and 10 <= int(raw) <= 25000:
                return _format_km(int(raw))

        # 3. System punktowy — TYLKO w tekście opisu, nigdy w pełnym HTML
        RANGE_CONTEXT = [
            "reichweite", "wh", "akku", "batterie", "kapazität",
            "ladung", "range", "motorleistung",
        ]

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
            if mileage_ctx:
                score += 15
            else:
                for kw in RANGE_CONTEXT:
                    if kw in ctx_near:
                        score -= 20
                        break

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
    stats = {"name": search["name"], "blocks": 0, "title_hits": 0, "price_hits": 0, "html": None}
    try:
        if REPLAY_DIR:  # odtwarzanie zapisanego HTML zamiast sieci
            fp = Path(REPLAY_DIR) / (re.sub(r'[^\w]+', "_", search["name"]) + ".html")
            html = fp.read_text(encoding="utf-8") if fp.exists() else ""
        else:
            r = scraper.get(search["url"], timeout=15)
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
            lm = re.search(r'(\b\d{5}\s+[^<\n]{2,40})', block)
            loc = re.sub(r'\s+', ' ', lm.group(1)).strip() if lm else None

            results.append({
                "id": ad_id,
                "title": title,
                "price": price_str,
                "price_num": parse_price(price_str),
                "loc": loc,
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
    for path in ("seen.json", "history.jsonl", "market.jsonl", "parser_health.json", "blackbox"):
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
        PARSE_STATE_FILE.write_text(json.dumps({
            "ok": now_ok, "title_rate": round(title_rate, 2),
            "price_rate": round(price_rate, 2), "checked": date.today().isoformat(),
        }))

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
            send_telegram(
                "🛠️ <b>DealHawk — parser wymaga uwagi!</b>\n\n"
                f"Skuteczność odczytu spadła: tytuł {int(title_rate*100)}%, "
                f"cena {int(price_rate*100)}% (norma >50%).\n"
                "Prawdopodobnie Kleinanzeigen zmieniło layout — HTML zapisany "
                "w blackbox/ do naprawy wzorców."
            )
            log.error(f"Parser drift: title={title_rate:.2f} price={price_rate:.2f}")
        elif now_ok and not was_ok:
            send_telegram("✅ <b>DealHawk — parser znów sprawny.</b>")
            log.info("Parser wrócił do normy")
    except Exception as e:
        log.error(f"check_parser_health error: {e}")


def main():
    seen = prune_seen(load_seen())
    new_count = 0
    total_found = 0
    today = date.today().isoformat()
    olx_cache = {}
    price_hist = build_price_history(seen)
    recent_index = build_recent_index(seen)
    pending_msgs = []
    all_stats = []

    for search in SEARCHES:
        listings, stats = fetch_listings(search)
        all_stats.append(stats)
        total_found += len(listings)
        log.info(f"[{search['name']}] znaleziono {len(listings)} ogłoszeń")

        # mediana ceny z tego wyszukiwania do scoringu
        prices_in_search = [l["price_num"] for l in listings if l["price_num"]]
        median_price = statistics.median(prices_in_search) if prices_in_search else None

        for listing in listings:
            prev = seen.get(listing["id"])
            if prev is not None:
                # Obniżka ceny na ogłoszeniu, które wcześniej przeszło filtry
                if (isinstance(prev, dict) and prev.get("score") is not None
                        and listing["price_num"] and prev.get("price_num")
                        and listing["price_num"] < prev["price_num"] * 0.95):
                    # świeża weryfikacja przebiegu — dane w bazie mogą być stare/błędne
                    fresh_mileage, _, _ = fetch_listing_details(listing["url"], listing["title"])
                    fresh_num = parse_mileage(fresh_mileage)
                    old_price = prev["price_num"]
                    prev["mileage"] = fresh_mileage
                    prev["mileage_num"] = fresh_num
                    prev["price"] = listing["price"]
                    prev["price_num"] = listing["price_num"]
                    if is_too_worn(fresh_num):
                        log.info(f"Obniżka pominięta (przebieg {fresh_mileage}): {listing['title'][:50]}")
                        continue
                    pending_msgs.append(
                        f"📉 <b>DealHawk — obniżka ceny!</b>\n\n"
                        f"📌 <b>{html_mod.escape(listing['title'])}</b>\n"
                        f"💰 {old_price} € → <b>{listing['price']}</b>\n"
                        f"🚵 {fresh_mileage}\n"
                        f"🔗 {listing['url']}"
                    )
                    # trajektoria obniżki do dziennika finalistów
                    append_history(olx_query_for(listing["title"], None), listing["price_num"],
                                   ad_id=listing["id"], mileage_num=fresh_num, ev="drop")
                    log.info(f"Obniżka {old_price} -> {listing['price_num']}: {listing['title'][:50]}")
                continue

            # LOG CAŁEGO RYNKU — każde nowe ogłoszenie, PRZED filtrami
            log_market(listing, search["name"])

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

            mileage, desc_text, detail_price = fetch_listing_details(listing["url"], listing["title"])
            mileage_num = parse_mileage(mileage)

            # Ratunek ceny ze strony ogłoszenia gdy lista jej nie dała
            if not listing["price_num"] and detail_price:
                listing["price"] = detail_price
                listing["price_num"] = parse_price(detail_price)

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

            # cena do kalkulacji zysku: popyt > cena porównywalna (rocznik/przebieg/bateria)
            de_wh = battery_wh(listing["title"], desc_text)   # bateria niemieckiego roweru
            olx_price = get_demand_price(olx_query)
            olx_price_label = "cena popytu OLX" if olx_price else "OLX"
            comparable = None
            if not olx_price and len(olx_offers) >= OLX_MIN_SAMPLES:
                comparable = olx_comparable_price(olx_offers, model_year, mileage_num, de_wh,
                                                  details=load_olx_details())
                if comparable[0]:
                    olx_price = comparable[0]
                    olx_price_label = f"OLX {comparable[1]}"

            # Realna cena zakupu po negocjacji — zysk liczymy OD NIEJ, nie od wywoławczej
            buy_price, nego_pct, nego_reasons = realistic_buy_price(
                listing["price_num"], listing["price"], desc_text)

            olx_line = olx_compare_str(olx_query, olx_offers, comparable)
            profit = calc_profit(buy_price, olx_price, mileage_num, model_year) if buy_price and olx_price else None

            # Płynność (dni do sprzedaży w PL) i ROI roczne z zaangażowanego kapitału
            liquidity_days = get_liquidity(olx_query)
            roi_annual = annual_roi(profit, buy_price, liquidity_days)

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

            # Gotowa wiadomość do sprzedawcy (tap = kopiuj). Gdy jest luz — od razu
            # z kwotą oferty na realnej cenie; do tego pytanie o przebieg gdy brak.
            ask_str = ""
            km_q = " Wie viele km ist es gelaufen?" if mileage == "brak danych" else ""
            if buy_price and nego_pct >= 0.06 and "Festpreis (mur)" not in nego_reasons and buy_price < listing["price_num"]:
                ask_str = (
                    "\n📋 Oferta do wysłania (tapnij aby skopiować):\n"
                    f"<code>Hallo, wäre der Preis von {buy_price} € möglich? "
                    f"Ich könnte kurzfristig mit Bargeld abholen.{km_q} Danke!</code>"
                )
            elif mileage == "brak danych":
                ask_str = (
                    "\n📋 Zapytaj sprzedawcę (tapnij aby skopiować):\n"
                    "<code>Hallo, wie viele Kilometer ist das Bike insgesamt gelaufen? Danke!</code>"
                )

            niche_str = ""
            if not is_premium_brand(listing["title"]):
                niche_str = "\n💎 Niszowa marka — przeszła tylko dzięki wyjątkowej cenie (sprawdź płynność na OLX!)"
            if small_battery:
                niche_str += "\n🔋 Mała bateria/SL — trudniejsza i wolniejsza odsprzedaż w PL"

            safe_title = html_mod.escape(listing["title"])
            # link ogłoszenia MUSI być pierwszym linkiem w wiadomości —
            # Telegram robi podgląd (zdjęcie roweru) z pierwszego linku
            msg = (
                f"🦅 <b>DealHawk</b> {rating}\n\n"
                f"📌 <b>{safe_title}</b>\n"
                f"🔗 {listing['url']}\n"
                f"💰 {listing['price']}{discount_str}\n"
                f"🚵 {mileage}{year_str}\n"
                f"⭐ Score: {sc}/100"
                f"{profit_str}"
                f"{nego_str}"
                f"{liq_str}"
                f"{trend_str}"
                f"{olx_line}"
                f"{hist_line or ''}"
                f"{niche_str}"
                f"{ask_str}\n"
                f"🔍 {search['name']}"
            )
            pending_msgs.append(msg)
            log.info(f"Nowe (score {sc}): {listing['title']}")

    if new_count == 0:
        log.info("Brak nowych ogłoszeń.")

    # Alert zdrowia: 0 ogłoszeń we WSZYSTKICH wyszukiwaniach = zmiana HTML
    # Kleinanzeigen albo blokada — bez alertu bot umarłby po cichu
    if total_found == 0:
        send_telegram(
            "🚨 <b>DealHawk — awaria parsera!</b>\n\n"
            "Wszystkie wyszukiwania zwróciły 0 ogłoszeń. "
            "Prawdopodobnie Kleinanzeigen zmieniło HTML albo blokuje scraper."
        )
        log.error("Wszystkie wyszukiwania puste — możliwa awaria parsera")

    # Monitor zdrowia parsera (#2) — alert przy spadku skuteczności odczytu
    check_parser_health(all_stats)

    # 1. Zapisz bazę (plik + git) — DOPIERO POTEM wysyłka.
    # Przerwany run = co najwyżej brak powiadomienia, nigdy duplikat.
    save_seen(seen)
    persist_seen_git()

    # 2. Wyślij zaległe powiadomienia (odstęp — limit Telegrama ~1 msg/s)
    for i, m in enumerate(pending_msgs):
        if i:
            time.sleep(1.2)
        send_telegram(m)


if __name__ == "__main__":
    main()
