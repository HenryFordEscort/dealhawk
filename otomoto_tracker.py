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
# Filtry URL (kodowane):
#   filter_enum_fuel_type=diesel
#   filter_enum_gearbox=automatic
#   filter_enum_drive=awd             (quattro / 4x4)
#   filter_float_year:from / :to
#   filter_float_engine_capacity:from / :to  (w cm3)
# ---------------------------------------------------------------------------
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
        ),
        "require_model_contains": "a5-sportback",
        "only_damaged": True,
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
            "&search%5Bfilter_enum_bodywork_type%5D=sedan"
        ),
        "require_model_contains": "a4-limousine",
        "only_damaged": True,
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
            "&search%5Bfilter_enum_bodywork_type%5D=sedan"
        ),
        "require_model_contains": None,
        "only_damaged": True,
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
            "&search%5Bfilter_enum_bodywork_type%5D=coupe"
        ),
        "require_model_contains": None,
        "only_damaged": True,
        "olx_query": "bmw seria 4 gran coupe g26 diesel xdrive",
    },
]

# ---------------------------------------------------------------------------
# OLX — wyszukiwania przez API (category_id=84 = Samochody osobowe)
# filter_enum_condition[0]=damaged  →  tylko uszkodzone
# filter_enum_petrol[0]=diesel
# filter_enum_gearbox[0]=automatic
# filter_float_year[from/to]
# ---------------------------------------------------------------------------
OLX_API = "https://www.olx.pl/api/v1/offers/"
OLX_SEARCHES = [
    {
        "name": "OLX Audi A5 Sportback 2.0 TDI quattro 2015-2019",
        "params": {"category_id": 84, "limit": 50, "currency": "PLN",
                   "query": "audi a5 sportback uszkodzony",
                   "filter_enum_condition": "damaged", "filter_enum_petrol": "diesel"},
        "year_from": 2015, "year_to": 2019,
        "require_model_key": "a5-sportback",
        # wszystkie słowa muszą być w tytule (lower)
        "title_must_contain_any": [["a5"], ["sportback"]],
        "title_must_not_contain": ["a3", "a4", "a6", "a7", "a8", "q3", "q5", "q7"],
    },
    {
        "name": "OLX Audi A4 Sedan 2.0 TDI quattro 2015-2019",
        "params": {"category_id": 84, "limit": 50, "currency": "PLN",
                   "query": "audi a4 sedan uszkodzony",
                   "filter_enum_condition": "damaged", "filter_enum_petrol": "diesel"},
        "year_from": 2015, "year_to": 2019,
        "require_model_key": None,
        "title_must_contain_any": [["a4"], ["sedan", "limuzyna", "limousine"]],
        "title_must_not_contain": ["a3", "a5", "a6", "a7", "a8", "allroad", "avant", "q3", "q5"],
    },
    {
        "name": "OLX BMW G20 Seria 3 320d xDrive 2019-2021",
        "params": {"category_id": 84, "limit": 50, "currency": "PLN",
                   "query": "bmw 320d xdrive uszkodzony",
                   "filter_enum_condition": "damaged", "filter_enum_petrol": "diesel"},
        "year_from": 2019, "year_to": 2021,
        "require_model_key": None,
        "title_must_contain_any": [["320", "seria 3", "serie 3", "3 series", "g20"]],
        "title_must_not_contain": ["x3", "x4", "x5", "gran coupe", "touring", "sedan m3"],
    },
    {
        "name": "OLX BMW G26 Seria 4 Gran Coupe 420d xDrive 2021-2023",
        "params": {"category_id": 84, "limit": 50, "currency": "PLN",
                   "query": "bmw 420d gran coupe uszkodzony",
                   "filter_enum_condition": "damaged", "filter_enum_petrol": "diesel"},
        "year_from": 2021, "year_to": 2023,
        "require_model_key": None,
        "title_must_contain_any": [["420", "seria 4", "serie 4", "4 series", "g26", "gran coupe"]],
        "title_must_not_contain": ["x4", "x5", "x6", "coupe 430", "m4", "m440"],
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
        slug = re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-")
        url = f"https://www.olx.pl/motoryzacja/samochody/q-{slug}/"
        r = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "pl-PL"},
            timeout=15,
        )
        prices = re.findall(r'"price":(\d+),"url":"https://www\.olx\.pl', r.text)
        nums = [int(p) for p in prices if 3000 < int(p) < 300000]
        if nums:
            return int(statistics.median(nums))
    except Exception as e:
        log.error(f"OLX fetch error: {e}")
    return None


def is_damaged(title: str, description: str = "") -> bool:
    combined = (title + " " + description).lower()
    return any(kw in combined for kw in DAMAGE_KEYWORDS)


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


def fetch_listings_olx(search: dict) -> list[dict]:
    results = []
    try:
        r = requests.get(
            OLX_API,
            params=search["params"],
            headers={"Accept-Language": "pl-PL", "User-Agent": "Mozilla/5.0", "Accept": "application/json"},
            timeout=20,
        )
        r.raise_for_status()
        ads = r.json().get("data", [])
        year_from = search.get("year_from")
        year_to = search.get("year_to")
        log.info(f"[{search['name']}] OLX API: {len(ads)} ogłoszeń")

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

            # Parametry
            year = None
            mileage_num = None
            engine_hp = None
            model_key = ""
            try:
                year = int(_parse_olx_param(params, "year") or 0) or None
            except (ValueError, TypeError):
                pass
            try:
                mileage_num = int(re.sub(r"\D", "", str(_parse_olx_param(params, "milage") or ""))) or None
            except (ValueError, TypeError):
                pass
            try:
                engine_hp = int(re.sub(r"\D", "", str(_parse_olx_param(params, "enginepower") or ""))) or None
            except (ValueError, TypeError):
                pass
            model_key = str(_parse_olx_param(params, "model") or "").lower()

            # Filtr roku (API nie obsługuje — robimy tutaj)
            if year_from and year and year < year_from:
                continue
            if year_to and year and year > year_to:
                continue

            # Filtr modelu (jeśli wymagany)
            required = search.get("require_model_key")
            if required and required not in model_key:
                continue

            # Filtr tytułu — wymagane grupy słów
            title_lower = ad.get("title", "").lower()
            desc_lower = ad.get("description", "")[:300].lower()
            title_desc = title_lower + " " + desc_lower
            must_groups = search.get("title_must_contain_any", [])
            if must_groups and not all(
                any(kw in title_desc for kw in group) for group in must_groups
            ):
                continue
            blacklist = search.get("title_must_not_contain", [])
            if any(kw in title_lower for kw in blacklist):
                continue

            # Wymuszamy uszkodzone — API filter_enum_condition często ignorowane
            if not is_damaged(title_lower, desc_lower):
                continue

            loc = ad.get("location") or {}
            results.append({
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
            })
    except Exception as e:
        log.error(f"OLX fetch error [{search['name']}]: {e}")
    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    seen = load_seen()
    new_count = 0
    today = date.today().isoformat()

    for search in SEARCHES:
        listings = fetch_listings_otomoto(search)
        log.info(f"[{search['name']}] sparsowano {len(listings)} ogłoszeń")

        log.info(f"  Pula do porównania: {len(listings)} ogłoszeń")

        # OLX mediana — raz na wyszukiwanie
        olx_price = fetch_olx_car_price(search["olx_query"])
        if olx_price:
            log.info(f"  OLX mediana: {olx_price:,} PLN")

        for listing in listings:
            lid = listing["id"]

            # Filtr modelu (np. a5-sportback, żeby odrzucić a5-coupe / a4 / a6)
            required_model = search.get("require_model_contains")
            if required_model and required_model not in listing["model_value"]:
                seen[lid] = {}
                continue

            damaged = is_damaged(listing["title"], listing["short_desc"])

            if search.get("only_damaged") and not damaged:
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
                f"{repair_str}{olx_str}\n"
                f"⭐ Score: {sc}/100\n"
                f"🔍 {search['name']}\n"
                f"🔗 {listing['url']}"
            )
            send_telegram(msg)
            log.info(f"Nowe ogłoszenie (score {sc}): {listing['title']}")

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
            new_count += 1

    # -----------------------------------------------------------------------
    # OLX
    # -----------------------------------------------------------------------
    seen_olx = load_seen_olx()

    for search in OLX_SEARCHES:
        listings = fetch_listings_olx(search)

        for listing in listings:
            lid = listing["id"]

            # Filtr regionu
            if not in_allowed_region(listing.get("region", "")):
                seen_olx[lid] = {}
                continue

            # OLX API już filtruje condition=damaged, ale sprawdzamy też tytuł
            damaged = is_damaged(listing["title"], listing["short_desc"])

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
                f"{repair_str}\n"
                f"⭐ Score: {sc}/100\n"
                f"🔍 {search['name']}\n"
                f"🔗 {listing['url']}"
            )
            send_telegram(msg)
            log.info(f"OLX nowe (score {sc}): {listing['title']}")

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
            new_count += 1

    if new_count == 0:
        log.info("Brak nowych ogłoszeń.")

    save_seen(seen)
    save_seen_olx(seen_olx)


if __name__ == "__main__":
    main()
