import os
import json
import logging
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
SEEN_FILE = Path("seen.json")
PIN_FILE = Path("pinned_summary.json")


def send_telegram(text: str):
    """Wysyła wiadomość, zwraca message_id."""
    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        r = requests.post(api_url, json=payload, timeout=10)
        r.raise_for_status()
        return r.json()["result"]["message_id"]
    except Exception as e:
        log.error(f"Telegram sendMessage error: {e}")
    return None


def pin_message(message_id: int):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/pinChatMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "message_id": message_id, "disable_notification": True},
            timeout=10,
        )
        r.raise_for_status()
        log.info(f"Przypięto wiadomość {message_id}")
    except Exception as e:
        log.error(f"Telegram pinChatMessage error: {e}")


def unpin_message(message_id: int):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/unpinChatMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "message_id": message_id},
            timeout=10,
        )
        r.raise_for_status()
        log.info(f"Odpięto wiadomość {message_id}")
    except Exception as e:
        log.error(f"Telegram unpinChatMessage error: {e}")


def load_pinned():
    if PIN_FILE.exists():
        try:
            return json.loads(PIN_FILE.read_text()).get("message_id")
        except Exception:
            pass
    return None


def save_pinned(message_id: int):
    PIN_FILE.write_text(json.dumps({"message_id": message_id}))


def update_olx_watch(queries):
    """Raz dziennie: śledzi oferty OLX per model. Oferta która znikła w <=14 dni
    = realna cena transakcyjna (popytu). Mediana takich cen > mediana cen ofertowych."""
    import statistics
    from tracker import fetch_olx_offers, load_olx_watch, OLX_WATCH_FILE, SOLD_FAST_DAYS

    watch = load_olx_watch()
    today = date.today()
    all_queries = set(queries) | set(watch.keys())

    for q in all_queries:
        try:
            current = fetch_olx_offers(q)
        except Exception as e:
            log.error(f"OLX watch fetch [{q}]: {e}")
            continue

        entry = watch.setdefault(q, {"offers": {}, "sold_fast": [], "demand_median": None, "updated": None})
        offers = entry["offers"]

        # zaktualizuj widziane / dodaj nowe
        for url, price in current.items():
            if url in offers:
                offers[url]["last"] = today.isoformat()
                offers[url]["price"] = price
            else:
                offers[url] = {"price": price, "first": today.isoformat(), "last": today.isoformat()}

        # oferty które znikły — jeśli żyły krótko, to realna sprzedaż
        for url in [u for u in offers if u not in current]:
            o = offers.pop(url)
            try:
                lifetime = (date.fromisoformat(o["last"]) - date.fromisoformat(o["first"])).days
                if lifetime <= SOLD_FAST_DAYS:
                    entry["sold_fast"].append({"price": o["price"], "date": today.isoformat()})
            except Exception:
                pass

        # trzymaj tylko sprzedaże z ostatnich 90 dni
        cutoff = (today - timedelta(days=90)).isoformat()
        entry["sold_fast"] = [s for s in entry["sold_fast"] if s["date"] >= cutoff]

        sold_prices = [s["price"] for s in entry["sold_fast"]]
        entry["demand_median"] = int(statistics.median(sold_prices)) if len(sold_prices) >= 5 else None
        entry["updated"] = today.isoformat()
        log.info(f"OLX watch [{q}]: {len(current)} ofert, {len(sold_prices)} szybkich sprzedaży, popyt={entry['demand_median']}")

    OLX_WATCH_FILE.write_text(json.dumps(watch, ensure_ascii=False, indent=1))


def main():
    if not SEEN_FILE.exists():
        log.info("Brak seen.json")
        return

    data = json.loads(SEEN_FILE.read_text())
    today = date.today().isoformat()

    today_listings = [
        v for v in data.values()
        if isinstance(v, dict) and v.get("date") == today and v.get("score") is not None
    ]

    if not today_listings:
        log.info("Brak ogłoszeń z dzisiaj.")
        send_telegram("🦅 <b>DealHawk — podsumowanie dnia</b>\n\nDzisiaj nie znaleziono nowych ofert.")
        return

    # Aktualizacja śledzenia ofert OLX (ceny popytu) — raz dziennie
    from tracker import olx_query_for
    try:
        queries = {olx_query_for(l["title"], l.get("search", "e-bike fully")) for l in today_listings}
        update_olx_watch(queries)
    except Exception as e:
        log.error(f"OLX watch update error: {e}")

    # Re-weryfikacja przebiegu kandydatów tuż przed wysyłką — dane ze skanu
    # mogą być błędne lub nieaktualne (sprzedawca edytuje ogłoszenie)
    from tracker import fetch_listing_details, parse_mileage, calc_profit, MAX_MILEAGE

    # Ranking po realnym zysku PLN (główne kryterium inwestycyjne);
    # oferty bez wyliczonego zysku lecą na koniec wg score
    def rank_key(l):
        profit = l.get("profit")
        return (profit is not None, profit if profit is not None else 0, l["score"])

    candidates = sorted(today_listings, key=rank_key, reverse=True)[:10]
    verified = []
    for l in candidates:
        fresh_mileage, _ = fetch_listing_details(l["url"], l["title"])
        fresh_num = parse_mileage(fresh_mileage)
        if fresh_num is not None and fresh_num > MAX_MILEAGE:
            log.info(f"Odrzucono przy weryfikacji ({fresh_mileage}): {l['title'][:50]}")
            continue
        if fresh_mileage != l.get("mileage"):
            log.info(f"Skorygowano przebieg {l.get('mileage')} -> {fresh_mileage}: {l['title'][:50]}")
            l["mileage"] = fresh_mileage
            l["mileage_num"] = fresh_num
            # przebieg zmienia wycenę — przelicz zysk
            if l.get("price_num") and l.get("olx_median"):
                l["profit"] = calc_profit(l["price_num"], l["olx_median"], fresh_num)
        verified.append(l)

    top5 = sorted(verified, key=rank_key, reverse=True)[:5]

    if not top5:
        send_telegram("🦅 <b>DealHawk — podsumowanie dnia</b>\n\nDzisiaj nie znaleziono nowych ofert (wszystkie odpadły przy weryfikacji).")
        return

    import html as html_mod
    lines = [f"🦅 <b>DealHawk — TOP {len(top5)} okazji dnia {today}</b>\n(ranking wg zysku z odsprzedaży w PL)\n"]
    for i, l in enumerate(top5, 1):
        medal = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"][i - 1]
        profit = l.get("profit")
        if profit is not None:
            emoji = "🟢" if profit > 500 else "🟡" if profit > 0 else "🔴"
            profit_line = f"   {emoji} Zysk PL: ~{profit:+,.0f} zł\n"
        else:
            profit_line = "   ⚪ Zysk PL: brak danych OLX\n"
        lines.append(
            f"{medal} <b>{html_mod.escape(l['title'])}</b>\n"
            f"   💰 {l['price']}  🚵 {l.get('mileage', 'brak danych')}  ⭐ {l['score']}/100\n"
            f"{profit_line}"
            f"   🔗 {l['url']}\n"
        )

    # Gotowe pytanie do sprzedawcy, jeśli któraś oferta nie ma przebiegu
    if any(l.get("mileage") in (None, "brak danych") for l in top5):
        lines.append(
            "📋 Brak przebiegu? Zapytaj sprzedawcę (tapnij aby skopiować):\n"
            "<code>Hallo, wie viele Kilometer ist das Bike insgesamt gelaufen? Danke!</code>"
        )

    # Odepnij poprzednie podsumowanie
    prev_id = load_pinned()
    if prev_id:
        unpin_message(prev_id)

    # Wyślij nowe i przypiń
    msg_id = send_telegram("\n".join(lines))
    if msg_id:
        pin_message(msg_id)
        save_pinned(msg_id)

    log.info(f"Wysłano podsumowanie — {len(today_listings)} ofert dzisiaj, TOP {len(top5)} wybrane.")


if __name__ == "__main__":
    main()
