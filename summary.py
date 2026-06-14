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


def send_telegram(text: str):
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
    except Exception as e:
        log.error(f"Telegram error: {e}")


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

    top5 = sorted(today_listings, key=lambda x: x["score"], reverse=True)[:5]

    lines = [f"🦅 <b>DealHawk — TOP {len(top5)} okazji dnia {today}</b>\n"]
    for i, l in enumerate(top5, 1):
        medal = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"][i - 1]
        lines.append(
            f"{medal} <b>{l['title']}</b>\n"
            f"   💰 {l['price']}  🚵 {l.get('mileage', 'brak danych')}  ⭐ {l['score']}/100\n"
            f"   🔗 {l['url']}\n"
        )

    send_telegram("\n".join(lines))
    log.info(f"Wysłano podsumowanie — {len(today_listings)} ofert dzisiaj, TOP {len(top5)} wybrane.")


if __name__ == "__main__":
    main()
