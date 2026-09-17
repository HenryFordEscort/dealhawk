import re
import os
import html
import json
import logging
import statistics
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import requests
import cloudscraper

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("otomoto.log"),
    ],
)
log = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

SEEN_FILE = Path("seen_otomoto.json")
SEEN_OLX_FILE = Path("seen_olx.json")
scraper = cloudscraper.create_scraper()

# ---------------------------------------------------------------------------
# Wyszukiwania
#
# Filtry w URL/API są WYŁĄCZNIE zawężeniem ruchu, nie gwarancją. Sprawdzone
# 21.08.2026 na żywych danych:
#   * Otomoto na /osobowe/audi/a5 dokłada „podobne oferty" — wracały A6
#     Limousine, Q5 i A4 Avant, mimo modelu w ścieżce adresu.
#   * OLX ignoruje filter_enum_condition i filter_enum_petrol — w odpowiedzi
#     na zapytanie o uszkodzone diesle było 102 „Nieuszkodzony" i 36 „Benzyna".
# Dlatego każde twarde kryterium jest sprawdzane w kodzie, na polach
# strukturalnych ogłoszenia (patrz `sprawdz_kryteria`). Dopasowanie po słowach
# w tytule zostało usunięte: „320" łapało się na „320KM" (moc silnika!), przez
# co przychodziły BMW Serii 5 i 8, a „gran coupe" wpuszczało Serię 2.
#
# Filtry URL (kodowane):
#   filter_enum_fuel_type=diesel
#   filter_enum_gearbox=automatic
#   filter_enum_drive=awd             (quattro / 4x4)
#   filter_float_year:from / :to
#   filter_float_engine_capacity:from / :to  (w cm3)
# ---------------------------------------------------------------------------

# Górny limit przebiegu — wspólny dla wszystkich wyszukiwań.
PRZEBIEG_MAX = 200_000

# Klucze modeli. UWAGA: OLX używa dla BMW Serii 3 slugu węgierskiego
# ("3-as-sorozat"), Otomoto polskiego ("seria-3"). Ten sam samochód, dwa zapisy,
# więc w zbiorze muszą być oba. Etykieta ("Seria 3") jest sprawdzana dodatkowo.
MODELE_A5_SPORTBACK = {"a5-sportback"}
MODELE_A4 = {"a4-limousine"}
MODELE_SERIA_3 = {"3-as-sorozat", "seria-3"}
MODELE_SERIA_4 = {"seria-4"}

# KRYTERIA Z 22.08 ZOSTAJĄ, decyzja właściciela z 16.09.2026. Tego dnia, po jego
# "dalej nie dostałem żadnej oferty na rozbitka", zmierzone na pełnych pulach
# OLX (wszystkie uszkodzone diesle tych modeli w Polsce, przepuszczone przez
# `sprawdz_kryteria`): pasowało 5 aut w Polsce i 0 w wybranych województwach,
# wszystkie 5 stały w wielkopolskim. Bot nie przegapił żadnego.
# Poluzowania z osobna, liczone w wybranych województwach:
#   przebieg do 300 tys. km            0
#   sąsiednie województwa              0
#   roczniki o 2 lata szerzej          3
#   kombi (A4 Avant, Seria 3 Touring)  3
#   napęd na jedną oś                  2
# Sesja Claude'a wdrożyła wtedy bez zgody kombi i szersze roczniki, bo właściciel
# nie zaznaczył żadnej opcji. Na Telegram poszły trzy A4 Avant i Touring.
# Właściciel: "mówiłem, że mnie kombi nie interesuje", a o rocznikach: "wracać
# do starych". Brak odpowiedzi to nie zgoda. Niczego tu nie poszerzać bez
# wyraźnego polecenia.
#
# Otomoto na stronie ogłoszenia pisze "Sedan" albo "Limuzyna" (oba mapowane na
# "sedan"), OLX w polu car_body "sedan".
NADWOZIE_SEDAN = {"sedan"}

SEARCHES = [
    {
        "name": "Audi A5 Sportback 2.0 TDI quattro AT 2015-2019",
        "url": (
            "https://www.otomoto.pl/osobowe/audi/a5"
            "?search%5Bfilter_enum_fuel_type%5D=diesel"
            "&search%5Bfilter_enum_gearbox%5D=automatic"
            "&search%5Bfilter_enum_drive%5D=awd"
            "&search%5Bfilter_float_year%3Afrom%5D=2015"
            "&search%5Bfilter_float_year%3Ato%5D=2019"
            "&search%5Bfilter_float_engine_capacity%3Afrom%5D=1900"
            "&search%5Bfilter_float_engine_capacity%3Ato%5D=2100"
            "&search%5Bfilter_enum_damaged%5D=1"
        ),
        "kryteria": {
            "modele": MODELE_A5_SPORTBACK,
            "rok": (2015, 2019),
            "paliwo": "diesel",
            "skrzynia": "automatic",
            "naped": "awd",
            "pojemnosc": (1900, 2100),
            "uszkodzony": True,
        },
    },
    {
        "name": "Audi A4 Limousine 2.0 TDI quattro AT 2015-2019",
        "url": (
            "https://www.otomoto.pl/osobowe/audi/a4"
            "?search%5Bfilter_enum_fuel_type%5D=diesel"
            "&search%5Bfilter_enum_gearbox%5D=automatic"
            "&search%5Bfilter_enum_drive%5D=awd"
            "&search%5Bfilter_float_year%3Afrom%5D=2015"
            "&search%5Bfilter_float_year%3Ato%5D=2019"
            "&search%5Bfilter_float_engine_capacity%3Afrom%5D=1900"
            "&search%5Bfilter_float_engine_capacity%3Ato%5D=2100"
            "&search%5Bfilter_enum_damaged%5D=1"
        ),
        "kryteria": {
            "modele": MODELE_A4,
            # Avant i allroad mają własne klucze modelu i odpadają już na
            # modelu; nadwozie to druga zapora, gdy klucz jest błędny
            "nadwozie": NADWOZIE_SEDAN,
            "rok": (2015, 2019),
            "paliwo": "diesel",
            "skrzynia": "automatic",
            "naped": "awd",
            "pojemnosc": (1900, 2100),
            "uszkodzony": True,
        },
    },
    {
        "name": "BMW Seria 3 Sedan 2.0d xDrive AT 2019-2021",
        "url": (
            "https://www.otomoto.pl/osobowe/bmw/seria-3"
            "?search%5Bfilter_enum_fuel_type%5D=diesel"
            "&search%5Bfilter_enum_gearbox%5D=automatic"
            "&search%5Bfilter_enum_drive%5D=awd"
            "&search%5Bfilter_float_year%3Afrom%5D=2019"
            "&search%5Bfilter_float_year%3Ato%5D=2021"
            "&search%5Bfilter_float_engine_capacity%3Afrom%5D=1900"
            "&search%5Bfilter_float_engine_capacity%3Ato%5D=2100"
            "&search%5Bfilter_enum_damaged%5D=1"
        ),
        "kryteria": {
            "modele": MODELE_SERIA_3,
            # tylko sedan: Touring i 3GT mają ten sam klucz modelu co sedan
            # i odpadają dopiero tutaj
            "nadwozie": NADWOZIE_SEDAN,
            "rok": (2019, 2021),
            "paliwo": "diesel",
            "skrzynia": "automatic",
            "naped": "awd",
            "pojemnosc": (1900, 2100),
            "uszkodzony": True,
        },
    },
    {
        "name": "BMW Seria 4 Gran Coupe 2.0d xDrive AT 2021-2023",
        "url": (
            "https://www.otomoto.pl/osobowe/bmw/seria-4"
            "?search%5Bfilter_enum_fuel_type%5D=diesel"
            "&search%5Bfilter_enum_gearbox%5D=automatic"
            "&search%5Bfilter_enum_drive%5D=awd"
            "&search%5Bfilter_float_year%3Afrom%5D=2021"
            "&search%5Bfilter_float_year%3Ato%5D=2023"
            "&search%5Bfilter_float_engine_capacity%3Afrom%5D=1900"
            "&search%5Bfilter_float_engine_capacity%3Ato%5D=2100"
            "&search%5Bfilter_enum_damaged%5D=1"
            "&search%5Bfilter_enum_bodywork_type%5D=coupe"
        ),
        "kryteria": {
            "modele": MODELE_SERIA_4,
            # BEZ filtra nadwozia: Gran Coupé bywa wystawiane jako coupe, sedan
            # ORAZ hatchback (29/10/5 w próbce), nie da się z tego zrobić sita
            "rok": (2021, 2023),
            "paliwo": "diesel",
            "skrzynia": "automatic",
            "naped": "awd",
            "pojemnosc": (1900, 2100),
            "uszkodzony": True,
        },
    },
]

# ---------------------------------------------------------------------------
# OLX: wyszukiwania po FILTRACH STRUKTURALNYCH, nie po tekście (od 16.09.2026)
#
# Zmierzone tego dnia: bez parametru `query` OLX honoruje co do sztuki model
# (także kilka naraz), stan, paliwo, skrzynię i rocznik. Stara notatka "OLX
# ignoruje filter_enum_condition" dotyczyła zapytań z tekstem, w których
# wyszukiwarka układa wyniki po trafności. Tekst "audi a4 sedan uszkodzony"
# nie znalazłby Avanta, a pierwsze 50 wyników po trafności to nie cały rynek.
#
# Na serwerze filtrujemy tylko pola, które OLX ma zawsze: na 392 uszkodzonych
# dieslach tych modeli rocznik, skrzynia, przebieg, nadwozie i województwo były
# w 100%, a napęd tylko w 74%. Napędu więc NIE ma w zapytaniu, bo brak danych
# to nie niezgodność i rozstrzyga `sprawdz_kryteria`.
#
# Klucze modeli na OLX (zmierzone 16.09.2026): Seria 3 to "3-as-sorozat"
# ("seria-3" daje 0 wyników), Seria 4 to "seria-4", A4 to "a4-limousine"
# i "a4-avant", A5 Sportback to "a5-sportback".
# ---------------------------------------------------------------------------
OLX_API = "https://www.olx.pl/api/v1/offers/"
OLX_KATEGORIA_AUDI = 182
OLX_KATEGORIA_BMW = 183


def _olx_params(kategoria: int, kryteria: dict) -> dict:
    params = {"category_id": kategoria, "limit": 50, "currency": "PLN",
              "filter_enum_condition[0]": "damaged",
              "filter_enum_petrol[0]": kryteria["paliwo"],
              "filter_enum_transmission[0]": kryteria["skrzynia"],
              "filter_float_year:from": kryteria["rok"][0],
              "filter_float_year:to": kryteria["rok"][1]}
    for i, model in enumerate(sorted(kryteria["modele"])):
        params[f"filter_enum_model[{i}]"] = model
    return params


OLX_SEARCHES = [
    {"name": "OLX " + SEARCHES[0]["name"], "kryteria": SEARCHES[0]["kryteria"],
     "params": _olx_params(OLX_KATEGORIA_AUDI, SEARCHES[0]["kryteria"])},
    {"name": "OLX " + SEARCHES[1]["name"], "kryteria": SEARCHES[1]["kryteria"],
     "params": _olx_params(OLX_KATEGORIA_AUDI, SEARCHES[1]["kryteria"])},
    {"name": "OLX " + SEARCHES[2]["name"], "kryteria": SEARCHES[2]["kryteria"],
     "params": _olx_params(OLX_KATEGORIA_BMW, SEARCHES[2]["kryteria"])},
    {"name": "OLX " + SEARCHES[3]["name"], "kryteria": SEARCHES[3]["kryteria"],
     "params": _olx_params(OLX_KATEGORIA_BMW, SEARCHES[3]["kryteria"])},
]

# Tylko te województwa
REGIONS_ALLOWED = {"małopolskie", "podkarpackie", "świętokrzyskie", "śląskie"}

# Słowa sugerujące uszkodzenie / wypadek
DAMAGE_KEYWORDS = [
    "uszkodzon", "po wypadku", "wypadek", "kolizja",
    "do naprawy", "na części", "niesprawny", "powódź",
    "skradzion", "bez silnika", "silnik uszkodz", "rozbity",
    "uszkodzony", "powypadkowy", "pokolizyjny", "do remontu",
]

def days_on_market(created_at: str) -> Optional[int]:
    """Ile dni temu dodano ogłoszenie."""
    if not created_at:
        return None
    try:
        dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).days
    except Exception:
        return None


def in_allowed_region(region: str) -> bool:
    if not region:
        return True  # brak danych = przepuść
    # Dokładna nazwa, nie "zawiera się". Do 16.09.2026 było `allowed in r`,
    # a "śląskie" siedzi w "dolnośląskie", więc bot wpuszczał całe
    # dolnośląskie. Tak samo "opolskie" siedzi w "wielkopolskie".
    return region.strip().lower() in REGIONS_ALLOWED


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_seen() -> dict:
    if SEEN_FILE.exists():
        data = json.loads(SEEN_FILE.read_text())
        if isinstance(data, list):
            return {ad_id: {} for ad_id in data}
        return data
    return {}


def save_seen(seen: dict):
    # Pusty wpis {} niczego nie mówi i od 16.09.2026 niczego nie blokuje (patrz
    # main), więc nie ma po co go trzymać. Tak znikają też wpisy sprzed poprawki.
    SEEN_FILE.write_text(json.dumps({k: v for k, v in seen.items() if v},
                                    ensure_ascii=False, indent=2))


def load_seen_olx() -> dict:
    if SEEN_OLX_FILE.exists():
        return json.loads(SEEN_OLX_FILE.read_text())
    return {}


def save_seen_olx(seen: dict):
    # pusty {} niczego nie mówi, tak samo jak w `save_seen`
    SEEN_OLX_FILE.write_text(json.dumps({k: v for k, v in seen.items() if v},
                                        ensure_ascii=False, indent=2))


# Stan jednego biegu, zbierany przez funkcje pobierające. Zerowany na starcie `main`.
_bieg = {"olx_ok": 0, "olx_bledy": 0, "problemy": []}


def _bieg_reset():
    _bieg.update({"olx_ok": 0, "olx_bledy": 0, "problemy": []})


def zglos_problem(tekst: str):
    """Kłopot, który nie jest awarią, ale właściciel ma go zobaczyć w podsumowaniu
    dnia, a nie tylko w logu, do którego nie zagląda."""
    log.warning(tekst)
    if tekst not in _bieg["problemy"]:
        _bieg["problemy"].append(tekst)


# Ile razy ponawiać jedną wiadomość: doba prób co pół godziny. Wiadomość, której
# Telegram nie przyjmie nigdy, nie może na zawsze zapychać kolejki.
DOSYLKA_PROB_MAX = 48


def dosylka(seen: dict, zapisz) -> int:
    """Ponawia wiadomości, których Telegram nie przyjął (pole `do_wyslania`).

    Właściciel 16.09.2026: "chcę pewność, że gdy pojawi się nowa oferta, to mnie
    powiadomisz". Wpis czeka więc z treścią wiadomości, aż Telegram ją przyjmie.
    Wymiana świadoma: bieg ubity dokładnie między wysyłką a zapisem da duplikat,
    ale duplikat jest tańszy niż zgubione auto. Po DOSYLKA_PROB_MAX próbach
    wiadomość trafia do problemów dnia razem z linkiem. Zwraca liczbę dosłanych."""
    dosylane = 0
    for lid, wpis in seen.items():
        if not (isinstance(wpis, dict) and wpis.get("do_wyslania")):
            continue
        przyciski = przyciski_oferty(lid) if wpis.get("przyciski") else None
        if send_telegram(wpis["do_wyslania"], zdjecie=wpis.get("zdjecie"), przyciski=przyciski):
            del wpis["do_wyslania"]
            wpis.pop("proby_wysylki", None)
            dosylane += 1
        else:
            wpis["proby_wysylki"] = wpis.get("proby_wysylki", 0) + 1
            if wpis["proby_wysylki"] >= DOSYLKA_PROB_MAX:
                del wpis["do_wyslania"]
                zglos_problem(f"Telegram przez dobę nie przyjął wiadomości o: {wpis.get('url') or lid}")
            else:
                zglos_problem("Telegram nie przyjmował wiadomości, bot ponawia je co pół godziny")
        zapisz(seen)
    return dosylane


def send_telegram(text: str, zdjecie: Optional[str] = None, przyciski: Optional[list] = None) -> bool:
    """True, gdy Telegram przyjął wiadomość. Wynik trzeba sprawdzać: do 16.09.2026
    odmowa kończyła się jedną linijką w logu, a ogłoszenie było już odhaczone,
    więc auto przepadało po cichu. Patrz `dosylka`.

    Ze `zdjecie` (od 17.09.2026) oferta idzie jako zdjęcie z podpisem, bo zakres
    szkody widać na zdjęciu, a nie w tytule. Gdy Telegram zdjęcia nie przyjmie
    albo podpis przekracza jego limit 1024 znaków, idzie sam tekst: brak zdjęcia
    to drobiazg, brak wiadomości już nie."""
    api = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    klawiatura = {"reply_markup": {"inline_keyboard": przyciski}} if przyciski else {}
    if zdjecie and len(text) <= 1024:
        try:
            r = requests.post(f"{api}/sendPhoto", json={
                "chat_id": TELEGRAM_CHAT_ID, "photo": zdjecie, "caption": text,
                "parse_mode": "HTML", **klawiatura}, timeout=20)
            r.raise_for_status()
            return True
        except Exception as e:
            log.warning(f"Telegram nie przyjął zdjęcia, wysyłam sam tekst: {e}")
    try:
        r = requests.post(f"{api}/sendMessage", json={
            "chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML",
            "disable_web_page_preview": False, **klawiatura}, timeout=10)
        r.raise_for_status()
        return True
    except Exception as e:
        log.error(f"Telegram error: {e}")
        return False


def wyslij_oferte(seen: dict, lid: str, wpis: dict, tekst: str, zdjecie: Optional[str], zapisz) -> bool:
    """ZAPIS PRZED WYSYŁKĄ, razem z treścią wiadomości. Treść schodzi z wpisu
    dopiero, gdy Telegram ją przyjmie, więc ani ubity bieg, ani odmowa Telegrama
    nie gubią auta: `dosylka` ponowi je w następnym biegu."""
    seen[lid] = {**wpis, "do_wyslania": tekst, "zdjecie": zdjecie, "przyciski": True}
    zapisz(seen)
    if not send_telegram(tekst, zdjecie=zdjecie, przyciski=przyciski_oferty(lid)):
        return False
    del seen[lid]["do_wyslania"]
    zapisz(seen)
    return True


# Przyciski pod ofertą (od 17.09.2026). Właściciel: po kilku tygodniach ma być
# czarno na białym widać, co odrzuca, zamiast zgadywać. Kliknięcie to FAKT,
# zapisany z kompletem danych o aucie. Nic z tego nie zmienia jeszcze kryteriów:
# najpierw fakty, potem wnioski, ten sam podział co w bocie rowerowym.
POWODY_ODRZUTU = {"ni": "nie interesuje", "sz": "za duża szkoda", "dr": "za drogo"}
ODRZUTY_FILE = Path("odrzuty_auta.jsonl")


def przyciski_oferty(lid: str) -> list:
    return [[{"text": "Nie interesuje", "callback_data": f"odrz|ni|{lid}"}],
            [{"text": "Za duża szkoda", "callback_data": f"odrz|sz|{lid}"},
             {"text": "Za drogo", "callback_data": f"odrz|dr|{lid}"}]]


def czytaj_przyciski(oferty: dict) -> int:
    """Zbiera kliknięcia przycisków i dopisuje je do ODRZUTY_FILE (append-only).

    Kolejkę `getUpdates` tego bota czyta tylko ten proces, raz na bieg, czyli
    z opóźnieniem do pół godziny. Dlatego po zapisie przyciski pod wiadomością
    zamieniają się w "zapisane": widać, że kliknięcie doszło, nawet gdy odpowiedź
    na samo kliknięcie jest już dla Telegrama za stara. `oferty` to wpisy z plików
    seen, z których bierzemy dane auta. Zwraca liczbę zapisanych kliknięć."""
    api = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    offset = _stan().get("telegram_offset", 0)
    try:
        r = requests.get(f"{api}/getUpdates", params={
            "offset": offset, "timeout": 0,
            "allowed_updates": json.dumps(["callback_query"])}, timeout=20)
        r.raise_for_status()
        zdarzenia = r.json().get("result", [])
    except Exception as e:
        log.error(f"getUpdates: {e}")
        zglos_problem("nie udało się odczytać kliknięć przycisków pod ofertami")
        return 0
    zapisane = 0
    for zdarzenie in zdarzenia:
        offset = max(offset, zdarzenie.get("update_id", 0) + 1)
        klik = zdarzenie.get("callback_query") or {}
        wiadomosc = klik.get("message") or {}
        if str((wiadomosc.get("chat") or {}).get("id")) != str(TELEGRAM_CHAT_ID):
            continue
        czesci = str(klik.get("data") or "").split("|")
        if len(czesci) != 3 or czesci[0] != "odrz" or czesci[1] not in POWODY_ODRZUTU:
            continue
        powod, lid = POWODY_ODRZUTU[czesci[1]], czesci[2]
        auto = oferty.get(lid) or {}
        rekord = {"kiedy": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  "powod": powod, "id": lid}
        rekord.update({k: auto.get(k) for k in ("title", "price_num", "year", "mileage_num", "url",
                                               "search", "date", "sprawne_mediana", "sprawne_n")})
        with ODRZUTY_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rekord, ensure_ascii=False) + "\n")
        zapisane += 1
        log.info(f"Kliknięcie '{powod}': {rekord.get('title')}")
        for metoda, dane in (
                ("editMessageReplyMarkup", {
                    "chat_id": TELEGRAM_CHAT_ID, "message_id": wiadomosc.get("message_id"),
                    "reply_markup": {"inline_keyboard": [[{"text": f"✓ zapisane: {powod}",
                                                           "callback_data": "zapisane"}]]}}),
                ("answerCallbackQuery", {"callback_query_id": klik.get("id"),
                                         "text": f"Zapisane: {powod}"})):
            try:
                requests.post(f"{api}/{metoda}", json=dane, timeout=10)
            except Exception:
                pass      # spóźniona odpowiedź na kliknięcie to nie awaria, zapis już jest
    if zdarzenia:
        _stan({"telegram_offset": offset})
    return zapisane


# Zaprzeczenia, które trzeba wyciąć PRZED szukaniem słów o uszkodzeniu.
# „nieuszkodzony" zawiera w sobie „uszkodzon", więc gołe `in` czyta zdanie
# „auto nieuszkodzone, bezwypadkowe" jako trafienie i wysyła czyste auto.
ZAPRZECZENIA_RE = re.compile(
    r"\b(?:nie\s?(?:jest\s+|był\s+|byl\s+|po\s+|z\s+)?|bez\s+|brak\s+(?:\w+\s+)?)"
    r"(?:uszkodz\w*|wypad\w*|kolizj\w*|pokolizyjn\w*)",
    re.IGNORECASE,
)


def is_damaged(title: str, description: str = "") -> bool:
    """Czy tekst mówi o uszkodzeniu. Używane tam, gdzie serwis nie podaje
    pola `condition` (Otomoto). Na OLX pierwszeństwo ma pole strukturalne."""
    combined = (title + " " + description).lower()
    combined = ZAPRZECZENIA_RE.sub(" ", combined)
    return any(kw in combined for kw in DAMAGE_KEYWORDS)


# ---------------------------------------------------------------------------
# Kryteria twarde — jedno sito dla Otomoto i OLX
# ---------------------------------------------------------------------------

# Napęd: OLX rozróżnia stały i dołączany 4x4, nas interesuje samo „na cztery koła"
NAPED_MAPA = {
    "all-wheel-permanent": "awd", "all-wheel-auto": "awd", "4x4": "awd",
    "awd": "awd", "rear-wheel": "rwd", "front-wheel": "fwd",
}

# Nazwy pól do komunikatu o brakach — użytkownik ma widzieć, czego bot NIE wie
ETYKIETY = {
    "modele": "model", "nadwozie": "nadwozie", "rok": "rocznik",
    "paliwo": "paliwo", "skrzynia": "skrzynia", "naped": "napęd",
    "pojemnosc": "pojemność", "uszkodzony": "stan", "przebieg": "przebieg",
}


def sprawdz_kryteria(ad: dict, kryteria: dict) -> tuple[bool, list[str]]:
    """Sprawdza ogłoszenie względem kryteriów. Zwraca (pasuje, braki_danych).

    Zasada, ustalona z użytkownikiem: **brak danych to nie niezgodność**.
    Ogłoszenie bez wypełnionego pola przechodzi, ale nazwa pola ląduje w
    `braki` i trafia do wiadomości — decyzję podejmuje człowiek. Twarde NIE
    pada tylko wtedy, gdy serwis podał wartość i ta wartość się nie zgadza.
    """
    braki = []

    def podane(v):
        return v is not None and v != ""

    modele = kryteria.get("modele")
    if modele:
        klucz = (ad.get("model_key") or "").lower()
        etykieta = re.sub(r"\s+", "", (ad.get("model_label") or "").lower())
        if not klucz and not etykieta:
            braki.append(ETYKIETY["modele"])
        elif klucz not in modele and not any(
            re.sub(r"[\s-]+", "", m) == etykieta for m in modele
        ):
            return False, braki

    nadwozie = kryteria.get("nadwozie")
    if nadwozie:
        if not podane(ad.get("body")):
            braki.append(ETYKIETY["nadwozie"])
        elif ad["body"].lower() not in nadwozie:
            return False, braki

    rok = kryteria.get("rok")
    if rok:
        if not podane(ad.get("year")):
            braki.append(ETYKIETY["rok"])
        elif not (rok[0] <= ad["year"] <= rok[1]):
            return False, braki

    for pole, klucz_ad in (("paliwo", "fuel"), ("skrzynia", "gearbox"), ("naped", "drive")):
        oczekiwane = kryteria.get(pole)
        if not oczekiwane:
            continue
        if not podane(ad.get(klucz_ad)):
            braki.append(ETYKIETY[pole])
        elif ad[klucz_ad] != oczekiwane:
            return False, braki

    poj = kryteria.get("pojemnosc")
    if poj:
        if not podane(ad.get("engine_cm3")):
            braki.append(ETYKIETY["pojemnosc"])
        elif not (poj[0] <= ad["engine_cm3"] <= poj[1]):
            return False, braki

    if kryteria.get("uszkodzony"):
        # damaged=False to informacja („Nieuszkodzony"), a nie brak danych
        if ad.get("damaged") is None:
            braki.append(ETYKIETY["uszkodzony"])
        elif not ad["damaged"]:
            return False, braki

    # "przebieg_max" w kryteriach nadpisuje limit; wycena sprawnych aut podaje None,
    # bo porównanie auta z 199 tys. km nie może się urywać na 200 tys.
    limit_km = kryteria.get("przebieg_max", PRZEBIEG_MAX)
    if limit_km:
        if not podane(ad.get("mileage_num")):
            braki.append(ETYKIETY["przebieg"])
        elif ad["mileage_num"] > limit_km:
            return False, braki

    return True, braki


# Wycena "tego samego auta sprawnego" (od 17.09.2026). Zastępuje trzy liczby,
# które wprowadzały w błąd na wiadomościach z 16.09.2026:
#  * "OLX mediana" z wyszukiwarki tekstowej, czyli przypadkowe ogłoszenia Audi,
#    w większości nieuszkodzone i z różnych roczników. Przy A4 Avant za 6 500 zł
#    pokazała 47 500 zł i "różnicę +41 000 zł";
#  * procent wobec mediany puli wyszukiwania, a pula Otomoto dla Audi to wszystkie
#    uszkodzone Audi (model w adresie jest ignorowany), także A6 i Q3;
#  * "szacunek naprawy" zgadujący kwotę po słowach z tytułu ("przód" = 15 000 zł)
#    oraz punkty i gwiazdki liczone z powyższych.
# Zmierzone 17.09.2026 na 5 pasujących rozbitkach: przy roczniku +-1, przebiegu
# +-30 tys. km i znanym napędzie 4x4 OLX ma zwykle 11-16 sprawnych odpowiedników
# (A5 Sportback 2017, 165 tys. km: mediana 88 350 zł z 14). A4 Limousine 2017
# ma przy +-30 tys. tylko 1, przy +-60 tys. już 5. A5 z 51 tys. km nie ma żadnego,
# więc wiadomość mówi wtedy "nie wiem" zamiast liczby z przypadku (reguła 6).
POROWNYWALNE_MIN = 5
OKNA_PRZEBIEGU = (30_000, 60_000)


def _zl(kwota: int) -> str:
    return f"{kwota:,} zł".replace(",", " ")


def wycena_sprawnego(olx_search: dict, listing: dict) -> dict:
    """Ceny wystawionych SPRAWNYCH aut z OLX: ten sam model, paliwo, skrzynia,
    pojemność i nadwozie, rocznik +-1, napęd ZNANY i zgodny (auto z nieznanym
    napędem to często tańsza wersja na przód), przebieg w oknie OKNA_PRZEBIEGU.
    Zwraca {"n", "rok", "km", "okno"}, a przy co najmniej POROWNYWALNE_MIN
    ogłoszeniach także mediana, p25 i p75. Ceny wystawione, nie transakcyjne."""
    rok = listing.get("year")
    if not rok:
        return {"n": 0, "powod": "ogłoszenie nie podaje rocznika"}
    kryt = {k: v for k, v in olx_search["kryteria"].items() if k != "uszkodzony"}
    kryt.update({"rok": (rok - 1, rok + 1), "przebieg_max": None})
    params = _olx_params(olx_search["params"]["category_id"], kryt)
    params["filter_enum_condition[0]"] = "notdamaged"
    oferty = fetch_listings_olx({"name": f"sprawne: {listing.get('title', '')[:40]}",
                                 "params": params, "kryteria": kryt})
    naped = kryt.get("naped")
    sprawne = [o for o in oferty if o.get("price_num") and o.get("damaged") is False
               and (not naped or o.get("drive") == naped)]
    km, okno, pula = listing.get("mileage_num"), None, sprawne
    if km is not None:
        for okno in OKNA_PRZEBIEGU:
            pula = [o for o in sprawne
                    if o.get("mileage_num") is not None and abs(o["mileage_num"] - km) <= okno]
            if len(pula) >= POROWNYWALNE_MIN:
                break
    ceny = sorted(o["price_num"] for o in pula)
    wynik = {"n": len(ceny), "rok": (rok - 1, rok + 1), "km": km, "okno": okno}
    if len(ceny) >= POROWNYWALNE_MIN:
        cwiartki = statistics.quantiles(ceny, n=4)
        wynik.update(mediana=int(statistics.median(ceny)), p25=int(cwiartki[0]), p75=int(cwiartki[2]))
    return wynik


def tekst_wyceny(listing: dict, w: dict) -> str:
    """Linie o opłacalności. Liczba tylko wtedy, gdy stoi na co najmniej
    POROWNYWALNE_MIN ogłoszeniach; inaczej wprost "nie wiem"."""
    if w.get("mediana") is None:
        n = w.get("n", 0)
        powod = w.get("powod") or (
            "na OLX nie ma porównywalnych sprawnych aut" if n == 0 else
            "na OLX jest tylko 1 porównywalne sprawne auto" if n == 1 else
            f"na OLX są tylko {n} porównywalne sprawne auta")
        return f"\n💶 Cena takiego auta sprawnego: nie wiem, {powod}"
    if w.get("okno") is not None:
        zakres_km = f", przebieg {max(0, w['km'] - w['okno']) // 1000}-{(w['km'] + w['okno']) // 1000} tys. km"
    else:
        zakres_km = ", dowolny przebieg, bo to ogłoszenie go nie podaje"
    linie = (f"\n💶 Sprawne takie auto na OLX: mediana {_zl(w['mediana'])}, połowa ofert od "
             f"{_zl(w['p25'])} do {_zl(w['p75'])} (z {w['n']} ogłoszeń: rocznik "
             f"{w['rok'][0]}-{w['rok'][1]}{zakres_km})")
    cena = listing.get("price_num")
    if cena:
        procent = round(100 * cena / w["mediana"])
        if w["mediana"] > cena:
            linie += (f"\n➡️ Ten rozbitek to {procent}% tej ceny: {_zl(w['mediana'] - cena)} "
                      f"na naprawę i zysk (ceny wystawione, nie transakcyjne)")
        else:
            linie += f"\n➡️ Ten rozbitek kosztuje tyle co sprawne auto albo więcej ({procent}%)"
    return linie


def tekst_oferty(zrodlo: str, listing: dict, nazwa_wyszukiwania: str, wycena: dict,
                 obnizka: str = "") -> str:
    """Jedna wiadomość o ofercie dla Otomoto i OLX. Treść z ogłoszenia przechodzi
    przez html.escape: znak "<" albo "&" w tytule psuł tryb HTML Telegrama,
    a odrzucona wiadomość wisiałaby w ponawianiu."""
    esc = html.escape
    rok = f"📅 {listing['year']}" if listing.get("year") else "📅 ?"
    przebieg = (f"🛣 {listing['mileage_num']:,} km".replace(",", " ")
                if listing.get("mileage_num") is not None else "🛣 brak przebiegu")
    moc = f"  ⚡ {listing['engine_hp']} KM" if listing.get("engine_hp") else ""
    miasto = f"  📍 {esc(listing['city'])}" if listing.get("city") else ""
    dni = days_on_market(listing.get("created_at", ""))
    na_rynku = f"  🕐 {dni}d na rynku" if dni is not None else ""
    uszkodzony = "\n⚠️ <b>USZKODZONY / PO WYPADKU</b>" if listing.get("damaged") else ""
    return (f"🔧 <b>{zrodlo}</b>\n\n"
            f"📌 <b>{esc(listing['title'])}</b>{uszkodzony}{obnizka}\n"
            f"💰 {_zl(listing['price_num']) if listing.get('price_num') else listing['price_str']}\n"
            f"{rok}{moc}  {przebieg}{miasto}{na_rynku}"
            f"{tekst_wyceny(listing, wycena)}{format_braki(listing.get('braki'))}\n"
            f"🔍 {esc(nazwa_wyszukiwania)}\n"
            f"🔗 {esc(listing['url'])}")


def format_braki(braki: list) -> str:
    """Czego bot NIE wie o tym aucie. Ogłoszenie z niewypełnionym polem nie
    jest odrzucane, ale musi to powiedzieć wprost — inaczej „przeszło kryteria"
    znaczy raz „sprawdzone", a raz „nie było czego sprawdzić"."""
    if not braki:
        return ""
    return "\n❓ Nie podano w ogłoszeniu: " + ", ".join(braki)


# ---------------------------------------------------------------------------
# Scraping Otomoto (urqlState w Next.js JSON)
# ---------------------------------------------------------------------------

def _parse_node(node: dict) -> Optional[dict]:
    """Normalizuje pojedynczy węzeł ogłoszenia Otomoto."""
    ad_id = str(node.get("id", ""))
    if not ad_id:
        return None

    title = node.get("title", "").strip()
    url = node.get("url", "") or f"https://www.otomoto.pl/oferta/{ad_id}"
    short_desc = node.get("shortDescription", "") or ""
    loc = node.get("location") or {}
    location_city = loc.get("city", {}).get("name", "")
    location_region = loc.get("region", {}).get("name", "").lower()
    created_at = node.get("createdAt", "")

    # Cena — format: price.amount.units (PLN, całkowita)
    price_num = None
    price_str = "brak ceny"
    try:
        amount = node["price"]["amount"]
        price_num = int(float(amount.get("units", 0) or amount.get("value", 0) or 0))
        if price_num:
            price_str = f"{price_num:,} PLN".replace(",", " ")
    except (KeyError, TypeError, ValueError):
        pass

    # Parametry (rok, przebieg, silnik, model, wersja …)
    params = {}
    mileage_num = None
    year = None
    engine_hp = None
    model_value = ""
    version_value = ""

    for p in node.get("parameters", []) or []:
        k = p.get("key", "")
        v = p.get("value", "") or p.get("displayValue", "") or ""
        params[k] = v
        if k == "mileage":
            try:
                mileage_num = int(re.sub(r"\D", "", str(v)))
            except ValueError:
                pass
        elif k == "year":
            try:
                year = int(v)
            except ValueError:
                pass
        elif k == "engine_power":
            try:
                engine_hp = int(re.sub(r"\D", "", str(v)))
            except ValueError:
                pass
        elif k == "model":
            model_value = str(v).lower()
        elif k == "version":
            version_value = str(v).lower()

    engine_cm3 = None
    if params.get("engine_capacity"):
        try:
            engine_cm3 = int(re.sub(r"\D", "", str(params["engine_capacity"])))
        except ValueError:
            pass

    # Otomoto nie zwraca pola `drive` w wynikach wyszukiwania — jedyny ślad po
    # napędzie jest w nazwie wersji („2.0 TDI quattro S tronic") albo w tytule.
    # Gdy go tam nie ma, zostaje None = „nie wiem", a nie „nie ma".
    # Miniatura z wyników wyszukiwania ma 640x480, a ten sam adres oddaje
    # 1080x720 (sprawdzone 17.09.2026), więc zdjęcie nie kosztuje zapytania.
    miniatura = node.get("thumbnail") or {}
    zdjecie = miniatura.get("x2") or miniatura.get("x1") or None
    if zdjecie:
        zdjecie = re.sub(r";s=\d+x\d+", ";s=1080x720", zdjecie)

    naped_tekst = f"{version_value} {title.lower()}"
    drive = "awd" if any(
        w in naped_tekst for w in ("quattro", "xdrive", "4x4", "4matic", "allrad")
    ) else None

    return {
        "id": ad_id,
        "title": title,
        "url": url,
        "short_desc": short_desc,
        "city": location_city,
        "region": location_region,
        "created_at": created_at,
        "price_num": price_num,
        "price_str": price_str,
        "params": params,
        "mileage_num": mileage_num,
        "year": year,
        "engine_hp": engine_hp,
        "model_value": model_value,
        "version_value": version_value,
        # pola znormalizowane — wspólny język z OLX-em dla `sprawdz_kryteria`
        "model_key": model_value,
        "model_label": "",
        "fuel": (params.get("fuel_type") or "").lower() or None,
        "gearbox": (params.get("gearbox") or "").lower() or None,
        "drive": drive,
        "engine_cm3": engine_cm3,
        "body": None,          # brak w odpowiedzi wyszukiwarki Otomoto
        "zdjecie": zdjecie,
        # Otomoto nie ma pola `condition`, a URL-e nie filtrują po uszkodzeniu —
        # słowa kluczowe to jedyny sygnał, więc ich brak znaczy „nieuszkodzone"
        "damaged": is_damaged(title, short_desc),
    }


HEADERS = {
    "Accept-Language": "pl-PL,pl;q=0.9",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}


def _fetch_page(url: str) -> list:
    """Pobiera jedną stronę wyników Otomoto, zwraca listę edges."""
    try:
        r = scraper.get(url, timeout=25, headers=HEADERS)
        r.raise_for_status()
        json_blocks = re.findall(
            r'<script[^>]*type="application/json"[^>]*>(.*?)</script>',
            r.text, re.DOTALL,
        )
        if not json_blocks:
            return []
        page_data = json.loads(json_blocks[0])
        urql_state = page_data.get("props", {}).get("pageProps", {}).get("urqlState", {})
        for v in urql_state.values():
            if not isinstance(v, dict):
                continue
            raw_data = v.get("data", "")
            if not isinstance(raw_data, str) or "advertSearch" not in raw_data:
                continue
            inner = json.loads(raw_data)
            edges = inner.get("advertSearch", {}).get("edges", [])
            if edges:
                return edges
    except Exception as e:
        log.error(f"Scrape error: {e}")
    return []


# Słowniki wartości ze STRONY ogłoszenia (wyniki wyszukiwania ich nie mają).
# Zebrane z żywych danych 22.08.2026.
NADWOZIE_OTOMOTO = {
    "sedan": "sedan", "limuzyna": "sedan",
    "kombi": "kombi", "kompakt": "kompakt", "coupe": "coupe", "coupé": "coupe",
    "kabriolet": "kabriolet", "suv": "suv", "minivan": "minivan",
    "auta małe": "male", "auta miejskie": "miejskie",
}
NAPED_OTOMOTO = {
    "na przednie koła": "fwd",
    "na tylne koła": "rwd",
}


def _mapuj_naped(v: str):
    v = (v or "").strip().lower()
    if not v:
        return None
    if v.startswith("4x4"):          # stały / dołączany automatycznie / ręcznie
        return "awd"
    return NAPED_OTOMOTO.get(v)


def pobierz_szczegoly(url: str) -> dict:
    """Dociąga ze STRONY ogłoszenia pola, których nie ma w wynikach wyszukiwania.

    Powód, zmierzony 22.08.2026: wyniki wyszukiwarki Otomoto niosą tylko
    make/model/rok/paliwo/skrzynia/przebieg/pojemność/moc. Nadwozia i napędu
    tam NIE MA, a filtry w URL-u ich nie pilnują — zapytanie o `bodywork_type
    =sedan` zwraca co do sztuki to samo, co bez filtra. Skutek: pierwsze
    „pasujące" BMW G20 okazało się Kombi na tylne koła, czyli Touring bez
    xDrive — dokładnie to, co miało odpaść. Bot puszczał je z adnotacją
    „nie wiem: nadwozie, napęd".

    Zwraca tylko to, co serwis podał; brak pola zostaje None („nie wiem"),
    zgodnie z zasadą, że brak danych to nie niezgodność. Nigdy nie rzuca —
    awaria dociągania ma degradować bota do stanu sprzed zmiany, nie zabijać.
    """
    out = {"body": None, "drive": None, "damaged": None, "version": None}
    try:
        r = scraper.get(url, timeout=25, headers=HEADERS)
        r.raise_for_status()
        bloki = re.findall(
            r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', r.text, re.DOTALL)
        if not bloki:
            return out
        advert = (json.loads(bloki[0]).get("props", {})
                  .get("pageProps", {}).get("advert") or {})
        pola = {p.get("key"): p.get("value") for p in (advert.get("details") or [])}
        if pola.get("body_type"):
            out["body"] = NADWOZIE_OTOMOTO.get(str(pola["body_type"]).strip().lower())
        out["drive"] = _mapuj_naped(pola.get("transmission"))
        if pola.get("damaged"):
            out["damaged"] = str(pola["damaged"]).strip().lower() in ("tak", "yes", "true")
        if pola.get("version"):
            out["version"] = str(pola["version"]).lower()
    except Exception as e:
        log.warning(f"Nie udało się dociągnąć szczegółów ({str(e)[:60]}): {url}")
    return out


def uzupelnij_ze_strony(listing: dict) -> dict:
    """Wpisuje dociągnięte pola do ogłoszenia. Nie nadpisuje wiedzy niewiedzą."""
    szcz = pobierz_szczegoly(listing["url"])
    for pole in ("body", "drive", "version"):
        if szcz.get(pole) is not None:
            listing[pole] = szcz[pole]
    if szcz.get("damaged") is not None:
        # Pole ze strony jest mocniejsze niż założenie z filtra w URL-u
        listing["damaged"] = szcz["damaged"]
        if szcz["damaged"]:
            listing.pop("szkoda_nieopisana", None)
    return listing


def fetch_listings_otomoto(search: dict, pages: int = 4) -> list[dict]:
    """Pobiera kilka stron wyników żeby mieć pulę do porównania cen."""
    results = []
    seen_ids = set()

    for page in range(1, pages + 1):
        sep = "&" if "?" in search["url"] else "?"
        url = f"{search['url']}{sep}page={page}"
        edges = _fetch_page(url)
        log.info(f"[{search['name']}] strona {page}: {len(edges)} edges")
        if not edges:
            break
        for edge in edges:
            node = edge.get("node", edge)
            ad = _parse_node(node)
            if ad and ad["id"] not in seen_ids:
                seen_ids.add(ad["id"])
                results.append(ad)
        if page == pages and len(edges) >= 32:
            # pełna ostatnia strona = dalsze ogłoszenia niewidoczne, a Otomoto
            # nie sortuje po dacie, więc ucięte mogą być akurat te najnowsze
            zglos_problem(f"Otomoto, {search['name']}: {pages} pełne strony, "
                          f"dalszych ogłoszeń bot nie widzi")

    # Otomoto ma własny filtr uszkodzonych (filter_enum_damaged=1) i on DZIAŁA:
    # z filtrem i bez niego dostajemy rozłączne zbiory ofert. To ważne, bo
    # wyniki wyszukiwarki nie zawierają pola o stanie, a słowa w tytule prawie
    # nigdy nie padają (0 na 32 w próbce) — na samych słowach ta połowa bota
    # milczała od 6 lipca. Skoro oferta przyszła z takiego zapytania, jest
    # uszkodzona; gdy treść tego nie potwierdza, mówimy o tym w wiadomości.
    if "filter_enum_damaged" in search["url"]:
        for ad in results:
            if not ad["damaged"]:
                ad["damaged"] = True
                ad["szkoda_nieopisana"] = True

    return results


# ---------------------------------------------------------------------------
# OLX scraper (REST API)
# ---------------------------------------------------------------------------

def _parse_olx_param(params: list, key: str):
    for p in params:
        if p.get("key") == key:
            v = p.get("value", {})
            if isinstance(v, dict):
                return v.get("key") or v.get("value") or v.get("label")
            return v
    return None


def _parse_olx_label(params: list, key: str) -> str:
    """Etykieta pola, np. „Seria 3". Potrzebna obok klucza, bo OLX trzyma dla
    BMW slugi węgierskie („3-as-sorozat") — po samym kluczu nie da się
    dopasować modelu do tego, co zwraca Otomoto."""
    for p in params:
        if p.get("key") == key:
            v = p.get("value", {})
            if isinstance(v, dict):
                return str(v.get("label") or "")
            return str(v or "")
    return ""


# Sufit stron na jedno wyszukiwanie OLX. Przy filtrach z 16.09.2026 największa
# pula (Seria 3, uszkodzone diesle z automatem 2017-2023) mieści się na jednej,
# więc sufit to bezpiecznik na przyszłość, nie codzienność.
OLX_STRON_MAX = 6


def fetch_listings_olx(search: dict) -> list[dict]:
    results = []
    try:
        # przez wspólne wejście z tracker.py, które obsługuje przekaźnik
        # Cloudflare, bez którego serwerownia GitHuba dostaje od OLX-a 403
        from urllib.parse import urlencode
        from olx import olx_get
        ads, znane_id = [], set()
        limit = search["params"].get("limit", 50)
        for strona in range(OLX_STRON_MAX):
            params = {**search["params"], "offset": strona * limit}
            r = olx_get(OLX_API + "?" + urlencode(params), timeout=25)
            if r is None or r.status_code != 200:
                _bieg["olx_bledy"] += 1
                log.error(f"[{search['name']}] OLX API niedostepne "
                          f"(status {getattr(r, 'status_code', 'brak')}, strona {strona + 1})")
                if strona == 0:
                    return results
                break          # to, co już przyszło, i tak sprawdzamy
            _bieg["olx_ok"] += 1
            odp = r.json()
            # promowane wracają na każdej stronie, a ta sama oferta dwa razy
            # w jednym biegu to dwa sprawdzenia tego samego auta
            for ad in odp.get("data", []):
                if ad.get("id") not in znane_id:
                    znane_id.add(ad.get("id"))
                    ads.append(ad)
            if not (odp.get("links") or {}).get("next"):
                break
        else:
            zglos_problem(f"OLX, {search['name']}: urwane na {OLX_STRON_MAX} stronach, "
                          f"dalszych ogłoszeń bot nie widzi")
        log.info(f"[{search['name']}] OLX API: {len(ads)} ogłoszeń")
        odrzucone = 0

        for ad in ads:
            params = ad.get("params", [])

            # Cena
            price_num = None
            price_str = "brak ceny"
            price_param = _parse_olx_param(params, "price")
            if isinstance(price_param, dict):
                price_num = int(price_param.get("value") or 0) or None
            elif price_param:
                try:
                    price_num = int(price_param)
                except (ValueError, TypeError):
                    pass
            # Fallback przez wartość w params
            if price_num is None:
                for p in params:
                    if p.get("key") == "price":
                        v = p.get("value", {})
                        if isinstance(v, dict) and v.get("value"):
                            price_num = int(v["value"])
            if price_num:
                price_str = f"{price_num:,} PLN".replace(",", " ")

            # Parametry — wszystko z pól strukturalnych, zero zgadywania z tytułu
            def liczba(klucz):
                try:
                    return int(re.sub(r"\D", "", str(_parse_olx_param(params, klucz) or ""))) or None
                except (ValueError, TypeError):
                    return None

            year = liczba("year")
            mileage_num = liczba("milage")
            engine_hp = liczba("enginepower")
            engine_cm3 = liczba("enginesize")
            model_key = str(_parse_olx_param(params, "model") or "").lower()
            model_label = _parse_olx_label(params, "model")
            fuel = str(_parse_olx_param(params, "petrol") or "").lower() or None
            gearbox = str(_parse_olx_param(params, "transmission") or "").lower() or None
            body = str(_parse_olx_param(params, "car_body") or "").lower() or None
            drive = NAPED_MAPA.get(str(_parse_olx_param(params, "drive") or "").lower())

            title_lower = ad.get("title", "").lower()
            desc_lower = ad.get("description", "")[:300].lower()

            # `condition` to pole wyboru w formularzu OLX-a — pewniejsze niż
            # słowa w opisie. Na słowa schodzimy tylko, gdy pola brak.
            stan = str(_parse_olx_param(params, "condition") or "").lower()
            if stan == "damaged":
                damaged = True
            elif stan == "notdamaged":
                damaged = False
            else:
                damaged = is_damaged(title_lower, desc_lower)

            # adres zdjęcia ma w sobie wzór "{width}x{height}" (sprawdzone 17.09.2026)
            zdjecia = ad.get("photos") or []
            zdjecie = None
            if zdjecia and (zdjecia[0] or {}).get("link"):
                zdjecie = zdjecia[0]["link"].replace("{width}", "1080").replace("{height}", "720")

            loc = ad.get("location") or {}
            listing = {
                "id": f"olx_{ad['id']}",
                "zdjecie": zdjecie,
                "title": ad.get("title", "").strip(),
                "url": ad.get("url", ""),
                "short_desc": ad.get("description", "")[:300],
                "city": loc.get("city", {}).get("name", ""),
                "region": loc.get("region", {}).get("name", "").lower(),
                "created_at": ad.get("created_time", ""),
                "price_num": price_num,
                "price_str": price_str,
                "mileage_num": mileage_num,
                "year": year,
                "engine_hp": engine_hp,
                "model_value": model_key,
                "version_value": "",
                "params": {},
                "model_key": model_key,
                "model_label": model_label,
                "fuel": fuel,
                "gearbox": gearbox,
                "drive": drive,
                "engine_cm3": engine_cm3,
                "body": body,
                "damaged": damaged,
                # lustro oferty z Otomoto — 47 z 51 wyników OLX-a to ten sam
                # samochód, który mamy już z drugiego kanału (patrz `main`)
                "external_url": ad.get("external_url") or "",
            }

            pasuje, braki = sprawdz_kryteria(listing, search["kryteria"])
            if not pasuje:
                odrzucone += 1
                continue
            listing["braki"] = braki
            results.append(listing)

        log.info(f"[{search['name']}] przeszło kryteria: {len(results)}, "
                 f"odrzucone: {odrzucone}")
    except Exception as e:
        log.error(f"OLX fetch error [{search['name']}]: {e}")
    return results


# ---------------------------------------------------------------------------
# Obserwowani wystawcy
#
# Pilnujemy KONKRETNEGO człowieka, nie modelu auta. Droga dojścia była kręta i
# warto wiedzieć czemu akurat tak (sprawdzone 21.08.2026):
#
#  * Otomoto nie pozwala wylistować ogłoszeń prywatnego sprzedawcy. Filtry
#    ?search[seller_id]=, /uzytkownik/<uuid> i ?search[city_id]= są po cichu
#    ignorowane — zwracają zwykłą listę wszystkich aut. Link „zobacz więcej
#    ofert tego sprzedawcy" dostają wyłącznie firmy. Skan całego województwa
#    odpada: wyniki nie są sortowane po dacie, a szukanej oferty nie było
#    w pierwszych 480.
#  * Na OLX ten sprzedawca NIE MA własnego konta. Jego ogłoszenia to lustra
#    ofert z Otomoto (`partner.code = otomoto_pl_form`) i wszystkie wiszą pod
#    technicznym kontem OLX-a o id 23063449 — wspólnym dla całej Polski.
#    Pilnowanie tego konta dałoby auta z Gdańska i Szczecina.
#  * Za to filtr miejscowości na OLX (`city_id`) działa dokładnie i daje
#    kilkadziesiąt ogłoszeń zamiast tysięcy.
#
# Stąd konstrukcja: pytamy OLX o miejscowość, a tożsamość wystawcy
# rozstrzygamy dopiero po `sellerId` ze strony Otomoto, do której prowadzi
# `external_url`. Nazwa kontaktowa NIE wystarcza — OLX pozwala ustawić inną
# przy każdym ogłoszeniu, a w tej samej wsi siedzą „Darek" i „Kuba", którzy
# są osobnymi sprzedawcami.
# ---------------------------------------------------------------------------

SEEN_WYSTAWCY_FILE = Path("seen_wystawcy.json")

WYSTAWCY = [
    {
        "nazwa": "Leszek, Oleśnica (pow. staszowski)",
        "otomoto_seller_id": "17449440",
        # cała motoryzacja (5), nie same osobowe (84): ten sprzedawca wystawia
        # też dostawcze — dwa Renault Master wpadłyby w dziurę
        "olx_category_id": 5,
        "olx_city_id": 103125,      # Oleśnica, pow. staszowski, świętokrzyskie
        "imie_kontaktowe": "leszek",
    },
]


def load_seen_wystawcy() -> dict:
    if SEEN_WYSTAWCY_FILE.exists():
        return json.loads(SEEN_WYSTAWCY_FILE.read_text())
    return {}


def save_seen_wystawcy(seen: dict):
    SEEN_WYSTAWCY_FILE.write_text(json.dumps(seen, ensure_ascii=False, indent=2))


def otomoto_id_z_url(url: str) -> Optional[str]:
    """Token oferty Otomoto z adresu ('...-ID6HMNXq.html' → 'ID6HMNXq').

    Slug bywa różny dla tego samego auta (OLX skleja własny), więc porównujemy
    wyłącznie token — jedyną trwałą częścią adresu.

    Host sprawdzany celowo: adresy OLX-a mają token w tym samym kształcie
    ('...-CID5-ID1btpnU.html'), więc bez tego dwa różne serwisy mogłyby sobie
    nawzajem zjeść ogłoszenie przy zbiegu identyfikatorów."""
    if "otomoto.pl" not in (url or ""):
        return None
    m = re.search(r"-(ID[0-9A-Za-z]+)\.html", url)
    return m.group(1) if m else None


# Wynik odczytu sprzedawcy dla oferty, której na Otomoto już nie ma. Osobny
# stan, bo "oferta zniknęła" to fakt, a "nie udało się przeczytać" to brak
# wiedzy. Zapamiętać wolno tylko fakt.
ZDJETA = "zdjeta"


def otomoto_seller_id(url: str) -> Optional[str]:
    """Identyfikator wystawcy ze strony oferty Otomoto.

    Trzy wyniki, nie dwa: numer sprzedawcy, ZDJETA albo None ("nie wiem").
    Zdjęta oferta to czyste HTTP 410 bez przekierowania (zmierzone 15.09.2026
    na pięciu ofertach z lipca), a żywa oddaje 200 z `advert.seller.id`.
    Wszystko inne, czyli 403, 404, 5xx, przekroczony czas albo strona bez
    numeru, to None: "sprawdź w następnym biegu", nigdy "to ktoś inny".
    404 nie było w pomiarze, więc nie udaje tu wiedzy o zdjęciu."""
    try:
        r = scraper.get(url, timeout=25, headers=HEADERS)
        if r.status_code == 410:
            return ZDJETA
        if r.status_code != 200:
            return None
        blocks = re.findall(
            r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', r.text, re.DOTALL)
        if blocks:
            try:
                seller = (json.loads(blocks[0]).get("props", {}).get("pageProps", {})
                          .get("advert", {}).get("seller", {}))
                if seller.get("id"):
                    return str(seller["id"])
            except (ValueError, AttributeError):
                pass
        m = re.search(r'"sellerId":"(\d+)"', r.text)   # zapasowo, z danych śledzących
        return m.group(1) if m else None
    except Exception as e:
        log.error(f"Otomoto seller id [{url[:60]}]: {e}")
        return None


def sprawdz_wystawce(wystawca: dict, seen: dict, odczyty: Optional[dict] = None) -> int:
    """Nowe ogłoszenia obserwowanego wystawcy. Zwraca liczbę wysłanych.

    `odczyty` dostaje liczbę prób i udanych odczytów sprzedawcy z Otomoto.
    Na nich stoi czujka `ocen_obserwacje`."""
    from urllib.parse import urlencode
    from olx import olx_get

    if odczyty is None:
        odczyty = {"proby": 0, "udane": 0}
    wyslane = 0
    params = {"category_id": wystawca["olx_category_id"],
              "city_id": wystawca["olx_city_id"], "limit": 50, "currency": "PLN"}
    r = olx_get(OLX_API + "?" + urlencode(params), timeout=25)
    if r is None or r.status_code != 200:
        log.error(f"[{wystawca['nazwa']}] OLX niedostępne "
                  f"(status {getattr(r, 'status_code', 'brak')})")
        return 0

    ogloszenia = r.json().get("data", [])
    nowe = [a for a in ogloszenia if f"w_{a['id']}" not in seen]
    log.info(f"[{wystawca['nazwa']}] w miejscowości: {len(ogloszenia)}, nowych: {len(nowe)}")

    for a in nowe:
        lid = f"w_{a['id']}"
        # Wszystko, czego tu trzeba, jest w pozycji LISTY: external_url, imię
        # kontaktowe, cena w `params`, miejscowość i data (sprawdzone 15.09.2026
        # na 33 ogłoszeniach z Oleśnicy). Do tego dnia bot dopytywał o każde
        # ogłoszenie osobno adresem /api/v1/offers/<id>/, którego przekaźnik
        # Cloudflare nie przepuszcza. Odmowa była zapisywana tak samo jak
        # "to nie on", więc od 22.08 obserwacja odhaczyła 52 ogłoszenia bez
        # sprawdzenia, w tym 6 ogłoszeń Leszka, i nie wysłała ani jednego.
        ext = a.get("external_url") or ""
        kontakt = str((a.get("contact") or {}).get("name") or "")

        pewnosc = None
        if "otomoto.pl" in ext:
            odczyty["proby"] += 1
            sprzedawca = otomoto_seller_id(ext)
            if sprzedawca is None:
                # NIEPRZECZYTANE TO NIE "KTOŚ INNY". Bez wpisu do `seen`
                # ogłoszenie wraca w następnym biegu.
                log.error(f"[{wystawca['nazwa']}] nie udało się odczytać sprzedawcy, "
                          f"sprawdzę w następnym biegu: {ext}")
                continue
            odczyty["udane"] += 1
            if sprzedawca == ZDJETA:
                seen[lid] = {"powod": "zdjete_z_otomoto"}
                continue
            if sprzedawca != wystawca["otomoto_seller_id"]:
                seen[lid] = {"powod": "inny_sprzedawca", "sprzedawca": sprzedawca}
                continue
            pewnosc = "potwierdzony"
        elif kontakt.lower() == wystawca["imie_kontaktowe"]:
            # Wystawione wprost na OLX, bez lustra z Otomoto. Nie ma po czym
            # potwierdzić tożsamości, więc mówimy o tym wprost.
            pewnosc = "niepotwierdzony"

        if not pewnosc:
            seen[lid] = {"powod": "inne_imie"}    # ktoś inny z tej samej miejscowości
            continue

        # cena siedzi w `params`, nie w polu najwyższego poziomu, bo samo
        # "price" jest puste i dawało "brak ceny" przy każdej ofercie
        cena_str = (_parse_olx_label(a.get("params", []), "price")
                    or (a.get("price") or {}).get("displayValue") or "brak ceny")
        loc = (a.get("location") or {}).get("city", {}).get("name", "")
        dni = days_on_market(a.get("created_time", ""))
        naglowek = ("👤 <b>ŚLEDZONY WYSTAWCA</b>" if pewnosc == "potwierdzony"
                    else "👤 <b>ŚLEDZONY WYSTAWCA?</b>")
        uwaga = ("" if pewnosc == "potwierdzony" else
                 "\n❓ Zgadza się tylko imię kontaktowe. Wystawione wprost na "
                 "OLX, więc nie da się potwierdzić po koncie Otomoto")
        tekst = (
            f"{naglowek}\n\n"
            f"📌 <b>{a.get('title', '')}</b>\n"
            f"💰 {cena_str}\n"
            f"🧑 {kontakt}  📍 {loc}"
            f"{f'  🕐 {dni}d na rynku' if dni is not None else ''}{uwaga}\n"
            f"🔍 {wystawca['nazwa']}\n"
            f"🔗 {a.get('url', '')}"
            + (f"\n🔗 Otomoto: {ext}" if ext else "")
        )
        # treść zostaje we wpisie, dopóki Telegram jej nie przyjmie (patrz `dosylka`)
        seen[lid] = {
            "title": a.get("title", ""),
            "url": a.get("url", ""),
            "otomoto_url": ext,
            "kontakt": kontakt,
            "pewnosc": pewnosc,
            "date": date.today().isoformat(),
            "do_wyslania": tekst,
        }
        if send_telegram(tekst):
            del seen[lid]["do_wyslania"]
            wyslane += 1
        log.info(f"[{wystawca['nazwa']}] nowe ({pewnosc}): {a.get('title','')[:50]}")

    return wyslane


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

STAN_FILE = Path("otomoto_stan.json")
PUSTE_DO_ALARMU = 2      # bieg co 30 min, więc alarm po ~godzinie martwoty


def _stan(zmiana=None) -> dict:
    stan = {}
    if STAN_FILE.exists():
        try:
            stan = json.loads(STAN_FILE.read_text())
        except Exception:
            stan = {}
    if zmiana is not None:
        stan.update(zmiana)
        try:
            STAN_FILE.write_text(json.dumps(stan))
        except Exception as e:
            log.error(f"zapis stanu: {e}")
    return stan


def ocen_zdrowie(pobrano_otomoto: int, pobrano_olx: int):
    """Milczący bot wygląda dokładnie jak spokojny rynek i to jest pułapka.

    `_fetch_page` łyka każdy błąd i zwraca pustą listę, więc blokada albo zmiana
    formatu kończy się ciszą bez końca. Alarm po PUSTE_DO_ALARMU pustych biegach
    (jeden to zwykle chwilowa wpadka), raz, plus jedno zdanie, gdy wróci.

    KAŻDE ŹRÓDŁO OSOBNO (od 16.09.2026). Wcześniej alarm zapalał się tylko, gdy
    padły oba naraz, więc martwy OLX przy żywym Otomoto był ciszą, a połowa
    rynku znikała bez słowa. `pobrano_olx` to liczba udanych odpowiedzi API OLX,
    nie pasujących aut: przy wąskich kryteriach zero pasujących to zwykły dzień.
    """
    try:
        stan = _stan()
        zmiana, padly, wrocily = {}, [], []
        for zrodlo, pobrano in (("Otomoto", pobrano_otomoto), ("OLX", pobrano_olx)):
            k_puste, k_zgl = f"puste_{zrodlo.lower()}", f"zgloszone_{zrodlo.lower()}"
            if pobrano:
                if stan.get(k_zgl):
                    wrocily.append(zrodlo)
                zmiana.update({k_puste: 0, k_zgl: False})
                continue
            puste = stan.get(k_puste, 0) + 1
            zmiana[k_puste] = puste
            log.error(f"Pusty przebieg {zrodlo} ({puste}. z rzędu)")
            if puste >= PUSTE_DO_ALARMU and not stan.get(k_zgl):
                zmiana[k_zgl] = True
                padly.append(zrodlo)
        _stan(zmiana)
        if len(padly) == 2:
            send_telegram(
                "🔕 <b>OtomotoHawk nie widzi ogłoszeń</b>\n\n"
                "Od godziny ani Otomoto, ani OLX nie oddają żadnych aut. "
                "To może być blokada albo przebudowa strony.\n"
                "Próbuję dalej co pół godziny. Odezwę się, gdy wróci.")
        elif padly:
            drugie = "OLX" if padly[0] == "Otomoto" else "Otomoto"
            send_telegram(
                f"🔕 <b>OtomotoHawk nie widzi ogłoszeń z {padly[0]}</b>\n\n"
                f"Od godziny nie dostaję żadnych ogłoszeń z {padly[0]}, więc oferty "
                f"stamtąd mogą nie przychodzić. {drugie} sprawdzam dalej normalnie.\n"
                "Próbuję co pół godziny. Odezwę się, gdy wróci.")
        if wrocily:
            send_telegram(f"✅ <b>OtomotoHawk: {' i '.join(wrocily)} "
                          f"{'już działają' if len(wrocily) == 2 else 'już działa'}.</b>")
            log.info(f"Powrót do normy: {', '.join(wrocily)}")
    except Exception as e:
        log.error(f"ocen_zdrowie error: {e}")


# Ile godzin obserwacja wystawców może być ślepa, zanim powie o tym na
# Telegramie. Czas, nie liczba biegów: GitHub puszcza tego bota od 2 do 40
# razy na dobę (zmierzone 15.08-15.09.2026), więc "dwa biegi" to raz godzina,
# a raz dziesięć.
SLEPOTA_DO_ALARMU_H = 6


def ocen_obserwacje(proby: int, udane: int, teraz: Optional[datetime] = None):
    """Czujka na obserwację wystawców, osobna od `ocen_zdrowie`.

    `ocen_zdrowie` liczy wyłącznie wyszukiwania modeli, więc przez 24 dni,
    kiedy obserwacja nie sprawdziła ani jednego ogłoszenia, stała na zielono.
    Od 15.09.2026 nieprzeczytane ogłoszenie nie jest odhaczane, tylko wraca
    w następnym biegu. Trwała awaria nie zjada więc ogłoszeń, ale nadal
    oznacza ciszę, i o tym jest ten alarm.

    Ślepy bieg to taki, w którym były próby odczytu sprzedawcy i żadna się
    nie udała. Bieg bez prób przerywa ciąg: nieprzeczytane ogłoszenie wraca
    co bieg, więc brak prób znaczy, że zniknęło z listy, a nie że awaria trwa.
    """
    teraz = teraz or datetime.now(timezone.utc)
    try:
        stan = _stan()
        zgloszone = bool(stan.get("wystawcy_zgloszone"))
        slepa_od = stan.get("wystawcy_slepa_od")
        if udane:
            if slepa_od or zgloszone:
                _stan({"wystawcy_slepa_od": None, "wystawcy_zgloszone": False})
            if zgloszone:
                send_telegram("✅ <b>OtomotoHawk znowu sprawdza obserwowanych sprzedawców.</b>")
                log.info("Obserwacja wystawców wróciła, wysłano potwierdzenie")
            return
        if not proby:
            if slepa_od:
                _stan({"wystawcy_slepa_od": None})
            return
        if not slepa_od:
            _stan({"wystawcy_slepa_od": teraz.isoformat()})
            return
        godzin = (teraz - datetime.fromisoformat(slepa_od)).total_seconds() / 3600
        log.error(f"Obserwacja wystawców nie widzi sprzedawców od {godzin:.1f} h")
        if godzin >= SLEPOTA_DO_ALARMU_H and not zgloszone:
            _stan({"wystawcy_zgloszone": True})
            send_telegram(
                "🔕 <b>OtomotoHawk nie może sprawdzić, kto wystawia</b>\n\n"
                "Od kilku godzin nie otwierają mi się ogłoszenia na Otomoto, więc nie "
                "wiem, czy nowe auta są od sprzedawców, których pilnuję. Niczego nie "
                "odhaczam, sprawdzę je, gdy strona wróci.")
    except Exception as e:
        log.error(f"ocen_obserwacje error: {e}")


# Łańcuszek w otomoto.yml daje bieg co ~30 min. Dwie godziny przerwy znaczą,
# że się zerwał i bot wrócił na cron, a ten od 27.08.2026 dowoził mediana co
# 3,6 h, maksymalnie co 11,4 h (zmierzone 15.09.2026 na 118 biegach). Przez
# trzy tygodnie nikt tego nie zauważył, bo wolny bot wygląda jak spokojny rynek.
PRZERWA_DO_ALARMU_H = 2


def ocen_tempo(teraz: Optional[datetime] = None):
    """Czy bot chodzi tak często, jak powinien. Jeden alarm na epizod i jedno
    zdanie, gdy przerwy wrócą do pół godziny."""
    teraz = teraz or datetime.now(timezone.utc)
    try:
        stan = _stan()
        zmiana = {"ostatni_bieg": teraz.isoformat(timespec="seconds")}
        poprzedni = stan.get("ostatni_bieg")
        if poprzedni:
            godzin = (teraz - datetime.fromisoformat(poprzedni)).total_seconds() / 3600
            zgloszone = bool(stan.get("tempo_zgloszone"))
            if godzin >= PRZERWA_DO_ALARMU_H:
                log.error(f"Przerwa od poprzedniego biegu: {godzin:.1f} h")
                zglos_problem(f"przerwa w sprawdzaniu: około {round(godzin)} godz.")
                if not zgloszone:
                    zmiana["tempo_zgloszone"] = True
                    send_telegram(
                        "🐢 <b>OtomotoHawk sprawdza rzadziej, niż powinien</b>\n\n"
                        f"Od poprzedniego sprawdzenia minęło około {round(godzin)} godz., "
                        "a powinno pół godziny. Nowe auta mogą przychodzić z opóźnieniem. "
                        "Dam znać, gdy wróci normalne tempo.")
            elif godzin < 1 and zgloszone:
                zmiana["tempo_zgloszone"] = False
                send_telegram("✅ <b>OtomotoHawk znowu sprawdza co pół godziny.</b>")
                log.info("Tempo biegów wróciło, wysłano potwierdzenie")
        _stan(zmiana)
    except Exception as e:
        log.error(f"ocen_tempo error: {e}")


PODSUMOWANIE_OD_GODZ = 18
STREFA = ZoneInfo("Europe/Warsaw")


def ocen_dzien(bieg: dict, teraz: Optional[datetime] = None):
    """Podsumowanie dnia po 18:00 czasu polskiego: DOWÓD, że bot żyje (16.09.2026).

    Właściciel po miesiącu ciszy: "jedyne, co chcę, to pewność, że gdy pojawi się
    nowa oferta, to mnie powiadomisz". Alarmy krzyczą, gdy coś padnie, ale nie
    krzykną, gdy bot nie chodzi WCALE (wyłączony, GitHub stoi, program pada na
    starcie), bo wtedy nie ma kto krzyczeć. Na to jest odwrotna umowa: podsumowanie
    przychodzi codziennie, a jego BRAK znaczy awarię. Cisza przestaje być dwuznaczna.

    Liczniki dnia siedzą w `otomoto_stan.json`, bo każdy bieg to osobny proces.
    Dzień wysyłki zapisywany dopiero po przyjęciu przez Telegram, więc odmowa
    oznacza ponowienie za pół godziny, a nie zgubione podsumowanie."""
    teraz = teraz or datetime.now(timezone.utc)
    lokalnie = teraz.astimezone(STREFA)
    dzis = lokalnie.date().isoformat()
    try:
        stan = _stan()
        d = stan.get("dzien") or {}
        if d.get("data") != dzis:
            # "od": godzina pierwszego biegu dnia. Bez niej licznik uruchomiony
            # w połowie dnia (wdrożenie, powrót po awarii) udawałby cały dzień.
            d = {"data": dzis, "od": lokalnie.strftime("%H:%M"), "biegi": 0,
                 "otomoto_ok": 0, "olx_ok": 0, "wyslane": 0, "problemy": []}
        d["biegi"] += 1
        d["otomoto_ok"] += 1 if bieg.get("otomoto_ok") else 0
        d["olx_ok"] += 1 if bieg.get("olx_ok") else 0
        d["wyslane"] += bieg.get("wyslane", 0)
        for problem in bieg.get("problemy", []):
            if problem not in d["problemy"]:
                d["problemy"] = (d["problemy"] + [problem])[-5:]
        d["pasujace_polska"] = bieg.get("pasujace_polska")
        d["pasujace_region"] = bieg.get("pasujace_region")
        _stan({"dzien": d})
        if lokalnie.hour < PODSUMOWANIE_OD_GODZ or stan.get("podsumowanie") == dzis:
            return
        if send_telegram(tekst_podsumowania(d)):
            _stan({"podsumowanie": dzis})
            log.info("Wysłano podsumowanie dnia")
    except Exception as e:
        log.error(f"ocen_dzien error: {e}")


def tekst_podsumowania(d: dict) -> str:
    def zrodlo(nazwa, ok):
        if ok == d["biegi"]:
            return f"{nazwa}: działa"
        return f"{nazwa}: odpowiadało w {ok} z {d['biegi']} sprawdzeń"
    linie = ["📋 <b>OtomotoHawk: podsumowanie dnia</b>", "",
             f"Sprawdzeń dziś: {d['biegi']} (pierwsze o {d.get('od', '?')})",
             zrodlo("Otomoto", d["otomoto_ok"]),
             zrodlo("OLX", d["olx_ok"]),
             f"Nowe oferty wysłane dziś: {d['wyslane']}"]
    if d.get("pasujace_polska") is not None:
        linie.append(f"Pasujące rozbitki na OLX teraz: w Twoich województwach "
                     f"{d['pasujace_region']}, w całej Polsce {d['pasujace_polska']}")
    if d["problemy"]:
        linie += ["", "⚠️ Problemy dziś:"] + [f"- {p}" for p in d["problemy"]]
    linie += ["", "Brak tego podsumowania do 19:00 znaczy, że bot nie działa."]
    return "\n".join(linie)


def obsluz_bez_wyniku(lista: list, dzis: str) -> None:
    """Ostatnia zapora. Każde pasujące ogłoszenie z wybranych województw musi po
    biegu mieć wynik we wpisie: wysłane, odrzucone ze strony z powodem albo lustro.
    Brak wyniku to błąd w kodzie, nie brak ofert.

    Dokładnie tak wyglądał błąd, przez który wyszukiwanie A4 nie wysłało niczego
    aż do 16.09.2026: bot chodził, biegi były zielone, alarmy milczały, a auta
    ginęły po cichu między dwoma wyszukiwaniami. Alarm idzie RAZ na ogłoszenie
    i od razu niesie linki, żeby auto nie przepadło. `lista` to krotki
    (plik seen, id, adres, tytuł)."""
    log.error(f"Pasujące ogłoszenia bez wyniku: {len(lista)}")
    zglos_problem(f"{len(lista)} pasujących ogłoszeń bez wyniku (błąd bota, linki poszły alarmem)")
    linki = "\n".join(f"🔗 {url}" for _, _, url, _ in lista[:10])
    wiecej = f"\n...i {len(lista) - 10} więcej" if len(lista) > 10 else ""
    tekst = ("⚠️ <b>OtomotoHawk: błąd bota, te pasujące ogłoszenia nie zostały obsłużone</b>\n\n"
             "To usterka w programie, nie brak ofert. Żeby nic nie przepadło:\n" + linki + wiecej)
    przyjete = send_telegram(tekst)
    for i, (seen, lid, url, tytul) in enumerate(lista):
        seen[lid] = {"powod": "bez_wyniku", "url": url, "title": tytul, "date": dzis}
        if i == 0 and not przyjete:
            seen[lid]["do_wyslania"] = tekst


WYWROTKA_CO_H = 6


def zglos_wywrotke(blad: Exception, teraz: Optional[datetime] = None):
    """Wywrotka programu to cisza, której nie zgłosi żaden inny alarm, bo wszystkie
    siedzą na końcu `main`. Raz na WYWROTKA_CO_H godzin, żeby błąd powtarzany co
    pół godziny nie zamienił się w spam."""
    teraz = teraz or datetime.now(timezone.utc)
    try:
        ostatnio = _stan().get("wywrotka_zgloszona")
        if ostatnio and teraz - datetime.fromisoformat(ostatnio) < timedelta(hours=WYWROTKA_CO_H):
            return
        if send_telegram("⚠️ <b>OtomotoHawk: błąd w programie</b>\n\n"
                         "Sprawdzanie ofert przerwał błąd. Próbuję dalej co pół godziny, "
                         "ale dopóki to trwa, nowe oferty mogą nie przychodzić.\n"
                         f"(dla serwisu: {type(blad).__name__})"):
            _stan({"wywrotka_zgloszona": teraz.isoformat(timespec="seconds")})
    except Exception as e:
        log.error(f"zglos_wywrotke error: {e}")


def main():
    _bieg_reset()
    ocen_tempo()          # na starcie: wywrotka skanu niżej nie może zgubić znacznika
    seen = load_seen()
    seen_olx = load_seen_olx()
    seen_wystawcy = load_seen_wystawcy()
    new_count = 0
    pobrano_otomoto = 0
    today = date.today().isoformat()
    czytaj_przyciski({**seen, **seen_olx})

    # Najpierw zaległe wiadomości, których Telegram poprzednio nie przyjął.
    wyslane = (dosylka(seen, save_seen) + dosylka(seen_olx, save_seen_olx)
               + dosylka(seen_wystawcy, save_seen_wystawcy))
    # Każde pasujące ogłoszenie z wybranych województw musi po biegu mieć wynik
    # we wpisie. Sprawdza to na końcu `obsluz_bez_wyniku`.
    kandydaci = []

    for search in SEARCHES:
        listings = fetch_listings_otomoto(search)
        pobrano_otomoto += len(listings)
        log.info(f"[{search['name']}] sparsowano {len(listings)} ogłoszeń")


        for listing in listings:
            lid = listing["id"]

            # Twarde kryteria na polach strukturalnych. Filtry w URL-u nie
            # wystarczają: /osobowe/audi/a5 dokłada „podobne oferty" i wracają
            # z niego A6 Limousine, Q5 czy A4 Avant.
            # Tanie sito na polach z wyszukiwarki — odsiewa większość ZANIM
            # wydamy żądanie na stronę ogłoszenia. Pełne sprawdzenie, już
            # z nadwoziem i napędem, jest niżej, po odsianiu znanych ofert.
            #
            # Odrzut sita i województwa NIE trafia do `seen`. Do 16.09.2026
            # trafiał jako {} i zjadał wyszukiwanie A4: Otomoto ignoruje model
            # w adresie Audi, więc A5 i A4 dostają tę samą pulę, A5 szło
            # pierwsze i odhaczało każde A4 jako znane, zanim wyszukiwanie A4
            # w ogóle je zobaczyło. Sito i tak chodzi co bieg, zapytań nie kosztuje.
            pasuje, _ = sprawdz_kryteria(listing, search["kryteria"])
            if not pasuje:
                continue

            # Filtr województwa
            if not in_allowed_region(listing.get("region", "")):
                log.info(f"Pominięto (region {listing.get('region','?')}): {listing['title'][:45]}")
                continue
            kandydaci.append((seen, lid, listing["url"], listing["title"]))

            # Wykrywanie obniżki ceny (ogłoszenie znane, ale cena spadła)
            prev = seen.get(lid)
            price_drop_str = ""
            if prev and isinstance(prev, dict) and prev.get("price_num") and listing["price_num"]:
                drop = prev["price_num"] - listing["price_num"]
                if drop >= 500:
                    price_drop_str = f"\n📉 <b>OBNIŻKA o {drop:,} zł!</b> (było: {prev['price_num']:,} zł)".replace(",", " ")
                    log.info(f"Obniżka ceny o {drop} zł: {listing['title'][:50]}")
                    seen[lid]["price_num"] = listing["price_num"]
                else:
                    seen[lid]["price_num"] = listing["price_num"]
                    continue  # znane ogłoszenie, brak istotnej zmiany
            elif seen.get(lid):
                # znane, brak danych cenowych do porównania. Pusty {} to NIE
                # "znane": tak zapisywał się do 16.09.2026 odrzut sita, również
                # cudzego wyszukiwania, więc nie świadczy o żadnym sprawdzeniu.
                continue

            # DOPIERO TERAZ strona ogłoszenia — dla nowych, po odsianiu znanych,
            # żeby jedno żądanie przypadało na kandydata, nie na cały rynek.
            # Stąd biorą się nadwozie i napęd, których wyszukiwarka nie oddaje.
            uzupelnij_ze_strony(listing)
            pasuje, braki = sprawdz_kryteria(listing, search["kryteria"])
            if not pasuje:
                log.info(f"Odrzucone po sprawdzeniu strony "
                         f"(nadwozie={listing.get('body')}, napęd={listing.get('drive')}): "
                         f"{listing['title'][:45]}")
                # Zapamiętane, bo powtórka kosztowałaby zapytanie co bieg. Z powodem,
                # bo po zmianie kryteriów te wpisy trzeba zdjąć (reguła 1).
                seen[lid] = {"powod": "strona", "nadwozie": listing.get("body"),
                             "naped": listing.get("drive")}
                continue
            if listing.get("szkoda_nieopisana"):
                braki.append("zakres szkody (Otomoto oznaczyło jako uszkodzone)")
            listing["braki"] = braki
            wycena = wycena_sprawnego(OLX_SEARCHES[SEARCHES.index(search)], listing)
            msg = tekst_oferty("Otomoto", listing, search["name"], wycena, price_drop_str)
            wpis = {"title": listing["title"], "price_num": listing["price_num"],
                    "mileage_num": listing["mileage_num"], "year": listing.get("year"),
                    "url": listing["url"], "search": search["name"], "date": today,
                    "sprawne_mediana": wycena.get("mediana"), "sprawne_n": wycena.get("n")}
            if wyslij_oferte(seen, lid, wpis, msg, listing.get("zdjecie"), save_seen):
                wyslane += 1
            log.info(f"Nowe ogłoszenie: {listing['title']}")
            new_count += 1

    # -----------------------------------------------------------------------
    # OLX
    # -----------------------------------------------------------------------
    # Ten sam samochód wystawiony na Otomoto i przelany na OLX to dwie
    # wiadomości o jednym aucie — a lustrem jest 47 z 51 ofert OLX-a.
    # Kanały mają osobne pliki `seen`, więc dopiero tu da się je zestawić.
    # Pomijamy TYLKO te, których pierwowzór faktycznie znamy z Otomoto;
    # gdy oryginał nie wpadł w nasze wyszukiwania, OLX jest jedynym źródłem.
    znane_otomoto = {otomoto_id_z_url(v.get("url"))
                     for v in seen.values() if isinstance(v, dict) and v.get("url")}
    znane_otomoto.discard(None)

    pasujace_polska, pasujace_region = set(), set()
    for search in OLX_SEARCHES:
        listings = fetch_listings_olx(search)

        for listing in listings:
            lid = listing["id"]
            pasujace_polska.add(lid)

            # Filtr regionu, bez wpisu, z tego samego powodu co przy Otomoto
            if not in_allowed_region(listing.get("region", "")):
                continue
            pasujace_region.add(lid)
            kandydaci.append((seen_olx, lid, listing["url"], listing["title"]))

            lustro = otomoto_id_z_url(listing.get("external_url"))
            if lustro and lustro in znane_otomoto:
                log.info(f"Pominięto (lustro oferty z Otomoto): {listing['title'][:45]}")
                seen_olx[lid] = {"powod": "lustro", "otomoto": lustro}
                continue

            # Wykrywanie obniżki ceny
            prev_olx = seen_olx.get(lid)
            price_drop_str = ""
            if prev_olx and isinstance(prev_olx, dict) and prev_olx.get("price_num") and listing["price_num"]:
                drop = prev_olx["price_num"] - listing["price_num"]
                if drop >= 500:
                    price_drop_str = f"\n📉 <b>OBNIŻKA o {drop:,} zł!</b>".replace(",", " ")
                    seen_olx[lid]["price_num"] = listing["price_num"]
                else:
                    seen_olx[lid]["price_num"] = listing["price_num"]
                    continue
            elif seen_olx.get(lid):
                continue      # pusty {} to nie "znane", patrz pętla Otomoto

            wycena = wycena_sprawnego(search, listing)
            msg = tekst_oferty("OLX", listing, search["name"], wycena, price_drop_str)
            wpis = {"title": listing["title"], "price_num": listing["price_num"],
                    "mileage_num": listing["mileage_num"], "year": listing.get("year"),
                    "url": listing["url"], "search": search["name"], "date": today,
                    "sprawne_mediana": wycena.get("mediana"), "sprawne_n": wycena.get("n")}
            if wyslij_oferte(seen_olx, lid, wpis, msg, listing.get("zdjecie"), save_seen_olx):
                wyslane += 1
            log.info(f"OLX nowe: {listing['title']}")
            new_count += 1

    # -----------------------------------------------------------------------
    # Obserwowani wystawcy — niezależne od kryteriów modelowych powyżej.
    # W try, bo to dodatek: jego awaria nie może zabrać głównego przebiegu
    # ani zablokować zapisu plików `seen`.
    # -----------------------------------------------------------------------
    odczyty = {"proby": 0, "udane": 0}
    try:
        for wystawca in WYSTAWCY:
            n = sprawdz_wystawce(wystawca, seen_wystawcy, odczyty)
            new_count += n
            wyslane += n
    except Exception as e:
        log.error(f"Obserwacja wystawców przerwana: {e}")
    save_seen_wystawcy(seen_wystawcy)

    bez_wyniku = [k for k in kandydaci if not k[0].get(k[1])]
    if bez_wyniku:
        obsluz_bez_wyniku(bez_wyniku, today)

    if new_count == 0:
        log.info("Brak nowych ogłoszeń.")

    save_seen(seen)
    save_seen_olx(seen_olx)
    ocen_zdrowie(pobrano_otomoto, _bieg["olx_ok"])
    ocen_obserwacje(odczyty["proby"], odczyty["udane"])
    ocen_dzien({"otomoto_ok": pobrano_otomoto > 0,
                "olx_ok": _bieg["olx_ok"] > 0 and not _bieg["olx_bledy"],
                "wyslane": wyslane,
                "pasujace_polska": len(pasujace_polska),
                "pasujace_region": len(pasujace_region),
                "problemy": list(_bieg["problemy"])})


if __name__ == "__main__":
    try:
        main()
    except Exception as blad:
        log.exception("Bieg się wywrócił")
        zglos_wywrotke(blad)
        raise
