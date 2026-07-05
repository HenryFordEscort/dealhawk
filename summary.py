import os
import json
import logging
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
