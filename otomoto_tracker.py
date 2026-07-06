import re
import os
import json
import logging
import statistics
from datetime import date
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
        "name": "BMW Seria 4 Gran Coupe 2.0d xDrive AT 2015-2023",
        "url": (
            "https://www.otomoto.pl/osobowe/bmw/seria-4"
            "?search%5Bfilter_enum_fuel_type%5D=diesel"
            "&search%5Bfilter_enum_gearbox%5D=automatic"
            "&search%5Bfilter_enum_drive%5D=awd"
            "&search%5Bfilter_float_year%3Afrom%5D=2015"
            "&search%5Bfilter_float_year%3Ato%5D=2023"
            "&search%5Bfilter_float_engine_capacity%3Afrom%5D=1900"
            "&search%5Bfilter_float_engine_capacity%3Ato%5D=2100"
        ),
        "require_model_contains": None,
        "only_damaged": True,
        "olx_query": "bmw seria 4 gran coupe diesel xdrive",
    },
]

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
    location_city = (node.get("location") or {}).get("city", {}).get("name", "")

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
            if listing["id"] in seen:
                continue

                # Filtr modelu (np. a5-sportback, żeby odrzucić a5-coupe / a4 / a6)
            required_model = search.get("require_model_contains")
            if required_model and required_model not in listing["model_value"]:
                log.info(f"Pominięto (model {listing['model_value']}): {listing['title'][:55]}")
                seen[listing["id"]] = {}
                continue

            damaged = is_damaged(listing["title"], listing["short_desc"])

            if search.get("only_damaged") and not damaged:
                log.info(f"Pominięto (sprawny): {listing['title'][:55]}")
                seen[listing["id"]] = {}
                continue

            median_price = comparable_median(listing, listings)
            sc = score_listing(listing, median_price)
            rating = stars(sc)

            # % poniżej/powyżej mediany podobnych aut
            discount_str = ""
            if median_price and listing["price_num"]:
                pct = (median_price - listing["price_num"]) / median_price * 100
                sign = "+" if pct > 0 else ""
                year = listing.get("year", "?")
                km_ref = listing.get("mileage_num")
                km_ref_str = f"{km_ref//1000}k km" if km_ref else "?"
                discount_str = f" ({sign}{pct:.1f}% vs podobne {year}/{km_ref_str})"

            # Porównanie z OLX
            olx_str = ""
            if olx_price and listing["price_num"]:
                diff = olx_price - listing["price_num"]
                emoji = "🟢" if diff > 3000 else "🟡" if diff >= 0 else "🔴"
                olx_str = (
                    f"\n{emoji} OLX mediana: {olx_price:,} zł"
                    f"  (różnica: {diff:+,} zł)"
                ).replace(",", " ")

            year_str = f"📅 {listing['year']}" if listing.get("year") else "📅 ?"
            km_str = (
                f"🛣 {listing['mileage_num']:,} km".replace(",", " ")
                if listing.get("mileage_num") is not None
                else "🛣 brak przebiegu"
            )
            hp_str = f"  ⚡ {listing['engine_hp']} KM" if listing.get("engine_hp") else ""
            city_str = f"  📍 {listing['city']}" if listing.get("city") else ""

            damaged_str = "\n⚠️ <b>USZKODZONY / PO WYPADKU</b>" if damaged else ""
            car_emoji = "🔧" if damaged else "🚗"

            msg = (
                f"{car_emoji} <b>OtomotoHawk</b> {rating}\n\n"
                f"📌 <b>{listing['title']}</b>{damaged_str}\n"
                f"💰 {listing['price_str']}{discount_str}\n"
                f"{year_str}{hp_str}  {km_str}{city_str}\n"
                f"⭐ Score: {sc}/100"
                f"{olx_str}\n"
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

    if new_count == 0:
        log.info("Brak nowych ogłoszeń.")

    save_seen(seen)


if __name__ == "__main__":
    main()
