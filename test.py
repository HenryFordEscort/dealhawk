#!/usr/bin/env python3
"""Testy regresyjne DealHawk — bez sieci, bez Telegrama.
Uruchom: python test.py   (exit 1 gdy cokolwiek pęknie).
Chroni całą logikę przed cichym zepsuciem przy zmianach."""
import os
import sys
import re
import json
import time
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
# REGRESJA: cechy silnie skorelowane z istniejącymi psuły wycenę (generacja
# silnika dawała -6,4%, czyli nowszy silnik = taniej). Pilnujemy, żeby żaden
# współczynnik nie miał absurdalnego znaku.
check(_c["cechy"]["y"]["zmiana_ceny_pct"] > 0, "nowszy rocznik NIGDY nie obniża ceny")
check(_c["cechy"]["wh"]["zmiana_ceny_pct"] > 0, "większa bateria NIGDY nie obniża ceny")
check("gen" not in _c["cechy"], "generacja silnika świadomie NIE jest cechą cennika")

print("Audyt: błędy znalezione po wdrożeniu:")
import olx as _o
# BUG A: stary błąd przekaźnika przeżywał reset i /status kłamał po naprawie
_o._diag["przekaznik_status"] = 401
_o.olx_diag_reset()
check(_o.olx_diag().get("przekaznik_status") is None,
      "reset diagnostyki kasuje stary błąd przekaźnika (nie przykleja się)")
# BUG B: zbieraj_rynek.py omijał przekaźnik i dostałby 403 na runnerze
_zr = Path("zbieraj_rynek.py").read_text(encoding="utf-8")
check("from olx import olx_get" in _zr and "requests.get" not in _zr,
      "zbieraj_rynek.py pobiera przez przekaźnik, nie wprost")
# BUG C: Worker odrzucał apex 'olx.pl' — wyglądało jak blokada OLX-a
_w = Path("cloudflare_worker.js").read_text(encoding="utf-8")
check('host === "olx.pl"' in _w, "Worker przepuszcza też adres bez www")
# żadne inne miejsce nie może omijać wspólnego wejścia do OLX
for _plik in ("tracker.py", "dozorca.py", "zbieraj_rynek.py", "otomoto_tracker.py"):
    _t = Path(_plik).read_text(encoding="utf-8")
    _zle = [ln for ln in _t.splitlines()
            if "requests.get" in ln and "olx" in ln.lower()]
    check(not _zle, f"{_plik}: brak pobierania OLX z pominięciem olx_get")
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

print("Wycena przeżywa blokadę OLX (rynek czytany z repo):")
from tracker import oferty_z_rynku  # noqa
_rf = Path(tempfile.mkdtemp()) / "rynek.jsonl"
_dzis_s = tracker.date.today().isoformat()
_stare = (tracker.date.today() - tracker.timedelta(days=40)).isoformat()
_linie = [{"ts": _dzis_s, "url": f"https://www.olx.pl/d/oferta/cube-stereo-hybrid-{i}.html",
           "cena": 12000 + i * 100, "y": 2022, "wh": 625} for i in range(6)]
_linie.append({"ts": _stare, "url": "https://www.olx.pl/d/oferta/cube-stereo-hybrid-stary.html",
               "cena": 99999})                       # za stare — ma odpaść
_linie.append({"ts": _dzis_s, "url": "https://www.olx.pl/d/oferta/cube-stereo-hybrid-0.html",
               "cena": 11111, "y": 2022})            # ten sam URL nowszy = nadpisuje
_rf.write_text("\n".join(json.dumps(x) for x in _linie), encoding="utf-8")
tracker.RYNEK_FILE = _rf
tracker._rynek_cache = None
_zr = oferty_z_rynku("cube stereo hybrid")
check(len(_zr) == 6, f"czyta oferty z repo, odsiewa przeterminowane: {len(_zr)}")
check(all(o["cena"] != 99999 for o in _zr), "oferta sprzed 40 dni pominięta")
check(any(o["cena"] == 11111 for o in _zr), "ostatni zapis tego samego URL wygrywa")
tracker._rynek_cache = None
tracker.RYNEK_FILE = Path(tempfile.mkdtemp()) / "brak.jsonl"
check(oferty_z_rynku("cokolwiek") == [], "brak pliku rynku → pusta lista, bez wywrotki")
tracker._rynek_cache = None
tracker.RYNEK_FILE = tracker.Path("rynek_pl.jsonl")

print("Strona ZAKUPOWA korzysta z cennika (i nie liczy korekty dwa razy):")
from tracker import calc_profit, mileage_factor, year_factor, parse_spec_fields  # noqa
_sur = calc_profit(2000, 14000, km=3000, year=2018)                       # stara droga
_cen = calc_profit(2000, 14000, km=3000, year=2018, juz_skorygowana=True)  # z cennika
check(_cen > _sur, "cena z cennika NIE jest dodatkowo karana za rok/przebieg")
# Zysk liczy sie od tego, co DOSTANIESZ, a nie od tego, co wystawisz —
# Twoj kupujacy tez przyjedzie i tez bedzie zbijal (symetria z zakupem).
check(_cen == int(tracker.cena_sprzedazy_realna(14000)
                  - 2000 * tracker.get_eur_pln() - tracker.TRANSPORT_PLN),
      "z cennika: zysk = to co DOSTANIESZ − koszt DE − transport, bez mnożników")
check(abs(_sur - int(tracker.cena_sprzedazy_realna(
              14000 * mileage_factor(3000) * year_factor(2018))
          - 2000 * tracker.get_eur_pln() - tracker.TRANSPORT_PLN)) <= 1,
      "bez cennika: stare mnożniki dalej działają (zgodność wsteczna)")

print("\nRezerwacja: trzy odpowiedzi, nie dwie:")
# Uzytkownik zauwazyl "przeciez pierwsze zdjecie jest reserviert". Stempel na
# zdjeciu jest poza zasiegiem bez AI, ale Kleinanzeigen dokłada plakietke —
# widoczna TYLKO w ukladzie z galeria w JSON-LD. W drugim ukladzie nie ma o
# rezerwacji ani slowa, wiec brak plakietki NIE jest dowodem, ze rower wolny.
_JSONLD = '"representativeOfPage": true'
_PLAKIETKA = ('<div class="badge-unavailable"><i class="icon large '
              'icon-reserved-flag-light-gray"></i> Reserviert </div>')

check(tracker.czy_zarezerwowane(_JSONLD + _PLAKIETKA) is True,
      "plakietka przy galerii → zarezerwowany")
check(tracker.czy_zarezerwowane(_JSONLD) is False,
      "układ z plakietkami, plakietki brak → wolny")
# Przy NOWYM ogloszeniu rezerwacji nie ma po co pokazywac: strone czytamy raz,
# po ~4 min od wystawienia, a sprzedawcy rezerwuja godziny pozniej (zmierzone
# 23.08: 14/14 wlasnych powiadomien z tego dnia bylo niezarezerwowanych).
_zrodlo = open("tracker.py").read()
_nowe = _zrodlo.split("# Rezerwacji przy NOWYM")[1][:1400]
check("ZAREZERWOWANY" not in _nowe.split("def ")[0],
      "nowe ogłoszenie: zero linijek o rezerwacji")
check(_zrodlo.count("ZAREZERWOWANY") == 1 and "_meta_sw" in _zrodlo,
      "rezerwacja tylko tam, gdzie stronę czytamy ponownie (obniżka ceny)")
check(tracker.czy_zarezerwowane("<div id='viewad-price'>100 €</div>") is None,
      "układ bez plakietek → 'nie wiem', a nie 'wolny'")
check(tracker.czy_zarezerwowane("", "Cube Stereo RESERVIERT", "") is True,
      "sprzedawca napisał to w tytule")
check(tracker.czy_zarezerwowane("", "", "Bike ist reserviert") is True,
      "sprzedawca napisał to w opisie")
check(tracker.czy_zarezerwowane(_JSONLD, "", "Noch nicht reserviert!") is False,
      "'nicht reserviert' znaczy coś odwrotnego")
check(tracker.czy_zarezerwowane(_JSONLD, "", "keine Reservierung möglich") is False,
      "'keine Reservierung' to nie rezerwacja")

print("\nTempo adaptacyjne: gęściej, dopóki serwis znosi:")
_T = tracker.tempo_po_skanie

# Czyste skany schodza w dol krok po kroku, ale nie ponizej dna
_s = {"tempo_s": tracker.TEMPO_START_S, "tempo_dno": tracker.TEMPO_DNO_S}
for _ in range(20):
    _s = _T(False, _s)
check(_s["tempo_s"] == tracker.TEMPO_DNO_S, "po serii czystych skanów siadamy na dnie")
check(_s["tempo_s"] < tracker.TEMPO_MAX_S, "dno jest gęstsze niż dzisiejsza produkcja")

# Wpadka = natychmiastowy odwrot na pelny odstep, bez schodzenia po stopniach
_po = _T(True, {"tempo_s": 150, "tempo_dno": 150, "tempo_karencja": 0})
check(_po["tempo_s"] == tracker.TEMPO_MAX_S, "strona-śmieć → od razu pełny odstęp")
check(_po["tempo_karencja"] == tracker.TEMPO_KARENCJA, "po wpadce długa karencja")

# Karencja trzyma pelny odstep, mimo ze skany sa czyste
_k = _po
for i in range(tracker.TEMPO_KARENCJA):
    check(_k["tempo_s"] == tracker.TEMPO_MAX_S, f"karencja trzyma tempo (skan {i+1})")
    _k = _T(False, _k)
check(_k["tempo_karencja"] == 0, "karencja się wyczerpuje")
check(_T(False, _k)["tempo_s"] < tracker.TEMPO_MAX_S,
      "po karencji wolno znowu przyspieszać")

# Wpadka PRZY DNIE podnosi dno — bot uczy sie, gdzie naprawde jest sciana
_d = _T(True, {"tempo_s": 150, "tempo_dno": 150, "tempo_karencja": 0})
check(_d["tempo_dno"] == 180, "wpadka przy dnie podnosi dno (nauka, nie powtórka)")
_d2 = _T(True, {"tempo_s": 300, "tempo_dno": 150, "tempo_karencja": 0})
check(_d2["tempo_dno"] == 150, "wpadka przy pełnym odstępie NIE podnosi dna")

# Dno nigdy nie przebije sufitu — najgorszy przypadek to dzisiejsze tempo
_w = {"tempo_s": 150, "tempo_dno": 150, "tempo_karencja": 0}
for _ in range(30):
    _w = _T(True, {**_w, "tempo_s": _w["tempo_dno"]})
check(_w["tempo_dno"] == tracker.TEMPO_MAX_S,
      "przy ciągłych wpadkach dno dochodzi do dzisiejszej produkcji i staje")
check(_w["tempo_s"] <= tracker.TEMPO_MAX_S, "nigdy nie schodzimy PONIŻEJ dzisiejszego tempa")

# Jedna podstawiona polka wystarczy, zeby zwolnic — nie czekamy na obie
check(_T(True, {"tempo_s": 150})["tempo_s"] == tracker.TEMPO_MAX_S,
      "sygnał to każda podstawiona półka, nie dopiero obie")

print("\nCicha zmiana układu strony ma krzyczeć, a nie milczeć:")
# 23.08 zmienilo sie id ceny i miejsce zdjec. Bot czytal dalej opis, wiec zadna
# awaria sie nie zglosila — album byl po prostu pusty. To ma sie nie powtorzyc.
def _licznik(czytane, zdj, cena):
    return {"czytane": czytane, "ze_zdjeciami": zdj, "z_cena": cena}

tracker._problemy.clear()
tracker.sprawdz_uklad(_licznik(12, 0, 12))
check("uklad" in tracker._problemy, "zero zdjęć na wszystkich stronach = awaria układu")

tracker._problemy.clear()
tracker.sprawdz_uklad(_licznik(12, 12, 0))
check("uklad" in tracker._problemy, "zero cen na wszystkich stronach = awaria układu")

tracker._problemy.clear()
tracker.sprawdz_uklad(_licznik(12, 3, 12))
check(tracker._problemy == [], "część ogłoszeń bez zdjęć to norma, nie awaria")

tracker._problemy.clear()
tracker.sprawdz_uklad(_licznik(2, 0, 0))
check(tracker._problemy == [], "przy dwóch odczytach cisza nic nie znaczy")

_t = tracker.opisz_awarie(["uklad"])
check("zmieniły wygląd" in _t and "przychodzą normalnie" in _t,
      "alarm o układzie mówi, że rowery i tak idą")
check("data-imgsrc" not in _t and "id=" not in _t, "zero żargonu w alarmie o układzie")
tracker._problemy.clear()

print("\nNieprzeczytana strona NIE JEST faktem 'brak danych':")
from datetime import datetime as _dt, timedelta as _td

_TER = _dt(2026, 8, 23, 12, 0, tzinfo=tracker.TZ_DE)
_OGL = {"id": "111", "title": "Cube Stereo Hybrid", "price": "3.200 €",
        "price_num": 3200, "url": "https://www.kleinanzeigen.de/s-anzeige/x/111-217-1",
        "foto": "https://img/1.jpg"}

# 1. Nieudany odczyt zapisuje sie BEZ oceny — inaczej bot uznalby awarie
#    za wiedze o rowerze i puscil zlom dalej (Cannondale Moterra, 10 328 km).
_s = {}
_n = tracker.zapisz_nieodczytane(_s, _OGL, None, "blad", "2026-08-23", "kanał e-bike", _TER)
check(_n == 1, "pierwszy nieudany odczyt = podejście 1")
check(_s["111"].get("score") is None, "nieudany odczyt nie zapisuje oceny")
check(_s["111"].get("mileage") is None, "nieudany odczyt nie zapisuje przebiegu")
check(_s["111"]["url"] == _OGL["url"], "zapamiętany adres — jest po czym wrócić")
check(_s["111"]["search"] == "kanał e-bike", "zapamiętane źródło (dziedziczy medianę)")

# 2. Kolejne podejscia licza sie dalej, a czas pierwszej proby sie nie przesuwa
_n2 = tracker.zapisz_nieodczytane(_s, _OGL, _s["111"], "blad", "2026-08-23",
                                  "kanał e-bike", _TER + _td(minutes=5))
check(_n2 == 2, "drugie podejście liczone")
check(_s["111"]["od"] == _TER.isoformat(), "czas pierwszej próby nieruchomy")

# 3. Ogloszenie zdjete to FAKT, nie awaria — nie ma czego czytac ani kupowac
_s2 = {}
check(tracker.zapisz_nieodczytane(_s2, _OGL, None, "usuniete", "2026-08-23") is None,
      "zdjęte ogłoszenie nie trafia do kolejki")
check("nieodczytane" not in _s2["111"], "zdjęte ogłoszenie zamknięte na amen")

# 4. Kolejka: co wraca, a co odpada
_baza = {"title": "X", "price_num": 3000,
         "url": "https://www.kleinanzeigen.de/s-anzeige/x/9-217-1"}
_seen = {
    "swieze":    dict(_baza, nieodczytane=1, od=(_TER - _td(minutes=5)).isoformat()),
    "starsze":   dict(_baza, nieodczytane=2, od=(_TER - _td(hours=3)).isoformat()),
    "wyczerp":   dict(_baza, nieodczytane=tracker.ODCZYT_PODEJSC,
                      od=(_TER - _td(hours=1)).isoformat()),
    "przedawn":  dict(_baza, nieodczytane=1,
                      od=(_TER - _td(hours=tracker.ODCZYT_WAZNE_H + 1)).isoformat()),
    "bez_url":   {"title": "X", "nieodczytane": 1, "od": _TER.isoformat()},
    "normalne":  {"date": "2026-08-23", "score": 70},
}
_kol = tracker.do_odczytania(_seen, _TER)
_ids = [i for i, _ in _kol]
check(_ids == ["swieze", "starsze"], f"kolejka = tylko zaległe, świeższe pierwsze ({_ids})")
check("wyczerp" not in _ids, "po ODCZYT_PODEJSC próbach odpuszczamy")
check("przedawn" not in _ids, "po ODCZYT_WAZNE_H rower i tak nieświeży")
check("bez_url" not in _ids, "bez adresu nie ma po co wracać")
check("normalne" not in _ids, "przeczytane ogłoszenia nie wracają")

# 5. Budzet ruchu: kolejka nie moze wypchnac biezacego skanu
_duzo = {str(i): dict(_baza, nieodczytane=1,
                      od=(_TER - _td(minutes=i)).isoformat()) for i in range(30)}
check(len(tracker.do_odczytania(_duzo, _TER)) == tracker.ODCZYT_NA_SKAN,
      f"kolejka przycięta do {tracker.ODCZYT_NA_SKAN} na skan")

# 6. Wpis wraca w ksztalcie ogloszenia, z adresem i cena
_OGL2 = dict(_OGL, loc="01067 Dresden", posted=_TER - _td(minutes=12))
_s3 = {}
tracker.zapisz_nieodczytane(_s3, _OGL2, None, "blad", "2026-08-23", "kanał e-bike", _TER)
_o = tracker.wpis_jako_ogloszenie("111", _s3["111"])
check(_o["url"] == _OGL["url"] and _o["price_num"] == 3200 and _o["id"] == "111",
      "zaległy wpis wraca jako pełne ogłoszenie")
check(_o["loc"] == "01067 Dresden", "region ocalały — nie zgubimy, gdzie to jest")
check(_o["posted"] == _OGL2["posted"],
      "czas wystawienia ocalał — ponowienie nie kłamie, że 'nie podano'")
check(_o["age_min"] is not None and _o["age_min"] >= 12,
      "wiek liczony na nowo przy podejściu, a nie zamrożony")

# 7. fetch_listing_details rozroznia "nie ma" od "nie dalo sie"
class _Odp:
    def __init__(self, kod, tekst=""):
        self.status_code, self.text, self.encoding = kod, tekst, "utf-8"
    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")

class _Scraper:
    def __init__(self, odp): self.odp, self.ile = odp, 0
    def get(self, *a, **k):
        self.ile += 1
        if isinstance(self.odp, Exception): raise self.odp
        return self.odp

_prawdziwy = tracker.scraper
_stary_sleep = tracker.time.sleep
tracker.time.sleep = lambda *_: None
try:
    tracker.scraper = _Scraper(_Odp(404))
    check(tracker.fetch_listing_details("http://x", "T")[4] == "usuniete",
          "404 → ogłoszenie zdjęte")
    check(tracker.scraper.ile == 1, "zdjętego ogłoszenia nie dobijamy próbami")

    tracker.scraper = _Scraper(TimeoutError("padło"))
    _wynik = tracker.fetch_listing_details("http://x", "T")
    check(_wynik[4] == "blad", "wyjątek sieci → błąd odczytu, nie 'brak danych'")
    check(_wynik[1] is None, "przy błędzie opis to None, nie pusty tekst")
    check(tracker.scraper.ile == tracker.ODCZYT_PROBY,
          f"{tracker.ODCZYT_PROBY} próby w jednym podejściu")

    # Strona przejsciowa ("zbyt wiele zadan") ma HTTP 200 i zero tresci —
    # bez tego sprawdzenia wygladalaby jak rower bez opisu i cicho przepadla.
    tracker.scraper = _Scraper(_Odp(200, "<html><body>Zu viele Anfragen</body></html>"))
    check(tracker.fetch_listing_details("http://x", "T")[4] == "blad",
          "strona 200 bez opisu i bez ceny → błąd, nie pusty opis")

    # Zdjete ogloszenie oddaje HTTP 200 i strone kategorii (sprawdzone na zywym
    # przykladzie 23.08), wiec po samym kodzie odpowiedzi nie da sie go poznac.
    tracker.scraper = _Scraper(_Odp(200,
        "<html><body>Diese Anzeige ist nicht mehr verfügbar</body></html>"))
    check(tracker.fetch_listing_details("http://x", "T")[4] == "usuniete",
          "'nicht mehr verfügbar' przy HTTP 200 → zdjęte, nie awaria")
    check(tracker.scraper.ile == 1, "zdjętego ogłoszenia nie ponawiamy 3 razy")

    # Nowy uklad strony (id="vip-ad-price", galeria jako tlo) — zmierzony
    # na zywo 23.08. Na nim odczyt ceny i album zdjec milczaly po cichu.
    _nowy = ('<div id="vip-ad-price">3.200 €</div>'
             '<p id="viewad-description-text">Bosch, 900 km gefahren</p>'
             "<div class=\"galleryimage-large--cover x\" style=\"background-image: "
             "url('https://img.kleinanzeigen.de/api/v1/prod-ads/images/ab/"
             "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee');\"></div>")
    tracker.scraper = _Scraper(_Odp(200, _nowy))
    _w2 = tracker.fetch_listing_details("http://x", "T")
    check(_w2[4] == "ok" and _w2[0] == "900 km", "nowy układ strony → czytany")
    check(_w2[2] == "3.200 €", "cena z nowego układu (id vip-ad-price)")
    check(len(_w2[3]) == 1, "album zdjęć z nowego układu (galeria jako tło)")

    # Cudze rowery z "podobnych ogloszen" nie moga wpasc do albumu
    _obce = _nowy + ('<div class="imagebox srpimagebox"><img src='
                     '"https://img.kleinanzeigen.de/api/v1/prod-ads/images/99/'
                     '11111111-2222-3333-4444-555555555555?rule=$_2.AUTO"></div>')
    tracker.scraper = _Scraper(_Odp(200, _obce))
    check(len(tracker.fetch_listing_details("http://x", "T")[3]) == 1,
          "zdjęcia z 'podobnych ogłoszeń' nie wchodzą do albumu")

    # Trzeci uklad, tego dnia przewazajacy: zdjecia tylko w JSON-LD.
    _json = ('<div id="vip-ad-price">2.000 €</div>'
             '<p id="viewad-description-text">Bosch, 500 km gefahren</p>'
             '<script>{"@type": "ImageObject", "contentUrl": '
             '"https://img.kleinanzeigen.de/api/v1/prod-ads/images/aa/'
             'aaaaaaaa-1111-2222-3333-444444444444?rule=$_59.AUTO", '
             '"representativeOfPage": true}</script>'
             '<script>{"@type": "ImageObject", "contentUrl": '
             '"https://img.kleinanzeigen.de/api/v1/prod-ads/images/bb/'
             'bbbbbbbb-1111-2222-3333-444444444444?rule=$_59.AUTO", '
             '"representativeOfPage": false}</script>')
    tracker.scraper = _Scraper(_Odp(200, _json))
    check(len(tracker.fetch_listing_details("http://x", "T")[3]) == 2,
          "album zdjęć z JSON-LD (układ bez data-imgsrc)")

    # Sekcja "podobne ogloszenia" na dole to CUDZE rowery — nie moga wejsc
    _obce_json = _json + ('<div class="imagebox srpimagebox">'
                          '<script>{"@type": "ImageObject", "contentUrl": '
                          '"https://img.kleinanzeigen.de/api/v1/prod-ads/images/cc/'
                          'cccccccc-1111-2222-3333-444444444444", '
                          '"representativeOfPage": false}</script></div>')
    check(len(tracker.galeria_ze_strony(_obce_json)) == 2,
          "cudze rowery z 'podobnych ogłoszeń' odcięte także w JSON-LD")

    _ok = ('<div id="viewad-price">3.200 €</div>'
           '<p id="viewad-description-text">Bosch Motor, 1100 km gefahren</p>')
    tracker.scraper = _Scraper(_Odp(200, _ok))
    _w = tracker.fetch_listing_details("http://x", "T")
    check(_w[4] == "ok" and _w[0] == "1.100 km", "zdrowa strona → 'ok' i przebieg")
finally:
    tracker.scraper = _prawdziwy
    tracker.time.sleep = _stary_sleep

print("\nPrzebieg: nigdy 'brak danych', gdy stoi w opisie (audyt 23.08):")
# Wszystkie przypadki z zywych ogloszen. Zasada: brak danych wolno zapisac
# TYLKO wtedy, gdy w opisie faktycznie nie ma przebiegu.
for _opis, _ocz in [
        ("Super Zustand und hat 1100km Bremsbeläge bei 1000km gewechselt", "1.100 km"),
        ("Das Bike hat nur 188km auf dem Buckel", "188 km"),
        ("Modelljahr 2022 mit 113 km in der Farbe Crimson", "113 km"),
        ("Motor: Bosch Active Line Km: 5250km Akku: original 400er", "5.250 km"),
        ("9096 km gefahren", "9.096 km"),
        ("Laufleistung 10.328 km", "10.328 km"),
        ("Km Stand 1519km", "1.519 km"),
        ("insgesamt 2337 km gefahren", "2.337 km"),
        ("5467km Gesamtlaufleistung Akku 500W Reichweite ca.120Km", "5.467 km")]:
    check(tracker._extract_mileage("E-Bike", _opis) == _ocz,
          f"znajduje przebieg: {_opis[:40]}")

# …i nigdy nie zmysla z zasiegu
for _opis in ["Volle Ladung für 100 km",
              "kann eine Fahrt von 100 km unterstützen",
              "Reichweite ca. 120 km",
              "190km auf Eco und 73 auf Turbo",
              "bis zu 90 km mit einer Akkuladung",
              "Reichweite bis 120 km im Eco Modus"]:
    check(tracker._extract_mileage("E-Bike", _opis) == "brak danych",
          f"NIE bierze zasięgu za przebieg: {_opis[:36]}")

check(tracker._extract_mileage("E-Bike Cube", "Guter Zustand, wenig benutzt.") == "brak danych",
      "gdy przebiegu naprawdę nie ma → uczciwe 'brak danych'")

print("\nDwa ogłoszenia, które nie powinny były przyjść (zgłoszone 23.08):")
# 1. Scott Ramson 600 — zwykły rower 26", przeszedł jako "elektryk", bo wzorzec
#    "e fully" nie miał granicy słowa i łapał się na "MountainbikE FULLY".
for _t in ["Scott Ramson 600 Mountainbike Fully 26zoll",
           "Verkaufe Bike Cube Fully",
           "Meine Bike Fully 29",
           "Trek Fully Mountainbike 29 Zoll"]:
    check(not tracker.is_electric(_t), f"NIE elektryk: {_t[:34]}")
# …ale prawdziwe elektryki muszą przechodzić dalej
for _t in ["Cannondale Moterra E-Bike Fully Größe M Shimano EP6",
           "Cube Stereo E Fully 750Wh", "Haibike E-Fully SDURO",
           "Scott Genius eRide 920", "E Bike Fully Haibike",
           "Trek Rail 9.8 Bosch", "KTM Macina Kapoho 625 Wh"]:
    check(tracker.is_electric(_t), f"elektryk: {_t[:34]}")
check(tracker.is_electric("Scott Genius eRide 920"),
      "eRide bez łącznika też jest elektrykiem (tak brandujе Scott)")

# 2. Cannondale Moterra — w opisie "10.328 km", a bot zapisał "brak danych"
#    i puścił dalej, bo model językowy zwrócił null i wyłączył wzorce.
_llm = tracker.llm_extract_mileage
try:
    tracker.llm_extract_mileage = lambda t, d: ("ok", None)      # model: "nie wiem"
    _opis = "Fully in gut gebrauchtem Zustand. Laufleistung 10.328 km. Bosch CX."
    check(tracker._extract_mileage("Cannondale Moterra", _opis) == "10.328 km",
          "wzorzec znajduje przebieg, którego model nie zwrócił")
finally:
    tracker.llm_extract_mileage = _llm
check(tracker.is_too_worn(10328), "10 tys. km to złom — filtr musi go odrzucić")
check(not tracker.is_too_worn(None),
      "brak przebiegu NADAL przepuszcza (świadomie) — dlatego 'nie wiem' modelu było groźne")

print("\nSymetria negocjacji (uwaga użytkownika 23.08 — to działa w dwie strony):")
from tracker import cena_sprzedazy_realna as _csr, NEGO_NA_MIEJSCU  # noqa: E402
check(_csr(10000) == int(10000 * (1 - NEGO_NA_MIEJSCU)),
      "od ceny wystawienia odchodzi tyle samo, ile utargujesz przy zakupie")
check(_csr(10000) < 10000, "nie dostajesz tyle, ile wystawiasz")
check(_csr(None) is None and _csr(0) is None, "brak ceny → brak wyniku")
_bez = int(14000 - 2000 * tracker.get_eur_pln() - tracker.TRANSPORT_PLN)
check(_cen < _bez, "zysk po symetrii jest NIŻSZY niż przy optymizmie dwustronnym")
check(_bez - _cen == int(14000 * NEGO_NA_MIEJSCU),
      "różnica to dokładnie targ kupującego, nic więcej")
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

# Diagnoza zostaje w LOGU; do użytkownika idzie jedno proste zdanie z bramki,
# i to dopiero po godzinie awarii — stąd tu sprawdzamy ciszę, nie treść.
_sent = []
tracker.send_telegram = lambda t: _sent.append(t)          # bez sieci
tracker.PARSE_STATE_FILE = Path(tempfile.mkdtemp()) / "ph.json"  # bez brudzenia repo
tracker._problemy.clear()
check_feed_health(_st(503, 10), 0)
check(_sent == [], "pusty skan NIE pisze od razu na Telegram")
check(tracker._problemy == ["slepy"], "pusty skan → zgłoszony do bramki")
check_feed_health(_st(503, 10), 0)
check(_sent == [] and tracker._problemy == ["slepy"], "drugi pusty skan: cisza, bez dublowania")
tracker._problemy.clear()
check_feed_health(_st(200, 10), 42)
check(_sent == [] and tracker._problemy == [], "normalna praca → cisza i zero zgłoszeń")

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
# widełki cen konfigurowalne — auta kosztują więcej niż rowery
_auto = ('data-cy="l-card" id="9"><a href="/d/oferta/audi-CID767-ID9.html"><h4>Audi</h4></a>'
         '<p data-testid="ad-price">145 000 zł</p>')
check(parse_olx_cards(_auto) == [], "145 000 zł odpada przy domyślnych (rowerowych) widełkach")
check(len(parse_olx_cards(_auto, 3000, 300000)) == 1, "to samo przechodzi przy widełkach dla aut")

print("Przekaźnik OLX (Cloudflare) i rozróżnianie awarii:")
from tracker import przekaznik_zyje, diagnoza_dostepu_olx  # noqa
check(przekaznik_zyje() is None, "bez skonfigurowanego przekaźnika → None (pytamy wprost)")
_bez = diagnoza_dostepu_olx(403, 0, 0)
check("nie ustawiono przekaźnika" in _bez.lower() or "nie wpuszcza" in _bez,
      "bez przekaźnika: alarm o blokadzie serwerowni")
tracker.OLX_RELAY_URL = "https://przyklad.workers.dev"
try:
    tracker.przekaznik_zyje = lambda: False
    _d = tracker.diagnoza_dostepu_olx(0, 0, 0)
    check("padł przekaźnik" in _d, "padły Worker → alarm o Cloudflare, nie o OLX")
    check("dash.cloudflare.com" in _d, "alarm mówi, gdzie zajrzeć")
    tracker.przekaznik_zyje = lambda: True
    import olx as _olx
    _olx._diag["przekaznik_status"] = 401
    check("ZŁY KLUCZ" in tracker.diagnoza_dostepu_olx(None, 0, 0),
          "odmowa 401 → alarm o niezgodnym kluczu")
    _olx._diag["przekaznik_status"] = 429
    _lim = tracker.diagnoza_dostepu_olx(None, 0, 0)
    check("LIMIT" in _lim and "wymień go" in _lim,
          "odmowa 429 → alarm o limicie I podejrzeniu wycieku klucza")
    _olx._diag["przekaznik_status"] = 503
    check("BRAK KLUCZA" in tracker.diagnoza_dostepu_olx(None, 0, 0),
          "odmowa 503 → Worker bez skonfigurowanego klucza")
    _olx._diag.pop("przekaznik_status", None)
    check("MIMO przekaźnika" in tracker.diagnoza_dostepu_olx(403, 0, 5000),
          "403 mimo działającego Workera → alarm, że i on jest blokowany")
finally:
    tracker.OLX_RELAY_URL = ""
    tracker.przekaznik_zyje = przekaznik_zyje

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

print("Monitoring OLX (cicha blokada nie może przejść niezauważona):")
from olx import olx_get, olx_diag, olx_diag_reset, OLX_HEADERS  # noqa
from tracker import alarm_olx_martwy  # noqa
check("Chrome/" in OLX_HEADERS["User-Agent"] and "Sec-Fetch-Mode" in OLX_HEADERS,
      "pełne nagłówki przeglądarki zamiast gołego 'Mozilla/5.0'")


class _Odp:
    def __init__(self, s, t=""):
        self.status_code, self.text = s, t


_zapisane = []
tracker.send_telegram = lambda t: _zapisane.append(t)
olx_diag_reset()
_stary_get = tracker.requests.get
tracker.requests.get = lambda *a, **k: _Odp(403)
try:
    olx_get("https://www.olx.pl/x")
    olx_get("https://www.olx.pl/y")
finally:
    tracker.requests.get = _stary_get
_d = olx_diag()
check(_d["zapytan"] == 2 and _d["ok"] == 0 and _d["statusy"].get("403") == 2,
      "blokady (403) policzone, zero udanych")
# Od 22.08 alarm NIE idzie prosto na Telegram — trafia do wspólnej bramki,
# która milczy, dopóki awaria nie utrzyma się przez godzinę.
tracker._problemy.clear()
alarm_olx_martwy("test")
check(_zapisane == [], "awaria OLX nie pisze od razu na Telegram")
check(tracker._problemy == ["olx"], "awaria OLX odnotowana dla bramki")
# 403 NIE może zostać uznane za śmierć oferty (to blokada, nie sprzedaż!)
tracker.requests.get = lambda *a, **k: _Odp(403)
try:
    check(tracker.olx_offer_gone("https://www.olx.pl/d/oferta/x") is None,
          "403 = 'nie wiadomo', NIGDY 'oferta sprzedana'")
    tracker.requests.get = lambda *a, **k: _Odp(404)
    check(tracker.olx_offer_gone("https://www.olx.pl/d/oferta/x") is True, "404 = oferta martwa")
finally:
    tracker.requests.get = _stary_get
olx_diag_reset()

print("Kanarek OLX (blokada widoczna w 5 minut, nie po 11 dniach):")
from tracker import olx_kanarek  # noqa
_kanar = []
tracker.send_telegram = lambda t: _kanar.append(t)
tracker.PARSE_STATE_FILE = Path(tempfile.mkdtemp()) / "ph2.json"
_KARTA = ('data-cy="l-card" id="1"><a href="/d/oferta/x-CID767-ID1.html"><h4>Rower</h4></a>'
          '<p data-testid="ad-price">9 100 zł</p>')


class _Blok:
    status_code, text = 403, ""


class _Ok:
    status_code, text = 200, _KARTA * 6


class _OkApi:            # kanarek pyta LEKKIE API, nie ciężkiej strony
    status_code, text = 200, '{"data":[1,2,3,4,5]}'

    def json(self):
        return {"data": [1, 2, 3, 4, 5]}


_prawdziwy_get = tracker.olx_get
try:
    tracker.olx_get = lambda *a, **k: _Blok()
    olx_kanarek()
    check(len(_kanar) == 0, "PIERWSZA wpadka → CISZA (to zwykle timeout, nie awaria)")
    olx_kanarek()
    tracker._problemy.clear()
    olx_kanarek()
    check(len(_kanar) == 0, "druga wpadka NIE pisze prosto na Telegram")
    check(tracker._problemy == ["olx"], "druga wpadka → zgłoszona do bramki")
    olx_kanarek()
    check(len(_kanar) == 0 and tracker._problemy == ["olx"],
          "awaria trwa → wciąż cisza, zgłoszenie się nie dubluje")
    tracker.olx_get = lambda *a, **k: _OkApi()
    tracker._problemy.clear()
    olx_kanarek()
    check(len(_kanar) == 0 and tracker._problemy == [],
          "powrót OLX-u → cisza; o końcu awarii decyduje bramka, nie kanarek")
    check(json.loads(tracker.PARSE_STATE_FILE.read_text())["olx"]["kart"] == 5,
          "stan zapisany w parser_health.json (widoczny bez logów runnera)")
    # awaria połączenia to NIE odmowa przekaźnika — komunikat musi to rozróżnić
    tracker.OLX_RELAY_URL = "https://x.workers.dev"
    tracker.przekaznik_zyje = lambda: True
    import olx as _ox
    _ox._diag.pop("przekaznik_status", None)
    _ox._diag["ostatni_wyjatek"] = "ReadTimeout"
    _msg = tracker.diagnoza_dostepu_olx(None, 0, 0)
    check("nie odpowiedział w czasie" in _msg and "ReadTimeout" in _msg,
          "timeout opisany jako timeout, nie jako 'kod odmowy: None'")
finally:
    tracker.olx_get = _prawdziwy_get
    tracker.OLX_RELAY_URL = ""
    tracker.przekaznik_zyje = przekaznik_zyje

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

# === WIEK OGŁOSZENIA =======================================================
# Regresja po Scott Ransome (22.08.2026): ogłoszenie z 00:41 przyszło o 17:31
# i wyglądało w Telegramie jak świeże. Data jest w karcie — ma być czytana.
print("\nWiek ogłoszenia:")
from datetime import datetime as _dt, timedelta as _td  # noqa: E402
from tracker import (  # noqa: E402
    parse_ad_time, ad_age_minutes, format_age, AD_TIME_PATTERN, TZ_DE, SWIEZOSC_MIN,
)

_teraz = _dt(2026, 8, 22, 17, 31, tzinfo=TZ_DE)
check(parse_ad_time("Heute, 00:41", _teraz) == _dt(2026, 8, 22, 0, 41, tzinfo=TZ_DE),
      "'Heute, 00:41' → dzisiaj 00:41")
check(parse_ad_time("Gestern, 18:12", _teraz) == _dt(2026, 8, 21, 18, 12, tzinfo=TZ_DE),
      "'Gestern, 18:12' → wczoraj")
check(parse_ad_time("21.08.2026", _teraz) == _dt(2026, 8, 21, 0, 0, tzinfo=TZ_DE),
      "sama data → północ")
check(parse_ad_time("", _teraz) is None, "pusty czas → None")
check(parse_ad_time("Top-Anzeige", _teraz) is None, "Top-Anzeige bez daty → None")
check(parse_ad_time("Heute, 99:99", _teraz) is None, "bzdurna godzina → None, bez wywrotki")
check(parse_ad_time("32.13.2026", _teraz) is None, "nieistniejąca data → None")

# ten sam HTML, który przychodzi z Kleinanzeigen (ikonka w środku diva)
_karta = ('data-adid="3491053472"><div class="aditem-main--top--right">'
          '<i class="icon icon-small icon-calendar-open" aria-hidden="true"></i>'
          '\n Heute, 00:41\n </div>')
_m = AD_TIME_PATTERN.search(_karta)
check(_m is not None, "wzorzec łapie div z datą")
check(parse_ad_time(_m.group(1), _teraz) == _dt(2026, 8, 22, 0, 41, tzinfo=TZ_DE),
      "data odczytana z prawdziwej karty (ikonka w środku)")

_wyst = _dt(2026, 8, 22, 0, 41, tzinfo=TZ_DE)
check(round(ad_age_minutes(_wyst, _teraz)) == 1010, "Scott Ransome: 16 h 50 min opóźnienia")
check(ad_age_minutes(None, _teraz) is None, "brak czasu → brak wieku")
check(ad_age_minutes(_wyst, _teraz) > SWIEZOSC_MIN, "16 h to NIE jest świeże ogłoszenie")
check(ad_age_minutes(_dt(2026, 8, 22, 17, 28, tzinfo=TZ_DE), _teraz) <= SWIEZOSC_MIN,
      "3 min = świeże (typowy cykl skanu)")

check(format_age(None) == "nie podano", "brak czasu opisany wprost")
check(format_age(3) == "3 min temu", "minuty")
check(format_age(1010) == "16 h 50 min temu", "godziny i minuty")
check(format_age(120) == "2 h temu", "równe godziny bez '0 min'")
check(format_age(4320) == "3 dni temu", "powyżej 2 dni w dniach")
check(format_age(-2) == "przed chwilą", "rozjechany zegar nie daje ujemnego wieku")

# === KANAŁ KATEGORII =======================================================
# To on odpowiada za czas reakcji. Musi cofać się dokładnie tak daleko,
# jak sięga przerwa, i głośno przyznać się, gdy nie domknął luki.
print("\nKanał kategorii (chodzenie wstecz):")
from tracker import fetch_feed, cena_w_widelkach, wraca_po_przecenie, FEED_MAX_STRON  # noqa: E402

_BAZA = _dt(2026, 8, 22, 20, 0, tzinfo=TZ_DE)


def _strony_co_5min(n_stron=30):
    """Udaje Kleinanzeigen: strona n to 5 minut ogłoszeń, coraz starszych."""
    def fake(search):
        n = int(search["name"].rsplit(".", 1)[1])
        if n > n_stron:
            return [], {"blocks": 0, "title_hits": 0, "price_hits": 0, "time_hits": 0,
                        "html": None, "status": 200}
        karty = []
        for i in range(5):
            t = _BAZA - _td(minutes=(n - 1) * 5 + i)
            karty.append({"id": f"{n}_{i}", "title": "E-MTB Fully", "price": "1500 €",
                          "price_num": 1500, "loc": None, "posted": t,
                          "age_min": (_BAZA - t).total_seconds() / 60, "url": "u"})
        return karty, {"blocks": 5, "title_hits": 5, "price_hits": 5, "time_hits": 5,
                       "html": None, "status": 200}
    return fake


_orig_fetch = tracker.fetch_listings
try:
    tracker.fetch_listings = _strony_co_5min()
    _l, _s, _ok = fetch_feed(None)
    check(_s["stron"] == 1 and _ok, "pierwszy bieg bierze tylko stronę 1 (bez zaciągania historii)")

    _l, _s, _ok = fetch_feed(_BAZA - _td(minutes=12))
    check(_ok, "przerwa 12 min → luka domknięta")
    check(_s["stron"] == 4, "przerwa 12 min (+3 min marginesu) → 4 strony")
    check(len(_l) == len(set(x["id"] for x in _l)), "brak duplikatów między stronami")
    check(_s["najnowsze"] == _BAZA, "znacznik = czas najnowszego ogłoszenia")

    _l, _s, _ok = fetch_feed(_BAZA - _td(minutes=90))
    check(_s["stron"] == FEED_MAX_STRON and not _ok,
          "przerwa większa niż limit stron → dosiegl=False, czyli ALARM")

    # awaria: strona bez dat = ślepe cofanie, lepiej stanąć niż udawać
    tracker.fetch_listings = lambda s: ([{"id": "x", "title": "t", "price": "1 €",
                                          "price_num": 1, "loc": None, "posted": None,
                                          "age_min": None, "url": "u"}],
                                        {"blocks": 1, "title_hits": 1, "price_hits": 1,
                                         "time_hits": 0, "html": None, "status": 200})
    _l, _s, _ok = fetch_feed(_BAZA - _td(minutes=90))
    check(_s["stron"] == 1 and not _ok, "strona bez dat przerywa cofanie i nie udaje sukcesu")

    # PODSTAWIONA STRONA: HTTP 200, właściwy adres, ale losowe stare ogłoszenia
    # bez dat. Wygląda jak zdrowy rynek — i to jest w niej najgroźniejsze.
    _smiec = [{"id": f"stare{i}", "title": "E-Bike Fully", "price": "1500 €",
               "price_num": 1500, "loc": None, "posted": None, "age_min": None,
               "url": "u"} for i in range(9)]
    tracker.fetch_listings = lambda s: (_smiec,
                                        {"blocks": 9, "title_hits": 9, "price_hits": 9,
                                         "time_hits": 0, "html": None, "status": 200})
    check(tracker.strona_zepsuta({"blocks": 9, "time_hits": 0}), "9 kafelków bez dat = śmieć")
    check(not tracker.strona_zepsuta({"blocks": 9, "time_hits": 7}), "kafelki z datami = zdrowa strona")
    check(not tracker.strona_zepsuta({"blocks": 2, "time_hits": 0}), "2 kafelki bez dat to za mało, by orzekać")
    _l, _s, _ok = fetch_feed(_BAZA - _td(minutes=12))
    check(_s["zepsuty"] and not _ok, "podstawiona strona → skan NIEUDANY")
    check(_l == [], "ze śmiecia nie bierzemy ANI JEDNEGO ogłoszenia")
    check(_s["najnowsze"] is None, "znacznik nietknięty → następny bieg przeczyta to okno jeszcze raz")
finally:
    tracker.fetch_listings = _orig_fetch

print("\nTarg na miejscu jako DRUGI etap (10% z poradników, nie z pomiaru):")
from tracker import (cena_po_ogledzinach as _cpo, realistic_buy_price as _rbp,  # noqa: E402
                     NEGO_NA_MIEJSCU, NEGO_MAX_LACZNIE)

_c, _l = _cpo(2000, 0.10)
check(_c < 2000 * 0.90, "targ na miejscu schodzi PONIŻEJ tego, co ustalone zdalnie")
check(abs(_l - (1 - 0.9 * 0.9)) < 0.001,
      "etapy składają się mnożnikowo, nie przez dodawanie (10%+10% = 19%, nie 20%)")
check(abs(_cpo(2000, 0.0)[1] - NEGO_NA_MIEJSCU) < 1e-9,
      "bez targu zdalnego zostaje sam targ na miejscu")
check(_cpo(2000, 0.18)[1] <= NEGO_MAX_LACZNIE, "łączny luz nie przekracza sufitu")
check(_cpo(None, 0.1) == (None, 0.0), "brak ceny → brak wyniku, bez wywrotki")

_t, _tl = _cpo(2000, 0.02, ["Festpreis (mur)"])
_z, _zl = _cpo(2000, 0.02, [])
check(_tl < _zl, "kto napisał 'Festpreis', jest twardy także na żywo")

# oferta zdalna MUSI zostać wyższa niż cel na miejscu — inaczej pisalibyśmy
# nieznajomemu kwotę, którą wypada rzucić dopiero stojąc przy rowerze
_bp, _pct, _po = _rbp(2150, "2150 EUR VB", "guter Zustand")
_cr, _ = _cpo(2150, _pct, _po)
check(_bp > _cr, "w wiadomości proponujemy WIĘCEJ niż cel na oględzinach")
check(_cr < 2150, "cel na oględzinach niższy od ceny wywoławczej")

print("\nZapis KAŻDEJ zmiany ceny (próg powiadomienia ≠ próg wiedzy):")
# Pytanie użytkownika: ile sprzedawcy realnie opuszczają przed sprzedażą.
# Jedyna publicznie dostępna droga do odpowiedzi to śledzenie publicznych
# obniżek — a bot notował je dopiero od 5%, więc drobne przepadały bez śladu.
_zapisy = []
_ah = tracker.append_history
try:
    tracker.append_history = lambda *a, **k: _zapisy.append((a, k.get("ev")))
    # 2% obniżki: za mało na powiadomienie, ale MUSI trafić do dziennika
    _prog_powiadomienia = 0.95
    _stara, _nowa = 10000, 9800
    check(_nowa >= _stara * _prog_powiadomienia,
          "2% nie przekracza progu powiadomienia (i dobrze — to nie okazja)")
    check(_nowa != _stara, "ale JEST zmianą ceny, więc ma zostać zapisana")
finally:
    tracker.append_history = _ah

# dziennik musi umieć zapisać typ zdarzenia
import inspect as _insp
check("ev" in _insp.signature(tracker.append_history).parameters,
      "append_history przyjmuje typ zdarzenia")
check("year" in _insp.signature(tracker.append_history).parameters,
      "…i rocznik, żeby dało się liczyć obniżki per rocznik")

print("\nSzacunek zysku — w każdym ogłoszeniu, ale zawsze podpisany:")
# Zmierzone 23.08 na 502 wycenach bez przecieku: mediana błędu 20%, a przy
# pewności "niska" co siódma wycena myli się ponad dwukrotnie. Liczba bez
# etykiety pewności udawałaby wiedzę — a Cube z sierpnia (~5 500 zł szacunku
# wobec 1 300 zł realnego zysku) pokazał, ile to kosztuje.
_ZR = tracker.wycen_z_cennikiem
try:
    _pr = {"cena": 9000, "n": 20, "widelki": (8000, 10000), "cech_znanych": 3,
           "pewnosc": "wysoka"}
    tracker.wycen_z_cennikiem = lambda *a, **k: _pr
    check(_pr["pewnosc"] in ("wysoka", "srednia", "niska"),
          "wycena niesie etykietę pewności, nie samą kwotę")
finally:
    tracker.wycen_z_cennikiem = _ZR

# progi pewności muszą zostać takie, jak je zmierzono
_w = tracker.wycen_z_cennikiem(
    [{"cena": 9000 + i, "y": 2022, "wh": 625, "km": 1000, "poziom": 4} for i in range(12)],
    {"y": 2022, "wh": 625, "km": 1000, "poziom": 4},
    {"cechy": {"y": {"wspolczynnik": 0.08, "srodek": 2022},
               "wh": {"wspolczynnik": 0.16, "srodek": 6.25},
               "km": {"wspolczynnik": -0.04, "srodek": 1.0}}})
check(_w is not None and _w["pewnosc"] == "wysoka",
      "3 znane cechy + 12 ofert = pewność wysoka")
_w2 = tracker.wycen_z_cennikiem(
    [{"cena": 9000 + i, "y": 2022} for i in range(5)], {"y": 2022},
    {"cechy": {"y": {"wspolczynnik": 0.08, "srodek": 2022}}})
check(_w2 is not None and _w2["pewnosc"] == "niska",
      "1 cecha + 5 ofert = pewność niska (14% takich myli się ponad 2x)")
check(tracker.wycen_z_cennikiem([{"cena": 9000}], {"y": 2022},
      {"cechy": {"y": {"wspolczynnik": 0.08, "srodek": 2022}}}) is None,
      "za mało ofert → None, czyli bot mówi 'nie wiem' zamiast zgadywać")
check(tracker.wycen_z_cennikiem([{"cena": 9000, "y": 2022}], {},
      {"cechy": {"y": {"wspolczynnik": 0.08, "srodek": 2022}}}) is None,
      "o wycenianym rowerze nie wiemy NIC → None")

print("\nOdduplikowanie ofert (funkcja była CICHO bezczynna):")
from tracker import odduplikuj  # noqa: E402

# rynek_pl.jsonl: 1427 wierszy na 417 adresów — ten sam rower liczony 3-4 razy.
# Stary klucz szedł po polu `sprzedawca`, którego w tym pliku NIE MA, więc
# warunek `is not None` sprawiał, że nie odsiewano niczego.
_wiersze = [
    {"url": "a", "model": "cube", "cena": 9000},
    {"url": "a", "model": "cube", "cena": 8500},   # ta sama oferta, po obniżce
    {"url": "b", "model": "trek", "cena": 7000},
]
_o = odduplikuj(_wiersze)
check(len(_o) == 2, "ta sama oferta w kilku skanach liczy się RAZ")
check(_o[0]["cena"] == 8500, "zostaje OSTATNIA obserwacja — niesie aktualną cenę")
check([x["url"] for x in _o] == ["a", "b"], "kolejność ofert zachowana")

# stara ścieżka (bez url, ze sprzedawcą) musi działać jak dotąd
_stare = [{"sprzedawca": "s1", "model": "cube", "cena": 9000},
          {"sprzedawca": "s1", "model": "cube", "cena": 9000},
          {"sprzedawca": "s2", "model": "cube", "cena": 9000}]
check(len(odduplikuj(_stare)) == 2, "klucz po sprzedawcy nadal działa, gdy nie ma adresu")
check(len(odduplikuj([{"model": "x", "cena": 1}, {"model": "x", "cena": 1}])) == 2,
      "bez adresu i bez sprzedawcy nie zgadujemy — obie zostają")
check(odduplikuj([]) == [], "pusta lista nie wywraca")

print("\nGaleria zdjęć (10 z 13 zdjęć na stronie to CUDZE rowery):")
from tracker import galeria_ze_strony, send_telegram_album, ALBUM_MAX  # noqa: E402

_B = "https://img.kleinanzeigen.de/api/v1/prod-ads/images"
_wlasne = (f'<div data-imgsrc="{_B}/51/51e49c4c-04cd-40e2-9836-253fb54b99fc">'
           f'<div data-imgsrc="{_B}/98/98c68a34-5e52-4f89-8957-c57ae5965d9e">')
# tak wygladaja podpowiedzi "moze cie zainteresuje" — inny rower, ten sam serwer
_obce = (f'<img src="{_B}/ed/ed7b3f67-4b40-46a3-95bf-8af824612db5">'
         f'"contentUrl":"{_B}/84/846557aa-f73d-4ef6-890d-27214fcd39d3"')
_g = galeria_ze_strony(_wlasne + _obce)
check(len(_g) == 2, "brane są TYLKO zdjęcia z galerii ogłoszenia, nie cudze rowery")
check(all(u.endswith("?rule=$_59.AUTO") for u in _g), "wymuszony czytelny rozmiar 960x720")
check(_g[0].split("?")[0].endswith("51e49c4c-04cd-40e2-9836-253fb54b99fc"),
      "kolejność z galerii zachowana — pierwsze zdjęcie zostaje główne")
check(galeria_ze_strony(_wlasne + _wlasne) == _g, "duplikaty odsiane")
check(galeria_ze_strony("") == [] and galeria_ze_strony(None) == [],
      "pusta strona → brak zdjęć, bez wywrotki")

check(send_telegram_album([]) is False, "brak zdjęć → nie ma czego wysyłać")
check(send_telegram_album(["http://x/1.jpg"]) is False,
      "jedno zdjęcie to nie album — Telegram wymaga co najmniej dwóch")
check(ALBUM_MAX == 10, "twardy limit Telegrama na album")

print("\nGotowe wiadomości do sprzedawcy (tyle pytań, ile braków):")
from tracker import (wiadomosc_do_sprzedawcy as _wds, wiadomosc_oferta as _wof,  # noqa: E402
                     klawiatura_kopiuj as _kk, PRZYCISK_MAX)

# Sedno: jedna wiadomość, a liczba pytań ZALEŻY od tego ogłoszenia
_w3 = _wds(["przebieg", "rama", "bateria"])
_w2 = _wds(["rama", "bateria"])
_w1 = _wds(["bateria"])
_w0 = _wds([])
check(_w3.count("Sie mir sagen") == 1, "trzy braki = JEDNO pytanie, nie trzy wiadomości")
check("km" in _w3 and "Rahmengröße" in _w3 and "Wh" in _w3, "trzy braki → wszystkie trzy w treści")
check("Rahmengröße" in _w2 and "Wh" in _w2 and "wie viele km" not in _w2,
      "dwa braki → tylko te dwa, bez pytania o znany przebieg")
check("Wh" in _w1 and "Rahmengröße" not in _w1, "jeden brak → jedno pytanie")
check("Sie mir sagen" not in _w0, "komplet danych → żadnych pytań")
check(" und " in _w2 and "," not in _w2.split("sagen,")[1].split("?")[0],
      "dwa braki łączy 'und', bez przecinka")
check(_w3.split("sagen,")[1].count(",") == 1 and " und " in _w3,
      "trzy braki: przecinek + 'und' przed ostatnim")

# KLUCZOWE: cena NIGDY w pierwszym kontakcie — za niska kwota i sprzedawca
# przestaje odpowiadać, zanim w ogóle poda przebieg
for _b in ([], ["przebieg"], ["rama", "bateria"], ["przebieg", "rama", "bateria"]):
    _tekst = _wds(_b)
    check("€" not in _tekst and "möglich" not in _tekst,
          f"pierwszy kontakt bez ceny ({len(_b)} braków)")

check("2040 €" in _wof(2040, po_pytaniach=True), "oferta niesie kwotę")
check(_wof(None, po_pytaniach=True) is None, "brak kwoty → brak przycisku oferty")
check("Danke für die Infos" in _wof(2040, po_pytaniach=True),
      "po pytaniach oferta to kontynuacja rozmowy, nie zaczepka")
check("Hallo!" in _wof(2040, po_pytaniach=False),
      "gdy pytań nie było, oferta musi się przedstawić")

for _b in ([], ["przebieg"], ["rama"], ["bateria"], ["przebieg", "rama"],
           ["przebieg", "rama", "bateria"]):
    check(len(_wds(_b)) <= PRZYCISK_MAX, f"pytanie mieści się w limicie ({len(_b)} braków)")
for _po in (True, False):
    check(len(_wof(2040, _po)) <= PRZYCISK_MAX, f"oferta mieści się w limicie (po_pytaniach={_po})")

_k = _kk([("A", "tresc-a"), ("B", "tresc-b")])
check(len(_k["inline_keyboard"]) == 2, "dwa przyciski = dwa osobne rzędy")
check(_k["inline_keyboard"][0][0]["copy_text"]["text"] == "tresc-a", "przycisk niesie tekst do schowka")
check(_kk([]) is None and _kk([("A", "")]) is None, "bez treści nie ma przycisku")
check(len(_kk([("A", "z" * 400)])["inline_keyboard"][0][0]["copy_text"]["text"]) == PRZYCISK_MAX,
      "za długi tekst przycięty do limitu API, zamiast błędu z Telegrama")

print("\nRegion zamiast nazwy wsi (28307 Osterholz nic nie mówi o dojeździe):")
from tracker import region_z_plz  # noqa: E402

check(region_z_plz("02826 Görlitz") == "Saksonia (przy granicy)", "przygraniczne oznaczone")
check(region_z_plz("15230 Frankfurt") == "Brandenburgia (przy granicy)", "Frankfurt n. Odrą przy granicy")
check(region_z_plz("80805 Schwabing") == "Bawaria", "Monachium to Bawaria")
check(region_z_plz("28307 Osterholz") == "Brema", "wieś zamieniona na land")
check(region_z_plz("66111 Saarbrücken") == "Saara", "najmniejszy land też rozpoznany")
check(region_z_plz(None) is None and region_z_plz("") is None, "brak kodu → brak zgadywania")
check(region_z_plz("bez kodu Berlin") is None, "sama nazwa bez kodu → None")

# Parser lokalizacji łapał współrzędne ze ścieżki SVG — 34% wpisów w market.jsonl
_wz = re.compile(r'\b(\d{5})\s+([A-ZÄÖÜ][^<\n\d]{1,38})')
check(_wz.search("09163 10.1363 5.62761 12.0003 5.62761C13.8643") is None,
      "współrzędne SVG NIE są uznane za adres")
_m = _wz.search('<div>28307 Osterholz</div>')
check(_m is not None and _m.group(2).strip() == "Osterholz", "prawdziwy adres nadal czytany")
check(_wz.search("88161 Lindenberg im Allgäu") is not None, "nazwa z polskimi/niem. znakami OK")

print("\nRozmiar ramy (do 22.08 bot szukał POLSKICH słów w NIEMIECKICH opisach):")
from tracker import rozmiar_ramy  # noqa: E402

# 0 trafień na 12 żywych ogłoszeń przed poprawką; 6 z nich podawało rozmiar wprost
for _opis, _oczek, _co in [
        ("9, E-Bike in der Gr. L, mit 29 Zoll DT SWISS Reifen", "L", "Gr. L"),
        ("Gesamtgewicht 130 kg Rahmengröße S Baujahr 2020", "S", "Rahmengröße + ß"),
        ("Neupreis 5999 € - Rahmenhöhe: XL - Laufräder: 29", "XL", "Rahmenhöhe"),
        ("Rad mit Wave-Rahmen und einer Rahmenhöhe von 53 cm", "53 cm", "rozmiar w cm"),
        ("Keine Rücktrittbremse Rahmengröße M 50cm 28er", "M / 50 cm", "litera I cm"),
        ("Farbe: Blue * Rahmengröße: 57 cm * Bosch", "57 cm", "same cm"),
        ("Cube Stereo, RH 48, Bosch CX", "48 cm", "skrót RH"),
        ("Rahmengrösse L, top Zustand", "L", "pisownia 'ss' zamiast ß"),
        ("Rama: HPC Carbon. Rozmiar: XL. Fox", "XL", "polski opis z OLX"),
        ("Cube Stereo, rama L, stan bdb", "L", "polskie 'rama L'"),
]:
    check(rozmiar_ramy("", _opis) == _oczek, f"{_co} → {_oczek}")

# Fałszywe trafienia są gorsze niż brak odpowiedzi: w tych samych zdaniach
# siedzą koła, opony i waga, a zły rozmiar ramy to zmarnowany dojazd.
for _opis, _co in [
        ("Größe 29 Zoll Laufräder, super Zustand", "koło 29 Zoll to nie rama"),
        ("Zulässiges Gesamtgewicht 130 kg", "waga 130 kg to nie rama"),
        ("Reifen Größe 2,6 Zoll Magic Mary", "opona 2,6 to nie rama"),
        ("Rama: HPC Carbon. Naped: Shimano XT", "materiał ramy to nie rozmiar"),
        ("rozmiar kol 29 cali", "rozmiar koła to nie rama"),
        ("Super Rad, wenig gefahren", "brak danych → brak zgadywania"),
]:
    check(rozmiar_ramy("", _opis) is None, _co)
check(rozmiar_ramy("Cube Stereo Hybrid 160 Gr. M (47)", "") == "M", "rozmiar czytany też z tytułu")

print("\nDwie półki (Levo FSR wpadło do 'Mountainbikes', nie do 'Elektrofahrräder'):")
from tracker import KANALY, feed_url, save_feed_znacznik  # noqa: E402

check(len(KANALY) == 2, "obie rubryki, w które sprzedawcy wrzucają e-MTB")
check({k["typ"] for k in KANALY} == {"ebike", "mountainbike"}, "e-bike i mountainbike")
check("type_s:ebike" in feed_url("ebike") and "seite" not in feed_url("ebike"),
      "strona 1 bez numeru strony w adresie")
check("seite:3/" in feed_url("mountainbike", 3) and "type_s:mountainbike" in feed_url("mountainbike", 3),
      "kolejne strony numerowane w obrębie tej samej rubryki")

_st = Path(tempfile.mkdtemp()) / "feed.json"
_orig_feed_state = tracker.FEED_STATE_FILE
try:
    tracker.FEED_STATE_FILE = _st
    _t1 = _dt(2026, 8, 22, 21, 40, tzinfo=TZ_DE)
    _t2 = _dt(2026, 8, 22, 22, 10, tzinfo=TZ_DE)
    save_feed_znacznik(_t1, "ebike")
    save_feed_znacznik(_t2, "mountainbike")
    check(tracker.load_feed_znacznik("ebike") == _t1
          and tracker.load_feed_znacznik("mountainbike") == _t2,
          "każda półka ma WŁASNY znacznik — awaria jednej nie cofa drugiej")
    # plik sprzed dołożenia drugiej półki
    _st.write_text(json.dumps({"ostatnie": _t1.isoformat()}))
    check(tracker.load_feed_znacznik("ebike") == _t1,
          "stary format czytany jako znacznik e-bike (wdrożenie nie zaczyna od zera)")
    check(tracker.load_feed_znacznik("mountainbike") is None,
          "nowa półka startuje czysto, bez zassania godzin historii")
finally:
    tracker.FEED_STATE_FILE = _orig_feed_state

print("\nWidełki cenowe w kodzie (kanał nie ma filtra w URL-u):")
check(cena_w_widelkach(1500), "1500 € w widełkach")
check(not cena_w_widelkach(3000), "3000 € odrzucone")
check(not cena_w_widelkach(500), "500 € odrzucone")
check(cena_w_widelkach(None), "brak ceny przepuszczony (ratuje ją strona ogłoszenia)")

print("\nPowrót po przecenie (historia Scotta 22.08):")
check(wraca_po_przecenie({"date": "2026-08-22", "cena_odrzut": 3000}, 2300) == 3000,
      "widziany za 3000 €, dziś 2300 € → wraca jako oferta")
check(wraca_po_przecenie({"date": "2026-08-22", "cena_odrzut": 3000}, 2900) is None,
      "przecena, ale wciąż poza widełkami → nadal cisza")
check(wraca_po_przecenie({"date": "2026-08-22", "score": 70, "price_num": 2000}, 1800) is None,
      "rower, który przeszedł filtry, idzie ścieżką obniżki, nie tą")
check(wraca_po_przecenie({"date": "2026-08-22"}, 2300) is None, "zwykły widziany → bez powrotu")
check(wraca_po_przecenie(None, 2300) is None, "brak wpisu → bez wywrotki")
check(wraca_po_przecenie({"cena_odrzut": 3000}, None) is None, "nieznana cena → bez powrotu")

# === BRAMKA ALARMOWA =======================================================
# Regresja po skardze 22.08: "dostalem w przeciagu chwili dziala nie dziala".
# Alarmy szły zboczem, więc mrugnięcie sieci wystarczało za powód.
print("\nAlarmy (jedna bramka, cisza przez godzinę):")
from tracker import ocen_zdrowie, opisz_awarie, AWARIA_PROG_MIN  # noqa: E402

_tmp = Path(tempfile.mkdtemp()) / "stan.json"
_orig_state, _orig_send = tracker.PARSE_STATE_FILE, tracker.send_telegram
_SKRZYNKA = []
try:
    tracker.PARSE_STATE_FILE = _tmp
    tracker.send_telegram = lambda t: _SKRZYNKA.append(t)

    def _cofnij_zegar(minut):
        """Udaje, że awaria trwa już tyle minut."""
        s = json.loads(_tmp.read_text())
        s["awaria_od"] = time.time() - minut * 60
        _tmp.write_text(json.dumps(s))

    # 1. MIGOTANIE: awaria pojawia się i znika — użytkownik nie widzi NIC
    ocen_zdrowie(["slepy"])
    check(_SKRZYNKA == [], "pierwsza wpadka: cisza")
    ocen_zdrowie([])
    check(_SKRZYNKA == [], "wpadka minęła sama: nadal cisza, zero 'działa/nie działa'")
    for _ in range(5):
        ocen_zdrowie(["olx"]); ocen_zdrowie([])
    check(_SKRZYNKA == [], "pięć mrugnięć pod rząd: wciąż ani jednej wiadomości")

    # 2. AWARIA KRÓTSZA NIŻ PRÓG
    ocen_zdrowie(["slepy"])
    _cofnij_zegar(AWARIA_PROG_MIN - 5)
    ocen_zdrowie(["slepy"])
    check(_SKRZYNKA == [], f"awaria {AWARIA_PROG_MIN - 5} min: jeszcze cisza")

    # 3. AWARIA PRZEKRACZA PRÓG → dokładnie jedna wiadomość
    _cofnij_zegar(AWARIA_PROG_MIN + 1)
    ocen_zdrowie(["slepy"])
    check(len(_SKRZYNKA) == 1, f"awaria ponad {AWARIA_PROG_MIN} min: jedna wiadomość")
    check("🔕" in _SKRZYNKA[0] and "nie działa" in _SKRZYNKA[0], "treść to alarm")
    for _ in range(10):
        ocen_zdrowie(["slepy"])
    check(len(_SKRZYNKA) == 1, "awaria trwa dalej: NIE powtarza się co skan")

    # 4. KONIEC AWARII → jedno "już działa", bo alarm był
    ocen_zdrowie([])
    check(len(_SKRZYNKA) == 2 and "już działa" in _SKRZYNKA[1], "koniec awarii: jedno potwierdzenie")
    ocen_zdrowie([])
    check(len(_SKRZYNKA) == 2, "kolejne zdrowe skany: cisza")

    # 5. Zdrowie bez wcześniejszego alarmu NIE generuje "już działa"
    _SKRZYNKA.clear()
    ocen_zdrowie(["slepy"]); ocen_zdrowie([])
    check(_SKRZYNKA == [], "cicha wpadka nie kończy się fałszywym 'już działa'")
finally:
    tracker.PARSE_STATE_FILE, tracker.send_telegram = _orig_state, _orig_send

print("\nRotacja zapytań kluczowych (24 żądania/skan ściągnęły dławienie):")
from tracker import wybierz_kluczowe, SEARCHES, KLUCZOWE_NA_SKAN  # noqa: E402

_idx, _widziane = 0, []
for _ in range(len(SEARCHES)):          # tyle skanów, ile zapytań
    _w, _idx = wybierz_kluczowe(KLUCZOWE_NA_SKAN, _idx)
    _widziane += [s["name"] for s in _w]
check(all(len(wybierz_kluczowe(KLUCZOWE_NA_SKAN, i)[0]) == KLUCZOWE_NA_SKAN
          for i in range(len(SEARCHES))), "każdy skan bierze stałą, małą porcję")
check(set(_widziane) == set(s["name"] for s in SEARCHES),
      "po pełnym obiegu KAŻDE zapytanie zostało odpytane — nic nie wypada z zasięgu")
check(wybierz_kluczowe(0, 5) == ([], 5), "zero zapytań = nic, indeks nietknięty")
check(len(wybierz_kluczowe(999, 0)[0]) == len(SEARCHES), "prośba o więcej niż jest = wszystkie, bez duplikatów")
_w1, _i1 = wybierz_kluczowe(2, len(SEARCHES) - 1)
check(len(_w1) == 2 and len(set(s["name"] for s in _w1)) == 2, "zawijanie na końcu listy nie dubluje")

print("\nZdjęcie i opis po polsku:")
from tracker import send_telegram_photo, tlumacz_opis, TELEGRAM_PODPIS_MAX  # noqa: E402

_karta = ('data-adid="1"><img src="https://img.kleinanzeigen.de/api/v1/prod-ads/'
          'images/51/51e49c4c-abc?rule=$_2.AUTO" srcset="x">')
_m = re.search(r'https://img\.kleinanzeigen\.de/api/v1/prod-ads/images/[^"?\s\\]+', _karta)
check(_m is not None and _m.group(0).endswith("51e49c4c-abc"),
      "adres zdjęcia wyłuskany bez parametru rozmiaru")
check(send_telegram_photo(None, "x") is False, "brak zdjęcia → sygnał 'wyślij tekstem'")
check(send_telegram_photo("http://x/a.jpg", "y" * (TELEGRAM_PODPIS_MAX + 1)) is False,
      "podpis ponad limit → tekstem, zamiast błędu z Telegrama")

_orig_get = tracker.requests.get


class _OdpT:
    status_code = 200

    def __init__(self, dane):
        self._d = dane

    def raise_for_status(self):
        pass

    def json(self):
        return self._d


try:
    tracker.requests.get = lambda *a, **k: _OdpT(
        {"responseStatus": 200, "responseData": {"translatedText": "Rower z 2021 roku."}})
    check(tlumacz_opis("Fahrrad aus 2021, sehr guter Zustand.") == "Rower z 2021 roku.",
          "opis przetłumaczony")
    check(tlumacz_opis("kurz") is None, "za krótki tekst → nie zawracamy głowy serwisowi")
    tracker.requests.get = lambda *a, **k: _OdpT(
        {"responseStatus": 429, "responseData": {"translatedText": "MYMEMORY WARNING: LIMIT"}})
    check(tlumacz_opis("Fahrrad aus 2021, sehr guter Zustand.") is None,
          "wyczerpany darmowy limit → None, a nie komunikat serwisu w wiadomości")
    tracker.requests.get = lambda *a, **k: _OdpT(
        {"responseStatus": 200, "responseData": {"translatedText": "MYMEMORY WARNING: X"}})
    check(tlumacz_opis("Fahrrad aus 2021, sehr guter Zustand.") is None,
          "ostrzeżenie serwisu nie udaje tłumaczenia")

    def _wybuch(*a, **k):
        raise RuntimeError("sieć padła")
    tracker.requests.get = _wybuch
    check(tlumacz_opis("Fahrrad aus 2021, sehr guter Zustand.") is None,
          "awaria tłumacza nie wywraca skanu")
finally:
    tracker.requests.get = _orig_get

print("\nTreść alarmu (bez żargonu):")
_slepy, _olx = opisz_awarie(["slepy"]), opisz_awarie(["olx"])
check("nie przyjdą" in _slepy, "ślepy bot mówi, co z tego wynika: nie przyjdą rowery")
check("przychodzą normalnie" in _olx, "awaria OLX mówi, że rowery i tak idą")
check(_slepy != _olx, "różne awarie = różny opis")
for _t in (_slepy, _olx, opisz_awarie(["slepy", "olx"])):
    check(not any(w in _t for w in ["parser", "HTML", "Cloudflare", "HTTP", "znacznik"]),
          f"zero żargonu w: {_t.splitlines()[0][:40]}")
    check("/status" in _t, "szczegóły techniczne na żądanie, nie z automatu")

if FAILS:
    print(f"\n❌ {len(FAILS)} TESTÓW NIE PRZESZŁO: {FAILS}")
    sys.exit(1)
print("\n✅ WSZYSTKIE TESTY OK")
sys.exit(0)
