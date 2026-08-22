#!/usr/bin/env python3
"""Testy regresyjne bota samochodowego.

Każdy przypadek poniżej to auto, które NAPRAWDĘ przyszło na Telegram albo
NAPRAWDĘ przeszło filtry 21.08.2026 — zapisane tu, żeby nie wróciło.
Bez sieci: sprawdzamy czyste funkcje na zamrożonych danych z API.
"""
import os
import sys

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test")
os.environ.setdefault("TELEGRAM_CHAT_ID", "test")

import otomoto_tracker as ot  # noqa: E402

bledy = []


def sprawdz(nazwa, warunek):
    if warunek:
        print(f"  OK   {nazwa}")
    else:
        print(f"  BLAD {nazwa}")
        bledy.append(nazwa)


def auto(**nadpisz):
    """Ogłoszenie spełniające wszystkie kryteria A5 Sportback."""
    baza = {
        "model_key": "a5-sportback", "model_label": "A5 Sportback",
        "year": 2018, "fuel": "diesel", "gearbox": "automatic", "drive": "awd",
        "engine_cm3": 1968, "body": "sedan", "damaged": True,
        "mileage_num": 118000,
    }
    baza.update(nadpisz)
    return baza


K_A5 = ot.SEARCHES[0]["kryteria"]
K_SERIA3 = ot.SEARCHES[2]["kryteria"]
K_SERIA4 = ot.SEARCHES[3]["kryteria"]

print("\n== wpuszcza to, co ma wpuszczać ==")
pasuje, braki = ot.sprawdz_kryteria(auto(), K_A5)
sprawdz("A5 Sportback 2018 TDI quattro automat uszkodzony przechodzi", pasuje)
sprawdz("...i nie zgłasza braków", braki == [])

print("\n== realne wpadki, które trafiły na Telegram ==")
# 24.07.2026, wyszukiwanie „BMW G20 Seria 3" — „320" złapało się na „320KM"
seria8 = auto(model_key="seria-8", model_label="Seria 8", year=2019)
sprawdz("BMW Seria 8 odpada z wyszukiwania Serii 3",
        not ot.sprawdz_kryteria(seria8, K_SERIA3)[0])
# 08.08.2026, wyszukiwanie „BMW G26 Seria 4" — złapało się „gran coupe"
seria2 = auto(model_key="seria-2", model_label="Seria 2", year=2021)
sprawdz("BMW Seria 2 Gran Coupe odpada z wyszukiwania Serii 4",
        not ot.sprawdz_kryteria(seria2, K_SERIA4)[0])
# przechodziło przez filtr 21.08.2026, zatrzymało je tylko województwo
seria5 = auto(model_key="5-os-sorozat", model_label="Seria 5", year=2019)
sprawdz("BMW Seria 5 odpada z wyszukiwania Serii 3",
        not ot.sprawdz_kryteria(seria5, K_SERIA3)[0])
# Otomoto dokłada „podobne oferty" do /osobowe/audi/a5
for m in ("a6-limousine", "q5", "a4-avant", "a5-coupe"):
    sprawdz(f"Otomoto: {m} odpada z wyszukiwania A5 Sportback",
            not ot.sprawdz_kryteria(auto(model_key=m, model_label=""), K_A5)[0])

print("\n== kryteria, których nikt nie sprawdzał do 21.08.2026 ==")
sprawdz("benzyna odpada", not ot.sprawdz_kryteria(auto(fuel="petrol"), K_A5)[0])
sprawdz("manual odpada", not ot.sprawdz_kryteria(auto(gearbox="manual"), K_A5)[0])
sprawdz("napęd na tył odpada", not ot.sprawdz_kryteria(auto(drive="rwd"), K_A5)[0])
sprawdz("napęd na przód odpada", not ot.sprawdz_kryteria(auto(drive="fwd"), K_A5)[0])
sprawdz("3.0 TDI odpada", not ot.sprawdz_kryteria(auto(engine_cm3=2967), K_A5)[0])
sprawdz("nieuszkodzone odpada", not ot.sprawdz_kryteria(auto(damaged=False), K_A5)[0])
sprawdz("rocznik poza zakresem odpada", not ot.sprawdz_kryteria(auto(year=2021), K_A5)[0])
sprawdz("318 tys. km odpada (limit 200 tys.)",
        not ot.sprawdz_kryteria(auto(mileage_num=318134), K_A5)[0])
sprawdz("200 tys. km jeszcze przechodzi",
        ot.sprawdz_kryteria(auto(mileage_num=200000), K_A5)[0])

print("\n== BMW: slug węgierski i polski to ten sam samochód ==")
sprawdz("OLX-owe '3-as-sorozat' przechodzi",
        ot.sprawdz_kryteria(auto(model_key="3-as-sorozat", model_label="Seria 3",
                                 year=2020), K_SERIA3)[0])
sprawdz("Otomotowe 'seria-3' przechodzi",
        ot.sprawdz_kryteria(auto(model_key="seria-3", model_label="", year=2020),
                            K_SERIA3)[0])
sprawdz("dopasowanie po etykiecie, gdy klucz nieznany",
        ot.sprawdz_kryteria(auto(model_key="bmw-3er", model_label="Seria 3",
                                 year=2020), K_SERIA3)[0])
sprawdz("Seria 3 Touring odpada (nadwozie)",
        not ot.sprawdz_kryteria(auto(model_key="seria-3", year=2020,
                                     body="estate-car"), K_SERIA3)[0])
sprawdz("3GT odpada",
        not ot.sprawdz_kryteria(auto(model_key="3gt", model_label="3GT",
                                     year=2020), K_SERIA3)[0])

print("\n== brak danych to nie niezgodność ==")
pasuje, braki = ot.sprawdz_kryteria(auto(drive=None), K_A5)
sprawdz("auto bez podanego napędu przechodzi", pasuje)
sprawdz("...i napęd trafia do braków", braki == ["napęd"])
pasuje, braki = ot.sprawdz_kryteria(auto(drive=None, engine_cm3=None), K_A5)
sprawdz("dwa braki naraz", pasuje and set(braki) == {"napęd", "pojemność"})
sprawdz("braki widać w wiadomości",
        "napęd" in ot.format_braki(["napęd"]) and ot.format_braki([]) == "")
sprawdz("nieznany model to za mało, żeby odrzucić",
        ot.sprawdz_kryteria(auto(model_key="", model_label=""), K_A5)[0])

print("\n== zaprzeczenia w opisie ==")
sprawdz("'uszkodzony prawy przód' to uszkodzenie",
        ot.is_damaged("Audi A5", "delikatnie uszkodzony przód, reszta sprawna"))
sprawdz("'nieuszkodzony' to NIE uszkodzenie",
        not ot.is_damaged("Audi A5 Sportback", "auto nieuszkodzone, bezwypadkowe"))
sprawdz("'bez uszkodzeń' to NIE uszkodzenie",
        not ot.is_damaged("Audi A5", "stan idealny, bez uszkodzeń lakieru"))
sprawdz("'nie po wypadku' to NIE uszkodzenie",
        not ot.is_damaged("BMW 320d", "samochód nie po wypadku"))
sprawdz("'brak uszkodzeń' to NIE uszkodzenie",
        not ot.is_damaged("BMW 320d", "brak uszkodzeń mechanicznych"))
sprawdz("zaprzeczenie nie zagłusza realnej szkody w tym samym opisie",
        ot.is_damaged("Audi A4", "bez uszkodzeń lakieru, ale rozbity przód"))

print("\n== filtr uszkodzonych na Otomoto ==")
_edges = [{"node": {"id": "1", "title": "Audi A5 Sportback 40 TDI quattro S tronic",
                    "shortDescription": "salon polska, serwis", "parameters": [
                        {"key": "model", "value": "a5-sportback"},
                        {"key": "year", "value": "2018"},
                        {"key": "fuel_type", "value": "diesel"},
                        {"key": "gearbox", "value": "automatic"},
                        {"key": "engine_capacity", "value": "1968"},
                        {"key": "mileage", "value": "118 000"}]}}]
_orig = ot._fetch_page
ot._fetch_page = lambda url: _edges if "page=1" in url else []
try:
    z_filtrem = ot.fetch_listings_otomoto(ot.SEARCHES[0], pages=1)[0]
    bez = dict(ot.SEARCHES[0], url="https://www.otomoto.pl/osobowe/audi/a5")
    bez_filtra = ot.fetch_listings_otomoto(bez, pages=1)[0]
finally:
    ot._fetch_page = _orig
sprawdz("oferta z zapytania o uszkodzone jest uszkodzona mimo braku słów w opisie",
        z_filtrem["damaged"] and z_filtrem.get("szkoda_nieopisana"))
sprawdz("...i przechodzi kryteria", ot.sprawdz_kryteria(z_filtrem, K_A5)[0])
sprawdz("bez filtra w URL-u decydują słowa — czyste auto odpada",
        not bez_filtra["damaged"])
sprawdz("napęd wyczytany z nazwy wersji ('quattro')", z_filtrem["drive"] == "awd")
sprawdz("wszystkie 4 wyszukiwania Otomoto mają filtr uszkodzonych",
        all("filter_enum_damaged" in s["url"] for s in ot.SEARCHES))

print("\n== obserwowany wystawca ==")
import json as _json  # noqa: E402
import re  # noqa: E402
import olx as _olx  # noqa: E402

W = ot.WYSTAWCY[0]
OTO = "https://www.otomoto.pl/osobowe/oferta/x-ID1.html"
_LISTA = [{"id": 101}, {"id": 102}, {"id": 103}, {"id": 104}]
_SZCZEGOLY = {
    # lustro z Otomoto, konto naszego wystawcy
    101: {"title": "Toyota Celica", "url": "olx/101", "external_url": OTO,
          "contact": {"name": "Leszek"},
          # cena jest w `params`, nie w polu `price` — realny układ odpowiedzi
          "params": [{"key": "price", "value": {"label": "34 800 zł"}}]},
    # lustro z Otomoto, ale to Darek — inny sprzedawca z tej samej wsi
    102: {"title": "Opel Astra", "url": "olx/102", "external_url": OTO + "?d",
          "contact": {"name": "Darek"}, "price": {"displayValue": "12 000 zł"}},
    # wystawione wprost na OLX, zgadza się tylko imię
    103: {"title": "Przyczepka", "url": "olx/103", "external_url": "",
          "contact": {"name": "Leszek"}, "price": {"displayValue": "900 zł"}},
    # ktoś zupełnie inny
    104: {"title": "Yamaha", "url": "olx/104", "external_url": "",
          "contact": {"name": "Kuba"}, "price": {"displayValue": "5 000 zł"}},
}


def _fake_olx_get(url, timeout=20, **kw):
    if "offers/?" in url or url.endswith("offers/"):
        return _olx.OdpowiedzOLX(200, _json.dumps({"data": _LISTA}))
    m = re.search(r"offers/(\d+)/", url)
    if m:
        return _olx.OdpowiedzOLX(200, _json.dumps({"data": _SZCZEGOLY[int(m.group(1))]}))
    return None


_zapis = {"olx_get": _olx.olx_get, "sid": ot.otomoto_seller_id, "tg": ot.send_telegram}
wiad = []
_olx.olx_get = _fake_olx_get
ot.otomoto_seller_id = lambda u: W["otomoto_seller_id"] if not u.endswith("?d") else "18347288"
ot.send_telegram = lambda t: wiad.append(t)
try:
    stan = {}
    ile = ot.sprawdz_wystawce(W, stan)
    ile2 = ot.sprawdz_wystawce(W, stan)      # drugi przebieg na tym samym stanie
finally:
    _olx.olx_get, ot.otomoto_seller_id, ot.send_telegram = (
        _zapis["olx_get"], _zapis["sid"], _zapis["tg"])

sprawdz("wysłane dokładnie 2 z 4 ogłoszeń w miejscowości", ile == 2)
sprawdz("Celica naszego wystawcy poszła", any("Celica" in m for m in wiad))
sprawdz("Opel Darka NIE poszedł (inne sellerId mimo tej samej wsi)",
        not any("Astra" in m for m in wiad))
sprawdz("Yamaha Kuby NIE poszła", not any("Yamaha" in m for m in wiad))
sprawdz("ogłoszenie wprost z OLX-a poszło z zastrzeżeniem",
        any("Przyczepka" in m and "nie da się potwierdzić" in m for m in wiad))
sprawdz("potwierdzone ogłoszenie bez zastrzeżenia",
        all("nie da się potwierdzić" not in m for m in wiad if "Celica" in m))
sprawdz("drugi przebieg nic nie powtarza", ile2 == 0)
sprawdz("cena wczytana z params, nie 'brak ceny'",
        any("34 800 zł" in m for m in wiad if "Celica" in m))
sprawdz("odrzuceni też zapamiętani (bez ponownego pobierania szczegółów)",
        len(stan) == 4)
sprawdz("wystawca pilnowany w calej motoryzacji, nie tylko osobowych",
        W["olx_category_id"] == 5)

print("\n== spójność konfiguracji ==")
sprawdz("każde wyszukiwanie Otomoto ma kryteria",
        all("kryteria" in s for s in ot.SEARCHES))
sprawdz("każde wyszukiwanie OLX ma kryteria",
        all("kryteria" in s for s in ot.OLX_SEARCHES))
sprawdz("żadne wyszukiwanie nie ma pustego zbioru modeli",
        all(s["kryteria"].get("modele") for s in ot.SEARCHES))
sprawdz("stare filtry po tytule zniknęły",
        not any("title_must_contain_any" in s for s in ot.OLX_SEARCHES))

print("\n== dociąganie nadwozia i napędu ze strony ogłoszenia ==")
# Regresja 22.08.2026: wyszukiwarka Otomoto NIE zwraca nadwozia ani napędu,
# a filtry w URL-u ich nie pilnują — wszystkie 9 „pasujących" BMW G20 okazało
# się Kombi na tylne koła, czyli Touring bez xDrive.
sprawdz("'4x4 (stały)' to napęd na cztery koła", ot._mapuj_naped("4x4 (stały)") == "awd")
sprawdz("'4x4 (dołączany automatycznie)' też", ot._mapuj_naped("4x4 (dołączany automatycznie)") == "awd")
sprawdz("'Na tylne koła' to tylny napęd", ot._mapuj_naped("Na tylne koła") == "rwd")
sprawdz("'Na przednie koła' to przedni", ot._mapuj_naped("Na przednie koła") == "fwd")
sprawdz("brak wartości to 'nie wiem', nie 'nie ma'", ot._mapuj_naped("") is None
        and ot._mapuj_naped(None) is None)
sprawdz("nieznana wartość nie udaje wiedzy", ot._mapuj_naped("Na gąsienice") is None)
sprawdz("Kombi rozpoznane", ot.NADWOZIE_OTOMOTO.get("kombi") == "kombi")
sprawdz("Limuzyna liczy się jako sedan", ot.NADWOZIE_OTOMOTO.get("limuzyna") == "sedan")

_bmw = {"model_key": "seria-3", "model_label": "", "year": 2020, "fuel": "diesel",
        "gearbox": "automatic", "drive": None, "engine_cm3": 1995, "body": None,
        "damaged": True, "mileage_num": 80000}
_kryt = [s for s in ot.SEARCHES if "G20" in s["name"]][0]["kryteria"]
sprawdz("bez danych ze strony Touring na RWD PRZECHODZI (stan sprzed poprawki)",
        ot.sprawdz_kryteria(dict(_bmw), _kryt)[0] is True)
sprawdz("z nadwoziem 'kombi' zostaje odrzucony",
        ot.sprawdz_kryteria(dict(_bmw, body="kombi"), _kryt)[0] is False)
sprawdz("z napędem 'rwd' zostaje odrzucony",
        ot.sprawdz_kryteria(dict(_bmw, drive="rwd"), _kryt)[0] is False)
sprawdz("prawdziwy sedan xDrive przechodzi bez braków",
        ot.sprawdz_kryteria(dict(_bmw, body="sedan", drive="awd"), _kryt) == (True, []))

# uzupelnij_ze_strony nie może zamienić wiedzy na niewiedzę
_orig = ot.pobierz_szczegoly
try:
    ot.pobierz_szczegoly = lambda url: {"body": None, "drive": None, "damaged": None, "version": None}
    _ad = {"url": "u", "body": "sedan", "drive": "awd", "damaged": True}
    ot.uzupelnij_ze_strony(_ad)
    sprawdz("pusta odpowiedź strony NIE kasuje tego, co już wiemy",
            _ad["body"] == "sedan" and _ad["drive"] == "awd" and _ad["damaged"] is True)
    ot.pobierz_szczegoly = lambda url: {"body": "kombi", "drive": "rwd", "damaged": False, "version": None}
    _ad2 = {"url": "u", "body": None, "drive": None, "damaged": True, "szkoda_nieopisana": True}
    ot.uzupelnij_ze_strony(_ad2)
    sprawdz("dane ze strony biją założenie z filtra w URL-u",
            _ad2["body"] == "kombi" and _ad2["drive"] == "rwd" and _ad2["damaged"] is False)
finally:
    ot.pobierz_szczegoly = _orig

print("\n== lustra ofert Otomoto na OLX ==")
sprawdz("token wyłuskany z adresu",
        ot.otomoto_id_z_url("https://www.otomoto.pl/osobowe/oferta/audi-a5-ID6I79ua.html") == "ID6I79ua")
sprawdz("inny slug, ten sam samochód",
        ot.otomoto_id_z_url("https://www.otomoto.pl/osobowe/oferta/zupelnie-inny-ID6I79ua.html")
        == ot.otomoto_id_z_url("https://www.otomoto.pl/osobowe/oferta/audi-a5-ID6I79ua.html"))
sprawdz("adres OLX-a nie ma tokenu Otomoto",
        ot.otomoto_id_z_url("https://www.olx.pl/d/oferta/x-CID5-ID1btpnU.html") is None)
sprawdz("brak adresu nie wywraca", ot.otomoto_id_z_url(None) is None and ot.otomoto_id_z_url("") is None)

print("\n== alarm o martwym bocie ==")
import tempfile as _tf, json as _js
from pathlib import Path as _P
_wyslane = []
_stary_send, _stary_plik = ot.send_telegram, ot.STAN_FILE
try:
    ot.send_telegram = lambda t: _wyslane.append(t)
    ot.STAN_FILE = _P(_tf.mkdtemp()) / "stan.json"
    ot.ocen_zdrowie(0, 0)
    sprawdz("pierwszy pusty przebieg → cisza", _wyslane == [])
    ot.ocen_zdrowie(0, 0)
    sprawdz("drugi pusty (≈godzina) → jeden alarm", len(_wyslane) == 1 and "nie widzę" in _wyslane[0])
    for _ in range(5):
        ot.ocen_zdrowie(0, 0)
    sprawdz("martwota trwa → bez powtórek", len(_wyslane) == 1)
    ot.ocen_zdrowie(12, 0)
    sprawdz("powrót → jedno potwierdzenie", len(_wyslane) == 2 and "już działa" in _wyslane[1])
    ot.ocen_zdrowie(12, 3)
    sprawdz("normalna praca → cisza", len(_wyslane) == 2)
    _wyslane.clear()
    ot.ocen_zdrowie(0, 0); ot.ocen_zdrowie(5, 0)
    sprawdz("pojedyncza wpadka nie kończy się fałszywym 'już działa'", _wyslane == [])
    sprawdz("bez żargonu w alarmie",
            not any(w in ot.STAN_FILE.name for w in ["HTTP", "JSON"]))
finally:
    ot.send_telegram, ot.STAN_FILE = _stary_send, _stary_plik

print()
if bledy:
    print(f"NIEPOWODZENIE: {len(bledy)} testów nie przeszło")
    for b in bledy:
        print(f"   - {b}")
    sys.exit(1)
print("Wszystkie testy przeszły.")
