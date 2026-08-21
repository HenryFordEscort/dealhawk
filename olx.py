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
OLX_RELAY_URL = os.environ.get("OLX_RELAY_URL", "").rstrip("/")
OLX_RELAY_KEY = os.environ.get("OLX_RELAY_KEY", "")

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
            odp = OdpowiedzOLX(int(r.headers.get("x-cel-status") or 0),
                               r.text, dict(r.headers))
        else:
            odp = requests.get(url, headers=OLX_HEADERS, timeout=timeout,
                               allow_redirects=allow_redirects)
    except Exception:
        _diag["bledy"] += 1
        return None
    k = str(odp.status_code)
    _diag["statusy"][k] = _diag["statusy"].get(k, 0) + 1
    if odp.status_code == 200:
        _diag["ok"] += 1
    return odp


def olx_diag() -> dict:
    return dict(_diag)


def olx_diag_reset() -> None:
    _diag.update({"zapytan": 0, "ok": 0, "puste": 0, "bledy": 0, "statusy": {},
                  "przekaznik_bledy": 0})


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
            rec["loc"] = lm.group(1).split(" - ")[0].strip()
        out.append(rec)
    return out
