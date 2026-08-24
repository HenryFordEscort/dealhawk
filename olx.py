#!/usr/bin/env python3
"""Wspólny klient OLX — używany NIEZALEŻNIE przez bota rowerowego i samochodowego.

Po co osobny plik: bot rowerowy (tracker.py, Kleinanzeigen) i samochodowy
(otomoto_tracker.py, Otomoto) mają się nie mieszać — żaden nie importuje
drugiego. Ale obu potrzebny jest ten sam dostęp do OLX, a duplikowanie go
kończyło się cichymi awariami: wzorzec JSON łapał ~38% ofert i przez wiele
tygodni nikt tego nie widział, bo poprawka w jednym pliku nie trafiała do
drugiego. Tu jest JEDNO miejsce — naprawa działa od razu dla obu botów.

Ten moduł nie wie nic o rowerach ani autach i niczego nie wysyła na Telegram.
"""
import json
import os
import re

import requests

# Nagłówki jak z prawdziwej przeglądarki. Gołe "Mozilla/5.0" to klasyczny
# podpis bota i z serwerowni leci w blokadę.
OLX_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.8",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}

# Przekaźnik na Cloudflare Workers — obchodzi blokadę serwerowni GitHuba
# (OLX odpowiada 403 na całą domenę z tych adresów; sprawdzone 21.08.2026
# na sześciu wejściach, łącznie z sitemap.xml). Puste = pytamy wprost.
# .strip() jest KONIECZNY: przy wklejaniu wyniku `openssl rand -base64 24`
# łatwo zabrać ze sobą znak nowej linii albo spację. Porównanie klucza jest
# ścisłe co do długości, więc jeden niewidoczny znak = ciągłe 401.
OLX_RELAY_URL = os.environ.get("OLX_RELAY_URL", "").strip().rstrip("/")
OLX_RELAY_KEY = os.environ.get("OLX_RELAY_KEY", "").strip()

_diag = {"zapytan": 0, "ok": 0, "puste": 0, "bledy": 0, "statusy": {},
         "przekaznik_bledy": 0}


class OdpowiedzOLX:
    """Ujednolica odpowiedź z przekaźnika i z zapytania wprost, żeby reszta
    kodu nie musiała wiedzieć, którą drogą przyszła."""

    def __init__(self, status_code, text, headers=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}

    def json(self):
        return json.loads(self.text)


def olx_get(url: str, timeout: int = 20, allow_redirects: bool = True):
    """Jedyne wejście do OLX w całym projekcie. Zwraca odpowiedź albo None.
    Liczy statystyki, żeby dało się odróżnić „rynek pusty" od „zablokowali nas"."""
    _diag["zapytan"] += 1
    try:
        if OLX_RELAY_URL:
            # Klucz idzie NAGŁÓWKIEM, nie w adresie — adresy trafiają do logów
            # (Cloudflare, pośredniki), nagłówki nie.
            r = requests.get(OLX_RELAY_URL, params={"u": url},
                             headers={"x-klucz": OLX_RELAY_KEY},
                             timeout=timeout, allow_redirects=False)
            if r.status_code != 200:
                # błąd SAMEGO przekaźnika (zły klucz, limit, padł) — inna klasa
                # problemu niż blokada OLX-a, więc liczona osobno
                _diag["przekaznik_bledy"] += 1
                _diag["bledy"] += 1
                _diag["przekaznik_status"] = r.status_code
                return None
            # przekaźnik odpowiedział poprawnie — kasujemy ślad po ewentualnej
            # wcześniejszej odmowie, inaczej /status pokazywałby stary błąd
            # jeszcze długo po naprawie
            _diag.pop("przekaznik_status", None)
            odp = OdpowiedzOLX(int(r.headers.get("x-cel-status") or 0),
                               r.text, dict(r.headers))
        else:
            odp = requests.get(url, headers=OLX_HEADERS, timeout=timeout,
                               allow_redirects=allow_redirects)
    except Exception as e:
        # zapamiętaj RODZAJ awarii — bez tego alarm mówi tylko "None" i nic
        # z niego nie wynika (realny przypadek z 21.08: timeout wyglądał jak
        # odmowa przekaźnika)
        _diag["bledy"] += 1
        _diag["ostatni_wyjatek"] = type(e).__name__
        return None
    k = str(odp.status_code)
    _diag["statusy"][k] = _diag["statusy"].get(k, 0) + 1
    if odp.status_code == 200:
        _diag["ok"] += 1
    return odp


def olx_diag() -> dict:
    return dict(_diag)


def olx_diag_reset() -> None:
    _diag.clear()          # clear(), nie update() — inaczej 'przekaznik_status'
    _diag.update({"zapytan": 0, "ok": 0, "puste": 0, "bledy": 0, "statusy": {},
                  "przekaznik_bledy": 0})   # przeżywał każdy reset


def zglos_pusta_strone() -> None:
    """HTTP 200, ale zero ofert — podejrzenie blokady albo zmiany layoutu."""
    _diag["puste"] += 1


def przekaznik_zyje():
    """Czy sam przekaźnik odpowiada (niezależnie od OLX-a).
    True/False, albo None gdy przekaźnik w ogóle nie jest używany."""
    if not OLX_RELAY_URL:
        return None
    try:
        r = requests.get(OLX_RELAY_URL + "/zdrowie", timeout=15)
        return r.status_code == 200 and "ok" in r.text.lower()
    except Exception:
        return False


def _cena_z_tekstu(txt: str):
    """„14 200 zł" → 14200. PL używa spacji (też twardych) jako separatora
    tysięcy, więc najpierw sklejamy cyfry. None gdy to nie cena."""
    m = re.search(r'\d+', re.sub(r'[\s  .]', '', txt or ""))
    return int(m.group()) if m else None


def parse_olx_cards(html: str, cena_min: int = 300, cena_max: int = 80000) -> list:
    """Parsuje kafelki wyników OLX (data-cy="l-card") → lista ofert.
    Czysta funkcja — testowalna na zapisanym HTML.

    UWAGA HISTORYCZNA: wcześniej czytaliśmy oferty wzorcem JSON
    ("price":N,"url":"..."), który występuje tylko przy części renderów —
    łapaliśmy 20 z 52 kafelków (38% rynku), i to obciążone w stronę ofert
    promowanych, czyli droższych i sklepowych. To zatruwało KAŻDĄ wycenę.
    Kafelek jest w HTML zawsze, więc parsujemy jego.

    cena_min/cena_max: widełki rozsądku — inne dla rowerów, inne dla aut."""
    out = []
    for card in re.split(r'(?=data-cy="l-card")', html or "")[1:]:
        card = re.sub(r'<style[^>]*>.*?</style>', '', card, flags=re.S)  # CSS zaśmieca
        hm = re.search(r'href="(/d/oferta/[^"]+)"', card)
        pm = re.search(r'data-testid="ad-price"[^>]*>([^<]+)', card)
        if not hm or not pm:
            continue
        price = _cena_z_tekstu(pm.group(1))
        if price is None or not (cena_min < price < cena_max):
            continue                              # „Za darmo"/„Zamienię"/śmieć
        href = hm.group(1).split("?")[0]          # bez search_reason= (URL kanoniczny)
        rec = {"url": "https://www.olx.pl" + href, "price": price,
               "promoted": "promoted" in hm.group(1)}
        im = re.search(r'id="(\d+)"', card)
        if im:
            rec["id"] = im.group(1)               # stabilne ID oferty
        tm = re.search(r'<h4[^>]*>(.*?)</h4>', card, re.S)
        if tm:
            rec["title"] = re.sub(r'<[^>]+>', '', tm.group(1)).strip()
        lm = re.search(r'data-testid="location-date"[^>]*>([^<]+)', card)
        if lm:
            # Kafelek niesie "Miasto, Dzielnica - Odświeżono dnia 20 sierpnia 2026".
            # Do 24.08.2026 brano stąd SAMĄ miejscowość, a datę wyrzucano — i przez
            # to jedyne, co bot wiedział o wieku oferty, to od kiedy sam ją widzi.
            # Miejscowość rozdziela przecinek, datę " - ", więc dzielimy raz.
            miejsce, _, data = lm.group(1).partition(" - ")
            rec["loc"] = miejsce.strip()
            if data.strip():
                rec["data_kafelka"] = data.strip()
        out.append(rec)
    # Czujka na ciszę (reguła 7): daty nie ma w ŻADNYM kafelku = OLX przebudował
    # kafelek, a nie "akurat dziś nikt nic nie odświeżył".
    if out and not any("data_kafelka" in r for r in out):
        _diag["kafelki_bez_daty"] = _diag.get("kafelki_bez_daty", 0) + 1
    return out


# Strona oferty OLX niesie CAŁE ogłoszenie w jednym JSON-ie — data wystawienia,
# data wygaśnięcia, konto sprzedawcy, czy to firma, miasto, zdjęcia. Regexpy po
# HTML-u dublowały ułamek tego i myliły się na boilerplate (fraza "NIEAKTUALNE"
# siedzi w pakiecie tłumaczeń KAŻDEJ strony, co zatruło nam 394 fałszywe
# "sprzedaże"). Zmierzone 24.08.2026 na żywej i martwej ofercie.
_PRERENDER = re.compile(r'__PRERENDERED_STATE__\s*=\s*("(?:[^"\\]|\\.)*")')


def parse_olx_ad_json(html: str):
    """Ogłoszenie OLX jako słownik — albo None, gdy go na stronie nie ma.

    None znaczy DOKŁADNIE "nie wiem", nie "oferta martwa": martwa strona ma
    w tym miejscu samo {"statusCode": 410} bez żadnego powodu zdjęcia, i tak
    samo wygląda strona, której nie udało się wyrenderować. Powód śmierci
    trzeba zapisać ZA ŻYCIA oferty — po fakcie nie ma go już skąd wziąć."""
    m = _PRERENDER.search(html or "")
    if not m:
        return None
    try:
        state = json.loads(json.loads(m.group(1)))       # JSON w stringu JSON-a
    except Exception:
        return None
    ad = (state.get("ad") or {}).get("ad")
    return ad if isinstance(ad, dict) else None
