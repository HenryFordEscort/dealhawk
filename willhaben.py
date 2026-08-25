#!/usr/bin/env python3
"""Willhaben.at — druga giełda zakupowa bota rowerowego, obok Kleinanzeigen.

Po co osobny plik: `tracker.py` ma 4600 linijek i całą logikę rowerową; drugi
serwis dołożony do środka zrobiłby z niego kolejny tysiąc. Wzorem `olx.py`
ten moduł zna WYŁĄCZNIE willhaben — nie wie nic o Boschu, marżach ani
Telegramie. Oddaje ogłoszenia w tym samym kształcie, co `tracker.fetch_listings`,
więc reszta potoku (filtry, wycena, powiadomienia) działa bez zmian.

CO ZMIERZONO 25.08.2026 — nie sprawdzać od nowa:

1. Cała lista siedzi w JSON-ie `__NEXT_DATA__`, nie w HTML-u. Tytuł, cena,
   CZAS WYSTAWIENIA co do sekundy, kod pocztowy, land, wszystkie zdjęcia
   i informacja, czy sprzedaje osoba prywatna. Zero regexpów po znacznikach
   — to jest ta sama lekcja, co reguła 8 z CLAUDE.md o OLX.
2. Lista jest DOMYŚLNIE posortowana po dacie (`published.descending`,
   `selected: true`). Nie trzeba o to prosić i nie ma trafności do obejścia.
3. Parametr `rows` w adresie działa i jest wart więcej niż cokolwiek innego:
   30 ogłoszeń = 43 min rynku, 100 = 2 h, 200 = 4,8 h. JEDNO żądanie zamiast
   dwunastu stron Kleinanzeigen. Godzinna przerwa w harmonogramie domyka się
   pojedynczym pobraniem, więc luki praktycznie przestają istnieć.
4. Przepustowość półek: e-bike ~41 ogłoszeń/h, MTB ~44/h.
5. Osiem żądań pod rząd, po 0,4-0,5 s — ani śladu dławienia i ani jednej
   strony bez dat. To NIE jest Kleinanzeigen; tamtejsza ostrożność (czyszczenie
   ciastek, brutalne cofanie tempa) zostaje, ale jako tania polisa, nie jako
   odpowiedź na zmierzoną karę.
6. Zdjęte ogłoszenie oddaje UCZCIWE HTTP 404 (i `page: "/404"` w JSON-ie).
   Odwrotnie niż Kleinanzeigen, gdzie zdjęte ogłoszenie to 200 ze stroną
   kategorii w środku — patrz komentarz przy `fetch_listing_details`.
7. PUŁAPKA: `BODY_DYN` z listy jest UCINANY na 256 znakach (125 z 200 ogłoszeń
   stało dokładnie na limicie). Wygląda jak pełny opis i nim nie jest. Wzięty
   za pełny dałby ciche „sprzedawca nie podał przebiegu" na rowerze, który ma
   przebieg w zdaniu drugim. Dlatego opis czytamy ZE STRONY OGŁOSZENIA, a
   `BODY_DYN` służy tylko za resztkę ratunkową, i to opisaną jako ucięta.
"""
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cloudscraper

try:
    from zoneinfo import ZoneInfo
    TZ_AT = ZoneInfo("Europe/Vienna")
except Exception:                       # brak bazy stref (goły obraz) — CEST
    TZ_AT = timezone(timedelta(hours=2))

log = logging.getLogger(__name__)

# --- PÓŁKI KATEGORII -------------------------------------------------------
# Dokładny odpowiednik dwóch półek Kleinanzeigen i z tego samego powodu:
# rubrykę wybiera sprzedawca i regularnie się myli. Na Kleinanzeigen kosztowało
# to Specialized Levo FSR wystawiony w „Mountainbikes" (złapany 34 min zamiast 2).
#
# UCZCIWIE: w oknie 200 ogłoszeń z 25.08.2026 półka MTB dała ZERO elektryków
# (przy 5 kandydatach z półki e-bike). Jedno okno to nie dowód, że nigdy nic
# nie da, ale też nie udawajmy, że coś już dała. Kosztuje jedno żądanie na skan
# — gdyby kiedyś przeszkadzała, wystarczy wykreślić ją z tej listy.
BAZA = "https://www.willhaben.at/iad/kaufen-und-verkaufen/marktplatz/fahrraeder"
POLKI = [
    {"typ": "wh-ebike", "kat": "e-bikes-4556", "nazwa": "willhaben e-bike"},
    # `szum`: jak "kanał MTB" po stronie Kleinanzeigen — w większości rowery
    # bez silnika, więc do dziennika rynku idą z niej tylko elektryki.
    {"typ": "wh-mtb", "kat": "mountainbikes-4559", "nazwa": "willhaben MTB",
     "szum": True},
]

# Prefiks identyfikatora. KONIECZNY, nie kosmetyczny: willhaben numeruje
# ogłoszenia tak samo jak Kleinanzeigen (9-cyfrowe liczby), a obie giełdy
# trafiają do JEDNEGO `seen.json`. Bez prefiksu kolizja numerów uciszyłaby
# rower — bot uznałby go za widzianego i nigdy o nim nie powiedział.
PREFIKS = "wh-"

# ŻADNYCH FILTRÓW W ADRESIE — cena i reszta kryteriów sprawdzane w kodzie,
# tak samo jak w kanale Kleinanzeigen i w bocie samochodowym. Rower za 3200 €
# ma być ZOBACZONY i zapamiętany, żeby po przecenie do 2400 € dało się go
# rozpoznać jako okazję, a nie jako nowe ogłoszenie.
ROWS_BAZA = 50             # ~70 min rynku jednym żądaniem — zapas na przerwę
ROWS_MAX = 200             # ~4,8 h; więcej i tak nie było potrzebne
OGLOSZEN_NA_H = 45         # zmierzone 25.08: e-bike 41/h, MTB 44/h — bierzemy górę
STRON_MAX = 4              # 4 × 200 ogłoszeń ≈ 18 h; dalej luki nie domykamy
MARGINES_MIN = 3           # ile cofnąć się za znacznik, na styk zegarów
LUKA_MAX_MIN = 16 * 60     # starszy znacznik = okno przepadło, bierzemy stronę 1

scraper = cloudscraper.create_scraper()
# austriacka wersja serwisu niezależnie od tego, gdzie stoi runner
scraper.headers.update({"Accept-Language": "de-AT,de;q=0.9"})

NEXT_DATA = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json"[^>]*>(.*?)</script>', re.S)

REPLAY_DIR = None          # ustawiane z testów: odtwarzanie zapisanego HTML


def czy_nasze(url: str) -> bool:
    """Czy ten adres należy do willhaben? Po tym rozpoznaje go `tracker`."""
    return "willhaben.at" in (url or "")


def _atrybuty(ad) -> dict:
    """Płaska mapa atrybutów ogłoszenia. Wielowartościowe zostają listą."""
    out = {}
    for a in ((ad.get("attributes") or {}).get("attribute") or []):
        v = a.get("values") or []
        out[a.get("name")] = v[0] if len(v) == 1 else v
    return out


def _czas(raw):
    """Napis ISO Z PRAWDZIWYM offsetem → datetime w strefie austriackiej.

    Do pól, którym wolno wierzyć: `publishedDate` ze strony ogłoszenia niesie
    jawne `+0200`. Do pól z listy służy `_czas_wystawienia` niżej — i ma to
    swój bardzo konkretny powód."""
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ_AT)
    return dt.astimezone(TZ_AT)


def _czas_wystawienia(a):
    """Czas wystawienia ogłoszenia z listy. To jest ZEGAR CAŁEGO KANAŁU.

    PUŁAPKA ZMIERZONA 25.08.2026 — najdroższa rzecz w tym pliku.
    Pole `PUBLISHED_String` kończy się literą Z, czyli deklaruje UTC, a niesie
    czas WIEDEŃSKI. Udowodnione dwiema niezależnymi drogami:

      · liczbowe `PUBLISHED` = 1787653572973 ms = 10:26:12 UTC = 12:26:12
        w Wiedniu, a `PUBLISHED_String` tego samego ogłoszenia mówi „12:26:12Z";
      · to samo ogłoszenie na własnej stronie ma `publishedDate` =
        „2026-08-25T12:23:17+0200", a na liście „2026-08-25T12:23:17Z" —
        ta sama godzina na zegarze, dwa sprzeczne offsety.

    Wzięte za UTC dodaje ogłoszeniom DWIE GODZINY W PRZÓD. Skutki, wszystkie
    ciche: każdy rower miałby wiek ujemny (czyli „przed chwilą", nawet gdyby
    wisiał pół dnia), alarm o spóźnieniu bota nie odpaliłby się nigdy,
    a znacznik półki stanąłby w przyszłości i luka po przerwie nigdy by się
    nie domknęła — bo żadne ogłoszenie nie jest starsze od jutra.

    Dlatego pierwszeństwo ma pole LICZBOWE (epoch w milisekundach), które nie
    ma jak skłamać o strefie. Napis jest tylko zapasem i czytamy go jako czas
    miejscowy — nigdy jako UTC."""
    ms = a.get("PUBLISHED") or a.get("CHANGED")
    try:
        if ms:
            return datetime.fromtimestamp(int(ms) / 1000, TZ_AT)
    except (TypeError, ValueError, OSError, OverflowError):
        pass
    raw = a.get("PUBLISHED_String")
    if not raw:
        return None
    try:
        # świadomie ucinamy fałszywe „Z" — patrz wyżej
        dt = datetime.fromisoformat(str(raw).rstrip("Z"))
    except (TypeError, ValueError):
        return None
    return dt.replace(tzinfo=TZ_AT) if dt.tzinfo is None else dt.astimezone(TZ_AT)


def _cena(a):
    """(napis do wiadomości, liczba). Brak ceny to fakt, nie zero.

    Ogłoszenia „Preis auf Anfrage" i „zu verschenken" mają PRICE = 0.
    Zero przepuszczone dalej udawałoby rower za darmo i wywracało każdą
    marżę; None znaczy „nie wiemy" i tak też jest traktowane w potoku."""
    try:
        n = int(float(a.get("PRICE") or a.get("PRICE/AMOUNT") or 0))
    except (TypeError, ValueError):
        return "brak ceny", None
    if n <= 0:
        return "brak ceny", None
    return f"{n} €", n


def _zdjecia(a, ad) -> list:
    """Adresy zdjęć w pełnym rozmiarze.

    Bierzemy wariant `referenceImageUrl` (~185 kB), nie `mainImageUrl` (~28 kB)
    — z tego samego powodu, dla którego przy Kleinanzeigen wymuszamy 960x720:
    Telegram pokazuje duże zdjęcie DUŻO większe, a to pierwsza rzecz,
    którą widać na telefonie."""
    adresy = []
    for img in ((ad.get("advertImageList") or {}).get("advertImage") or []):
        u = img.get("referenceImageUrl") or img.get("mainImageUrl")
        if u and u not in adresy:
            adresy.append(u)
    if adresy:
        return adresy
    # zapas: lista podaje same referencje plików, rozdzielone średnikiem
    for ref in str(a.get("ALL_IMAGE_URLS") or "").split(";"):
        ref = ref.strip()
        if ref:
            u = f"https://cache.willhaben.at/mmo/{ref}"
            if u not in adresy:
                adresy.append(u)
    return adresy


# --- REGION Z KODU POCZTOWEGO ----------------------------------------------
# Austriacki kod ma CZTERY cyfry, niemiecki pięć — ta sama funkcja dla obu
# czytałaby „5071 Siezenheim" jako niemieckie „50xx" (Nadrenia). Kraj bierzemy
# więc z adresu ogłoszenia, nie z kształtu kodu.
#
# Land i tak jest w JSON-ie (atrybut STATE), więc kod pocztowy służy tu tylko
# za zapas. Zaznaczamy to, co zmienia decyzję o dojeździe: Wiedeń i wschód
# kraju są od Polski BLIŻEJ niż zachodnie Niemcy, a Tyrol i Vorarlberg dużo
# dalej niż cokolwiek, po co bot dziś jeździ.
_REGIONY_AT = [
    (10, 19, "Wiedeń (najbliżej z Austrii)"),
    (20, 25, "Dolna Austria (blisko)"), (26, 29, "Dolna Austria (blisko)"),
    (30, 39, "Dolna Austria"), (40, 46, "Górna Austria"),
    (47, 49, "Górna Austria"), (50, 56, "Salzburg"), (57, 57, "Salzburg"),
    (58, 59, "Salzburg"), (60, 66, "Tyrol (daleko)"),
    (67, 69, "Vorarlberg (najdalej)"), (70, 76, "Burgenland (blisko)"),
    (80, 89, "Styria"), (90, 96, "Karyntia"), (97, 99, "Tyrol Wschodni (daleko)"),
]


def region_z_plz(loc: str):
    """Land z austriackiego kodu pocztowego. None, gdy kodu nie ma."""
    m = re.match(r'\s*(\d{2})', loc or "")
    if not m:
        return None
    n = int(m.group(1))
    for lo, hi, nazwa in _REGIONY_AT:
        if lo <= n <= hi:
            return nazwa
    return None


def _ogloszenie(ad) -> dict:
    """Jedno ogłoszenie z JSON-a → kształt, którego oczekuje `tracker`."""
    a = _atrybuty(ad)
    ad_id = str(a.get("ADID") or ad.get("id") or "").strip()
    if not ad_id:
        return None
    cena_txt, cena_num = _cena(a)
    plz, miasto = a.get("POSTCODE") or "", a.get("LOCATION") or ""
    loc = " ".join(x for x in (plz, miasto) if x) or None
    land = a.get("STATE")
    zdj = _zdjecia(a, ad)
    posted = _czas_wystawienia(a)
    seo = a.get("SEO_URL") or ""
    return {
        "id": PREFIKS + ad_id,
        "title": (a.get("HEADING") or "").strip() or "Brak tytułu",
        "price": cena_txt,
        "price_num": cena_num,
        "loc": loc,
        "region": region_z_plz(loc) or land,
        "foto": zdj[0] if zdj else None,
        "zdjecia": zdj,          # cała galeria JUŻ z listy — bez pobierania strony
        "posted": posted,
        # Wiek zostawiamy pusty ŚWIADOMIE: liczy go `tracker` zaraz po
        # odebraniu półki, jednym zegarem dla obu giełd. Patrz komentarz
        # przy pętli półek — od tej liczby zależy kolejność powiadomień,
        # alarm o spóźnieniu i pomiar zwłoki w dzienniku rynku.
        "age_min": None,
        "url": f"https://www.willhaben.at/iad/{seo}" if seo
               else f"https://www.willhaben.at/iad/kaufen-und-verkaufen/d/-{ad_id}/",
        # Sprzedawca prywatny czy firma — willhaben mówi to WPROST, w polu
        # z formularza. Po polskiej stronie ta sama informacja kosztowała
        # regexp, który nie trafił ani razu (0 z 1427 wierszy, reguła 8).
        "prywatny": a.get("ISPRIVATE") == "1",
        # Opis z listy jest UCIĘTY na 256 znakach — patrz nagłówek pliku.
        # Trzymamy go tylko jako resztkę ratunkową i tak jest podpisany.
        "opis_uciety": a.get("BODY_DYN") or None,
        "serwis": "willhaben",
    }


def pobierz_strone(kat: str, rows: int = ROWS_BAZA, strona: int = 1, nazwa: str = ""):
    """Jedna strona półki. Zwraca (ogłoszenia, statystyki) — jak fetch_listings.

    Statystyki mają te same klucze co po stronie Kleinanzeigen, żeby czujki
    zdrowia parsera nie musiały wiedzieć, z którego serwisu przyszły. Różnica
    jest jedna i istotna: tam `title_hits` mierzy skuteczność regexpów, tu —
    czy JSON nadal ma pola, których szukamy. Zmiana nazwy pola po ich stronie
    ma zjechać tym samym alarmem, co zmiana HTML tam."""
    stats = {"name": nazwa or kat, "serwis": "willhaben", "blocks": 0,
             "title_hits": 0, "price_hits": 0, "time_hits": 0,
             "html": None, "status": None}
    wynik = []
    try:
        if REPLAY_DIR:                  # odtwarzanie zapisanego HTML (czarna skrzynka)
            fp = Path(REPLAY_DIR) / f"{kat}-{strona}.html"
            tekst = fp.read_text(encoding="utf-8") if fp.exists() else ""
            stats["status"] = 200
        else:
            # Ciastka czyścimy przed każdym żądaniem. Na willhaben nie
            # zmierzono, żeby to było konieczne (8/8 czystych odpowiedzi),
            # ale kosztuje zero, a na Kleinanzeigen brak tego zabierał
            # 22 z 23 odpowiedzi. Tania polisa.
            scraper.cookies.clear()
            url = f"{BAZA}/{kat}?rows={rows}" + (f"&page={strona}" if strona > 1 else "")
            r = scraper.get(url, timeout=25)
            stats["status"] = r.status_code
            r.raise_for_status()
            r.encoding = "utf-8"
            tekst = r.text

        m = NEXT_DATA.search(tekst)
        if not m:
            # Tak wygląda strona przejściowa / antybotowa: HTTP 200, właściwy
            # adres, a w środku nie ma czego parsować. Ratuje HTML do czarnej
            # skrzynki, żeby dało się zobaczyć, co przyszło zamiast listy.
            stats["html"] = tekst
            log.error(f"[{stats['name']}] odpowiedź bez __NEXT_DATA__ "
                      f"({len(tekst)} znaków) — to nie jest lista ogłoszeń")
            return [], stats

        sr = (json.loads(m.group(1)).get("props", {})
              .get("pageProps", {}).get("searchResult") or {})
        lista = (sr.get("advertSummaryList") or {}).get("advertSummary") or []
        for ad in lista:
            stats["blocks"] += 1
            l = _ogloszenie(ad)
            if not l:
                continue
            if l["title"] != "Brak tytułu":
                stats["title_hits"] += 1
            if l["price_num"] is not None:
                stats["price_hits"] += 1
            if l["posted"]:
                stats["time_hits"] += 1
            wynik.append(l)

        # Czarna skrzynka przy podejrzeniu przebudowy serwisu — ten sam próg
        # co po stronie Kleinanzeigen: połowa ogłoszeń bez tytułu albo bez ceny.
        if stats["blocks"] >= 10:
            udalo = min(stats["title_hits"], stats["price_hits"]) / stats["blocks"]
            if udalo < 0.5:
                stats["html"] = tekst
    except Exception as e:
        log.error(f"[{stats['name']}] błąd pobierania: {e}")
    return wynik, stats


def strona_zepsuta(stats) -> bool:
    """Czy tej odpowiedzi nie wolno uznać za obejrzany rynek?

    Trzy różne awarie, jedna odpowiedź: nie przesuwamy znacznika czasu.
    Zepsuty skan kosztuje wtedy jedno żądanie i nic więcej — następny
    przeczyta dokładnie to samo okno."""
    if stats.get("html") is not None and stats.get("blocks", 0) == 0:
        return True                      # odpowiedź bez JSON-a — antybot/awaria
    if stats.get("status") not in (200, None):
        return True                      # 4xx/5xx
    # Ogłoszenia są, ale ANI JEDNO nie ma czasu wystawienia. Po stronie
    # Kleinanzeigen to podpis podstawionej strony; tu znaczyłoby, że zniknęło
    # pole PUBLISHED_String — czyli przebudowa. Skutek ten sam: bez dat nie da
    # się cofać po półce, więc to nie jest wiedza o rynku.
    return stats.get("blocks", 0) >= 5 and stats.get("time_hits", 0) == 0


def _ile_rows(od, teraz=None) -> int:
    """Ile ogłoszeń zamówić, żeby JEDNYM żądaniem sięgnąć za znacznik.

    Tu jest cała przewaga willhaben nad Kleinanzeigen: zamiast chodzić po
    kolejnych stronach po 10 minut rynku każda, pytamy od razu o tyle, ile
    trzeba. Dwukrotny zapas, bo tempo ogłoszeń zmienia się z porą dnia —
    a niedobrana strona kosztuje drugie żądanie, czyli dokładnie to, przed
    czym się bronimy."""
    if od is None:
        return ROWS_BAZA
    luka_min = max(0.0, ((teraz or datetime.now(TZ_AT)) - od).total_seconds() / 60)
    trzeba = int(luka_min / 60 * OGLOSZEN_NA_H * 2) + 10
    return max(ROWS_BAZA, min(ROWS_MAX, trzeba))


def fetch_feed(od=None, kat="e-bikes-4556", nazwa=None, teraz=None):
    """Czyta jedną półkę wstecz, aż dojdzie do ogłoszeń starszych niż `od`.

    Zwraca (ogłoszenia, statystyki, dosiegl) — ten sam kontrakt co
    `tracker.fetch_feed`, żeby pętla główna nie musiała ich rozróżniać.
    `dosiegl=False` znaczy, że limit stron skończył się ZANIM domknęliśmy
    lukę — czyli część ogłoszeń przepadła i trzeba o tym krzyknąć."""
    nazwa = nazwa or f"willhaben {kat}"
    teraz = teraz or datetime.now(TZ_AT)
    wynik, widziane, stats_all = [], set(), []
    dosiegl = od is None                # pierwszy bieg: bierzemy tylko stronę 1
    zepsuty = False

    # Luka za duża, żeby ją domknąć? Nie zaczynaj — ta sama pułapka co po
    # stronie Kleinanzeigen: znacznik stoi, więc każdy następny skan znowu
    # przechodzi komplet stron, luka rośnie i spirala się zaciska.
    za_stary = od is not None and (teraz - od).total_seconds() / 60 > LUKA_MAX_MIN
    if za_stary:
        log.error(f"[{nazwa}] znacznik sprzed ponad {LUKA_MAX_MIN // 60} h — "
                  f"luki nie da się domknąć, biorę jedną stronę i ruszam znacznik")
        od = None
    prog = (od - timedelta(minutes=MARGINES_MIN)) if od else None
    rows = _ile_rows(od, teraz)

    for n in range(1, STRON_MAX + 1):
        karty, stats = pobierz_strone(kat, rows, n, f"{nazwa} s.{n}")
        stats_all.append(stats)
        if strona_zepsuta(stats):
            log.error(f"[{nazwa}] strona {n}: odpowiedź nie do przyjęcia — "
                      f"skan uznany za nieudany")
            zepsuty = True
            break
        if not karty:
            break
        nowe = [l for l in karty if l["id"] not in widziane]
        for l in nowe:
            widziane.add(l["id"])
        wynik.extend(nowe)
        if prog is None:
            break
        czasy = [l["posted"] for l in karty if l["posted"]]
        if czasy and min(czasy) < prog:
            dosiegl = True
            break
        if not czasy:
            log.warning(f"[{nazwa}] strona {n} bez dat — przerywam cofanie")
            break

    if zepsuty:
        wynik = []                       # nic z tego biegu nie jest wiarygodne
        dosiegl = False
    czasy = [l["posted"] for l in wynik if l["posted"]]
    stats = {"name": nazwa, "typ": kat, "serwis": "willhaben",
             "zepsuty": zepsuty,
             "blocks": sum(s["blocks"] for s in stats_all),
             "title_hits": sum(s["title_hits"] for s in stats_all),
             "price_hits": sum(s["price_hits"] for s in stats_all),
             "time_hits": sum(s["time_hits"] for s in stats_all),
             "html": next((s["html"] for s in stats_all if s["html"]), None),
             "status": stats_all[-1]["status"] if stats_all else None,
             "stron": len(stats_all),
             "rows": rows,
             "za_stary": za_stary,
             "najnowsze": max(czasy) if czasy else None}
    return wynik, stats, dosiegl


def szczegoly(url: str, proba: int = 1, prob_max: int = 3):
    """Strona ogłoszenia. Zwraca (opis, cena|None, zdjęcia, stan, meta).

    `stan` to jedyna uczciwa odpowiedź na pytanie „czy my to przeczytaliśmy":
        "ok"       — strona wczytana, opis mamy
        "usuniete" — ogłoszenia nie ma albo już nie jest aktywne
        "blad"     — NIE UDAŁO SIĘ; niczego o tym rowerze nie wiemy

    Rozróżnienie jest sednem — nieudany odczyt zapisany jako „sprzedawca nie
    podał przebiegu" przepuszcza złom, bo filtr zużycia przepuszcza brak
    danych. To ta sama umowa, co w `tracker.fetch_listing_details`.

    Zdjęte ogłoszenie rozpoznajemy TRZEMA drogami, bo serwis może wybrać
    dowolną: HTTP 404/410, `page: "/404"` w JSON-ie, albo status ogłoszenia
    inny niż `active` (wygasłe, sprzedane, zdjęte przez sprzedawcę)."""
    try:
        r = scraper.get(url, timeout=25)
        if r.status_code in (404, 410):
            return None, None, [], "usuniete", {}
        r.raise_for_status()
        r.encoding = "utf-8"
        m = NEXT_DATA.search(r.text)
        if not m:
            raise ValueError("strona ogłoszenia bez __NEXT_DATA__")
        dane = json.loads(m.group(1))
        if str(dane.get("page") or "").startswith("/404"):
            return None, None, [], "usuniete", {}
        ad = (dane.get("props", {}).get("pageProps", {}) or {}).get("advertDetails")
        if not ad:
            raise ValueError("strona bez ogłoszenia — to nie jest karta oferty")
        status = ((ad.get("advertStatus") or {}).get("id") or "").lower()
        if status and status != "active":
            log.info(f"willhaben: ogłoszenie nieaktywne ({status}): {url}")
            return None, None, [], "usuniete", {}

        a = _atrybuty(ad)
        opis = a.get("DESCRIPTION") or ""
        opis = re.sub(r'<br\s*/?>', "\n", opis)
        opis = re.sub(r'<[^>]+>', " ", opis)
        _, cena_num = _cena(a)
        zdj = _zdjecia(a, ad)
        meta = {
            # Willhaben nie ma plakietki „zarezerwowane" — i tego nie
            # udajemy. None znaczy „nie wiem" i tak jest wypisywane.
            "zarezerwowane": None,
            "prywatny": a.get("ISPRIVATE") == "1",
            "wystawione": _czas(ad.get("publishedDate")),
        }
        return opis, (f"{cena_num} €" if cena_num else None), zdj, "ok", meta
    except Exception as e:
        log.error(f"willhaben, odczyt ogłoszenia ({proba}/{prob_max}): {e}")
        if proba < prob_max:
            import time
            time.sleep(2 * proba)
            return szczegoly(url, proba + 1, prob_max)
    return None, None, [], "blad", {}
