#!/usr/bin/env python3
"""Testy regresyjne DealHawk — bez sieci, bez Telegrama.
Uruchom: python test.py   (exit 1 gdy cokolwiek pęknie).
Chroni całą logikę przed cichym zepsuciem przy zmianach."""
import os
import sys
import json
import tempfile
from pathlib import Path

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test")
os.environ.setdefault("TELEGRAM_CHAT_ID", "test")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tracker  # noqa: E402
from tracker import (  # noqa: E402
    _extract_mileage, is_electric, is_fully, is_junk, is_small_battery, battery_wh,
    extract_year, year_factor, has_known_motor, is_too_worn, is_premium_brand,
    negotiation_headroom, realistic_buy_price, dedup_key, find_relisting,
    build_recent_index, get_liquidity, annual_roi, price_trend, log_market,
    append_history, parse_price, parse_mileage, olx_query_for,
    _match_pool, TITLE_PATTERNS, PRICE_PATTERNS, CURRENT_YEAR,
)

tracker._eur_pln_cache = 4.30  # bez sieci
FAILS = []


def check(cond, name):
    print(("  ✓ " if cond else "  ✗ ") + name)
    if not cond:
        FAILS.append(name)


print("Przebieg (ekstrakcja):")
check(_extract_mileage("Nur 800km Trek", "") == "800 km", "tytuł 'Nur 800km'")
check(_extract_mileage("Trek Rail", "Km Stand 1519km") == "1.519 km", "opis 'Km Stand'")
check(_extract_mileage("T", "Reichweite ca. 120km Akku 625Wh") == "brak danych", "zasięg ≠ przebieg")
check(_extract_mileage("Focus", "5467km Gesamtlaufleistung Akku 500W Reichweite ca.120Km") == "5.467 km", "przebieg mimo zasięgu obok")
check(_extract_mileage("Specialized Levo 700 Wh Akku - nur 1.400 km", "") == "1.400 km", "tytuł: nur X km obok Akku")
check(_extract_mileage("Trek", "Software neu, 12.300 km, Rad wird") == "12.300 km", "duży przebieg w opisie")
check(_extract_mileage("Trek", "") == "brak danych", "brak danych = uczciwie")

print("Elektryk / fully / śmieci:")
check(is_electric("Cube Stereo Hybrid 120 625"), "Stereo Hybrid = elektryk")
check(is_electric("Trek Rail 5 625Wh"), "sklejone 625Wh")
check(not is_electric("Canyon Spectral Mountainbike"), "analog odpada")
check(not is_electric("Cube Stereo 140 Enduro"), "Stereo bez Hybrid = analog")
check(is_fully("Cube Stereo Hybrid 140") and not is_fully("Trek Marlin Hardtail"), "fully vs hardtail")
check(not is_junk("Cube Stereo Hybrid Rahmengröße L"), "Rahmengröße przechodzi")
check(is_junk("E-Bike Rahmen Carbon"), "sama rama odpada")
check(is_junk("Cube Fully XL Bosch"), "XL odpada")
check(is_junk("Motor Bosch CX 85Nm"), "część (Motor...) odpada")
check(is_junk("Hardtail e-bike bosch"), "hardtail odpada")

print("Silnik / marka / bateria:")
check(has_known_motor("Cube", "Bosch Performance CX"), "Bosch = OK")
check(not has_known_motor("Cube", "Shimano EP8 motor"), "Shimano odpada")
check(has_known_motor("X", None), "błąd pobrania = kredyt zaufania")
check(is_premium_brand("KTM Macina") and not is_premium_brand("Conway Xyron"), "whitelista marek")
check(is_small_battery("Levo SL Comp", ""), "SL = mała bateria")
check(is_small_battery("Cube", "320 Wh Akku"), "<500 Wh = mała")
check(not is_small_battery("Trek", "625 Wh Akku"), "625 Wh = OK")
check(battery_wh("x", "320 Wh + 160 Wh extender") == 320, "battery_wh bierze największą sensowną")

print("Rocznik + wycena:")
check(extract_year("Modelljahr 2022") == 2022, "rocznik z 'Modelljahr'")
check(extract_year("Cube 160 625Wh") is None, "160/625 to nie rok")
check(year_factor(CURRENT_YEAR - 3) == 1.0, "rok odniesienia = 1.0")
check(year_factor(CURRENT_YEAR - 1) > 1.0 > year_factor(CURRENT_YEAR - 5), "nowszy>starszy")
check(is_too_worn(4000) and not is_too_worn(2000) and not is_too_worn(None), "próg przebiegu 3000")

print("Negocjacja:")
check(realistic_buy_price(2500, "2.500 € VB", "")[0] == 2200, "2500 VB → 2200 (kalibracja)")
check(negotiation_headroom(2000, "2.000 € Festpreis", "")[0] == 0.02, "Festpreis = mur")
check(negotiation_headroom(3000, "3.000 € VB", "muss weg")[0] > 0.10, "VB+presja > baza")

print("Dedup:")
check(dedup_key("Cube Stereo Hybrid 140 top") == "cube stereo hybrid 140", "klucz = model")
check(dedup_key("Ebike Fully Rock+ Bosch") == "ebike fully rock bosch", "nieznany → tytuł")
idx = build_recent_index({"1": {"title": "Cube Stereo Hybrid 140", "price_num": 1800,
                                "mileage_num": 1250, "score": 40, "date": tracker.date.today().isoformat()}})
check(find_relisting(idx, "Cube Stereo Hybrid 140 top", 1800, 1280) is not None, "re-listing wykryty (tolerancja)")
check(find_relisting(idx, "Cube Stereo Hybrid 140", 2500, 1250) is None, "inna cena ≠ dubel")

print("Płynność / ROI / trend:")
tracker._olx_watch_cache = {"m": {"sold_fast": [{"price": 100, "date": "x", "days": d} for d in [8, 10, 12, 6, 9]]}}
check(get_liquidity("m") == 9, "płynność = mediana dni")
check(get_liquidity("brak") is None, "brak danych = None")
check(annual_roi(400, 1500, 10) is not None and annual_roi(400, 1500, 5) > annual_roi(400, 1500, 30), "ROI: szybszy=wyższy")
check(annual_roi(None, 1500, 10) is None and annual_roi(400, None, 10) is None, "ROI guardy")

print("Parser (samonaprawianie):")
check(_match_pool(PRICE_PATTERNS, '"adlist--item--price">1.500 €<')[0] is not None, "cena wzorzec główny")
check(_match_pool(PRICE_PATTERNS, '>1.850 € VB<')[0] is not None, "cena fallback (goła kwota)")
check(_match_pool(TITLE_PATTERNS, 'href="/s-anzeige/x/1">Trek Rail</a>')[0].group(2) == "Trek Rail", "tytuł wzorzec")

print("Parsowanie liczb:")
check(parse_price("2.200 € VB") == 2200, "parse_price")
check(parse_mileage("1.519 km") == 1519 and parse_mileage("brak danych") is None, "parse_mileage")

print("Dziennik / log rynku (pola):")
_tmp = tempfile.mkdtemp()
tracker.HISTORY_FILE = Path(_tmp, "h.jsonl"); tracker._history_cache = None
append_history("cube", 2000, ad_id="1", olx_median=14500, ev="drop")
_h = json.loads(tracker.HISTORY_FILE.read_text().splitlines()[0])
check(_h["kurs"] == 4.30 and _h["ev"] == "drop" and _h["olx"] == 14500, "append_history: kurs+event+olx")
tracker.MARKET_FILE = Path(_tmp, "m.jsonl")
log_market({"id": "9", "title": "Cube Stereo Hybrid 140 Modelljahr 2022, 1819 km", "price_num": 2000,
            "loc": "89520 Heidenheim"}, "Cube")
_m = json.loads(tracker.MARKET_FILE.read_text().splitlines()[0])
check(_m["m"] == "cube stereo hybrid 140" and _m["y"] == 2022 and _m["km"] == 1819 and _m["loc"].startswith("89520"),
      "log_market: model+rocznik+przebieg+lokalizacja z tytułu")

print("Precyzja OLX (parser URL / mediana przycięta / porównywalne):")
from tracker import parse_olx_slug, trimmed_median, wh_class, olx_comparable_price  # noqa
check(parse_olx_slug("https://www.olx.pl/d/oferta/cube-140-750wh-2023-rok-108km-x") == (2023, 108, 750),
      "slug: rok+przebieg+bateria")
check(parse_olx_slug("https://www.olx.pl/d/oferta/cube-stereo-one-44-hpc-slx-x") == (None, None, None),
      "slug bez kotwic → brak false-positów")
check(parse_olx_slug("https://www.olx.pl/d/oferta/levo-85nm-bosch-x") == (None, None, None),
      "85nm (moment) ≠ przebieg")
check(trimmed_median([100, 200, 300, 400, 30000]) == 300, "mediana przycięta zabija outlier 30000")
check(wh_class(500) == "S" and wh_class(625) == "M" and wh_class(750) == "L", "klasy baterii")
# porównywalne: DE rower 2022, 625Wh → wybiera pas, nie całą populację
_offers = {
    "https://www.olx.pl/d/oferta/a-2022-rok-625wh-x": 15000,
    "https://www.olx.pl/d/oferta/b-2022-rok-625wh-x": 15500,
    "https://www.olx.pl/d/oferta/c-2021-rok-625wh-x": 14500,
    "https://www.olx.pl/d/oferta/d-2023-rok-625wh-x": 16000,
    "https://www.olx.pl/d/oferta/e-2019-rok-500wh-x": 9000,    # stary, mała bateria — powinien odpaść
    "https://www.olx.pl/d/oferta/f-one-44-slx-x": 29000,       # inny model/premium — odpaść
}
cp, method, n = olx_comparable_price(_offers, ref_year=2022, ref_wh=625)
check(cp is not None and cp < 20000 and "bateria" in method, f"porównywalne odfiltrowało outliery (cp={cp}, {method})")
check(n < len(_offers), "pas węższy niż cała populacja")
# strukturalny przebieg z cache nadpisuje zgadywanie z URL-a
_off2 = {"https://www.olx.pl/d/oferta/a-x": 15000, "https://www.olx.pl/d/oferta/b-x": 15500,
         "https://www.olx.pl/d/oferta/c-x": 14000, "https://www.olx.pl/d/oferta/d-x": 16000,
         "https://www.olx.pl/d/oferta/e-x": 9000}
_det = {"https://www.olx.pl/d/oferta/e-x": {"km": 12000}}  # ta jedna ma 12000 km (zajeżdżona)
cp_ref, _, n_ref = olx_comparable_price(_off2, ref_km=1000)  # bez detali
cp_det, _, n_det = olx_comparable_price(_off2, ref_km=1000, details=_det)  # z detalami
check(n_det < n_ref or cp_det != cp_ref, "detale z cache zawężają pas (odrzucają 12000 km przy ref 1000)")

print("Trafność OLX (części / keyword-stuffing / nowe-sklepowe):")
from tracker import olx_relevant_offers, _parse_detail_fields, _is_shop_slug  # noqa
_pool = {
    "https://www.olx.pl/d/oferta/specialized-turbo-levo-comp-2022-x": 17000,
    "https://www.olx.pl/d/oferta/rower-specialized-turbo-levo-expert-x": 19000,
    "https://www.olx.pl/d/oferta/specialized-turbo-levo-sl-x": 16000,
    "https://www.olx.pl/d/oferta/super-specialized-turbo-levo-alloy-x": 15500,
    "https://www.olx.pl/d/oferta/ladowarka-do-rowerow-specialized-turbo-levo-x": 550,      # część (start sluga)
    "https://www.olx.pl/d/oferta/bateria-akumulator-specialized-turbo-levo-500wh-x": 580,  # część
    "https://www.olx.pl/d/oferta/nowy-wyswietlacz-specialized-turbo-levo-x": 880,          # część (tania + słowo)
}
_rel = olx_relevant_offers("specialized turbo levo", _pool)
check(len(_rel) == 4 and all(p >= 15000 for p in _rel.values()), f"części odsiane ({len(_rel)}/7 zostało)")
# keyword-stuffing: cube z 'trek...rail' upchniętym w ogonie NIE wpada do trek rail
_stuffed = {
    "https://www.olx.pl/d/oferta/trek-rail-9-8-xt-l-x": 15000,
    "https://www.olx.pl/d/oferta/trek-rail-5-2022-x": 12000,
    "https://www.olx.pl/d/oferta/rower-trek-rail-7-x": 13000,
    "https://www.olx.pl/d/oferta/e-mtb-trek-rail-9-x": 16000,
    "https://www.olx.pl/d/oferta/cube-stereo-hybrid-160-race-2022-ebike-trek-enduro-focus-trail-jam-mtb-rail-gorski-x": 11700,
}
_rel2 = olx_relevant_offers("trek rail", _stuffed)
check(len(_rel2) == 4 and not any("cube" in u for u in _rel2), "keyword-stuffing odrzucony (Cube nie wpada do Trek Rail)")
check(_is_shop_slug("https://www.olx.pl/d/oferta/raty-0-12m-cy-gw-cube-x") and
      not _is_shop_slug("https://www.olx.pl/d/oferta/cube-stereo-hybrid-x"), "wykrywanie sklepów (raty/F-VAT)")
# zamieniona kolejność słów = ten sam rower (zwarte okno, nie sztywna kolejność)
_swap = {"https://www.olx.pl/d/oferta/specialized-levo-turbo-comp-x": 15000,
         "https://www.olx.pl/d/oferta/rower-specialized-turbo-levo-x": 16000}
check(len(olx_relevant_offers("specialized turbo levo", _swap)) == 2, "'levo turbo' = 'turbo levo' (kolejność luzem)")

print("Detale OLX (rocznik+Wh z opisu, odporność na boilerplate):")
_html = ('<html>© 2026 OLX <script>var y=2026</script>'
         'Przebieg • 1 200 km ... Stan: Używane ...'
         '"description":"Sprzedam rower z 2022 roku, bateria 625 Wh, stan bdb"</html>')
_d = _parse_detail_fields(_html)
check(_d.get("km") == 1200 and _d.get("stan") == "Używane", "przebieg+stan ze strony")
check(_d.get("y") == 2022, "rocznik z OPISU (nie 2026 ze stopki!)")
check(_d.get("wh") == 625, "bateria z opisu")
_d2 = _parse_detail_fields('<html>© 2026 OLX rower bez opisu</html>')
check(_d2.get("y") is None, "bez opisu → bez rocznika (zero false-positów)")

print("Porównywalne preferują używane:")
_mix = {f"https://www.olx.pl/d/oferta/cube-used-{i}-x": 14000 + i * 200 for i in range(5)}
_mix["https://www.olx.pl/d/oferta/raty-0-f-vat-cube-nowy-x"] = 22000     # sklep, nówka
_mix["https://www.olx.pl/d/oferta/cube-nowka-x"] = 21500
_det3 = {"https://www.olx.pl/d/oferta/cube-nowka-x": {"stan": "Nowe"}}
cp3, met3, n3 = olx_comparable_price(_mix, details=_det3)
check(cp3 is not None and cp3 < 16000 and "używane" in met3, f"nówki wykluczone (cp={cp3}, {met3})")

print("Wykrywanie sprzedaży OLX (pozytywny dowód, nie boilerplate):")
from tracker import _judge_olx_dead  # noqa
_live_html = 'x status\\":\\"active x ... "nie jest już dostępne" w tłumaczeniach ... nieaktualne'
check(_judge_olx_dead(_live_html) is False, "żywa (status active) mimo fraz 'nieaktualne' w boilerplate")
check(_judge_olx_dead('"availability":"https://schema.org/InStock"') is False, "żywa (schema InStock)")
check(_judge_olx_dead('x status\\":\\"removed_by_user x') is True, "martwa (removed_by_user)")
check(_judge_olx_dead('strona bez zadnych markerow nieaktualne') is None, "brak dowodu → None (nie zgadujemy)")

print("Prognoza sprzedaży OLX (cena domykająca / werdykt):")
from tracker import olx_sell_forecast  # noqa
_today = tracker.date.today().isoformat()
tracker._olx_watch_cache = {"cube stereo hybrid 140": {
    "sold_fast": [{"price": 14000, "p0": 15000, "date": _today, "days": d} for d in [8, 12, 6, 10, 9, 11]],
    "sell_through_pct": 78, "typical_drop_pct": 6.0, "demand_median": 14000, "updated": _today,
}}
_f = olx_sell_forecast("cube stereo hybrid 140", asking_price=16000)
check(_f is not None and _f["clearing"] == 14000 and _f["sell_through"] == 78, "forecast: cena domykająca + sprzedawalność")
check("za wysoko" in _f["verdict"], "werdykt: 16000 vs 14000 = za wysoko")
_f2 = olx_sell_forecast("cube stereo hybrid 140", asking_price=14100)
check("OK" in _f2["verdict"], "werdykt: 14100 ≈ domykająca = OK")
check(olx_sell_forecast("nieznany") is None, "brak danych → None")

if FAILS:
    print(f"\n❌ {len(FAILS)} TESTÓW NIE PRZESZŁO: {FAILS}")
    sys.exit(1)
print("\n✅ WSZYSTKIE TESTY OK")
sys.exit(0)
