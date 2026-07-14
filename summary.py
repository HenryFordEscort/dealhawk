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
    import time
    from tracker import (fetch_olx_offers, olx_relevant_offers, load_olx_watch, OLX_WATCH_FILE,
                         SOLD_FAST_DAYS, LIQUIDITY_MAX_DAYS, load_olx_details, fetch_olx_detail,
                         OLX_DETAILS_FILE, OLX_DETAILS_KEEP_DAYS)

    watch = load_olx_watch()
    today = date.today()
    all_queries = set(queries) | set(watch.keys())
    all_offer_urls = set()   # do wzbogacenia o strukturalny przebieg/stan

    from tracker import olx_offer_gone

    for q in all_queries:
        try:
            # filtr trafności — części i keyword-stuffing nie mogą zostać
            # "sprzedażami" (ładowarka 550 zł znika = fałszywy popyt 550 zł!)
            current = olx_relevant_offers(q, fetch_olx_offers(q))
        except Exception as e:
            log.error(f"OLX watch fetch [{q}]: {e}")
            continue

        # pusta lista = blokada/zmiana HTML, NIE masowa sprzedaż — pomiń cykl
        if not current:
            log.warning(f"OLX watch [{q}]: 0 ofert w odpowiedzi — pomijam ten cykl")
            continue

        all_offer_urls.update(current.keys())
        entry = watch.setdefault(q, {"offers": {}, "sold_fast": [], "demand_median": None, "updated": None})
        offers = entry["offers"]

        # zaktualizuj widziane / dodaj nowe. p0 = cena początkowa (do liczenia
        # o ile sprzedawca zbija zanim rower zejdzie); price = ostatnia widziana.
        for url, price in current.items():
            if url in offers:
                offers[url]["last"] = today.isoformat()
                offers[url]["price"] = price
                offers[url].setdefault("p0", price)
            else:
                offers[url] = {"price": price, "p0": price,
                               "first": today.isoformat(), "last": today.isoformat()}

        # oferty poza oknem wyników — potwierdź śmierć na stronie oferty.
        # olx_offer_gone wymaga POZYTYWNEGO dowodu (404 / status nieaktywny);
        # samo wypadnięcie z okna NIGDY nie liczy się jako sprzedaż.
        for url in [u for u in offers if u not in current]:
            gone = olx_offer_gone(url)
            if gone is not True:
                continue  # żyje albo nie wiadomo → obserwuj dalej
            o = offers.pop(url)
            try:
                # czas życia: od pierwszego zobaczenia do potwierdzonej śmierci
                # (byliśmy pewni że żyła wczoraj — sprawdzamy codziennie)
                lifetime = (today - date.fromisoformat(o["first"])).days
                rec = {"p0": o.get("p0", o["price"]), "price": o["price"],
                       "date": today.isoformat(), "days": lifetime}
                det = load_olx_details().get(url) or {}
                for f in ("km", "y", "wh", "stan"):   # atrybuty → popyt per wariant
                    if det.get(f) is not None:
                        rec[f] = det[f]
                if lifetime <= LIQUIDITY_MAX_DAYS:
                    entry["sold_fast"].append(rec)                        # sprzedaż
                else:
                    entry.setdefault("expired", []).append(rec)          # wisiała za długo
            except Exception:
                pass

        # trzymaj tylko sprzedaże z ostatnich 90 dni
        cutoff = (today - timedelta(days=90)).isoformat()
        entry["sold_fast"] = [s for s in entry["sold_fast"] if s["date"] >= cutoff]

        # cena popytu tylko z szybkich sprzedaży (<=14 dni = wiarygodna cena);
        # stare wpisy bez 'days' liczą się jako szybkie (były zapisywane pod tą regułą)
        demand_prices = [s["price"] for s in entry["sold_fast"] if s.get("days", 0) <= SOLD_FAST_DAYS]
        if len(demand_prices) >= 3:
            # odetnij historyczne śmieci (części zapisane przed filtrem trafności)
            dm = statistics.median(demand_prices)
            demand_prices = [p for p in demand_prices if p >= 0.3 * dm]
        entry["demand_median"] = int(statistics.median(demand_prices)) if len(demand_prices) >= 5 else None
        liq_days = [s["days"] for s in entry["sold_fast"] if isinstance(s.get("days"), int)]
        liq_med = int(statistics.median(liq_days)) if len(liq_days) >= 5 else None

        # przytnij 'expired' do 90 dni i policz statystyki sprzedaży-strony
        entry["expired"] = [s for s in entry.get("expired", []) if s.get("date", "") >= cutoff]
        # typowa obniżka: o ile % cena domykająca < początkowej (tylko gdzie mamy p0≠price)
        drops = [(s["p0"] - s["price"]) / s["p0"] for s in entry["sold_fast"]
                 if s.get("p0") and s.get("price") and s["p0"] >= s["price"] > 0]
        entry["typical_drop_pct"] = round(statistics.median(drops) * 100, 1) if len(drops) >= 5 else None
        # sprzedawalność: udział ofert które ZESZŁY szybko vs wszystkie zamknięte
        n_sold, n_exp = len(entry["sold_fast"]), len(entry.get("expired", []))
        entry["sell_through_pct"] = round(n_sold / (n_sold + n_exp) * 100) if (n_sold + n_exp) >= 5 else None

        entry["updated"] = today.isoformat()
        log.info(f"OLX watch [{q}]: {len(current)} ofert, {n_sold} sprzedaży / {n_exp} wygasłych, "
                 f"popyt={entry['demand_median']}, płynność={liq_med} dni, "
                 f"sprzedawalność={entry['sell_through_pct']}%, obniżka={entry['typical_drop_pct']}%")

    OLX_WATCH_FILE.write_text(json.dumps(watch, ensure_ascii=False, indent=1))

    # Wzbogacenie o strukturalny przebieg/stan ze stron ofert (pełna dokładność).
    # Cache per-URL: pobieramy TYLKO nowe oferty, z limitem i pauzą (grzecznie).
    try:
        details = load_olx_details()
        new_urls = [u for u in all_offer_urls if u not in details]
        fetched = 0
        for u in new_urls:
            if fetched >= 150:   # limit na jeden run — reszta doładuje się jutro
                break
            details[u] = {**fetch_olx_detail(u), "seen": today.isoformat()}
            fetched += 1
            time.sleep(0.8)      # grzeczność wobec OLX
        for u in all_offer_urls:  # odśwież znacznik obecności
            if u in details:
                details[u]["seen"] = today.isoformat()
        cutoff = (today - timedelta(days=OLX_DETAILS_KEEP_DAYS)).isoformat()
        details = {u: d for u, d in details.items() if d.get("seen", "") >= cutoff}
        OLX_DETAILS_FILE.write_text(json.dumps(details, ensure_ascii=False))
        got_km = sum(1 for d in details.values() if d.get("km") is not None)
        log.info(f"OLX details: +{fetched} pobranych, {len(details)} w cache ({got_km} z przebiegiem)")
    except Exception as e:
        log.error(f"OLX details enrichment error: {e}")


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

    # Zwiń duble (ten sam rower, dwa ID) — historyczne, sprzed dedupu w skanie
    from tracker import dedup_key
    seen_keys, deduped = set(), []
    for v in today_listings:
        k = (dedup_key(v.get("title", "")), v.get("price_num"))
        if k in seen_keys:
            continue
        seen_keys.add(k)
        deduped.append(v)
    today_listings = deduped

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
    from tracker import fetch_listing_details, parse_mileage, calc_profit, annual_roi, MAX_MILEAGE

    # Ranking wg ROI rocznego (efektywność kapitału) — oferty z dodatnim ROI
    # pierwsze, potem reszta wg zysku bezwzględnego, na końcu wg score.
    def rank_key(l):
        roi = l.get("roi_annual")
        profit = l.get("profit") if l.get("profit") is not None else -10**9
        good_roi = roi is not None and roi > 0
        return (good_roi, roi if good_roi else 0, profit, l["score"])

    candidates = sorted(today_listings, key=rank_key, reverse=True)[:10]
    verified = []
    for l in candidates:
        fresh_mileage, _, fresh_price = fetch_listing_details(l["url"], l["title"])
        if not l.get("price_num") and fresh_price:
            from tracker import parse_price
            l["price"] = fresh_price
            l["price_num"] = parse_price(fresh_price)
        fresh_num = parse_mileage(fresh_mileage)
        if fresh_num is not None and fresh_num > MAX_MILEAGE:
            log.info(f"Odrzucono przy weryfikacji ({fresh_mileage}): {l['title'][:50]}")
            continue
        if fresh_mileage != l.get("mileage"):
            log.info(f"Skorygowano przebieg {l.get('mileage')} -> {fresh_mileage}: {l['title'][:50]}")
            l["mileage"] = fresh_mileage
            l["mileage_num"] = fresh_num
            # przebieg zmienia wycenę — przelicz zysk i ROI (realna cena + rocznik)
            base_price = l.get("buy_price") or l.get("price_num")
            if base_price and l.get("olx_median"):
                l["profit"] = calc_profit(base_price, l["olx_median"], fresh_num, l.get("year"))
                l["roi_annual"] = annual_roi(l["profit"], base_price, l.get("liquidity_days"))
        verified.append(l)

    top5 = sorted(verified, key=rank_key, reverse=True)[:5]

    if not top5:
        send_telegram("🦅 <b>DealHawk — podsumowanie dnia</b>\n\nDzisiaj nie znaleziono nowych ofert (wszystkie odpadły przy weryfikacji).")
        return

    import html as html_mod
    lines = [f"🦅 <b>DealHawk — TOP {len(top5)} okazji dnia {today}</b>\n(ranking wg ROI rocznego — zwrotu z kapitału)\n"]
    for i, l in enumerate(top5, 1):
        medal = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"][i - 1]
        profit = l.get("profit")
        if profit is not None:
            emoji = "🟢" if profit > 500 else "🟡" if profit > 0 else "🔴"
            profit_line = f"   {emoji} Zysk PL: ~{profit:+,.0f} zł\n"
        else:
            profit_line = "   ⚪ Zysk PL: brak danych OLX\n"
        roi = l.get("roi_annual")
        liq = l.get("liquidity_days")
        if roi is not None and liq:
            # zwrot z kapitału na tę transakcję = ROI_roczne × dni / 365
            per_trade = roi * liq / 365
            profit_line += f"   💹 Zwrot {per_trade*100:+.0f}% w ~{liq} dni\n"
        lines.append(
            f"{medal} <b>{html_mod.escape(l['title'])}</b>\n"
            f"   💰 {l['price']}  🚵 {l.get('mileage', 'brak danych')}{('  📅 ' + str(l['year'])) if l.get('year') else ''}  ⭐ {l['score']}/100\n"
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
