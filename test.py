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

print("Integralność modułów (składnia/import):")
import importlib  # noqa
try:
    import summary  # noqa
    check(True, "summary.py importuje się (składnia OK)")
except Exception as e:
    check(False, f"summary.py NIE importuje się: {e}")

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

print("Cennik cech (ile rynek płaci za rocznik/baterię/wyposażenie):")
from tracker import zbuduj_cennik, wycen_z_cennikiem, odduplikuj, _mnoznik  # noqa
# sztuczny rynek: cena rośnie z rocznikiem i baterią, spada z przebiegiem
_rynek = []
for i in range(40):
    y, wh, km = 2019 + i % 6, 400 + (i % 4) * 125, (i % 5) * 1000
    cena = int(6000 * (1.15 ** (y - 2019)) * (1 + (wh - 400) / 400 * 0.5) * (1 - km / 100000))
    _rynek.append({"cena": cena, "y": y, "wh": wh, "km": km, "sprzedawca": str(i)})
_c = zbuduj_cennik(_rynek)
check(_c and _c["n_ofert"] == 40, "cennik policzony z rynku")
check(_c["cechy"]["y"]["zmiana_ceny_pct"] > 5, "wykrywa: nowszy rocznik = drożej")
check(_c["cechy"]["wh"]["zmiana_ceny_pct"] > 0, "wykrywa: większa bateria = drożej")
check(all("srodek" in v for v in _c["cechy"].values()), "każda cecha ma punkt odniesienia")
# REGRESJA: bez wyśrodkowania mnożnik liczył exp(0.2*2018) i wycena szła w kosmos
_m, _ = _mnoznik({"y": 2018, "wh": 400}, _c)
check(0.01 < _m < 100, f"mnożnik w rozsądnym zakresie (był 1e150): {_m:.3f}")
_stary = wycen_z_cennikiem(_rynek, {"y": 2018, "wh": 400, "km": 3000}, _c)
_nowy = wycen_z_cennikiem(_rynek, {"y": 2024, "wh": 750, "km": 500}, _c)
check(_stary and _nowy and _stary["cena"] < _nowy["cena"] * 0.6,
      f"stary/mała bateria WYRAŹNIE taniej ({_stary['cena']} vs {_nowy['cena']})")
check(500 < _stary["cena"] < 60000, "wycena w realnych widełkach, nie astronomiczna")
check(wycen_z_cennikiem(_rynek, {}, _c) is None, "o naszym rowerze nic nie wiemy → brak wyceny")
check(wycen_z_cennikiem([], {"y": 2020}, _c) is None, "brak ofert → brak wyceny")
check(_stary["widelki"][0] < _stary["cena"] < _stary["widelki"][1], "widełki obejmują wycenę")
# spam sklepu liczy się raz
_spam = [{"cena": 9999, "sprzedawca": "sklep", "model": "x"} for _ in range(40)]
check(len(odduplikuj(_spam)) == 1, "40 ogłoszeń tego samego sprzedawcy = 1 obserwacja")
check(len(odduplikuj([{"cena": 100 + i, "sprzedawca": "s", "model": "x"} for i in range(5)])) == 5,
      "różne ceny tego samego sprzedawcy = różne rowery")

print("Strona ZAKUPOWA korzysta z cennika (i nie liczy korekty dwa razy):")
from tracker import calc_profit, mileage_factor, year_factor, parse_spec_fields  # noqa
_sur = calc_profit(2000, 14000, km=3000, year=2018)                       # stara droga
_cen = calc_profit(2000, 14000, km=3000, year=2018, juz_skorygowana=True)  # z cennika
check(_cen > _sur, "cena z cennika NIE jest dodatkowo karana za rok/przebieg")
check(_cen == int(14000 - 2000 * tracker.get_eur_pln() - tracker.TRANSPORT_PLN),
      "z cennika: zysk = cena PL − koszt DE − transport, bez mnożników")
check(abs(_sur - int(14000 * mileage_factor(3000) * year_factor(2018)
                     - 2000 * tracker.get_eur_pln() - tracker.TRANSPORT_PLN)) <= 1,
      "bez cennika: stare mnożniki dalej działają (zgodność wsteczna)")
# niemieckie opisy też muszą dawać specyfikację
_de = parse_spec_fields("Rahmen: Carbon, Antrieb Shimano XT, Federgabel RockShox Lyrik")
check(_de.get("osprzet") == "xt" and _de.get("rama") == "carbon" and _de.get("widelec") == "lyrik",
      "specyfikacja czytana z NIEMIECKIEGO opisu (Antrieb/Rahmen/Federgabel)")
check(parse_spec_fields("Bremsen Shimano XT, Antrieb Deore").get("osprzet") == "deore",
      "niemieckie 'Bremsen XT' nie podszywa się pod napęd")

print("Silnik wyceny sprzedaży (build_price_reco):")
from tracker import build_price_reco, format_price_reco, parse_wycen_command  # noqa
_off = {f"https://www.olx.pl/d/oferta/rower-{i}": p for i, p in
        enumerate([14500, 15000, 15500, 14800, 15200, 15000])}
_fc = {"clearing": 14000, "days": 9, "sell_through": 78, "drop_pct": 6}
_r = build_price_reco(_off, {}, _fc, ref_year=2018, ref_km=2300, ref_wh=400, mode="balans")
check(_r is not None and 14800 <= _r["market"] <= 15200, "market ≈ mediana porównywalnych")
check(_r["clearing"] == 14000 and not _r["clearing_est"], "clearing z realnych sprzedaży")
check(_r["listing"] == 14420 and _r["room"] == 420, "balans: zapas na negocjacje nad domykającą")
check(build_price_reco(_off, {}, _fc, mode="szybko")["listing"] == 14000, "szybko: na cenie domykającej")
check(build_price_reco(_off, {}, _fc, mode="max")["listing"] >= _r["market"], "max: co najmniej poziom rynku")
_re = build_price_reco(_off, {}, None, mode="balans")   # brak forecast → szacunek
check(_re["clearing_est"] and _re["clearing"] < _re["market"], "bez sprzedaży: clearing szacowany < rynek")
check(build_price_reco({}, {}, None) is None, "brak ofert → None")
check("Wystaw za" in format_price_reco("cube", _r) and "14 420" in format_price_reco("cube", _r), "format: rekomendacja w wiadomości")
check("Za mało" in format_price_reco("x", None), "format: brak danych = uczciwy komunikat")

print("Parser komendy /wycen:")
check(parse_wycen_command("/wycen cube stereo hybrid 2018 2300 400") ==
      ("cube stereo hybrid", 2018, 2300, 400, "balans"), "pełna komenda: model/rok/km/wh")
check(parse_wycen_command("wycen specialized levo 2022 500 szybko") ==
      ("specialized levo", 2022, None, 500, "szybko"), "tryb szybko + bez przebiegu")
check(parse_wycen_command("/wycen trek rail max")[4] == "max", "tryb max")
check(parse_wycen_command("/start") is None, "nie-komenda → None")
check(parse_wycen_command("/wycen 2018 2300") is None, "sam numerek bez modelu → None")

print("Sprzedawalność wg półki cenowej (segment_liquidity):")
from tracker import segment_liquidity, format_segments  # noqa
_watch = {
    "tani model": {
        "sold_fast": [{"price": 2500, "days": 8} for _ in range(6)],
        "expired": [{"price": 2600} for _ in range(1)],
    },
    "martwy srodek": {  # 5-8k: dużo wygasłych, mało sprzedanych
        "sold_fast": [{"price": 6000, "days": 40} for _ in range(2)],
        "expired": [{"price": 6200} for _ in range(6)],
    },
    "premium": {  # 12-16k: dobra sprzedawalność
        "sold_fast": [{"price": 14000, "days": 20} for _ in range(5)],
        "expired": [{"price": 13500} for _ in range(2)],
    },
}
_seg = {r["band"]: r for r in segment_liquidity(_watch)}
check(_seg["do 3k zł"]["sell_through"] > 80 and _seg["do 3k zł"]["days"] == 8, "dół: schodzi szybko")
check(_seg["5–8k zł"]["sell_through"] < 35, "wolne pasmo: niska sprzedawalność")
check(_seg["12–16k zł"]["sell_through"] > 60 and _seg["12–16k zł"]["clearing"] == 14000, "premium: schodzi + clearing")
check(_seg["8–12k zł"]["n"] == 0, "puste pasmo = zero próbki")
check("Za mało" in format_segments(segment_liquidity({})), "brak danych = uczciwy komunikat")

# REGRESJA: wiszące oferty MUSZĄ wchodzić do mianownika. Bez tego wychodziło
# 100% sprzedaży w każdym paśmie (liczyliśmy tylko tych, co zniknęli).
_dzis = tracker.date(2026, 8, 21)
_wisi = {"model": {
    "sold_fast": [{"price": 14000, "days": 5} for _ in range(5)],
    "offers": {f"u{i}": {"price": 14000, "first": "2026-06-01"} for i in range(15)},
}}
_r = {r["band"]: r for r in segment_liquidity(_wisi, dzis=_dzis)}["12–16k zł"]
check(_r["sell_through"] == 25, f"5 sprzedanych + 15 wiszących = 25% (było 100%): {_r['sell_through']}%")
check(_r["expired"] == 15, "wiszące >30 dni policzone jako niesprzedane")
# oferta wisząca krótko = jeszcze nie wiadomo, nie liczy się w żadną stronę
_swieze = {"m": {"sold_fast": [{"price": 14000, "days": 5} for _ in range(5)],
                 "offers": {f"u{i}": {"price": 14000, "first": "2026-08-15"} for i in range(9)}}}
_r2 = {r["band"]: r for r in segment_liquidity(_swieze, dzis=_dzis)}["12–16k zł"]
check(_r2["sell_through"] == 100 and _r2["za_wczesnie"] == 9,
      "świeże oferty pomijane (za wcześnie na ocenę), nie liczone jako niesprzedane")
# zniknięcie w <2 dni = podejrzenie wznowienia, nie sprzedaż
_szyb = {"m": {"sold_fast": [{"price": 14000, "days": 1} for _ in range(10)]
                            + [{"price": 14000, "days": 10} for _ in range(5)]}}
_r3 = {r["band"]: r for r in segment_liquidity(_szyb, dzis=_dzis)}["12–16k zł"]
check(_r3["podejrzane"] == 10 and _r3["sold"] == 5, "zniknięcia w 1 dzień odsiane jako wznowienia")
check("wznowienia" in format_segments(segment_liquidity(_szyb, dzis=_dzis)),
      "komunikat przyznaje się do odsianych wznowień")

print("Diagnoza pustego skanu (awaria serwisu vs zmiana HTML) + anty-spam:")
from tracker import diagnose_empty_scan, check_feed_health  # noqa
_st = lambda code, n: [{"status": code} for _ in range(n)]
check("po ICH stronie" in diagnose_empty_scan(_st(503, 10)), "same 503 = awaria Kleinanzeigen, nie parsera")
check("blokuje" in diagnose_empty_scan(_st(403, 10)), "same 403 = blokada antybot")
check("zmiana HTML" in diagnose_empty_scan(_st(200, 10)), "200 bez ogłoszeń = realna zmiana HTML")
check("sieciowe" in diagnose_empty_scan(_st(None, 10)), "brak odpowiedzi = problem sieciowy")
check("po ICH stronie" in diagnose_empty_scan(_st(503, 6) + _st(200, 4)), "większość 503 wygrywa diagnozę")

_sent = []
tracker.send_telegram = lambda t: _sent.append(t)          # bez sieci
tracker.PARSE_STATE_FILE = Path(tempfile.mkdtemp()) / "ph.json"  # bez brudzenia repo
check_feed_health(_st(503, 10), 0)
check(len(_sent) == 1 and "leży" in _sent[0], "1. pusty skan → jeden alert z diagnozą 503")
check_feed_health(_st(503, 10), 0)
check(len(_sent) == 1, "2. pusty skan → CISZA (nie spamuje co 5 min)")
check_feed_health(_st(200, 10), 42)
check(len(_sent) == 2 and "znów działa" in _sent[1], "powrót → jedna wiadomość o wznowieniu")
check_feed_health(_st(200, 10), 42)
check(len(_sent) == 2, "normalna praca → cisza")

print("Parser kafelków OLX (pokrycie rynku — regresja z 38%):")
from tracker import parse_olx_cards  # noqa
_card = ('data-cy="l-card" id="1067864095"><style data-emotion="css x">.css-x{color:red;}</style>'
         '<a href="/d/oferta/rower-cube-stereo-CID767-ID1abc.html?search_reason=search%7Cpromoted">'
         '<h4 class="c">Cube Stereo Hybrid 140 <b>750Wh</b></h4></a>'
         '<p data-testid="ad-price">14 200 zł</p>'
         '<p data-testid="location-date">Opole - Odświeżono dnia 20 sierpnia 2026</p>')
_card2 = ('data-cy="l-card" id="222"><a href="/d/oferta/rower-b-CID767-ID2xyz.html">'
          '<h4>Trek Rail 5</h4></a><p data-testid="ad-price">9 100 zł</p>'
          '<p data-testid="location-date">Kraków - dzisiaj</p>')
_free = ('data-cy="l-card" id="333"><a href="/d/oferta/c-CID767-ID3.html"><h4>x</h4></a>'
         '<p data-testid="ad-price">Za darmo</p>')
_cards = parse_olx_cards("<div>" + _card + _card2 + _free + "</div>")
check(len(_cards) == 2, "parsuje kafelki, odrzuca 'Za darmo'")
check(_cards[0]["price"] == 14200, "cena ze spacją tysięcy (14 200 zł → 14200)")
check(_cards[0]["url"] == "https://www.olx.pl/d/oferta/rower-cube-stereo-CID767-ID1abc.html",
      "URL kanoniczny — bez ?search_reason")
check(_cards[0]["id"] == "1067864095" and _cards[0]["promoted"], "ID oferty + flaga promowania")
check(_cards[0]["title"] == "Cube Stereo Hybrid 140 750Wh", "tytuł bez tagów HTML")
check(_cards[0]["loc"] == "Opole" and _cards[1]["loc"] == "Kraków", "lokalizacja bez daty")
check(not _cards[1]["promoted"], "brak promowania = False")
check(parse_olx_cards("<html>pusta strona</html>") == [], "brak kafelków → pusta lista")

print("Specyfikacja z opisu (osprzęt/amortyzator/rama — 'Rail 5' ≠ 'Rail 9.8'):")
from tracker import parse_spec_fields, load_spec_kb  # noqa
check(bool(load_spec_kb().get("amortyzator_przod")), "wiedza_sprzet.json wczytany")
_s = parse_spec_fields("Rama: HPC Carbon. Naped: Shimano XT. Skok przod 170 mm. Rozmiar: XL. Fox Factory")
check(_s.get("osprzet") == "xt" and _s.get("rama") == "carbon", "osprzęt + rama z opisu")
check(_s.get("skok_mm") == 170 and _s.get("rozmiar") == "XL", "skok w mm + rozmiar ramy")
check(_s.get("wersja") == "factory", "wersja wykonania widelca (Factory)")
# PUŁAPKA: 'SLX' u Cube'a to nazwa wersji roweru, nie grupa Shimano
_trap = parse_spec_fields("Cube Stereo Hybrid 140 SLX ebike, model Slx czyli bogata wersja")
check("osprzet" not in _trap, "SLX bez kontekstu NIE udaje grupy napędowej")
check(parse_spec_fields("naped shimano slx, kaseta").get("osprzet") == "slx",
      "SLX z kontekstem 'shimano/naped' = grupa napędowa")
# PUŁAPKA: rozmiar koła/opony to nie skok amortyzatora
check("skok_mm" not in parse_spec_fields("kola 622 mm, opony 100 mm szerokosci"),
      "mm bez kontekstu ≠ skok amortyzatora")
# marka bez modelu — uczciwe 'nie wiem', nie zgadywanie
check(parse_spec_fields("zawieszenie RockShox, hamulce tarczowe").get("widelec") == "nieznany model",
      "sama marka widelca = 'nieznany model', bez zgadywania rangi")
check("widelec_rank" not in parse_spec_fields("zawieszenie RockShox"), "brak modelu = brak rangi")
_lyrik = parse_spec_fields("widelec RockShox Lyrik Ultimate")
_recon = parse_spec_fields("widelec RockShox Recon")
check(_lyrik["widelec_rank"] > _recon["widelec_rank"], "drabinka działa: Lyrik > Recon")
check(parse_spec_fields("") == {}, "pusty opis → nic (bez wymyślania)")
check(parse_spec_fields("Bosch CX Gen4 85 Nm").get("bosch_gen") == 4, "generacja silnika Boscha")
# PUŁAPKA: "XT" to i grupa napędowa, i hamulec — nie wolno policzyć dwa razy
_dbl = parse_spec_fields("hamulce Shimano XT 4-tlokowe, naped Shimano Deore")
check(_dbl.get("hamulce") == "xt" and _dbl.get("osprzet") == "deore",
      "hamulce XT nie podszywają się pod napęd (napęd = Deore)")
# łączny poziom wyposażenia — z tego, co akurat podane
check(parse_spec_fields("naped shimano xtr, rama carbon").get("poziom") == 6, "poziom z 2 cech = 6")
check(parse_spec_fields("naped shimano deore, rama alu").get("poziom") == 3, "słaby osprzęt + alu = 3")
_p1 = parse_spec_fields("naped shimano xt")
check(_p1.get("poziom") and _p1.get("poziom_n") == 1, "poziom z 1 cechy + licznik pewności")
check(parse_spec_fields("rower bez specyfikacji").get("poziom") is None, "brak cech → brak poziomu")
check(parse_spec_fields("naped shimano xtr, rama carbon").get("poziom")
      > parse_spec_fields("naped shimano deore, rama alu").get("poziom"), "poziom rośnie z jakością")

print("Dozorca OLX (zapisuje fakty, nie wnioski):")
import dozorca  # noqa
from dozorca import wykryj_zdarzenia, do_sprawdzenia, odcisk, dni_zycia  # noqa

_T1, _T2 = "2026-08-21T10:00", "2026-08-21T11:00"
_of = lambda p, t="Cube Stereo 140", l="Opole": {"url": "u1", "p": p, "q": "cube", "tytul": t, "loc": l}

# 1. nowa oferta
_ev, _st = wykryj_zdarzenia({}, {"a": _of(14900)}, {"cube"}, _T1)
check(len(_ev) == 1 and _ev[0]["ev"] == "nowa" and _ev[0]["p"] == 14900, "nowa oferta = zdarzenie 'nowa'")
check(_st["a"]["p0"] == 14900 and _st["a"]["pierwszy"] == _T1, "zapamiętana cena początkowa i data")

# 2. bez zmian = CISZA (żadnych zdarzeń-śmieci w dzienniku)
_ev2, _st2 = wykryj_zdarzenia(_st, {"a": _of(14900)}, {"cube"}, _T2)
check(_ev2 == [], "ta sama oferta bez zmian → zero zdarzeń")
check(_st2["a"]["p0"] == 14900 and _st2["a"]["ostatni"] == _T2, "p0 nietknięte, 'ostatni' odświeżony")

# 3. zmiana ceny
_ev3, _st3 = wykryj_zdarzenia(_st2, {"a": _of(13900)}, {"cube"}, _T2)
check(len(_ev3) == 1 and _ev3[0]["ev"] == "cena" and _ev3[0]["p_stara"] == 14900,
      "obniżka → zdarzenie 'cena' ze starą i nową")
check(_st3["a"]["p0"] == 14900 and _st3["a"]["p"] == 13900, "p0 to nadal cena WYJŚCIOWA")

# 4. zniknięcie z wyników NIE jest jeszcze zdarzeniem — tylko licznikiem
_ev4, _st4 = wykryj_zdarzenia(_st3, {}, {"cube"}, _T2)
check(_ev4 == [], "wypadnięcie z wyników ≠ zdarzenie (może to ranking)")
check(_st4["a"]["braki"] == 1, "policzone jako 1 brak")
_st5 = _st4
for _ in range(2):
    _, _st5 = wykryj_zdarzenia(_st5, {}, {"cube"}, _T2)
check(_st5["a"]["braki"] == 3, "braki się kumulują")
check(len(do_sprawdzenia(_st5)) == 1, "po 3 brakach oferta trafia do sprawdzenia strony")
check(do_sprawdzenia(_st4) == [], "po 1 braku jeszcze nie sprawdzamy")

# 5. KRYTYCZNE: awaria pobrania zapytania NIE może wyglądać jak wymarcie rynku
_ev6, _st6 = wykryj_zdarzenia(_st3, {}, set(), _T2)     # zapytanie padło
check(_st6["a"].get("braki", 0) == 0, "gdy zapytanie padło, oferty NIE są uznane za zaginione")

# 6. powrót po zniknięciu = dowód, że NIE została sprzedana
_ev7, _st7 = wykryj_zdarzenia(_st5, {"a": _of(13900)}, {"cube"}, _T2)
check(any(z["ev"] == "wrocila" for z in _ev7), "powrót oferty → zdarzenie 'wrocila'")
check(_st7["a"].get("braki", 0) == 0, "licznik braków wyzerowany po powrocie")

# 7. odcisk palca — do wykrywania wznowień
check(odcisk("Cube Stereo 140!!!", 14900, "Opole") == odcisk("cube stereo 140", 14900, "opole"),
      "odcisk odporny na wielkość liter i znaki")
check(odcisk("Cube Stereo 140", 14900, "Opole") != odcisk("Cube Stereo 140", 13900, "Opole"),
      "inna cena = inny odcisk")
check(dni_zycia({"pierwszy": "2026-08-01T10:00"}, "2026-08-21T10:00") == 20, "dni życia oferty")
check(dni_zycia({"pierwszy": "bzdura"}, _T1) is None, "zła data → None, bez wywrotki")

if FAILS:
    print(f"\n❌ {len(FAILS)} TESTÓW NIE PRZESZŁO: {FAILS}")
    sys.exit(1)
print("\n✅ WSZYSTKIE TESTY OK")
sys.exit(0)
