import re
import os
import json
import logging
import statistics
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import requests
import cloudscraper

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("otomoto.log"),
    ],
)
log = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

SEEN_FILE = Path("seen_otomoto.json")
SEEN_OLX_FILE = Path("seen_olx.json")
scraper = cloudscraper.create_scraper()

# ---------------------------------------------------------------------------
# Wyszukiwania
#
# Filtry w URL/API są WYŁĄCZNIE zawężeniem ruchu, nie gwarancją. Sprawdzone
# 21.08.2026 na żywych danych:
#   * Otomoto na /osobowe/audi/a5 dokłada „podobne oferty" — wracały A6
#     Limousine, Q5 i A4 Avant, mimo modelu w ścieżce adresu.
#   * OLX ignoruje filter_enum_condition i filter_enum_petrol — w odpowiedzi
#     na zapytanie o uszkodzone diesle było 102 „Nieuszkodzony" i 36 „Benzyna".
# Dlatego każde twarde kryterium jest sprawdzane w kodzie, na polach
# strukturalnych ogłoszenia (patrz `sprawdz_kryteria`). Dopasowanie po słowach
# w tytule zostało usunięte: „320" łapało się na „320KM" (moc silnika!), przez
# co przychodziły BMW Serii 5 i 8, a „gran coupe" wpuszczało Serię 2.
#
# Filtry URL (kodowane):
#   filter_enum_fuel_type=diesel
#   filter_enum_gearbox=automatic
#   filter_enum_drive=awd             (quattro / 4x4)
#   filter_float_year:from / :to
#   filter_float_engine_capacity:from / :to  (w cm3)
# ---------------------------------------------------------------------------

# Górny limit przebiegu — wspólny dla wszystkich wyszukiwań.
PRZEBIEG_MAX = 200_000

# Klucze modeli. UWAGA: OLX używa dla BMW slugów węgierskich („3-as-sorozat"),
# Otomoto polskich („seria-3") — ten sam samochód, dwa zapisy, więc w zbiorze
# muszą być oba. Etykieta („Seria 3") jest sprawdzana dodatkowo.
MODELE_A5_SPORTBACK = {"a5-sportback"}
MODELE_A4_SEDAN = {"a4-limousine"}
MODELE_SERIA_3 = {"3-as-sorozat", "seria-3"}
MODELE_SERIA_4 = {"seria-4"}

SEARCHES = [
    {
        "name": "Audi A5 Sportback 2.0 TDI quattro AT 2015-2019",
        "url": (
            "https://www.otomoto.pl/osobowe/audi/a5"
            "?search%5Bfilter_enum_fuel_type%5D=diesel"
            "&search%5Bfilter_enum_gearbox%5D=automatic"
            "&search%5Bfilter_enum_drive%5D=awd"
            "&search%5Bfilter_float_year%3Afrom%5D=2015"
            "&search%5Bfilter_float_year%3Ato%5D=2019"
            "&search%5Bfilter_float_engine_capacity%3Afrom%5D=1900"
            "&search%5Bfilter_float_engine_capacity%3Ato%5D=2100"
            "&search%5Bfilter_enum_damaged%5D=1"
        ),
        "kryteria": {
            "modele": MODELE_A5_SPORTBACK,
            "rok": (2015, 2019),
            "paliwo": "diesel",
            "skrzynia": "automatic",
            "naped": "awd",
            "pojemnosc": (1900, 2100),
            "uszkodzony": True,
        },
        "olx_query": "audi a5 sportback tdi quattro",
    },
    {
        "name": "Audi A4 Sedan 2.0 TDI quattro AT 2015-2019",
        "url": (
            "https://www.otomoto.pl/osobowe/audi/a4"
            "?search%5Bfilter_enum_fuel_type%5D=diesel"
            "&search%5Bfilter_enum_gearbox%5D=automatic"
            "&search%5Bfilter_enum_drive%5D=awd"
            "&search%5Bfilter_float_year%3Afrom%5D=2015"
            "&search%5Bfilter_float_year%3Ato%5D=2019"
            "&search%5Bfilter_float_engine_capacity%3Afrom%5D=1900"
            "&search%5Bfilter_float_engine_capacity%3Ato%5D=2100"
            "&search%5Bfilter_enum_damaged%5D=1"
            "&search%5Bfilter_enum_bodywork_type%5D=sedan"
        ),
        "kryteria": {
            "modele": MODELE_A4_SEDAN,
            "nadwozie": {"sedan"},
            "rok": (2015, 2019),
            "paliwo": "diesel",
            "skrzynia": "automatic",
            "naped": "awd",
            "pojemnosc": (1900, 2100),
            "uszkodzony": True,
        },
        "olx_query": "audi a4 sedan tdi quattro",
    },
    {
        "name": "BMW G20 Seria 3 Sedan 2.0d xDrive AT 2019-2021",
        "url": (
            "https://www.otomoto.pl/osobowe/bmw/seria-3"
            "?search%5Bfilter_enum_fuel_type%5D=diesel"
            "&search%5Bfilter_enum_gearbox%5D=automatic"
            "&search%5Bfilter_enum_drive%5D=awd"
            "&search%5Bfilter_float_year%3Afrom%5D=2019"
            "&search%5Bfilter_float_year%3Ato%5D=2021"
            "&search%5Bfilter_float_engine_capacity%3Afrom%5D=1900"
            "&search%5Bfilter_float_engine_capacity%3Ato%5D=2100"
            "&search%5Bfilter_enum_damaged%5D=1"
            "&search%5Bfilter_enum_bodywork_type%5D=sedan"
        ),
        "kryteria": {
            "modele": MODELE_SERIA_3,
            # bez tego wchodzi Touring — na OLX to 27 z 48 wyników zapytania
            "nadwozie": {"sedan"},
            "rok": (2019, 2021),
            "paliwo": "diesel",
            "skrzynia": "automatic",
            "naped": "awd",
            "pojemnosc": (1900, 2100),
            "uszkodzony": True,
        },
        "olx_query": "bmw seria 3 g20 diesel xdrive sedan",
    },
    {
        "name": "BMW G26 Seria 4 Gran Coupe 2.0d xDrive AT 2021-2023",
        "url": (
            "https://www.otomoto.pl/osobowe/bmw/seria-4"
            "?search%5Bfilter_enum_fuel_type%5D=diesel"
            "&search%5Bfilter_enum_gearbox%5D=automatic"
            "&search%5Bfilter_enum_drive%5D=awd"
            "&search%5Bfilter_float_year%3Afrom%5D=2021"
            "&search%5Bfilter_float_year%3Ato%5D=2023"
            "&search%5Bfilter_float_engine_capacity%3Afrom%5D=1900"
            "&search%5Bfilter_float_engine_capacity%3Ato%5D=2100"
            "&search%5Bfilter_enum_damaged%5D=1"
            "&search%5Bfilter_enum_bodywork_type%5D=coupe"
        ),
        "kryteria": {
            "modele": MODELE_SERIA_4,
            # BEZ filtra nadwozia: Gran Coupé bywa wystawiane jako coupe, sedan
            # ORAZ hatchback (29/10/5 w próbce) — nie da się z tego zrobić sita
            "rok": (2021, 2023),
            "paliwo": "diesel",
            "skrzynia": "automatic",
            "naped": "awd",
            "pojemnosc": (1900, 2100),
            "uszkodzony": True,
        },
        "olx_query": "bmw seria 4 gran coupe g26 diesel xdrive",
    },
]

# ---------------------------------------------------------------------------
# OLX — wyszukiwania przez API (category_id=84 = Samochody osobowe)
#
# `query` to zwykłe szukanie po tekście i nic nie gwarantuje — w odpowiedzi na
# „bmw seria 4 gran coupe uszkodzony" przychodziły „Pozostałe Ford" i
# „Pozostałe Hyundai". Filtry filter_enum_* zostawiam, bo zawężają ruch, ale
# OLX potrafi je zignorować, więc rozstrzyga `kryteria` sprawdzane w kodzie.
#
# Historia: 23.07.2026 filter_enum_gearbox i filter_float_year wyleciały z
# zapytania, bo dawały HTTP 400. Rok wrócił wtedy jako kontrola w kodzie,
# ale skrzynia, napęd i pojemność NIE — i przez miesiąc nikt nie sprawdzał
# ani automatu, ani quattro. Teraz sprawdza je `sprawdz_kryteria`.
# ---------------------------------------------------------------------------
OLX_API = "https://www.olx.pl/api/v1/offers/"
OLX_SEARCHES = [
    {
        "name": "OLX Audi A5 Sportback 2.0 TDI quattro 2015-2019",
        "params": {"category_id": 84, "limit": 50, "currency": "PLN",
                   "query": "audi a5 sportback uszkodzony",
                   "filter_enum_condition": "damaged", "filter_enum_petrol": "diesel"},
        "kryteria": SEARCHES[0]["kryteria"],
    },
    {
        "name": "OLX Audi A4 Sedan 2.0 TDI quattro 2015-2019",
        "params": {"category_id": 84, "limit": 50, "currency": "PLN",
                   "query": "audi a4 sedan uszkodzony",
                   "filter_enum_condition": "damaged", "filter_enum_petrol": "diesel"},
        "kryteria": SEARCHES[1]["kryteria"],
    },
    {
        "name": "OLX BMW G20 Seria 3 320d xDrive 2019-2021",
        "params": {"category_id": 84, "limit": 50, "currency": "PLN",
                   "query": "bmw 320d xdrive uszkodzony",
                   "filter_enum_condition": "damaged", "filter_enum_petrol": "diesel"},
        "kryteria": SEARCHES[2]["kryteria"],
    },
    {
        "name": "OLX BMW G26 Seria 4 Gran Coupe 420d xDrive 2021-2023",
        "params": {"category_id": 84, "limit": 50, "currency": "PLN",
                   "query": "bmw 420d gran coupe uszkodzony",
                   "filter_enum_condition": "damaged", "filter_enum_petrol": "diesel"},
        "kryteria": SEARCHES[3]["kryteria"],
    },
]

# Tylko te województwa
REGIONS_ALLOWED = {"małopolskie", "podkarpackie", "świętokrzyskie", "śląskie"}

# Minimalna obniżka względem mediany żeby wysłać powiadomienie (%)
# Dla uszkodzonych aut pomijamy - każde uszkodzone jest warte uwagi
MIN_DISCOUNT_PCT = 0  # ustaw np. 10 żeby filtrować tylko okazje

# Słowa sugerujące uszkodzenie / wypadek
DAMAGE_KEYWORDS = [
    "uszkodzon", "po wypadku", "wypadek", "kolizja",
    "do naprawy", "na części", "niesprawny", "powódź",
    "skradzion", "bez silnika", "silnik uszkodz", "rozbity",
    "uszkodzony", "powypadkowy", "pokolizyjny", "do remontu",
]

GOOD_CONDITION_KEYWORDS = [
    "bezwypadkowy", "bez wypadku", "jeden właściciel", "1 właściciel",
    "serwisowany w aso", " aso", "stan idealny", "jak nowy",
    "bezkolizyjny", "perfekcyjny",
]

# Szacunkowe koszty naprawy na podstawie słów kluczowych w tytule/opisie
REPAIR_COST_KEYWORDS = [
    (["airbag", "poduszk"], 8000),
    (["spalony", "pożar", "pozar", "ogień", "ogien"], 6000),
    (["zatarty", "zatarcie", "zatartym"], 14000),
    (["silnik", "motor"], 12000),
    (["skrzyni", "skrzynię", "skrzynia"], 8000),
    (["turbo"], 5000),
    (["przód", "przod", "front"], 15000),
    (["tył", "tyl", "tył", "tyl "], 10000),
    (["bok", "boczn"], 7000),
    (["dach"], 9000),
    (["powódź", "powodz", "zalany", "woda"], 18000),
    (["wypadek", "kolizja", "powypadkow", "pokolizyjn"], 20000),
]


def days_on_market(created_at: str) -> Optional[int]:
    """Ile dni temu dodano ogłoszenie."""
    if not created_at:
        return None
    try:
        dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).days
    except Exception:
        return None


def in_allowed_region(region: str) -> bool:
    if not region:
        return True  # brak danych = przepuść
    r = region.lower()
    return any(allowed in r for allowed in REGIONS_ALLOWED)


def estimate_repair(title: str, description: str = "") -> Optional[int]:
    """Szacuje koszt naprawy na podstawie słów kluczowych. Zwraca None jeśli brak wskazówek."""
    combined = (title + " " + description).lower()
    total = 0
    matched = False
    for keywords, cost in REPAIR_COST_KEYWORDS:
        if any(kw in combined for kw in keywords):
            total += cost
            matched = True
    return total if matched else None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_seen() -> dict:
    if SEEN_FILE.exists():
        data = json.loads(SEEN_FILE.read_text())
        if isinstance(data, list):
            return {ad_id: {} for ad_id in data}
        return data
    return {}


def save_seen(seen: dict):
    SEEN_FILE.write_text(json.dumps(seen, ensure_ascii=False, indent=2))


def load_seen_olx() -> dict:
    if SEEN_OLX_FILE.exists():
        return json.loads(SEEN_OLX_FILE.read_text())
    return {}


def save_seen_olx(seen: dict):
    SEEN_OLX_FILE.write_text(json.dumps(seen, ensure_ascii=False, indent=2))


def send_telegram(text: str):
    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    try:
        r = requests.post(api_url, json=payload, timeout=10)
        r.raise_for_status()
    except Exception as e:
        log.error(f"Telegram error: {e}")


def fetch_olx_car_price(query: str) -> Optional[int]:
    """Mediana cen z OLX motoryzacja dla podanego zapytania."""
    try:
        from olx import olx_get, parse_olx_cards
        slug = re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-")
        url = f"https://www.olx.pl/motoryzacja/samochody/q-{slug}/"
        r = olx_get(url, timeout=20)          # przez przekaźnik, jeśli ustawiony
        if r is None or r.status_code != 200:
            return None
        # kafelki zamiast wzorca JSON — ten łapał tylko ~38% ofert na stronie
        nums = [c["price"] for c in parse_olx_cards(r.text, 3000, 300000)]
        if nums:
            return int(statistics.median(nums))
    except Exception as e:
        log.error(f"OLX fetch error: {e}")
    return None


# Zaprzeczenia, które trzeba wyciąć PRZED szukaniem słów o uszkodzeniu.
# „nieuszkodzony" zawiera w sobie „uszkodzon", więc gołe `in` czyta zdanie
# „auto nieuszkodzone, bezwypadkowe" jako trafienie i wysyła czyste auto.
ZAPRZECZENIA_RE = re.compile(
    r"\b(?:nie\s?(?:jest\s+|był\s+|byl\s+|po\s+|z\s+)?|bez\s+|brak\s+(?:\w+\s+)?)"
    r"(?:uszkodz\w*|wypad\w*|kolizj\w*|pokolizyjn\w*)",
    re.IGNORECASE,
)


def is_damaged(title: str, description: str = "") -> bool:
    """Czy tekst mówi o uszkodzeniu. Używane tam, gdzie serwis nie podaje
    pola `condition` (Otomoto). Na OLX pierwszeństwo ma pole strukturalne."""
    combined = (title + " " + description).lower()
    combined = ZAPRZECZENIA_RE.sub(" ", combined)
    return any(kw in combined for kw in DAMAGE_KEYWORDS)


# ---------------------------------------------------------------------------
# Kryteria twarde — jedno sito dla Otomoto i OLX
# ---------------------------------------------------------------------------

# Napęd: OLX rozróżnia stały i dołączany 4x4, nas interesuje samo „na cztery koła"
NAPED_MAPA = {
    "all-wheel-permanent": "awd", "all-wheel-auto": "awd", "4x4": "awd",
    "awd": "awd", "rear-wheel": "rwd", "front-wheel": "fwd",
}

# Nazwy pól do komunikatu o brakach — użytkownik ma widzieć, czego bot NIE wie
ETYKIETY = {
    "modele": "model", "nadwozie": "nadwozie", "rok": "rocznik",
    "paliwo": "paliwo", "skrzynia": "skrzynia", "naped": "napęd",
    "pojemnosc": "pojemność", "uszkodzony": "stan", "przebieg": "przebieg",
}


def sprawdz_kryteria(ad: dict, kryteria: dict) -> tuple[bool, list[str]]:
    """Sprawdza ogłoszenie względem kryteriów. Zwraca (pasuje, braki_danych).

    Zasada, ustalona z użytkownikiem: **brak danych to nie niezgodność**.
    Ogłoszenie bez wypełnionego pola przechodzi, ale nazwa pola ląduje w
    `braki` i trafia do wiadomości — decyzję podejmuje człowiek. Twarde NIE
    pada tylko wtedy, gdy serwis podał wartość i ta wartość się nie zgadza.
    """
    braki = []

    def podane(v):
        return v is not None and v != ""

    modele = kryteria.get("modele")
    if modele:
        klucz = (ad.get("model_key") or "").lower()
        etykieta = re.sub(r"\s+", "", (ad.get("model_label") or "").lower())
        if not klucz and not etykieta:
            braki.append(ETYKIETY["modele"])
        elif klucz not in modele and not any(
            re.sub(r"[\s-]+", "", m) == etykieta for m in modele
        ):
            return False, braki

    nadwozie = kryteria.get("nadwozie")
    if nadwozie:
        if not podane(ad.get("body")):
            braki.append(ETYKIETY["nadwozie"])
        elif ad["body"].lower() not in nadwozie:
            return False, braki

    rok = kryteria.get("rok")
    if rok:
        if not podane(ad.get("year")):
            braki.append(ETYKIETY["rok"])
        elif not (rok[0] <= ad["year"] <= rok[1]):
            return False, braki

    for pole, klucz_ad in (("paliwo", "fuel"), ("skrzynia", "gearbox"), ("naped", "drive")):
        oczekiwane = kryteria.get(pole)
        if not oczekiwane:
            continue
        if not podane(ad.get(klucz_ad)):
            braki.append(ETYKIETY[pole])
        elif ad[klucz_ad] != oczekiwane:
            return False, braki

    poj = kryteria.get("pojemnosc")
    if poj:
        if not podane(ad.get("engine_cm3")):
            braki.append(ETYKIETY["pojemnosc"])
        elif not (poj[0] <= ad["engine_cm3"] <= poj[1]):
            return False, braki

    if kryteria.get("uszkodzony"):
        # damaged=False to informacja („Nieuszkodzony"), a nie brak danych
        if ad.get("damaged") is None:
            braki.append(ETYKIETY["uszkodzony"])
        elif not ad["damaged"]:
            return False, braki

    if PRZEBIEG_MAX:
        if not podane(ad.get("mileage_num")):
            braki.append(ETYKIETY["przebieg"])
        elif ad["mileage_num"] > PRZEBIEG_MAX:
            return False, braki

    return True, braki


def comparable_median(listing: dict, pool: list[dict]) -> Optional[float]:
    """
    Mediana cen z puli ogłoszeń podobnych do danego:
      - ten sam rocznik ±1 rok
      - podobny przebieg ±30 000 km
    Jeśli za mało danych (<4 szt.) rozszerza przedział do ±2 lata i ±60 000 km.
    """
    year = listing.get("year")
    km = listing.get("mileage_num")

    for year_delta, km_delta in [(1, 30000), (2, 60000), (3, 100000)]:
        candidates = []
        for p in pool:
            p_price = p.get("price_num")
            p_year = p.get("year")
            p_km = p.get("mileage_num")
            if not p_price or p_price < 1000:
                continue
            if year and p_year and abs(p_year - year) > year_delta:
                continue
            if km is not None and p_km is not None and abs(p_km - km) > km_delta:
                continue
            candidates.append(p_price)
        if len(candidates) >= 4:
            return statistics.median(candidates)

    # Fallback: cała pula
    all_prices = [p["price_num"] for p in pool if p.get("price_num", 0) > 1000]
    return statistics.median(all_prices) if all_prices else None


def score_listing(listing: dict, median_price: Optional[float]) -> int:
    score = 0
    combined = listing.get("title", "").lower() + " " + listing.get("short_desc", "").lower()

    # 1. Cena vs mediana podobnych aut (0–50 pkt)
    price = listing.get("price_num")
    if price and median_price:
        discount_pct = (median_price - price) / median_price * 100
        score += max(0, min(50, int(discount_pct * 2)))

    # 2. Przebieg (0–25 pkt)
    km = listing.get("mileage_num")
    if km is not None:
        if km < 60000:
            score += 25
        elif km < 100000:
            score += 20
        elif km < 150000:
            score += 12
        elif km < 200000:
            score += 5
    else:
        score += 10

    # 3. Rok (0–15 pkt)
    year = listing.get("year")
    if year:
        score += max(0, min(15, (year - 2014) * 3))

    # 4. Stan (0–10 pkt)
    for kw in GOOD_CONDITION_KEYWORDS:
        if kw in combined:
            score += 10
            break

    return score


def format_braki(braki: list) -> str:
    """Czego bot NIE wie o tym aucie. Ogłoszenie z niewypełnionym polem nie
    jest odrzucane, ale musi to powiedzieć wprost — inaczej „przeszło kryteria"
    znaczy raz „sprawdzone", a raz „nie było czego sprawdzić"."""
    if not braki:
        return ""
    return "\n❓ Nie podano w ogłoszeniu: " + ", ".join(braki)


def stars(score: int) -> str:
    if score >= 70:
        return "🔥🔥🔥"
    if score >= 50:
        return "🔥🔥"
    if score >= 30:
        return "🔥"
    return ""


# ---------------------------------------------------------------------------
# Scraping Otomoto (urqlState w Next.js JSON)
# ---------------------------------------------------------------------------

def _parse_node(node: dict) -> Optional[dict]:
    """Normalizuje pojedynczy węzeł ogłoszenia Otomoto."""
    ad_id = str(node.get("id", ""))
    if not ad_id:
        return None

    title = node.get("title", "").strip()
    url = node.get("url", "") or f"https://www.otomoto.pl/oferta/{ad_id}"
    short_desc = node.get("shortDescription", "") or ""
    loc = node.get("location") or {}
    location_city = loc.get("city", {}).get("name", "")
    location_region = loc.get("region", {}).get("name", "").lower()
    created_at = node.get("createdAt", "")

    # Cena — format: price.amount.units (PLN, całkowita)
    price_num = None
    price_str = "brak ceny"
    try:
        amount = node["price"]["amount"]
        price_num = int(float(amount.get("units", 0) or amount.get("value", 0) or 0))
        if price_num:
            price_str = f"{price_num:,} PLN".replace(",", " ")
    except (KeyError, TypeError, ValueError):
        pass

    # Parametry (rok, przebieg, silnik, model, wersja …)
    params = {}
    mileage_num = None
    year = None
    engine_hp = None
    model_value = ""
    version_value = ""

    for p in node.get("parameters", []) or []:
        k = p.get("key", "")
        v = p.get("value", "") or p.get("displayValue", "") or ""
        params[k] = v
        if k == "mileage":
            try:
                mileage_num = int(re.sub(r"\D", "", str(v)))
            except ValueError:
                pass
        elif k == "year":
            try:
                year = int(v)
            except ValueError:
                pass
        elif k == "engine_power":
            try:
                engine_hp = int(re.sub(r"\D", "", str(v)))
            except ValueError:
                pass
        elif k == "model":
            model_value = str(v).lower()
        elif k == "version":
            version_value = str(v).lower()

    engine_cm3 = None
    if params.get("engine_capacity"):
        try:
            engine_cm3 = int(re.sub(r"\D", "", str(params["engine_capacity"])))
        except ValueError:
            pass

    # Otomoto nie zwraca pola `drive` w wynikach wyszukiwania — jedyny ślad po
    # napędzie jest w nazwie wersji („2.0 TDI quattro S tronic") albo w tytule.
    # Gdy go tam nie ma, zostaje None = „nie wiem", a nie „nie ma".
    naped_tekst = f"{version_value} {title.lower()}"
    drive = "awd" if any(
        w in naped_tekst for w in ("quattro", "xdrive", "4x4", "4matic", "allrad")
    ) else None

    return {
        "id": ad_id,
        "title": title,
        "url": url,
        "short_desc": short_desc,
        "city": location_city,
        "region": location_region,
        "created_at": created_at,
        "price_num": price_num,
        "price_str": price_str,
        "params": params,
        "mileage_num": mileage_num,
        "year": year,
        "engine_hp": engine_hp,
        "model_value": model_value,
        "version_value": version_value,
        # pola znormalizowane — wspólny język z OLX-em dla `sprawdz_kryteria`
        "model_key": model_value,
        "model_label": "",
        "fuel": (params.get("fuel_type") or "").lower() or None,
        "gearbox": (params.get("gearbox") or "").lower() or None,
        "drive": drive,
        "engine_cm3": engine_cm3,
        "body": None,          # brak w odpowiedzi wyszukiwarki Otomoto
        # Otomoto nie ma pola `condition`, a URL-e nie filtrują po uszkodzeniu —
        # słowa kluczowe to jedyny sygnał, więc ich brak znaczy „nieuszkodzone"
        "damaged": is_damaged(title, short_desc),
    }


HEADERS = {
    "Accept-Language": "pl-PL,pl;q=0.9",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}


def _fetch_page(url: str) -> list:
    """Pobiera jedną stronę wyników Otomoto, zwraca listę edges."""
    try:
        r = scraper.get(url, timeout=25, headers=HEADERS)
        r.raise_for_status()
        json_blocks = re.findall(
            r'<script[^>]*type="application/json"[^>]*>(.*?)</script>',
            r.text, re.DOTALL,
        )
        if not json_blocks:
            return []
        page_data = json.loads(json_blocks[0])
        urql_state = page_data.get("props", {}).get("pageProps", {}).get("urqlState", {})
        for v in urql_state.values():
            if not isinstance(v, dict):
                continue
            raw_data = v.get("data", "")
            if not isinstance(raw_data, str) or "advertSearch" not in raw_data:
                continue
            inner = json.loads(raw_data)
            edges = inner.get("advertSearch", {}).get("edges", [])
            if edges:
                return edges
    except Exception as e:
        log.error(f"Scrape error: {e}")
    return []


# Słowniki wartości ze STRONY ogłoszenia (wyniki wyszukiwania ich nie mają).
# Zebrane z żywych danych 22.08.2026.
NADWOZIE_OTOMOTO = {
    "sedan": "sedan", "limuzyna": "sedan",
    "kombi": "kombi", "kompakt": "kompakt", "coupe": "coupe", "coupé": "coupe",
    "kabriolet": "kabriolet", "suv": "suv", "minivan": "minivan",
    "auta małe": "male", "auta miejskie": "miejskie",
}
NAPED_OTOMOTO = {
    "na przednie koła": "fwd",
    "na tylne koła": "rwd",
}


def _mapuj_naped(v: str):
    v = (v or "").strip().lower()
    if not v:
        return None
    if v.startswith("4x4"):          # stały / dołączany automatycznie / ręcznie
        return "awd"
    return NAPED_OTOMOTO.get(v)


def pobierz_szczegoly(url: str) -> dict:
    """Dociąga ze STRONY ogłoszenia pola, których nie ma w wynikach wyszukiwania.

    Powód, zmierzony 22.08.2026: wyniki wyszukiwarki Otomoto niosą tylko
    make/model/rok/paliwo/skrzynia/przebieg/pojemność/moc. Nadwozia i napędu
    tam NIE MA, a filtry w URL-u ich nie pilnują — zapytanie o `bodywork_type
    =sedan` zwraca co do sztuki to samo, co bez filtra. Skutek: pierwsze
    „pasujące" BMW G20 okazało się Kombi na tylne koła, czyli Touring bez
    xDrive — dokładnie to, co miało odpaść. Bot puszczał je z adnotacją
    „nie wiem: nadwozie, napęd".

    Zwraca tylko to, co serwis podał; brak pola zostaje None („nie wiem"),
    zgodnie z zasadą, że brak danych to nie niezgodność. Nigdy nie rzuca —
    awaria dociągania ma degradować bota do stanu sprzed zmiany, nie zabijać.
    """
    out = {"body": None, "drive": None, "damaged": None, "version": None}
    try:
        r = scraper.get(url, timeout=25, headers=HEADERS)
        r.raise_for_status()
        bloki = re.findall(
            r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', r.text, re.DOTALL)
        if not bloki:
            return out
        advert = (json.loads(bloki[0]).get("props", {})
                  .get("pageProps", {}).get("advert") or {})
        pola = {p.get("key"): p.get("value") for p in (advert.get("details") or [])}
        if pola.get("body_type"):
            out["body"] = NADWOZIE_OTOMOTO.get(str(pola["body_type"]).strip().lower())
        out["drive"] = _mapuj_naped(pola.get("transmission"))
        if pola.get("damaged"):
            out["damaged"] = str(pola["damaged"]).strip().lower() in ("tak", "yes", "true")
        if pola.get("version"):
            out["version"] = str(pola["version"]).lower()
    except Exception as e:
        log.warning(f"Nie udało się dociągnąć szczegółów ({str(e)[:60]}): {url}")
    return out


def uzupelnij_ze_strony(listing: dict) -> dict:
    """Wpisuje dociągnięte pola do ogłoszenia. Nie nadpisuje wiedzy niewiedzą."""
    szcz = pobierz_szczegoly(listing["url"])
    for pole in ("body", "drive", "version"):
        if szcz.get(pole) is not None:
            listing[pole] = szcz[pole]
    if szcz.get("damaged") is not None:
        # Pole ze strony jest mocniejsze niż założenie z filtra w URL-u
        listing["damaged"] = szcz["damaged"]
        if szcz["damaged"]:
            listing.pop("szkoda_nieopisana", None)
    return listing


def fetch_listings_otomoto(search: dict, pages: int = 4) -> list[dict]:
    """Pobiera kilka stron wyników żeby mieć pulę do porównania cen."""
    results = []
    seen_ids = set()

    for page in range(1, pages + 1):
        sep = "&" if "?" in search["url"] else "?"
        url = f"{search['url']}{sep}page={page}"
        edges = _fetch_page(url)
        log.info(f"[{search['name']}] strona {page}: {len(edges)} edges")
        if not edges:
            break
        for edge in edges:
            node = edge.get("node", edge)
            ad = _parse_node(node)
            if ad and ad["id"] not in seen_ids:
                seen_ids.add(ad["id"])
                results.append(ad)

    # Otomoto ma własny filtr uszkodzonych (filter_enum_damaged=1) i on DZIAŁA:
    # z filtrem i bez niego dostajemy rozłączne zbiory ofert. To ważne, bo
    # wyniki wyszukiwarki nie zawierają pola o stanie, a słowa w tytule prawie
    # nigdy nie padają (0 na 32 w próbce) — na samych słowach ta połowa bota
    # milczała od 6 lipca. Skoro oferta przyszła z takiego zapytania, jest
    # uszkodzona; gdy treść tego nie potwierdza, mówimy o tym w wiadomości.
    if "filter_enum_damaged" in search["url"]:
        for ad in results:
            if not ad["damaged"]:
                ad["damaged"] = True
                ad["szkoda_nieopisana"] = True

    return results


# ---------------------------------------------------------------------------
# OLX scraper (REST API)
# ---------------------------------------------------------------------------

def _parse_olx_param(params: list, key: str):
    for p in params:
        if p.get("key") == key:
            v = p.get("value", {})
            if isinstance(v, dict):
                return v.get("key") or v.get("value") or v.get("label")
            return v
    return None


def _parse_olx_label(params: list, key: str) -> str:
    """Etykieta pola, np. „Seria 3". Potrzebna obok klucza, bo OLX trzyma dla
    BMW slugi węgierskie („3-as-sorozat") — po samym kluczu nie da się
    dopasować modelu do tego, co zwraca Otomoto."""
    for p in params:
        if p.get("key") == key:
            v = p.get("value", {})
            if isinstance(v, dict):
                return str(v.get("label") or "")
            return str(v or "")
    return ""


def fetch_listings_olx(search: dict) -> list[dict]:
    results = []
    try:
        # przez wspólne wejście z tracker.py — obsługuje przekaźnik Cloudflare,
        # bez którego serwerownia GitHuba dostaje od OLX-a 403 (od 10.08.2026)
        from urllib.parse import urlencode
        from olx import olx_get
        r = olx_get(OLX_API + "?" + urlencode(search["params"]), timeout=25)
        if r is None or r.status_code != 200:
            log.error(f"[{search['name']}] OLX API niedostepne "
                      f"(status {getattr(r, 'status_code', 'brak')})")
            return results
        ads = r.json().get("data", [])
        log.info(f"[{search['name']}] OLX API: {len(ads)} ogłoszeń")
        odrzucone = 0

        for ad in ads:
            params = ad.get("params", [])

            # Cena
            price_num = None
            price_str = "brak ceny"
            price_param = _parse_olx_param(params, "price")
            if isinstance(price_param, dict):
                price_num = int(price_param.get("value") or 0) or None
            elif price_param:
                try:
                    price_num = int(price_param)
                except (ValueError, TypeError):
                    pass
            # Fallback przez wartość w params
            if price_num is None:
                for p in params:
                    if p.get("key") == "price":
                        v = p.get("value", {})
                        if isinstance(v, dict) and v.get("value"):
                            price_num = int(v["value"])
            if price_num:
                price_str = f"{price_num:,} PLN".replace(",", " ")

            # Parametry — wszystko z pól strukturalnych, zero zgadywania z tytułu
            def liczba(klucz):
                try:
                    return int(re.sub(r"\D", "", str(_parse_olx_param(params, klucz) or ""))) or None
                except (ValueError, TypeError):
                    return None

            year = liczba("year")
            mileage_num = liczba("milage")
            engine_hp = liczba("enginepower")
            engine_cm3 = liczba("enginesize")
            model_key = str(_parse_olx_param(params, "model") or "").lower()
            model_label = _parse_olx_label(params, "model")
            fuel = str(_parse_olx_param(params, "petrol") or "").lower() or None
            gearbox = str(_parse_olx_param(params, "transmission") or "").lower() or None
            body = str(_parse_olx_param(params, "car_body") or "").lower() or None
            drive = NAPED_MAPA.get(str(_parse_olx_param(params, "drive") or "").lower())

            title_lower = ad.get("title", "").lower()
            desc_lower = ad.get("description", "")[:300].lower()

            # `condition` to pole wyboru w formularzu OLX-a — pewniejsze niż
            # słowa w opisie. Na słowa schodzimy tylko, gdy pola brak.
            stan = str(_parse_olx_param(params, "condition") or "").lower()
            if stan == "damaged":
                damaged = True
            elif stan == "notdamaged":
                damaged = False
            else:
                damaged = is_damaged(title_lower, desc_lower)

            loc = ad.get("location") or {}
            listing = {
                "id": f"olx_{ad['id']}",
                "title": ad.get("title", "").strip(),
                "url": ad.get("url", ""),
                "short_desc": ad.get("description", "")[:300],
                "city": loc.get("city", {}).get("name", ""),
                "region": loc.get("region", {}).get("name", "").lower(),
                "created_at": ad.get("created_time", ""),
                "price_num": price_num,
                "price_str": price_str,
                "mileage_num": mileage_num,
                "year": year,
                "engine_hp": engine_hp,
                "model_value": model_key,
                "version_value": "",
                "params": {},
                "model_key": model_key,
                "model_label": model_label,
                "fuel": fuel,
                "gearbox": gearbox,
                "drive": drive,
                "engine_cm3": engine_cm3,
                "body": body,
                "damaged": damaged,
                # lustro oferty z Otomoto — 47 z 51 wyników OLX-a to ten sam
                # samochód, który mamy już z drugiego kanału (patrz `main`)
                "external_url": ad.get("external_url") or "",
            }

            pasuje, braki = sprawdz_kryteria(listing, search["kryteria"])
            if not pasuje:
                odrzucone += 1
                continue
            listing["braki"] = braki
            results.append(listing)

        log.info(f"[{search['name']}] przeszło kryteria: {len(results)}, "
                 f"odrzucone: {odrzucone}")
    except Exception as e:
        log.error(f"OLX fetch error [{search['name']}]: {e}")
    return results


# ---------------------------------------------------------------------------
# Obserwowani wystawcy
#
# Pilnujemy KONKRETNEGO człowieka, nie modelu auta. Droga dojścia była kręta i
# warto wiedzieć czemu akurat tak (sprawdzone 21.08.2026):
#
#  * Otomoto nie pozwala wylistować ogłoszeń prywatnego sprzedawcy. Filtry
#    ?search[seller_id]=, /uzytkownik/<uuid> i ?search[city_id]= są po cichu
#    ignorowane — zwracają zwykłą listę wszystkich aut. Link „zobacz więcej
#    ofert tego sprzedawcy" dostają wyłącznie firmy. Skan całego województwa
#    odpada: wyniki nie są sortowane po dacie, a szukanej oferty nie było
#    w pierwszych 480.
#  * Na OLX ten sprzedawca NIE MA własnego konta. Jego ogłoszenia to lustra
#    ofert z Otomoto (`partner.code = otomoto_pl_form`) i wszystkie wiszą pod
#    technicznym kontem OLX-a o id 23063449 — wspólnym dla całej Polski.
#    Pilnowanie tego konta dałoby auta z Gdańska i Szczecina.
#  * Za to filtr miejscowości na OLX (`city_id`) działa dokładnie i daje
#    kilkadziesiąt ogłoszeń zamiast tysięcy.
#
# Stąd konstrukcja: pytamy OLX o miejscowość, a tożsamość wystawcy
# rozstrzygamy dopiero po `sellerId` ze strony Otomoto, do której prowadzi
# `external_url`. Nazwa kontaktowa NIE wystarcza — OLX pozwala ustawić inną
# przy każdym ogłoszeniu, a w tej samej wsi siedzą „Darek" i „Kuba", którzy
# są osobnymi sprzedawcami.
# ---------------------------------------------------------------------------

SEEN_WYSTAWCY_FILE = Path("seen_wystawcy.json")

WYSTAWCY = [
    {
        "nazwa": "Leszek — Oleśnica (staszowski)",
        "otomoto_seller_id": "17449440",
        # cała motoryzacja (5), nie same osobowe (84): ten sprzedawca wystawia
        # też dostawcze — dwa Renault Master wpadłyby w dziurę
        "olx_category_id": 5,
        "olx_city_id": 103125,      # Oleśnica, pow. staszowski, świętokrzyskie
        "imie_kontaktowe": "leszek",
    },
]


def load_seen_wystawcy() -> dict:
    if SEEN_WYSTAWCY_FILE.exists():
        return json.loads(SEEN_WYSTAWCY_FILE.read_text())
    return {}


def save_seen_wystawcy(seen: dict):
    SEEN_WYSTAWCY_FILE.write_text(json.dumps(seen, ensure_ascii=False, indent=2))


def otomoto_id_z_url(url: str) -> Optional[str]:
    """Token oferty Otomoto z adresu ('...-ID6HMNXq.html' → 'ID6HMNXq').

    Slug bywa różny dla tego samego auta (OLX skleja własny), więc porównujemy
    wyłącznie token — jedyną trwałą częścią adresu.

    Host sprawdzany celowo: adresy OLX-a mają token w tym samym kształcie
    ('...-CID5-ID1btpnU.html'), więc bez tego dwa różne serwisy mogłyby sobie
    nawzajem zjeść ogłoszenie przy zbiegu identyfikatorów."""
    if "otomoto.pl" not in (url or ""):
        return None
    m = re.search(r"-(ID[0-9A-Za-z]+)\.html", url)
    return m.group(1) if m else None


def otomoto_seller_id(url: str) -> Optional[str]:
    """Identyfikator wystawcy ze strony oferty Otomoto. None, gdy się nie da."""
    try:
        r = scraper.get(url, timeout=25, headers=HEADERS)
        if r.status_code != 200:
            return None
        blocks = re.findall(
            r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', r.text, re.DOTALL)
        if blocks:
            try:
                seller = (json.loads(blocks[0]).get("props", {}).get("pageProps", {})
                          .get("advert", {}).get("seller", {}))
                if seller.get("id"):
                    return str(seller["id"])
            except (ValueError, AttributeError):
                pass
        m = re.search(r'"sellerId":"(\d+)"', r.text)   # zapasowo, z danych śledzących
        return m.group(1) if m else None
    except Exception as e:
        log.error(f"Otomoto seller id [{url[:60]}]: {e}")
        return None


def sprawdz_wystawce(wystawca: dict, seen: dict) -> int:
    """Nowe ogłoszenia obserwowanego wystawcy. Zwraca liczbę wysłanych."""
    from urllib.parse import urlencode
    from olx import olx_get

    wyslane = 0
    params = {"category_id": wystawca["olx_category_id"],
              "city_id": wystawca["olx_city_id"], "limit": 50, "currency": "PLN"}
    r = olx_get(OLX_API + "?" + urlencode(params), timeout=25)
    if r is None or r.status_code != 200:
        log.error(f"[{wystawca['nazwa']}] OLX niedostępne "
                  f"(status {getattr(r, 'status_code', 'brak')})")
        return 0

    ogloszenia = r.json().get("data", [])
    nowe = [a for a in ogloszenia if f"w_{a['id']}" not in seen]
    log.info(f"[{wystawca['nazwa']}] w miejscowości: {len(ogloszenia)}, nowych: {len(nowe)}")

    for a in nowe:
        lid = f"w_{a['id']}"
        # Szczegóły pobieramy TYLKO dla nieznanych ofert — inaczej byłoby
        # kilkadziesiąt zapytań co pół godziny zamiast jednego.
        rd = olx_get(f"{OLX_API}{a['id']}/", timeout=20)
        if rd is None or rd.status_code != 200:
            seen[lid] = {}                    # np. 410: oferta już zdjęta
            continue
        d = rd.json().get("data", {})
        ext = d.get("external_url") or ""
        kontakt = str((d.get("contact") or {}).get("name") or "")

        pewnosc = None
        if "otomoto.pl" in ext:
            if otomoto_seller_id(ext) == wystawca["otomoto_seller_id"]:
                pewnosc = "potwierdzony"
        elif kontakt.lower() == wystawca["imie_kontaktowe"]:
            # Wystawione wprost na OLX, bez lustra z Otomoto — nie ma po czym
            # potwierdzić tożsamości, więc mówimy o tym wprost.
            pewnosc = "niepotwierdzony"

        if not pewnosc:
            seen[lid] = {}                    # ktoś inny z tej samej miejscowości
            continue

        # cena siedzi w `params`, nie w polu najwyższego poziomu — d["price"]
        # jest puste i dawało „brak ceny" przy każdej ofercie
        cena_str = (_parse_olx_label(d.get("params", []), "price")
                    or (d.get("price") or {}).get("displayValue") or "brak ceny")
        loc = (d.get("location") or {}).get("city", {}).get("name", "")
        dni = days_on_market(d.get("created_time", ""))
        naglowek = ("👤 <b>ŚLEDZONY WYSTAWCA</b>" if pewnosc == "potwierdzony"
                    else "👤 <b>ŚLEDZONY WYSTAWCA?</b>")
        uwaga = ("" if pewnosc == "potwierdzony" else
                 "\n❓ Zgadza się tylko imię kontaktowe — wystawione wprost na "
                 "OLX, więc nie da się potwierdzić po koncie Otomoto")
        send_telegram(
            f"{naglowek}\n\n"
            f"📌 <b>{d.get('title', '')}</b>\n"
            f"💰 {cena_str}\n"
            f"🧑 {kontakt}  📍 {loc}"
            f"{f'  🕐 {dni}d na rynku' if dni is not None else ''}{uwaga}\n"
            f"🔍 {wystawca['nazwa']}\n"
            f"🔗 {d.get('url', '')}"
            + (f"\n🔗 Otomoto: {ext}" if ext else "")
        )
        log.info(f"[{wystawca['nazwa']}] nowe ({pewnosc}): {d.get('title','')[:50]}")
        seen[lid] = {
            "title": d.get("title", ""),
            "url": d.get("url", ""),
            "otomoto_url": ext,
            "kontakt": kontakt,
            "pewnosc": pewnosc,
            "date": date.today().isoformat(),
        }
        wyslane += 1

    return wyslane


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

STAN_FILE = Path("otomoto_stan.json")
PUSTE_DO_ALARMU = 2      # bieg co 30 min, więc alarm po ~godzinie martwoty


def _stan(zmiana=None) -> dict:
    stan = {}
    if STAN_FILE.exists():
        try:
            stan = json.loads(STAN_FILE.read_text())
        except Exception:
            stan = {}
    if zmiana is not None:
        stan.update(zmiana)
        try:
            STAN_FILE.write_text(json.dumps(stan))
        except Exception as e:
            log.error(f"zapis stanu: {e}")
    return stan


def ocen_zdrowie(pobrano_otomoto: int, pobrano_olx: int):
    """Milczący bot wygląda dokładnie jak spokojny rynek — i to jest pułapka.

    Bot rowerowy stracił tak 11 dni danych. Tu też: `_fetch_page` łyka każdy
    błąd i zwraca pustą listę, więc blokada albo zmiana formatu JSON-a kończy
    się ciszą bez końca. Alarm dopiero po PUSTE_DO_ALARMU pustych biegach —
    jeden pusty przebieg to zwykle chwilowa wpadka — i tylko raz, plus jedno
    zdanie, gdy wróci. Bez żargonu: użytkownik nie jest techniczny.
    """
    try:
        stan = _stan()
        puste = stan.get("puste", 0)
        zgloszone = bool(stan.get("zgloszone"))
        if pobrano_otomoto or pobrano_olx:
            _stan({"puste": 0, "zgloszone": False})
            if zgloszone:
                send_telegram("✅ <b>OtomotoHawk — już działa.</b>")
                log.info("Powrót do normy — wysłano potwierdzenie")
            return
        puste += 1
        _stan({"puste": puste})
        log.error(f"Pusty przebieg ({puste}. z rzędu) — Otomoto 0, OLX 0")
        if puste >= PUSTE_DO_ALARMU and not zgloszone:
            _stan({"zgloszone": True})
            send_telegram(
                "🔕 <b>OtomotoHawk — nie widzę ogłoszeń</b>\n\n"
                "Od godziny ani Otomoto, ani OLX nie oddają żadnych aut. "
                "To może być blokada albo przebudowa strony.\n"
                "Próbuję dalej co pół godziny. Odezwę się, gdy wróci.")
    except Exception as e:
        log.error(f"ocen_zdrowie error: {e}")


def main():
    seen = load_seen()
    new_count = 0
    pobrano_otomoto = pobrano_olx = 0
    today = date.today().isoformat()

    for search in SEARCHES:
        listings = fetch_listings_otomoto(search)
        pobrano_otomoto += len(listings)
        log.info(f"[{search['name']}] sparsowano {len(listings)} ogłoszeń")

        log.info(f"  Pula do porównania: {len(listings)} ogłoszeń")

        # OLX mediana — raz na wyszukiwanie
        olx_price = fetch_olx_car_price(search["olx_query"])
        if olx_price:
            log.info(f"  OLX mediana: {olx_price:,} PLN")

        for listing in listings:
            lid = listing["id"]

            # Twarde kryteria na polach strukturalnych. Filtry w URL-u nie
            # wystarczają: /osobowe/audi/a5 dokłada „podobne oferty" i wracają
            # z niego A6 Limousine, Q5 czy A4 Avant.
            # Tanie sito na polach z wyszukiwarki — odsiewa większość ZANIM
            # wydamy żądanie na stronę ogłoszenia. Pełne sprawdzenie, już
            # z nadwoziem i napędem, jest niżej, po odsianiu znanych ofert.
            pasuje, _ = sprawdz_kryteria(listing, search["kryteria"])
            if not pasuje:
                seen[lid] = {}
                continue

            # Filtr województwa
            if not in_allowed_region(listing.get("region", "")):
                log.info(f"Pominięto (region {listing.get('region','?')}): {listing['title'][:45]}")
                seen[lid] = {}
                continue

            median_price = comparable_median(listing, listings)

            # Wykrywanie obniżki ceny (ogłoszenie znane, ale cena spadła)
            prev = seen.get(lid)
            price_drop_str = ""
            if prev and isinstance(prev, dict) and prev.get("price_num") and listing["price_num"]:
                drop = prev["price_num"] - listing["price_num"]
                if drop >= 500:
                    price_drop_str = f"\n📉 <b>OBNIŻKA o {drop:,} zł!</b> (było: {prev['price_num']:,} zł)".replace(",", " ")
                    log.info(f"Obniżka ceny o {drop} zł: {listing['title'][:50]}")
                    seen[lid]["price_num"] = listing["price_num"]
                else:
                    seen[lid]["price_num"] = listing["price_num"]
                    continue  # znane ogłoszenie, brak istotnej zmiany
            elif lid in seen:
                continue  # znane, brak danych cenowych do porównania

            # DOPIERO TERAZ strona ogłoszenia — dla nowych, po odsianiu znanych,
            # żeby jedno żądanie przypadało na kandydata, nie na cały rynek.
            # Stąd biorą się nadwozie i napęd, których wyszukiwarka nie oddaje.
            uzupelnij_ze_strony(listing)
            pasuje, braki = sprawdz_kryteria(listing, search["kryteria"])
            if not pasuje:
                log.info(f"Odrzucone po sprawdzeniu strony "
                         f"(nadwozie={listing.get('body')}, napęd={listing.get('drive')}): "
                         f"{listing['title'][:45]}")
                seen[lid] = {}
                continue
            if listing.get("szkoda_nieopisana"):
                braki.append("zakres szkody (Otomoto oznaczyło jako uszkodzone)")
            listing["braki"] = braki
            damaged = listing["damaged"]

            sc = score_listing(listing, median_price)
            rating = stars(sc)

            # % vs mediana podobnych aut
            discount_str = ""
            if median_price and listing["price_num"]:
                pct = (median_price - listing["price_num"]) / median_price * 100
                sign = "+" if pct > 0 else ""
                km_ref = listing.get("mileage_num")
                km_ref_str = f"{km_ref//1000}k km" if km_ref else "?"
                discount_str = f" ({sign}{pct:.1f}% vs {listing.get('year','?')}/{km_ref_str})"

            # Porównanie z OLX
            olx_str = ""
            if olx_price and listing["price_num"]:
                diff = olx_price - listing["price_num"]
                emoji = "🟢" if diff > 3000 else "🟡" if diff >= 0 else "🔴"
                olx_str = (
                    f"\n{emoji} OLX mediana: {olx_price:,} zł  (różnica: {diff:+,} zł)"
                ).replace(",", " ")

            # Szacunek naprawy
            repair = estimate_repair(listing["title"], listing["short_desc"])
            repair_str = ""
            if repair and listing["price_num"]:
                total = listing["price_num"] + repair
                repair_str = f"\n🔩 Szac. naprawa: ~{repair:,} zł  →  łącznie: ~{total:,} zł".replace(",", " ")

            # Czas na rynku
            days = days_on_market(listing.get("created_at", ""))
            days_str = f"  🕐 {days}d na rynku" if days is not None else ""

            year_str = f"📅 {listing['year']}" if listing.get("year") else "📅 ?"
            km_str = (
                f"🛣 {listing['mileage_num']:,} km".replace(",", " ")
                if listing.get("mileage_num") is not None
                else "🛣 brak przebiegu"
            )
            hp_str = f"  ⚡ {listing['engine_hp']} KM" if listing.get("engine_hp") else ""
            city_str = f"  📍 {listing['city']}" if listing.get("city") else ""
            damaged_str = "\n⚠️ <b>USZKODZONY / PO WYPADKU</b>" if damaged else ""

            msg = (
                f"🔧 <b>OtomotoHawk</b> {rating}\n\n"
                f"📌 <b>{listing['title']}</b>{damaged_str}{price_drop_str}\n"
                f"💰 {listing['price_str']}{discount_str}\n"
                f"{year_str}{hp_str}  {km_str}{city_str}{days_str}"
                f"{repair_str}{olx_str}{format_braki(listing.get('braki'))}\n"
                f"⭐ Score: {sc}/100\n"
                f"🔍 {search['name']}\n"
                f"🔗 {listing['url']}"
            )
            # ZAPIS PRZED WYSYŁKĄ. Bieg bywa ubijany w połowie (22.08 GitHub
            # skasował pięć biegów bota rowerowego pod rząd) — przy odwrotnej
            # kolejności każde takie ubicie oznaczałoby powtórzone wiadomości.
            # Teraz najgorszy przypadek to brak powiadomienia, nigdy duplikat.
            seen[listing["id"]] = {
                "title": listing["title"],
                "price_num": listing["price_num"],
                "mileage_num": listing["mileage_num"],
                "year": listing.get("year"),
                "url": listing["url"],
                "search": search["name"],
                "date": today,
                "score": sc,
                "median_podobnych": int(median_price) if median_price else None,
                "olx_median": olx_price,
            }
            save_seen(seen)
            send_telegram(msg)
            log.info(f"Nowe ogłoszenie (score {sc}): {listing['title']}")
            new_count += 1

    # -----------------------------------------------------------------------
    # OLX
    # -----------------------------------------------------------------------
    seen_olx = load_seen_olx()

    # Ten sam samochód wystawiony na Otomoto i przelany na OLX to dwie
    # wiadomości o jednym aucie — a lustrem jest 47 z 51 ofert OLX-a.
    # Kanały mają osobne pliki `seen`, więc dopiero tu da się je zestawić.
    # Pomijamy TYLKO te, których pierwowzór faktycznie znamy z Otomoto;
    # gdy oryginał nie wpadł w nasze wyszukiwania, OLX jest jedynym źródłem.
    znane_otomoto = {otomoto_id_z_url(v.get("url"))
                     for v in seen.values() if isinstance(v, dict) and v.get("url")}
    znane_otomoto.discard(None)

    for search in OLX_SEARCHES:
        listings = fetch_listings_olx(search)
        pobrano_olx += len(listings)

        for listing in listings:
            lid = listing["id"]

            # Filtr regionu
            if not in_allowed_region(listing.get("region", "")):
                seen_olx[lid] = {}
                continue

            lustro = otomoto_id_z_url(listing.get("external_url"))
            if lustro and lustro in znane_otomoto:
                log.info(f"Pominięto (lustro oferty z Otomoto): {listing['title'][:45]}")
                seen_olx[lid] = {}
                continue

            # ustalone już w fetch_listings_olx — z pola `condition`, nie z tytułu
            damaged = listing["damaged"]

            # Wykrywanie obniżki ceny
            prev_olx = seen_olx.get(lid)
            price_drop_str = ""
            if prev_olx and isinstance(prev_olx, dict) and prev_olx.get("price_num") and listing["price_num"]:
                drop = prev_olx["price_num"] - listing["price_num"]
                if drop >= 500:
                    price_drop_str = f"\n📉 <b>OBNIŻKA o {drop:,} zł!</b>".replace(",", " ")
                    seen_olx[lid]["price_num"] = listing["price_num"]
                else:
                    seen_olx[lid]["price_num"] = listing["price_num"]
                    continue
            elif lid in seen_olx:
                continue

            sc = score_listing(listing, None)
            rating = stars(sc)

            year_str = f"📅 {listing['year']}" if listing.get("year") else "📅 ?"
            km_str = (
                f"🛣 {listing['mileage_num']:,} km".replace(",", " ")
                if listing.get("mileage_num") is not None
                else "🛣 brak przebiegu"
            )
            hp_str = f"  ⚡ {listing['engine_hp']} KM" if listing.get("engine_hp") else ""
            city_str = f"  📍 {listing['city']}" if listing.get("city") else ""
            damaged_str = "\n⚠️ <b>USZKODZONY / PO WYPADKU</b>" if damaged else ""

            repair = estimate_repair(listing["title"], listing["short_desc"])
            repair_str = ""
            if repair and listing["price_num"]:
                total = listing["price_num"] + repair
                repair_str = f"\n🔩 Szac. naprawa: ~{repair:,} zł  →  łącznie: ~{total:,} zł".replace(",", " ")

            days = days_on_market(listing.get("created_at", ""))
            days_str = f"  🕐 {days}d na rynku" if days is not None else ""

            msg = (
                f"🔧 <b>OLX</b> {rating}\n\n"
                f"📌 <b>{listing['title']}</b>{damaged_str}{price_drop_str}\n"
                f"💰 {listing['price_str']}\n"
                f"{year_str}{hp_str}  {km_str}{city_str}{days_str}"
                f"{repair_str}{format_braki(listing.get('braki'))}\n"
                f"⭐ Score: {sc}/100\n"
                f"🔍 {search['name']}\n"
                f"🔗 {listing['url']}"
            )
            seen_olx[lid] = {
                "title": listing["title"],
                "price_num": listing["price_num"],
                "mileage_num": listing["mileage_num"],
                "year": listing.get("year"),
                "url": listing["url"],
                "search": search["name"],
                "date": today,
                "score": sc,
            }
            save_seen_olx(seen_olx)          # zapis PRZED wysyłką — patrz wyżej
            send_telegram(msg)
            log.info(f"OLX nowe (score {sc}): {listing['title']}")
            new_count += 1

    # -----------------------------------------------------------------------
    # Obserwowani wystawcy — niezależne od kryteriów modelowych powyżej.
    # W try, bo to dodatek: jego awaria nie może zabrać głównego przebiegu
    # ani zablokować zapisu plików `seen`.
    # -----------------------------------------------------------------------
    seen_wystawcy = load_seen_wystawcy()
    try:
        for wystawca in WYSTAWCY:
            new_count += sprawdz_wystawce(wystawca, seen_wystawcy)
    except Exception as e:
        log.error(f"Obserwacja wystawców przerwana: {e}")
    save_seen_wystawcy(seen_wystawcy)

    if new_count == 0:
        log.info("Brak nowych ogłoszeń.")

    save_seen(seen)
    save_seen_olx(seen_olx)
    ocen_zdrowie(pobrano_otomoto, pobrano_olx)


if __name__ == "__main__":
    main()
