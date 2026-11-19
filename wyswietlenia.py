#!/usr/bin/env python3
"""Wyswietlenia ogloszen OLX - pierwszy pomiar POPYTU w tym projekcie.

PO CO (16.11.2026). Wszystkie dotychczasowe instrumenty - spis rynku,
przeplyw polki, plynnosc, polka DE - patrza na PODAZ. Nawet "zdjete
ogloszenie" jest poszlaka po stronie sprzedajacego. Nie dalo sie odrozniac
dwoch zupelnie roznych diagnoz: "rower stoi, bo jest za drogi" (ludzie
wchodza i nie dzwonia) od "rower stoi, bo nikt go nie oglada" (zly tytul,
zle zdjecia, zla kategoria).

SKAD TA LICZBA. Strona oferty pokazuje publicznie licznik:
    <span data-testid="page-view-counter">Wyswietlenia: 329</span>
W HTML z serwera go NIE MA - dokleja go JavaScript po doscrollowaniu do
stopki. Dlatego wczesniej tego samego dnia zapisalem w repo blednie, ze OLX
nie pokazuje licznika nikomu poza wlascicielem ogloszenia; grep po zrodle
znajdowal same szablony tlumaczen. Podglad ruchu sieciowego przegladarki
pokazal jedno zapytanie:

    POST https://production-graphql.eu-sharedservices.olxcdn.com/graphql
    {"operationName":"PageViews","variables":{"adId":"1100315214"},
     "query":"query PageViews($adId: String!) { myAds { pageViews(adId: $adId)
              { pageViews } } }"}

Nazwa `myAds` myli: zapytanie dziala dla CUDZEGO ogloszenia i bez logowania,
bo dokladnie tak pyta strona otwarta przez anonimowego goscia.

SPRAWDZONE 16.11.2026 I ZAMKNIETE: TA DROGA NIE DZIALA. Endpoint odpowiada
HTTP 200, ale trescia:

    {"errors":[{"message":"Authorization header is missing",
                "path":["myAds"],"extensions":{"code":"UNAUTHENTICATED"}}]}

Tak samo przy golym zapytaniu, z `origin` i `referer`, z pelnym zestawem
naglowkow przegladarki i z naglowkami Apollo - cztery warianty, ta sama
odpowiedz. Przegladarka dostaje liczbe dlatego, ze aplikacja OLX-a dokłada
token, ktory ma nawet gosc niezalogowany.

DLACZEGO NIE SIEGAMY PO TEN TOKEN. Licznik na stronie jest publiczny, ale API
za nim juz nie - podszywanie sie pod klienta webowego serwisu to co innego niz
pobranie publicznej strony. Zostaje droga, ktora czyta te liczbe tak, jak
czyta ja czlowiek: wyrenderowanie strony oferty. To jest kosztowniejsze
(przegladarka zamiast jednego POST-a) i dlatego ma sens wylacznie na PROBCE,
czyli dokladnie tak, jak ten plik zaprojektowano.

STAN PLIKU: `czytaj_wyswietlenia` zostaje jako zapis tego, co zostalo
sprawdzone, i zawsze odda None. Caly pomiar (probka po pasmach, wiek, na dobe,
dziennik, raport) jest gotowy i czeka na czytnik, ktory faktycznie oddaje
liczbe - podaje sie go przez parametr `czytnik` w `zmierz()`.

CO Z TEGO LICZYMY. Sama liczba wyswietlen nic nie mowi, bo rosnie z wiekiem
ogloszenia. Miara jest WYSWIETLENIA NA DOBE ZYCIA, porownywalna miedzy
ofertami roznego wieku, i dopiero jej mediana w pasmie cenowym jest
odpowiedzia na pytanie "czy na ten przedzial ktos w ogole patrzy".
"""
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path

import requests

WYSWIETLENIA_FILE = Path("wyswietlenia.jsonl")
POLKA_STAN = Path("polka_stan.json")

GRAPHQL = "https://production-graphql.eu-sharedservices.olxcdn.com/graphql"
ZAPYTANIE = ("query PageViews($adId: String!) {\n  myAds {\n"
             "    pageViews(adId: $adId) {\n      pageViews\n    }\n  }\n}")

# Ile ofert odpytujemy na przebieg. 200 DZIENNIE, i ta liczba nie jest
# okragla przez przypadek: darmowy limit Cloudflare to 10 minut czasu
# przegladarki na dobe, a oferta czyta sie w okolo 3 sekundy, wiec 200 ofert
# to caly limit wykorzystany i ani sekundy ponad.
#
# Czemu dziennie, a nie raz w tygodniu: 1400 ofert tygodniowo zamiast 150,
# swieze ogloszenia lapane w ciagu doby od wystawienia (a nie po tygodniu,
# gdy najlepsze juz zeszly) i pelne okrazenie polki 8 tys.+ co okolo trzy
# tygodnie. Przyrosty licza sie tak samo dobrze, bo `przyrosty()` dzieli
# roznice przez liczbe dni miedzy odczytami.
PROBKA_NA_PRZEBIEG = 200

# TEMPO (19.11.2026). Pierwszy bieg przez Cloudflare: 5 zapytan, 3 x HTTP 429.
# Dokumentacja Browser Run podaje dla planu darmowego "Total requests
# (Quick Actions only): 1 every 10 seconds". Pol sekundy zapasu, bo limit
# liczy sie po stronie Cloudflare, a nasz zegar nie jest ich zegarem.
ODSTEP_S = 10.5

# HAMULEC. Darmowe 10 minut czasu przegladarki na dobe liczy Cloudflare, nie
# my; po przekroceniu kazde kolejne zapytanie to 429 az do polnocy UTC.
# Przerywamy sami przy 9 minutach, zeby ostatnie odczyty byly odczytami,
# a nie seria bledow w dzienniku.
BUDZET_MS = 9 * 60 * 1000

# Pasma z przeplywu, zeby obie miary mowily o tych samych polkach.
PASMA = [(8000, 9000), (9000, 10000), (10000, 12000), (12000, 14000),
         (14000, 16000), (16000, 20000), (20000, 25000), (25000, None)]


def _pasmo(cena):
    if not isinstance(cena, int):
        return None
    for lo, hi in PASMA:
        if lo <= cena < (hi if hi is not None else 10 ** 9):
            return lo
    return None


def z_odpowiedzi(dane):
    """Liczba wyswietlen z odpowiedzi GraphQL albo None.

    None znaczy "nie wiem" i nigdy nie jest zamieniane na zero: zero
    wyswietlen to mocne twierdzenie o ogloszeniu, a nieudany odczyt nie
    jest zadnym twierdzeniem."""
    try:
        n = dane["data"]["myAds"]["pageViews"]["pageViews"]
    except (TypeError, KeyError):
        return None
    return n if isinstance(n, int) and n >= 0 else None


def czytaj_wyswietlenia(ad_id, url=None, sesja=None, timeout=15):
    """Jedno zapytanie o licznik. None przy kazdym niepowodzeniu."""
    klient = sesja or requests
    try:
        r = klient.post(GRAPHQL, timeout=timeout, json={
            "operationName": "PageViews",
            "variables": {"adId": str(ad_id)},
            "query": ZAPYTANIE})
        if r.status_code != 200:
            return None
        return z_odpowiedzi(r.json())
    except Exception:
        return None


# Znacznik, pod ktorym OLX renderuje licznik. Szukamy ATRYBUTU, nie napisu:
# napis jest tlumaczeniem i zmienia sie z jezykiem, data-testid nie.
SELEKTOR = '[data-testid="page-view-counter"]'
CZEKAJ_MS = 8000


# === ODCZYT PRZEZ CLOUDFLARE (droga serwerowa) ==============================
# Cloudflare ma uslugę, ktora renderuje strone po swojej stronie i oddaje tekst
# wskazanych selektorow: "Browser Run", dawniej Browser Rendering. Dwie rzeczy
# robia z niej wlasciwa odpowiedz na ten problem:
#   - renderuje z WLASNYCH adresow, a te OLX wpuszcza - caly ten bot chodzi po
#     OLX-ie przez przekaznik na Cloudflare i dostaje 200,
#   - plan darmowy daje 10 MINUT czasu przegladarki na dobe, a caly tygodniowy
#     przebieg na 150 ofertach to okolo 7 minut.
# Dzieki temu pomiar nie potrzebuje ani runnera GitHuba (zablokowany przez
# OLX), ani komputera wlasciciela (zajety).
CF_SCRAPE = ("https://api.cloudflare.com/client/v4/accounts/"
             "{konto}/browser-run/scrape")


# PRZEWIJANIE (19.11.2026). Drugi bieg: 429 i ValueError znikly, ale wszystkie
# piec odczytow to "selektor pusty". Licznika nie ma w DOM w chwili pobrania,
# bo OLX dokleja go dopiero, gdy stopka wejdzie w pole widzenia. Zapytanie
# musi wiec zrobic to, co robi czlowiek: zejsc na dol strony i poczekac.
#
# Trzy rzeczy naraz, bo dokumentacja nie mowi, w jakiej kolejnosci Browser Run
# wykonuje skrypt wzgledem pobrania selektorow, a kazda z nich sama w sobie
# moze wystarczyc:
#   - wysoki ekran: przy 4000 px stopka bywa widoczna od razu,
#   - skrypt przewijajacy, powtarzany, bo tresc rosnie w trakcie doczytywania,
#   - czekanie na SAM licznik zamiast na cisze w sieci.
# bestAttempt zostawia to, co udalo sie zebrac, gdy licznik mimo wszystko nie
# wejdzie - lepsze niz twardy blad bez zadnej tresci.
SKRYPT_NA_DOL = (
    "(function(){var n=0;var t=setInterval(function(){"
    "window.scrollTo(0,document.body.scrollHeight);"
    "if(++n>12)clearInterval(t);},400);})();")


def cialo_scrape(url, selektor=SELEKTOR):
    """Cialo zapytania /scrape dla jednej oferty."""
    return {
        "url": url,
        "elements": [{"selector": selektor}],
        "viewport": {"width": 1280, "height": 4000},
        "gotoOptions": {"waitUntil": "networkidle0"},
        "addScriptTag": [{"content": SKRYPT_NA_DOL}],
        "waitForSelector": {"selector": selektor, "timeout": 15000},
        "bestAttempt": True,
    }


class CzytnikCloudflare:
    """Czyta licznik przez Browser Run. Interfejs ten sam co czytnik lokalny.

    Konto i token biora sie ze srodowiska (CF_ACCOUNT_ID, CF_TOKEN), bo to
    sekrety - w repozytorium ma ich nie byc. Token potrzebuje uprawnienia
    "Browser Run - Edit" i niczego wiecej.

    Nagłowek odpowiedzi X-Browser-Ms-Used mowi, ile czasu przegladarki poszlo
    na to zapytanie; sumujemy go, zeby bylo widac, czy mieścimy sie w darmowym
    limicie, zanim zrobi to za nas rachunek."""

    def __init__(self, konto=None, token=None, timeout=60,
                 budzet_ms=BUDZET_MS):
        import os
        self.konto = konto or os.environ.get("CF_ACCOUNT_ID") or ""
        self.token = token or os.environ.get("CF_TOKEN") or ""
        self.timeout = timeout
        self.powody = {}
        self.ms_uzyte = 0
        self.budzet_ms = budzet_ms
        self.ostatnie = None

    def __enter__(self):
        if not self.konto or not self.token:
            raise RuntimeError("brak CF_ACCOUNT_ID albo CF_TOKEN")
        return self

    def __exit__(self, *a):
        return False

    @staticmethod
    def _ms(r):
        """Czas przegladarki z naglowka. Cloudflare potrafi oddac ulamek
        ("2873.5"), a int() na takim napisie rzuca ValueError - 19.11.2026
        to zjadlo OBA udane odczyty z piecioelementowej probki, bo wyjatek
        leciał przed odczytaniem liczby."""
        try:
            return int(float(r.headers.get("X-Browser-Ms-Used") or 0))
        except (TypeError, ValueError):
            return 0

    def _poczekaj(self):
        """Przerwa miedzy zapytaniami, zeby nie wejsc w limit tempa."""
        import time
        if self.ostatnie is not None:
            zostalo = ODSTEP_S - (time.monotonic() - self.ostatnie)
            if zostalo > 0:
                time.sleep(zostalo)
        self.ostatnie = time.monotonic()

    def __call__(self, oid, url=None):
        if not url:
            self.powody["brak url"] = self.powody.get("brak url", 0) + 1
            return None
        if self.ms_uzyte >= self.budzet_ms:
            self.powody["budzet"] = self.powody.get("budzet", 0) + 1
            return None
        self._poczekaj()
        powod = "?"
        try:
            r = requests.post(
                CF_SCRAPE.format(konto=self.konto), timeout=self.timeout,
                headers={"Authorization": f"Bearer {self.token}",
                         "Content-Type": "application/json"},
                json=cialo_scrape(url))
            self.ms_uzyte += self._ms(r)
            if r.status_code != 200:
                # 429 ma dwa rozne znaczenia - za szybko albo koniec limitu na
                # dobe - a rozroznia je dopiero tresc, wiec idzie do powodu.
                powod = f"HTTP {r.status_code}"
                if r.status_code == 429:
                    powod += ": " + (r.text or "")[:60].replace("\n", " ")
                return None
            dane = r.json()
            if not dane.get("success"):
                powod = str((dane.get("errors") or [{}])[0].get("message"))[:60]
                return None
            n = liczba_z_odpowiedzi_cf(dane)
            powod = "ok" if n is not None else "selektor pusty"
            return n
        except Exception as e:
            powod = type(e).__name__
            return None
        finally:
            self.powody[powod] = self.powody.get(powod, 0) + 1


def liczba_z_odpowiedzi_cf(dane):
    """Licznik z odpowiedzi /scrape. None, gdy selektora nie bylo na stronie.

    Pusta lista `results` znaczy, ze element sie nie pojawil - najczesciej
    dlatego, ze oferta zostala zdjeta albo ze licznik nie zdazyl sie doczytac.
    To jest brak odczytu, nie zero wyswietlen."""
    for el in (dane.get("result") or []):
        for w in (el.get("results") or []):
            n = liczba_z_tekstu(w.get("text"))
            if n is not None:
                return n
    return None


def liczba_z_tekstu(tekst):
    """"Wyswietlenia: 1 329" -> 1329. None, gdy liczby nie ma.

    Spacja w tysiacach bywa zwykla, bywa nielamliwa (\u00a0) - obie musza
    przejsc, bo inaczej kazda oferta powyzej tysiaca wyswietlen wypada
    z pomiaru i zostaja same slabe."""
    import re as _re
    m = _re.search(r"\d[\d\s\u00a0]*", tekst or "")
    if not m:
        return None
    cyfry = _re.sub(r"[^\d]", "", m.group(0))
    return int(cyfry) if cyfry else None


class CzytnikPrzegladarki:
    """Czyta licznik tak, jak czyta go czlowiek: renderujac strone oferty.

    PO CO TAK, A NIE JEDNYM ZAPYTANIEM. Endpoint, z ktorego strona bierze te
    liczbe, wymaga tokenu (patrz naglowek pliku) i po ten token nie siegamy.
    Renderowanie jest drozsze, ale nie udaje nikogo: otwieramy publiczna
    strone, tak jak kupujacy.

    JEDNA PRZEGLADARKA NA CALA PROBKE, nie jedna na oferte - start Chrome'a
    trwa dluzej niz samo wejscie na strone, wiec czterdziesci uruchomien
    zamienia dwie minuty w kwadrans.

    GDZIE TO MA CHODZIC: NIE NA RUNNERZE GITHUBA. Zmierzone 16.11.2026 na
    dwoch biegach: 45 prob, 45 odpowiedzi HTTP 403. OLX blokuje cala te
    serwerownie - to ten sam blok, dla ktorego caly bot chodzi przez
    przekaznik na Cloudflare, i przegladarka nic tu nie zmienia, bo blokada
    jest na adresie, nie na kliencie. Przez przekaznik tez nie przejdzie:
    on przepisuje jeden adres, a strona doczytuje kilkadziesiat wlasnych
    zasobow prosto z olx.pl i dostanie na nie 403.

    Zostaje maszyna z adresem, ktory OLX wpuszcza, czyli komputer wlasciciela.
    Z niego licznik czyta sie bez problemu (sprawdzone: 329 dla ogloszenia
    1100315214). `channel="chrome"` uzywa zainstalowanego Chrome'a, wiec nie
    ma pobierania przegladarki; gdy go nie ma, spada na chromium z pakietu."""

    def __init__(self, odstep_s=1.0):
        self.odstep_s = odstep_s
        self.powody = {}
        self._pw = self._browser = self._ctx = None

    def __enter__(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        try:
            self._browser = self._pw.chromium.launch(channel="chrome",
                                                     headless=True)
        except Exception:
            self._browser = self._pw.chromium.launch(headless=True)
        self._ctx = self._browser.new_context(locale="pl-PL")
        return self

    def __exit__(self, *a):
        for x in (self._ctx, self._browser):
            try:
                x and x.close()
            except Exception:
                pass
        try:
            self._pw and self._pw.stop()
        except Exception:
            pass

    def __call__(self, oid, url=None):
        """Licznik dla jednej oferty. None przy kazdym niepowodzeniu.

        POWOD NIEPOWODZENIA JEST ZAPISYWANY w `self.powody`. Pierwszy bieg
        16.11.2026 dal 0 odczytow na 40 prob i z dziennika nie dalo sie
        odczytac, czy to blokada, czy zmiana znacznika - a to dwie zupelnie
        rozne naprawy. Cichy None jest tu gorszy niz brak pomiaru."""
        if not url or self._ctx is None:
            self.powody["brak url"] = self.powody.get("brak url", 0) + 1
            return None
        import time as _time
        strona = self._ctx.new_page()
        powod = "?"
        try:
            odp = strona.goto(url, timeout=30000, wait_until="domcontentloaded")
            status = odp.status if odp is not None else None
            if status and status != 200:
                powod = f"HTTP {status}"
                return None
            # licznik doczytuje sie dopiero, gdy stopka wejdzie w pole widzenia
            strona.mouse.wheel(0, 40000)
            strona.wait_for_selector(SELEKTOR, timeout=CZEKAJ_MS)
            n = liczba_z_tekstu(strona.inner_text(SELEKTOR))
            powod = "ok" if n is not None else "znacznik bez liczby"
            return n
        except Exception as e:
            # tytul strony mowi, czy to captcha albo "dostep zablokowany",
            # czy normalna oferta, na ktorej tylko nie doczekalismy znacznika
            try:
                powod = f"{type(e).__name__}: {(strona.title() or '')[:60]}"
            except Exception:
                powod = type(e).__name__
            return None
        finally:
            self.powody[powod] = self.powody.get(powod, 0) + 1
            try:
                strona.close()
            except Exception:
                pass
            _time.sleep(self.odstep_s)   # grzecznosc wobec serwisu


def wiek_dni(wystawiono, teraz):
    """Ile dob ogloszenie zyje. None, gdy nie wiadomo od kiedy."""
    if not wystawiono:
        return None
    try:
        a = datetime.fromisoformat(str(wystawiono).replace("Z", "+00:00"))
        b = datetime.fromisoformat(str(teraz).replace("Z", "+00:00"))
    except ValueError:
        return None
    if a.tzinfo:
        a = a.replace(tzinfo=None)
    if b.tzinfo:
        b = b.replace(tzinfo=None)
    return max((b - a).days, 0)


def na_dobe(wyswietlen, wiek):
    """Wyswietlenia na dobe zycia. None, gdy wieku nie znamy.

    Wiek 0 (ogloszenie z dzisiaj) liczy sie jako jedna doba, a nie jako
    dzielenie przez zero - inaczej kazda swieza oferta wypadalaby z pomiaru
    albo dawala nieskonczonosc."""
    if not isinstance(wyswietlen, int) or wiek is None:
        return None
    return round(wyswietlen / max(wiek, 1), 1)


def probka(stan, ile=PROBKA_NA_PRZEBIEG, juz_zmierzone=()):
    """Ktore oferty odpytac. Rownomiernie po pasmach, nie "pierwsze z brzegu".

    Probka wzieta z czola slownika bylaby probka JEDNEGO pasma albo jednego
    sprzedawcy - a pytanie brzmi, jak popyt rozklada sie MIEDZY pasmami.
    Dlatego budzet dzieli sie po rowno, a pasmo, ktore nie wykorzysta swojego,
    oddaje reszte pozostalym.

    Pomijamy oferty bez daty wystawienia (nie ma jak policzyc wieku, wiec
    odczyt bylby nieporownywalny) i te juz zmierzone w tym przebiegu."""
    wg_pasm = {}
    for oid, rec in (stan or {}).items():
        if oid in juz_zmierzone or not isinstance(rec, dict):
            continue
        if not rec.get("wystawiono") or not rec.get("url"):
            continue
        p = _pasmo(rec.get("p"))
        if p is None:
            continue
        wg_pasm.setdefault(p, []).append(oid)
    for lst in wg_pasm.values():
        lst.sort()                      # powtarzalnosc: ta sama probka z tego
                                        # samego stanu, zeby dalo sie odtworzyc
    wybor, pasma = [], sorted(wg_pasm)
    if not pasma:
        return []
    na_pasmo = max(ile // len(pasma), 1)
    for p in pasma:
        wybor += wg_pasm[p][:na_pasmo]
    if len(wybor) < ile:                # niewykorzystany budzet idzie dalej
        for p in pasma:
            for oid in wg_pasm[p][na_pasmo:]:
                if len(wybor) >= ile:
                    break
                wybor.append(oid)
    return wybor[:ile]


# Jaka czesc przebiegu idzie na STARE oferty (panel), a jaka na nowe.
# Caly przebieg na panelu mierzylby w kolko te same rowery i nigdy nie
# dotknal nowych; caly na nowych nie dalby ani jednego przyrostu. 60/40
# daje jedno i drugie: 120 ofert z przyrostem i 80 nowych dziennie, przy
# naplywie okolo 33 nowych ofert na dobe na polce.
UDZIAL_PANELU = 0.6


def panel(stan, historia=(), ile=PROBKA_NA_PRZEBIEG, udzial=UDZIAL_PANELU):
    """Te same oferty co poprzednio, uzupelnione nowymi do pelnej probki.

    PO CO PANEL, A NIE LOSOWANIE ZA KAZDYM RAZEM (16.11.2026). Licznik OLX-a
    jest skumulowany od wystawienia, wiec jeden odczyt juz daje srednia na
    dobe zycia. Drugi odczyt TEJ SAMEJ oferty daje cos, czego zadna nowa
    probka nie da: PRZYROST miedzy przebiegami, czyli zainteresowanie w tym
    tygodniu, a nie usrednione przez cale zycie ogloszenia. Dlatego oferta
    raz wzieta zostaje w panelu, dopoki stoi.

    Oferty, ktore zeszly z polki, wypadaja same - nie ma ich w stanie.
    Zwolnione miejsca zapelniaja nowe, rozlozone po pasmach jak w `probka`."""
    stare = [oid for oid in historia if oid in (stan or {})]
    stare = list(dict.fromkeys(stare))[:max(int(ile * udzial), 1)]
    brakuje = ile - len(stare)
    if brakuje <= 0:
        return stare
    return stare + probka(stan, ile=brakuje, juz_zmierzone=set(stare))


def zmierzone_wczesniej(rekordy):
    """Identyfikatory z dziennika, od ostatnio widzianych. Tylko te z odczytem:
    oferta, ktorej nie udalo sie odczytac, nie jest czescia panelu."""
    out = []
    for r in reversed(rekordy):
        if r.get("id") and isinstance(r.get("wyswietlen"), int):
            out.append(r["id"])
    return list(dict.fromkeys(out))


def przyrosty(rekordy):
    """Ile wyswietlen przybylo miedzy dwoma ostatnimi odczytami tej samej
    oferty, przeliczone NA DOBE odstepu.

    To jest wlasciwy miernik zainteresowania TERAZ. Srednia na dobe zycia
    miesza dzisiejszy popyt z tym sprzed pol roku, a przy ofercie stojacej
    od marca to juz nie jest informacja o rynku, tylko o historii."""
    wg_ofert = {}
    for r in rekordy:
        if r.get("id") and isinstance(r.get("wyswietlen"), int) and r.get("ts"):
            wg_ofert.setdefault(r["id"], []).append(r)
    out = []
    for oid, lst in wg_ofert.items():
        if len(lst) < 2:
            continue
        lst.sort(key=lambda x: x["ts"])
        a, b = lst[-2], lst[-1]
        dni = wiek_dni(a["ts"], b["ts"])
        if not dni:
            continue                      # dwa odczyty tego samego dnia
        przyrost = b["wyswietlen"] - a["wyswietlen"]
        if przyrost < 0:
            continue                      # licznik nie maleje; to inna oferta
                                          # pod tym samym id albo reset po stronie OLX
        out.append({"id": oid, "dni": dni, "przyrost": przyrost,
                    "na_dobe": round(przyrost / dni, 1),
                    "p": b.get("p"), "pasmo": b.get("pasmo"),
                    "firma": b.get("firma")})
    return out


def zmierz(stan=None, ile=PROBKA_NA_PRZEBIEG, teraz=None, czytnik=None,
           historia=None):
    """Przechodzi probke i zwraca rekordy do dziennika. Bez zapisu."""
    teraz = teraz or datetime.now().strftime("%Y-%m-%dT%H:%M")
    if stan is None:
        stan = (json.loads(POLKA_STAN.read_text(encoding="utf-8"))
                if POLKA_STAN.exists() else {})
    czytnik = czytnik or czytaj_wyswietlenia
    if historia is None:
        historia = zmierzone_wczesniej(wczytaj())
    out = []
    for oid in panel(stan, historia, ile):
        rec = stan[oid]
        n = czytnik(oid, rec.get("url"))
        w = wiek_dni(rec.get("wystawiono"), teraz)
        out.append({"ts": teraz, "id": oid, "p": rec.get("p"),
                    "firma": bool(rec.get("firma")), "pasmo": _pasmo(rec.get("p")),
                    "wiek_dni": w, "wyswietlen": n, "na_dobe": na_dobe(n, w)})
    return out


def zapisz(rekordy, plik=None):
    """Dopisuje do dziennika. Jak kazdy dziennik w tym repo: tylko dopisujemy."""
    if not rekordy:
        return 0
    plik = plik or WYSWIETLENIA_FILE
    with plik.open("a", encoding="utf-8") as f:
        for r in rekordy:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(rekordy)


# Ponizej tylu odczytow mediana jest loteria i zamiast niej idzie "nie wiem".
MIN_ODCZYTOW = 10


def podsumuj(rekordy):
    """Mediana wyswietlen na dobe: calosc, pasma, prywatne vs firmy."""
    dobre = [r for r in rekordy if isinstance(r.get("na_dobe"), (int, float))]
    if len(dobre) < MIN_ODCZYTOW:
        return {"n": len(dobre), "mediana": None, "pasma": {}, "prywatne": None,
                "firmy": None, "bez_odczytu": len(rekordy) - len(dobre)}
    med = lambda v: (round(statistics.median(v), 1)
                     if len(v) >= MIN_ODCZYTOW else None)
    pasma = {}
    for lo, _ in PASMA:
        w = [r["na_dobe"] for r in dobre if r.get("pasmo") == lo]
        if len(w) >= MIN_ODCZYTOW:
            pasma[str(lo)] = {"n": len(w), "mediana": med(w)}
    return {"n": len(dobre),
            "mediana": med([r["na_dobe"] for r in dobre]),
            "pasma": pasma,
            "prywatne": med([r["na_dobe"] for r in dobre if not r.get("firma")]),
            "firmy": med([r["na_dobe"] for r in dobre if r.get("firma")]),
            "bez_odczytu": len(rekordy) - len(dobre)}


def wczytaj(plik=None):
    out = []
    try:
        for linia in (plik or WYSWIETLENIA_FILE).read_text(
                encoding="utf-8").splitlines():
            if linia.strip():
                try:
                    out.append(json.loads(linia))
                except json.JSONDecodeError:
                    continue
    except FileNotFoundError:
        pass
    return out


def raport() -> str:
    d = podsumuj(wczytaj())
    if d["mediana"] is None:
        return ("👁 <b>Wyswietlenia ofert</b>\n"
                f"Za malo odczytow na wniosek ({d['n']}, potrzeba "
                f"{MIN_ODCZYTOW}). Uruchom <code>python3 wyswietlenia.py</code>.")
    L = ["👁 <b>Wyswietlenia ofert - ile osob to oglada</b>",
         f"<i>{d['n']} odczytow z dziennika</i>", "",
         f"Mediana: <b>{d['mediana']}</b> wyswietlen na dobe zycia ogloszenia"]
    if d["prywatne"] is not None and d["firmy"] is not None:
        L.append(f"· prywatne {d['prywatne']} · firmy {d['firmy']}")
    if d["pasma"]:
        L.append("")
        L.append("<b>Wg pasma cenowego</b>")
        for lo, w in sorted(d["pasma"].items(), key=lambda x: int(x[0])):
            L.append(f"· od {int(lo) // 1000}k: <b>{w['mediana']}</b> "
                     f"na dobe (n={w['n']})")
    if d["bez_odczytu"]:
        L.append(f"\n<i>ⓘ {d['bez_odczytu']} prob bez odczytu - to NIE sa "
                 f"zera wyswietlen, tylko brak odpowiedzi.</i>")
    pr = przyrosty(wczytaj())
    if len(pr) >= MIN_ODCZYTOW:
        nd = sorted(x["na_dobe"] for x in pr)
        L += ["", "<b>Zainteresowanie TERAZ (przyrost miedzy przebiegami)</b>",
              f"· mediana: <b>{round(statistics.median(nd), 1)}</b> nowych "
              f"wyswietlen na dobe (n={len(pr)} ofert)"]
        martwe = sum(1 for x in pr if x["przyrost"] == 0)
        if martwe:
            L.append(f"· {martwe} ofert nie zyskalo ANI JEDNEGO wyswietlenia "
                     f"miedzy przebiegami")
        L.append("<i>ⓘ To mierzy biezacy ruch na tych samych ofertach. Srednia "
                 "na dobe zycia miesza dzisiejszy popyt z tym sprzed pol roku.</i>")
    L.append("<i>ⓘ Licznik rosnie z wiekiem ogloszenia, wiec porownywalna jest "
             "dopiero liczba NA DOBE zycia, nie sama suma.</i>")
    return "\n".join(L)


if __name__ == "__main__":
    if "--raport" in sys.argv:
        import re as _re
        print(_re.sub(r"</?(b|i|code)>", "", raport()))
    elif "--sprawdz" in sys.argv:
        # Jedno ogloszenie, zeby zobaczyc, czy endpoint w ogole odpowiada
        # spoza przegladarki. To jest ta niesprawdzona rzecz z naglowka pliku.
        ad = sys.argv[sys.argv.index("--sprawdz") + 1]
        print(json.dumps({"id": ad, "wyswietlen": czytaj_wyswietlenia(ad)},
                         ensure_ascii=False))
    else:
        import os
        ile = int(os.environ.get("PROBKA") or PROBKA_NA_PRZEBIEG)
        # Domyslnie Cloudflare: renderuje po swojej stronie, nie obciaza
        # niczego u wlasciciela i nie wymaga runnera, ktorego OLX nie wpuszcza.
        # CZYTNIK=lokalny zostaje jako droga zapasowa.
        lokalny = (os.environ.get("CZYTNIK") or "").lower() == "lokalny"
        Klasa = CzytnikPrzegladarki if lokalny else CzytnikCloudflare
        try:
            with Klasa() as czytnik:
                rek = zmierz(ile=ile, czytnik=czytnik)
                powody = dict(czytnik.powody)
                ms = getattr(czytnik, "ms_uzyte", 0)
        except ImportError:
            print(json.dumps({"ok": False, "blad": "brak playwright - "
                              "pip install playwright"}, ensure_ascii=False))
            raise SystemExit(1)
        except RuntimeError as e:
            print(json.dumps({"ok": False, "blad": str(e)}, ensure_ascii=False))
            raise SystemExit(1)
        n = zapisz(rek)
        udane = sum(1 for r in rek if r.get("wyswietlen") is not None)
        print(json.dumps({"ok": True, "zapisane": n, "z_odczytem": udane,
                          "bez_odczytu": n - udane,
                          "powody": powody,
                          "czas_przegladarki_s": round(ms / 1000, 1)},
                         ensure_ascii=False))
