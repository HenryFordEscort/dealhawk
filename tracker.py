import re
import os
import json
import logging
import cloudscraper
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

# Słowa które oznaczają uszkodzony/niepełny rower — ignorujemy takie ogłoszenia
SKIP_KEYWORDS = [
    "defekt", "bastler", "ersatzteile", "ersatzteil", "rahmen only",
    "schlachtfest", "unfall", "unfallschaden", "wasserschaden",
    "ohne motor", "ohne akku", "rahmen", "motor defekt", "akku defekt",
]

MAX_MILEAGE = 3000

def is_junk(title: str) -> bool:
    t = title.lower()
    return any(kw in t for kw in SKIP_KEYWORDS)

def is_too_worn(mileage: str) -> bool:
    if mileage == "brak danych":
        return False
    m = re.search(r'[\d.,]+', mileage)
    if not m:
        return False
    km = int(m.group().replace(".", "").replace(",", ""))
    return km > MAX_MILEAGE

# Kleinanzeigen URL z filtrem ceny: /s-preis:MIN:MAX/zapytanie/k0
def url(query):
    slug = query.replace(" ", "-")
    return f"https://www.kleinanzeigen.de/s-preis:{MIN_PRICE}:{MAX_PRICE}/{slug}/k0"

SEARCHES = [
    # --- Ogólne terminy na fully / e-mtb ---
    {"name": "e-bike fully",          "url": url("e-bike-fully")},
    {"name": "ebike fully",           "url": url("ebike-fully")},
    {"name": "elektro fully",         "url": url("elektro-fully")},
    {"name": "e-mtb fully",           "url": url("e-mtb-fully")},
    {"name": "emtb",                  "url": url("emtb")},
    {"name": "e-mountainbike fully",  "url": url("e-mountainbike-fully")},
    {"name": "pedelec fully",         "url": url("pedelec-fully")},
    {"name": "elektrofahrrad fully",  "url": url("elektrofahrrad-fully")},
    # --- Marki ---
    {"name": "Cube Stereo Hybrid",    "url": url("cube-stereo-hybrid")},
    {"name": "Cube Stereo E",         "url": url("cube-stereo-e")},
    {"name": "Trek Rail",             "url": url("trek-rail")},
    {"name": "Trek Powerfly FS",      "url": url("trek-powerfly-fs")},
    {"name": "KTM Macina Lycan",      "url": url("ktm-macina-lycan")},
    {"name": "KTM Macina Kapoho",     "url": url("ktm-macina-kapoho")},
    {"name": "KTM Macina fully",      "url": url("ktm-macina-fully")},
    {"name": "Scott Strike E-Ride",   "url": url("scott-strike-e-ride")},
    {"name": "Scott Patron",          "url": url("scott-patron")},
    {"name": "Scott Genius E-Ride",   "url": url("scott-genius-e-ride")},
    {"name": "Specialized Levo",      "url": url("specialized-levo")},
    {"name": "Specialized Turbo Levo","url": url("specialized-turbo-levo")},
]

SEEN_FILE = Path("seen.json")
scraper = cloudscraper.create_scraper()


def load_seen() -> set:
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text()))
    return set()


def save_seen(seen: set):
    SEEN_FILE.write_text(json.dumps(list(seen)))


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


def fetch_mileage(url: str) -> str:
    try:
        r = scraper.get(url, timeout=15)
        r.raise_for_status()
        html = r.text

        # Atrybuty ogłoszenia (np. "1.200 km")
        attr = re.search(
            r'(?:Kilometerstand|Laufleistung|km-Stand)[^\d]*(\d[\d\s.,]*)\s*km',
            html, re.IGNORECASE
        )
        if attr:
            return attr.group(1).strip() + " km"

        # Opis tekstowy — szukaj wzorców typu "1200 km", "1.200km", "ca. 500 km"
        desc = re.search(
            r'(?:ca\.?\s*)?(\d[\d.,]*)\s*km\b',
            html, re.IGNORECASE
        )
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
            title_clean = title.strip()
            if is_junk(title_clean):
                log.info(f"Pominięto (śmieć): {title_clean[:60]}")
                continue
            results.append({
                "id": ad_id,
                "title": title_clean,
                "price": prices[i].strip() if i < len(prices) else "brak ceny",
                "url": f"https://www.kleinanzeigen.de{href}",
            })

    except Exception as e:
        log.error(f"Scrape error [{search['name']}]: {e}")
    return results


def main():
    seen = load_seen()
    new_count = 0

    for search in SEARCHES:
        listings = fetch_listings(search)
        log.info(f"[{search['name']}] znaleziono {len(listings)} ogłoszeń")

        for listing in listings:
            if listing["id"] in seen:
                continue

            seen.add(listing["id"])
            new_count += 1

            mileage = fetch_mileage(listing["url"])

            if is_too_worn(mileage):
                log.info(f"Pominięto (za duży przebieg {mileage}): {listing['title'][:50]}")
                continue

            msg = (
                f"🦅 <b>DealHawk</b> — nowe ogłoszenie!\n\n"
                f"📌 <b>{listing['title']}</b>\n"
                f"💰 {listing['price']}\n"
                f"🚵 {mileage}\n"
                f"🔍 {search['name']}\n"
                f"🔗 {listing['url']}"
            )
            send_telegram(msg)
            log.info(f"Nowe: {listing['title']}")

    if new_count == 0:
        log.info("Brak nowych ogłoszeń.")

    save_seen(seen)


if __name__ == "__main__":
    main()
