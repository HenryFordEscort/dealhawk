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
sprawdz("rocznik poza zakresem odpada (2022 przy 2013-2021)",
        not ot.sprawdz_kryteria(auto(year=2022), K_A5)[0])
sprawdz("...z obu stron (2012)", not ot.sprawdz_kryteria(auto(year=2012), K_A5)[0])
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
# od 16.09.2026 kombi wpuszczone świadomie (patrz komentarz przy SEARCHES)
sprawdz("Seria 3 Touring przechodzi",
        ot.sprawdz_kryteria(auto(model_key="seria-3", year=2020,
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
ot.send_telegram = lambda t: wiad.append(t)
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
ot.send_telegram = lambda t: wiad.append(t)
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
ot.send_telegram = lambda t: None
try:
    for _szukaj in ot.OLX_SEARCHES:
        ot.fetch_listings_olx(_szukaj)
    ot.fetch_olx_car_price(ot.SEARCHES[0]["olx_query"])
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
sprawdz("z nadwoziem 'kompakt' zostaje odrzucony",
        ot.sprawdz_kryteria(dict(_bmw, body="kompakt"), _kryt)[0] is False)
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

print("\n== alarm o ślepej obserwacji wystawców ==")
# `ocen_zdrowie` liczy tylko wyszukiwania modeli i przez 24 dni ślepej
# obserwacji stał na zielono. Czas liczony w godzinach, nie w biegach.
from datetime import datetime as _dt, timedelta as _td, timezone as _tz  # noqa: E402

_ocen = getattr(ot, "ocen_obserwacje", None)
_wyslane = []
_stary_send, _stary_plik = ot.send_telegram, ot.STAN_FILE
try:
    ot.send_telegram = lambda t: _wyslane.append(t)
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
    ot.send_telegram = lambda t: _wyslane.append(t)
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
        _tempo(teraz=_t)
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

print("\n== poszerzenie kryteriów z 16.09.2026 ==")
K_A4 = ot.SEARCHES[1]["kryteria"]
_a4 = auto(model_key="a4-limousine", model_label="A4 Limousine", year=2017)
sprawdz("A4 Limousine przechodzi", ot.sprawdz_kryteria(_a4, K_A4)[0])
sprawdz("A4 Avant (kombi) przechodzi",
        ot.sprawdz_kryteria(dict(_a4, model_key="a4-avant", model_label="A4 Avant",
                                 body="estate-car"), K_A4)[0])
sprawdz("A4 allroad odpada (własny klucz modelu)",
        not ot.sprawdz_kryteria(dict(_a4, model_key="a4-allroad", model_label="A4 allroad",
                                     body="estate-car"), K_A4)[0])
sprawdz("Seria 4 z 2019 przechodzi",
        ot.sprawdz_kryteria(auto(model_key="seria-4", model_label="Seria 4", year=2019), K_SERIA4)[0])
sprawdz("Seria 4 z 2026 odpada",
        not ot.sprawdz_kryteria(auto(model_key="seria-4", model_label="Seria 4", year=2026), K_SERIA4)[0])

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
_podmienione = ("fetch_listings_otomoto", "fetch_olx_car_price", "uzupelnij_ze_strony", "fetch_listings_olx",
                "sprawdz_wystawce", "send_telegram", "SEEN_FILE", "SEEN_OLX_FILE", "SEEN_WYSTAWCY_FILE", "STAN_FILE")
_zapis_main = {n: getattr(ot, n) for n in _podmienione}
_seen_main = {}
try:
    ot.fetch_listings_otomoto = lambda s, pages=4: [dict(l) for l in _pula_audi] if "/audi/" in s["url"] else []
    ot.fetch_olx_car_price = lambda q: None
    ot.uzupelnij_ze_strony = lambda l: l
    ot.fetch_listings_olx = lambda s: []
    ot.sprawdz_wystawce = lambda *a, **k: 0
    ot.send_telegram = lambda t: _wys_main.append(t)
    for _n in ("SEEN_FILE", "SEEN_OLX_FILE", "SEEN_WYSTAWCY_FILE", "STAN_FILE"):
        setattr(ot, _n, _katalog / f"{_n}.json")
    # stan sprzed poprawki: A4 już raz "odhaczone" pustym wpisem, plus śmieć spoza puli
    ot.SEEN_FILE.write_text(_js.dumps({"502": {}, "999": {}}))
    ot.main()
    _seen_main = _js.loads(ot.SEEN_FILE.read_text())
finally:
    for _n, _v in _zapis_main.items():
        setattr(ot, _n, _v)
sprawdz("A4 ze wspólnej puli z A5 poszło, mimo starego pustego wpisu",
        any("a4-limousine 502" in m for m in _wys_main))
sprawdz("auto z dolnośląskiego nie poszło", not any(" 501" in m for m in _wys_main))
sprawdz("w zapisanym seen nie ma pustych wpisów, starych ani nowych",
        _seen_main and "999" not in _seen_main and all(v for v in _seen_main.values()))

print()
if bledy:
    print(f"NIEPOWODZENIE: {len(bledy)} testów nie przeszło")
    for b in bledy:
        print(f"   - {b}")
    sys.exit(1)
print("Wszystkie testy przeszły.")
