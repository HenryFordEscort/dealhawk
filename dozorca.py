#!/usr/bin/env python3
"""Dozorca OLX — obserwuje polski rynek i zapisuje SUROWE FAKTY.

ZASADA NACZELNA: zapisujemy to, co widzieliśmy, a nie to, co z tego wynika.
Dziennik mówi "oferta X była widoczna 21.08 o 14:00 za 12 900 zł" — nigdy
"ten rower sprzedał się w 5 dni". Wnioski (sprzedaż vs wznowienie, czas
sprzedaży, krzywe przeżycia) liczy się osobno z tego dziennika i można je
przeliczyć od zera, gdy reguły okażą się błędne.

Poprzednia katastrofa (fałszywe "100% sprzedaje się w 2 dni") wzięła się
dokładnie stąd, że zapisywaliśmy wnioski — i trzeba było skasować dane.

Pliki:
  zdarzenia/olx-RRRR-MM.jsonl  — dziennik zdarzeń, append-only, NIGDY nie kasowany
  olx_stan.json                — bieżący stan (kto żyje); ODTWARZALNY z dziennika

Uruchom: python3 dozorca.py [ile_modeli]
"""
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path



import tracker

STAN_FILE = Path("olx_stan.json")
ZDROWIE_FILE = Path("dozorca_zdrowie.json")
ZDARZENIA_DIR = Path("zdarzenia")

# Oferta potrafi wypaść z okna wyników bez powodu (ranking, stronicowanie),
# więc pojedyncze zniknięcie nic nie znaczy. Dopiero tyle przebiegów z rzędu
# bez niej każe sprawdzić stronę oferty.
BRAKI_DO_SPRAWDZENIA = 3
MAX_SPRAWDZEN_NA_PRZEBIEG = 40      # limit pobrań stron ofert (grzeczność + czas)
MAX_FAKTOW_NA_PRZEBIEG = 40         # limit pobrań po fakty ze strony oferty
PROB_FAKTOW = 3                     # tyle nieudanych prób i odpuszczamy ofertę
ODSWIEZ_PRZED_H = 72                # na tyle przed wygaśnięciem pytamy ponownie

# Ile godzin może minąć między zdjęciem oferty a naszym zauważeniem tego.
# WYLICZONE, nie wzięte z sufitu: dozorca chodzi co ~2 h (zmierzone na 35
# przebiegach 21-24.08.2026: mediana 1,9 h), a zniknięcie potwierdzamy dopiero
# po BRAKI_DO_SPRAWDZENIA nieobecnościach z rzędu. 3 × 2 h + zapas = 8 h.
LUZ_WYKRYCIA_H = 8


def teraz_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M")


def odcisk(tytul: str, cena, loc: str) -> str:
    """Odcisk palca oferty — do rozpoznania, że sprzedawca skasował ogłoszenie
    i wystawił je od nowa (wznowienie), zamiast sprzedać rower.
    Tytuł sprowadzony do samych liter i cyfr, żeby drobne zmiany nie myliły."""
    t = re.sub(r'[^a-z0-9ąćęłńóśźż]+', '', (tytul or "").lower())[:60]
    return f"{t}|{cena}|{(loc or '').lower()}"


def wykryj_zdarzenia(stan: dict, biezace: dict, zapytania_ok: set, teraz: str):
    """SERCE DOZORCY — czysta funkcja, bez sieci i bez plików (testowalna).

    stan:         {id_oferty: rekord} — co wiedzieliśmy do tej pory
    biezace:      {id_oferty: {...}} — co widać w tym przebiegu
    zapytania_ok: zapytania, które udało się pobrać. Oferty z zapytań, które
                  padły, NIE mogą być uznane za zaginione — inaczej awaria
                  sieci wyglądałaby jak masowa wyprzedaż.

    Zwraca (lista_zdarzeń, nowy_stan)."""
    zdarzenia = []
    stan = {k: dict(v) for k, v in stan.items()}      # nie modyfikujemy wejścia

    for oid, ob in biezace.items():
        stary = stan.get(oid)
        if stary is None:
            rec = {"url": ob["url"], "p": ob["p"], "p0": ob["p"],
                   "tytul": ob.get("tytul"), "loc": ob.get("loc"),
                   "q": ob.get("q"), "pierwszy": teraz, "ostatni": teraz,
                   "odcisk": odcisk(ob.get("tytul"), ob["p"], ob.get("loc"))}
            stan[oid] = rec
            zdarzenia.append({"ts": teraz, "ev": "nowa", "id": oid, "p": ob["p"],
                              "q": ob.get("q"), "tytul": ob.get("tytul"),
                              "loc": ob.get("loc"), "odcisk": rec["odcisk"],
                              "url": ob["url"]})
            continue

        if stary.pop("braki", 0):                     # była zaginiona, wróciła
            zdarzenia.append({"ts": teraz, "ev": "wrocila", "id": oid})
        if ob["p"] != stary.get("p"):
            zdarzenia.append({"ts": teraz, "ev": "cena", "id": oid,
                              "p": ob["p"], "p_stara": stary.get("p")})
            stary["p"] = ob["p"]
        stary["ostatni"] = teraz
        stan[oid] = stary

    # nieobecne w tym przebiegu — tylko z zapytań, które faktycznie się pobrały
    for oid, rec in stan.items():
        if oid in biezace or rec.get("q") not in zapytania_ok:
            continue
        rec["braki"] = rec.get("braki", 0) + 1
    return zdarzenia, stan


def _do_odswiezenia(rec: dict, teraz: str) -> bool:
    """Czy odpytać ofertę, o której już coś wiemy?

    Tylko wtedy, gdy zbliża się jej data ważności. Sprzedawca odświeża
    ogłoszenie i OLX PRZESUWA mu tę datę — a wtedy zejście oferty wypadłoby
    po naszej starej dacie i policzyłoby się jako wygaśnięcie, choć rower
    mógł się właśnie sprzedać. Najwyżej raz dziennie na ofertę, więc przez
    całe jej życie to kilka dodatkowych pobrań."""
    w, t = _czas(rec.get("wazne_do")), _czas(teraz)
    if w is None or t is None:
        return False
    if w - t > timedelta(hours=ODSWIEZ_PRZED_H):
        return False                       # do wygaśnięcia daleko
    return (rec.get("fakty_ts") or "")[:10] != teraz[:10]


def bez_faktow(stan: dict, limit: int = MAX_FAKTOW_NA_PRZEBIEG, teraz: str = ""):
    """Oferty do odpytania o fakty ze strony (data wystawienia, data
    wygaśnięcia, konto sprzedawcy). Najpierw te, o których nie wiemy nic,
    i najświeższe — bo to one najczęściej znikają, a fakty trzeba złapać
    ZANIM znikną: martwa strona OLX niesie samo {"statusCode": 410}
    i nic więcej (zmierzone 24.08.2026)."""
    nowe = [(oid, r) for oid, r in stan.items()
            if not r.get("fakty_ts") and r.get("fakty_prob", 0) < PROB_FAKTOW]
    nowe.sort(key=lambda x: x[1].get("pierwszy") or "", reverse=True)
    konczace = []
    if teraz:
        konczace = [(oid, r) for oid, r in stan.items()
                    if r.get("fakty_ts") and _do_odswiezenia(r, teraz)]
        konczace.sort(key=lambda x: x[1].get("wazne_do") or "")
    return (nowe + konczace)[:limit]


def _czas(s):
    """Znacznik czasu z dziennika ("2026-08-24T13:12") albo z OLX-a
    ("2026-09-16T15:48:13+02:00") → aware datetime w UTC. None gdy nie da się."""
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s)
    except Exception:
        try:
            d = datetime.strptime(s, "%Y-%m-%dT%H:%M")
        except Exception:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)      # dziennik pisze UTC
    return d.astimezone(timezone.utc)


def powod_zniknienia(wazne_do, ts_znikniecia, luz_h: int = LUZ_WYKRYCIA_H):
    """WNIOSEK z dziennika, nie zapis w nim: czemu oferta zeszła z OLX-a.

    "zdjeta"  — sprzedawca ją zdjął, zanim OLX zdążył ją wygasić. To jedyny
                przypadek, w którym rower MÓGŁ się sprzedać.
    "wygasla" — dożyła swojej daty ważności. Nikt jej nie kupił.
    None      — nie wiadomo (brak daty ważności albo zniknięcie wypadło
                w oknie niepewności naszego własnego wykrywania).

    Bez tego rozdzielenia każde wygaśnięcie liczyło się jako sprzedaż, czyli
    dokładnie w stronę, w którą wycena już raz pomyliła się czterokrotnie."""
    w, t = _czas(wazne_do), _czas(ts_znikniecia)
    if w is None or t is None:
        return None
    luz = timedelta(hours=luz_h)
    if t < w:
        return "zdjeta"        # zeszła, choć miała jeszcze ważność — pewne
    if t - luz > w:
        return "wygasla"       # nawet z naszym opóźnieniem było już po dacie
    return None                # zniknięcie w oknie niepewności — nie zgadujemy


# Fakty przeniesione do wpisu o zniknięciu. Bez nich dziennik pamięta tylko
# tyle, że oferta zeszła — a martwa strona OLX nie odda już ani daty
# wystawienia, ani ważności, ani sprzedawcy.
FAKTY_DO_DZIENNIKA = ("wystawiono", "wazne_do", "odswiezono", "sprzedawca",
                      "firma", "miasto", "wojewodztwo", "zdjecia")


def zdarzenie_znikla(oid: str, rec: dict, teraz: str) -> dict:
    """Wpis do dziennika o zejściu oferty — SAME FAKTY, żadnego wniosku.

    Nie ma tu słowa "sprzedana" ani "wygasła": to wniosek, liczy go
    powod_zniknienia() osobno, żeby dało się przeliczyć od zera, gdy reguła
    okaże się zła. `dni` mówi, ile oferta była WIDZIANA PRZEZ NAS, a
    `wystawiono` — od kiedy naprawdę wisiała. To dwie różne liczby i mylenie
    ich dawało "wszystko schodzi w 2 dni" po dwóch dniach zbierania."""
    z = {"ts": teraz, "ev": "znikla", "id": oid,
         "p": rec.get("p"), "p0": rec.get("p0"),
         "q": rec.get("q"), "odcisk": rec.get("odcisk"),
         "dni": dni_zycia(rec, teraz)}
    for k in FAKTY_DO_DZIENNIKA:
        if rec.get(k) is not None:
            z[k] = rec[k]
    return z


def do_sprawdzenia(stan: dict, limit: int = MAX_SPRAWDZEN_NA_PRZEBIEG):
    """Oferty nieobecne dostatecznie długo, by sprawdzić ich stronę.
    Najpierw te najdłużej zaginione."""
    kand = [(oid, r) for oid, r in stan.items()
            if r.get("braki", 0) >= BRAKI_DO_SPRAWDZENIA]
    kand.sort(key=lambda x: -x[1].get("braki", 0))
    return kand[:limit]


def dni_zycia(rec, teraz: str):
    """Ile dni oferta była widoczna. None gdy daty nie da się odczytać."""
    try:
        a = datetime.strptime(rec["pierwszy"], "%Y-%m-%dT%H:%M")
        b = datetime.strptime(teraz, "%Y-%m-%dT%H:%M")
        return max(0, (b - a).days)
    except Exception:
        return None


def zapisz_zdarzenia(zdarzenia):
    """Dopisuje do miesięcznego pliku. Rotacja miesięczna trzyma pliki małe,
    ale NIC nie jest kasowane — dziennik rośnie w nieskończoność."""
    if not zdarzenia:
        return
    ZDARZENIA_DIR.mkdir(exist_ok=True)
    plik = ZDARZENIA_DIR / f"olx-{datetime.now(timezone.utc):%Y-%m}.jsonl"
    with plik.open("a", encoding="utf-8") as f:
        for z in zdarzenia:
            f.write(json.dumps(z, ensure_ascii=False) + "\n")


def wczytaj_stan() -> dict:
    try:
        return json.loads(STAN_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def modele(limit):
    try:
        w = json.loads(Path("olx_watch.json").read_text())
        q = sorted(w, key=lambda k: -len((w[k] or {}).get("offers", {})))
    except Exception:
        q = []
    q = [x for x in q if x and not x[0].isupper()]
    return q[:limit] or ["cube stereo hybrid", "trek rail", "specialized turbo levo"]


def czujka_faktow(prob: int, udane: int) -> None:
    """Reguła 7: parsujesz cudzy HTML → dołóż czujkę na ciszę.
    Dziesięć prób i ZERO faktów to nie pech, tylko zmieniony układ strony OLX
    albo blokada. Bez tego dozorca chodziłby dalej, zapisywał puste rekordy
    i wyglądał zdrowo. Alarm najwyżej raz dziennie — awaria trwa godzinami,
    a dozorca chodzi co 2 h."""
    if prob < 10 or udane:
        return
    try:
        dzis = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        try:
            zdrowie = json.loads(ZDROWIE_FILE.read_text(encoding="utf-8"))
        except Exception:
            zdrowie = {}
        if zdrowie.get("alarm_fakty") == dzis:
            return
        # Osobny plik, nie parser_health.json: ten drugi zapisuje też tracker
        # (co 5 min) i podsumowanie, a każdy workflow commituje swoje — wspólny
        # plik kończy się konfliktem przy `git pull --rebase`.
        zdrowie["alarm_fakty"] = dzis
        ZDROWIE_FILE.write_text(json.dumps(zdrowie), encoding="utf-8")
        tracker.send_telegram(
            "🔕 <b>Dozorca nie czyta już stron ofert.</b>\n"
            f"Sprawdził {prob} ogłoszeń i z żadnego nie wyciągnął daty "
            "wystawienia ani sprzedawcy. Zwykle znaczy to, że OLX przebudował "
            "stronę. Zbieranie ofert działa dalej — ale wiek ogłoszeń "
            "przestaje się zapisywać.")
    except Exception as e:
        print(f"  czujka faktów nie zadziałała: {type(e).__name__}")


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    teraz = teraz_utc()
    stan = wczytaj_stan()
    biezace, zapytania_ok = {}, set()

    tracker.olx_diag_reset()
    for q in modele(limit):
        url = "https://www.olx.pl/sport-hobby/rowery/q-" + q.replace(" ", "-") + "/"
        r = tracker.olx_get(url, timeout=25)
        if r is None or r.status_code != 200:
            print(f"  [{q}] brak odpowiedzi (status {getattr(r, 'status_code', '-')})")
            continue                       # NIE dodajemy do zapytania_ok
        karty = tracker.parse_olx_cards(r.text)
        if not karty:
            print(f"  [{q}] 0 kart — pomijam (nie uznaje za wymarcie)")
            continue
        trafne = tracker.olx_relevant_offers(q, {c["url"]: c["price"] for c in karty})
        zapytania_ok.add(q)
        for c in karty:
            if c["url"] not in trafne:
                continue
            oid = c.get("id") or c["url"]
            biezace[oid] = {"url": c["url"], "p": c["price"], "q": q,
                            "tytul": c.get("title"), "loc": c.get("loc")}
        print(f"  [{q}] {len(trafne)} ofert")
        time.sleep(0.4)

    # NIC nie zebrane = blokada albo zmiana OLX, nigdy "rynek wymarł".
    # Wychodzimy BEZ zapisu stanu — inaczej jutro wszystko wyglądałoby na nowe.
    if not zapytania_ok:
        tracker.alarm_olx_martwy("Dozorca: żaden model nie zwrócił ofert.",
                                 f"Obserwowanych ofert w stanie: {len(stan)}")
        return

    zdarzenia, stan = wykryj_zdarzenia(stan, biezace, zapytania_ok, teraz)

    # FAKTY ZE STRONY OFERTY — pobierane, DOPÓKI oferta żyje.
    # Martwa strona OLX to samo {"statusCode": 410}: ani daty wystawienia, ani
    # daty ważności, ani sprzedawcy (zmierzone 24.08.2026). Kto tego nie zbierze
    # za życia, ten po zniknięciu wie tylko tyle, że zniknęło.
    prob, udane = 0, 0
    for oid, rec in bez_faktow(stan, teraz=teraz):
        prob += 1
        fakty = tracker.olx_offer_facts(rec["url"])
        if fakty:
            udane += 1
            rec.update(fakty)
            rec["fakty_ts"] = teraz
            zdarzenia.append(dict({"ts": teraz, "ev": "fakty", "id": oid}, **fakty))
        else:
            rec["fakty_prob"] = rec.get("fakty_prob", 0) + 1
        time.sleep(0.3)
    if prob:
        print(f"  fakty: {udane}/{prob}")
    czujka_faktow(prob, udane)

    # potwierdzenie śmierci — wymaga POZYTYWNEGO dowodu (404/status nieaktywny).
    # Samo wypadniecie z wyników nigdy nie liczy sie jako zniknięcie.
    for oid, rec in do_sprawdzenia(stan):
        wynik = tracker.olx_offer_state(rec["url"])
        gone = wynik["gone"]
        if gone is True:
            zdarzenia.append(zdarzenie_znikla(oid, rec, teraz))
            stan.pop(oid, None)
        elif gone is False:
            rec["braki"] = 0               # żyje, tylko wypadła z okna wyników
            # Skoro i tak pobraliśmy stronę: odśwież fakty. Sprzedawca przesuwa
            # `wazne_do` odświeżeniem ogłoszenia, a na starej dacie wygaśnięcie
            # wyglądałoby jak zdjęcie oferty.
            if wynik["fakty"]:
                rec.update(wynik["fakty"])
                rec["fakty_ts"] = teraz
        time.sleep(0.3)

    zapisz_zdarzenia(zdarzenia)
    STAN_FILE.write_text(json.dumps(stan, ensure_ascii=False), encoding="utf-8")
    ile = {}
    for z in zdarzenia:
        ile[z["ev"]] = ile.get(z["ev"], 0) + 1
    print(f"zdarzenia: {ile or 'brak'} | obserwowanych ofert: {len(stan)}")


if __name__ == "__main__":
    main()
