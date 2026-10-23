#!/usr/bin/env python3
"""Zapas polki Kleinanzeigen - CALA polka marek premium co dobe, nie przeplyw.

PO CO (22.10.2026). Bot widzi WYLACZNIE ogloszenia swieze i to jest jego
jedyne pole widzenia:

  - polka kategorii (`fetch_feed`) oddaje najnowsze karty, jedna strona to
    ~10 minut rynku, a `FEED_MAX_STRON` = 12 stron to ~2 h wstecz;
  - pierwsza strona zapytania modelowego (`SEARCHES`) siega podobnie;
  - fraza wraca w rotacji co ~110 minut.

Wszystko, co wisi dluzej, jest poza oknem i bot nie dowie sie o nim nigdy.
Do tego wpis w `seen.json` jest TERMINALNY: rower raz odrzucony milczy na
zawsze, wraca tylko przy realnej przecenie i tylko wtedy, gdy bot zobaczy go
ponownie - a stojace ogloszenie nie wraca na swieza liste. Kazda zmiana reguly
dziala wiec TYLKO W PRZOD.

DLACZEGO TO WAZNE, A NIE TYLKO CIEKAWE. Wlasciciel ma tani transport
(kierowca zawodowy przejazdem, gielda transportowa ~300 zl), wiec NIE MUSI
wygrywac wyscigu o ogloszenie sprzed pieciu minut. Potrzebuje ofert, ktore
stoja tygodniami - tam da sie targowac i spokojnie umowic odbior. Zmierzone
wczesniej: z ceny schodzi 2,8% sprzedawcow, mediana zejscia 200 EUR, i dzieje
sie to PO KILKU DNIACH STANIA, nie w dniu wystawienia. Dokladnie te oferty
sa dzis dla bota niewidoczne.

Ten plik jest odpowiednikiem `przeplyw.py` dla strony niemieckiej. Tam rama
to cala kategoria OLX od 8000 zl; tu rama jest ZMIERZONA i wezsza, bo polka
niemiecka jest o rzad wielkosci wieksza - patrz "CO SIE NIE ZMIESCILO".

PODZIAL ROL (ten sam co dozorca.py / zycie_ofert.py): dziennik trzyma
WYLACZNIE fakty ("22.10 o 19:04 ogloszenie X stalo za 2 400 EUR, wystawione
14.09"), nigdy wnioskow ("ten rower stoi dlugo, mozna targowac"). Wnioski
licza `zapas()` i `przelicz()` i mozna je przeliczyc od zera, gdy regula
okaze sie bledna. To jest cala roznica miedzy "zmiana reguly = przeliczenie"
a "zmiana reguly = zbieranie od nowa przez miesiac".

=== CO ZMIERZONE 22.10.2026, I DLACZEGO RAMA WYGLADA TAK ========================

1. SUFIT PAGINACJI TO STRONA 50, czyli 1350 ogloszen na jedno zapytanie.
   To niemiecki odpowiednik limitu 1000 rekordow z API OLX-a. Zmierzone na
   zapytaniu kategoria+cena: strony 1, 2, 20 i 50 oddaja po 27 rozlacznych
   kart (zero wspolnych numerow), a strona 51 i 60 oddaja TO SAMO co strona
   50 (24-25 wspolnych kart z 27, reszta to przesuniecie polki miedzy
   zadaniami) i adres gubi segment `seite:`. Powyzej strony 50 ogona NIE MA
   JAK zobaczyc.

2. CALA POLKA W WIDELKACH BOTA SIE NIE DA. Zapytanie
   `fahrraeder.type_s:ebike` + `preis:800:3000` oddaje **39 716** stojacych
   ogloszen. To 1471 stron dziennie, przy zmierzonym dlawieniu Kleinanzeigen
   (~50 zadan w 10 minut z jednego adresu = strona-smiec na 20 minut) to
   okolo 5 godzin nieprzerwanego pobierania. Nie ma takiej doby.

3. RAMA, KTORA SIE ZMIESCILA: szesc marek z `PREMIUM_BRANDS`, bo TYLKO one
   dostaja powiadomienie (marka niszowa przechodzi wyjatkowo, przy przecenie
   30%+ - patrz `NICHE_MIN_DISCOUNT_PCT`). Zmierzone tego dnia, kategoria
   e-bike, 800-3000 EUR: cube 4 445, ktm 2 183, specialized 686, scott 505,
   canyon 339, trek 334. Razem **8 492 ogloszenia**, a po rozbiciu na pasma
   cenowe 9 047 (granice pasm zachodza, numer ogloszenia odsiewa duble).
   W stronach listy: **384 na dobe** - patrz punkt 5.

   Marka, nie slowo "fully". Zapytanie `marka-fully` daje 692 ogloszenia
   zamiast 8 492, czyli dwunastokrotnie mniej - ale mierzy WTEDY to, czy
   sprzedawca raczyl napisac slowo, a nie to, czy rower jest fully. Ta sama
   choroba, ktora `is_fully` przechodzil 02.09.2026: na 37 modelach odsetek
   przepuszczonych byl ROWNY odsetkowi tych, ktorzy slowo napisali. O tym,
   czy rower jest fully, decyduja bramki bota na zapisanym tytule - patrz
   `przelicz()` - a nie zapytanie do serwisu. Wpuszczamy szeroko, oznaczamy
   wasko.

4. CZEGO SIE NIE ZMIESCILO - nie udawaj, ze sie zmiescilo:
   - marki niszowe (31 224 ogloszenia poza szescioma markami). Wchodza do
     bota wylacznie przy przecenie 30%+, a tego nie da sie ocenic bez
     mediany, ktorej z tej ramy nie mamy. Zapas ich NIE WIDZI.
   - polka `mountainbike` (sprzedawca myli rubryke - tak zginal Levo FSR
     22.08.2026). Marka+fully na tej polce to kolejne 4 926 ogloszen, czyli
     drugie tyle ruchu. ODLOZONE, nie przeoczone.
   - ogloszenia bez ustawionego typu. Ta sama decyzja co przy trzeciej polce
     kanalu (19.09.2026): dziura warta 0,65% wolumenu, lekarstwo gorsze od
     choroby.

5. KOSZT, ZMIERZONY PRZED WDROZENIEM. Przelot zapasu dokłada **384 strony
   listy na dobe**. Rachunek jest z pomiaru, nie z dzielenia 9 047 przez 27:
   strona oddaje ZMIERZONE ~25 kart, nie 27 (pasmo cube 1600-1900 ma 845
   ogloszen na 34 stronach), a kazde pasmo placi jedno zadanie za powtorzona
   ostatnia strone, czyli za rozpoznanie konca. Stad
   `suma(ceil(ogloszen / 25) + 1)` po czternastu pasmach = 384.

   Do tego kolejka szczegolow najwyzej `BUDZET_SZCZEGOLOW` = 40 stron
   ogloszen. Razem **424 pobrania na dobe**. Glowny skan pobiera dzis
   ~111 stron ogloszen dziennie (zmierzone `sprawdz_zachowanie.py` na oknie
   06-19.09) plus ~24 strony listy na skan. Ten modul NIE MA PRAWA jechac
   tym samym adresem IP co glowny skan - inaczej oba wpadaja w dlawienie,
   a wtedy traci sie nie zapas, tylko czas reakcji, czyli rzecz wazniejsza.
   Dlatego `PAUZA_S` trzyma tempo pod zmierzonym progiem dlawienia i dlatego
   przelot ma chodzic z WLASNEGO workflow (wlasny runner = wlasny adres),
   tak jak dozorca.

Pliki:
  polka_de_stan.json              - kto stoi na polce teraz (odtwarzalny)
  zdarzenia_de_polka/polka-RRRR-MM.jsonl - dziennik, append-only, NIGDY nie kasowany
  polka_de_wyslane.json           - co juz poszlo z przeliczenia (zeby nie dwa razy)

Dziennik w kawalkach miesiecznych, bo git nie zapisuje roznic, tylko caly
plik od nowa (18.09.2026: jeden wiersz dopisany do pliku 27,5 MB tworzyl nowy
obiekt na 27,5 MB, siedem razy na bieg).

Uruchom:
  python3 polka_de.py                   # przejdz zapas i zapisz zmiany
  python3 polka_de.py --raport          # co stoi, jak dlugo, kto schodzi z ceny
  python3 polka_de.py --szczegoly       # kolejka: doczytaj przebieg najdluzej wiszacym
  python3 polka_de.py --przelicz        # ocen zapas DZISIEJSZA regula, NA SUCHO
  python3 polka_de.py --przelicz --wyslij --tak   # ... i wyslij, swiadomie
"""
import argparse
import json
import statistics
import sys
import time
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

import tracker as T

STAN_FILE = Path("polka_de_stan.json")
ZDARZENIA_DIR = Path("zdarzenia_de_polka")
WYSLANE_FILE = Path("polka_de_wyslane.json")

# --- SUFITY SERWISU, ZMIERZONE 22.10.2026 ----------------------------------
# Kleinanzeigen oddaje najwyzej 50 stron na jedno zapytanie. Strona 51 i 60
# wracaja z trescia strony 50, a adres gubi segment `seite:`.
SUFIT_STRON = 50
# Ile kart oddaje strona. 27 to MAKSIMUM (strony 1-2), a zmierzona srednia to
# ~25: pasmo cube 1600-1900 ma 845 ogloszen na 34 stronach. Ta stala sluzy
# WYLACZNIE do liczenia sufitu i kosztu. NIE WOLNO uzywac jej jako sygnalu
# "to ostatnia strona" - strony 30-33 tego pasma mialy po 25 kart i pasmo
# trwalo dalej. Patrz `przejdz_pasmo`.
KART_NA_STRONE = 27
KART_SREDNIO = 25
SUFIT_OGLOSZEN = SUFIT_STRON * KART_NA_STRONE      # 1350

# Tempo. Zmierzone dlawienie Kleinanzeigen: ~50 zadan w 10 minut z jednego
# adresu daje strone-smiec na 20 minut (rozdzial o filtrze fully, 02.09.2026).
# 13 s to ~4,6 zadania na minute, czyli POD tym progiem z zapasem. 344 strony
# zapasu chodza wtedy ~75 minut i to jest cena, ktora tu placimy swiadomie:
# szybciej znaczy strona-smiec, a strona-smiec to pasmo NIEPELNE, czyli
# dokladnie to, przed czym ten plik ma chronic.
PAUZA_S = 13
# Odmowa w trakcie WLASNEJ serii to najczesciej chwilowe dlawienie, nie awaria
# serwisu - jedna dluzsza pauza i powtorka ratuja caly przelot (27.09.2026 po
# stronie OLX: siedem pasm z osmiu wrocilo puste, a spis kategorii piec minut
# wczesniej przeszedl bez potkniecia).
PAUZA_PO_ODMOWIE_S = 60

# Ogloszenie musi zniknac z DWOCH dob z rzedu, zeby uznac je za zejscie.
# Jeden brak to moze byc dziura w wynikach serwisu (to samo zalozenie, co
# BRAKI_DO_SPRAWDZENIA w dozorcy - tam zmierzone: 18% znikniec to falszywy
# alarm).
BRAKI_DO_ZEJSCIA = 2

# Ile stron listy wolno pobrac w jednym uruchomieniu. Przelot jest WZNAWIALNY:
# pasmo przejste do konca zostaje odhaczone w stanie, a nastepny bieg bierze
# te, ktorych jeszcze nie bylo. Doba zalicza sie dopiero, gdy wszystkie pasma
# przeszly W TEJ SAMEJ DOBIE.
#
# 420 to zapas nad zmierzonymi 384 (`koszt_stron()`), zeby jeden bieg mogl
# domknac cala dobe. Mniejsza wartosc nie gubi danych - przelot wznowi sie
# w nastepnym biegu - ale doba bez domkniecia nie zalicza ani jednego
# zniknięcia, wiec dzielenie zapasu na biegi kosztuje dni pomiaru.
BUDZET_STRON = 420

# Ile stron OGLOSZEN (nie listy) wolno doczytac na jeden bieg kolejki.
# 40 stron przy PAUZA_S to ~9 minut, czyli doklada ~12% do czasu przelotu
# (zmierzone 23.10.2026: krok kolejki trwal 8 min 40 s).
#
# TA LICZBA MA TERAZ POMIAR, nie zalozenie. Pierwszy pelny zapas
# (23.10.2026, 8 479 ogloszen) rozklada sie tak:
#   1 124 - przebieg stoi w TYTULE, czyli jest za darmo (`fakty_z_karty`),
#   7 355 - przebiegu nie znamy, czyli 184 dni po 40 stron na dobe,
#     341 - z tych 7 355 to model z `topowe_modele.json`, czyli 9 dni,
#      69 - z tych 341 jest TANSZY od mediany swojej marki, czyli 2 dni.
#
# I to jest cala odpowiedz na "czy 40 wystarczy": na CALY zapas nie
# wystarczy i nie ma sensu, zeby wystarczylo, bo 7 tysiecy przebiegow
# nikomu nie jest potrzebne. Na kandydatow, u ktorych przebieg decyduje
# o pieniadzach, wystarcza dwa biegi. Podniesienie tej liczby kupuje tylko
# dluzszy ogon, a placi sie za nie ruchem - zmierzonym waskim gardlem tego
# repo. Dlatego zostaje 40, a kolejnosc kolejki robi reszte.
BUDZET_SZCZEGOLOW = 40

# Ile wiadomosci najwyzej wychodzi z jednego przeliczenia zapasu. Ta sama
# liczba co `tracker.MAX_WYSYLEK_NA_BIEG`, bo chroni przed tym samym:
# przeliczenie 8 tysiecy ogloszen nowa regula to potencjalna lawina.
SUFIT_WYSYLKI = T.MAX_WYSYLEK_NA_BIEG

# === RAMA POMIARU - NIE RUSZAC BEZ NOWEGO POMIARU ==========================
# `liczba` to ZMIERZONA 22.10.2026 liczba stojacych ogloszen w tym pasmie.
# Stoi tu po to, zeby bylo widac, ktore pasmo zbliza sie do sufitu 1350 -
# nie zeby cokolwiek nia liczyc. Pasmo, ktore sufit przekroczy, KRZYCZY
# i jest oznaczane jako NIEPELNE (ogona nie widac), zamiast po cichu
# obcinac ogon.
#
# Granice pasm ZACHODZA na siebie: `preis:800:1200` obejmuje oba konce, wiec
# rower za 1 200 EUR widac w dwoch pasmach. Numer ogloszenia odsiewa duble.
PASMA = [
    ("cube",         800, 1200,  527),
    ("cube",        1200, 1600,  888),
    ("cube",        1600, 1900,  845),
    ("cube",        1900, 2200,  920),
    ("cube",        2200, 2500,  760),
    ("cube",        2500, 3000,  954),
    ("ktm",          800, 1500,  515),
    ("ktm",         1500, 2000,  576),
    ("ktm",         2000, 2500,  680),
    ("ktm",         2500, 3000,  518),
    ("specialized",  800, 3000,  686),
    ("scott",        800, 3000,  505),
    ("canyon",       800, 3000,  339),
    ("trek",         800, 3000,  334),
]


def klucz_pasma(marka, lo, hi) -> str:
    return f"{marka}|{lo}|{hi}"


def koszt_stron(pasma=None) -> int:
    """Ile stron listy kosztuje JEDEN pelny przelot zapasu.

    Liczone z tabeli `PASMA`, a nie wpisane w dokumentacji - inaczej pierwsze
    dolozone pasmo rozjechaloby liczbe z rzeczywistoscia po cichu. Plus jedno
    zadanie na pasmo za powtorzona ostatnia strone, czyli za rozpoznanie,
    ze pasmo sie skonczylo."""
    pasma = pasma if pasma is not None else PASMA
    return sum(-(-liczba // KART_SREDNIO) + 1 for _, _, _, liczba in pasma)


def adres(marka, lo, hi, n=1) -> str:
    """Adres strony listy. Kolejnosc segmentow jest ta z serwisu: najpierw
    filtr ceny, potem numer strony, potem fraza, a na koncu kategoria z typem.
    Przestawienie ich gubi filtr po cichu - zmierzone 22.10.2026 na wariancie,
    w ktorym cena wyladowala za `c217` i licznik wynikow wrocil bez niej."""
    seite = f"seite:{n}/" if n > 1 else ""
    return (f"https://www.kleinanzeigen.de/s-fahrraeder/preis:{lo}:{hi}/"
            f"{seite}{marka}/k0c217+fahrraeder.type_s:ebike")


# --- FAKTY Z KARTY ---------------------------------------------------------

def fakty_z_karty(karta: dict, marka: str) -> dict:
    """Same FAKTY z kafelka listy, zero wnioskow.

    `wyst` to znacznik wystawienia ze strony - FAKT albo None, nigdy nasza
    data pierwszego widzenia. Ta sama regula, ktora po stronie DE kosztowala
    2331 falszywych faktow (25.09.2026): wiek liczony od naszego pierwszego
    widzenia zanizal czas stania ponad dwukrotnie, a tu wiek stania JEST
    produktem, bo wlasnie na nim stoi targ.

    `rez` tylko z tytulu i tylko gdy sprzedawca napisal to wprost. Zmierzone
    22.10.2026 na stronie listy marki: znacznikow `badge-unavailable`
    ani `icon-reserved-flag` NIE MA NA LISCIE W OGOLE (zero wystapien slowa
    "reserviert" na calej stronie), wiec brak flagi NIE JEST dowodem, ze
    rower jest wolny. Flage z plakietki uzupelnia dopiero kolejka szczegolow,
    ze strony ogloszenia.

    `km` Z TYTULU, parserem `tracker._extract_mileage` - tym samym, ktorego
    uzywa reszta repo, bo druga kopia rozjechalaby sie przy pierwszej
    poprawce (ta sama nauka co przy `litera_ramy` i kaskadzie bramek).
    ZMIERZONE 23.10.2026 na pierwszym pelnym zapasie (8 479 ogloszen):
    przebieg stoi w tytule u 1 124 z nich, czyli u 13,3%, a kolejka
    szczegolow znala go wtedy u 28. To 40 razy wiecej odczytow ZA DARMO,
    bez ani jednej dodatkowej strony - a przebieg jest obok ceny jedynym
    kryterium, ktore decyduje o zysku.

    `km_skad` mowi, SKAD ten odczyt jest: "tytul" albo "strona". Bez tego
    nie da sie ani ustawic kolejnosci kolejki (40 stron na dobe idzie tam,
    gdzie przebiegu NIE znamy), ani obronic drogiego odczytu ze strony
    przed tanszym z tytulu."""
    wyst = karta.get("posted")
    km = T._extract_mileage(karta.get("title") or "", "")
    km = km if km and km != "brak danych" else None
    return {
        "t": karta.get("title") or "",
        "km": km,
        "km_skad": "tytul" if km else None,
        "p": karta.get("price_num") if isinstance(karta.get("price_num"), int) else None,
        "url": karta.get("url"),
        "loc": karta.get("loc"),
        "marka": marka,
        "wyst": wyst.isoformat() if hasattr(wyst, "isoformat") else wyst,
        "rez": True if T.REZERWACJA_TEKST.search(karta.get("title") or "") else None,
    }


def _wiek_dni(znacznik, do_kiedy: datetime):
    """Ile dni stoi. None, gdy znacznika nie znamy - nigdy zero."""
    if not znacznik:
        return None
    try:
        d = datetime.fromisoformat(str(znacznik))
    except ValueError:
        return None
    if d.tzinfo is not None:
        d = d.replace(tzinfo=None)
    return max((do_kiedy - d).days, 0)


def wiek_ogloszenia(rec, teraz: datetime):
    """Wiek z WYSTAWIENIA, a gdy go nie znamy - None.

    Nie podstawiamy tu naszego pierwszego widzenia i to jest cala roznica
    miedzy faktem i zalozeniem (regula 6). Ile ogloszen ma znacznik, mowi
    `zapas()` wprost, zamiast udawac, ze wie o wszystkich."""
    return _wiek_dni(rec.get("wyst"), teraz)


# --- PRZEJSCIE PASMA ------------------------------------------------------

def przejdz_pasmo(marka, lo, hi, pobierz=None, pauza=None):
    """Wszystkie ogloszenia z pasma. Zwraca (oferty, ostrzezenia, pelne, stron).

    `pelne` mowi, czy pasmo udalo sie przejsc DO KONCA, i jest wazniejsze od
    samych ofert: bez tego brak ogloszenia w wyniku jest nieodroznialny od
    "serwis nie odpowiedzial", a to nie to samo. Pierwsze znaczy "zeszlo",
    drugie - "nie wiem". Awaria CZESCIOWA jest tu grozniejsza od calkowitej,
    bo daje wynik NIEPUSTY i wiarygodnie wygladajacy (27.09.2026 po stronie
    OLX: przelot zaliczyl sie jako udany i zapisal 88 zejsc, ktorych nie bylo,
    a 3191 ogloszeniom podbil licznik braku).

    KONIEC PASMA POZNAJEMY PO STRONIE BEZ ANI JEDNEJ NOWEJ KARTY, i to jest
    jedyny warunek, ktory sie obronil w pomiarze. Zmierzone 22.10.2026 na
    pasmie cube 1600-1900 EUR (845 ogloszen):

      seite:30..33  po 25 kart, zero wspolnych z poprzednia  <- SRODEK pasma
      seite:34      20 kart                                  <- ostatnia
      seite:40      te same 20 kart co strona 34
      seite:60      te same 20 kart co strona 34

    Czyli za ostatnia strona serwis oddaje ja raz za razem. Kosztuje to
    jedno zadanie na pasmo i jest warte tej ceny: **liczba kart na stronie
    NIE JEST sygnalem konca** - strony 30-33 mialy po 25 kart, a pasmo
    trwalo dalej. Pierwsza wersja tego kodu konczyla pasmo na "mniej niz 27
    kart" i obcielaby je na trzeciej stronie z trzydziestu czterech, meldujac
    pasmo jako PELNE. Dokladnie ten rodzaj cichego obciecia ogona, przed
    ktorym ten plik ma chronic.

    Licznika wynikow ("39.716 Ergebnisse") NIE czytamy, bo `fetch_listings`
    oddaje karty, nie HTML, a wlasne zadanie po sam licznik podwoiloby ruch.
    Powtorzona ostatnia strona odpowiada na to samo pytanie taniej.

    STRONA PUSTA TO NIE KONIEC PASMA, TO DLAWIENIE. Za koncem pasma serwis
    oddaje karty (pomiar wyzej), wiec zero kart moze znaczyc tylko jedno:
    strone okrojona (~105 kB, status 200, bez znacznikow). Pasmo jest wtedy
    NIEPELNE."""
    pobierz = pobierz or T.fetch_listings
    pauza = PAUZA_S if pauza is None else pauza
    oferty, ostrz = {}, []
    nazwa = f"zapas {marka} {lo}-{hi}"
    for n in range(1, SUFIT_STRON + 1):
        karty, stats = pobierz({"name": nazwa, "url": adres(marka, lo, hi, n)})
        if not karty:
            # JEDNA PONOWNA PROBA po dluzszej pauzie. Kilka sekund ratuje caly
            # przelot, a odmowa w trakcie wlasnej serii to zwykle dlawienie.
            time.sleep(PAUZA_PO_ODMOWIE_S)
            karty, stats = pobierz({"name": nazwa, "url": adres(marka, lo, hi, n)})
        if not karty:
            ostrz.append(f"pasmo {marka} {lo}-{hi}: strona {n} wrocila bez kart "
                         f"(status {stats.get('status')}, takze po ponownej "
                         f"probie) - pasmo NIEPELNE")
            return oferty, ostrz, False, n
        nowe = {k["id"]: k for k in karty if k.get("id") and k["id"] not in oferty}
        oferty.update(nowe)
        if not nowe:
            # Serwis oddal te same karty co wczesniej, czyli jestesmy za
            # ostatnia strona pasma. Zmierzone 22.10.2026 - patrz docstring.
            return oferty, ostrz, True, n
        if n == SUFIT_STRON:
            # KRZYCZYMY. Ciche obciecie ogona to dokladnie ten rodzaj
            # niepelnych danych, przed ktorym ten plik ma chronic.
            ostrz.append(f"pasmo {marka} {lo}-{hi} ma wiecej niz {SUFIT_OGLOSZEN} "
                         f"ofert, a Kleinanzeigen oddaje najwyzej {SUFIT_STRON} "
                         f"stron: PODZIEL TO PASMO w polka_de.PASMA, inaczej "
                         f"ogon jest niewidoczny")
            return oferty, ostrz, False, n
        time.sleep(pauza)
    return oferty, ostrz, False, SUFIT_STRON


# --- STAN I DZIENNIK -------------------------------------------------------

def wczytaj_stan() -> dict:
    """Stan albo pusty. BLEDU ODCZYTU NIE POLYKAMY tam, gdzie plik istnieje
    i jest uszkodzony - wyjatek wywraca bieg, a nie zamienia zapasu w zero
    (regula 7: pusty zapas po cichu to zapis 8 tysiecy falszywych wejsc)."""
    if not STAN_FILE.exists():
        return {"o": {}, "przelot": {}}
    d = json.loads(STAN_FILE.read_text(encoding="utf-8"))
    d.setdefault("o", {})
    d.setdefault("przelot", {})
    return d


def zapisz_stan(stan: dict):
    STAN_FILE.write_text(json.dumps(stan, ensure_ascii=False), encoding="utf-8")


def plik_dziennika(teraz: str = None) -> Path:
    """Kawalek miesieczny, liczony WZGLEDEM `ZDARZENIA_DIR`. Nazwa nie jest
    wpisana na sztywno, bo testy podstawiaja tam wlasny katalog - sztywna
    nazwa robilaby z kazdej piaskownicy zapis do katalogu bota."""
    msc = (teraz or datetime.now().strftime("%Y-%m"))[:7]
    return ZDARZENIA_DIR / f"polka-{msc}.jsonl"


def dopisz_zdarzenia(zdarzenia: list, teraz: str = None):
    if not zdarzenia:
        return
    ZDARZENIA_DIR.mkdir(exist_ok=True)
    with plik_dziennika(teraz).open("a", encoding="utf-8") as f:
        for z in zdarzenia:
            f.write(json.dumps(z, ensure_ascii=False) + "\n")


def wczytaj_zdarzenia() -> list:
    """Wszystkie kawalki po kolei, od najstarszego. Czytnik patrzacy na jeden
    kawalek dostalby ulamek danych i NIE KRZYKNALBY - wynik nadal wygladalby
    wiarygodnie (18.09.2026, dziennik rynku)."""
    out = []
    if not ZDARZENIA_DIR.exists():
        return out
    for plik in sorted(ZDARZENIA_DIR.glob("polka-????-??.jsonl")):
        for linia in plik.read_text(encoding="utf-8").splitlines():
            if not linia.strip():
                continue
            try:
                out.append(json.loads(linia))
            except json.JSONDecodeError:
                continue
    return out


def pasmo_ogloszenia(rec):
    """Do ktorego pasma trafia to ogloszenie. None, gdy nie wiadomo.

    Nieznana cena albo nieznana marka znaczy "nie wiem, gdzie mialoby byc",
    a nie "nie ma go" - dokladnie jak nieznana cena w `przeplyw.pasmo_ceny`."""
    p, marka = rec.get("p"), rec.get("marka")
    if not isinstance(p, int) or not marka:
        return None
    for m, lo, hi, _ in PASMA:
        if m == marka and lo <= p <= hi:
            return klucz_pasma(m, lo, hi)
    return None


def dni_niepelne(ev=None) -> set:
    """Doby, ktorych zejsc NIE wolno czytac jako faktow.

    Przelot jest tu WZNAWIALNY, wiec odpowiednikiem "przelotu niepelnego"
    z `przeplyw.py` jest DOBA, w ktorej zapas nigdy nie przeszedl w calosci.
    W takiej dobie czesc pasm nie byla pobrana ani razu, wiec jej "wyszla"
    moze znaczyc "nie pobralismy tego". Wpisy zostaja w dzienniku, bo
    dziennika sie nie kasuje - to warstwa wnioskow ma je pomijac i mowic
    o tym w raporcie."""
    ev = ev if ev is not None else wczytaj_zdarzenia()
    byly, pelne = set(), set()
    for z in ev:
        if z.get("ev") != "przebieg":
            continue
        d = z.get("dzien") or (z.get("ts") or "")[:10]
        byly.add(d)
        if z.get("pelny"):
            pelne.add(d)
    return byly - pelne


def juz_dzis(ev=None) -> bool:
    """Czy zapas przeszedl dzis W CALOSCI.

    Czyta DZIENNIK, nie stan - roznica jest cala. Stan dostaje dzisiejsza
    date takze po przebiegu czesciowym, wiec bieg urwany w polowie zaliczylby
    dobe i zablokowal kolejne (27.09.2026 po stronie OLX zly przelot o 00:39
    zablokowal trzy kolejne biegi komunikatem "polka juz przeszla dzis")."""
    dzis = date.today().isoformat()
    ev = ev if ev is not None else wczytaj_zdarzenia()
    return any(z.get("ev") == "przebieg" and z.get("pelny")
               and (z.get("dzien") or (z.get("ts") or "")[:10]) == dzis
               for z in ev)


# --- PRZELOT ---------------------------------------------------------------

def spisz(teraz: str = None, budzet_stron: int = None, pobierz=None,
          pauza=None) -> dict:
    """Przechodzi zapas, porownuje z poprzednim stanem, dopisuje fakty.

    PRZELOT JEST WZNAWIALNY W OBREBIE DOBY. 344 strony przy tempie pod
    progiem dlawienia to ~75 minut, a bieg moze zostac uciety (limit zadania,
    dlawienie, awaria). Pasmo przejste DO KONCA zostaje odhaczone w stanie
    i nastepny bieg tej samej doby bierze tylko te, ktorych jeszcze nie bylo.
    Doba zalicza sie, gdy wszystkie pasma przeszly w TEJ SAMEJ dobie - inaczej
    "brak" z pasma nieprzejstego mieszalby sie z "brakiem" z pasma przejstego.

    BRAK OGLOSZENIA JEST FAKTEM TYLKO W PASMIE PRZEJSTYM DO KONCA W TEJ
    DOBIE. W pasmie, ktorego serwis nie oddal, brak znaczy "nie wiem" i nie
    rusza ani licznika brakow, ani zejsc.

    LICZNIK BRAKU PODBIJA SIE RAZ NA DOBE, nie raz na bieg. Bez tego dwa biegi
    tej samej doby zdjelyby ogloszenie z polki w ciagu jednego dnia, choc
    `BRAKI_DO_ZEJSCIA` ma znaczyc dwie DOBY z rzedu."""
    teraz = teraz or datetime.now().strftime("%Y-%m-%dT%H:%M")
    dzis = teraz[:10]
    budzet = BUDZET_STRON if budzet_stron is None else budzet_stron
    stan = wczytaj_stan()
    o = stan["o"]
    przelot = stan["przelot"]
    if przelot.get("dzien") != dzis:
        przelot = {"dzien": dzis, "pasma": {}}
    zrobione = przelot["pasma"]

    zdarzenia, ostrz = [], []
    stron_razem = 0
    pasm_w_biegu = 0
    for marka, lo, hi, _ in PASMA:
        k = klucz_pasma(marka, lo, hi)
        if k in zrobione:
            continue
        if stron_razem >= budzet:
            break
        biezace, w, pelne, stron = przejdz_pasmo(marka, lo, hi, pobierz, pauza)
        ostrz += w
        stron_razem += stron
        pasm_w_biegu += 1
        zdarzenia.append({"ts": teraz, "dzien": dzis, "ev": "pasmo", "pasmo": k,
                          "kart": len(biezace), "stron": stron, "pelne": pelne})
        if pelne:
            zrobione[k] = teraz
        for oid, karta in biezace.items():
            f = fakty_z_karty(karta, marka)
            rec = o.get(oid)
            if rec is None:
                o[oid] = {**f, "p0": f["p"], "pierwszy": teraz, "ostatni": teraz,
                          "braki": 0}
                zdarzenia.append({
                    "ts": teraz, "dzien": dzis, "ev": "weszla", "id": oid,
                    "t": f["t"], "p": f["p"], "marka": marka, "loc": f["loc"],
                    "wyst": f["wyst"],
                    # Wiek w chwili wejscia w NASZE pole widzenia. Pierwszy
                    # przelot to inwentaryzacja, nie naplyw: ogloszenie
                    # wystawione trzy tygodnie temu nie weszlo dzis na rynek.
                    "wiek_przy_wejsciu": _wiek_dni(
                        f["wyst"], datetime.strptime(teraz, "%Y-%m-%dT%H:%M")),
                })
            else:
                if f["p"] is not None and rec.get("p") != f["p"]:
                    zdarzenia.append({"ts": teraz, "dzien": dzis, "ev": "cena",
                                      "id": oid, "p": f["p"],
                                      "p_stara": rec.get("p"),
                                      "p0": rec.get("p0")})
                if f["rez"] and not rec.get("rez"):
                    zdarzenia.append({"ts": teraz, "dzien": dzis, "ev": "rez",
                                      "id": oid, "rez": True})
                stare_p0 = rec.get("p0")
                km, rama, czytane = rec.get("km"), rec.get("rama"), rec.get("czytane")
                km_skad = rec.get("km_skad")
                rec.update(f)
                # Pierwsza cena i odczyty ze strony ogloszenia ZOSTAJA -
                # nadpisanie ich kartą z listy skasowalo by jedyny slad targu.
                rec["p0"] = stare_p0 if stare_p0 is not None else f["p"]
                # ODCZYT ZE STRONY WYGRYWA Z TYTULEM, bo kosztowal strone
                # z budzetu 40 na dobe. Brak `km_skad` liczy sie tu JAKO
                # strona, nie jako tytul: tak wygladaja wpisy sprzed tej
                # zmiany i zaniżenie ich do tytulu skasowalo by odczyty,
                # za ktore juz zaplacilismy ruchem.
                # Tytul nadpisuje tylko tytul - sprzedawca potrafi poprawic
                # liczbe w tytule, a wtedy swiezszy odczyt jest lepszy.
                if km is not None and (km_skad != "tytul" or f["km"] is None):
                    rec["km"] = km
                    rec["km_skad"] = km_skad
                if rama is not None:
                    rec["rama"] = rama
                if czytane is not None:
                    rec["czytane"] = czytane
                rec["ostatni"] = teraz
                rec["braki"] = 0
                rec.pop("brak_dzien", None)

    # --- NIEOBECNI -------------------------------------------------------
    pominiete = 0
    for oid in list(o):
        rec = o[oid]
        if (rec.get("ostatni") or "")[:10] == dzis:
            continue                       # widziany dzis
        if pasmo_ogloszenia(rec) not in zrobione:
            pominiete += 1                 # jego pasma dzis nie przeszlismy
            continue
        if rec.get("brak_dzien") == dzis:
            continue                       # licznik braku juz dzis podbity
        rec["braki"] = rec.get("braki", 0) + 1
        rec["brak_dzien"] = dzis
        if rec["braki"] < BRAKI_DO_ZEJSCIA:
            continue                       # jeden brak to jeszcze nie zejscie
        dt = datetime.strptime(teraz, "%Y-%m-%dT%H:%M")
        zdarzenia.append({"ts": teraz, "dzien": dzis, "ev": "wyszla", "id": oid,
                          "t": rec.get("t"), "p": rec.get("p"),
                          "p0": rec.get("p0"), "marka": rec.get("marka"),
                          "loc": rec.get("loc"), "wyst": rec.get("wyst"),
                          "rez": rec.get("rez"),
                          "wiek": wiek_ogloszenia(rec, dt),
                          "u_nas_dni": _wiek_dni(rec.get("pierwszy"), dt)})
        o.pop(oid)

    pelny = len(zrobione) == len(PASMA)
    zdarzenia.append({"ts": teraz, "dzien": dzis, "ev": "przebieg",
                      "pelny": pelny, "pasm_w_biegu": pasm_w_biegu,
                      "pasm_zrobionych": len(zrobione), "pasm": len(PASMA),
                      "stron": stron_razem, "w_zapasie": len(o),
                      "pominiete_bo_pasmo_nieprzejste": pominiete})
    dopisz_zdarzenia(zdarzenia, teraz)
    stan["przelot"] = przelot
    zapisz_stan(stan)
    return {"ok": True, "pelny": pelny, "w_zapasie": len(o),
            "stron": stron_razem, "pasm_zrobionych": len(zrobione),
            "pasm": len(PASMA),
            "weszlo": sum(1 for z in zdarzenia if z["ev"] == "weszla"),
            "wyszlo": sum(1 for z in zdarzenia if z["ev"] == "wyszla"),
            "zmian_ceny": sum(1 for z in zdarzenia if z["ev"] == "cena"),
            "pominiete_bo_pasmo_nieprzejste": pominiete,
            "ostrzezenia": ostrz}


# --- KOLEJKA SZCZEGOLOW ---------------------------------------------------
# Strona ogloszenia jest DROGA: glowny skan pobiera ich dzis ~111 na dobe
# i to jest sufit przy zmierzonym dlawieniu. Zapas ma ich 8 tysiecy, wiec
# doczytanie wszystkiego nie wchodzi w rachunek - potrzebna jest kolejka
# z budzetem i z kolejnoscia, ktora ma uzasadnienie.

def kolejka_szczegolow(stan=None, topowe=None, budzet=None, teraz=None) -> list:
    """Kogo doczytac najpierw. Zwraca [(id, rec), ...], najwyzej `budzet`.

    KOLEJNOSC, i kazdy jej stopien ma powod:
      1. ogloszenie jeszcze nie czytane (drugi odczyt tego samego nic nie
         doklada, dopoki cena sie nie ruszyla),
      2. ogloszenie BEZ PRZEBIEGU przed tym, ktory przebieg podal w tytule.
         ZMIERZONE 23.10.2026 na pierwszym zapasie: 1 124 z 8 479 ogloszen
         ma przebieg w tytule, czyli budzet 40 stron na dobe szedl w 13%
         przypadkow po liczbe, ktora i tak znamy. Rozmiar ramy i plakietka
         rezerwacji sa dalej tylko na stronie, wiec taki wpis z kolejki NIE
         wypada - schodzi za tych, u ktorych brakuje rzeczy drozszej,
      3. model z `topowe_modele.json` przed resztą - to te rowery, przy
         ktorych przebieg i rozmiar decyduja o zysku, a nie o ciekawostce,
      4. najdluzej WISZACE przed swiezymi - bo o to w tym module chodzi:
         ogloszenie stojace trzy tygodnie jest tam, gdzie da sie targowac,
         a swieze i tak zobaczy glowny skan.

    Ogloszenie bez znacznika wystawienia idzie na KONIEC swojej grupy, a nie
    na poczatek: nie wiemy, czy stoi dlugo, a zgadywanie z naszego pierwszego
    widzenia to dokladnie ten blad, ktory zanizal czas stania dwukrotnie."""
    import najlepsze                        # tylko tutaj - kolejka go potrzebuje
    stan = stan if stan is not None else wczytaj_stan()
    o = stan.get("o") or {}
    topowe = topowe if topowe is not None else najlepsze.load_topowe()
    budzet = BUDZET_SZCZEGOLOW if budzet is None else budzet
    teraz = teraz or datetime.now()
    czeka = []
    for oid, rec in o.items():
        if rec.get("czytane"):
            continue
        if not rec.get("url"):
            continue
        top = najlepsze.pietro_modelu(rec.get("t") or "", topowe) is not None
        wiek = wiek_ogloszenia(rec, teraz)
        czeka.append((0 if not rec.get("km") else 1, 0 if top else 1,
                      -(wiek if wiek is not None else -1), oid, rec))
    czeka.sort(key=lambda x: (x[0], x[1], x[2], x[3]))
    return [(oid, rec) for _, _, _, oid, rec in czeka[:budzet]]


def doczytaj_szczegoly(budzet=None, teraz=None, czytaj=None, pauza=None) -> dict:
    """Doczytuje przebieg, rozmiar i flage rezerwacji z STRON OGLOSZEN.

    Zapisuje FAKTY: przebieg, rozmiar ramy, rezerwacja, cena ze strony. Czego
    NIE zapisuje: zadnej oceny, czy to okazja - to liczy `przelicz()`.

    Nieudany odczyt NIE jest zapisywany jako brak danych na zawsze: wpis
    dostaje `czytane` tylko wtedy, gdy strona dala sie przeczytac. Inaczej
    jedno dlawienie zamykalo by rower na stale (ta sama choroba, co licznik
    nieudanych odczytow, ktory nie rosl - 20.09.2026)."""
    czytaj = czytaj or T.czytaj_ogloszenie
    pauza = PAUZA_S if pauza is None else pauza
    teraz = teraz or datetime.now().strftime("%Y-%m-%dT%H:%M")
    stan = wczytaj_stan()
    kolejka = kolejka_szczegolow(stan, budzet=budzet)
    zdarzenia, przeczytane, nieudane = [], 0, 0
    for i, (oid, _) in enumerate(kolejka):
        rec = stan["o"].get(oid)
        if rec is None:
            continue
        if i:
            time.sleep(pauza)
        km, opis, cena, _zdjecia, wynik, _meta = czytaj(rec["url"], rec.get("t") or "")
        if wynik != "ok":
            nieudane += 1
            rec["odczyty_nieudane"] = rec.get("odczyty_nieudane", 0) + 1
            continue
        rama = T.rozmiar_ramy(T.opis_z_polami(opis or ""), rec.get("t") or "")
        rez = T.czy_zarezerwowane(opis or "", rec.get("t") or "", opis or "")
        rec["czytane"] = teraz
        if km and km != "brak danych":
            rec["km"] = km
            rec["km_skad"] = "strona"
        if rama:
            rec["rama"] = rama
        if rez is not None:
            rec["rez"] = rez
        if isinstance(cena, int):
            rec["p_strona"] = cena
        przeczytane += 1
        zdarzenia.append({"ts": teraz, "dzien": teraz[:10], "ev": "szczegoly",
                          "id": oid, "km": rec.get("km"), "rama": rec.get("rama"),
                          "rez": rec.get("rez"), "p_strona": rec.get("p_strona")})
    dopisz_zdarzenia(zdarzenia, teraz)
    zapisz_stan(stan)
    return {"ok": True, "w_kolejce": len(kolejka), "przeczytane": przeczytane,
            "nieudane": nieudane, "stron_pobranych": len(kolejka)}


# --- PRZELICZENIE ZAPASU DZISIEJSZA REGULA --------------------------------

def _mediany_zapasu(o: dict) -> dict:
    """Mediana ceny w obrebie marki, do bramki `nisza`. Z zapasu, bo tylko on
    obejmuje cala polke tych marek - a nie z jednego skanu."""
    ceny = {}
    for rec in o.values():
        if isinstance(rec.get("p"), int) and rec.get("marka"):
            ceny.setdefault(rec["marka"], []).append(rec["p"])
    return {m: statistics.median(v) for m, v in ceny.items() if v}


def przelicz(stan=None) -> dict:
    """Ocenia ZAPISANY zapas DZISIEJSZA regula - bez pobierania czegokolwiek.

    PO CO. Kazda zmiana reguly dziala dzis tylko w przod: wpis w `seen.json`
    jest terminalny, a stojace ogloszenie nie wraca na swieza liste, wiec
    rower odrzucony stara regula milczy na zawsze. Ten tryb odpowiada na
    pytanie "ktore z 8 tysiecy stojacych ofert przeszlyby bramki, gdyby bot
    patrzyl na nie dzisiaj".

    CZEGO TO NIE OBEJMUJE - nie udawaj, ze obejmuje. Kaskada jest ta sama,
    ktora liczy `sprawdz_zachowanie.werdykt`, czyli bramki stojace PRZED
    pobraniem strony ogloszenia. Poza zasiegiem zostaja: filtr silnika
    z opisu, przebieg, mala bateria, Levo FSR, dedup re-listingu i cala
    punktacja. `dociera` znaczy "doszloby do pobrania strony", a NIE
    "tyle wiadomosci by poszlo" - i dlatego `--wyslij` ma wlasny sufit.

    Bramka `nisza` niesie JEDNO wejscie odtworzone: produkcja porownuje cene
    z mediana DANEGO SKANU, a tu liczymy ja z zapasu w obrebie marki."""
    import sprawdz_zachowanie as SZ
    stan = stan if stan is not None else wczytaj_stan()
    o = stan.get("o") or {}
    med = _mediany_zapasu(o)
    bramki = Counter()
    dociera = []
    for oid, rec in o.items():
        w = SZ.werdykt({"t": rec.get("t") or "", "p": rec.get("p")},
                       med.get(rec.get("marka")))
        bramki[w] += 1
        if w == SZ.DOCIERA:
            dociera.append((oid, rec))
    wyslane = wczytaj_wyslane()
    nowe = [(oid, rec) for oid, rec in dociera if oid not in wyslane]
    return {"w_zapasie": len(o), "bramki": dict(bramki),
            "dociera": len(dociera), "do_wyslania": len(nowe),
            "juz_wyslane": len(dociera) - len(nowe), "oferty": nowe}


def wczytaj_wyslane() -> dict:
    if not WYSLANE_FILE.exists():
        return {}
    try:
        return json.loads(WYSLANE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def wiadomosc_z_zapasu(oid, rec, teraz=None) -> str:
    """Jedna oferta z zapasu, po ludzku. KAZDA LICZBA MA ETYKIETE (regula 6):
    wiek stania jest ZMIERZONY, gdy znamy znacznik wystawienia, i NIEZNANY,
    gdy go nie ma - nigdy podstawiony z naszego pierwszego widzenia."""
    teraz = teraz or datetime.now()
    wiek = wiek_ogloszenia(rec, teraz)
    L = [f"🗄 <b>{rec.get('t') or 'bez tytulu'}</b>",
         f"Cena: <b>{rec.get('p')} €</b>" if rec.get("p") else "Cena: nie podano"]
    if rec.get("p0") and rec.get("p") and rec["p0"] != rec["p"]:
        L.append(f"Pierwsza cena u nas: {rec['p0']} € "
                 f"(zeszlo {rec['p0'] - rec['p']} €)")
    L.append(f"Stoi: <b>{wiek} dni</b> od wystawienia" if wiek is not None
             else "Stoi: <b>nie wiem</b> (ogloszenie nie podalo daty)")
    if rec.get("loc"):
        L.append(f"Gdzie: {rec['loc']}")
    if rec.get("km"):
        L.append(f"Przebieg: {rec['km']}")
    if rec.get("rama"):
        L.append(f"Rama: {rec['rama']}")
    if rec.get("rez"):
        L.append("⚠️ Sprzedawca oznaczyl jako zarezerwowane")
    L.append(f"\n{rec.get('url')}")
    L.append("\n<i>ⓘ To oferta z ZAPASU, nie ze swiezej polki - stoi juz "
             "jakis czas, wiec jest miejsce na targ. Bot nie czytal jeszcze "
             "strony pod silnik i baterie, wiec to NIE jest ocena okazji.</i>"
             if not rec.get("czytane") else
             "\n<i>ⓘ Oferta z ZAPASU - stoi juz jakis czas, wiec jest miejsce "
             "na targ.</i>")
    return "\n".join(L)


def wyslij_z_zapasu(oferty, sufit=None, teraz=None, wyslij=None) -> dict:
    """Wysyla oferty z przeliczenia. Z WLASNYM SUFITEM i wlasnym stanem.

    Stan jest wlasny (`polka_de_wyslane.json`), a nie `seen.json`, i to jest
    decyzja, nie wygoda: `seen.json` to stan dedupu glownego skanu, zapisywany
    dziś WYLACZNIE przez tracker (podzial na kawalki, 19.09.2026, KROK
    PIERWSZY - czytamy z kawalkow, zapisujemy tylko do `seen.json`). Drugi
    pisarz do tego pliku znaczy albo lawine powtorek, albo cisze. Ten sam
    wzorzec co `best_wyslane.json` w kanale najlepszych."""
    wyslij = wyslij or T.send_telegram
    sufit = SUFIT_WYSYLKI if sufit is None else sufit
    teraz = teraz or datetime.now().strftime("%Y-%m-%dT%H:%M")
    stan_wyslane = wczytaj_wyslane()
    poszlo = 0
    for oid, rec in oferty[:sufit]:
        if wyslij(wiadomosc_z_zapasu(oid, rec)):
            stan_wyslane[oid] = {"kiedy": teraz, "p": rec.get("p")}
            poszlo += 1
    WYSLANE_FILE.write_text(json.dumps(stan_wyslane, ensure_ascii=False, indent=2),
                            encoding="utf-8")
    return {"wyslane": poszlo, "zostalo": max(len(oferty) - poszlo, 0),
            "sufit": sufit}


# --- WNIOSKI ---------------------------------------------------------------

def zapas(stan=None, teraz=None) -> dict:
    """Co stoi w zapasie TERAZ, w rozbiciu.

    UWAGA NA JEDNO, tak samo jak przy polce OLX: zbior stojacych ofert
    z natury przewaza wolno schodzace. Ogloszenie, ktore zeszlo w tydzien,
    widac w takim zdjeciu tylko tydzien, a wiszace rok - caly rok. Dlatego
    "mediana wieku stojacych" NIE JEST czasem sprzedazy i nie wolno jej tak
    podawac."""
    stan = stan if stan is not None else wczytaj_stan()
    o = stan.get("o") or {}
    if not o:
        return {}
    teraz = teraz or datetime.now()
    wieki = sorted(x for x in (wiek_ogloszenia(v, teraz) for v in o.values())
                   if x is not None)
    out = {
        "w_zapasie": len(o),
        "z_data_wystawienia": len(wieki),
        "bez_daty_wystawienia": len(o) - len(wieki),
        "marki": dict(Counter(v.get("marka") for v in o.values())),
        "przeczytanych_stron": sum(1 for v in o.values() if v.get("czytane")),
        "zarezerwowanych": sum(1 for v in o.values() if v.get("rez")),
    }
    if wieki:
        out["mediana_wieku_stojacych"] = int(statistics.median(wieki))
        out["starsze_niz_30_dni"] = sum(1 for x in wieki if x > 30)
        out["starsze_niz_90_dni"] = sum(1 for x in wieki if x > 90)
    # Kto zszedl z ceny, odkad go widzimy. To jest ta liczba, na ktorej stoi
    # caly pomysl targu - i jedyne miejsce w tym module, gdzie zapas mowi
    # cokolwiek o sprzedawcy.
    zeszli = [(v["p0"] - v["p"]) for v in o.values()
              if isinstance(v.get("p0"), int) and isinstance(v.get("p"), int)
              and v["p0"] > v["p"]]
    out["zeszlo_z_ceny"] = len(zeszli)
    if zeszli:
        out["mediana_zejscia_eur"] = int(statistics.median(zeszli))
    return out


def przeplyw(dni: int = 28) -> dict:
    """WNIOSEK z dziennika: ile wchodzi i wychodzi z zapasu na dobe.

    Pierwszy przelot wpisuje CALY zapas jako "weszla" - to nie naplyw, tylko
    inwentaryzacja. Dlatego wejscia z ogloszeniem starszym niz doba nie licza
    sie do naplywu: ogloszenie wystawione trzy tygodnie temu nie weszlo dzis
    na rynek, tylko dzis weszlo w nasze pole widzenia.

    Zejscia z DOB NIEPELNYCH sa pomijane w calosci - patrz `dni_niepelne`."""
    ev = wczytaj_zdarzenia()
    if not ev:
        return {"dni_danych": 0}
    zle = dni_niepelne(ev)
    od = (date.today() - timedelta(days=dni)).isoformat()
    swieze = [z for z in ev if (z.get("dzien") or (z.get("ts") or "")[:10]) >= od
              and z.get("ev") in ("weszla", "wyszla", "cena")]
    wejscia = [z for z in swieze if z["ev"] == "weszla"]
    nowe = [z for z in wejscia if isinstance(z.get("wiek_przy_wejsciu"), int)
            and z["wiek_przy_wejsciu"] <= 1]
    wyszly = [z for z in swieze if z["ev"] == "wyszla"
              and (z.get("dzien") or (z.get("ts") or "")[:10]) not in zle]
    odrzucone = sum(1 for z in swieze if z["ev"] == "wyszla"
                    and (z.get("dzien") or (z.get("ts") or "")[:10]) in zle)
    dni_obs = len({(z.get("dzien") or (z.get("ts") or "")[:10]) for z in ev
                   if z.get("ev") == "przebieg" and z.get("pelny")
                   and (z.get("dzien") or (z.get("ts") or "")[:10]) >= od})
    wieki = sorted(z["wiek"] for z in wyszly if isinstance(z.get("wiek"), int))
    przeceny = [z for z in swieze if z["ev"] == "cena"
                and isinstance(z.get("p"), int) and isinstance(z.get("p_stara"), int)
                and z["p"] < z["p_stara"]]
    zejscia_eur = sorted(z["p_stara"] - z["p"] for z in przeceny)
    return {
        "dni_danych": dni_obs,
        "nowe_ogloszenia": len(nowe),
        "nowe_na_dobe": round(len(nowe) / dni_obs, 1) if dni_obs else None,
        "wejscia_starych": len(wejscia) - len(nowe),
        "zejscia": len(wyszly),
        "zejscia_na_dobe": round(len(wyszly) / dni_obs, 1) if dni_obs else None,
        "zejscia_odrzucone": odrzucone,
        # Mediana z mniej niz 10 obserwacji nie jest mediana, tylko liczba -
        # ta sama granica co w `przeplyw.py` po stronie OLX.
        "mediana_wieku_zejscia": int(statistics.median(wieki)) if len(wieki) >= 10 else None,
        "przecen": len(przeceny),
        "mediana_przeceny_eur": (int(statistics.median(zejscia_eur))
                                 if len(zejscia_eur) >= 10 else None),
    }


def raport() -> str:
    z = lambda v: f"{int(v):,}".replace(",", " ")
    sk = zapas()
    if not sk:
        return ("🗄 <b>Zapas polki DE</b>\nJeszcze nie przeszedlem zapasu. "
                "Uruchom <code>python3 polka_de.py</code>.")
    p = przeplyw()
    ev = wczytaj_zdarzenia()
    L = ["🗄 <b>Zapas polki Kleinanzeigen: co STOI, nie co weszlo</b>",
         "<i>szesc marek premium, kategoria e-bike, 800-3000 € - cala polka "
         "tych marek, nie probka</i>", ""]
    L.append(f"W zapasie stoi <b>{z(sk['w_zapasie'])}</b> ogloszen")
    if sk.get("mediana_wieku_stojacych") is not None:
        L.append(f"· mediana wieku: <b>{sk['mediana_wieku_stojacych']} dni</b> "
                 f"od wystawienia")
        L.append(f"· stoi dluzej niz 30 dni: {z(sk['starsze_niz_30_dni'])}, "
                 f"dluzej niz 90 dni: {z(sk['starsze_niz_90_dni'])}")
    if sk.get("bez_daty_wystawienia"):
        L.append(f"· bez daty wystawienia: {z(sk['bez_daty_wystawienia'])} "
                 f"(nie wiemy, jak dlugo stoja - nie podstawiamy wlasnej daty)")
    if sk.get("zeszlo_z_ceny"):
        L.append(f"· zeszlo z ceny, odkad patrzymy: <b>{z(sk['zeszlo_z_ceny'])}</b>"
                 + (f", mediana zejscia {sk['mediana_zejscia_eur']} €"
                    if sk.get("mediana_zejscia_eur") else ""))
    L.append(f"· stron ogloszen doczytanych: {z(sk['przeczytanych_stron'])} "
             f"z {z(sk['w_zapasie'])}")
    L.append("\n<i>ⓘ Mediana wieku STOJACYCH ofert to nie czas sprzedazy. "
             "Szybko schodzace widac w takim zdjeciu tylko przez chwile, "
             "a wiszace rok widac caly rok.</i>")
    biegi = [x for x in ev if x.get("ev") == "przebieg"]
    if biegi and not biegi[-1].get("pelny"):
        L.append(f"\n⚠️ <b>Ostatni przebieg NIE domknal zapasu</b> "
                 f"({biegi[-1].get('pasm_zrobionych')} z {biegi[-1].get('pasm')} "
                 f"pasm) - jego zejscia sa pominiete, reszta pasm idzie "
                 f"w nastepnym biegu.")
    if p.get("dni_danych", 0) < 2:
        L.append(f"\n<i>ⓘ Przeplywu jeszcze nie ma: {p.get('dni_danych', 0)} doba "
                 f"pomiaru. Pierwszy przelot to inwentaryzacja, nie naplyw.</i>")
        return "\n".join(L)
    L += ["", f"<b>Przeplyw z {p['dni_danych']} dob</b>",
          f"· nowych ogloszen: {z(p['nowe_ogloszenia'])} ({p['nowe_na_dobe']} na dobe)",
          f"· zejsc z zapasu: {z(p['zejscia'])} ({p['zejscia_na_dobe']} na dobe)"]
    if p["mediana_wieku_zejscia"] is not None:
        L.append(f"· mediana wieku przy zejsciu: <b>{p['mediana_wieku_zejscia']} dni</b>")
    if p["przecen"]:
        L.append(f"· przecen: {z(p['przecen'])}"
                 + (f", mediana {p['mediana_przeceny_eur']} €"
                    if p.get("mediana_przeceny_eur") else
                    " (za malo na mediane)"))
    if p["wejscia_starych"]:
        L.append(f"\n<i>ⓘ {z(p['wejscia_starych'])} ogloszen weszlo w pole "
                 f"widzenia, ale bylo starszych niz doba - to nie naplyw, "
                 f"tylko nadrobienie zaleglosci, i nie jest liczone.</i>")
    if p.get("zejscia_odrzucone"):
        L.append(f"\n<i>ⓘ {z(p['zejscia_odrzucone'])} zapisanych zejsc pominieto: "
                 f"pochodza z dob, w ktorych zapas nie przeszedl w calosci, "
                 f"wiec ich \"brak\" znaczy \"nie pobralismy\".</i>")
    L.append("\n<i>ⓘ Zejscie z zapasu to nie sprzedaz. Moze byc sprzedaz, "
             "wygasniecie, zdjecie albo zmiana ceny poza widelki.</i>")
    return "\n".join(L)


def _bez_html(s: str) -> str:
    import re
    return re.sub(r"</?(b|i|code)>", "", s)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--raport", action="store_true", help="co stoi i co schodzi")
    ap.add_argument("--szczegoly", action="store_true",
                    help="doczytaj przebieg i rozmiar z kolejki")
    ap.add_argument("--przelicz", action="store_true",
                    help="ocen zapas DZISIEJSZA regula (na sucho)")
    ap.add_argument("--wyslij", action="store_true",
                    help="z --przelicz: wyslij oferty na Telegram")
    ap.add_argument("--tak", action="store_true",
                    help="swiadoma zgoda na wysylke; bez niej --wyslij nie wysyla")
    ap.add_argument("--raz-na-dobe", action="store_true",
                    help="nic nie rob, gdy zapas przeszedl dzis w calosci")
    ap.add_argument("--budzet-stron", type=int, default=None)
    ap.add_argument("--budzet-szczegolow", type=int, default=None)
    a = ap.parse_args()

    if a.raport:
        print(_bez_html(raport()))
    elif a.szczegoly:
        print(json.dumps(doczytaj_szczegoly(budzet=a.budzet_szczegolow),
                         ensure_ascii=False))
    elif a.przelicz:
        w = przelicz()
        print(f"W zapasie: {w['w_zapasie']}")
        for bramka, ile in sorted(w["bramki"].items(), key=lambda x: -x[1]):
            print(f"  {bramka:16} {ile}")
        print(f"\nDociera do pobrania strony: {w['dociera']}")
        print(f"Juz wyslane z zapasu:       {w['juz_wyslane']}")
        print(f"DO WYSLANIA:                {w['do_wyslania']} wiadomosci")
        if not a.wyslij:
            print("\nTo byla PROBA NA SUCHO - nic nie wyszlo. Wysylka: "
                  "--przelicz --wyslij --tak")
        elif not a.tak:
            print(f"\n--wyslij BEZ --tak: nic nie wyszlo. Powyzsza liczba "
                  f"({w['do_wyslania']}) to tyle wiadomosci, ile przyjdzie na "
                  f"telefon, po {SUFIT_WYSYLKI} na jedno uruchomienie. "
                  f"Dodaj --tak, jesli o to chodzi.")
        else:
            print(json.dumps(wyslij_z_zapasu(w["oferty"]), ensure_ascii=False))
    elif a.raz_na_dobe and juz_dzis():
        print(json.dumps({"ok": True, "pominiete": "zapas juz przeszedl dzis"},
                         ensure_ascii=False))
    else:
        w = spisz(budzet_stron=a.budzet_stron)
        print(json.dumps({k: v for k, v in w.items() if k != "ostrzezenia"},
                         ensure_ascii=False))
        for o in w.get("ostrzezenia") or []:
            print("UWAGA:", o, file=sys.stderr)
