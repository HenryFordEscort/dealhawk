import re
import os
import json
import logging
import statistics
import cloudscraper
from datetime import date
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

MIN_PRICE = 800
MAX_PRICE = 2500
MAX_MILEAGE = 3000

SKIP_KEYWORDS = [
    "defekt", "bastler", "ersatzteile", "ersatzteil", "rahmen only",
    "schlachtfest", "unfall", "unfallschaden", "wasserschaden",
    "ohne motor", "ohne akku", "motor defekt", "akku defekt",
    # nie-fully / miejskie
    "hardtail", " ht ", "trekking", "city bike", "citybike",
    "lastenrad", "lastenfahrrad", "cargo", "faltrad", "klapprad",
    "tiefeinsteiger", "tiefeinstieg", "cityrad", "urban", "comfort",
    "cruiser", "touring", "cross", "gravel",
    # same ramy
    "rahmen", "frameset", "frame only", "nur rahmen",
]

# Słowa które potwierdzają że to fully (wymagane dla ogólnych wyszukiwań)
FULLY_KEYWORDS = [
    "fully", "full suspension", "full-suspension", " fs ", "fs,", "fs)",
    "stereo hybrid", "levo", "rail", "powerfly", "strike", "patron",
    "genius", "macina lycan", "macina kapoho", "spectral", "torque",
    "nduro", "allmtn", "e-asx", "wild fs", "eone-sixty",
]

def is_fully(title: str) -> bool:
    t = title.lower()
    return any(kw in t for kw in FULLY_KEYWORDS)

# Marki z wysokim resale value w Polsce
PREMIUM_BRANDS = ["cube", "trek", "specialized", "scott", "ktm"]

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


def fetch_olx_price(query: str):
    try:
        slug = query.lower().replace(" ", "-")
        url = f"https://www.olx.pl/sport-hobby/rowery/q-{slug}/"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "pl-PL"}, timeout=15)
        prices = re.findall(r'"price":(\d+),"url":"https://www\.olx\.pl', r.text)
        nums = [int(p) for p in prices if 500 < int(p) < 80000]
        if nums:
            return int(statistics.median(nums))
    except Exception as e:
        log.error(f"OLX fetch error: {e}")
    return None


def mileage_factor(km) -> float:
    """Korekta wartości roweru względem przebiegu vs mediany rynkowej (~1500km)."""
    if km is None:
        return 1.0   # brak danych = zakładamy średni stan
    if km < 300:     return 1.15  # prawie nowy +15%
    if km < 800:     return 1.08  # bardzo mało używany +8%
    if km < 1500:    return 1.03  # mało używany +3%
    if km < 2500:    return 0.95  # średni przebieg -5%
    return          0.85          # duży przebieg -15%


def calc_profit(price_de_eur: int, price_pl_pln: int, km=None) -> int:
    kurs = get_eur_pln()
    koszt_de = price_de_eur * kurs
    adjusted_pl = price_pl_pln * mileage_factor(km)
    return int(adjusted_pl - koszt_de - TRANSPORT_PLN)


def load_seen() -> dict:
    if SEEN_FILE.exists():
        data = json.loads(SEEN_FILE.read_text())
        # migracja ze starego formatu (lista ID) do nowego (dict)
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
    t = title.lower()
    return any(kw in t for kw in SKIP_KEYWORDS)


def is_too_worn(mileage_num) -> bool:
    if mileage_num is None:
        return False
    return mileage_num > MAX_MILEAGE


def fetch_mileage(url: str) -> str:
    try:
        r = scraper.get(url, timeout=15)
        r.raise_for_status()
        html = r.text

        attr = re.search(
            r'(?:Kilometerstand|Laufleistung|km-Stand)[^\d]*(\d[\d\s.,]*)\s*km',
            html, re.IGNORECASE
        )
        if attr:
            return attr.group(1).strip() + " km"

        desc = re.search(r'(?:ca\.?\s*)?(\d[\d.,]*)\s*km\b', html, re.IGNORECASE)
        if desc:
            km = desc.group(1).replace(".", "").replace(",", "")
            if km.isdigit() and 10 <= int(km) <= 50000:
                return desc.group(1) + " km"

    except Exception as e:
        log.error(f"Mileage fetch error: {e}")
    return "brak danych"


def fetch_listings(search: dict) -> list[dict]:
    results = []
    seen_ids = set()
    try:
        r = scraper.get(search["url"], timeout=15)
        r.raise_for_status()
        html = r.text

        ids = re.findall(r'data-adid="(\d+)"', html)
        title_href_pairs = re.findall(
            r'href="(/s-anzeige/[^"]+)">([^<\n]+)</a>', html
        )
        prices_raw = re.findall(
            r'"adlist--item--price">([^<]+)<', html
        ) or re.findall(
            r'class="aditem-main--middle--price-shipping--price">\s*([^\n<]+)', html
        )
        prices = [p.strip() for p in prices_raw if p.strip()]

        for i, ad_id in enumerate(ids):
            if ad_id in seen_ids:
                continue
            seen_ids.add(ad_id)
            if i < len(title_href_pairs):
                href, title = title_href_pairs[i]
            else:
                href, title = f"/s-anzeige/{ad_id}", "Brak tytułu"
            price_str = prices[i].strip() if i < len(prices) else "brak ceny"
            results.append({
                "id": ad_id,
                "title": title.strip(),
                "price": price_str,
                "price_num": parse_price(price_str),
                "url": f"https://www.kleinanzeigen.de{href}",
            })

    except Exception as e:
        log.error(f"Scrape error [{search['name']}]: {e}")
    return results


def stars(score: int) -> str:
    if score >= 80:
        return "🔥🔥🔥"
    if score >= 60:
        return "🔥🔥"
    if score >= 40:
        return "🔥"
    return ""


def main():
    seen = load_seen()
    new_count = 0
    today = date.today().isoformat()

    for search in SEARCHES:
        listings = fetch_listings(search)
        log.info(f"[{search['name']}] znaleziono {len(listings)} ogłoszeń")

        # mediana ceny z tego wyszukiwania do scoringu
        prices_in_search = [l["price_num"] for l in listings if l["price_num"]]
        median_price = statistics.median(prices_in_search) if prices_in_search else None

        for listing in listings:
            if listing["id"] in seen:
                continue

            if is_junk(listing["title"]):
                log.info(f"Pominięto (śmieć): {listing['title'][:50]}")
                seen[listing["id"]] = {}
                continue

            if not is_fully(listing["title"]):
                log.info(f"Pominięto (nie fully): {listing['title'][:50]}")
                seen[listing["id"]] = {}
                continue

            mileage = fetch_mileage(listing["url"])
            mileage_num = parse_mileage(mileage)

            if is_too_worn(mileage_num):
                log.info(f"Pominięto (za duży przebieg {mileage}): {listing['title'][:50]}")
                seen[listing["id"]] = {}
                continue

            listing["mileage"] = mileage
            listing["mileage_num"] = mileage_num
            sc = score_listing(listing, median_price)

            # Szacowany zysk z odsprzedazy w Polsce
            olx_price = fetch_olx_price(search["name"])
            profit = calc_profit(listing["price_num"], olx_price, mileage_num) if listing["price_num"] and olx_price else None

            seen[listing["id"]] = {
                "title": listing["title"],
                "price": listing["price"],
                "price_num": listing["price_num"],
                "mileage": mileage,
                "mileage_num": mileage_num,
                "url": listing["url"],
                "search": search["name"],
                "date": today,
                "score": sc,
                "profit": profit,
                "olx_median": olx_price,
            }

            new_count += 1
            rating = stars(sc)

            discount_str = ""
            if median_price and listing["price_num"]:
                pct = int((median_price - listing["price_num"]) / median_price * 100)
                discount_str = f" ({pct:+d}% vs DE)"

            profit_str = ""
            if profit is not None:
                emoji = "🟢" if profit > 500 else "🟡" if profit > 0 else "🔴"
                profit_str = f"\n{emoji} Zysk PL: ~{profit:+,.0f} zł (OLX mediana: {olx_price:,} zł, transport osobno)"

            msg = (
                f"🦅 <b>DealHawk</b> {rating}\n\n"
                f"📌 <b>{listing['title']}</b>\n"
                f"💰 {listing['price']}{discount_str}\n"
                f"🚵 {mileage}\n"
                f"⭐ Score: {sc}/100"
                f"{profit_str}\n"
                f"🔍 {search['name']}\n"
                f"🔗 {listing['url']}"
            )
            send_telegram(msg)
            log.info(f"Nowe (score {sc}): {listing['title']}")

    if new_count == 0:
        log.info("Brak nowych ogłoszeń.")

    save_seen(seen)


if __name__ == "__main__":
    main()
