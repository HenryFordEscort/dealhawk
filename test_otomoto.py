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
sprawdz("rocznik poza zakresem odpada (2020 przy 2015-2019)",
        not ot.sprawdz_kryteria(auto(year=2020), K_A5)[0])
sprawdz("...z obu stron (2014)", not ot.sprawdz_kryteria(auto(year=2014), K_A5)[0])
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
sprawdz("Seria 3 GT (liftback, ten sam klucz modelu) odpada na nadwoziu",
        not ot.sprawdz_kryteria(auto(model_key="3-as-sorozat", model_label="Seria 3",
                                     year=2019, body="hatchback"), K_SERIA3)[0])
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
# Pozycje LISTY w realnym kształcie: external_url, contact i params siedzą
# w odpowiedzi wyszukiwarki (sprawdzone 15.09.2026 na 33 ogłoszeniach
# z Oleśnicy). Ogłoszenie wystawione wprost na OLX nie ma klucza external_url.
_LISTA = [
    # lustro z Otomoto, konto naszego wystawcy
    {"id": 101, "title": "Toyota Celica", "url": "olx/101", "external_url": OTO,
     "contact": {"name": "Leszek"},
     # cena jest w `params`, nie w polu `price`, to realny układ odpowiedzi
     "params": [{"key": "price", "value": {"label": "34 800 zł"}}]},
    # lustro z Otomoto, ale to Darek, inny sprzedawca z tej samej wsi
    {"id": 102, "title": "Opel Astra", "url": "olx/102", "external_url": OTO + "?d",
     "contact": {"name": "Darek"}, "price": {"displayValue": "12 000 zł"}},
    # wystawione wprost na OLX, zgadza się tylko imię
    {"id": 103, "title": "Przyczepka", "url": "olx/103",
     "contact": {"name": "Leszek"}, "price": {"displayValue": "900 zł"}},
    # ktoś zupełnie inny
    {"id": 104, "title": "Yamaha", "url": "olx/104",
     "contact": {"name": "Kuba"}, "price": {"displayValue": "5 000 zł"}},
]
_adresy_olx = []     # każdy adres, o który bot zapytał OLX w tych testach


def _fake_olx_get(url, timeout=20, **kw):
    """Zachowuje się jak produkcja za przekaźnikiem: wyszukiwarka odpowiada,
    a na każdy inny adres API przekaźnik odmawia i olx_get oddaje None.
    Poprzednia atrapa odpowiadała też na /api/v1/offers/<id>/ i dlatego testy
    przechodziły przez 24 dni, kiedy obserwacja na produkcji nie działała."""
    _adresy_olx.append(url)
    if "offers/?" in url or url.endswith("offers/"):
        return _olx.OdpowiedzOLX(200, _json.dumps({"data": _LISTA}))
    return None


def _lustro_sid(url):
    return W["otomoto_seller_id"] if not url.endswith("?d") else "18347288"


_zapis = {"olx_get": _olx.olx_get, "sid": ot.otomoto_seller_id, "tg": ot.send_telegram,
          "scraper": ot.scraper}


def _przywroc():
    _olx.olx_get, ot.otomoto_seller_id, ot.send_telegram, ot.scraper = (
        _zapis["olx_get"], _zapis["sid"], _zapis["tg"], _zapis["scraper"])


wiad = []
_olx.olx_get = _fake_olx_get
ot.otomoto_seller_id = _lustro_sid
ot.send_telegram = lambda t, **k: wiad.append(t) or True
try:
    stan = {}
    ile = ot.sprawdz_wystawce(W, stan)
    ile2 = ot.sprawdz_wystawce(W, stan)      # drugi przebieg na tym samym stanie
finally:
    _przywroc()

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
sprawdz("odrzuceni też zapamiętani", len(stan) == 4)
sprawdz("odrzut zapisuje powód, nie gołe {}",
        stan["w_102"].get("powod") == "inny_sprzedawca" and stan["w_104"].get("powod"))
# zapisane kodami, bo samych znaków w repo ma nie być w ogóle
_DLUGIE_MYSLNIKI = (chr(0x2014), chr(0x2013))
sprawdz("wiadomości o wystawcy bez długich myślników",
        wiad and not any(d in m for m in wiad for d in _DLUGIE_MYSLNIKI))
sprawdz("wystawca pilnowany w calej motoryzacji, nie tylko osobowych",
        W["olx_category_id"] == 5)

print("\n== nieprzeczytane to nie 'ktoś inny' ==")
# Regresja 15.09.2026: odmowa przekaźnika była zapisywana tak samo jak "to nie
# on", więc obserwacja odhaczyła 52 ogłoszenia bez sprawdzenia i przez 24 dni
# nie wysłała nic, choć Leszek wystawił w tym czasie 4 auta.
# WŁASNOŚĆ, nie ścieżka: każda odpowiedź Otomoto, która nie jest ani żywą
# stroną, ani zdjętą ofertą, zostawia ogłoszenie do następnego biegu.


class _Odp:
    def __init__(self, status_code, text=""):
        self.status_code, self.text = status_code, text


class _Scraper:
    def __init__(self, odp):
        self.odp = odp

    def get(self, url, **kw):
        if isinstance(self.odp, Exception):
            raise self.odp
        return self.odp


# stara wersja nie ma tej stałej, a test ma na niej paść, nie wywrócić się
_zdjeta = getattr(ot, "ZDJETA", "zdjeta")
wiad = []
_olx.olx_get = _fake_olx_get
ot.send_telegram = lambda t, **k: wiad.append(t) or True
try:
    for _opis, _odp in [("403", _Odp(403)), ("404", _Odp(404)), ("429", _Odp(429)),
                        ("502", _Odp(502)), ("przekroczony czas", TimeoutError("czas")),
                        ("strona bez numeru sprzedawcy", _Odp(200, "<html></html>"))]:
        ot.scraper = _Scraper(_odp)
        _s = {}
        ot.sprawdz_wystawce(W, _s)
        sprawdz(f"{_opis}: lustra zostają do sprawdzenia, nie trafiają do seen",
                "w_101" not in _s and "w_102" not in _s)
    sprawdz("...i nic o nich nie poszło", not any("Celica" in m or "Astra" in m for m in wiad))

    _s = {}
    ot.scraper = _Scraper(_Odp(502))
    ot.sprawdz_wystawce(W, _s)
    ot.otomoto_seller_id = _lustro_sid       # strona Otomoto wróciła
    wiad.clear()
    ot.sprawdz_wystawce(W, _s)
    sprawdz("gdy Otomoto wróciło, ogłoszenie Leszka poszło w kolejnym biegu",
            any("Celica" in m for m in wiad))
    ot.otomoto_seller_id = _zapis["sid"]

    ot.scraper = _Scraper(_Odp(410))
    sprawdz("410 to oferta zdjęta, a nie 'nie wiem'", ot.otomoto_seller_id(OTO) == _zdjeta)
    _s = {}
    ot.sprawdz_wystawce(W, _s)
    sprawdz("zdjęta z Otomoto zapamiętana z powodem, bez wysyłki",
            (_s.get("w_101") or {}).get("powod") == "zdjete_z_otomoto")

    ot.scraper = _Scraper(_Odp(502))
    _odcz = {"proby": 0, "udane": 0}
    try:
        ot.sprawdz_wystawce(W, {}, _odcz)
    except TypeError:
        pass                                  # stara wersja nie liczy odczytów
    sprawdz("czujka dostaje liczbę prób i udanych odczytów",
            _odcz == {"proby": 2, "udane": 0})
finally:
    _przywroc()

print("\n== adresy OLX a przekaźnik Cloudflare ==")
# Przekaźnik przepuszcza tylko wymienione ścieżki, a żadna atrapa tego nie
# widzi. Obserwacja pytała o /api/v1/offers/<id>/ i na produkcji dostawała
# odmowę za każdym razem. Wzorce wyjęte wprost z kodu Workera, żeby test
# i przekaźnik nie mogły się rozjechać.
from pathlib import Path as _Pth  # noqa: E402
from urllib.parse import urlparse as _urlparse  # noqa: E402

_kod_workera = (_Pth(__file__).resolve().parent / "cloudflare_worker.js").read_text()
_blok = _kod_workera.split("DOZWOLONE_SCIEZKI = [", 1)[1].split("];", 1)[0]
_wzorce = [re.compile(w) for w in re.findall(r"^\s*/(.+?)/,", _blok, re.M)]
sprawdz("wzorce przekaźnika wczytane (bez nich test niżej przechodziłby na pusto)",
        len(_wzorce) >= 3)

_olx.olx_get = _fake_olx_get
ot.send_telegram = lambda t, **k: None
try:
    for _szukaj in ot.OLX_SEARCHES:
        ot.fetch_listings_olx(_szukaj)
    if hasattr(ot, "wycena_sprawnego"):          # stara wersja (reguła 2) jej nie ma
        ot.wycena_sprawnego(ot.OLX_SEARCHES[0], auto(year=2017, mileage_num=150000))
finally:
    _przywroc()


def _przejdzie(url):
    p = _urlparse(url)
    return (p.hostname or "").endswith("olx.pl") and any(w.search(p.path) for w in _wzorce)


_odmowy = sorted({u for u in _adresy_olx if not _przejdzie(u)})
sprawdz(f"każdy adres, o który bot pyta OLX, przejdzie przez przekaźnik "
        f"({len(set(_adresy_olx))} różnych)", _adresy_olx and not _odmowy)
for _u in _odmowy[:3]:
    print(f"       przekaźnik odmówi: {_u}")

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
_kryt = K_SERIA3
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
    ot.send_telegram = lambda t, **k: _wyslane.append(t) or True
    ot.STAN_FILE = _P(_tf.mkdtemp()) / "stan.json"
    ot.ocen_zdrowie(0, 0)
    sprawdz("pierwszy pusty przebieg → cisza", _wyslane == [])
    ot.ocen_zdrowie(0, 0)
    sprawdz("drugi pusty (≈godzina) → jeden alarm o obu źródłach",
            len(_wyslane) == 1 and "nie widzi" in _wyslane[0] and "ani OLX" in _wyslane[0])
    for _ in range(5):
        ot.ocen_zdrowie(0, 0)
    sprawdz("martwota trwa → bez powtórek", len(_wyslane) == 1)
    ot.ocen_zdrowie(12, 0)
    sprawdz("wraca samo Otomoto → potwierdzenie tylko o nim",
            len(_wyslane) == 2 and "Otomoto już działa" in _wyslane[1] and "OLX" not in _wyslane[1])
    ot.ocen_zdrowie(12, 3)
    sprawdz("wraca OLX → jedno potwierdzenie o OLX",
            len(_wyslane) == 3 and "OLX już działa" in _wyslane[2])
    ot.ocen_zdrowie(12, 3)
    sprawdz("normalna praca → cisza", len(_wyslane) == 3)
    _wyslane.clear()
    ot.ocen_zdrowie(0, 0); ot.ocen_zdrowie(5, 3)
    sprawdz("pojedyncza wpadka nie kończy się fałszywym 'już działa'", _wyslane == [])
    # Regresja 16.09.2026: alarm zapalał się tylko, gdy padły OBA źródła, więc
    # martwy OLX przy żywym Otomoto był ciszą.
    ot.STAN_FILE = _P(_tf.mkdtemp()) / "stan.json"
    ot.ocen_zdrowie(40, 0); ot.ocen_zdrowie(40, 0)
    sprawdz("padł sam OLX → alarm o OLX", len(_wyslane) == 1 and "z OLX" in _wyslane[0])
    sprawdz("bez żargonu w alarmie",
            not any(w in ot.STAN_FILE.name for w in ["HTTP", "JSON"]))
finally:
    ot.send_telegram, ot.STAN_FILE = _stary_send, _stary_plik

print("\n== alarm o ślepej obserwacji wystawców ==")
# `ocen_zdrowie` liczy tylko wyszukiwania modeli i przez 24 dni ślepej
# obserwacji stał na zielono. Czas liczony w godzinach, nie w biegach.
from datetime import datetime as _dt, timedelta as _td, timezone as _tz  # noqa: E402

_ocen = getattr(ot, "ocen_obserwacje", None)
_wyslane = []
_stary_send, _stary_plik = ot.send_telegram, ot.STAN_FILE
try:
    ot.send_telegram = lambda t, **k: _wyslane.append(t) or True
    ot.STAN_FILE = _P(_tf.mkdtemp()) / "stan.json"
    if _ocen is None:
        sprawdz("jest czujka na ślepą obserwację wystawców", False)
    else:
        _t0 = _dt(2026, 9, 15, 12, 0, tzinfo=_tz.utc)
        _ocen(2, 0, teraz=_t0)
        _ocen(2, 0, teraz=_t0 + _td(hours=2))
        sprawdz("2 h bez odczytu to chwilowa wpadka, cisza", _wyslane == [])
        _ocen(2, 0, teraz=_t0 + _td(hours=7))
        sprawdz("7 h bez odczytu: jeden alarm",
                len(_wyslane) == 1 and "nie może sprawdzić" in _wyslane[0])
        _ocen(2, 0, teraz=_t0 + _td(hours=20))
        sprawdz("ślepota trwa: bez powtórek", len(_wyslane) == 1)
        _ocen(1, 1, teraz=_t0 + _td(hours=21))
        sprawdz("udany odczyt: jedno potwierdzenie",
                len(_wyslane) == 2 and "znowu" in _wyslane[1])
        _ocen(0, 0, teraz=_t0 + _td(hours=22))
        sprawdz("bieg bez prób odczytu: cisza", len(_wyslane) == 2)
        sprawdz("bez długich myślników w alarmie i potwierdzeniu",
                not any(d in m for m in _wyslane for d in _DLUGIE_MYSLNIKI))
        _wyslane.clear()
        _ocen(1, 0, teraz=_t0 + _td(hours=30))
        _ocen(0, 0, teraz=_t0 + _td(hours=40))   # nieczytelne ogłoszenie zniknęło z listy
        _ocen(1, 0, teraz=_t0 + _td(hours=41))
        sprawdz("dwie wpadki przedzielone biegiem bez prób to nie ciągła ślepota",
                _wyslane == [])
finally:
    ot.send_telegram, ot.STAN_FILE = _stary_send, _stary_plik

print("\n== alarm o zbyt rzadkich biegach ==")
# Od 27.08.2026 GitHub dowoził z crona bieg mediana co 3,6 h i przez trzy
# tygodnie nikt tego nie zauważył, bo wolny bot wygląda jak spokojny rynek.
_tempo = getattr(ot, "ocen_tempo", None)
_wyslane = []
_stary_send, _stary_plik = ot.send_telegram, ot.STAN_FILE
try:
    ot.send_telegram = lambda t, **k: _wyslane.append(t) or True
    ot.STAN_FILE = _P(_tf.mkdtemp()) / "stan.json"
    if _tempo is None:
        sprawdz("jest czujka na zbyt rzadkie biegi", False)
    else:
        _t = _dt(2026, 9, 16, 8, 0, tzinfo=_tz.utc)
        _tempo(teraz=_t)                                  # pierwszy bieg: sam zapis
        _t += _td(minutes=31)
        _tempo(teraz=_t)
        sprawdz("bieg co pół godziny: cisza", _wyslane == [])
        _t += _td(hours=3, minutes=36)                    # mediana z crona po 27.08
        ot._bieg_reset() if hasattr(ot, "_bieg_reset") else None
        _tempo(teraz=_t)
        sprawdz("przerwa trafia do problemów dnia (podsumowanie ją pokaże)",
                any("przerwa" in p for p in getattr(ot, "_bieg", {}).get("problemy", [])))
        sprawdz("3,6 h przerwy: jeden alarm",
                len(_wyslane) == 1 and "rzadziej" in _wyslane[0] and "4 godz." in _wyslane[0])
        _t += _td(hours=11)
        _tempo(teraz=_t)
        sprawdz("kolejna długa przerwa: bez powtórki", len(_wyslane) == 1)
        _t += _td(minutes=30)
        _tempo(teraz=_t)
        sprawdz("powrót do pół godziny: jedno potwierdzenie",
                len(_wyslane) == 2 and "co pół godziny" in _wyslane[1])
        _t += _td(minutes=30)
        _tempo(teraz=_t)
        sprawdz("dalej normalnie: cisza", len(_wyslane) == 2)
        sprawdz("bez długich myślników w alarmie o tempie",
                not any(d in m for m in _wyslane for d in _DLUGIE_MYSLNIKI))
finally:
    ot.send_telegram, ot.STAN_FILE = _stary_send, _stary_plik

print("\n== łańcuszek biegów w otomoto.yml ==")
# Sam cron nie wystarcza (patrz wyżej). Pilnujemy, żeby łańcuszek nie zniknął
# z pliku przy okazji innej zmiany, bo wtedy bot po cichu wraca do biegu co
# kilka godzin.
_wf = (_Pth(__file__).resolve().parent / ".github" / "workflows" / "otomoto.yml").read_text()
sprawdz("każdy bieg wyzwala następny", "gh workflow run otomoto.yml" in _wf)
sprawdz("...z uprawnieniem do wyzwalania", "actions: write" in _wf)
sprawdz("...także po wywrotce skanu, ale nie po ręcznym anulowaniu", "!cancelled()" in _wf)
sprawdz("...i nie wcześniej niż po pół godzinie od startu",
        "30 * 60" in _wf and 'sleep "$zostalo"' in _wf)
sprawdz("...z zapasowym startem, żeby pusty nie dał pętli bez przerwy",
        'START="${START:-$(date +%s)}"' in _wf)
sprawdz("cron zostaje jako siatka bezpieczeństwa", "cron:" in _wf)

print("\n== zapis stanu nie może wisieć (26.09.2026) ==")
# DealHawk dostał tę poprawkę 18.09.2026, OtomotoHawk chodził bez niej tydzień
# dłużej. Zmierzone 26.09 na 14 kolejnych biegach, różnicą między datą commitu
# a datą wpisaną przez rebase: wisiało 14 na 14, od 12 min 27 s do 19 min 56 s.
# Tego samego dnia DealHawk zapisywał się w 1-2 s na wszystkich siedmiu
# ogniwach biegu 36245103542 - z tej samej odległości od `main` i na tej samej
# minucie.
import re as _re

# KROK WYCINANY PO NAZWIE, nie regexpem po całym pliku: w `otomoto.yml` jest
# druga pętla (sprzątacz zakleszczonej kolejki DealHawka) i strażnik nie może
# jej brać za pętlę zapisu.
_krok_zapisu = _wf.split("name: Zapisz seen_otomoto.json")[1].split("\n      - name:")[0]
_petla_m = _re.search(r"for i in ([\d ]+); do(.*?)\n          done", _krok_zapisu, _re.S)
sprawdz("pętla zapisu jest w kroku zapisu", _petla_m is not None)
_petla = _petla_m.group(2) if _petla_m else ""
_proby = len(_petla_m.group(1).split()) if _petla_m else 0

# POBÓR OGRANICZONY Z KONSTRUKCJI. `actions/checkout` robi klon PŁYTKI, więc
# gołe `pull --rebase` prosi o historię, której ten klon nie ma, i serwer
# dosyła całe repozytorium.
sprawdz("pobór przed zapisem sięga tylko wierzchołka (--depth=1)",
        "--depth=1" in _petla)
# SAMO `pull --rebase --depth=1` TO PUŁAPKA, odrzucona 18.09 na replice
# runnera: po płytkim poborze nie ma wspólnego przodka, więc git odwraca role
# i przekłada commity main-a na nasz.
sprawdz("rebase ma JAWNĄ BAZĘ, czyli przekłada NASZE commity na świeży wierzchołek",
        "--onto" in _petla and "FETCH_HEAD" in _petla)
sprawdz("żadnego `git pull` w pętli zapisu - ani gołego, ani z --depth",
        "git pull" not in _petla)

# KAŻDE SIECIOWE POLECENIE GITA W PĘTLI MA LIMIT CZASU. Liczone
# PORÓWNANIEM, a nie obecnością napisu: bez tego dołożenie czwartego
# polecenia bez limitu przeszłoby po cichu, a to właśnie ono wisiałoby przez
# pół cyklu. `git rebase --abort` jest z tego wyjęte - sprząta po nieudanej
# próbie, jest lokalne i nie ma jak wisieć na sieci.
_git_w_petli = _re.findall(r"\bgit (fetch|rebase|push)\b(?! --abort)", _petla)
_z_limitem = _re.findall(r"timeout -k \d+ \d+ git (fetch|rebase|push)\b", _petla)
sprawdz("każde sieciowe polecenie gita w pętli ma limit czasu",
        len(_git_w_petli) > 0 and _git_w_petli == _z_limitem)
# ZAWIESZONY GIT NIE UMIERA OD SIGTERM. Zmierzone 18.09 u DealHawka: log
# kończył się sześcioma wpisami "Terminate orphan process: git".
_dobicia = [int(x) for x in _re.findall(r"timeout -k (\d+) \d+ git", _petla)]
sprawdz("zawieszony git jest DOBIJANY, a nie tylko proszony",
        bool(_dobicia) and min(_dobicia) > 0)

# BUDŻET LICZONY Z PLIKU, NIE WPISANY. Podniesienie któregokolwiek progu bez
# policzenia reszty ma tu paść. Sumujemy ŚWIADOMIE zawyżając: czekanie
# łańcuszka nie dodaje się do zapisu, tylko się o niego skraca - a strażnik
# ma trzymać górne ograniczenie, nie średnią.
_limity = [int(x) for x in _re.findall(r"timeout -k \d+ (\d+) git", _petla)]
_pauza = int(_re.search(r"sleep (\d+)\s*\n\s*done", _krok_zapisu).group(1))
_sufit = int(_re.search(r"timeout-minutes:\s*(\d+)", _wf).group(1)) * 60
_czekanie = int(_re.search(r"START \+ (\d+) \* 60", _wf).group(1)) * 60
# 40 s na kroki PRZED zapisem. Zmierzone 26.09: 22 s na biegu 36243701860
# i 32 s na 36242103112 (setup, sprzątacz, checkout, pip, czujka, skan).
_PRZED_ZAPISEM_S = 40
_najgorszy = _proby * sum(_limity) + _proby * sum(_dobicia) + (_proby - 1) * _pauza
sprawdz(f"najgorszy zapis ({_najgorszy} s) plus reszta biegu "
        f"({_PRZED_ZAPISEM_S + _czekanie} s) mieści się w suficie {_sufit} s",
        bool(_limity) and _najgorszy + _PRZED_ZAPISEM_S + _czekanie < _sufit)

# NIEUDANY ZAPIS MA BYĆ CZERWONY. Stara pętla po trzech nieudanych próbach po
# prostu się kończyła i krok wychodził zerem, więc utrata stanu wyglądała
# dokładnie tak samo jak udany zapis. A utrata stanu znaczy tu, że następny
# bieg wyśle te same auta drugi raz.
_po_petli = _krok_zapisu.split("\n          done", 1)[1] if "\n          done" in _krok_zapisu else ""
sprawdz("nieudany zapis maluje krok na czerwono, zamiast wyjść zerem",
        "exit 1" in _po_petli and "::error::" in _po_petli)
# SPIĘCIE: czerwony krok zapisu NIE MOŻE zerwać łańcuszka. Gdyby krok
# wyzwalający następny bieg stał pod `success()` albo bez warunku, ta jedna
# linijka `exit 1` zatrzymywałaby bota po pierwszej nieudanej próbie zapisu.
_lancuszek = _wf.split("name: Wyzwól następny bieg")[1]
sprawdz("...a łańcuszek i tak rusza, bo stoi pod !cancelled()",
        "exit 1" not in _po_petli or "!cancelled()" in _lancuszek)

print("\n== A4 i Seria 4 w kryteriach ==")
K_A4 = ot.SEARCHES[1]["kryteria"]
_a4 = auto(model_key="a4-limousine", model_label="A4 Limousine", year=2017)
sprawdz("A4 Limousine przechodzi", ot.sprawdz_kryteria(_a4, K_A4)[0])
sprawdz("A4 Avant odpada",
        not ot.sprawdz_kryteria(dict(_a4, model_key="a4-avant", model_label="A4 Avant",
                                     body="estate-car"), K_A4)[0])
sprawdz("A4 allroad odpada (własny klucz modelu)",
        not ot.sprawdz_kryteria(dict(_a4, model_key="a4-allroad", model_label="A4 allroad",
                                     body="estate-car"), K_A4)[0])
sprawdz("Seria 4 z 2022 przechodzi",
        ot.sprawdz_kryteria(auto(model_key="seria-4", model_label="Seria 4", year=2022), K_SERIA4)[0])
sprawdz("Seria 4 z 2019 (poprzednia generacja) odpada",
        not ot.sprawdz_kryteria(auto(model_key="seria-4", model_label="Seria 4", year=2019), K_SERIA4)[0])

print("\n== województwa: dokładna nazwa, nie 'zawiera się' ==")
# Regresja 16.09.2026: "śląskie" siedzi w "dolnośląskie" i bot wpuszczał
# całe dolnośląskie.
sprawdz("dolnośląskie to NIE śląskie", not ot.in_allowed_region("dolnośląskie"))
sprawdz("...a wybrane cztery przechodzą, też z dużej litery i spacją",
        all(ot.in_allowed_region(r) for r in (" Śląskie ", "Małopolskie", "podkarpackie", "świętokrzyskie")))
sprawdz("brak województwa to 'nie wiem', więc przechodzi", ot.in_allowed_region(""))

print("\n== OLX: filtry strukturalne zamiast tekstu ==")
# Regresja 16.09.2026: tekst "audi a4 sedan uszkodzony" nie znajdzie Avanta,
# a pierwsze 50 wyników po trafności to nie cały rynek.
def _modele_zapytania(p):
    return {v for k, v in p.items() if k.startswith("filter_enum_model[")}
sprawdz("żadne wyszukiwanie OLX nie pyta tekstem",
        not any("query" in s["params"] for s in ot.OLX_SEARCHES))
sprawdz("każde pyta o kategorię i model, a model pochodzi z kryteriów",
        all(s["params"].get("category_id") and _modele_zapytania(s["params"])
            and _modele_zapytania(s["params"]) <= set(s["kryteria"]["modele"]) for s in ot.OLX_SEARCHES))
sprawdz("każde pyta tylko o uszkodzone",
        all(s["params"].get("filter_enum_condition[0]") == "damaged" for s in ot.OLX_SEARCHES))
sprawdz("rocznik w zapytaniu to rocznik z kryteriów",
        all((s["params"].get("filter_float_year:from"), s["params"].get("filter_float_year:to"))
            == tuple(s["kryteria"]["rok"]) for s in ot.OLX_SEARCHES))
sprawdz("napędu NIE ma w zapytaniu (OLX nie zna go w 26% ogłoszeń)",
        not any(k.startswith("filter_enum_drive") for s in ot.OLX_SEARCHES for k in s["params"]))

print("\n== OLX: stronicowanie ==")
_strony = []


def _olx_strony(n_stron, zawsze_dalej=False):
    def get(url, timeout=20, **kw):
        from urllib.parse import urlparse, parse_qs
        _strony.append(url)
        nr = int(parse_qs(urlparse(url).query).get("offset", ["0"])[0]) // 50
        dane = [{"id": 900 + nr * 10 + i, "title": f"auto {nr}-{i}", "params": []} for i in range(3)]
        dane.append({"id": 1, "title": "promowane", "params": []})    # wraca na każdej stronie
        dalej = zawsze_dalej or nr + 1 < n_stron
        return _olx.OdpowiedzOLX(200, _json.dumps({"data": dane, "links": {"next": {"href": "x"}} if dalej else {}}))
    return get


_do_sita = []
_stare_sito = ot.sprawdz_kryteria
try:
    ot.sprawdz_kryteria = lambda l, k: (_do_sita.append(l["id"]), (False, []))[1]
    _olx.olx_get = _olx_strony(3)
    ot.fetch_listings_olx(ot.OLX_SEARCHES[0])
    sprawdz("czyta wszystkie 3 strony", len(_strony) == 3)
    sprawdz("każde ogłoszenie z każdej strony trafia do sita, promowane tylko raz",
            sorted(_do_sita) == sorted(["olx_1"] + [f"olx_{900 + nr * 10 + i}" for nr in range(3) for i in range(3)]))
    _strony.clear()
    _olx.olx_get = _olx_strony(0, zawsze_dalej=True)
    ot.fetch_listings_olx(ot.OLX_SEARCHES[0])
    sprawdz("bez końca listy staje na suficie stron", len(_strony) == getattr(ot, "OLX_STRON_MAX", -1))
finally:
    ot.sprawdz_kryteria = _stare_sito
    _przywroc()

print("\n== pętla Otomoto: odrzut jednego wyszukiwania nie zjada drugiego ==")
# Regresja 16.09.2026: A5 i A4 dostają z Otomoto tę samą pulę Audi (model w adresie
# jest ignorowany), a odrzut sita trafiał do seen jako {}. A5 szło pierwsze, więc
# wyszukiwanie A4 nie mogło wysłać NICZEGO. Test puszcza cały main() na atrapach.


def _oto(id_, model, region):
    return dict(auto(model_key=model, model_label="", body=None), id=id_,
                title=f"Audi {model} {id_}", url=f"https://www.otomoto.pl/osobowe/oferta/x-ID{id_}.html",
                short_desc="", city="Katowice", region=region, created_at="", price_num=40000,
                price_str="40 000 PLN", params={}, engine_hp=190, model_value=model, version_value="")


_pula_audi = [_oto("501", "a5-sportback", "dolnośląskie"),   # pasuje we wszystkim poza województwem
              _oto("502", "a4-limousine", "śląskie")]         # pasuje do A4, sito A5 je odrzuca
_wys_main = []
_katalog = _P(_tf.mkdtemp())
_podmienione = ("fetch_listings_otomoto", "wycena_sprawnego", "czytaj_przyciski", "uzupelnij_ze_strony", "fetch_listings_olx",
                "sprawdz_wystawce", "send_telegram", "SEEN_FILE", "SEEN_OLX_FILE", "SEEN_WYSTAWCY_FILE", "STAN_FILE")
_zapis_main = {n: getattr(ot, n, None) for n in _podmienione}
_seen_main = {}
try:
    ot.fetch_listings_otomoto = lambda s, pages=4: [dict(l) for l in _pula_audi] if "/audi/" in s["url"] else []
    ot.wycena_sprawnego = lambda s, l: {"n": 0}
    ot.czytaj_przyciski = lambda o: 0
    ot.uzupelnij_ze_strony = lambda l: l
    ot.fetch_listings_olx = lambda s: []
    ot.sprawdz_wystawce = lambda *a, **k: 0
    ot.send_telegram = lambda t, **k: _wys_main.append(t) or True
    for _n in ("SEEN_FILE", "SEEN_OLX_FILE", "SEEN_WYSTAWCY_FILE", "STAN_FILE"):
        setattr(ot, _n, _katalog / f"{_n}.json")
    # stan sprzed poprawki: A4 już raz "odhaczone" pustym wpisem, plus śmieć spoza puli
    ot.SEEN_FILE.write_text(_js.dumps({"502": {}, "999": {}}))
    ot.main()
    _seen_main = _js.loads(ot.SEEN_FILE.read_text())
finally:
    for _n, _v in _zapis_main.items():
        if _v is None:
            delattr(ot, _n)
        else:
            setattr(ot, _n, _v)
sprawdz("A4 ze wspólnej puli z A5 poszło, mimo starego pustego wpisu",
        any("a4-limousine 502" in m for m in _wys_main))
sprawdz("auto z dolnośląskiego nie poszło", not any(" 501" in m for m in _wys_main))
sprawdz("w zapisanym seen nie ma pustych wpisów, starych ani nowych",
        _seen_main and "999" not in _seen_main and all(v for v in _seen_main.values()))

print("\n== kombi nie wchodzi nigdzie (decyzja właściciela) ==")
# 16.09.2026 kombi zostało wpuszczone bez zgody i na Telegram poszły trzy A4 Avant
# i Touring. Właściciel: "mówiłem, że mnie kombi nie interesuje".
# WŁASNOŚĆ: kombi zapisane którymkolwiek sposobem (OLX "estate-car", Otomoto
# "kombi", klucz modelu Avanta) odpada w obu wyszukiwaniach, które mają wersję kombi.
for _nazwa, _k, _model in (("A4", K_A4, "a4-limousine"), ("Seria 3", K_SERIA3, "3-as-sorozat")):
    for _body in ("estate-car", "kombi"):
        sprawdz(f"{_nazwa}: nadwozie '{_body}' odpada",
                not ot.sprawdz_kryteria(auto(model_key=_model, model_label="",
                                             year=_k["rok"][0] + 1, body=_body), _k)[0])
sprawdz("A4: klucz modelu Avanta odpada nawet bez podanego nadwozia",
        not ot.sprawdz_kryteria(auto(model_key="a4-avant", model_label="A4 Avant",
                                     year=2017, body=None), K_A4)[0])
sprawdz("OLX nie pyta o Avanta",
        not any("a4-avant" in s["params"].values() for s in ot.OLX_SEARCHES))

print("\n== kryteria ustalone przez właściciela (16.09.2026) ==")
# Po wpadce z kombi i szerszymi rocznikami właściciel: "wracać do starych".
# Test przypina decyzję, żeby żadna zmiana nie poszerzyła kryteriów po cichu.
sprawdz("roczniki jak z 22.08: A5 i A4 2015-2019, Seria 3 2019-2021, Seria 4 2021-2023",
        [tuple(s["kryteria"]["rok"]) for s in ot.SEARCHES]
        == [(2015, 2019), (2015, 2019), (2019, 2021), (2021, 2023)])
sprawdz("adresy Otomoto pytają o te same roczniki co kryteria",
        all(f"year%3Afrom%5D={s['kryteria']['rok'][0]}" in s["url"]
            and f"year%3Ato%5D={s['kryteria']['rok'][1]}" in s["url"] for s in ot.SEARCHES))
sprawdz("przebieg do 200 tys. km i tylko cztery województwa",
        ot.PRZEBIEG_MAX == 200_000
        and ot.REGIONS_ALLOWED == {"małopolskie", "podkarpackie", "świętokrzyskie", "śląskie"})

print("\n== wysyłka potwierdzana: odmowa Telegrama nie gubi auta ==")
# Regresja 16.09.2026: odmowa Telegrama kończyła się linijką w logu, a ogłoszenie
# było już odhaczone. Właściciel: "chcę pewność, że mnie powiadomisz".


def _uruchom_main(katalog, wysylka, pula_audi=(), olx_a4=()):
    podmiany = {
        "fetch_listings_otomoto": lambda s, pages=4: [dict(l) for l in pula_audi] if "/audi/" in s["url"] else [],
        "wycena_sprawnego": lambda s, l: {"n": 0},
        "czytaj_przyciski": lambda o: 0,
        "uzupelnij_ze_strony": lambda l: l,
        "fetch_listings_olx": lambda s: [dict(l) for l in olx_a4] if s is ot.OLX_SEARCHES[1] else [],
        "sprawdz_wystawce": lambda *a, **k: 0,
        "ocen_dzien": lambda *a, **k: None,
        "send_telegram": wysylka,
    }
    pliki = {n: katalog / f"{n}.json" for n in ("SEEN_FILE", "SEEN_OLX_FILE", "SEEN_WYSTAWCY_FILE", "STAN_FILE")}
    # getattr z domyślnym: na starej wersji (reguła 2) części funkcji nie ma,
    # a test ma wtedy PAŚĆ na sprawdzeniu, nie wywrócić się w przygotowaniu
    stare = {n: getattr(ot, n, None) for n in list(podmiany) + list(pliki)}
    try:
        for n, v in {**podmiany, **pliki}.items():
            setattr(ot, n, v)
        ot.main()
    finally:
        for n, v in stare.items():
            if v is None:
                delattr(ot, n)
            else:
                setattr(ot, n, v)
    return _js.loads(pliki["SEEN_FILE"].read_text()), _js.loads(pliki["SEEN_OLX_FILE"].read_text())


_kat = _P(_tf.mkdtemp())
_auto602 = _oto("602", "a4-limousine", "śląskie")
_s1, _ = _uruchom_main(_kat, lambda t, **k: None, pula_audi=[_auto602])      # Telegram odmawia
sprawdz("odmowa Telegrama: treść wiadomości czeka we wpisie",
        bool((_s1.get("602") or {}).get("do_wyslania")))
_dostarczone = []
_s2, _ = _uruchom_main(_kat, lambda t, **k: _dostarczone.append(t) or True, pula_audi=[_auto602])
sprawdz("następny bieg dosyła wiadomość, dokładnie raz",
        sum("a4-limousine 602" in m for m in _dostarczone) == 1)
sprawdz("...i zdejmuje ją z wpisu", "do_wyslania" not in _s2.get("602", {}))

print("\n== lustro z OLX dostaje wynik we wpisie ==")
_kat = _P(_tf.mkdtemp())
_lustro = dict(_oto("x", "a4-limousine", "śląskie"), id="olx_777", braki=[],
               url="https://www.olx.pl/d/oferta/x-CID5-ID777.html",
               external_url="https://www.otomoto.pl/osobowe/oferta/x-ID603.html")
_wys_l = []
_so, _sl = _uruchom_main(_kat, lambda t, **k: _wys_l.append(t) or True,
                         pula_audi=[_oto("603", "a4-limousine", "śląskie")], olx_a4=[_lustro])
sprawdz("auto poszło raz, lustro z OLX nie zdublowało wiadomości",
        sum("a4-limousine" in m for m in _wys_l) == 1)
sprawdz("lustro ma wynik we wpisie (nie pusty {}), więc ostatnia zapora milczy",
        (_sl.get("olx_777") or {}).get("powod") == "lustro" and not any("błąd bota" in m for m in _wys_l))

print("\n== ostatnia zapora: pasujące ogłoszenie bez wyniku ==")
# Tak wyglądał błąd, przez który A4 nie wysłało niczego do 16.09.2026: bot chodził,
# alarmy milczały, a auta ginęły po cichu. Teraz taki stan krzyczy i niesie linki.
import inspect as _inspect  # noqa: E402
_obsluz = getattr(ot, "obsluz_bez_wyniku", None)
_wys_z = []
_stary_send = ot.send_telegram
try:
    ot.send_telegram = lambda t, **k: _wys_z.append(t) or True
    if _obsluz is None:
        sprawdz("jest ostatnia zapora na ogłoszenia bez wyniku", False)
    else:
        _sz = {"a": {}}
        _obsluz([(_sz, "a", "https://x.pl/1", "Audi A4"), (_sz, "b", "https://x.pl/2", "BMW 320d")], "2026-09-16")
        sprawdz("jeden alarm z linkami do obu ogłoszeń",
                len(_wys_z) == 1 and "https://x.pl/1" in _wys_z[0] and "https://x.pl/2" in _wys_z[0])
        sprawdz("oba dostały wynik, więc alarm się nie powtórzy", bool(_sz.get("a")) and bool(_sz.get("b")))
finally:
    ot.send_telegram = _stary_send
sprawdz("main() naprawdę woła ostatnią zaporę", "obsluz_bez_wyniku(" in _inspect.getsource(ot.main))

print("\n== podsumowanie dnia: brak wiadomości = bot nie działa ==")
from zoneinfo import ZoneInfo as _Strefa  # noqa: E402
_dzien = getattr(ot, "ocen_dzien", None)
_wys_d = []
_stary_send, _stary_plik = ot.send_telegram, ot.STAN_FILE
try:
    ot.send_telegram = lambda t, **k: _wys_d.append(t) or True
    ot.STAN_FILE = _P(_tf.mkdtemp()) / "stan.json"
    if _dzien is None:
        sprawdz("jest codzienne podsumowanie", False)
    else:
        def _t(dzien, godz, minuta=0):
            return _dt(2026, 9, dzien, godz, minuta, tzinfo=_Strefa("Europe/Warsaw")).astimezone(_tz.utc)
        _ok = {"otomoto_ok": True, "olx_ok": True, "wyslane": 0, "pasujace_polska": 5,
               "pasujace_region": 0, "problemy": []}
        _dzien(dict(_ok), teraz=_t(16, 10))
        _dzien(dict(_ok, otomoto_ok=False, problemy=["Otomoto: pełne strony"]), teraz=_t(16, 10, 30))
        _dzien(dict(_ok, wyslane=2), teraz=_t(16, 11))
        sprawdz("przed 18:00 cisza", _wys_d == [])
        _dzien(dict(_ok), teraz=_t(16, 18, 5))
        sprawdz("po 18:00 jedno podsumowanie", len(_wys_d) == 1)
        sprawdz("...z liczbą sprawdzeń i wysłanych ofert",
                "Sprawdzeń dziś: 4 (pierwsze o 10:00)" in _wys_d[0] and "Nowe oferty wysłane dziś: 2" in _wys_d[0])
        sprawdz("...z niepełnym Otomoto i problemem dnia",
                "odpowiadało w 3 z 4" in _wys_d[0] and "pełne strony" in _wys_d[0])
        sprawdz("...z rynkiem w Polsce i w województwach",
                "w Twoich województwach 0, w całej Polsce 5" in _wys_d[0])
        sprawdz("...i z umową: brak podsumowania to awaria", "Brak tego podsumowania" in _wys_d[0])
        _dzien(dict(_ok), teraz=_t(16, 18, 35))
        sprawdz("kolejny bieg po 18:00: bez powtórki", len(_wys_d) == 1)
        _dzien(dict(_ok), teraz=_t(17, 18, 10))
        sprawdz("następny dzień: nowe podsumowanie, liczniki od zera",
                len(_wys_d) == 2 and "Sprawdzeń dziś: 1 (pierwsze o 18:10)" in _wys_d[1])
        ot.send_telegram = lambda t, **k: _wys_d.append(t) and False      # Telegram odmawia
        _dzien(dict(_ok), teraz=_t(18, 18, 5))
        ot.send_telegram = lambda t, **k: _wys_d.append(t) or True
        _dzien(dict(_ok), teraz=_t(18, 18, 35))
        sprawdz("odmowa Telegrama: podsumowanie idzie w następnym biegu",
                len(_wys_d) == 4 and "Sprawdzeń dziś: 2 (pierwsze o 18:05)" in _wys_d[3])
        sprawdz("bez długich myślników w podsumowaniu",
                not any(d in m for m in _wys_d for d in _DLUGIE_MYSLNIKI))
finally:
    ot.send_telegram, ot.STAN_FILE = _stary_send, _stary_plik

print("\n== wywrotka programu nie jest ciszą ==")
_wywrotka = getattr(ot, "zglos_wywrotke", None)
_wys_w = []
_stary_send, _stary_plik = ot.send_telegram, ot.STAN_FILE
try:
    ot.send_telegram = lambda t, **k: _wys_w.append(t) or True
    ot.STAN_FILE = _P(_tf.mkdtemp()) / "stan.json"
    if _wywrotka is None:
        sprawdz("jest alarm o wywrotce programu", False)
    else:
        _t0 = _dt(2026, 9, 16, 12, 0, tzinfo=_tz.utc)
        _wywrotka(KeyError("x"), teraz=_t0)
        _wywrotka(KeyError("x"), teraz=_t0 + _td(hours=1))
        sprawdz("jeden alarm, bez spamu co pół godziny", len(_wys_w) == 1 and "błąd w programie" in _wys_w[0])
        _wywrotka(KeyError("x"), teraz=_t0 + _td(hours=7))
        sprawdz("trwa dłużej niż 6 h → przypomnienie", len(_wys_w) == 2)
finally:
    ot.send_telegram, ot.STAN_FILE = _stary_send, _stary_plik
_wejscie = _Pth(ot.__file__).read_text().split('if __name__ == "__main__":')[-1]
sprawdz("wejście programu łapie wywrotkę i zgłasza ją", "zglos_wywrotke(" in _wejscie and "raise" in _wejscie)

print("\n== wycena: to samo auto sprawne, nie przypadkowa mediana ==")
# Regresja 17.09.2026: przy A4 Avant za 6 500 zł wiadomość pokazała "OLX mediana
# 47 500 zł, różnica +41 000 zł", czyli medianę przypadkowych Audi, w większości
# nieuszkodzonych, a procent liczyła względem wszystkich uszkodzonych Audi.
_pytania_wyceny = []


def _olx_auto(i, cena, rok, km, naped="all-wheel-permanent", stan="notdamaged"):
    params = [{"key": "price", "value": {"value": cena, "label": f"{cena} zł"}},
              {"key": "year", "value": {"key": str(rok)}}, {"key": "milage", "value": {"key": str(km)}},
              {"key": "model", "value": {"key": "a5-sportback", "label": "A5 Sportback"}},
              {"key": "petrol", "value": {"key": "diesel"}}, {"key": "transmission", "value": {"key": "automatic"}},
              {"key": "enginesize", "value": {"key": "1968"}}, {"key": "car_body", "value": {"key": "sedan"}},
              {"key": "condition", "value": {"key": stan}}]
    if naped:
        params.append({"key": "drive", "value": {"key": naped}})
    return {"id": i, "title": f"Audi {i}", "url": f"https://www.olx.pl/d/oferta/a-{i}.html", "params": params,
            "location": {"region": {"name": "Śląskie"}, "city": {"name": "Katowice"}}}


def _olx_odpowiedz(ogloszenia):
    def get(url, timeout=20, **kw):
        _pytania_wyceny.append(url)
        return _olx.OdpowiedzOLX(200, _json.dumps({"data": ogloszenia, "links": {}}))
    return get


_wycena = getattr(ot, "wycena_sprawnego", None)
_rozbitek = auto(year=2017, mileage_num=165000, price_num=64900)
if _wycena is None:
    sprawdz("jest wycena sprawnego auta", False)
else:
    try:
        _ofe = [_olx_auto(i, c, 2017, k) for i, (c, k) in enumerate(
            [(80000, 150000), (85000, 160000), (88000, 170000), (90000, 175000), (95000, 180000), (99000, 190000)], 1)]
        _ofe += [_olx_auto(20, 40000, 2017, 165000, naped=None), _olx_auto(21, 41000, 2017, 166000, naped=None)]
        _olx.olx_get = _olx_odpowiedz(_ofe)
        _w = _wycena(ot.OLX_SEARCHES[0], _rozbitek)
        sprawdz("pyta OLX tylko o sprawne auta, rocznik +-1",
                _pytania_wyceny and all("notdamaged" in u and "year%3Afrom=2016" in u and "year%3Ato=2018" in u
                                        for u in _pytania_wyceny))
        sprawdz("auta z nieznanym napędem nie zaniżają porównania", _w.get("n") == 6)
        sprawdz("mediana z sześciu sprawnych 4x4", _w.get("mediana") == 89000)
        _olx.olx_get = _olx_odpowiedz([_olx_auto(30 + i, 70000 + i * 1000, 2018, 205000 + i * 1000) for i in range(5)])
        _w2 = _wycena(ot.OLX_SEARCHES[0], auto(year=2018, mileage_num=199000, price_num=50000))
        sprawdz("porównanie auta z 199 tys. km nie urywa się na limicie 200 tys.",
                _w2.get("n") == 5 and bool(_w2.get("mediana")))
        _olx.olx_get = _olx_odpowiedz(_ofe[:3])
        _w3 = _wycena(ot.OLX_SEARCHES[0], _rozbitek)
        sprawdz("przy 3 porównywalnych nie ma mediany", _w3.get("mediana") is None and _w3.get("n") == 3)
        _txt3 = ot.tekst_wyceny(_rozbitek, _w3)
        sprawdz("...i wiadomość mówi wprost 'nie wiem'", "nie wiem" in _txt3 and "3 porównywalne" in _txt3)
        _olx.olx_get = _olx_odpowiedz([_olx_auto(40 + i, 80000 + i * 1000, 2017, 165000 + (45000 if i % 2 else -45000))
                                       for i in range(5)])
        _w4 = _wycena(ot.OLX_SEARCHES[0], _rozbitek)
        sprawdz("rzadkie auto: okno przebiegu poszerza się do +-60 tys.",
                _w4.get("okno") == 60000 and bool(_w4.get("mediana")))
    finally:
        _przywroc()
    _txt = ot.tekst_wyceny(_rozbitek, _w)
    sprawdz("wiadomość podaje medianę, liczbę ogłoszeń i zastrzeżenie o cenach wystawionych",
            "mediana 89 000 zł" in _txt and "z 6 ogłoszeń" in _txt and "ceny wystawione, nie transakcyjne" in _txt)
    sprawdz("...i ile zostaje na naprawę i zysk", "24 100 zł na naprawę i zysk" in _txt)
    _oferta = ot.tekst_oferty("OLX", dict(_rozbitek, title="Audi <b>A5</b> & spółka", url="https://x.pl/a?b=1&c=2",
                                          price_str="64 900 PLN", city="Kraków", created_at="", braki=[]),
                              "OLX A5", _w)
    sprawdz("w ofercie nie ma już mylących liczb (Score, OLX mediana, szacunek naprawy, ogniki)",
            not any(s in _oferta for s in ("Score", "OLX mediana", "Szac. naprawa", "🔥")))
    sprawdz("tytuł z ogłoszenia nie psuje trybu HTML Telegrama", "&lt;b&gt;A5&lt;/b&gt; &amp; spółka" in _oferta)
    sprawdz("bez długich myślników w ofercie", not any(d in _oferta for d in _DLUGIE_MYSLNIKI))

print("\n== zdjęcie w wiadomości ==")
_wezel = {"id": "9", "title": "Audi A5",
          "thumbnail": {"x1": "https://ireland.apollo.olxcdn.com/v1/files/abc-OTOMOTOPL/image;s=320x240",
                        "x2": "https://ireland.apollo.olxcdn.com/v1/files/abc-OTOMOTOPL/image;s=640x480"}}
sprawdz("Otomoto: zdjęcie z miniatury wyników, w większym rozmiarze",
        ((ot._parse_node(_wezel) or {}).get("zdjecie") or "").endswith(";s=1080x720"))
_olx_ze_zdjeciem = dict(_olx_auto(50, 30000, 2017, 165000, stan="damaged"),
                        photos=[{"link": "https://ireland.apollo.olxcdn.com:443/v1/files/p1-PL/image;s={width}x{height}"}])
_olx.olx_get = _olx_odpowiedz([_olx_ze_zdjeciem])
try:
    _lz = ot.fetch_listings_olx(ot.OLX_SEARCHES[0])
finally:
    _przywroc()
sprawdz("OLX: zdjęcie z listy, bez wzoru {width}x{height}",
        bool(_lz) and (_lz[0].get("zdjecie") or "").endswith("image;s=1080x720"))

print("\n== wysyłka: zdjęcie z przyciskami, a gdy nie przejdzie, sam tekst ==")
_posty = []


class _OdpTg:
    def __init__(self, ok):
        self.ok = ok

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError("400")


def _bezpiecznie(f, *a, **k):
    try:
        return f(*a, **k)
    except TypeError:
        return None        # stara wersja nie zna zdjęć ani przycisków: test ma paść, nie wywrócić się


_prawdziwy_send = _zapis["tg"]
_stary_post = ot.requests.post
try:
    ot.requests.post = lambda url, json=None, timeout=None: (_posty.append((url.rsplit("/", 1)[-1], json)), _OdpTg(True))[1]
    _ok = _bezpiecznie(_prawdziwy_send, "tekst", zdjecie="https://x.pl/z.jpg", przyciski=[[{"text": "a", "callback_data": "b"}]])
    sprawdz("oferta ze zdjęciem idzie jako zdjęcie z podpisem i przyciskami",
            _ok is True and _posty[0][0] == "sendPhoto" and _posty[0][1].get("caption") == "tekst"
            and "reply_markup" in _posty[0][1])
    _posty.clear()
    ot.requests.post = lambda url, json=None, timeout=None: (_posty.append((url.rsplit("/", 1)[-1], json)),
                                                              _OdpTg("sendMessage" in url))[1]
    _ok = _bezpiecznie(_prawdziwy_send, "tekst", zdjecie="https://x.pl/z.jpg", przyciski=[[{"text": "a", "callback_data": "b"}]])
    sprawdz("zdjęcie odrzucone: dochodzi sam tekst, dalej z przyciskami",
            _ok is True and [p[0] for p in _posty] == ["sendPhoto", "sendMessage"] and "reply_markup" in _posty[1][1])
    _posty.clear()
    ot.requests.post = lambda url, json=None, timeout=None: (_posty.append((url.rsplit("/", 1)[-1], json)), _OdpTg(False))[1]
    sprawdz("gdy nie przejdzie nic, wynik to False (i ponawianie)", _prawdziwy_send("tekst") is False)
finally:
    ot.requests.post = _stary_post

print("\n== przyciski pod ofertą i zapis kliknięć ==")
_przyciski = getattr(ot, "przyciski_oferty", None)
if _przyciski is None:
    sprawdz("są przyciski pod ofertą", False)
else:
    _klaw = _przyciski("olx_1096475334999")
    sprawdz("trzy powody: nie interesuje, za duża szkoda, za drogo",
            sorted(b["text"] for rzad in _klaw for b in rzad) == ["Nie interesuje", "Za drogo", "Za duża szkoda"])
    sprawdz("dane przycisku mieszczą się w limicie Telegrama (64 bajty)",
            all(len(b["callback_data"].encode()) <= 64 for rzad in _klaw for b in rzad))
_kat = _P(_tf.mkdtemp())
_kwargi = []
_uruchom_main(_kat, lambda t, **k: _kwargi.append(k) or True, pula_audi=[_oto("701", "a4-limousine", "śląskie")])
sprawdz("oferta wysłana przez main() ma przyciski", any(k.get("przyciski") for k in _kwargi))

_czytaj = getattr(ot, "czytaj_przyciski", None)
if _czytaj is None:
    sprawdz("kliknięcia przycisków są zapisywane", False)
else:
    _stary_get, _stary_post = ot.requests.get, ot.requests.post
    _stary_plik, _stary_odrz = ot.STAN_FILE, ot.ODRZUTY_FILE
    _posty.clear()
    try:
        ot.STAN_FILE = _P(_tf.mkdtemp()) / "stan.json"
        ot.ODRZUTY_FILE = _P(_tf.mkdtemp()) / "odrzuty.jsonl"
        _zdarzenia = [
            {"update_id": 10, "callback_query": {"id": "a", "data": "odrz|sz|olx_5",
                                                 "message": {"message_id": 77, "chat": {"id": ot.TELEGRAM_CHAT_ID}}}},
            {"update_id": 11, "callback_query": {"id": "b", "data": "odrz|dr|olx_5",
                                                 "message": {"message_id": 78, "chat": {"id": "obcy-czat"}}}},
            {"update_id": 12, "callback_query": {"id": "c", "data": "zapisane",
                                                 "message": {"message_id": 77, "chat": {"id": ot.TELEGRAM_CHAT_ID}}}},
        ]

        class _OdpGet:
            def raise_for_status(self):
                pass

            def json(self):
                return {"result": _zdarzenia}

        ot.requests.get = lambda url, params=None, timeout=None: _OdpGet()
        ot.requests.post = lambda url, json=None, timeout=None: (_posty.append((url.rsplit("/", 1)[-1], json)), _OdpTg(True))[1]
        _n = _czytaj({"olx_5": {"title": "Audi A4", "price_num": 30000, "year": 2017, "url": "https://x.pl/5"}})
        _rekordy = [_js.loads(l) for l in ot.ODRZUTY_FILE.read_text().splitlines()]
        sprawdz("zapisane jedno kliknięcie z naszego czatu, obcy czat i 'zapisane' pominięte",
                _n == 1 and len(_rekordy) == 1)
        sprawdz("...z powodem i danymi auta",
                _rekordy[0]["powod"] == "za duża szkoda" and _rekordy[0]["title"] == "Audi A4"
                and _rekordy[0]["price_num"] == 30000)
        sprawdz("przyciski pod wiadomością zamieniają się w 'zapisane'",
                any(m == "editMessageReplyMarkup" and "zapisane" in _js.dumps(d, ensure_ascii=False) for m, d in _posty))
        sprawdz("wskaźnik kolejki przesunięty za wszystkie zdarzenia",
                _js.loads(ot.STAN_FILE.read_text()).get("telegram_offset") == 13)
        ot._bieg_reset()

        def _blad_get(url, params=None, timeout=None):
            raise RuntimeError("409 Conflict")
        ot.requests.get = _blad_get
        sprawdz("nieudany odczyt kliknięć trafia do problemów dnia",
                _czytaj({}) == 0 and any("kliknięć" in p for p in ot._bieg["problemy"]))
    finally:
        ot.requests.get, ot.requests.post = _stary_get, _stary_post
        ot.STAN_FILE, ot.ODRZUTY_FILE = _stary_plik, _stary_odrz
sprawdz("plik kliknięć jest zapisywany do repo przez workflow", "odrzuty_auta.jsonl" in _wf)

print("\n== ponawianie ma limit i nie gubi zdjęcia ani przycisków ==")
_proby_dos = []
_stary_send = ot.send_telegram
try:
    ot._bieg_reset()
    ot.send_telegram = lambda t, **k: _proby_dos.append(k) and False
    _sd = {"olx_9": {"url": "https://x.pl/9", "do_wyslania": "oferta", "zdjecie": "https://x.pl/9.jpg",
                     "przyciski": True, "proby_wysylki": getattr(ot, "DOSYLKA_PROB_MAX", 48) - 1}}
    ot.dosylka(_sd, lambda s: None)
    sprawdz("ponowienie niesie zdjęcie i przyciski",
            bool(_proby_dos) and bool(_proby_dos[0].get("zdjecie")) and bool(_proby_dos[0].get("przyciski")))
    sprawdz("po dobie prób wiadomość schodzi z kolejki i trafia do problemów z linkiem",
            "do_wyslania" not in _sd["olx_9"] and any("https://x.pl/9" in p for p in ot._bieg["problemy"]))
finally:
    ot.send_telegram = _stary_send


# === SORTOWANIE PO DACIE (19.09.2026) ==================================
# Otomoto domyślnie układa wyniki po trafności, a bot bierze tylko `pages`
# pierwszych stron - więc ucięte mogły być najnowsze ogłoszenia. Zmierzone
# z runnera tym samym `_fetch_page`, oba adresy w odstępie 8 s: bez
# sortowania wiek pierwszych pięciu ofert to 14570, 12734, 7063, 22757,
# 21766 minut (kolejność losowa), z sortowaniem 359, 1099, 1253, 1253,
# 1404 (rosnąco). Raport: gałąź `diagnoza/raporty`.
print("\nSortowanie po dacie:")
import inspect  # noqa: E402

sprawdz("jest czym posortować po dacie", hasattr(ot, "adres_po_dacie"))
if hasattr(ot, "adres_po_dacie"):
    _u = "https://www.otomoto.pl/osobowe/audi/a5?search%5Bfilter_enum_drive%5D=awd"
    sprawdz("parametr sortowania dochodzi do adresu z pytajnikiem",
            ot.adres_po_dacie(_u).endswith(ot.ORDER_PO_DACIE)
            and ot.adres_po_dacie(_u).count("?") == 1)
    sprawdz("adres bez pytajnika dostaje pytajnik, nie ampersand",
            ot.adres_po_dacie("https://x.pl/a") == "https://x.pl/a?" + ot.ORDER_PO_DACIE)
    sprawdz("IDEMPOTENTNA - drugie wywołanie nie dokłada parametru dwa razy",
            ot.adres_po_dacie(ot.adres_po_dacie(_u)) == ot.adres_po_dacie(_u))
    sprawdz("parametr jest dokładnie ten ZMIERZONY, nie podobny",
            ot.ORDER_PO_DACIE == "search%5Border%5D=created_at_first%3Adesc")

    # KAŻDE wyszukiwanie dostaje sortowanie SAMO, bez wpisywania go przy
    # każdym wpisie w SEARCHES. Nowy wpis dodany za pół roku ma je mieć
    # bez pamiętania o nim - dlatego test pyta o wynik dla WSZYSTKICH.
    sprawdz("wszystkie wyszukiwania dostają sortowanie z jednego miejsca",
            all(ot.ORDER_PO_DACIE in ot.adres_po_dacie(x["url"]) for x in ot.SEARCHES))
    sprawdz("żaden wpis w SEARCHES nie ma sortowania wklejonego na sztywno",
            not any(ot.ORDER_PO_DACIE in x["url"] for x in ot.SEARCHES))

    # Strażnik na ŚCIEŻKĘ POBIERANIA: sam fakt, że funkcja istnieje, nic nie
    # daje, jeśli pętla jej nie woła. Ten sam błąd co "komentarz opisujący
    # zasadę to nie jest zasada" z 18.09 w bocie rowerowym.
    _src = inspect.getsource(ot.fetch_listings_otomoto)
    sprawdz("pętla pobierania NAPRAWDĘ woła adres_po_dacie",
            "adres_po_dacie(" in _src)
    sprawdz("stary sklejacz adresu zniknął, więc nie ma drugiej ścieżki",
            'sep = "&" if "?" in search["url"] else "?"' not in _src)

print("\n== zniknięcie ogłoszenia śledzonego wystawcy ==")
# Właściciel 25.09.2026: "chcę powiadomienie, kiedy ogłoszenie śledzonego
# wystawcy znika". Tego dnia cztery pilnowane ogłoszenia Leszka zniknęły po
# cichu (Otomoto potwierdzało 410 przy każdym), a bot nie pisnął.
_znik = getattr(ot, "sprawdz_znikniecia", None)
_wszystkie_z = []


def _wpis_zywy(**nad):
    baza = {"title": "BMW 320d", "url": "https://www.olx.pl/d/oferta/bmw-CID5-ID1.html",
            "otomoto_url": "https://www.otomoto.pl/osobowe/oferta/bmw-ID6X.html",
            "kontakt": "Leszek", "pewnosc": "potwierdzony", "date": "2026-09-01",
            "cena": "38 900 zł", "wystawione": "2026-09-01", "ostatnio_widziane": "2026-09-24"}
    baza.update(nad)
    return baza


def _w_miejscowosci(lid, cena="40 000 zł", kiedy="2026-09-01T10:00:00+02:00"):
    return {"id": lid.replace("w_", ""), "created_time": kiedy,
            "params": [{"key": "price", "value": {"label": cena}}]}


if _znik is None:
    sprawdz("jest powiadomienie o zniknięciu ogłoszenia wystawcy", False)
else:
    _wys_z = []
    _stary_send, _stary_sid, _stary_dziennik = ot.send_telegram, ot.otomoto_seller_id, ot.ZYCIE_WYSTAWCY_FILE
    try:
        ot.send_telegram = lambda t, **k: (_wys_z.append(t), _wszystkie_z.append(t))[0] or True
        ot.ZYCIE_WYSTAWCY_FILE = _P(_tf.mkdtemp()) / "zycie.jsonl"

        _s = {"w_1": _wpis_zywy(cena="brak ceny", wystawione="")}
        _znik(W, _s, {"w_1": _w_miejscowosci("w_1", "37 500 zł")}, True)
        sprawdz("żywe ogłoszenie odświeża cenę i datę wystawienia",
                _s["w_1"]["cena"] == "37 500 zł" and _s["w_1"]["wystawione"] == "2026-09-01" and _wys_z == [])

        ot.otomoto_seller_id = lambda u: ot.ZDJETA
        _s = {"w_1": _wpis_zywy()}
        _znik(W, _s, {}, True)
        sprawdz("jeden brak na liście to jeszcze nie zniknięcie",
                _wys_z == [] and "znikla" not in _s["w_1"])
        _znik(W, _s, {}, True)
        sprawdz("drugi brak plus 410 z Otomoto: jedno powiadomienie",
                len(_wys_z) == 1 and bool(_s["w_1"].get("znikla")))
        sprawdz("...z autem, ceną i czasem wiszenia",
                all(x in _wys_z[0] for x in ("BMW 320d", "38 900 zł", "wisiało")))
        sprawdz("...i bez udawania, że wie o sprzedaży", "Nie wiem, czy sprzedane" in _wys_z[0])
        _znik(W, _s, {}, True)
        sprawdz("kolejny bieg nie powtarza powiadomienia", len(_wys_z) == 1)
        _dziennik = [_js.loads(l) for l in ot.ZYCIE_WYSTAWCY_FILE.read_text().splitlines()]
        sprawdz("zdarzenie trafiło do dziennika z autem i liczbą dni",
                len(_dziennik) == 1 and _dziennik[0]["zdarzenie"] == "zniknelo"
                and _dziennik[0]["title"] == "BMW 320d" and _dziennik[0]["po_dniach"] is not None)

        _wys_z.clear()
        ot.otomoto_seller_id = lambda u: W["otomoto_seller_id"]
        _s = {"w_2": _wpis_zywy()}
        _znik(W, _s, {}, True)
        _znik(W, _s, {}, True)
        sprawdz("wypadło z listy, ale na Otomoto stoi dalej: cisza i wyzerowany licznik",
                _wys_z == [] and "znikla" not in _s["w_2"] and "brak_biegow" not in _s["w_2"])

        ot.otomoto_seller_id = lambda u: None
        _s = {"w_3": _wpis_zywy()}
        _znik(W, _s, {}, True)
        _znik(W, _s, {}, True)
        sprawdz("nieczytelna strona oferty: żadnego powiadomienia, wpis czeka",
                _wys_z == [] and "znikla" not in _s["w_3"])

        _s = {"w_4": _wpis_zywy(otomoto_url="", pewnosc="niepotwierdzony")}
        for _ in range(ot.BRAK_BIEGOW_BEZ_LUSTRA - 1):
            _znik(W, _s, {}, True)
        sprawdz("bez lustra z Otomoto bot czeka dłużej", _wys_z == [])
        _znik(W, _s, {}, True)
        sprawdz("...ale w końcu powiadamia i mówi, skąd to wie",
                len(_wys_z) == 1 and "liście OLX" in _wys_z[0])

        _wys_z.clear()
        ot._bieg_reset()
        _s = {"w_5": _wpis_zywy()}
        _znik(W, _s, {}, False)
        sprawdz("ucięta lista miejscowości nie może udawać zniknięć",
                _wys_z == [] and "brak_biegow" not in _s["w_5"]
                and any("urwana" in p for p in ot._bieg["problemy"]))
    finally:
        ot.send_telegram, ot.otomoto_seller_id, ot.ZYCIE_WYSTAWCY_FILE = _stary_send, _stary_sid, _stary_dziennik
sprawdz("bez długich myślników w powiadomieniach o zniknięciu",
        _wszystkie_z and not any(d in m for m in _wszystkie_z for d in _DLUGIE_MYSLNIKI))

print("\n== lista miejscowości czytana do końca ==")
# Gdyby bot czytał tylko pierwsze 50 ogłoszeń, auta z dalszych stron udawałyby
# zniknięte. Dziś w Oleśnicy jest ich 30, ale to nie jest żadna gwarancja.
_strony_w, _wys_w2 = [], []


def _fake_olx_wystawca(url, timeout=20, **kw):
    from urllib.parse import urlparse, parse_qs
    _strony_w.append(url)
    offset = int(parse_qs(urlparse(url).query).get("offset", ["0"])[0])
    if offset == 0:
        dane = [{"id": 800 + i, "title": f"auto {i}", "contact": {"name": "Kuba"}, "params": []}
                for i in range(50)]
        return _olx.OdpowiedzOLX(200, _json.dumps({"data": dane, "links": {"next": {"href": "x"}}}))
    return _olx.OdpowiedzOLX(200, _json.dumps(
        {"data": [{"id": 900, "title": "BMW Leszka", "contact": {"name": "Leszek"}, "params": []}], "links": {}}))


_s_pag = {"w_900": _wpis_zywy(title="BMW Leszka")}
_olx.olx_get = _fake_olx_wystawca
ot.send_telegram = lambda t, **k: _wys_w2.append(t) or True
ot.otomoto_seller_id = lambda u: ot.ZDJETA
try:
    ot.sprawdz_wystawce(W, _s_pag)
    ot.sprawdz_wystawce(W, _s_pag)
finally:
    _przywroc()
sprawdz("bot czyta kolejne strony listy miejscowości", len(_strony_w) >= 4)
sprawdz("ogłoszenie z drugiej strony nie udaje zniknięcia",
        "znikla" not in _s_pag["w_900"] and not any("ZNIKNĘŁO" in m for m in _wys_w2))
sprawdz("dziennik zniknięć jest zapisywany do repo przez workflow", "zycie_wystawcy.jsonl" in _wf)

print()
if bledy:
    print(f"NIEPOWODZENIE: {len(bledy)} testów nie przeszło")
    for b in bledy:
        print(f"   - {b}")
    sys.exit(1)
print("Wszystkie testy przeszły.")
