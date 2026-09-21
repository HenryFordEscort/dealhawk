import re
import os
import sys
import math
import hashlib
import json
import time
import html as html_mod
import logging
import statistics
import cloudscraper

# Wspólny klient OLX — ten sam plik obsługuje bota rowerowego i samochodowego,
# żeby poprawka trafiała od razu do obu. Boty NIE importują się nawzajem.
from olx import (OLX_HEADERS, OLX_RELAY_KEY, OLX_RELAY_URL, olx_diag,
                 olx_diag_reset, olx_get, parse_olx_ad_json, parse_olx_cards,
                 przekaznik_zyje, zglos_pusta_strone)

# Druga giełda zakupowa (Austria). Cały jej HTML i JSON siedzi w osobnym
# pliku — tak jak OLX — żeby zmiana po ich stronie nie wymagała dotykania
# logiki rowerowej. Stąd bot bierze WYŁĄCZNIE ogłoszenia; filtry, wycena,
# powiadomienia i dzienniki są wspólne dla obu giełd.
import willhaben
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# Kleinanzeigen podaje czas wystawienia w czasie niemieckim, a runner GitHuba
# chodzi na UTC — bez przeliczenia każdy wiek ogłoszenia byłby o 2 h zawyżony.
try:
    from zoneinfo import ZoneInfo
    TZ_DE = ZoneInfo("Europe/Berlin")
except Exception:                       # brak bazy stref (goły obraz) — CEST
    TZ_DE = timezone(timedelta(hours=2))

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
# WYŁĄCZONE ŚWIADOMIE (23.08.2026): bot nie ma kosztować ani grosza.
# Czytanie przebiegu przez Claude Haiku kosztowało ~35 zł/mies. przy ~300
# ogłoszeniach dziennie, a na sprawdzonej próbce i tak pudłowało — Cannondale
# Moterra miał w opisie "10.328 km", model zwrócił null i rower po 10 tys. km
# przeszedł jako kandydat. Wzorce znajdują ten przebieg bez trudu.
# Aby wrócić: ustaw sekret ANTHROPIC_API_KEY w GitHubie i dopisz go do
# .github/workflows/tracker.yml — kod jest gotowy i nietknięty.
ANTHROPIC_API_KEY = os.environ.get("DEALHAWK_ANTHROPIC_KEY")

MIN_PRICE = 800
# Sufit podniesiony 2500 → 3000 € (25.08.2026, decyzja właściciela).
# Powód ZMIERZONY na 15 620 ogłoszeniach z 22-24.08: w starych widełkach
# 800-2500 € tylko 4,0% ogłoszeń to w ogóle kandydat (marka premium + fully
# + elektryk), a w paśmie 2500-3000 € już 9,7% — dwa i pół raza gęściej.
# Jakość też: mediana rocznika 2023 przy 786 km wobec 2023 przy 1400 km niżej.
# Wyżej nie idziemy: SZACOWANY (nie zmierzony) zysk spada o 266 zł na każde
# +100 € ceny zakupu i zeruje się przy ~3600 €, a szacunek bota zawyżał
# na jedynej realnej sprzedaży czterokrotnie — więc realny próg jest niżej.
MAX_PRICE = 3000
MAX_MILEAGE = 3000

# --- TRYB PĘTLI (opcjonalny, domyślnie WYŁĄCZONY) --------------------------
# 0 = jeden skan i koniec (testy i ręczne odpalenia). Produkcja chodzi w pętli
# 8 minut — patrz tracker.yml: harmonogram GitHuba ma p90 = 10,4 min odstępu
# i maksimum 25 min, więc tempa nie da się na nim oprzeć. Odstęp między
# skanami wewnątrz pętli jest ADAPTACYJNY (TEMPO_* niżej), a PETLA_ODSTEP_S
# służy już tylko za wartość awaryjną, gdyby stan tempa był pusty.
PETLA_MINUT = int(os.environ.get("DEALHAWK_PETLA_MINUT", "0"))
PETLA_ODSTEP_S = int(os.environ.get("DEALHAWK_ODSTEP_S", "60"))

# --- TEMPO ADAPTACYJNE -----------------------------------------------------
# Cel: skrócić średni czas do powiadomienia, NIGDY nie wpadając na stronę-śmieć.
# Bot skanuje coraz gęściej, dopóki serwis to znosi, a gdy zobaczy podstawioną
# stronę — natychmiast się cofa i długo nie próbuje wracać.
#
# Dlaczego cofnięcie musi być brutalne, a nie stopniowe (zmierzone 23.08):
# ~50 żądań w 10 minut wpędziło ten adres w stronę-śmieć, a potem SIEDEMNAŚCIE
# pobrań przez 17 minut, po jednym na minutę, nie odblokowało go ani razu.
# Kara nie mija od pojedynczego zwolnienia — trzeba naprawdę zamilknąć.
TEMPO_MAX_S = 300      # sufit = dzisiejsza produkcja; gorzej niż dziś nie będzie
TEMPO_DNO_S = 60       # najgęściej, na ile pozwalamy na starcie
TEMPO_START_S = 180    # zaczynamy ostrożnie i schodzimy w dół
TEMPO_KROK_S = 15      # po każdym czystym skanie o tyle gęściej
TEMPO_KARENCJA = 10    # tyle czystych skanów na pełnym odstępie po wpadce


# --- PEWNOŚĆ, ŻE NIC NIE PRZEPADŁO ----------------------------------------
# Cel użytkownika: "mamy mieć pewność co do działania tej technologii; żeby nie
# było opóźnienia ani pominięcia oferty dobrego roweru". Dwa niezależne
# sprawdzenia, oba DARMOWE — liczą to, co bot i tak już ma w ręku.
#
# Powód: dzisiejsza awaria (znacznik zamrożony od 8:35 do 22:34) trwała
# jedenaście godzin i NIC nie krzyczało, bo ogłoszenia przez cały czas jakoś
# płynęły. Cicha awaria jest gorsza od głośnej — musi mieć własny czujnik.
ZALEGLOSC_MIN = 45     # znacznik półki starszy niż tyle = jesteśmy w tyle


def sprawdz_pokrycie(zrodla, seen) -> list:
    """Ogłoszenia, które bot ZOBACZYŁ, a które nie mają zapisanej decyzji.

    Niezmiennik całego potoku: każde ogłoszenie z listy wychodzi z pętli albo
    jako powiadomienie, albo jako wpis z powodem odrzucenia. Jeśli którekolwiek
    zniknęło bez śladu, znaczy to, że pętla wywróciła się w połowie — i to
    jest dokładnie ten rodzaj cichej zguby, którego nie wolno przemilczeć."""
    zgubione = []
    for _, listings, _ in zrodla:
        for l in listings:
            if l.get("id") and l["id"] not in seen:
                zgubione.append(l)
    return zgubione


def sprawdz_zaleglosc(teraz=None) -> list:
    """Półki, których znacznik czasu stoi w miejscu — czyli jesteśmy w tyle.

    Skan może "się udać" i nic nie znaczyć: dziś bot przez jedenaście godzin
    czytał stronę 1, widział świeże ogłoszenia i zgłaszał "ok", a znacznik
    tkwił na 8:35 rano. Wiek znacznika jest jedyną liczbą, która to pokazuje."""
    spoznione = []
    for kan in POLKI:
        zn = load_feed_znacznik(kan["typ"])
        if not zn:
            continue
        wiek = ad_age_minutes(zn, teraz)
        if wiek is not None and wiek > ZALEGLOSC_MIN:
            spoznione.append((kan["nazwa"], wiek))
    return spoznione


WATCHDOG_MIN = 20      # tyle minut bez skanu i skanujemy bez pytania o tempo


def _znacznik(stan: dict, klucz: str) -> float:
    """Znacznik czasu ze stanu. Bzdura w pliku = 0, czyli 'rób to teraz'."""
    try:
        return float(stan.get(klucz) or 0)
    except (TypeError, ValueError):
        return 0.0


def czy_pora_na_kluczowe(stan: dict, teraz=None) -> bool:
    """Zapytania kluczowe mają WŁASNY, rzadki zegar i nie wolno go przyspieszyć.

    To one raz już położyły kanał (5 żądań na skan → półka padała co drugi raz).
    Przy łańcuszku krótkich biegów bez tego zegara jechałyby przy każdym ogniwie,
    czyli kilka razy częściej niż dziś. Łapią za to 79 kandydatów, których półki
    nie widzą — dlatego zostają, tylko rzadko."""
    teraz = time.time() if teraz is None else teraz
    return (teraz - _znacznik(stan, "kluczowe_ts")) >= KLUCZOWE_CO_MIN * 60


def czy_pora_na_skan(stan: dict, teraz=None):
    """Czy ten bieg ma skanować? Zwraca (tak/nie, powód po ludzku).

    Brama istnieje, bo tempa nie nadaje już jeden długo żyjący bieg, tylko
    łańcuszek krótkich — każdy na własnym runnerze, czyli własnym adresie IP.
    To ta sama liczba żądań rozłożona na wiele adresów zamiast jednego, a
    zmierzone 23.08 dławienie jest właśnie per adres: produkcja z 3 żądaniami
    na bieg nie oberwała ani razu, a mój laptop po ~50 żądaniach dostał
    stronę-śmieć na 20 minut.

    ZASADA: przy każdej niepewności PRZEPUSZCZAMY. Zgubione powiadomienie
    kosztuje okazję, zbędny skan kosztuje jedno żądanie. Ta asymetria decyduje
    o każdym `return` poniżej — stąd czuwak, który po WATCHDOG_MIN minutach
    ciszy skanuje niezależnie od tempa i niezależnie od tego, co pokazuje stan.
    Bez niego jedna bzdura w pliku stanu mogłaby uciszyć bota na całą noc."""
    teraz = time.time() if teraz is None else teraz
    try:
        ostatni = _znacznik(stan, "ostatni_skan")
        tempo = int(stan.get("tempo_s") or TEMPO_START_S)
    except (TypeError, ValueError):
        return True, "stan nieczytelny — skanuję"
    if ostatni <= 0:
        return True, "pierwszy skan"
    minelo = teraz - ostatni
    if minelo < 0:
        return True, "zegar się cofnął — skanuję"
    if minelo >= WATCHDOG_MIN * 60:
        return True, f"czuwak: {int(minelo // 60)} min bez skanu"
    if minelo + 5 >= tempo:          # 5 s luzu — biegi nie startują co do sekundy
        return True, f"pora: {int(minelo)} s od ostatniego (tempo {tempo} s)"
    return False, f"za wcześnie: {int(minelo)} s z {tempo} s"


TEMPO_PROG_ZLYCH = 5   # tyle ZŁYCH POD RZĄD = kara; mniej to tło serwisu
TEMPO_LUZ = 30         # tyle czystych skanów z rzędu i dno wolno obniżyć


def tempo_po_skanie(zepsute: bool, stan: dict) -> dict:
    """Nowe tempo po skanie. Czysta funkcja — stan wchodzi i wychodzi.

    REAGUJEMY NA CZĘSTOŚĆ, NIE NA POJEDYNCZE ZDARZENIE. Zmierzone 23-24.08 na
    trzech oknach po ~120 skanów: podstawiona strona wraca ze stałą częstością
    ok. 1/3 — także wtedy, gdy bot chodził rzadko i grzecznie, i także w dniu,
    w którym nic nie zmienialiśmy. To jest TŁO tego serwisu, nie kara za nasz
    ruch. Pierwsza wersja cofała się po każdej takiej stronie i zerowała
    karencję, więc tempo nigdy nie zeszło z sufitu: bot był bezpieczny
    i bezużytecznie wolny naraz. Druga, licząca 3 złe z 5, wypadła w symulacji
    tak samo — przy tle 1/3 taka piątka trafia się co czwarte okno.

    Dlatego liczy się SERIA POD RZĄD. Przy tle 1/3 pięć z rzędu zdarza się
    losowo raz na ~200 skanów, więc jest wiarygodnym sygnałem prawdziwej kary,
    a nie szumem.

    Pojedyncza podstawiona strona kosztuje jedno żądanie i nic więcej — znacznik
    czasu zostaje nietknięty, więc następny skan przeczyta to samo okno. Karą
    jest dopiero SERIA."""
    t = int(stan.get("tempo_s") or TEMPO_START_S)
    dno = int(stan.get("tempo_dno") or TEMPO_DNO_S)
    kar = int(stan.get("tempo_karencja") or 0)
    czyste = int(stan.get("tempo_czyste") or 0)
    seria = (int(stan.get("tempo_seria") or 0) + 1) if zepsute else 0
    kara = seria >= TEMPO_PROG_ZLYCH

    if kara:
        if t <= dno + TEMPO_KROK_S:
            dno = min(TEMPO_MAX_S, dno + 30)     # ściana jest wyżej, niż sądziliśmy
        # Okno czyścimy: to była odpowiedź na TE złe skany i nie wolno ich
        # liczyć drugi raz, bo karencja odnawiałaby się w kółko i bot nigdy
        # by z niej nie wyszedł.
        t, kar, czyste, seria = TEMPO_MAX_S, TEMPO_KARENCJA, 0, 0
    elif kar > 0:
        kar, t = kar - 1, TEMPO_MAX_S
    else:
        czyste = 0 if zepsute else czyste + 1
        # Ściana potrafi się cofnąć — po długiej serii czystych skanów wolno
        # spróbować gęściej. Bez tego dno raz podniesione zostaje na zawsze,
        # a jedna zła godzina spowalniałaby bota do końca świata.
        if czyste >= TEMPO_LUZ and dno > TEMPO_DNO_S:
            dno, czyste = max(TEMPO_DNO_S, dno - 30), 0
        t = max(dno, t - TEMPO_KROK_S)
    return {"tempo_s": t, "tempo_dno": dno, "tempo_karencja": kar,
            "tempo_seria": seria, "tempo_czyste": czyste}
KLUCZOWE_CO_MIN = 5    # 23 zapytania kluczowe są drogie — nie co minutę
PUSH_CO_MIN = 5        # jak często commitować seen.json bez powiadomień
_ostatni_push = 0.0

SKIP_KEYWORDS = [
    "defekt", "bastler", "ersatzteile", "ersatzteil", "rahmen only",
    "schlachtfest", "unfall", "unfallschaden", "wasserschaden",
    "ohne motor", "ohne akku", "motor defekt", "akku defekt",
    # nie-fully / miejskie
    "hardtail", "hartail", "trekking", "city bike", "citybike",
    "lastenrad", "lastenfahrrad", "cargo", "faltrad", "klapprad", "faltbar",
    "tiefeinsteiger", "tiefeinstieg", "cityrad", "cruiser", "gravel",
    # same ramy
    "frameset", "frame only", "nur rahmen",
]

# Krótkie/ryzykowne słowa — wymagają granicy słowa, żeby nie łapać
# "Rahmengröße", "Cross Country", nazw modeli itp.
SKIP_PATTERNS = [
    r'\bht\b',            # hardtail w skrócie
    r'\brahmen\b',        # sama rama (ale NIE Rahmengröße/Rahmenhöhe)
    r'\bcross\b(?![\s-]?country)',  # rower crossowy (ale NIE Cross-Country)
    r'\burban\b',
    r'\bcomfort\b',
    r'\btouring\b',
]

# ZA DUŻA RAMA TO NIE ŚMIEĆ - rozdzielone 20.09.2026.
#
# `\bxl\b` i `\bxxl\b` stały w `SKIP_PATTERNS`, czyli w liście "to nie jest
# rower, tylko część". Ale XL opisuje ROWER KOMPLETNY, tylko za dużą ramę -
# to decyzja biznesowa o zbycie w Polsce, ta sama rodzina co odrzut ramy S,
# a nie rozpoznanie ogłoszenia o samej ramie.
#
# Pomylenie tych dwóch rzeczy kosztowało konkretnie: `is_junk` jest bramką
# ZAMKNIĘTĄ dla modeli z listy życzeń (uzasadnienie w CLAUDE.md brzmi
# dosłownie "ogłoszenie o samej ramie to nie rower"), więc obserwowany Cube
# Stereo Hybrid 160 TM w rozmiarze XL wypadał jako "śmieć" - wbrew
# wyraźnemu "zadbaj, żebym nie przegapił żadnego ogłoszenia".
#
# Zmierzone 20.09.2026 na 137 029 unikalnych ogłoszeniach z dziennika:
# 5 059 ma XL/XXL w tytule, a wśród 90 rowerów obserwowanego modelu jest
# ich 8 - czyli co dziewiąty egzemplarz tego, o co właściciel prosił
# imiennie, ginął na regule z cudzej listy.
#
# Wpadka zgłoszona tytułem "CUBE Stereo Hybrid 160 HPC TM 750 | XL,
# nur 894km, Service 03/26" (3517638486, 2 990 €, 894 km).
RAMA_ZA_DUZA_PATTERNS = [r'\bxxl\b', r'\bxl\b']


def za_duza_rama(title: str) -> bool:
    """Czy tytuł mówi o ramie XL albo XXL. Osobno od `is_junk` z rozmysłu."""
    t = (title or "").lower()
    return any(re.search(p, t) for p in RAMA_ZA_DUZA_PATTERNS)

# Jeśli tytuł ZACZYNA SIĘ od jednego z tych słów → sprzedaje część, nie cały rower
PART_TITLE_PREFIXES = [
    "motor", "akku", "gabel", "bremse", "kurbel", "kassette",
    "schaltwerk", "sattelstütze", "sattelstutze", "antrieb",
    "display", "ladegerät", "ladegerat", "ladekabel",
]

# Słowa które potwierdzają że to fully (wymagane dla ogólnych wyszukiwań)
# FILTR FULLY MIERZYŁ STARANNOŚĆ SPRZEDAWCY, NIE ROWER (02.09.2026)
#
# To ta sama choroba co w `has_known_motor` przed poprawką z 01-02.09 i to
# samo lekarstwo: obok słowa kluczowego muszą stać NAZWY MODELI, które są
# fully z definicji.
#
# Dowód, że filtr mierzył słowo, a nie rower. Na 37 modelach, przy których
# sprzedawcy sami piszą "Fully" w co najmniej 40% ogłoszeń, odsetek
# przepuszczonych przez `is_fully` jest RÓWNY odsetkowi tych, którzy to słowo
# napisali: Bulls Sonic 63% i 63%, Conway Xyron 67% i 69%, Focus Jam² 44%
# i 45%, Giant Stance 66% i 65%. Filtr nie wnosił do tych modeli ani jednej
# własnej informacji.
#
# Zmierzona cena tej pomyłki na 55 dniach rynku: ~15 prawdziwych fully dziennie
# odrzucanych przed pobraniem strony. Większość to marki niszowe, które i tak
# potrzebują 30% zniżki, ale dwa modele z whitelisty ginęły w całości:
#
#   Specialized Kenevo    65 rowerów, przechodziło 18 (28%), 27 w widełkach
#   KTM Macina Prowler    28 rowerów, przechodziło  5 (18%)
#
# Odrzucane tytuły to np. "Specialized Turbo Kenevo Expert 2018 E-MTB" i "KTM
# Macina Prowler Pro E-MTB Carbon L Bosch CX" za 1 800 €. Oba przechodzą filtr
# silnika i nie są Levo FSR, więc były to gotowe oferty tracone na słowie.
#
# "cube stereo" bez "hybrid" dochodzi z tego samego powodu: "Cube Stereo Pro
# 120P E-Bike 29 Zoll" to Stereo Hybrid, tylko sprzedawca skrócił nazwę.
#
# CZEGO NIE WOLNO Z TYM ZROBIĆ: skasować filtru. Stoi PRZED
# `czytaj_ogloszenie`, więc jest bramką na ruch, a nie ozdobą. Zmierzone:
# dziś dochodzi do pobrania strony 122 ogłoszenia dziennie, a bez tego filtru
# doszłoby 353. Przy zmierzonym dławieniu Kleinanzeigen (~50 żądań w 10 minut
# z jednego adresu = strona-śmieć na 20 minut) potrojenie ruchu to nie
# oszczędność, tylko ślepota.
#
# Koszt tej poprawki jest policzony: +2,4 pobrania stron dziennie (+2%)
# i +0,5 powiadomienia dziennie. Ani "kenevo" (71 ogłoszeń), ani "macina
# prowler" (30) nie mają w danych ANI JEDNEGO tytułu ze słowem "hardtail".
#
# Jak dopisywać kolejne: NIE szukaj ich mierząc, kto pisze "Fully" - modele,
# których tu brakuje, to z definicji te, przy których nikt tego nie pisze.
# Ta droga jest kołowa i sama z siebie odrzuciła Kenevo i Prowlera.
FULLY_KEYWORDS = [
    "fully", "full suspension", "full-suspension", " fs ", "fs,", "fs)",
    "stereo hybrid", "cube stereo", "levo", "kenevo", "rail", "powerfly",
    "strike", "patron", "genius", "macina lycan", "macina kapoho",
    "macina prowler", "spectral", "torque", "nduro", "allmtn", "e-asx",
    "wild fs", "eone-sixty", "strive",
]

ELECTRIC_KEYWORDS = [
    # "e bike" bez granicy lapalo sie na "VerkaufE BIKE" i "MeinE BIKE" —
    # w niemieckich tytulach to nagminne. Odwrotnie: "eRide" pisane bez
    # lacznika (tak brandujе Scott) NIE bylo rozpoznawane wcale.
    r"\be[- ]bike\b", r"ebike", r"elektro", r"pedelec", r"bosch",
    r"shimano steps", r"yamaha", r"brose", r"fazua", r"\bakku\b", r"\bwh\b",
    r"\blevo\b", r"\btrek rail\b", r"powerfly", r"macina", r"\bstrike\b",
    r"\bpatron\b",
    # nazwy modeli które SĄ elektryczne z definicji (bez tego "Cube Stereo
    # Hybrid 120 Pro 625" bez słowa Wh/Bosch był błędnie odrzucany jako analog)
    r"stereo hybrid", r"kenevo", r"\be-mtb\b", r"\bemtb\b", r"e-mountainbike",
    # \b konieczne: bez niego "e fully" lapalo sie na "Mountainbik-E FULLY",
    # bo w niemieckim mnostwo slow konczy sie na "e". Tak przeszedl zwykly
    # Scott Ramson 600 (26 cali, bez silnika) jako rzekomy elektryk.
    r"\be[- ]fully\b", r"genius e-?ride", r"\be-?ride\b", r"\d{3}\s*wh",
]

# --- REGION Z KODU POCZTOWEGO ----------------------------------------------
# "28307 Osterholz" nic nie mówi o tym, gdzie po ten rower jechać. Niemiecki
# kod pocztowy niesie tę informację w dwóch pierwszych cyfrach, więc zamiast
# nazwy wsi pokazujemy land — i zaznaczamy te, które leżą przy naszej granicy,
# bo to jedyna rzecz zmieniająca decyzję o dojeździe.
_REGIONY = [
    (0, 1, "Saksonia"), (2, 2, "Saksonia (przy granicy)"),
    (3, 3, "Brandenburgia (przy granicy)"), (4, 5, "Saksonia"),
    (6, 6, "Saksonia-Anhalt"), (7, 7, "Turyngia"), (8, 9, "Saksonia"),
    (10, 14, "Berlin"), (15, 15, "Brandenburgia (przy granicy)"),
    (16, 16, "Brandenburgia"), (17, 17, "Meklemburgia (przy granicy)"),
    (18, 19, "Meklemburgia"), (20, 22, "Hamburg"), (23, 25, "Szlezwik-Holsztyn"),
    (26, 27, "Dolna Saksonia"), (28, 28, "Brema"), (29, 31, "Dolna Saksonia"),
    (32, 33, "Nadrenia Płn.-Westfalia"), (34, 36, "Hesja"),
    (37, 38, "Dolna Saksonia"), (39, 39, "Saksonia-Anhalt"),
    (40, 48, "Nadrenia Płn.-Westfalia"), (49, 49, "Dolna Saksonia"),
    (50, 53, "Nadrenia Płn.-Westfalia"), (54, 56, "Nadrenia-Palatynat"),
    (57, 59, "Nadrenia Płn.-Westfalia"), (60, 65, "Hesja"), (66, 66, "Saara"),
    (67, 67, "Nadrenia-Palatynat"), (68, 79, "Badenia-Wirtembergia"),
    (80, 87, "Bawaria"), (88, 88, "Badenia-Wirtembergia"),
    (89, 97, "Bawaria"), (98, 99, "Turyngia"),
]


def region_z_plz(loc: str):
    """Land z niemieckiego kodu pocztowego. None, gdy kodu nie ma.

    Granice landów nie pokrywają się idealnie z kodami, więc na styku dwóch
    landów wynik bywa przybliżony — ale rząd wielkości "jak daleko jechać"
    jest zawsze poprawny, a to jedyne, po co ta informacja tu jest."""
    m = re.match(r'\s*(\d{2})', loc or "")
    if not m:
        return None
    n = int(m.group(1))
    for lo, hi, nazwa in _REGIONY:
        if lo <= n <= hi:
            return nazwa
    return None


def is_fully(title: str) -> bool:
    t = title.lower()
    # Sprzedawca napisał wprost "hardtail" — to bije nazwę modelu. Levo, Levo SL
    # czy Moterra to zwykle full, ale istnieją wersje HT i wtedy tytuł mówi
    # prawdę, a nasza lista modeli zgaduje. Fakt z ogłoszenia wygrywa z domysłem.
    if re.search(r"hard\s?tail|\bhardtail\b", t):
        return False
    return any(kw in t for kw in FULLY_KEYWORDS)

def is_electric(title: str) -> bool:
    t = title.lower()
    return any(re.search(kw, t) for kw in ELECTRIC_KEYWORDS)

# Marki z wysokim resale value w Polsce — tylko te dostają powiadomienia.
# Niszowa marka przechodzi wyjątkowo, gdy cena jest mocno poniżej mediany.
# Canyon: nowsze modele (Strive:ON, Torque:ON od ~2023) mają Boscha —
# filtr silnika i tak odsiewa wersje na Shimano EP8
PREMIUM_BRANDS = ["cube", "trek", "specialized", "scott", "ktm", "canyon"]
NICHE_MIN_DISCOUNT_PCT = 30


def is_premium_brand(title: str) -> bool:
    t = title.lower()
    return any(b in t for b in PREMIUM_BRANDS)

# Słowa sugerujące dobry stan
GOOD_CONDITION = [
    "neuwertig", "wie neu", "kaum gefahren", "wenig gefahren",
    "top zustand", "sehr gut", "unbenutzt", "ovp", "originalverpackt",
]

def url(query):
    slug = query.replace(" ", "-")
    return f"https://www.kleinanzeigen.de/s-preis:{MIN_PRICE}:{MAX_PRICE}/{slug}/k0"

SEARCHES = [
    # --- Ogólne terminy na fully / e-mtb ---
    {"name": "e-bike fully",           "url": url("e-bike-fully")},
    {"name": "ebike fully",            "url": url("ebike-fully")},
    {"name": "elektro fully",          "url": url("elektro-fully")},
    {"name": "e-mtb fully",            "url": url("e-mtb-fully")},
    {"name": "emtb",                   "url": url("emtb")},
    {"name": "e-mountainbike fully",   "url": url("e-mountainbike-fully")},
    {"name": "pedelec fully",          "url": url("pedelec-fully")},
    {"name": "elektrofahrrad fully",   "url": url("elektrofahrrad-fully")},
    # --- Marki ---
    {"name": "Cube Stereo Hybrid",     "url": url("cube-stereo-hybrid")},
    {"name": "Cube Stereo E",          "url": url("cube-stereo-e")},
    {"name": "Trek Rail",              "url": url("trek-rail")},
    {"name": "Trek Powerfly FS",       "url": url("trek-powerfly-fs")},
    {"name": "KTM Macina Lycan",       "url": url("ktm-macina-lycan")},
    {"name": "KTM Macina Kapoho",      "url": url("ktm-macina-kapoho")},
    {"name": "KTM Macina fully",       "url": url("ktm-macina-fully")},
    {"name": "Scott Strike E-Ride",    "url": url("scott-strike-e-ride")},
    {"name": "Scott Patron",           "url": url("scott-patron")},
    {"name": "Scott Genius E-Ride",    "url": url("scott-genius-e-ride")},
    {"name": "Canyon Strive ON",       "url": url("canyon-strive")},
    {"name": "Canyon Torque ON",       "url": url("canyon-torque-on")},
    {"name": "Specialized Levo",       "url": url("specialized-levo")},
    {"name": "Specialized Turbo Levo", "url": url("specialized-turbo-levo")},
]

TRANSPORT_PLN = 300  # do recznej korekty przed zakupem

SEEN_FILE = Path("seen.json")
OBSERWOWANE_FILE = Path("obserwowane.json")
scraper = cloudscraper.create_scraper()
# niemiecka wersja strony niezależnie od tego, gdzie stoi runner —
# od tego zależą etykiety dat ("Heute"/"Gestern"), które czyta parser
scraper.headers.update({"Accept-Language": "de-DE,de;q=0.9"})
_eur_pln_cache = None


def get_eur_pln() -> float:
    global _eur_pln_cache
    if _eur_pln_cache:
        return _eur_pln_cache
    try:
        r = requests.get("https://api.exchangerate-api.com/v4/latest/EUR", timeout=10)
        _eur_pln_cache = r.json()["rates"]["PLN"]
        return _eur_pln_cache
    except Exception:
        return 4.25  # fallback


# Wzorce znanych modeli — do precyzyjnego zapytania OLX (marka+model,
# nie ogólne "e-bike fully" które porównuje jabłka z gruszkami)
MODEL_PATTERNS = [
    r'cube stereo hybrid\s*\d*',
    r'cube stereo\s*\d*',
    r'trek rail\s*\d*',
    r'trek powerfly(?:\s*fs)?\s*\d*',
    r'specialized (?:turbo )?(?:levo|kenevo)(?:\s*sl)?',
    r'scott strike(?:\s*e-?ride)?',
    r'scott patron',
    r'scott genius(?:\s*e-?ride)?',
    r'ktm macina\s+\w+',
    r'canyon (?:strive|torque|spectral|neuron)',   # bez ':on' — czysty slug do OLX
]

OLX_MIN_SAMPLES = 5  # poniżej tylu ofert mediana to loteria — nie liczymy zysku


def olx_query_for(title: str, fallback: str) -> str:
    """Wyciąga markę+model z tytułu; jak się nie da — nazwa wyszukiwania."""
    t = title.lower()
    for p in MODEL_PATTERNS:
        m = re.search(p, t)
        if m:
            return m.group(0).strip()
    # S-WORKS BEZ KLUCZA WYCENY (17.09.2026). Wzorzec Specializeda wymaga
    # "specialized [turbo] levo" jednym ciągiem, więc "S-Works" w środku,
    # odwrócone "S-Works SL Levo" albo brak słowa "Specialized" dawały None.
    # Zmierzone na 105 855 tytułach: 55 z 89 ofert S-Works Levo/Kenevo nie
    # miało klucza, a `main` podstawiał wtedy nazwę WYSZUKIWANIA ("kanał MTB")
    # - czyli najdroższe rowery na rynku wyceniał względem wszystkich
    # elektryków naraz.
    #
    # Działa WYŁĄCZNIE jako zapas, gdy wzorzec nic nie znalazł, więc żaden
    # tytuł, który dziś ma klucz, go nie zmienia (sprawdzone: 0 na 105 855).
    # 51 z 55 trafia w klucze, które już mają dane popytu w olx_watch.json.
    if re.search(r's[\s-]?works', t) and re.search(r'\b(?:levo|kenevo)\b', t):
        z = re.sub(r'\bs[\s-]?works\b\s*', '', t)
        z = re.sub(r'\bsl\s+((?:turbo\s+)?(?:levo|kenevo))\b', r'\1 sl', z)
        if not re.search(r'specialized\s+(?:turbo\s+)?(?:levo|kenevo)', z):
            z = re.sub(r'\b((?:turbo\s+)?(?:levo|kenevo))\b', r'specialized \1', z, count=1)
        for p in MODEL_PATTERNS:
            m = re.search(p, z)
            if m:
                return m.group(0).strip()
    return fallback


CURRENT_YEAR = date.today().year


_ROK = r'20(?:1[5-9]|2[0-6])'

# ROK, KTÓRY NALEŻY DO CZĘŚCI, NIE DO ROWERU. "Neuer Akku 02/2026" mówi
# o baterii wstawionej w 2026, a nie o roczniku roweru — a właśnie tak
# 26.08.2026 rower z 2023 dostał rocznik 2026. Skutek policzony na własnym
# cenniku cech: wycena 17 162 zł zamiast 13 928 zł, czyli zysk zawyżony
# z ~1 840 zł do ~4 750 zł. Rocznik dopłaca 7,2% na rok, więc trzy lata
# pomyłki to jedna czwarta ceny roweru.
#
# Weto jest CIASNE z rozmysłem. Samo sąsiedztwo słowa "Akku" nie wystarcza:
# w "625 Akku - 2022" rok najpewniej JEST rocznikiem roweru, a zawetowanie
# go zamieniłoby jedną pomyłkę na drugą. Wetujemy wyłącznie jawne
# "ta część jest nowa / serwisowana w roku X". Zmierzone 01.09.2026 na
# 39 550 unikalnych tytułach z market.jsonl.
# Dwa wzorce, bo rok bywa po OBU stronach czasownika wymiany: "Akku neu 2026"
# i "Akku 2025 erneuert" znaczą to samo. W obu rok musi zostać grupą 1 —
# `extract_year` czyta z niego POZYCJĘ, nie wartość.
_ROK_CUDZY = (
    re.compile(
        r'(?:neue[rns]?\s+(?:akku|batterie|motor|antrieb|kette|reifen|bremsen)'
        r'|(?:akku|batterie|motor|antrieb|kette)\s+'
        r'(?:neu\b|erneuert|getauscht|ausgetauscht|ersetzt|gewechselt)'
        r'|inspektion|service|wartung|gewartet|garantie\s+bis)'
        rf'[^.;|]{{0,20}}?\b({_ROK})\b', re.I),
    re.compile(
        r'\b(?:akku|batterie|motor|antrieb|kette)\b'
        rf'[^.;|]{{0,20}}?\b({_ROK})\b\s*'
        r'(?:erneuert|getauscht|ausgetauscht|ersetzt|gewechselt|neu\b)', re.I),
)


# ROK BĘDĄCY CZĘŚCIĄ PEŁNEJ DATY to nie rocznik roweru (17.09.2026).
# Właściciel oznaczył przyciskiem "za stary" Treka Rail 9.5 z rocznikiem 2026.
# Jedyny rok w całym opisie stał w "HERBST SALE %%% BIS 30.9.2026", czyli
# w dacie końca promocji sklepu. Ta sama pułapka co "NUR BIS ZUM 31.08.2026"
# sklepu BESV opisana przy parserze listy - tylko w innym miejscu kodu.
#
# Wzorzec jest CIASNY i to był drugi pomiar tego dnia, nie pierwszy. Pierwsza
# wersja łapała każdy rok z liczbą i separatorem przed sobą i na 105 855
# unikalnych tytułach zmieniała wynik w 184 - psując prawdziwe roczniki:
# "KTM Power Sport 10 - 2024" (model 10, rocznik 2024), "Conway XYRON SUV
# 6.9 - 2022", "BJ. 8/2017" (Baujahr sierpień 2017 TO JEST rocznik). Rocznika
# roweru nikt nie pisze z DNIEM i MIESIĄCEM, więc łapiemy tylko D.M.RRRR
# z kropkami i bez spacji.
#
# Zmierzone na tych samych 105 855 tytułach: zmienia wynik w 15 (0,014%).
# 14 poprawnych - daty promocji ("Aktionspreis NUR bis 31.08.2026"), daty
# zakupu ("Kaufdatum 14.10.2024"), faktury - a w jednym odzyskuje prawdziwy
# rocznik ("ab 01.09.2026 - KTM Macina Style 710 ... 2023" daje 2023, nie
# 2026). Jeden wątpliwy: "Hard Ray E 2.0.2022" traci rocznik, bo wersja "2.0"
# jest sklejona kropką z rokiem. "Nie wiem" jest tam tańsze niż zgadywanie.
#
# Działa WYŁĄCZNIE w kroku 3 (goły rok). Kroki 1 i 2 zostają bez zmian: jedyny
# przypadek na 105 855, w którym krok 1 zwracał rok uznany przez `_ROK_CUDZY`
# za cudzy ("Neue Akku 11,6 Ah/ Baujahr 2024"), był POPRAWNY - to jawny
# Baujahr, a weto myliło się przez sąsiedztwo słowa "Akku".
_ROK_W_DACIE = re.compile(rf'\b\d{{1,2}}\.\d{{1,2}}\.({_ROK})\b')


def extract_year(text):
    """Wyciąga rocznik ROWERU (2015-2026) z tytułu/opisu. None gdy brak.

    Rok stojący przy wymienionej części nie jest rocznikiem roweru —
    patrz `_ROK_CUDZY`. Gdy w tekście nie zostaje żaden inny rok, zwracamy
    None: „nie wiem" jest tańsze niż rocznik młodszy o trzy lata."""
    if not text:
        return None
    yr = _ROK
    # 1. z kontekstem — najpewniejsze
    m = re.search(rf'(?:modelljahr|modell|baujahr|bj\.?|mj\.?|jahrgang|aus|von|rok)\s*[:.]?\s*({yr})', text, re.I)
    if m:
        return int(m.group(1))
    # 2. "2023er"
    m = re.search(rf'\b({yr})er\b', text, re.I)
    if m:
        return int(m.group(1))
    # 3. goły rok — pomijając te, które już mają właściciela
    cudze = {m.start(1) for wz in _ROK_CUDZY for m in wz.finditer(text)}
    cudze |= {m.start(1) for m in _ROK_W_DACIE.finditer(text)}
    for m in re.finditer(rf'\b({yr})\b', text):
        if m.start(1) not in cudze:
            return int(m.group(1))
    return None


# --- WIEK OGŁOSZENIA -------------------------------------------------------
# Karta wyniku na Kleinanzeigen niesie czas wystawienia ("Heute, 00:41") i bot
# dotąd go wyrzucał. Bez tego "nowe" znaczyło tylko "pierwszy raz je widzę" —
# ogłoszenie, które weszło do wyników 17 h po wystawieniu (bo sprzedawca zbił
# cenę do widełek albo poprawił opis), wyglądało jak świeże. Ogłoszenie wiekowe
# to inna decyzja: rower był już widziany przez cały rynek.
# ZMIERZONE 01.09.2026, 11:15 — Kleinanzeigen PRZEBUDOWAŁO listę wyników.
# Klasy semantyczne (`aditem-main--top--right`) zniknęły ze strony do zera
# i zastąpiły je klasy narzędziowe w stylu Tailwinda, generowane, więc nie
# nadające się na kotwicę. Tytuł i cena przeżyły, bo ich wzorce stoją na
# `href="/s-anzeige/..."` i na kształcie kwoty — data stała jako jedyna.
# Skutek: bot widział "0 ogłoszeń" na obu półkach przez ponad sześć godzin,
# bo bez daty nie da się cofać po kanale, a strona z kafelkami i bez ani
# jednej daty jest z definicji podstawioną listą (patrz `strona_zepsuta`).
#
# Nowa kotwica to KSZTAŁT TREŚCI, nie klasa: goły `<span>` z samą datą,
# stojący zaraz za ikoną zegara. Sprawdzone na żywej stronie tego dnia —
# 25 z 27 kafelków. Dwa pozostałe to poprawne pudła: reklama „Direkt kaufen"
# nie ma daty w ogóle, a sklep BESV ma `31.08.2026` w TREŚCI ogłoszenia
# („NUR BIS ZUM 31.08.2026"), nie w polu daty. Wzorzec na `<span>` z SAMĄ
# datą odrzuca ją sam z siebie — to ta sama pułapka co „NIEAKTUALNE"
# w boilerplate OLX (reguła 8), tylko po niemieckiej stronie.
#
# Stary wzorzec ZOSTAJE na początku listy. Nic nie kosztuje, a serwis potrafi
# oddawać kilka układów naraz (zmierzone 23.08 na galerii zdjęć) i wersja
# sprzed przebudowy może jeszcze komuś wracać.
AD_TIME_PATTERNS = [
    # `(?s)` W SAMYM WZORCU, nie w re.compile. `_match_pool` woła
    # `re.search(p, block)` bez flag, więc przeniesienie tego wzorca do puli
    # 01.09.2026 po cichu zabrało mu DOTALL — a data w starym układzie stoi
    # w OSOBNEJ LINII wewnątrz diva, więc `(.*?)</div>` przestawało ją łapać.
    # Zmierzone: wariant wieloliniowy dawał None tam, gdzie wersja sprzed
    # zmiany czytała go poprawnie. Regresja wprowadzona przy naprawie
    # przebudowy i wyłapana tego samego dnia.
    r'(?s)aditem-main--top--right"[^>]*>(.*?)</div>',
    r'<span[^>]*>((?:Heute|Gestern),\s*\d{1,2}:\d{2})</span>',
    r'<span[^>]*>(\d{1,2}\.\d{1,2}\.\d{4})</span>',
    # TRZECI UKŁAD, złapany 01.09.2026 o 21:06 przez czarną skrzynkę
    # (`blackbox/niema-kanał_e_bike-2026-09-01-fe1f794c.html`): lżejsza
    # odpowiedź, 183 kB zamiast 638 kB, 32 kafelki. Ani starej klasy, ani
    # gołego `<span>` — za to 30 razy słowo „Heute" w `adlist--item--info--date`.
    # To ta sama rodzina co `adlist--item--price` w PRICE_PATTERNS, więc cena
    # z tej strony czytała się od dawna, a data nie miała czym. Stąd te trzy
    # pudła na osiem skanów: serwis oddaje TRZY układy, a pula znała dwa.
    # `(?s)` konieczne — data stoi w osobnej linii wewnątrz diva.
    r'(?s)adlist--item--info--date">(.*?)</div>',
]
# Zgodność wstecz: stara nazwa wskazuje na pierwszy wzorzec.
AD_TIME_PATTERN = re.compile(AD_TIME_PATTERNS[0], re.S)

# Powyżej tylu minut ogłoszenie nie jest już "świeże". Skan idzie co 5 minut,
# a kanał sam nadrabia przerwy cofaniem się wstecz, więc 30 minut znaczy, że
# coś zawiodło: albo stanął wyzwalacz, albo roweru w ogóle nie było w kanale.
SWIEZOSC_MIN = 30


def parse_ad_time(raw, now=None):
    """'Heute, 00:41' / 'Gestern, 18:12' / '21.08.2026' -> datetime (strefa DE).

    Zwraca None, gdy formatu nie da się odczytać — brak daty nigdy nie może
    wywrócić skanu. Dla samej daty (bez godziny) zwraca północ, więc wiek jest
    znany tylko z dokładnością do doby."""
    if not raw:
        return None
    txt = re.sub(r'<[^>]+>', ' ', raw)
    txt = re.sub(r'\s+', ' ', txt).strip()
    if not txt:
        return None
    now = now or datetime.now(TZ_DE)
    m = re.search(r'\b(\d{1,2})[:.](\d{2})\b', txt)
    low = txt.lower()
    if m and ("heute" in low or "gestern" in low):
        godz, minuty = int(m.group(1)), int(m.group(2))
        if godz > 23 or minuty > 59:
            return None
        dzien = now.date() - (timedelta(days=1) if "gestern" in low else timedelta(0))
        return datetime(dzien.year, dzien.month, dzien.day, godz, minuty, tzinfo=TZ_DE)
    m = re.search(r'\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b', txt)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return datetime(y, mo, d, tzinfo=TZ_DE)
        except ValueError:
            return None
    return None


def ad_age_minutes(posted, now=None):
    """Ile minut temu wystawiono ogłoszenie. None gdy nie znamy czasu."""
    if not posted:
        return None
    now = now or datetime.now(TZ_DE)
    return (now - posted).total_seconds() / 60.0


def format_age(minutes) -> str:
    """Wiek ogłoszenia po ludzku. '?' gdy Kleinanzeigen nie podało czasu."""
    if minutes is None:
        return "nie podano"
    if minutes < 0:                      # zegar runnera rozjechany ze stroną
        return "przed chwilą"
    if minutes < 60:
        return f"{int(minutes)} min temu"
    if minutes < 48 * 60:
        h, m = divmod(int(minutes), 60)
        return f"{h} h {m} min temu" if m else f"{h} h temu"
    return f"{int(minutes // 1440)} dni temu"


def olx_search_url(query: str) -> str:
    slug = query.lower().replace(" ", "-")
    return f"https://www.olx.pl/sport-hobby/rowery/q-{slug}/"


# === JEDNA BRAMKA ALARMOWA =================================================
# Użytkownik nie jest techniczny i dostawał osiem różnych alarmów, w tym pary
# "nie działa"/"działa" w odstępie minuty — bo każdy alarm szedł ZBOCZEM, więc
# mrugnięcie sieci wystarczało za powód. Teraz liczy się TRWANIE: dopóki awaria
# nie utrzyma się AWARIA_PROG_MIN, nie dowiaduje się o niej wcale. Potem jedno
# zdanie po ludzku, bez żargonu, i jedno "już działa" na koniec. Szczegóły
# techniczne zostają w logu i pod komendą /status — na żądanie, nie z automatu.
# Nieprzeczytana strona ogłoszenia NIE JEST faktem "brak danych".
# Serwer po drugiej stronie bywa chwilowo niedostępny i to nie jest w naszej
# mocy — w naszej mocy jest nie zapisać takiej chwili jako wiedzy o rowerze.
# Ogłoszenie z nieudanym odczytem trafia do kolejki i wraca w kolejnych
# skanach po własny adres, aż się przeczyta. Kanał kategorii go już nie odda:
# znacznik czasu przesunął się dalej, więc bez tej kolejki byłby stracony.
ODCZYT_PROBY = 3           # prób w jednym podejściu (2 s, 4 s przerwy)
# SUFIT PODNIESIONY Z 8 NA 20 (20.09.2026), decyzja właściciela po pomiarze.
# Przy skanie co ~70 s (mediana z 198 skanów) 8 podejść to ~10 minut, a 20 to
# ~pół godziny. Powód: na CAŁYM dzienniku (74 dni, 140 425 ogłoszeń) rozkład
# udanych odczytów wygląda tak — 1 próba: 140 046, 2: 366, 3: 11, **7: 1**.
# Najtrudniejszy rower, który ostatecznie się przeczytał, potrzebował SIEDMIU
# podejść, więc stary sufit 8 miał margines jednej próby. Między trójką
# a siódemką w danych nie ma NIC, więc ogona tego rozkładu nie znamy.
#
# Rachunek jest niesymetryczny i to on rozstrzygnął: po jednej stronie
# kilkanaście dodatkowych pobrań (do progu doszło JEDNO ogłoszenie na 74 dni,
# więc +12 pobrań na dwa i pół miesiąca), po drugiej rower w widełkach
# zgubiony przez kwadrans awarii serwisu. Ruch NA SKAN się nie zmienia:
# z kolejki i tak idzie najwyżej ODCZYT_NA_SKAN sztuk, wydłuża się wyłącznie
# ogon pojedynczego ogłoszenia.
ODCZYT_PODEJSC = 20        # podejść w kolejnych skanach zanim odpuścimy
ODCZYT_NA_SKAN = 6         # ile zaległych czytamy w jednym skanie (budżet ruchu)
ODCZYT_WAZNE_H = 36        # po tylu godzinach rower i tak jest już nieświeży

AWARIA_PROG_MIN = 60
_problemy = []

# Czujka cichej zmiany układu strony. 23.08 Kleinanzeigen podmieniło stronę
# ogłoszenia: id ceny z "viewad-price" na "vip-ad-price", a zdjęcia z
# data-imgsrc na bloki JSON-LD. Bot czytał dalej opis, więc NIC nie krzyczało —
# tylko album był pusty, a cena ze strony nie działała. Od teraz brak zdjęć albo
# brak ceny na WSZYSTKICH przeczytanych stronach naraz jest traktowany jak
# awaria: to nie przypadek, to zmiana układu.
CZUJKA_MIN = 5              # poniżej tylu odczytów cisza nic nie znaczy
_czujka = {"czytane": 0, "ze_zdjeciami": 0, "z_cena": 0}


def zlicz_odczyt(zdjecia, cena) -> None:
    _czujka["czytane"] += 1
    if zdjecia:
        _czujka["ze_zdjeciami"] += 1
    if cena:
        _czujka["z_cena"] += 1


def sprawdz_uklad(licznik=None) -> None:
    """Wywoływane raz na skan, przed oceną zdrowia."""
    c = licznik if licznik is not None else _czujka
    if c["czytane"] < CZUJKA_MIN:
        return
    if not c["ze_zdjeciami"]:
        zglos_problem("uklad", f"zero zdjęć na {c['czytane']} stronach")
    if not c["z_cena"]:
        zglos_problem("uklad", f"zero cen na {c['czytane']} stronach")


def zglos_problem(rodzaj: str, szczegol: str = "") -> None:
    """Odnotowuje awarię w trakcie skanu. Sam nie wysyła NICZEGO."""
    if rodzaj not in _problemy:
        _problemy.append(rodzaj)
    log.error(f"awaria [{rodzaj}] {szczegol}")


def _stan(zmiana=None):
    """Czyta/zapisuje wspólny plik stanu (i tak commitowany do repo)."""
    stan = {}
    if PARSE_STATE_FILE.exists():
        try:
            stan = json.loads(PARSE_STATE_FILE.read_text())
        except Exception:
            stan = {}
    if zmiana is not None:
        stan.update(zmiana)
        try:
            PARSE_STATE_FILE.write_text(json.dumps(stan))
        except Exception as e:
            log.error(f"zapis stanu: {e}")
    return stan


def opisz_awarie(rodzaje) -> str:
    """Awaria po ludzku — co z tego wynika DLA NIEGO, nie co się zepsuło."""
    slepy = "slepy" in rodzaje
    olx = "olx" in rodzaje
    if "zaleglosc" in rodzaje or "zgubione" in rodzaje:
        return ("🔕 <b>DealHawk — mogłem coś przegapić</b>\n\n"
                "Główny kanał ogłoszeń stoi w miejscu dłużej, niż powinien. "
                "Rowery mogły przejść mi koło nosa.\n\n"
                "Sam się z tego wygrzebuję i odezwę, gdy wróci. "
                "Jeśli szukasz teraz — zerknij na Kleinanzeigen własnymi oczami.")
    if "uklad" in rodzaje and not slepy:
        return ("🔕 <b>DealHawk — ogłoszenia zmieniły wygląd</b>\n\n"
                "Rowery przychodzą normalnie, ale przestałem wyciągać ze strony "
                "zdjęcia albo cenę. To znaczy, że Kleinanzeigen przebudowało "
                "stronę i muszę się dostroić.\n\n"
                "Nic nie zgubisz — link w wiadomości działa jak zwykle.")
    if slepy and olx:
        tresc = ("Nie mogę pobrać ogłoszeń z Niemiec ani sprawdzić cen na OLX. "
                 "Nowe rowery na razie nie przyjdą.")
    elif slepy:
        tresc = ("Nie mogę pobrać listy ogłoszeń z Niemiec. "
                 "Nowe rowery na razie nie przyjdą.")
    else:
        tresc = ("Nie mam dostępu do OLX. Rowery z Niemiec przychodzą normalnie, "
                 "ale wyceny liczę ze starszych danych.")
    return (f"🔕 <b>DealHawk — coś nie działa od godziny</b>\n\n{tresc}\n\n"
            "Próbuję dalej co 5 minut, zwykle mija samo. "
            "Odezwę się, gdy wróci. Szczegóły: napisz <code>/status</code>")


def ocen_zdrowie(rodzaje):
    """Wywoływane RAZ na skan, na końcu. Jedyne miejsce, które alarmuje."""
    try:
        stan = _stan()
        od = stan.get("awaria_od")
        zgloszona = bool(stan.get("awaria_zgloszona"))
        if not rodzaje:
            if od or zgloszona:
                _stan({"awaria_od": None, "awaria_zgloszona": False})
            if zgloszona:
                send_telegram("✅ <b>DealHawk — już działa.</b>")
                log.info("Awaria zakończona — wysłano potwierdzenie")
            return
        teraz = time.time()
        if not od:
            _stan({"awaria_od": teraz})
            log.warning(f"awaria {rodzaje} — zegar ruszył, cisza do "
                        f"{AWARIA_PROG_MIN} min")
            return
        trwa_min = (teraz - od) / 60
        if trwa_min >= AWARIA_PROG_MIN and not zgloszona:
            _stan({"awaria_zgloszona": True})
            send_telegram(opisz_awarie(rodzaje))
            log.error(f"awaria {rodzaje} trwa {int(trwa_min)} min — zgłoszona")
        else:
            log.warning(f"awaria {rodzaje} trwa {int(trwa_min)} min "
                        f"({'już zgłoszona' if zgloszona else 'jeszcze cisza'})")
    except Exception as e:
        log.error(f"ocen_zdrowie error: {e}")


def alarm_olx_martwy(kontekst: str, szczegoly: str = "") -> None:
    """OLX nie oddaje ofert. Cisza jest gorsza od fałszywego alarmu —
    poprzednio kosztowała 11 dni niezebranych danych — ale alarm idzie przez
    wspólną bramkę, więc chwilowa wpadka nie budzi nikogo."""
    d = olx_diag()
    zglos_problem("olx", f"{kontekst} | {d} | {szczegoly}")


def fetch_olx_offers(query: str, pages: int = 2) -> dict:
    """Zwraca {url_oferty: cena} z pierwszych `pages` stron wyników OLX
    (więcej próbki = lepsze filtrowanie do porównywalnych)."""
    out = {}
    slug = query.lower().replace(" ", "-")
    for page in range(1, pages + 1):
        url = f"https://www.olx.pl/sport-hobby/rowery/q-{slug}/"
        if page > 1:
            url += f"?page={page}"
        r = olx_get(url)
        if r is None or r.status_code != 200:
            break
        cards = parse_olx_cards(r.text)
        if not cards:
            zglos_pusta_strone()      # 200 bez kart = podejrzenie blokady
            break                     # (albo po prostu koniec wyników)
        out.update({c["url"]: c["price"] for c in cards})
    return out


# --- Pełna dokładność OLX: strukturalny przebieg/stan ze strony oferty. ---
# Strona waży ~2 MB, więc cache per-URL (pobieramy raz, potem tylko nowe oferty).
OLX_DETAILS_FILE = Path("olx_details.json")
OLX_DETAILS_KEEP_DAYS = 60
_olx_details_cache = None


def load_olx_details() -> dict:
    global _olx_details_cache
    if _olx_details_cache is None:
        if OLX_DETAILS_FILE.exists():
            try:
                _olx_details_cache = json.loads(OLX_DETAILS_FILE.read_text())
            except Exception:
                _olx_details_cache = {}
        else:
            _olx_details_cache = {}
    return _olx_details_cache


# === SPECYFIKACJA Z OPISU: osprzęt, amortyzator, rama =========================
# Cena zależy nie tylko od rocznika i baterii — "Trek Rail 5" i "Rail 9.8" to ten
# sam model w wyszukiwarce i dwa razy inna cena. Te dane SĄ w opisach (amortyzator
# 94%, osprzęt 94%, rama 89% zbadanych ofert), a bot je wyrzucał, choć strony i tak
# pobierał. Drabinki jakości siedzą w wiedza_sprzet.json — do wglądu i poprawek
# właściciela. Tu ustalamy CO jest lepsze; ILE to warte policzy rynek.
SPEC_KB_FILE = Path("wiedza_sprzet.json")
_spec_kb_cache = None

# Słowa, które muszą stać BLISKO nazwy, żeby uznać ją za grupę napędową.
# Bez tego "Cube Stereo Hybrid 140 SLX" (wersja Cube'a) udaje grupę Shimano SLX.
# Konteksty po polsku I po niemiecku — ten sam parser czyta oferty z OLX
# (wycena) i z Kleinanzeigen (zakup), więc musi rozumieć oba rynki.
_GRUPA_KONTEKST = ["shimano", "sram", "naped", "napęd", "osprzet", "osprzęt",
                   "przerzutka", "przerzutki", "grupa", "kaseta", "korba", "manetka",
                   "antrieb", "schaltung", "schaltwerk", "kassette", "kurbel"]
_SKOK_KONTEKST = ["skok", "travel", "amortyz", "zawieszen", "widelec", "przod", "przód",
                  "federweg", "federgabel", "gabel", "dampfer", "dämpfer"]
# "XT" bywa i grupą napędową, i hamulcem — rozstrzyga sąsiedztwo
_HAMULCE_KONTEKST = ["hamulc", "hamulec", "brake", "tarcz", "zacisk", "klocki",
                     "bremse", "bremsen", "scheibenbrems"]


def load_spec_kb() -> dict:
    """Wczytuje drabinki jakości sprzętu z pliku. Brak pliku = brak wiedzy
    (bot nie zgaduje — woli nie wiedzieć niż skłamać)."""
    global _spec_kb_cache
    if _spec_kb_cache is None:
        try:
            _spec_kb_cache = json.loads(SPEC_KB_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            log.error(f"load_spec_kb error: {e}")
            _spec_kb_cache = {}
    return _spec_kb_cache


def _ma_kontekst(text: str, pos: int, slowa: list, okno: int = 45) -> bool:
    """Czy w promieniu `okno` znaków wokół pozycji stoi któreś ze słów?"""
    frag = text[max(0, pos - okno):pos + okno]
    return any(s in frag for s in slowa)


def _najlepszy_z_drabinki(d: str, drabinka: dict, wymagany_kontekst=None,
                          zakazany_kontekst=None):
    """Znajduje najwyżej stojący w drabince komponent wymieniony w opisie.
    `wymagany_kontekst` — nazwa liczy się tylko obok tych słów.
    `zakazany_kontekst` — i NIE liczy się obok tamtych. Bez tego drugiego
    "hamulce Shimano XT" wpada jednocześnie do hamulców i do napędu."""
    best = None
    for nazwa, ranga in (drabinka or {}).items():
        if nazwa.startswith("_") or not isinstance(ranga, int):
            continue
        for m in re.finditer(r'(?:^|[^\w])' + re.escape(nazwa) + r'(?:[^\w]|$)', d):
            if wymagany_kontekst and not _ma_kontekst(d, m.start(), wymagany_kontekst):
                continue
            if zakazany_kontekst and _ma_kontekst(d, m.start(), zakazany_kontekst, 25):
                continue
            if best is None or ranga > best[1]:
                best = (nazwa, ranga)
            break
    return best


# --- ROZMIAR RAMY ----------------------------------------------------------
# Rozmiar decyduje, czy rower da się w ogóle sprzedać — na L kupca szuka się
# tygodniami, a XS potrafi nie znaleźć go wcale. Do 22.08.2026 bot szukał go
# POLSKIMI słowami ("rozmiar", "rama") w NIEMIECKICH opisach, więc pole było
# puste zawsze: 0 trafień na 12 żywych ogłoszeń, z których 6 podawało rozmiar
# wprost. Formy zebrane z żywych danych:
#   "Gr. L" · "Rahmengröße S" · "Rahmenhöhe: XL" · "Rahmenhöhe von 53 cm"
#   "Rahmengröße M 50cm" · "Rahmengröße: 57 cm"
_GR = r'gr(?:o|ö|oe)(?:ss|ß|s)?e'          # große / grösse / grosse / groesse
# Ta sama funkcja czyta opisy NIEMIECKIE (Kleinanzeigen) i POLSKIE (OLX) —
# parse_spec_fields wołane jest na obu — więc etykiety muszą być w dwóch językach.
_RAMA_ETYKIETA = (r'(?:rahmen\s?h(?:o|ö|oe)he|rahmen\s?' + _GR +
                  r'|\brh\b|' + _GR + r'|\bgr\.'
                  r'|rozmiar(?:\s+ramy)?|\brama\b|\bramy\b)')
_RAMA_LITERY = ("xs", "s", "m", "l", "xl", "xxl")
_RAMA_CM_MIN, _RAMA_CM_MAX = 33, 65      # poza tym zakresem to nie jest rama


def rozmiar_ramy(title: str, desc: str):
    """Rozmiar ramy z tytułu/opisu ("M", "53 cm", "M / 50 cm"). None gdy brak.

    Zwraca WYŁĄCZNIE to, czego jest pewna. Liczby spoza zakresu ramy odpadają,
    bo w tych samych zdaniach siedzą koła (29 Zoll), opony (2,6") i waga
    (130 kg) — złapanie któregoś z nich byłoby gorsze niż brak odpowiedzi."""
    tekst = re.sub(r'\s+', ' ', f"{title or ''} {desc or ''}").lower()
    litera = cm = None
    for m in re.finditer(_RAMA_ETYKIETA + r'\s*(?::|von|,)?\s*([^,;.|]{0,24})', tekst):
        ogon = m.group(1)
        if "zoll" in ogon:                 # to rozmiar koła, nie ramy
            continue
        if litera is None:
            ml = re.match(r'\s*(xs|xxl|xl|s|m|l)\b', ogon)
            if ml and ml.group(1) in _RAMA_LITERY:
                litera = ml.group(1).upper()
        if cm is None:
            mc = re.search(r'(\d{2})\s*(?:cm\b|$|\s)', ogon)
            if mc and _RAMA_CM_MIN <= int(mc.group(1)) <= _RAMA_CM_MAX:
                cm = int(mc.group(1))
        if litera and cm:
            break
    if litera and cm:
        return f"{litera} / {cm} cm"
    if litera:
        return litera
    if cm:
        return f"{cm} cm"
    return None


# Litery w kolejności od najdłuższej: inaczej "XS" przeczytałoby się jako "S",
# a "XL" jako "L" - czyli rower trafiłby do cudzego rozmiaru.
_RAMA_LITERA = re.compile(r'^(XS|XXL|XL|S|M|L)\b')


def rama_oferty(oferta) -> object:
    """Zapisany rozmiar ramy oferty ("L", "53 cm", "M / 50 cm") albo None.

    Czyta POLE `rama`, które tracker zapisuje z tytułu I OPISU, a dopiero
    z jego braku próbuje samego tytułu. Zmierzone 12.09.2026: z samego tytułu
    rozmiar da się odczytać w 14% ofert (381 z 2 732 wysłanych), więc czytnik
    tytułowy jest protezą dla wpisów sprzed dołożenia pola, nie rozwiązaniem."""
    return (oferta.get("rama")
            or rozmiar_ramy(oferta.get("title") or "", ""))


def litera_ramy(oferta) -> object:
    """Sama LITERA rozmiaru ("L") albo None. Jedno źródło dla całego repo.

    Rozmiary podane w centymetrach ŚWIADOMIE zostawiamy jako "nie wiem": ten
    sam numer znaczy co innego u Cube'a i u Specialized, a pomyłka kosztuje tu
    odrzucenie dobrego roweru. Wolimy nie wiedzieć niż wiedzieć źle."""
    surowy = rama_oferty(oferta)
    if not surowy:
        return None
    m = _RAMA_LITERA.match(str(surowy).strip().upper())
    return m.group(1) if m else None


def parse_spec_fields(desc: str) -> dict:
    """Wyciąga z opisu: amortyzator (+wersja), skok, osprzęt, ramę, rozmiar,
    generację silnika. Czysta funkcja — testowalna bez sieci. Zwraca WYŁĄCZNIE
    to, czego jest pewna: brak dopasowania = brak klucza, nigdy zgadywanie."""
    if not desc:
        return {}
    d = desc.lower()
    kb = load_spec_kb()
    out = {}

    w = _najlepszy_z_drabinki(d, kb.get("amortyzator_przod"))
    if w:
        out["widelec"], out["widelec_rank"] = w
    elif re.search(r'rock\s?shox|rockshox|\bfox\b', d):
        out["widelec"] = "nieznany model"      # marka jest, model nie — uczciwie

    w = _najlepszy_z_drabinki(d, kb.get("wersja_amortyzatora"))
    if w:
        out["wersja"], out["wersja_rank"] = w

    # skok w mm: tylko z kontekstem, inaczej złapiemy rozmiar koła albo opony
    for m in re.finditer(r'(\d{3})\s*mm', d):
        v = int(m.group(1))
        if 100 <= v <= 220 and _ma_kontekst(d, m.start(), _SKOK_KONTEKST):
            out["skok_mm"] = v
            break

    # napęd: nazwa obok "shimano/przerzutka", ale NIE obok "hamulce"
    w = _najlepszy_z_drabinki(d, kb.get("osprzet"), _GRUPA_KONTEKST, _HAMULCE_KONTEKST)
    if w:
        out["osprzet"], out["osprzet_rank"] = w

    w = _najlepszy_z_drabinki(d, kb.get("rama"))
    if w:
        out["rama"], out["rama_rank"] = w

    r = rozmiar_ramy("", desc)
    if r:
        out["rozmiar"] = r

    # hamulce: te same nazwy co grupy napędowe (XT!), więc również z kontekstem
    w = _najlepszy_z_drabinki(d, kb.get("hamulce"), _HAMULCE_KONTEKST)
    if w:
        out["hamulce"], out["hamulce_rank"] = w

    # generacja silnika Bosch — mocna wskazówka o roczniku (Gen4 = 2020+)
    m = re.search(r'\bgen\.?\s?([2-5])\b', d)
    if m:
        out["bosch_gen"] = int(m.group(1))
    elif "smart system" in d:
        out["bosch_gen"] = 5

    # --- ŁĄCZNY POZIOM WYPOSAŻENIA (1-6) ---------------------------------
    # Pojedyncze cechy mają dziurawe pokrycie (widelec tylko 28% ofert), ale
    # "choć jedna" to już 74%. A że mocno się powtarzają (widelec vs rama:
    # korelacja 0.89 — znając widelec, znasz i ramę), nie ma sensu trzymać ich
    # osobno. Składamy w jeden wskaźnik z tego, co akurat jest w opisie.
    #
    # HAMULCÓW tu NIE MA celowo: po ich dodaniu drabinka przestawała być
    # monotoniczna (poziom 6 wychodził tańszy od 5). Zostają jako informacja,
    # ale do wskaźnika nie wchodzą — sprawdzone na 108 ofertach, nie zgadnięte.
    skladniki = [(out[k] / mx * 6) for k, mx in
                 (("osprzet_rank", 6), ("widelec_rank", 8),
                  ("rama_rank", 2)) if out.get(k)]
    if skladniki:
        out["poziom"] = round(sum(skladniki) / len(skladniki))
        out["poziom_n"] = len(skladniki)     # na ilu cechach oparty = pewność
    return out


def _parse_detail_fields(h: str) -> dict:
    """Wyciąga strukturalne pola ze strony oferty OLX: przebieg, stan,
    a z OPISU (nie z boilerplate strony!) rocznik, baterię i specyfikację."""
    out = {}
    m = re.search(r'Przebieg[^\d]{0,10}(\d[\d\s]*)\s*km', h)
    if m:
        v = int(m.group(1).replace(" ", ""))
        if 0 < v <= 60000:
            out["km"] = v
    m = re.search(r'Stan[:•\s]{1,4}(Nowe|Używane|Jak nowe|Bardzo dobry|Dobry)', h)
    if m:
        out["stan"] = m.group(1)
    # rocznik i Wh TYLKO z treści opisu — cała strona ma lata w stopce/skryptach
    dm = re.search(r'"description":"((?:[^"\\]|\\.)*)"', h)
    if dm:
        desc = dm.group(1)
        ym = (re.search(r'(?:rok(?:u|iem)?|rocznik|model(?:l?jahr)?|bj\.?)\D{0,8}(20(?:1[5-9]|2[0-6]))', desc, re.I)
              or re.search(r'\b(20(?:1[5-9]|2[0-6]))\s*r(?:\b|ok)', desc, re.I)
              # "t2021" / "mj2022" — rocznik sklejony z literą, spotykane w opisach
              or re.search(r'\b[a-z]{1,2}(20(?:1[5-9]|2[0-6]))\b', desc, re.I))
        if ym:
            out["y"] = int(ym.group(1))
        whm = re.search(r'(\d{3})\s*wh\b', desc, re.I)
        if whm and 300 <= int(whm.group(1)) <= 1000:
            out["wh"] = int(whm.group(1))
        out.update(parse_spec_fields(desc))
    return out


def fetch_olx_detail(url: str) -> dict:
    """Pobiera stronę oferty OLX → strukturalny przebieg/stan/rocznik/bateria."""
    r = olx_get(url)
    if r is None or r.status_code != 200:
        return {}
    return _parse_detail_fields(r.text)


# Zdjęcia OLX leżą na CDN pod stałym identyfikatorem pliku:
# .../v1/files/fih7e57o7ztm-PL/image;s=1000x563 → "fih7e57o7ztm".
# To NAJMOCNIEJSZY dostępny dowód, że dwa ogłoszenia to ten sam rower —
# odcisk po tytule i cenie pęka dokładnie na sprzedawcach, którzy zbijają
# cenę przed wznowieniem (33 z 41 zmian ceny w dzienniku to obniżki).
_FOTO_ID = re.compile(r'/files/([A-Za-z0-9]+)-\w+/')


def _fakty_z_ad_json(ad) -> dict:
    """Ogłoszenie OLX (JSON ze strony) → same FAKTY, których potrzebuje dozorca.

    Nic tu nie jest wnioskiem — żadnego "sprzedane", "wygasłe" ani "sklep spamuje".
    Wnioski liczy się osobno z dziennika i można je przeliczyć od zera.
    Pusty słownik znaczy "nie wiem", nigdy "nie ma"."""
    if not isinstance(ad, dict):
        return {}
    out = {}
    for klucz, pole in (("wystawiono", "createdTime"), ("wazne_do", "validToTime"),
                        ("odswiezono", "lastRefreshTime"), ("status", "status")):
        v = ad.get(pole)
        if isinstance(v, str) and v:
            out[klucz] = v
    if out.get("odswiezono") is None and isinstance(ad.get("pushupTime"), str):
        out["odswiezono"] = ad["pushupTime"]
    if isinstance(ad.get("isBusiness"), bool):
        out["firma"] = ad["isBusiness"]
    user = ad.get("user")
    if isinstance(user, dict) and user.get("id") is not None:
        out["sprzedawca"] = str(user["id"])
    loc = ad.get("location")
    if isinstance(loc, dict):
        if loc.get("cityName"):
            out["miasto"] = loc["cityName"]
        if loc.get("regionName"):
            out["wojewodztwo"] = loc["regionName"]
    foty = []
    for p in (ad.get("photos") or []):
        adres = p if isinstance(p, str) else (p or {}).get("link") or ""
        m = _FOTO_ID.search(adres)
        if m and m.group(1) not in foty:
            foty.append(m.group(1))
    if foty:
        out["zdjecia"] = foty
    cena = ((ad.get("price") or {}).get("regularPrice") or {})
    if isinstance(cena.get("negotiable"), bool):
        out["negocjowalna"] = cena["negotiable"]
    return out


def olx_offer_facts(url: str) -> dict:
    """Pobiera stronę oferty → fakty z jej JSON-a. {} gdy się nie udało."""
    r = olx_get(url, timeout=15)
    if r is None or r.status_code != 200:
        return {}
    return _fakty_z_ad_json(parse_olx_ad_json(r.text))


# Części zatruwają pulę cen (ładowarka 550 zł liczona jak rower!) i "sprzedaże"
PART_SLUG_WORDS = ["bateria", "akumulator", "ladowarka", "wyswietlacz", "display",
                   "silnik", "sztyca", "widelec", "amortyzator", "przerzutka", "kaseta"]


def _is_shop_slug(url: str) -> bool:
    """Sklep/komis (raty, F-VAT) = zwykle nowy rower — zawyża porównanie z używanym."""
    slug = url.split("/d/oferta/")[-1].lower()
    return bool(re.search(r'\braty\b|f-?vat|leasing', slug))


def olx_relevant_offers(query: str, offers: dict) -> dict:
    """Filtruje wyniki OLX do ofert FAKTYCZNIE dotyczących modelu.
    1) Słowa modelu muszą być w slugu W KOLEJNOŚCI, blisko siebie i blisko
       początku — odrzuca keyword-stuffing ('...trek-enduro-focus-trail-jam-
       mtb-rail' w ogonie tytułu Cube'a wpadało do wyników Trek Rail).
    2) Części (ładowarki/baterie/wyświetlacze): słowo części na starcie sluga
       ALBO słowo części + cena <30% mediany puli."""
    tokens = [t for t in query.lower().split() if t and not t.isdigit()]

    def find_tok(slug, tok):
        # token jako CAŁY człon sluga (granice na '-') — 'rail' nie może
        # matchować wewnątrz 'trail'
        m = re.search(r'(?:^|-)' + re.escape(tok) + r'(?=-|$)', slug)
        if not m:
            return None
        i = m.start() + (1 if m.group(0).startswith('-') else 0)
        return (i, i + len(tok))

    # zwarte okno: wszystkie słowa modelu blisko siebie (dowolna kolejność —
    # 'levo turbo' vs 'turbo levo' to ten sam rower), blisko początku tytułu
    window = sum(len(t) for t in tokens) + len(tokens) + 14
    stage1 = {}
    for url, price in offers.items():
        slug = url.split("/d/oferta/")[-1].lower()
        spans = [find_tok(slug, t) for t in tokens]
        if any(s is None for s in spans):
            continue
        first = min(s[0] for s in spans)
        last = max(s[1] for s in spans)
        if first > 45 or (last - first) > window:
            continue
        head = slug[:24]
        if any(w in head for w in PART_SLUG_WORDS) and not slug.startswith("rower"):
            continue
        stage1[url] = price
    if not stage1:
        return stage1
    med = statistics.median(list(stage1.values()))
    out = {}
    for url, price in stage1.items():
        slug = url.split("/d/oferta/")[-1].lower()
        if price < 0.3 * med and any(w in slug for w in PART_SLUG_WORDS):
            continue  # tanie + słowo części = część
        out[url] = price
    return out


def parse_olx_slug(url: str):
    """Wyciąga (rok, przebieg_km, bateria_wh) z URL-a oferty OLX — bez dodatkowych
    zapytań. Kotwice (rok/r, km, wh) minimalizują false-posity. None gdy brak."""
    slug = url.split("/d/oferta/")[-1].lower()
    ym = re.search(r'(?:^|-)(20(?:1[5-9]|2[0-6]))(?:r\b|-rok|-|$)', slug)
    km = re.search(r'(\d{2,5})-?km\b', slug)
    wh = re.search(r'(\d{3})-?wh\b', slug)
    year = int(ym.group(1)) if ym else None
    kmv = int(km.group(1)) if km and 10 <= int(km.group(1)) <= 30000 else None
    whv = int(wh.group(1)) if wh and 300 <= int(wh.group(1)) <= 1000 else None
    return year, kmv, whv


def trimmed_median(vals, trim=0.15):
    """Mediana po odcięciu skrajnych `trim` z obu stron — zabija outliery
    (części, premium-warianty, scamy, inny model w wynikach)."""
    if not vals:
        return None
    s = sorted(vals)
    k = int(len(s) * trim)
    core = s[k:len(s) - k] if len(s) > 2 * k + 1 else s
    return int(statistics.median(core))


def wh_class(wh):
    """Klasa baterii (S<550, M<700, L) — 500/625/750 Wh to duża różnica ceny."""
    if not wh:
        return None
    return "S" if wh < 550 else "M" if wh < 700 else "L"


# === CENNIK CECH: ile rynek realnie płaci za rocznik, baterię, wyposażenie ====
# Zastępuje łamane "pasma podobieństwa", które traktowały nieznany atrybut jak
# pasujący (stąd stary Cube 2018/400 Wh wyceniany jak model 2023/750 Wh).
# Współczynniki NIE są wymyślone — liczą się z zebranych ofert (rynek_pl.jsonl)
# i lądują w cennik_cech.json wraz z liczbą ofert, na których się opierają.
CENNIK_FILE = Path("cennik_cech.json")
RYNEK_FILE = Path("rynek_pl.jsonl")
_cennik_cache = None


def _regresja_1d(xs, ys):
    """Najprostsza regresja liniowa y = a*x + b. Zwraca (a, b) albo None.
    Bez zależności zewnętrznych — wszystko ma być sprawdzalne gołym okiem."""
    n = len(xs)
    if n < 5:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    war = sum((x - mx) ** 2 for x in xs)
    if war <= 0:
        return None
    a = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / war
    return a, my - a * mx


# Cechy: (klucz, jak przeliczyć ofertę na liczbę, opis dla właściciela)
#
# DLACZEGO NIE MA TU GENERACJI SILNIKA (sprawdzone 21.08.2026, 599 ofert):
# Pomysł był taki, żeby wywnioskować rocznik z tego, co oferta podaje częściej
# niż rok. Dwie ślepe uliczki, obie odrzucone na podstawie danych, nie przeczuć:
#
#  1. rocznik z BATERII — błędne koło. Bateria już jest wyceniana poniżej, więc
#     jej wpływ liczyłby się drugi raz, przemycony przez rocznik.
#  2. GENERACJA SILNIKA jako osobna cecha — wygląda obiecująco (przy tej samej
#     baterii 750 Wh: Gen4 to rocznik ~2023, Gen5 ~2024; surowo Gen5 jest droższy,
#     14 900 vs 13 999 zł). Ale współczynnik liczy się PO odjęciu wpływu baterii,
#     rocznika i wyposażenia — a nowszy silnik chodzi w parze z nimi wszystkimi.
#     Po odliczeniu zostaje sam szum: wyszło -6,4%, czyli nowszy silnik rzekomo
#     obniża cenę, a wycena odwracała się (Gen5 tańszy od Gen4). Odrzucone.
#
# Wniosek: rocznik, bateria i wyposażenie wyczerpują to, co da się tu wycisnąć.
# Kolejne cechy z tej rodziny będą powtarzać te same informacje.
CECHY = [
    ("poziom", lambda r: r.get("poziom"), "stopień wyposażenia (1-6)"),
    ("wh", lambda r: (r["wh"] / 100) if r.get("wh") else None, "każde 100 Wh baterii"),
    ("y", lambda r: r.get("y"), "każdy rocznik nowszy"),
    ("km", lambda r: (r["km"] / 1000) if r.get("km") is not None else None,
     "każde 1000 km przebiegu"),
]


def odduplikuj(rows):
    """Ten sam rower wystawiony wielokrotnie liczy się RAZ. Bez tego cennik
    ustala sklep, który najgłośniej spamuje (realny przypadek: 40 z 46
    obserwacji '500 Wh' to była jedna oferta jednego sprzedawcy)."""
    # Ta sama OFERTA wraca do dziennika przy każdym skanie: w rynek_pl.jsonl
    # było 1427 wierszy na 417 adresów, czyli każdy rower liczony 3-4 razy.
    # Klucz po sprzedawcy tego nie łapał, bo w tym pliku pola `sprzedawca`
    # w ogóle nie ma — warunek `is not None` czynił funkcję bezczynną.
    # Zostawiamy OSTATNIĄ obserwację oferty, bo niesie aktualną cenę.
    po_url, kolejnosc = {}, []
    reszta, widziane = [], set()
    for r in rows:
        url = r.get("url")
        if url:
            if url not in po_url:
                kolejnosc.append(url)
            po_url[url] = r
            continue
        klucz = (r.get("sprzedawca"), r.get("model"), r.get("cena"))
        if klucz[0] is not None and klucz in widziane:
            continue
        widziane.add(klucz)
        reszta.append(r)
    return [po_url[u] for u in kolejnosc] + reszta


def zbuduj_cennik(rows):
    """Liczy, ile rynek dopłaca za każdą cechę. Metoda: kolejno dla każdej cechy
    regresja na logarytmie ceny (współczynnik = zmiana procentowa), licząc tylko
    oferty, które TĘ cechę podają — dzięki temu dziurawe dane nie wykluczają
    oferty z całej analizy. Zwraca dict gotowy do zapisu w cennik_cech.json."""
    # Rocznik i przebieg z adresu ZANIM cokolwiek policzymy - inaczej cennik
    # cech liczy wagę rocznika na jednej czwartej ofert zamiast na jednej
    # trzeciej (patrz `uzupelnij_z_adresu`).
    rows = [uzupelnij_z_adresu(r) for r in rows]
    rows = odduplikuj([r for r in rows if (r.get("cena") or 0) > 500])
    if len(rows) < 20:
        return None
    reszty = {i: math.log(r["cena"]) for i, r in enumerate(rows)}
    baza = statistics.median(reszty.values())
    for i in reszty:
        reszty[i] -= baza
    cennik = {}
    for klucz, konwersja, opis in CECHY:
        wartosci = [konwersja(r) for r in rows if konwersja(r) is not None]
        if len(wartosci) < 5:
            continue
        # ŚRODEK = typowy rower na rynku. Wszystko liczymy jako odchyłkę od niego,
        # dzięki czemu "cecha nieznana" znaczy po prostu "typowa", a nie zero.
        srodek = statistics.median(wartosci)
        pary = [(konwersja(r) - srodek, reszty[i]) for i, r in enumerate(rows)
                if konwersja(r) is not None]
        wynik = _regresja_1d([x for x, _ in pary], [y for _, y in pary])
        if not wynik:
            continue
        a, b = wynik
        cennik[klucz] = {"wspolczynnik": round(a, 4), "srodek": srodek,
                         "zmiana_ceny_pct": round((math.exp(a) - 1) * 100, 1),
                         "n_ofert": len(pary), "opis": opis}
        for i, r in enumerate(rows):        # zdejmij wyjaśnioną część i licz dalej
            x = konwersja(r)
            if x is not None:
                reszty[i] -= a * (x - srodek) + b
    return {"_opis": "Ile rynek doplaca za kazda ceche. Policzone z ofert OLX, "
                     "nie wymyslone. 'zmiana_ceny_pct' = o tyle % zmienia sie "
                     "cena na kazda jednostke cechy.",
            "data": date.today().isoformat(), "n_ofert": len(rows),
            "cena_bazowa": int(math.exp(baza)), "cechy": cennik}


def load_cennik():
    global _cennik_cache
    if _cennik_cache is None:
        try:
            _cennik_cache = json.loads(CENNIK_FILE.read_text(encoding="utf-8"))
        except Exception:
            _cennik_cache = {}
    return _cennik_cache


_rynek_cache = None


_SLUG_OLX = re.compile(r'/oferta/(.+?)-(?:CID|ID)')


def tytul_z_adresu_olx(url: str) -> str:
    """Tytuł ogłoszenia wydobyty z adresu OLX.

    OLX wkleja cały tytuł w adres ("/oferta/370-km-cube-stereo-hybrid-140-
    race-shimano-xt-l-xl-CID767-ID...."), a `rynek_pl.jsonl` zapisuje adres
    przy każdej ofercie. Tytuł jest więc na dysku od zawsze, tylko nikt go
    stamtąd nie czytał."""
    m = _SLUG_OLX.search(url or "")
    return m.group(1).replace("-", " ") if m else ""


def uzupelnij_z_adresu(rec: dict) -> dict:
    """Dopisuje ROCZNIK i PRZEBIEG odczytane z adresu, gdy brakuje ich w polach.

    Po co: polscy sprzedawcy rzadko wypełniają pola strukturalne. Zmierzone
    12.09.2026 na 910 unikalnych ofertach z `rynek_pl.jsonl`: rocznik znany
    w 22%, przebieg w 24%. A wycena porównuje do nich niemieckie rowery, więc
    dla trzech czwartych porównań nie wie, z którego roku jest odniesienie -
    i to jest przyczyna, dla której Specialized Levo z 2020 dostał medianę
    14 098 zł, policzoną głównie z nowszych roczników.

    Zmierzony odzysk z samego adresu: rocznik 22% -> 27%, przebieg 24% -> 35%.
    Zero nowych żądań, samo czytanie tego, co już leży w pliku.

    NIE NADPISUJE pól, które już są. Pole strukturalne pochodzi z formularza
    OLX i jest pewniejsze niż tytuł, w którym "2023" bywa numerem modelu."""
    if not isinstance(rec, dict):
        return rec
    tytul = tytul_z_adresu_olx(rec.get("url") or "")
    if not tytul:
        return rec
    if not rec.get("y"):
        y = extract_year(tytul)
        if y:
            rec["y"] = y
    if rec.get("km") is None:
        km = parse_mileage(_extract_mileage(tytul, ""))
        if km is not None:
            rec["km"] = km
    return rec


def oferty_z_rynku(query: str, max_wiek_dni: int = 21):
    """Oferty PL z ZAPISANEGO rynku (rynek_pl.jsonl) zamiast z sieci.

    OLX blokuje serwerownię GitHuba — HTTP 403, potwierdzone 21.08.2026, zarówno
    strona, jak i API. Runner NIE MOŻE więc pobierać ofert na żywo. Zbieranie robi
    maszyna z normalnym łączem i wrzuca wynik do repo; tutaj tylko go czytamy.
    Dzięki temu wycena działa mimo blokady — po prostu na danych sprzed doby."""
    global _rynek_cache
    if _rynek_cache is None:
        _rynek_cache = []
        try:
            for line in RYNEK_FILE.open(encoding="utf-8"):
                try:
                    _rynek_cache.append(uzupelnij_z_adresu(json.loads(line)))
                except Exception:
                    continue
        except Exception:
            pass
    if not _rynek_cache:
        return []
    granica = (date.today() - timedelta(days=max_wiek_dni)).isoformat()
    po_url = {}
    for r in _rynek_cache:                  # ostatni zapis danej oferty wygrywa
        if r.get("ts", "") >= granica and r.get("cena") and r.get("url"):
            po_url[r["url"]] = r
    if not po_url:
        return []
    trafne = olx_relevant_offers(query, {u: r["cena"] for u, r in po_url.items()})
    return [po_url[u] for u in trafne]


def _mnoznik(rec, cennik):
    """Ile razy droższy jest ten rower od TYPOWEGO na rynku — z jego znanych
    cech. Cecha nieznana = zakładamy typową (odchyłka zero), więc oferta bez
    podanego rocznika nie wywraca porównania. Zwraca (mnożnik, ile_cech)."""
    cechy = (cennik or {}).get("cechy") or {}
    log_sum, znane = 0.0, 0
    for klucz, konwersja, _ in CECHY:
        x = konwersja(rec)
        c = cechy.get(klucz) or {}
        if x is None or "srodek" not in c or "wspolczynnik" not in c:
            continue                    # cennik w starym/ułomnym formacie — pomijamy
        log_sum += c["wspolczynnik"] * (x - c["srodek"])
        znane += 1
    return math.exp(log_sum), znane


def wycen_z_cennikiem(oferty, ref, cennik=None):
    """Wycena przez PRZELICZENIE każdej oferty na specyfikację naszego roweru.
    Zamiast szukać bliźniaka (i udawać, że nieznany atrybut pasuje), bierzemy
    cenę oferty i korygujemy ją o różnicę cech — jak rzeczoznawca.

    oferty: lista dictów z 'cena' + rozpoznanymi cechami
    ref:    dict z cechami wycenianego roweru
    Zwraca dict albo None, gdy nie da się wycenić rzetelnie."""
    cennik = cennik if cennik is not None else load_cennik()
    if not (cennik or {}).get("cechy") or not oferty:
        return None
    m_ref, znane_ref = _mnoznik(ref, cennik)
    if not znane_ref:
        return None                     # o naszym rowerze nie wiemy NIC — nie zgadujemy
    przeliczone = []
    for o in oferty:
        if not o.get("cena"):
            continue
        m_o, znane_o = _mnoznik(o, cennik)
        if not znane_o:
            continue                    # o tej ofercie nie wiemy nic — pomijamy
        przeliczone.append(o["cena"] * m_ref / m_o)
    if len(przeliczone) < 4:
        return None
    s = sorted(przeliczone)
    return {"cena": trimmed_median(przeliczone), "n": len(przeliczone),
            "widelki": (int(s[len(s) // 4]), int(s[3 * len(s) // 4])),
            "cech_znanych": znane_ref,
            "pewnosc": "wysoka" if znane_ref >= 3 and len(przeliczone) >= 12
                       else "srednia" if znane_ref >= 2 and len(przeliczone) >= 6
                       else "niska"}


def olx_comparable_price(offers: dict, ref_year=None, ref_km=None, ref_wh=None, details=None):
    """Cena OLX dopasowana do KONKRETNEGO roweru (rocznik/przebieg/bateria).
    Strukturalne dane ze stron ofert (details) wygrywają nad zgadywaniem
    z URL-a. Nowe/sklepowe oferty wykluczane, dopóki starcza używanych
    (nasz rower z DE jest używany — nówka z ratami zawyża porównanie).
    Degradacja łagodna: od najostrzejszego pasa do całej populacji.
    Zwraca (cena, etykieta_metody, liczba_ofert_w_pasie)."""
    if not offers:
        return None, None, 0
    parsed = []
    for url, price in offers.items():
        y, k, w = parse_olx_slug(url)
        d = (details or {}).get(url) or {}
        if d.get("km") is not None:
            k = d["km"]              # strukturalny przebieg ze strony oferty
        if d.get("y"):
            y = d["y"]               # rocznik z opisu oferty
        if d.get("wh"):
            w = d["wh"]              # bateria z opisu oferty
        is_new = d.get("stan") == "Nowe" or _is_shop_slug(url)
        parsed.append({"p": price, "y": y, "k": k, "w": w, "new": is_new})
    ref_cls = wh_class(ref_wh)

    def band(pool, use_km, use_year, use_wh):
        out = []
        for o in pool:
            # wykluczamy tylko gdy ZNAMY atrybut po obu stronach i się różni
            if use_year and ref_year and o["y"] and abs(o["y"] - ref_year) > 1:
                continue
            if use_wh and ref_cls and o["w"] and wh_class(o["w"]) != ref_cls:
                continue
            if use_km and ref_km and o["k"] and abs(o["k"] - ref_km) > 1000:
                continue
            out.append(o["p"])
        return out

    used = [o for o in parsed if not o["new"]]
    ladders = ([(used, ", używane")] if len(used) >= 4 else []) + [(parsed, "")]
    for pool, suffix in ladders:
        for vals, label in [
            (band(pool, True, True, True),   "rok±1, przebieg, bateria"),
            (band(pool, False, True, True),  "rok±1, bateria"),
            (band(pool, False, True, False), "rok±1"),
            (band(pool, False, False, True), "bateria"),
        ]:
            if len(vals) >= 4:
                return trimmed_median(vals), label + suffix, len(vals)
        if suffix and len(band(pool, False, False, False)) >= 4:
            # żaden pas atrybutów nie zadziałał, ale mamy dość używanych
            vals = band(pool, False, False, False)
            return trimmed_median(vals), "używane (przycięte)", len(vals)
    allp = [o["p"] for o in parsed]
    return trimmed_median(allp), "cały model (przycięte)", len(allp)


def olx_compare_str(query: str, offers: dict, comparable=None) -> str:
    """Mini-porównywarka: zakres cen modelu na OLX + cena porównywalna + link."""
    if not offers:
        return ""
    prices = sorted(offers.values())
    med = prices[len(prices) // 2]
    line = (
        f"\n🇵🇱 OLX \"{query}\": {len(prices)} ofert · "
        f"{prices[0]:,}–{prices[-1]:,} zł · mediana {med:,} zł".replace(",", " ")
    )
    if comparable and comparable[0]:
        cp, method, n = comparable
        line += f"\n🎯 Porównywalne ({method}, {n} ofert): ~{cp:,} zł".replace(",", " ")
    demand = get_demand_price(query)
    if demand:
        line += f" · 💸 realnie schodzą po ~{demand:,} zł".replace(",", " ")
    line += f"\n🔍 {olx_search_url(query)}"
    return line


OLX_WATCH_FILE = Path("olx_watch.json")
DEMAND_MAX_AGE_DAYS = 14   # świeżość ceny popytu
SOLD_FAST_DAYS = 14        # oferta znikła w <= tyle dni = realnie sprzedana po tej cenie
LIQUIDITY_MAX_DAYS = 45    # dłużej = prawdopodobnie porzucone ogłoszenie, nie sprzedaż
LIQUIDITY_MIN_SAMPLES = 5  # tyle sprzedaży trzeba by płynność była wiarygodna

_olx_watch_cache = None


def olx_offer_state(url: str):
    """Jedno pobranie strony oferty → (czy martwa, fakty o niej).

    Zwraca {"gone": True/False/None, "fakty": {...} lub None}.

    Dwie rzeczy naraz, bo martwa strona NIE NIESIE POWODU zdjęcia — ma tylko
    {"statusCode": 410} (zmierzone 24.08.2026). Wszystko, czego potrzeba do
    odróżnienia "wygasło samo" od "sprzedawca zdjął", trzeba złapać, DOPÓKI
    oferta żyje. Dlatego przy każdym sprawdzeniu, które zastaje ofertę żywą,
    odświeżamy jej fakty — w tym `wazne_do`, które sprzedawca przesuwa
    odświeżeniem ogłoszenia."""
    r = olx_get(url, timeout=10, allow_redirects=False)
    if r is None:
        return {"gone": None, "fakty": None}
    if r.status_code in (404, 410):
        return {"gone": True, "fakty": None}
    if r.status_code in (301, 302, 308):
        return {"gone": True, "fakty": None}   # przekierowanie na kategorię = zdjęta
    if r.status_code != 200:
        return {"gone": None, "fakty": None}   # 403/429 = blokada, a nie śmierć
    ad = parse_olx_ad_json(r.text)
    fakty = _fakty_z_ad_json(ad)
    if fakty and fakty.get("status") == "active":
        return {"gone": False, "fakty": fakty}   # twardy dowód życia z JSON-a
    return {"gone": _judge_olx_dead(r.text), "fakty": fakty}


def olx_offer_gone(url: str):
    """Czy oferta OLX naprawdę zniknęła (sprzedana/usunięta)?
    Wymaga POZYTYWNEGO dowodu śmierci — frazy typu 'nieaktualne' siedzą
    w pakiecie tłumaczeń KAŻDEJ strony OLX (to zatruło nam 394 fałszywe
    'sprzedaże' z medianą 0 dni). Zwraca True/False/None (nie wiadomo)."""
    return olx_offer_state(url)["gone"]


def _judge_olx_dead(h: str):
    """Ocena treści strony oferty: True=martwa, False=żywa, None=nie wiadomo."""
    if re.search(r'status\\?":\\?"active', h) or "schema.org/InStock" in h:
        return False      # twardy dowód życia
    m = re.search(r'status\\?":\\?"(\w+)', h)
    if m and m.group(1) in ("removed_by_user", "outdated", "expired", "finished",
                            "disabled", "moderated", "removed"):
        return True
    return None           # brak dowodu w żadną stronę — nie zgadujemy


def load_olx_watch() -> dict:
    global _olx_watch_cache
    if _olx_watch_cache is None:
        if OLX_WATCH_FILE.exists():
            try:
                _olx_watch_cache = json.loads(OLX_WATCH_FILE.read_text())
            except Exception:
                _olx_watch_cache = {}
        else:
            _olx_watch_cache = {}
    return _olx_watch_cache


def get_liquidity(query: str, cena=None):
    """Medianowy czas do zejścia oferty w PL (dni). None gdy za mało danych.

    LICZONE OD DATY WYSTAWIENIA NA OLX, nie od naszego pierwszego widzenia -
    i to jest cała różnica. Stara wersja brała `days` z `sold_fast`, czyli
    wiek widziany przez bota. Zmierzone 20.09.2026: oferta żyje MEDIANĘ 30 DNI,
    zanim bot ją w ogóle zobaczy (49% ma wtedy więcej niż 30 dni), więc każda
    wyglądała na młodą. Mediana wychodziła 8-9 dni przy realnej 12, a 3442
    zapisy "sprzedane" stały obok 56 "wygasłych" - przy 42% ofert wiszących
    ponad 60 dni taka proporcja nie jest możliwa.

    Liczbę oddaje `plynnosc.py` z dziennika dozorcy (zdarzenia/olx-*.jsonl),
    bo tam leżą fakty: data wystawienia, data ważności i potwierdzone zejście.
    Gdy danych jest za mało, wraca None - bez podmiany na starą, zawyżoną
    miarę, bo ROI liczone z 8 dni zamiast 12 jest o połowę za wysokie."""
    try:
        import plynnosc
        w = plynnosc.dni_do_zejscia(q=query, cena=cena)
    except Exception as e:
        log.warning(f"plynnosc niedostępna ({type(e).__name__}) - płynność: nie wiem")
        return None
    return w["dni"] if w else None


def olx_sell_forecast(query: str, asking_price=None):
    """Odpowiada 'ile ma stać / czy się sprzeda' dla modelu — z realnego cyklu
    życia ofert OLX. Zwraca dict albo None gdy za mało danych.
    - clearing: mediana ceny DOMYKAJĄCEJ (za ile realnie schodzi)
    - days: mediana dni do sprzedaży
    - sell_through: % ofert które faktycznie zeszły (vs wygasłe)
    - drop_pct: o ile średnio sprzedawcy zbijają cenę przed sprzedażą
    - verdict: ocena twojej ceny wywoławczej vs cena domykająca"""
    w = load_olx_watch().get(query)
    if not w:
        return None
    sold = [s for s in w.get("sold_fast", []) if isinstance(s, dict)]
    clearing = [s["price"] for s in sold if s.get("price")]
    if len(clearing) < LIQUIDITY_MIN_SAMPLES:
        return None
    out = {
        "clearing": int(statistics.median(clearing)),
        "days": get_liquidity(query),
        "sell_through": w.get("sell_through_pct"),
        "drop_pct": w.get("typical_drop_pct"),
        "n": len(clearing),
    }
    if asking_price:
        cm = out["clearing"]
        diff = (asking_price - cm) / cm * 100
        if diff <= 2:
            out["verdict"] = f"✅ cena OK — na poziomie ceny domykającej ({cm:,} zł)".replace(",", " ")
        elif diff <= 8:
            out["verdict"] = f"🟡 lekko za wysoko (+{diff:.0f}% vs {cm:,} zł) — obserwujący czekają na obniżkę".replace(",", " ")
        else:
            out["verdict"] = f"🔴 za wysoko (+{diff:.0f}% vs {cm:,} zł) — zbij by sprzedać".replace(",", " ")
    return out


# === SILNIK DYNAMICZNEJ WYCENY SPRZEDAŻY (bot do sprzedaży, punkt 1) ===========
# Odpowiada: "mam TEN rower — za ile go wystawić, za ile realnie zejdzie, w ile
# dni?" Łączy poziom rynku (olx_comparable_price, dopasowany do rocznika/przebiegu/
# baterii) z realnym cyklem życia ofert (olx_sell_forecast: cena domykająca, dni,
# % sprzedaży, typowy zjazd z ceny). Bez danych o realnych sprzedażach schodzi
# łagodnie do szacunku z cen wywoławczych i uczciwie to oznacza.
DROP_DEFAULT_PCT = 10  # zakładany zjazd z ceny gdy brak danych o realnym zbijaniu
CLEARING_HAIR_MIN = 0.6   # domykająca poniżej 60% wywoławczej = dane do wyrzucenia,
                          # nie okazja (ten sam próg co po stronie zakupu)


# WERSJA MODELU DO ZAWĘŻENIA PULI PORÓWNAWCZEJ (17.09.2026).
#
# `olx_relevant_offers` wyrzuca z zapytania wszystkie liczby, więc "trek rail
# 5", "trek rail 9" i "trek rail" dostają TĘ SAMĄ pulę - zmierzone: 42 oferty
# o identycznych adresach, w tym Rail 5, 7, 9.5, 9.7, 9.8 i 9.9 naraz. Dla
# Cube'a "stereo hybrid 120", "140" i "160" to wspólne 284 oferty razem z ONE44.
#
# Wyrzucanie liczb NIE jest błędem samym w sobie, tylko łatką na inny: wzorce
# w MODEL_PATTERNS łapią KAŻDĄ liczbę po nazwie, więc w danych popytu stoją
# klucze "cube stereo hybrid 2021" (rocznik), "750" (bateria), "29" (koło).
# Samo zdjęcie łatki zabiłoby wycenę tych rowerów - "cube stereo hybrid 2021"
# zostałby z pulą ZERO. Dlatego klucze i dane popytu zostają nietknięte,
# a wersję czytamy osobno i tylko do zawężenia puli przy wycenie.
#
# Wersje wyłącznie tam, gdzie mieszanie jest zmierzone. Liczby na tych samych
# rowerach, pula dziś -> pula tej samej wersji:
#     Cube Stereo Hybrid 120 Pro 625 (2022)   9 900 ->  9 434 zł
#     Cube Stereo Hybrid 160 HPC SLX (2023)  13 539 -> 14 487 zł
#     Trek Rail 9.8 (2023)                   17 507 -> 19 081 zł
# Modele podstawowe w dół, topowe w górę - czyli dokładnie to, co pula
# wymieszana spłaszczała.
def wariant_modelu(tekst):
    """Wersja modelu z tytułu ALBO ze sluga OLX ("rail-9-7" = "rail 9.7")."""
    t = (tekst or "").lower().replace("-", " ")
    if re.search(r"stereo\s+hybrid", t):
        m = re.search(r"\bone\s?(22|44|55|77)\b", t)
        if m:
            return f"stereo one{m.group(1)}"
        m = re.search(r"stereo\s+hybrid\s+(?:hp[ac]\s+)?(1[2-6]0)\b", t)
        return f"stereo {m.group(1)}" if m else None
    if re.search(r"\btrek\b", t) and re.search(r"\brail\b", t):
        m = re.search(r"\brail\s*\+?\s*(9[\s.]?[5789]|[579])\b", t)
        if not m:
            return None
        return "rail " + re.sub(r"^9[\s.]?([5789])$", r"9.\1", m.group(1))
    if re.search(r"\b(?:levo|kenevo)\b", t):
        # kolejność MA znaczenie: "comp alloy" zawiera słowo "comp"
        for nazwa, wz in (("s-works", r"\bs\s?works\b"), ("pro", r"\bpro\b"),
                          ("expert", r"\bexpert\b"),
                          ("comp alloy", r"\bcomp\b.{0,12}\balloy\b|\balloy\b.{0,12}\bcomp\b"),
                          ("comp", r"\bcomp\b"), ("alloy", r"\balloy\b")):
            if re.search(wz, t):
                return f"levo {nazwa}"
    return None


def zawez_do_wariantu(oferty, tytul):
    """(oferty tej samej wersji, wersja) albo (oferty bez zmian, None)."""
    w = wariant_modelu(tytul)
    if not w:
        return oferty, None
    return [o for o in oferty
            if wariant_modelu(tytul_z_adresu_olx(o.get("url") or "")) == w], w


def oferty_z_cechami(offers, details):
    """Łączy {url: cena} z rozpoznanymi cechami w listę dla cennika cech.
    Dane ze strony oferty biją zgadywanie z adresu URL."""
    out = []
    for url, price in (offers or {}).items():
        y, k, w = parse_olx_slug(url)
        rec = dict((details or {}).get(url) or {})
        rec["cena"] = price
        # Adres potrzebny do zawężenia puli do tej samej wersji modelu
        # (`zawez_do_wariantu`). Ścieżka "z repo" miała go zawsze.
        rec.setdefault("url", url)
        rec.setdefault("y", y)
        rec.setdefault("wh", w)
        if rec.get("km") is None and k is not None:
            rec["km"] = k
        out.append(rec)
    return out


def build_price_reco(offers, details, forecast, ref_year=None, ref_km=None,
                     ref_wh=None, mode="balans", ref_poziom=None):
    """Czysta funkcja (bez sieci — testowalna). Zwraca dict albo None.

    METODA — DWA KROKI I KONIEC (ustalone z właścicielem 24.08.2026):
      1. Poziom rynku DLA TEGO ROWERU: bierzemy oferty podobnych modeli
         i przeliczamy każdą na naszą specyfikację (cennik cech).
      2. Minus targ przy oględzinach (NEGO_NA_MIEJSCU) — kupujący przyjedzie
         i swoje utarguje. To wszystko.

    Trzeciego kroku NIE MA i nie będzie. Ceny domykającej nie da się zmierzyć:
    prywatni sprzedawcy jej nie publikują, a „cena tuż przed zniknięciem oferty"
    to nadal cena WYWOŁAWCZA — targ odbył się po niej i nigdzie nie jest zapisany.
    Żadne dłuższe zbieranie danych tego nie zmieni, bo tej liczby nie ma w źródle.

    Zmierzone 24.08.2026, zanim ta warstwa poszła do kosza: proporcja
    „domykająca/wywoławcza" wyszła 0,88–1,07 dla wszystkich 8 modeli, czyli
    w praktyce jeden. Nie wnosiła NIC poza ryzykiem, że nadpisze wycenę po
    cechach — i dokładnie to trzykrotnie robiła.

    mode wybiera, gdzie stanąć w ZMIERZONYM rozrzucie podobnych ofert:
    'szybko' = jak najtańsi (dolny kwartyl), 'balans' = jak typowi (mediana),
    'max' = jak najdrożsi (górny kwartyl). Żadnych wymyślonych procentów."""
    wyc = wycen_z_cennikiem(oferty_z_cechami(offers, details),
                            {"y": ref_year, "km": ref_km, "wh": ref_wh,
                             "poziom": ref_poziom})
    if wyc:
        market, n = wyc["cena"], wyc["n"]
        method = f"cennik cech — {wyc['cech_znanych']} cech znanych"
        widelki = wyc["widelki"]
    else:
        cp, method, n = olx_comparable_price(offers, ref_year, ref_km, ref_wh, details)
        if not cp:
            return None
        market, widelki = cp, None

    if mode == "szybko":
        listing = widelki[0] if widelki else market
    elif mode == "max":
        listing = widelki[1] if widelki else market
    else:
        listing = market
    listing = int(listing)

    # Jedyne odjęcie w całej wycenie. Świadomie ZAŁOŻONE, nie zmierzone —
    # i tak podpisane w wiadomości, żeby nikt nie wziął tego za pomiar.
    dostaniesz = cena_sprzedazy_realna(listing)
    room = listing - dostaniesz

    conf = "wysoka" if n >= 12 else "średnia" if n >= LIQUIDITY_MIN_SAMPLES else "niska"

    return {"n": n, "method": method, "market": market, "listing": listing,
            "dostaniesz": dostaniesz, "room": room, "widelki": widelki,
            "days": (forecast or {}).get("days"), "mode": mode,
            "nego_pct": round(NEGO_NA_MIEJSCU * 100), "confidence": conf}


def format_price_reco(query, r, ref_year=None, ref_km=None, ref_wh=None) -> str:
    """Składa wiadomość Telegram (HTML) z rekomendacji wyceny."""
    z = lambda v: f"{int(v):,}".replace(",", " ")
    if not r:
        return (f"🤷 Za mało ofert OLX dla „<b>{query}</b>”, żeby wycenić rzetelnie.\n"
                f"Sprawdź pisownię modelu albo podaj ogólniej (np. sama marka+model).")
    spec = []
    if ref_year: spec.append(str(ref_year))
    if ref_km is not None: spec.append(f"{z(ref_km)} km")
    if ref_wh: spec.append(f"{ref_wh} Wh")
    spec = f" ({', '.join(spec)})" if spec else ""
    lines = [f"💰 <b>Wycena sprzedaży: {query}{spec}</b>",
             f"<i>pewność: {r['confidence']} · {r['n']} porównywalnych "
             f"({r['method']})</i>", ""]
    rozrzut = ""
    if r.get("widelki"):
        rozrzut = f" <i>(podobne stoją za {z(r['widelki'][0])}–{z(r['widelki'][1])})</i>"
    lines.append(f"📊 ZMIERZONE — poziom rynku: ~{z(r['market'])} zł{rozrzut}")
    lines.append("")
    lines.append(f"🎯 <b>Wystaw za: {z(r['listing'])} zł</b>")
    if r["mode"] == "szybko":
        lines.append("⚡ Jak najtańsi z podobnych — zejdzie najszybciej.")
    elif r["mode"] == "max":
        lines.append("⛰ Jak najdrożsi z podobnych — poczekasz dłużej.")
    else:
        lines.append("⚖️ Na poziomie typowej oferty tego roweru.")
    lines.append("")
    lines.append(f"💸 <b>Dostaniesz ~{z(r['dostaniesz'])} zł</b> "
                 f"<i>(ZAŁOŻONE: kupiec utarguje {r['nego_pct']}% przy oględzinach — "
                 f"to jedyna niezmierzona liczba w tej wycenie)</i>")
    if r.get("days"):
        lines.append(f"⏱ Podobne znikają z OLX po ~{r['days']} dniach.")
    return "\n".join(lines)


def price_reco_for(query, ref_year=None, ref_km=None, ref_wh=None, mode="balans",
                   max_detail_fetch=8):
    """Wersja z siecią: ściąga oferty OLX, dobiera brakujące szczegóły dla kilku
    najbliższych ofert (koszt OK — wywoływane ręcznie), liczy rekomendację."""
    offers = olx_relevant_offers(query, fetch_olx_offers(query, pages=2))
    if not offers:
        return None
    details = dict(load_olx_details())          # kopia — nie brudzimy cache na dysku
    fetched = 0
    for url in list(offers):
        if fetched >= max_detail_fetch:
            break
        if url in details:
            continue
        d = fetch_olx_detail(url)
        if d:
            details[url] = d
        fetched += 1
    forecast = olx_sell_forecast(query)
    return build_price_reco(offers, details, forecast, ref_year, ref_km, ref_wh, mode)


# === ODCZYT SEGMENTÓW: gdzie rynek realnie kupuje, a gdzie martwa strefa =======
# Hipoteza właściciela: rynek PL jest barbell — góra bierze nówki na raty, dół
# bierze złom za 1500, a używka premium za ~6k leży w martwym środku. To NIE
# hipoteza do wierzenia — agregujemy CAŁY zebrany cykl życia ofert OLX (sprzedane
# vs wygasłe) w półki cenowe i pokazujemy sprzedawalność liczbowo. Wtedy w martwej
# strefie po prostu NIE kupujemy.
SEGMENT_BANDS = [(0, 3000, "do 3k zł"), (3000, 5000, "3–5k zł"),
                 (5000, 8000, "5–8k zł"), (8000, 12000, "8–12k zł"),
                 (12000, 16000, "12–16k zł"), (16000, 10**9, "16k+ zł")]
SEGMENT_MIN_SAMPLES = 4  # mniej ofert w paśmie = statystyka niewiarygodna


SEGMENT_HORYZONT = 30      # w tylu dniach pytamy "sprzedało się czy nie?"
RELISTING_MIN_DNI = 2      # zniknięcie szybsze niż to = podejrzenie wznowienia


def segment_liquidity(watch=None, horyzont=SEGMENT_HORYZONT, dzis=None):
    """Ile % ofert schodzi w `horyzont` dni — wg półki cenowej.

    POPRAWKA ISTOTNA: wcześniej liczyliśmy tylko oferty, które zniknęły, przez
    co wychodziło 100% sprzedaży wszędzie. Oferty, które WCIĄŻ WISZĄ, to nie
    brak danych — to informacja, że się nie sprzedały. Teraz wchodzą do
    mianownika, gdy wiszą już dłużej niż horyzont. Te młodsze pomijamy, bo
    o nich naprawdę jeszcze nic nie wiadomo (nie zgadujemy w żadną stronę).

    NIE WYSYŁAĆ TEGO DO WŁAŚCICIELA. Arytmetyka tej funkcji jest poprawna, ale
    jej WEJŚCIE nie jest: `sold_fast[].days` to wiek liczony od naszego
    pierwszego widzenia oferty, a oferta żyje medianę 30 dni, zanim bot ją
    zobaczy (zmierzone 20.09.2026 na 735 ofertach; 49% ma wtedy ponad 30 dni).
    Przez to wszystko wygląda na sprzedane szybko: 3442 zapisy "sprzedane"
    obok 56 "wygasłych", przy 42% ofert wiszących dłużej niż 60 dni.
    Uczciwy pomiar liczy `plynnosc.py` z dziennika dozorcy - od daty
    wystawienia, z cenzurowaniem ofert wciąż wiszących. Ta funkcja zostaje
    tylko do porównań na `olx_watch.json`."""
    watch = watch if watch is not None else load_olx_watch()
    dzis = dzis or date.today()
    bands = {lbl: {"zeszlo": [], "nie_zeszlo": 0, "za_wczesnie": 0, "podejrzane": 0}
             for _, _, lbl in SEGMENT_BANDS}

    def band_for(price):
        for lo, hi, lbl in SEGMENT_BANDS:
            if lo <= price < hi:
                return lbl
        return None

    def wiek(o):
        try:
            return (dzis - date.fromisoformat(o["first"])).days
        except Exception:
            return None

    for w in watch.values():
        if not isinstance(w, dict):
            continue
        for s in w.get("sold_fast", []):            # potwierdzone zniknięcia
            if not (isinstance(s, dict) and s.get("price")):
                continue
            lbl = band_for(s["price"])
            if not lbl:
                continue
            d = s.get("days")
            if isinstance(d, int) and d < RELISTING_MIN_DNI:
                bands[lbl]["podejrzane"] += 1       # pewnie wznowienie, nie sprzedaż
            elif isinstance(d, int) and d <= horyzont:
                bands[lbl]["zeszlo"].append(s)
            else:
                bands[lbl]["nie_zeszlo"] += 1       # zeszło, ale po terminie
        for s in w.get("expired", []):              # wisiała bardzo długo
            if isinstance(s, dict) and s.get("price") and band_for(s["price"]):
                bands[band_for(s["price"])]["nie_zeszlo"] += 1
        for o in (w.get("offers") or {}).values():  # WCIĄŻ WISZĄCE — sedno poprawki
            if not (isinstance(o, dict) and o.get("price")):
                continue
            lbl = band_for(o["price"])
            if not lbl:
                continue
            v = wiek(o)
            if v is None or v < horyzont:
                bands[lbl]["za_wczesnie"] += 1      # za wcześnie na ocenę
            else:
                bands[lbl]["nie_zeszlo"] += 1       # wisi dłużej niż horyzont

    out = []
    for _, _, lbl in SEGMENT_BANDS:
        b = bands[lbl]
        n_ok, n_nie = len(b["zeszlo"]), b["nie_zeszlo"]
        total = n_ok + n_nie
        days = [s["days"] for s in b["zeszlo"] if isinstance(s.get("days"), int)]
        prices = [s["price"] for s in b["zeszlo"] if s.get("price")]
        out.append({"band": lbl, "n": total, "sold": n_ok, "expired": n_nie,
                    "za_wczesnie": b["za_wczesnie"], "podejrzane": b["podejrzane"],
                    "sell_through": round(n_ok / total * 100) if total else None,
                    "days": int(statistics.median(days)) if days else None,
                    "clearing": int(statistics.median(prices)) if prices else None})
    return out


def format_segments(rows) -> str:
    """Składa wiadomość Telegram (HTML) z tabelą sprzedawalności wg półki."""
    have = [r for r in rows if r["n"] >= SEGMENT_MIN_SAMPLES]
    if not have:
        return ("📊 <b>Sprzedawalność wg półki cenowej</b>\n"
                "Za mało zebranych danych o cyklu życia ofert — bot dopiero je "
                "zbiera (trzeba kilku–kilkunastu dni obserwacji OLX).")
    z = lambda v: f"{int(v):,}".replace(",", " ")
    lines = [f"📊 <b>Ile schodzi w {SEGMENT_HORYZONT} dni — wg półki cenowej</b>",
             "<i>oferty wciąż wiszące liczone jako niesprzedane</i>", ""]
    for r in rows:
        if r["n"] < SEGMENT_MIN_SAMPLES:
            continue
        st = r["sell_through"]
        icon = "🟢" if st >= 60 else "🟡" if st >= 35 else "🔴"
        d = f"~{r['days']} dni" if r["days"] else "—"
        lines.append(f"{icon} <b>{r['band']}</b>: schodzi {st}% · {d} · próbka {r['n']}")
    best = max(have, key=lambda r: r["sell_through"])
    worst = min(have, key=lambda r: r["sell_through"])
    lines += ["", f"✅ Najlepiej schodzi: <b>{best['band']}</b> ({best['sell_through']}%)"]
    if worst["band"] != best["band"]:
        lines.append(f"⛔ Najwolniej: <b>{worst['band']}</b> ({worst['sell_through']}%) "
                     f"— tu kapitał stoi najdłużej")
    # uczciwie: co jeszcze zaburza obraz
    podejrzane = sum(r.get("podejrzane", 0) for r in rows)
    wczesnie = sum(r.get("za_wczesnie", 0) for r in rows)
    if podejrzane or wczesnie:
        uwagi = []
        if podejrzane:
            uwagi.append(f"{podejrzane} zniknięć w <{RELISTING_MIN_DNI} dni pominięto "
                         f"(pewnie wznowienia, nie sprzedaże)")
        if wczesnie:
            uwagi.append(f"{wczesnie} ofert wisi za krótko, by je ocenić")
        lines.append(f"\n<i>ⓘ {'; '.join(uwagi)}</i>")
    return "\n".join(lines)


def annual_roi(profit_pln, buy_price_eur, liquidity_days):
    """Roczny zwrot z zaangażowanego kapitału. None gdy brak danych.
    ROI = zysk / kapitał × (365 / dni_do_sprzedaży)."""
    if profit_pln is None or not buy_price_eur or not liquidity_days:
        return None
    invested = buy_price_eur * get_eur_pln() + TRANSPORT_PLN
    if invested <= 0:
        return None
    return profit_pln / invested * (365 / max(liquidity_days, 1))


def get_demand_price(query: str):
    """Cena POPYTU: mediana ofert OLX które znikły szybko (= realne transakcje),
    a nie cen życzeniowych z wiszących ogłoszeń. None gdy brak świeżych danych."""
    w = load_olx_watch().get(query)
    if not w or not w.get("demand_median"):
        return None
    try:
        updated = date.fromisoformat(w["updated"])
        if (date.today() - updated).days <= DEMAND_MAX_AGE_DAYS:
            return w["demand_median"]
    except Exception:
        pass
    return None


HISTORY_MIN_SAMPLES = 5


HISTORY_YEAR_MIN_SAMPLES = 3  # dla porównania w obrębie tego samego rocznika


# --- Dziennik historii (#1 + #4): append-only, jedna linijka na ofertę. ---
# NIGDY nie kasowany ani nadpisywany — rośnie w nieskończoność. To trwały
# zapis rynku (nie do skopiowania przez konkurencję). Bogaty zestaw pól,
# żeby w przyszłości dało się liczyć deprecjację, spread DE↔PL i sezonowość.
HISTORY_FILE = Path("history.jsonl")
_history_cache = None


def append_history(model, price_num, ad_id=None, mileage_num=None, year=None,
                   olx_median=None, profit=None, buy_price=None, ev=None, zr=None):
    """Dopisuje 1 wpis do dziennika finalistów (append-only, nigdy nie nadpisuje).

    `zr` to giełda pochodzenia. BRAK tego pola znaczy Kleinanzeigen — tak
    wygląda cała historia do 25.08.2026 i tak ma zostać, żeby stare wiersze
    nie wymagały przepisywania. Austria dostaje "wh". Bez tej etykiety zdanie
    "ceny modelu −8% / 3 tyg (rynek DE tanieje)" zaczęłoby po cichu opisywać
    dwa różne rynki naraz, a to jest kłamstwo w liczbie, nie niedokładność."""
    if not model or not price_num:
        return
    try:
        rec = {"ts": date.today().isoformat(), "m": model, "p": int(price_num),
               "kurs": round(get_eur_pln(), 3)}                   # kurs EUR/PLN w tym momencie
        if zr:                  rec["zr"] = zr                    # giełda (brak = Kleinanzeigen)
        if ev:                  rec["ev"] = ev                    # typ zdarzenia (np. "drop")
        if ad_id:               rec["id"] = ad_id                 # referencja do ogłoszenia
        if mileage_num is not None: rec["km"] = mileage_num       # przebieg
        if year:                rec["y"] = year                   # rocznik
        if olx_median:          rec["olx"] = int(olx_median)      # cena PL w tym momencie (spread!)
        if buy_price:           rec["buy"] = int(buy_price)       # realna cena po negocjacji
        if profit is not None:  rec["profit"] = int(profit)       # szacowany zysk
        with HISTORY_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        log.error(f"append_history error: {e}")


# --- Log CAŁEGO rynku (#1 z audytu): każde widziane ogłoszenie, przed filtrami. ---
# ~500 wpisów/dzień zamiast ~10 — prawdziwe rozkłady cen, deprecjacja, geografia.
# Dane wyłącznie z listy (bez pobierania podstron) — koszt ~zero.
MARKET_FILE = Path("market.jsonl")   # LEGACY: tylko do ODCZYTU, nic tu nie dopisujemy
# DZIENNIK RYNKU W KAWAŁKACH MIESIĘCZNYCH (18.09.2026). Powód jest w gicie,
# nie w danych: git NIE ZAPISUJE RÓŻNIC, tylko cały plik od nowa. Dopisanie
# jednego wiersza do pliku na 27,5 MB tworzyło nowy obiekt na 27,5 MB,
# a bot commitował siedem razy na bieg, co pięć minut. Stąd brały się paczki,
# na których push się zawieszał.
#
# Przy podziale na miesiące zmienia się WYŁĄCZNIE bieżący kawałek. Stare leżą
# nietknięte, więc git ich nie przepakowuje. Nic się nie kasuje i nic nie
# ginie - to jest przeniesienie, nie przycinanie.
#
# Czemu akurat ten plik pierwszy: dziennik rynku jest materiałem do ANALIZ.
# Gdyby czytnik pominął kawałek, najwyżej jakaś statystyka stanie na mniejszej
# próbce. `seen.json` jest stanem dedupu - tam pomyłka znaczy albo lawinę
# powtórek, albo ciszę, więc idzie osobno i ostrożniej.


def market_biezacy() -> Path:
    """Kawałek na bieżący miesiąc - JEDYNY plik, do którego dopisujemy.

    Nazwa liczona WZGLĘDEM `MARKET_FILE`, a nie wpisana na sztywno, bo testy
    i narzędzia podstawiają tam własną ścieżkę. Wpisana na sztywno robiłaby
    z każdej piaskownicy zapis do prawdziwego katalogu bota - ta sama pułapka
    co „ŚCIEŻKA NIGDY W DOMYŚLNYM ARGUMENCIE" z 09.09."""
    baza = MARKET_FILE.with_suffix("")
    return baza.with_name(f"{baza.name}-{date.today().strftime('%Y-%m')}.jsonl")


def market_kawalki() -> list:
    """Wszystkie kawałki dziennika, od najstarszego. Legacy idzie PIERWSZY,
    bo jest najstarszy - a kolejność ma znaczenie: `zbuduj_rozrzut`
    i `odzyskaj_silnik` liczą „ostatnie spotkanie wygrywa", więc przestawienie
    kawałków cofnęłoby ceny do stanu sprzed miesięcy."""
    baza = MARKET_FILE.with_suffix("")
    katalog = baza.parent if str(baza.parent) else Path(".")
    stare = [MARKET_FILE] if MARKET_FILE.exists() else []
    return stare + sorted(katalog.glob(f"{baza.name}-????-??.jsonl"))


def market_wiersze():
    """Iterator po CAŁYM dzienniku rynku, niezależnie od podziału na kawałki.
    Każdy czytnik ma iść tędy - inaczej po podziale zobaczy ułamek danych
    i nikt tego nie zauważy, bo wynik nadal będzie wyglądał wiarygodnie
    (reguła 7)."""
    for kawalek in market_kawalki():
        try:
            with kawalek.open(encoding="utf-8") as f:
                for linia in f:
                    yield linia
        except OSError:
            continue


def log_market(listing, search_name):
    """Zapisuje 1 ogłoszenie do surowego logu rynku (append-only)."""
    try:
        title = listing.get("title", "")
        rec = {"ts": date.today().isoformat(), "id": listing["id"], "t": title,
               "p": listing.get("price_num"), "s": search_name,
               "kurs": round(get_eur_pln(), 3)}
        model = olx_query_for(title, None)
        if model:
            rec["m"] = model
        y = extract_year(title)
        if y:
            rec["y"] = y
        km = parse_mileage(_extract_mileage(title, ""))   # przebieg tylko z tytułu
        if km is not None:
            rec["km"] = km
        if listing.get("loc"):
            rec["loc"] = listing["loc"]
        # czas wystawienia + ile minut minęło, zanim bot je zobaczył — bez tego
        # w logu nie da się odróżnić wolnego bota od ogłoszenia, które weszło
        # do wyników z opóźnieniem, i każda skarga na spóźnienie jest zgadywanką
        if listing.get("posted"):
            rec["wyst"] = listing["posted"].isoformat()
            if listing.get("age_min") is not None:
                rec["op"] = int(listing["age_min"])
        with market_biezacy().open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        log.error(f"log_market error: {e}")


def _load_history():
    global _history_cache
    if _history_cache is None:
        _history_cache = []
        if HISTORY_FILE.exists():
            try:
                for line in HISTORY_FILE.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line:
                        _history_cache.append(json.loads(line))
            except Exception:
                _history_cache = []
    return _history_cache


def price_trend(model, days=21, zrodlo=None):
    """Trend ceny modelu z własnego dziennika: % zmiany mediany
    (świeższa połowa okna vs starsza). None gdy za mało danych.

    Domyślnie liczony WYŁĄCZNIE z rynku niemieckiego, bo dokładnie tak jest
    podpisany w wiadomości ("rynek DE tanieje"). Wiersze z Austrii lecą do
    dziennika od 25.08.2026 i czekają na własną, osobną próbkę — sklejenie
    dwóch rynków w jedną medianę zrobiłoby z tej liczby średnią z niczego."""
    if not model:
        return None
    try:
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        mid = (date.today() - timedelta(days=days // 2)).isoformat()
        older, newer = [], []
        for r in _load_history():
            if r.get("m") != model or r.get("ts", "") < cutoff:
                continue
            if r.get("zr") != zrodlo:      # brak pola = Kleinanzeigen
                continue
            (newer if r["ts"] >= mid else older).append(r["p"])
        if len(older) < 4 or len(newer) < 4:
            return None
        o, n = statistics.median(older), statistics.median(newer)
        if o <= 0:
            return None
        return int((n - o) / o * 100)
    except Exception:
        return None


def build_price_history(seen: dict) -> dict:
    """Cennik referencyjny per model z własnej historii skanów (seen.json).
    Trzyma (cena, rocznik) — porównanie może być zawężone do rocznika.

    ŚWIADOMIE wspólny dla obu giełd, w odróżnieniu od `price_trend`. Ta liczba
    odpowiada na pytanie "czy widziałem kiedyś ten model taniej", a bot kupuje
    w Niemczech i w Austrii — więc najtańszy egzemplarz jest najtańszy
    niezależnie od kraju. Komunikat też niczego o kraju nie twierdzi. Trend
    twierdzi ("rynek DE") i dlatego tam rozdzielamy."""
    hist = {}
    for ad_id, v in seen.items():
        if not isinstance(v, dict):
            continue
        title, price = v.get("title"), v.get("price_num")
        if not title or not price:
            continue
        key = olx_query_for(title, None)
        if key:
            yr = v.get("year") or extract_year(title)
            hist.setdefault(key, []).append((price, yr))
    return hist


def price_history_signal(title: str, price_num, year, hist: dict):
    """Porównanie ceny z historią modelu — najpierw w obrębie rocznika,
    fallback do całego modelu. Zwraca (linia_wiadomości|None, bonus_score)."""
    if not price_num:
        return None, 0
    key = olx_query_for(title, None)
    if not key:
        return None, 0
    entries = hist.get(key, [])
    same_year = [p for p, y in entries if year and y == year]
    if len(same_year) >= HISTORY_YEAR_MIN_SAMPLES:
        prices, label = same_year, f'"{key}" {year}'
    elif len(entries) >= HISTORY_MIN_SAMPLES:
        prices, label = [p for p, _ in entries], f'"{key}"'
    else:
        return None, 0
    mn, med = min(prices), int(statistics.median(prices))
    if price_num <= mn:
        return (f"\n🏆 NAJTAŃSZY {label} z {len(prices)} ofert (mediana {med} €)!", 15)
    pct = int((med - price_num) / med * 100)
    if pct >= 15:
        return (f"\n📊 {pct}% taniej niż mediana {label} ({med} € z {len(prices)} ofert)", 8)
    return None, 0


# === ROZRZUT MODELU: czy ta oferta jest tania NA TLE SWOICH ==================
# Stary sygnał cenowy w `score_listing` porównywał ofertę do mediany CAŁEJ
# półki — a na jednej półce leży Cube za 900 € i Levo za 4 000 €. Punkty za
# cenę mierzyły więc, JAKI to model, a nie czy oferta jest dobra: tani model
# zawsze wyglądał na okazję, drogi nigdy. To ten sam kształt błędu co
# "POZIOM vs PROPORCJA" (reguła 4), tylko po stronie zakupu.
#
# ZMIERZONE 29.08.2026 na własnych danych (market.jsonl + rynek_pl.jsonl,
# odsiane sklepy z PL, targ policzony po obu stronach): marża zależy dużo
# bardziej od tego, GDZIE w rozrzucie własnego modelu leży oferta, niż od
# tego, JAKI to model. Różnica między najlepszą a najgorszą rodziną wyszła
# ~1 200 zł, a różnica między typową a dobrą ofertą TEGO SAMEGO modelu
# 500-3 000 zł. Te same Cube Stereo Hybrid 160 HPC SLX 750 stały tego dnia
# od 2 199 do 3 666 € — 1 467 € (≈6 300 zł) rozrzutu na jednym rowerze.
ROZRZUT_OKNO_DNI = 30
# Dolny kwartyl liczony na 12 rowerach to trzeci od dołu — grubo, ale uczciwie.
# Poniżej tego progu bot ma mówić "nie wiem" i zostawić stary sygnał w spokoju.
# ZAŁOŻONE (próg wybrany ręcznie), ale koszt zmierzony: przy n>=12 kubełki
# łapią 2,0% ruchu, przy n>=8 — 2,5%, przy n>=15 — 1,7%. Płaska zależność,
# więc wybrano wariant ostrożniejszy.
ROZRZUT_MIN_ROWEROW = 12
_rozrzut_cache = None

# Producent pisze pojemność w NAZWIE modelu, bez jednostki: "Stereo Hybrid 160
# HPC SLX 750". `battery_wh` wymaga literalnego "Wh" i dlatego na samych
# tytułach z rynku czytał baterię w 17% ogłoszeń — za mało, żeby powstał choćby
# jeden kubełek dla Cube'a (zmierzone 29.08.2026 na 16 998 ogłoszeniach).
# Goły numer bierzemy WYŁĄCZNIE z listy realnych pojemności i tylko wtedy, gdy
# nie przylega do niego cyfra ani litera i nie stoi za nim jednostka — inaczej
# "nur 800 km", "XTR M900" i "Cube Editor Hybrid Pro 400X" wchodzą jako bateria.
POJEMNOSCI_BATERII = (900, 800, 750, 725, 700, 630, 625, 600, 545, 500, 400)
_BATERIA_GOLA = re.compile(
    r'(?<![\d,.a-z])(' + '|'.join(str(p) for p in POJEMNOSCI_BATERII) +
    r')(?![\d,.a-z])(?!\s*(?:km|kg|watt|w\b|zoll|mm|€|eur|c\b))', re.I)


def bateria_z_nazwy(title, desc=None):
    """Pojemność baterii z tytułu/opisu — także wtedy, gdy siedzi w nazwie
    modelu bez jednostki. Zwraca None, gdy nie da się jej odczytać."""
    wh = battery_wh(title, desc)
    if wh:
        return wh
    m = _BATERIA_GOLA.search((title or "").lower())
    return int(m.group(1)) if m else None


def zbuduj_rozrzut(rows=None, dzis=None):
    """Rozrzut cen w obrębie (model, klasa baterii) z dziennika rynku.

    Zwraca {(model, "S"|"M"|"L"): posortowana lista cen}.

    Dwie decyzje, obie zmierzone i obie ważne:

    1. Czytamy WYŁĄCZNIE półki (`NAZWY_POLEK`), nigdy zapytań kluczowych.
       Zapytania mają w adresie `s-preis:800:3000`, więc nie widzą droższego
       końca rynku: 0% ich ogłoszeń jest powyżej 3 000 €, a na kanale e-bike
       jest tam 16% (zmierzone 29.08.2026). Kwartyl liczony z uciętej próbki
       byłby zaniżony, i to o różną wartość dla różnych modeli. Warunek pisany
       przez `NAZWY_POLEK`, a nie listę nazw, żeby nowa półka dołączała sama,
       a nowe zapytanie kluczowe samo NIE dołączało.
    2. Liczymy ROWERY, nie ogłoszenia (reguła 5): najpierw po `id`, potem
       scalamy identyczne (tytuł, cena). Zmierzone: to drugie zdejmuje 3,3%
       obserwacji. Wznowień z OBNIŻONĄ ceną to NIE łapie — odcisk z ceną pęka
       dokładnie na tych, których miałby łapać (patrz `dozorca.odcisk`),
       a market.jsonl nie zapisuje sprzedawcy, więc drugiej drogi tu nie ma.
    """
    if rows is None:
        rows = []
        try:
            for linia in market_wiersze():
                try:
                    rows.append(json.loads(linia))
                except Exception:
                    continue
        except FileNotFoundError:
            return {}
    granica = ((dzis or date.today()) - timedelta(days=ROZRZUT_OKNO_DNI)).isoformat()
    polki = NAZWY_POLEK
    po_id = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        if r.get("ts", "") < granica or r.get("s") not in polki:
            continue
        if not isinstance(r.get("p"), int):
            continue
        po_id[r.get("id") or id(r)] = r
    kubelki, widziane = {}, set()
    for r in po_id.values():
        tytul = r.get("t") or ""
        odcisk = (_tytul_znormalizowany(tytul), r["p"])
        if odcisk in widziane:
            continue
        widziane.add(odcisk)
        model = olx_query_for(tytul, None)
        klasa = wh_class(bateria_z_nazwy(tytul))
        if model and klasa:
            kubelki.setdefault((model, klasa), []).append(r["p"])
    for k in kubelki:
        kubelki[k].sort()
    return kubelki


def load_rozrzut(force=False):
    """Rozrzut liczony RAZ na bieg (0,07 s na 26 tys. wierszy, zmierzone).

    Czujka na ciszę (reguła 7): dziennik rynku pełny, a kubełków zero znaczy,
    że zmieniły się nazwy półek albo padło czytanie tytułów. Cicho zniknąłby
    wtedy cały sygnał cenowy i nikt by tego nie zauważył."""
    global _rozrzut_cache
    if _rozrzut_cache is None or force:
        _rozrzut_cache = zbuduj_rozrzut()
        duze = sum(1 for v in _rozrzut_cache.values() if len(v) >= ROZRZUT_MIN_ROWEROW)
        # Czujka patrzy na CAŁY dziennik, nie na sam plik legacy - inaczej po
        # przeniesieniu danych do kawałków zamilkłaby po cichu (reguła 7).
        _jest_dziennik = any(k.exists() and k.stat().st_size > 0
                             for k in market_kawalki())
        if _jest_dziennik and duze == 0:
            zglos_problem("rozrzut", f"kubełków {len(_rozrzut_cache)}, żaden nie ma "
                                     f"{ROZRZUT_MIN_ROWEROW} rowerów")
        log.info(f"rozrzut modeli: {len(_rozrzut_cache)} kubełków, "
                 f"{duze} z pełną próbką")
    return _rozrzut_cache


def kubelek_rozrzutu(title, desc, rozrzut=None):
    """(klucz, ceny) dla oferty albo (None, None), gdy nie ma z czym porównać.

    Bateria oferty czytana jest z tytułu I OPISU, bo opis mamy pobrany —
    pula odniesienia stoi na samych tytułach i to jej ograniczenie, nie jej
    definicja."""
    if rozrzut is None:
        rozrzut = load_rozrzut()
    model = olx_query_for(title or "", None)
    klasa = wh_class(bateria_z_nazwy(title, desc))
    if not model or not klasa:
        return None, None
    ceny = rozrzut.get((model, klasa))
    if not ceny or len(ceny) < ROZRZUT_MIN_ROWEROW:
        return None, None
    return (model, klasa), ceny


def kwartyle(ceny):
    """(Q1, mediana) z posortowanej listy. Q1 liczony przez indeks, bez
    interpolacji — na 12-20 obserwacjach i tak udaje precyzję."""
    s = sorted(ceny)
    return s[len(s) // 4], int(statistics.median(s))


def sygnal_rozrzutu(title, desc, price_num, rozrzut=None):
    """Czy ta oferta jest tania NA TLE SWOJEGO modelu i swojej baterii.

    Zwraca (linia_wiadomości|None, bonus_score). Każda liczba w linii jest
    ZMIERZONA (reguła 6) i niesie, na ilu rowerach stoi."""
    if not price_num:
        return None, 0
    klucz, ceny = kubelek_rozrzutu(title, desc, rozrzut)
    if not klucz:
        return None, 0
    q1, med = kwartyle(ceny)
    if price_num > q1:
        return None, 0
    etykieta = f'{klucz[0]} / bateria {klucz[1]}'
    ile = int((med - price_num) / med * 100) if med else 0
    return (f"\n🎯 DÓŁ ROZRZUTU: {price_num} € przy dolnym kwartylu {q1} € "
            f"i medianie {med} € — {ile}% pod medianą "
            f"({len(ceny)} rowerów, {etykieta}, ostatnie {ROZRZUT_OKNO_DNI} dni)", 20)


# Silnik marży negocjacyjnej — kalibracja z realnego rynku:
# rower 2.500 € "VB" schodzi ~10% (200-300 €) już na etapie wiadomości.
NEGO_BASE_VB = 0.10       # baza dla ceny "VB" (do negocjacji)
NEGO_BASE_OPEN = 0.05     # brak znacznika — trochę luzu i tak jest
NEGO_BASE_FIXED = 0.02    # "Festpreis" — mur, ale czasem drgnie
NEGO_MAX = 0.18           # sufit realnego zejścia zdalnie
# Targ na miejscu, przy oględzinach — ETAP DRUGI, po tym co ustalone zdalnie.
# Publicznych danych o nim NIE MA (sprawdzone 23.08.2026): jedyny duży pomiar
# targowania się to eBay Best Offer (88 mln ofert, sprzedaż po 73% ceny przy
# negocjacji), ale to targ przez formularz, przed spotkaniem, i przy średnim
# przedmiocie za 95 $. Najbliższy odpowiednik to niemieckie poradniki o
# prywatnym zakupie auta: realnie ~10%, powyżej 20% "nikt nie traktuje
# poważnie". Stąd 10% — jako ZAŁOŻENIE, nie pomiar. Do podmiany na własne
# dane, gdy uzbiera się kilkanaście zakupów.
NEGO_NA_MIEJSCU = 0.10
NEGO_MAX_LACZNIE = 0.22   # sufit dla obu etapów razem


def negotiation_headroom(price_num, price_str, desc):
    """Szacowany luz negocjacyjny (%) — ile realnie zejdziesz z ceny wywoławczej.
    Zwraca (procent 0-0.18, lista powodów)."""
    if not price_num:
        return 0.0, []
    text = f"{price_str} {desc or ''}".lower()
    # "Festpreis" = mur: żadne bonusy się nie doliczają
    if re.search(r'festpreis|fixpreis|preis ist fix|nicht verhandel|keine verhandlung|nachlass ausgeschlossen', text):
        return NEGO_BASE_FIXED, ["Festpreis (mur)"]

    reasons = []
    if re.search(r'\bvb\b|verhandlungsbasis|verhandelbar|preis verhandel', text):
        pct = NEGO_BASE_VB
        reasons.append("VB")
    else:
        pct = NEGO_BASE_OPEN
    # sygnały motywacji sprzedawcy → większy luz
    if re.search(r'muss weg|schnell verkauf|keine zeit|zeitmangel|umzug|umständehalber|'
                 r'aus platzgr|brauche.{0,10}geld|neuanschaffung|kein bedarf|nicht mehr genutzt', text):
        pct += 0.04
        reasons.append("presja sprzedawcy")
    # okrągła cena = miejsce zostawione na negocjację
    if price_num >= 1000 and price_num % 100 == 0:
        pct += 0.02
        reasons.append("okrągła cena")
    return min(pct, NEGO_MAX), reasons


def realistic_buy_price(price_num, price_str, desc):
    """Cena, którą warto ZAPROPONOWAĆ zdalnie (zaokrąglona do 10 €).

    To jeszcze nie jest cena, którą zapłacisz — patrz `cena_po_ogledzinach`."""
    if not price_num:
        return None, 0.0, []
    pct, reasons = negotiation_headroom(price_num, price_str, desc)
    return int(round(price_num * (1 - pct) / 10) * 10), pct, reasons


def cena_po_ogledzinach(price_num, pct_zdalny, reasons=None):
    """Cena, którą realnie zapłacisz: targ zdalny PLUS targ na miejscu.

    Dwa etapy składają się mnożnikowo, a nie przez dodawanie — kto zbił 10%
    wiadomością, nie zbije drugich 10% w garażu od tej samej kwoty. Sprzedawca,
    który napisał "Festpreis", jest twardy również na żywo, więc dostaje ułamek
    tego luzu. Zwraca (cena, laczny_procent)."""
    if not price_num:
        return None, 0.0
    na_miejscu = (NEGO_NA_MIEJSCU * 0.4 if "Festpreis (mur)" in (reasons or [])
                  else NEGO_NA_MIEJSCU)
    laczny = 1 - (1 - pct_zdalny) * (1 - na_miejscu)
    laczny = min(laczny, NEGO_MAX_LACZNIE)
    return int(round(price_num * (1 - laczny) / 10) * 10), laczny


def mileage_factor(km) -> float:
    """Korekta wartości roweru względem przebiegu vs mediany rynkowej (~1500km)."""
    if km is None:
        return 1.0   # brak danych = zakładamy średni stan
    if km < 300:     return 1.15  # prawie nowy +15%
    if km < 800:     return 1.08  # bardzo mało używany +8%
    if km < 1500:    return 1.03  # mało używany +3%
    if km < 2500:    return 0.95  # średni przebieg -5%
    return          0.85          # duży przebieg -15%


def year_factor(model_year) -> float:
    """Korekta wartości roweru względem rocznika vs typowego roweru na rynku
    wtórnym (~3 lata). Mediana OLX miesza roczniki — bez tego rower 2024 i 2018
    o tej samej nazwie dostawałyby tę samą wycenę odsprzedaży."""
    if not model_year:
        return 1.0
    ref = CURRENT_YEAR - 3            # typowy wiek roweru w medianie OLX
    factor = 1.0 + 0.08 * (model_year - ref)   # ~8% na rok
    return max(0.70, min(1.30, factor))


def cena_sprzedazy_realna(price_pl_pln):
    """Ile realnie dostaniesz, a nie ile wystawisz.

    SYMETRIA: skoro po stronie zakupu zakładamy, że utargujesz swoje przy
    oględzinach, to po stronie sprzedaży trzeba założyć to samo — Twój kupujący
    też przyjedzie i też będzie zbijał. Bez tego model jest optymistyczny
    dwustronnie i dokładnie tak powstała pomyłka 4x na Cube (szacunek ~5 500 zł
    wobec 1 300 zł realnego zysku).

    Dotyczy to również "ceny domykającej": to mediana OSTATNICH CEN Z OGŁOSZEŃ,
    które szybko znikły (summary.py) — czyli też cena wywoławcza. Targ kupującego
    odbył się już po niej i nigdzie nie jest zapisany."""
    if not price_pl_pln:
        return None
    return int(price_pl_pln * (1 - NEGO_NA_MIEJSCU))


def calc_profit(price_de_eur: int, price_pl_pln: int, km=None, year=None,
                juz_skorygowana: bool = False) -> int:
    """Zysk z odsprzedaży w PL. `juz_skorygowana=True` gdy cena PL pochodzi
    z cennika cech — jest wtedy PRZELICZONA na ten konkretny rower i ponowne
    mnożenie przez ręczne mileage_factor/year_factor liczyłoby korektę
    drugi raz (raz z rynku, raz z sufitu).

    `price_pl_pln` to cena WYWOŁAWCZA — targ kupującego zdejmuje z niej
    cena_sprzedazy_realna()."""
    kurs = get_eur_pln()
    koszt_de = price_de_eur * kurs
    adjusted_pl = (price_pl_pln if juz_skorygowana
                   else price_pl_pln * mileage_factor(km) * year_factor(year))
    return int(cena_sprzedazy_realna(adjusted_pl) - koszt_de - TRANSPORT_PLN)


def max_profitable_mileage(price_de_eur: int, price_pl_pln: int, min_profit: int = 500, year=None) -> str:
    """Zwraca max przebieg przy którym deal jest opłacalny (zysk >= min_profit PLN)."""
    kurs = get_eur_pln()
    # ta sama symetria co w calc_profit: liczy się to, co DOSTANIESZ
    price_pl_pln = cena_sprzedazy_realna(price_pl_pln * year_factor(year))
    koszt_de = price_de_eur * kurs + TRANSPORT_PLN + min_profit
    needed_factor = koszt_de / price_pl_pln

    if needed_factor <= 0.85:
        return "do 3.000 km"
    if needed_factor <= 0.95:
        return "do 2.500 km"
    if needed_factor <= 1.03:
        return "do 1.500 km"
    if needed_factor <= 1.08:
        return "do 800 km"
    if needed_factor <= 1.15:
        return "do 300 km"
    return "nieopłacalne nawet nowy"


SEEN_MAX_AGE_DAYS = 90


def seen_kawalki(plik=None) -> list:
    """Kawałki stanu od NAJSTARSZEGO. Legacy `seen.json` zawsze pierwszy.

    `plik` pozwala zapytać o kawałki INNEGO stanu niż własny `SEEN_FILE` -
    korzystają z tego `rozmiary.py` i `oferta.py`, które mają własną stałą
    `SEEN` (testy ją podmieniają). Domyślne `None`, a nie sama ścieżka:
    ścieżka w domyślnym argumencie wiąże się w chwili definicji modułu,
    więc podmiana `SEEN_FILE` nie miałaby wtedy skutku. Ten błąd wyszedł
    w tym repo już dwa razy - patrz „ŚCIEŻKA NIGDY W DOMYŚLNYM ARGUMENCIE".

    KROK PIERWSZY podziału (19.09.2026): czytamy z kawałków, ale zapisujemy
    dalej wyłącznie do `seen.json`. Dopóki żaden kawałek nie istnieje, ta
    funkcja oddaje dokładnie jeden plik i `load_seen` zachowuje się co do
    joty tak jak przedtem - to jest cała gwarancja bezpieczeństwa tego kroku
    i pilnuje jej osobny test.

    Dlaczego w ogóle dwa kroki: `seen.json` to stan dedupu, a pomyłka tutaj
    znaczy albo lawinę powtórek na telefonie, albo ciszę. Najpierw na
    produkcji ma się wykazać ODCZYT, przy niezmienionym zapisie, i dopiero
    potem wolno ruszyć zapis.

    Nazwa liczona WZGLĘDEM `SEEN_FILE`, nie wpisana na sztywno - testy
    i narzędzia podstawiają tam własną ścieżkę (ta sama pułapka co przy
    kawałkach dziennika rynku i co „ŚCIEŻKA NIGDY W DOMYŚLNYM ARGUMENCIE")."""
    zrodlo = plik or SEEN_FILE          # rozwiązywane W WYWOŁANIU
    baza = zrodlo.with_suffix("")
    katalog = baza.parent if str(baza.parent) else Path(".")
    stare = [zrodlo] if zrodlo.exists() else []
    return stare + sorted(katalog.glob(f"{baza.name}-????-??.json"))


def load_seen() -> dict:
    """Złączony stan ze wszystkich kawałków. PÓŹNIEJSZY WYGRYWA.

    Kolejność ma znaczenie i jest odwrotna niż przy dzienniku rynku tylko
    z pozoru: tam „ostatnie spotkanie wygrywa" dotyczy ceny, tu tego samego
    ogłoszenia dotkniętego ponownie (przecena, dopisany rozmiar, `score`).
    Świeższy kawałek musi przykryć starszy wpis, inaczej cofnęlibyśmy cenę.

    BŁĘDU ODCZYTU NIE POŁYKAMY (reguła 7). Uszkodzony plik zamieniony po
    cichu na pusty stan znaczy, że bot uzna CAŁY rynek za nowy i wyśle
    kilkaset powiadomień naraz. Wyjątek wywraca bieg, krok świeci na
    czerwono i nic nie wychodzi - to jest tańszy koniec tej historii."""
    seen: dict = {}
    for kawalek in seen_kawalki():
        data = json.loads(kawalek.read_text())
        # migracja ze starego formatu (lista ID) do nowego (dict)
        if isinstance(data, list):
            data = {ad_id: {} for ad_id in data}
        seen.update(data)
    return seen


def prune_seen(seen: dict) -> dict:
    """Usuwa wpisy starsze niż SEEN_MAX_AGE_DAYS — ogłoszenia dawno wygasły,
    a plik commitowany co 5 min nie może rosnąć w nieskończoność."""
    cutoff = (date.today() - timedelta(days=SEEN_MAX_AGE_DAYS)).isoformat()
    today = date.today().isoformat()
    pruned = {}
    for ad_id, v in seen.items():
        if not isinstance(v, dict):
            continue
        # legacy wpisy bez daty dostają dzisiejszą (zaczyna im tykać zegar)
        if not v.get("date"):
            v = dict(v, date=today)
        if v["date"] >= cutoff:
            pruned[ad_id] = v
    removed = len(seen) - len(pruned)
    if removed:
        log.info(f"Usunięto {removed} wpisów starszych niż {SEEN_MAX_AGE_DAYS} dni")
    return pruned


def save_seen(seen: dict):
    SEEN_FILE.write_text(json.dumps(seen, ensure_ascii=False, indent=2))


DEDUP_DAYS = 14        # okno w którym re-listing tego samego roweru = duplikat
DEDUP_PRICE_PCT = 0.03  # cena może się nieznacznie zmienić przy ponownym wystawieniu
DEDUP_KM_TOL = 300      # rozjazd przebiegu POWYZEJ tego = inny rower.
# Ta sama liczba, odwrocone znaczenie (01.09.2026): kiedys ZGODNY przebieg
# w tych granicach potwierdzal tozsamosc i mylil sie w 67% przypadkow
# (60 z 90 zdlawien na 60 dniach). Dzis moze juz tylko ZAPRZECZYC.
# Od ilu znaków tytuł sam w sobie jest dowodem tożsamości. Zmierzone
# 25.08.2026 na 1293 powiadomieniach z bazy: identyczny tytuł przy identycznej
# cenie trafił się 31 razy (65 zbędnych wiadomości, jedno ogłoszenie poszło
# CZTERNAŚCIE razy). Tylko 3 z tych 31 grup mają tytuł krótszy niż 30 znaków
# — i akurat te są niepewne ("e bike fully focus" za 1650 € to mogą być dwa
# różne rowery). Próg 30 bierze więc 28 z 31 grup i zostawia niepewne w spokoju.
DEDUP_TYTUL_MIN = 30


def dedup_key(title):
    """Klucz do dedupu: rozpoznany model, a gdy nieznany — znormalizowany tytuł.
    Bez fallbacku dwa identyczne ogłoszenia modelu spoza listy szły podwójnie."""
    model = olx_query_for(title, None)
    if model:
        return model
    t = re.sub(r'[^a-z0-9]+', ' ', (title or "").lower()).strip()
    return t or None


def _tytul_znormalizowany(title):
    """Tytuł sprowadzony do samych liter i cyfr — do porównywania ogłoszeń."""
    return re.sub(r'[^a-z0-9]+', ' ', (title or "").lower()).strip()


def build_recent_index(seen: dict) -> list:
    """Lista (klucz, cena, przebieg, data, miejscowość, tytuł znormalizowany,
    tytuł surowy) z powiadomionych ofert z 14 dni — do wykrywania re-listingów.

    Tytuł SUROWY jest siódmy i jest potrzebny osobno: `sprzeczne_warianty`
    czyta z niego rozmiar ramy, a ten siedzi w "Gr. L" i "51 cm" —
    normalizacja robi z tego "gr l" i etykieta przestaje się dopasowywać."""
    cutoff = (date.today() - timedelta(days=DEDUP_DAYS)).isoformat()
    idx = []
    for v in seen.values():
        if not isinstance(v, dict) or v.get("score") is None:
            continue
        if v.get("date", "") < cutoff:
            continue
        key = dedup_key(v.get("title", ""))
        if key and v.get("price_num"):
            idx.append((key, v["price_num"], v.get("mileage_num"), v.get("date"),
                        v.get("loc"), _tytul_znormalizowany(v.get("title")),
                        v.get("title")))
    return idx


# Wersje tego samego modelu, które znaczą INNY rower. Lista nie jest wzięta
# z głowy: to tokeny realnie odróżniające warianty w naszych danych, policzone
# 01.09.2026 na 44 342 tytułach z market.jsonl — pro 2 146 razy, race 1 114,
# hpc 974, sl 693, comp 368, slx 361, allroad 345, one 329.
# Granice słowa po OBU stronach są warunkiem koniecznym, nie ozdobą: bez nich
# "SLX" dopasowałoby się jako "SL" i dwa różne warianty wyszłyby na zgodne —
# a to jest dokładnie ta para, która 26.08.2026 kosztowała rower.
WERSJE_WZ = re.compile(
    r'\b(?:s-works|action\s*team|allroad|prestige|elite|expert|comp|race|pro'
    r'|slt|slx|sl|hpc|hpa|tm|team|one|evo|ltd|limited|tuned|sport|tour|plus)\b',
    re.I)


def _warianty(tytul):
    """Zbiór nazw wersji wypisanych w tytule (pusty = tytuł nic nie mówi)."""
    return frozenset(m.group(0).lower().replace(" ", "")
                     for m in WERSJE_WZ.finditer(tytul or ""))


def _rama_czesci(tytul):
    """(litera, centymetry) z rozmiaru ramy. None na pozycji = nie podano."""
    r = rozmiar_ramy(tytul, None) or ""
    ml = re.match(r'(XS|XXL|XL|S|M|L)\b', r)
    mc = re.search(r'(\d{2})\s*cm', r)
    return (ml.group(1) if ml else None, int(mc.group(1)) if mc else None)


def sprzeczne_warianty(tytul_a, tytul_b):
    """Czy tytuły JAWNIE PRZECZĄ temu, że to ten sam rower. Powód albo None.

    To odwrotność `find_relisting`: tamta szuka dowodu TOŻSAMOŚCI, ta dowodu
    RÓŻNICY. Różnica wygrywa — tak samo jak `_MOTOR_DO_WYMIANY` wygrywa
    z `_MOTOR_WYMIENIONY`. Powód jest ten sam co tam: dowód identyczności
    stoi na zbiegu okoliczności (model + cena + jeden fakt), a dowód różnicy
    stoi na tym, co sprzedawca NAPISAŁ WPROST.

    MILCZENIE NIE JEST SPRZECZNOŚCIĄ, a podzbiór to milczenie. "HPC" i
    "HPC Pro" to ten sam rower opisany krócej i dłużej; dopiero "HPC Race"
    kontra "HPC Pro" jest sprzecznością, bo KAŻDA strona mówi coś, czemu
    druga przeczy. Bez tego warunku weto strzelało w skrócone tytuły —
    zmierzone: 4 997 par zamiast 4 254.

    ZMIERZONE 01.09.2026 na market.jsonl (60 dni, 5 541 rowerów po
    odduplikowaniu): z 8 594 par sklejonych miejscowością sprzeczne są
    4 254 (50%), a z 149 par sklejonych samym przebiegiem — 74 (50%).
    Na 1 145 par o IDENTYCZNYM tytule weto nie strzela ani razu i strzelić
    nie może: identyczny tytuł daje identyczne zbiory po obu stronach."""
    wa, wb = _warianty(tytul_a), _warianty(tytul_b)
    if (wa - wb) and (wb - wa):
        return f"wersja {sorted(wa)} vs {sorted(wb)}"
    ba, bb = bateria_z_nazwy(tytul_a), bateria_z_nazwy(tytul_b)
    if ba and bb and ba != bb:
        return f"bateria {ba} vs {bb}"
    # Litera do litery, centymetry do centymetrów. "L" kontra "L / 62 cm" to
    # ta sama rama opisana dokładniej, a nie dwie różne.
    (la, ca), (lb, cb) = _rama_czesci(tytul_a), _rama_czesci(tytul_b)
    if la and lb and la != lb:
        return f"rama {la} vs {lb}"
    if ca and cb and ca != cb:
        return f"rama {ca} cm vs {cb} cm"
    return None


def find_relisting(index: list, title, price_num, mileage_num, loc=None):
    """Zwraca datę pierwotnego ogłoszenia jeśli to re-listing, inaczej None.

    NIEWIEDZA NIE POTWIERDZA TOŻSAMOŚCI. Stara wersja pomijała porównanie
    przebiegu, gdy którakolwiek strona go nie podała — czyli "nie wiem, ile ma
    kilometrów" działało jak "kilometry się zgadzają". Klucz modelu jest gruby
    (`cube stereo hybrid 120` to Race, Pro, SL, 500/625/750 Wh naraz), a cena
    2 000 € powtarza się co kilka dni, więc do zdławienia zdrowego ogłoszenia
    wystarczał zbieg okoliczności.

    Realny przypadek (3492893110, 23.08): Cube Stereo Hybrid 120 Race 625 za
    2 000 €, przebiegu w opisie nie było. Bot uznał go za powtórkę innego Cube'a
    za 2 000 € sprzed sześciu dni — innego roweru, innego sprzedawcy — i nie
    powiadomił. Ogłoszenie było w porządku pod każdym innym względem.

    Do uznania za powtórkę potrzebny jest DOWÓD tożsamości: ta sama
    miejscowość ALBO ten sam tytuł co do znaku. Gdy nie ma żadnego —
    powiadamiamy. Zdublowana wiadomość kosztuje sekundę uwagi, zdławione
    ogłoszenie kosztuje rower.

    TRZECI DOWÓD dołożony 25.08.2026, po skardze „przyszło 5 powiadomień tego
    samego roweru". Przyszły cztery, i wszystkie słusznie wedle ówczesnych
    reguł: sieć sklepów wystawiła TEN SAM nowy rower („KTM Macina Lycan 772 L
    Glorious — 2026 — 48 cm") za 2 799 € w czterech oddziałach naraz.
    Miejscowość za każdym razem inna, przebiegu brak (rower nowy) — więc ani
    jeden z dwóch dowodów nie mógł zadziałać, a dwa dowody oparte na METRYCE
    roweru nie mają jak rozpoznać kopiuj-wklej.

    Tytuł identyczny co do znaku PRZY tej samej cenie nie jest zbiegiem
    okoliczności — to jedno ogłoszenie powielone. Zmierzone na 1293
    powiadomieniach z bazy: 31 takich grup, 65 zbędnych wiadomości, rekord to
    jedno ogłoszenie wysłane CZTERNAŚCIE razy. Warunek długości (patrz
    DEDUP_TYTUL_MIN) chroni tytuły ogólne, gdzie zbieg okoliczności jest realny.

    PRZEBIEG PRZESTAŁ BYĆ DOWODEM TOŻSAMOŚCI (01.09.2026) i jest teraz
    wyłącznie dowodem RÓŻNICY. Powód jest zmierzony, nie teoretyczny: wierna
    powtórka 60 dni rynku (43 466 ogłoszeń wobec indeksu 1 459 ocenionych
    ofert) pokazała 158 ogłoszeń zdławionych jako powtórki, z czego 90 stało
    WYŁĄCZNIE na zgodnym przebiegu — i co najmniej 60 z tych 90 (67%) było
    innym rowerem, co widać w samych tytułach. Dla porównania: dowód z tytułu
    zadziałał 62 razy i nie pomylił się ANI RAZU, dowód z miejscowości 6 razy
    i też ani razu. Przebieg dokładał więc same pomyłki.
    To jest ta wpadka: 3492497177 (Cube Stereo Hybrid 160 HPC SLX 750,
    1350 km, 2 550 €) zdławiony 26.08.2026 jako powtórka 3435648674
    (HPC **SL** 750, 1100 km, 2 500 €, rocznik 2022). Różnica przebiegu 250 km
    mieściła się w tolerancji 300 km, różnica ceny 2,0% w tolerancji 3%.
    Rower żył jeszcze 01.09 i zdążył stanieć do 2 400 €.

    Te same 300 km, które kiedyś potwierdzały tożsamość, dziś jej PRZECZĄ:
    rower przy wznowieniu nie traci kilometrów, więc rozjechany przebieg to
    przesłanka różnicy. Kierunek zmiany jest bezpieczny — dowód różnicy może
    tylko dołożyć wiadomość, nigdy jej nie zabrać."""
    model = dedup_key(title)
    if not model or not price_num:
        return None
    tytul = _tytul_znormalizowany(title)
    for wpis in index:
        m, p, km, d = wpis[0], wpis[1], wpis[2], wpis[3]
        stara_loc = wpis[4] if len(wpis) > 4 else None
        stary_tytul = wpis[5] if len(wpis) > 5 else None
        # Tytuł SUROWY (pozycja 6) — znormalizowany gubi wielkość liter
        # i interpunkcję, a rozmiar ramy czyta się z "Gr. L", nie z "gr l".
        stary_surowy = wpis[6] if len(wpis) > 6 else stary_tytul
        if m != model:
            continue
        if abs(p - price_num) > price_num * DEDUP_PRICE_PCT:
            continue
        loc_zgodna = bool(loc and stara_loc and loc.strip() == stara_loc.strip())
        tytul_zgodny = bool(stary_tytul and tytul == stary_tytul
                            and len(tytul) >= DEDUP_TYTUL_MIN)
        if not (loc_zgodna or tytul_zgodny):
            continue
        # DOWÓD RÓŻNICY BIJE DOWÓD TOŻSAMOŚCI — patrz sprzeczne_warianty.
        if sprzeczne_warianty(title, stary_surowy):
            continue
        if (km is not None and mileage_num is not None
                and abs(km - mileage_num) > DEDUP_KM_TOL):
            continue
        return d
    return None


ALBUM_MAX = 10          # twardy limit Telegrama na jeden album


def send_telegram_album(adresy) -> bool:
    """Reszta zdjęć jako album POD wiadomością, BEZ dźwięku.

    Telegram nie pozwala doczepić przycisków do albumu, więc kolejność jest
    odwrotna niż mogłoby się wydawać: najpierw leci wiadomość ze zdjęciem
    głównym i przyciskami (to ona robi powiadomienie), a dopiero potem reszta
    galerii — cicho, żeby jeden rower nie brzęczał w telefonie dwa razy."""
    adresy = [a for a in (adresy or []) if a][:ALBUM_MAX]
    if len(adresy) < 2:
        return False
    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMediaGroup"
    media = [{"type": "photo", "media": a} for a in adresy]
    try:
        r = requests.post(api_url, json={"chat_id": TELEGRAM_CHAT_ID, "media": media,
                                         "disable_notification": True}, timeout=20)
        if r.ok:
            return True
        log.warning(f"sendMediaGroup {r.status_code}: {r.text[:120]}")
    except Exception as e:
        log.warning(f"sendMediaGroup błąd: {str(e)[:80]}")
    return False


# --- CZUJKA NA MARTWĄ WYSYŁKĘ (18.09.2026) ---------------------------------
# Wpadka tego dnia: token bota przestał działać, KAŻDA wysyłka padała 3 na 3,
# `send_telegram` zapisywała błąd do logu i wracała bez słowa, a bieg kończył
# się KODEM 0. W Actions świeciło się na zielono, właściciel stracił pięć
# powiadomień i dowiedział się o awarii dopiero wtedy, gdy sam zapytał,
# czemu jest cicho. Diagnostyka „wszystko ok" nie miała jak dojść, bo jechała
# tą samą drogą, która padła.
#
# To ta sama rodzina co „alarm działał, kompensacja nie" z 01.09: mechanizm
# istniał i był przetestowany, tylko sygnał szedł tam, gdzie nikt go nie
# odbierał. ALARM O ZERWANEJ DRODZE NIE MOŻE JECHAĆ TĄ DROGĄ. Poza Telegramem
# zostaje jeden świadek - kod wyjścia biegu - więc zgubiona wiadomość maluje
# krok w Actions na czerwono.
#
# Liczymy WIADOMOŚCI, nie próby: `send_telegram` ponawia 3 razy przez ~6 s,
# więc jeden wpis tutaj znaczy „ta wiadomość nie doszła i już nie dojdzie".
ZGUBIONE_WYSYLKI: list = []


# HAMULEC NA LAWINĘ POWIADOMIEŃ (20.09.2026)
#
# Po co: wszystkie bramki tego bota stoją na regułach, a każdą z nich da się
# poluzować jedną linijką - i wtedy nic nie stoi między rynkiem a telefonem
# właściciela. Kanał najlepszych ma sufit `MAX_NA_BIEG` od 09.09; DealHawk
# nie miał go NIGDY. Właściciel poprosił o mechanizmy chroniące przed awarią
# zlecaną z innych rozmów, które nie znają kontekstu, i to jest ta warstwa,
# która działa NIEZALEŻNIE od tego, którą linijkę ktoś ruszył.
#
# PRÓG WZIĘTY Z POMIARU, NIE Z GŁOWY. Zmierzone 20.09.2026 na 296 ostatnich
# commitach `history.jsonl` (wiersze dopisane przez JEDEN bieg):
#
#     mediana 1  |  p90 3  |  p99 8  |  największy zdrowy 11
#
# Jeden wynik odstający (5 740 wierszy, commit 03925394 z 17.09 21:21)
# wyłączony świadomie: to nadrabianie po 16-godzinnej awarii, a nie skan -
# tego samego dnia i o tej samej minucie drgnęły oba wskaźniki kolejek.
# Sufit 20 to niemal dwukrotność największego zdrowego biegu. Dla porównania
# CAŁY dzień to mediana 18 wysłanych ofert, p90 83, maksimum 148
# (liczone na wpisach ze `score` w `seen.json`).
MAX_WYSYLEK_NA_BIEG = 20


def utnij_lawine(pending, sufit=MAX_WYSYLEK_NA_BIEG):
    """(co wysłać, ile uciętych). Funkcja CZYSTA - żeby dało się ją przetestować.

    Reszta NIE wraca w następnym biegu i to jest świadome: `seen.json` jest
    zapisany PRZED wysyłką, więc te rowery są już zapisane jako widziane.
    Gdyby wracały, hamulec zamieniłby jedną lawinę w lawinę powtarzaną co
    bieg - dokładnie ta pułapka, którą opisuje rozdział o kanale najlepszych
    („inaczej wracałaby co bieg, czyli zamieniłaby jedną cichą stratę
    w pętlę hałasu"). Dlatego cena hamulca jest płacona RAZ, a właściciel
    dostaje o tym osobną wiadomość - cisza tutaj byłaby gorsza od lawiny."""
    if sufit is None or len(pending) <= sufit:
        return pending, 0
    return pending[:sufit], len(pending) - sufit


def wiadomosc_o_lawinie(ile_uciete, ile_wyslane, przyklady):
    """Jedna wiadomość zamiast setek. Mówi liczbę, nie 'coś poszło nie tak'."""
    L = ["🛑 <b>Zatrzymałem lawinę powiadomień</b>", ""]
    # Czas TERAŹNIEJSZY, bo ta wiadomość idzie PRZED paczką - właściciel
    # czyta ją, zanim tamte dojdą.
    L.append(f"Ten skan chce wysłać <b>{ile_uciete + ile_wyslane}</b> ofert naraz. "
             f"Wysyłam {ile_wyslane} najświeższych, resztę ({ile_uciete}) wstrzymuję.")
    L.append("")
    L.append("Zdrowy skan wysyła kilka. Tyle naraz znaczy, że <b>zmieniła się "
             "któraś reguła</b> - prawdopodobnie po ostatniej poprawce.")
    if przyklady:
        L.append("")
        L.append("Przykłady z wstrzymanych:")
        for t in przyklady[:3]:
            L.append(f"• {html_mod.escape(str(t)[:70])}")
    L.append("")
    L.append("<i>Te oferty NIE wrócą same - są już zapisane jako widziane. "
             "Jeśli to nie jest awaria, powiedz, a podniosę próg.</i>")
    return "\n".join(L)


def zakoncz():
    """Kod 1, gdy choć jedna wiadomość nie doszła. Inaczej cicho.

    Wołane na KOŃCU biegu, nie w miejscu awarii, i to jest cała ostrożność
    tej czujki: `main` zapisuje seen.json i pushuje PRZED wysyłką, a krok
    „Zapisz seen.json" w tracker.yml ma `if: always()`. Czerwony bieg nie
    gubi więc ani jednego ogłoszenia - traci tylko zielony kolor.

    Bezpiecznik, bez którego ta czujka byłaby SZKODLIWA: `fail-fast: false`
    w tracker.yml. Bez niego pierwsze czerwone ogniwo kasuje sześć
    pozostałych, czyli robi dokładnie to, czego robić nie wolno - gubi skan.
    Pilnuje tego test."""
    if not ZGUBIONE_WYSYLKI:
        return 0
    log.error(f"NIE DOSZŁO {len(ZGUBIONE_WYSYLKI)} wiadomości na Telegram: "
              + " | ".join(ZGUBIONE_WYSYLKI[:5]))
    return 1


def send_telegram(text: str, klawiatura=None, bez_podgladu=False) -> bool:
    """`bez_podgladu` tylko dla LIST ofert. Przy pojedynczym rowerze podgląd
    strony jest zaletą (widać zdjęcie), ale pod listą ośmiu linków Telegram
    i tak pokaże tylko pierwszy - czyli losowy rower udający najważniejszy."""
    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": bez_podgladu,
    }
    if klawiatura:
        payload["reply_markup"] = klawiatura
    for attempt in range(3):
        try:
            r = requests.post(api_url, json=payload, timeout=10)
            if r.status_code == 429:
                retry_after = r.json().get("parameters", {}).get("retry_after", 3)
                log.warning(f"Telegram rate limit, czekam {retry_after}s")
                time.sleep(retry_after + 1)
                continue
            # BŁĄD SKŁADNI HTML NIE MA PRAWA ZJEŚĆ WIADOMOŚCI (19.09.2026).
            # Telegram odpowiada wtedy 400 i "can't parse entities", a stary
            # kod ponawiał TO SAMO trzy razy i gubił rower na zawsze - koszt
            # jednego znaku "<" w tytule ogłoszenia. Ponowienie bez
            # `parse_mode` dowozi treść ze znacznikami na wierzchu: brzydko,
            # ale czytelnie, a cena pomyłki spada z roweru na estetykę.
            #
            # Wyłącznie dla TEGO błędu. Przy 429 i przy sieci ponawiamy po
            # staremu, bo tam składnia jest w porządku.
            if r.status_code == 400 and "parse" in r.text.lower():
                log.error(f"Telegram odrzucił HTML, wysyłam bez znaczników: {r.text[:120]}")
                goly = dict(payload); goly.pop("parse_mode", None)
                goly["text"] = re.sub(r"<[^>]+>", "", text)
                try:
                    r2 = requests.post(api_url, json=goly, timeout=10)
                    r2.raise_for_status()
                    return True
                except Exception as e2:
                    log.error(f"również bez znaczników nie poszło: {e2}")
                    break
            r.raise_for_status()
            return True
        except Exception as e:
            log.error(f"Telegram error (próba {attempt + 1}/3): {e}")
            time.sleep(2)
    # Trzy próby za nami - ta wiadomość przepadła. Zapisujemy sam początek
    # tekstu, bo w logu biegu ma być widać, CO nie doszło, a nie tylko ile.
    ZGUBIONE_WYSYLKI.append(re.sub(r"<[^>]+>", "", text)[:80].replace("\n", " "))
    return False


# === KOMENDY Z TELEGRAMA (kanał wejścia dla bota do sprzedaży) ================
# Bot dotąd tylko WYSYŁAŁ. Tu czyta odpowiedzi właściciela (getUpdates), żeby dało
# się odpytać go z telefonu: „/wycen cube stereo hybrid 2018 2300 400”.
# Offset pilnuje, by nie przetwarzać tej samej wiadomości dwa razy. Wszystko w
# try/except — komendy to dodatek, nigdy nie mogą wywalić głównego skanu.
TELEGRAM_OFFSET_FILE = Path("telegram_offset.json")


# --- JEDNA GOTOWA WIADOMOŚĆ DO SPRZEDAWCY ----------------------------------
# Zasada: JEDNA wiadomość, nie trzy. Sprzedawca odpowiada raz, a Ty kopiujesz
# raz. Pytamy wyłącznie o to, czego bot NIE wyczytał — pytanie o przebieg
# podany w ogłoszeniu wygląda na niechlujstwo i zniechęca do odpowiedzi.
#
# Tekst jedzie w przycisku "kopiuj" (Telegram: copy_text), więc zajmuje jedną
# linijkę ekranu zamiast bloku cytatu. Limit API to 256 znaków — przy jego
# przekroczeniu odpadają najpierw uprzejmości, potem oferta, NIGDY pytania.
PRZYCISK_MAX = 256

_PYTANIA_DE = {
    "przebieg": "wie viele km es gelaufen ist",
    "rama": "welche Rahmengröße es hat",
    "bateria": "wie viel Wh der Akku hat",
}


def wiadomosc_do_sprzedawcy(braki):
    """Pierwszy kontakt: pytania o braki. BEZ ceny — patrz `wiadomosc_oferta`."""
    pytania = [_PYTANIA_DE[b] for b in ("przebieg", "rama", "bateria") if b in (braki or [])]
    baza = "Hallo! Ist das Rad noch verfügbar?"
    if not pytania:
        return f"{baza} Ich hole kurzfristig mit Bargeld ab. Danke!"
    if len(pytania) == 1:
        srodek = f" Können Sie mir sagen, {pytania[0]}?"
    else:
        srodek = f" Können Sie mir sagen, {', '.join(pytania[:-1])} und {pytania[-1]}?"
    for ogon in (" Ich hole kurzfristig mit Bargeld ab. Danke!", " Danke!", ""):
        if len(baza + srodek + ogon) <= PRZYCISK_MAX:
            return baza + srodek + ogon
    return (baza + srodek)[:PRZYCISK_MAX]


def wiadomosc_oferta(buy_price, po_pytaniach: bool):
    """Propozycja ceny — ZAWSZE osobna wiadomość, wysyłana dopiero gdy sprzedawca
    odpisze.

    Zasada od użytkownika (22.08): oferta doklejona do pierwszego kontaktu
    potrafi zabić rozmowę, zanim się zacznie — jeśli kwota wyda się za niska,
    sprzedawca po prostu nie odpowie i nie dowiesz się nawet o przebiegu.
    Najpierw wyciągamy informacje, targujemy się później.

    `po_pytaniach` zmienia otwarcie: gdy pytania już poszły, to kontynuacja
    rozmowy, a nie zaczepka do nieznajomego."""
    if not buy_price:
        return None
    if po_pytaniach:
        return (f"Danke für die Infos! Wären {buy_price} € möglich? "
                f"Ich könnte kurzfristig mit Bargeld abholen.")[:PRZYCISK_MAX]
    return (f"Hallo! Ist das Rad noch verfügbar? Wären {buy_price} € möglich? "
            f"Ich hole kurzfristig mit Bargeld ab. Danke!")[:PRZYCISK_MAX]


TELEGRAM_PODPIS_MAX = 1024   # limit Telegrama na podpis pod zdjęciem


def klawiatura_kopiuj(przyciski):
    """Przyciski, które po tapnięciu wrzucają gotowy tekst do schowka.

    `przyciski` to lista par (napis, tekst). Każdy w osobnym rzędzie, żeby
    napis się nie ucinał — to i tak jedna linijka ekranu na przycisk, zamiast
    kilku linijek bloku cytatu. Telegram nazywa to copy_text; limit 256 znaków."""
    rzedy = [[{"text": napis, "copy_text": {"text": tekst[:PRZYCISK_MAX]}}]
             for napis, tekst in (przyciski or []) if tekst]
    return {"inline_keyboard": rzedy} if rzedy else None


def send_telegram_photo(foto_url: str, caption: str, klawiatura=None) -> bool:
    """Wysyła zdjęcie roweru z podpisem. False = nie poszło, trzeba tekstem.

    Zdjęcie w wiadomości jest kilka razy większe niż podgląd linka, a na
    ekranie blokady widać je razem z pierwszą linijką podpisu — czyli zyskiem
    i ceną. Cała ozdoba jest jednak podporządkowana zasadzie: powiadomienie
    nie może zginąć przez to, że obrazek się nie pobrał."""
    if not foto_url:
        return False
    if len(caption) > TELEGRAM_PODPIS_MAX:
        return False
    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "photo": foto_url,
               "caption": caption, "parse_mode": "HTML"}
    if klawiatura:
        payload["reply_markup"] = klawiatura
    for proba in range(2):
        try:
            r = requests.post(api_url, json=payload, timeout=15)
            if r.status_code == 429:
                time.sleep(r.json().get("parameters", {}).get("retry_after", 3) + 1)
                continue
            if r.ok:
                return True
            # najczęściej: Telegram nie pobrał obrazka z serwera Kleinanzeigen
            log.warning(f"sendPhoto {r.status_code}: {r.text[:120]}")
            return False
        except Exception as e:
            log.warning(f"sendPhoto błąd: {str(e)[:80]}")
            time.sleep(1)
    return False


# --- TŁUMACZENIE OPISU (za darmo) ------------------------------------------
# MyMemory: bez klucza, bez rejestracji, 5 tys. słów na dobę anonimowo —
# przy ~20 powiadomieniach dziennie (~1,6 tys. słów) mieści się z zapasem.
#
# UWAGA NA JAKOŚĆ: w teście 22.08 przetłumaczyło "Nur 2000 km gelaufen" jako
# "Spacerowaliśmy niecałe 2000 km". Dlatego opis jest w wiadomości WYŁĄCZNIE
# prozą-ozdobnikiem, a każda liczba, na której podejmujesz decyzję (przebieg,
# bateria, rocznik, cena), pochodzi z własnego parsera bota i stoi osobno.
# Nigdy nie przenosić liczb z tłumaczenia do linijek decyzyjnych.
TLUMACZ_URL = "https://api.mymemory.translated.net/get"
TLUMACZ_MAX_ZNAKOW = 480     # limit pojedynczego zapytania


def tlumacz_opis(tekst: str):
    """Niemiecki opis → polski. None, gdy się nie udało (wtedy oryginał)."""
    if not tekst:
        return None
    czysty = re.sub(r'\s+', ' ', tekst).strip()[:TLUMACZ_MAX_ZNAKOW]
    if len(czysty) < 20:
        return None
    try:
        r = requests.get(TLUMACZ_URL, params={"q": czysty, "langpair": "de|pl"},
                         timeout=15)
        r.raise_for_status()
        d = r.json()
        if d.get("responseStatus") not in (200, "200"):
            log.warning(f"tłumacz: status {d.get('responseStatus')}")
            return None
        out = (d.get("responseData") or {}).get("translatedText") or ""
        out = re.sub(r'\s+', ' ', out).strip()
        # serwis czasem oddaje wejście bez zmian albo komunikat o limicie
        if not out or out.upper().startswith("MYMEMORY WARNING") or out == czysty:
            return None
        return out
    except Exception as e:
        log.warning(f"tłumacz błąd: {str(e)[:80]}")
        return None


def potwierdz_przycisk(cb_id):
    """Zdejmuje z przycisku kręciołek i mówi, że komenda przyjęta.

    Odpowiedź przychodzi z opóźnieniem jednego biegu (do ~minuty), więc
    Telegram czasem odmówi jej przyjęciem ("query is too old"). To nie jest
    awaria i nie ma prawa niczego zatrzymać - lista i tak przyjdzie."""
    if not cb_id:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery",
            json={"callback_query_id": cb_id,
                  "text": "Przeglądam zapisane oferty…"}, timeout=10)
    except Exception as e:
        log.info(f"answerCallbackQuery: {e}")


def read_telegram_commands() -> list:
    """Zwraca listę nowych tekstów od właściciela (z jego czatu). Aktualizuje
    offset w pliku. Bezpieczne — każdy błąd łyka i zwraca []."""
    try:
        offset = 0
        if TELEGRAM_OFFSET_FILE.exists():
            offset = json.loads(TELEGRAM_OFFSET_FILE.read_text()).get("offset", 0)
        r = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates",
            params={"offset": offset, "timeout": 0}, timeout=15)
        data = r.json()
        if not data.get("ok"):
            return []
        texts, max_id = [], offset - 1
        for upd in data.get("result", []):
            max_id = max(max_id, upd.get("update_id", max_id))
            # STUKNIĘCIE W PRZYCISK to ta sama komenda, tylko wpisana za
            # właściciela. Zamiana na tekst od razu tutaj jest po to, żeby
            # niżej istniała JEDNA ścieżka obsługi zamiast dwóch - dwie
            # rozjeżdżają się przy pierwszej poprawce, a tę dostaje tylko
            # jedna z nich. Przyciski z kanału najlepszych (`zl|...`) nie są
            # nasze: odpada je warunek czatu i nie tykamy ich niczym.
            cb = upd.get("callback_query")
            if cb:
                rozmowa = ((cb.get("message") or {}).get("chat") or {}).get("id")
                if str(rozmowa) != str(TELEGRAM_CHAT_ID):
                    continue
                t = komenda_z_przycisku(cb.get("data"))
                if t:
                    texts.append(t)
                potwierdz_przycisk(cb.get("id"))
                continue
            msg = upd.get("message") or upd.get("channel_post") or {}
            if str((msg.get("chat") or {}).get("id")) != str(TELEGRAM_CHAT_ID):
                continue
            t = (msg.get("text") or "").strip()
            if t:
                texts.append(t)
        if data.get("result"):
            TELEGRAM_OFFSET_FILE.write_text(json.dumps({"offset": max_id + 1}))
        return texts
    except Exception as e:
        log.error(f"read_telegram_commands error: {e}")
    return []


def parse_wycen_command(text: str):
    """'/wycen cube stereo hybrid 2018 2300 400 szybko' →
    (query, year, km, wh, mode). Liczby rozpoznawane po zakresie: 2015-2026=rok,
    300-1000=bateria(Wh), reszta=przebieg. 'szybko'/'max' ustawia tryb.
    None gdy to nie komenda wyceny."""
    text = text.strip()
    if not re.match(r'/?wyce[nń]\b', text, re.I):
        return None
    body = re.sub(r'^/?wyce[nń]\s*', '', text, flags=re.I)
    mode = "balans"
    if re.search(r'\bszybk', body, re.I):
        mode = "szybko"
    elif re.search(r'\b(max|maks)', body, re.I):
        mode = "max"
    body = re.sub(r'\b(szybk\w*|maks\w*|max\w*)\b', ' ', body, flags=re.I)
    nums = [int(x) for x in re.findall(r'\d{2,5}', body)]
    year = next((x for x in nums if 2015 <= x <= 2026), None)
    wh = next((x for x in nums if 300 <= x <= 1000), None)
    km = next((x for x in nums if x != year and x != wh), None)
    query = re.sub(r'\s+', ' ', re.sub(r'\d{2,5}', ' ', body)).strip().lower()
    if not query:
        return None
    return query, year, km, wh, mode


def handle_status() -> str:
    """Raport zdrowia liczony NA RUNNERZE — czyli w tym samym środowisku, w
    którym bot faktycznie pracuje. Odpowiada na pytanie „czy OLX nas wpuszcza"
    bez grzebania w logach GitHub Actions."""
    z = lambda v: f"{int(v):,}".replace(",", " ")
    L = ["🩺 <b>DealHawk — stan zdrowia</b>", ""]

    r = olx_get("https://www.olx.pl/sport-hobby/rowery/q-rower-elektryczny/", timeout=20)
    st = r.status_code if r is not None else "brak odpowiedzi"
    kart = len(parse_olx_cards(r.text)) if (r is not None and r.status_code == 200) else 0
    kb = (len(r.text) // 1024) if r is not None else 0
    L.append(f"🇵🇱 <b>OLX (strona): {'wpuszcza ✅' if kart else 'BLOKUJE 🚫'}</b>\n"
             f"   HTTP {st} · {kart} kafelków · {z(kb)} kB")
    try:
        ra = olx_get("https://www.olx.pl/api/v1/offers/?offset=0&limit=40"
                     "&query=cube+stereo+hybrid", timeout=20)
        rek = len((((ra.json() or {}).get("data")) or [])) if (
            ra is not None and ra.status_code == 200) else 0
        L.append(f"🔌 <b>OLX (API): {'działa ✅' if rek else 'BLOKUJE 🚫'}</b>\n"
                 f"   HTTP {ra.status_code if ra is not None else '-'} · {rek} rekordów")
    except Exception as e:
        L.append(f"🔌 OLX (API): błąd — {str(e)[:60]}")

    # Diagnostyka przekaźnika — bez niej "brak odpowiedzi" niczego nie mówi
    if OLX_RELAY_URL:
        kod = olx_diag().get("przekaznik_status")
        opis = {401: "ODRZUCA KLUCZ — sekret OLX_RELAY_KEY w GitHubie różni się\n"
                     "   od zmiennej KLUCZ w Workerze (uwaga na spację/enter na końcu)",
                429: "przekroczony limit zapytań na minutę",
                503: "Worker nie ma ustawionej zmiennej KLUCZ",
                403: "adres poza dozwoloną listą ścieżek"}.get(kod)
        if przekaznik_zyje() is False:
            L.append("🛰 <b>Przekaźnik: NIE ODPOWIADA</b> 🚫\n"
                     "   sprawdź Workera na dash.cloudflare.com")
        elif kod:
            L.append(f"🛰 <b>Przekaźnik: żyje, ale odmawia (HTTP {kod})</b>\n   {opis}")
        else:
            L.append("🛰 <b>Przekaźnik: OK</b> ✅")
    else:
        L.append("🛰 <b>Przekaźnik: NIE SKONFIGUROWANY</b>\n"
                 "   brak sekretów OLX_RELAY_URL / OLX_RELAY_KEY w GitHubie")

    try:
        ph = json.loads(PARSE_STATE_FILE.read_text())
    except Exception:
        ph = {}
    if ph.get("title_rate") is not None:
        L.append(f"🇩🇪 <b>Kleinanzeigen: {'✅' if ph.get('ok', True) else '⚠️'}</b>\n"
                 f"   odczyt tytułów {int(ph['title_rate'] * 100)}%, "
                 f"cen {int(ph.get('price_rate', 0) * 100)}%")
    # Kanał decyduje o tym, jak SZYBKO przychodzą rowery. Gdy leży, ogłoszenia
    # nadal płyną z zapytań kluczowych, tylko wolniej i w losowej kolejności —
    # dlatego to nie jest alarm, ale musi być widoczne na żądanie.
    kanal = ph.get("kanal")
    if kanal:
        wszystkie_ok = all(cz.strip().endswith(": ok") for cz in kanal.split("·"))
        L.append(f"⚡ <b>Półki nowości: {'✅' if wszystkie_ok else '⚠️'}</b>")
        for kan in POLKI:
            zn = load_feed_znacznik(kan["typ"])
            stan_kan = next((cz.strip() for cz in kanal.split("·")
                             if cz.strip().startswith(kan["nazwa"])), "")
            dobry = stan_kan.endswith(": ok")
            L.append(f"   {'✅' if dobry else '⚠️'} {kan['nazwa']}: "
                     + ("czyta po kolei" if dobry else stan_kan.split(": ", 1)[-1])
                     + (f", ostatnie {format_age(ad_age_minutes(zn))}" if zn else ""))
        if not wszystkie_ok:
            L.append("   (rowery i tak przychodzą, tylko wolniej)")

    L.append("")
    watch = load_olx_watch()
    dat = [v.get("updated") for v in watch.values() if isinstance(v, dict) and v.get("updated")]
    ofert = sum(len(v.get("offers") or {}) for v in watch.values() if isinstance(v, dict))
    L.append(f"📅 Obserwacja OLX: {max(dat) if dat else 'brak'} "
             f"({len(watch)} modeli, {ofert} ofert)")
    cen = load_cennik()
    if cen.get("cechy"):
        L.append(f"💰 Cennik cech: {cen.get('data', '?')} ({cen.get('n_ofert', 0)} ofert)")
    try:
        L.append(f"👁 Dozorca: śledzi {len(json.loads(Path('olx_stan.json').read_text()))} ofert")
    except Exception:
        L.append("👁 Dozorca: jeszcze nie ruszył")
    return "\n".join(L)


def handle_wycen(query, year, km, wh, mode) -> str:
    """Odpytuje silnik wyceny i składa odpowiedź (z siecią)."""
    r = price_reco_for(query, year, km, wh, mode)
    return format_price_reco(query, r, year, km, wh)


# === DZIENNIK REALNYCH TRANSAKCJI (moduł 4: sprzężenie zwrotne) ===============
# Cały rzeczoznawca stoi na cenach WYWOŁAWCZYCH z OLX — czyli na tym, czego
# sprzedawcy sobie życzą. Ile ktoś naprawdę zapłacił, nie wie stąd nikt. Dopóki
# nie ma ani jednej zapisanej transakcji, każdą „naprawę" wyceny da się sprawdzić
# tylko po tym, czy wynik wygląda sensownie — a to nie jest sprawdzenie.
# Ten plik jest jedynym źródłem prawdy w całym repo. Jak history.jsonl:
# dopisujemy, NIGDY nie kasujemy.
TRANSAKCJE_FILE = Path("transakcje.jsonl")
_SLOWA_PUSTE = {"i", "w", "na", "za", "z", "do", "od", "the", "rower", "ebike"}


def _slowa(opis: str) -> set:
    return {w for w in re.findall(r"\w+", (opis or "").lower())
            if len(w) > 2 and w not in _SLOWA_PUSTE}


def parse_transakcja_command(text: str):
    """'/kupilem 6200 cube stereo hybrid 2022' → ('kupno', 6200, 'cube stereo...').
    Cena to pierwsza liczba >=500, która NIE jest rocznikiem — dzięki temu działa
    zarówno „/sprzedalem 10500 cube 2022", jak i „/sprzedalem cube 2022 za 10500".
    None gdy to nie ta komenda albo nie podano ceny."""
    m = re.match(r'/?(kupi[lł]em|sprzeda[lł]em)\b', (text or "").strip(), re.I)
    if not m:
        return None
    typ = "kupno" if re.match(r'/?kupi', m.group(0), re.I) else "sprzedaz"
    body = text.strip()[m.end():].strip()
    ceny = [int(x) for x in re.findall(r'\d{3,7}', body)
            if int(x) >= 500 and not (2015 <= int(x) <= 2026)]
    if not ceny:
        return None
    cena = ceny[0]
    opis = re.sub(r'(?<!\d)' + str(cena) + r'(?!\d)', ' ', body, count=1)
    opis = re.sub(r'\s+', ' ', opis).strip(" ,.-—")
    return (typ, cena, opis)


def zapisz_transakcje(typ, cena, opis, plik=None):
    rec = {"ts": datetime.now().isoformat(timespec="minutes"),
           "typ": typ, "cena": cena, "opis": opis}
    (plik or TRANSAKCJE_FILE).open("a", encoding="utf-8").write(
        json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def wczytaj_transakcje(plik=None):
    out = []
    try:
        for line in (plik or TRANSAKCJE_FILE).read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except Exception:
                continue                      # śmieć w linii nie ubija dziennika
    except FileNotFoundError:
        pass
    return out


def sparuj_transakcje(transakcje):
    """Paruje każdą sprzedaż z najlepiej pasującym WCZEŚNIEJSZYM zakupem (po
    wspólnych słowach opisu, minimum 2). Zwraca (pary, otwarte_zakupy), gdzie
    para to (zakup_albo_None, sprzedaz, zysk_albo_None).

    Świadomie zachowawcze: sprzedaż bez pewnego dopasowania zostaje zapisana
    z zyskiem None, zamiast doklejać się do przypadkowego zakupu. Zmyślony zysk
    zatruwa jedyne prawdziwe dane, jakie ten bot ma."""
    zakupy = [t for t in transakcje if t.get("typ") == "kupno"]
    zajete, pary = set(), []
    for s in [t for t in transakcje if t.get("typ") == "sprzedaz"]:
        slowa_s = _slowa(s.get("opis"))
        najlepszy, ile = None, 0
        for i, zak in enumerate(zakupy):
            if i in zajete or zak.get("ts", "") > s.get("ts", ""):
                continue
            wspolne = len(slowa_s & _slowa(zak.get("opis")))
            if wspolne > ile:
                najlepszy, ile = i, wspolne
        if najlepszy is not None and ile >= 2:
            zajete.add(najlepszy)
            pary.append((zakupy[najlepszy], s, s["cena"] - zakupy[najlepszy]["cena"]))
        else:
            pary.append((None, s, None))
    return pary, [zak for i, zak in enumerate(zakupy) if i not in zajete]


def handle_transakcja(typ, cena, opis) -> str:
    """Zapisuje transakcję i odpowiada. Przy sprzedaży pokazuje REALNY zysk,
    jeśli da się ją zestawić z zakupem."""
    zapisz_transakcje(typ, cena, opis)
    pary, otwarte = sparuj_transakcje(wczytaj_transakcje())
    z = lambda v: f"{int(v):,}".replace(",", " ")
    if typ == "kupno":
        return ("📥 <b>Zapisane: kupno za " + z(cena) + " zł</b>\n"
                f"<i>{html_mod.escape(opis)}</i>\n\n"
                f"Otwartych pozycji: {len(otwarte)}\n"
                "Gdy sprzedasz, napisz <code>/sprzedalem &lt;cena&gt; " +
                html_mod.escape(opis) + "</code> — dopiero wtedy bot pozna "
                "swój błąd i będzie się miał na czym uczyć.")
    zysk = next((zy for zak, sp, zy in pary
                 if sp.get("cena") == cena and sp.get("opis") == opis), None)
    L = ["📤 <b>Zapisane: sprzedaż za " + z(cena) + " zł</b>",
         f"<i>{html_mod.escape(opis)}</i>", ""]
    if zysk is None:
        L.append("⚠️ Nie znalazłem pasującego zakupu, więc zysku nie liczę.")
        L.append("Jeśli ten rower był kupiony, dopisz go: "
                 "<code>/kupilem &lt;cena&gt; " + html_mod.escape(opis) + "</code>")
    else:
        L.append(f"💰 <b>Realny zysk: {z(zysk)} zł</b> (bez kosztów transportu)")
    zamkniete = [zy for _, _, zy in pary if zy is not None]
    L += ["", f"<i>Zamkniętych transakcji w dzienniku: {len(zamkniete)}. "
              "Od nich zależy, czy wycena kiedykolwiek zacznie być sprawdzalna.</i>"]
    return "\n".join(L)


def handle_zycie() -> str:
    """Co dozorca wie o życiu ofert na OLX — surowy odczyt z dziennika.

    Import w środku funkcji, nie na górze pliku: `zycie_ofert` czyta `dozorca`,
    a `dozorca` czyta ten plik. Na górze zrobiłoby się kółko importów."""
    try:
        import zycie_ofert
        return "<b>Życie ofert na OLX</b>\n<pre>" + zycie_ofert.raport() + "</pre>"
    except Exception as e:
        log.error(f"handle_zycie: {e}")
        return "⚠️ Nie udało się policzyć — dziennik dozorcy nie do odczytania."


DOJRZALE_NA_RAZ = 6        # tyle mieści się na ekranie bez przewijania

# Jedno miejsce z listą komend, żeby pomoc nie rozjechała się z kodem.
POMOC_KOMENDY = (
    "Co umiem:\n"
    "<code>/rozmiar L</code> (albo samo <code>/L</code>) - przejrzyj oferty "
    "w jednym rozmiarze ramy\n"
    "<code>/dojrzale</code> — kto schodzi z ceny i nadal stoi\n"
    "<code>/oferta 3515700088</code> — gotowa wiadomość z twardą ofertą\n"
    "<code>/wycen model rok przebieg bateria</code> — wycena sprzedaży\n"
    "<code>/kupilem cena opis</code> — zapisz realny zakup\n"
    "<code>/sprzedalem cena opis</code> — zapisz realną sprzedaż\n"
    "<code>/segmenty</code> — sprzedawalność wg półki cenowej\n"
    "<code>/zycie</code> — co dozorca wie o ofertach na OLX\n"
    "<code>/status</code> — czy bot żyje i co widzi"
)


def handle_dojrzale(min_obnizek=2) -> str:
    """Ogłoszenia, w których sprzedawca schodził z ceny i NADAL stoi.

    Druga strona DealHawka. Tamten pyta „co nowego", kanał najlepszych pyta
    „co najlepsze", a tu pytamy „kto już chce się tego pozbyć". Sprzedawca po
    dwóch obniżkach negocjuje inaczej niż ten, który wystawił wczoraj - i tam
    właśnie siedzi marża.

    `dojrzale.py` liczył to od dawna i nie było go czym wywołać: plik był
    narzędziem z linii poleceń, a właściciel pracuje z telefonu.

    Nic tu nie jest pobierane. Cały wynik pochodzi z dziennika, więc
    ogłoszenie mogło w międzyczasie zniknąć - i tak to nazywamy wprost,
    zamiast udawać wiedzę, której nie mamy."""
    try:
        import dojrzale
        wszystkie = dojrzale.zbierz(min_obnizek=min_obnizek)
    except Exception as e:
        log.error(f"handle_dojrzale: {e}")
        return "⚠️ Nie udało się policzyć dojrzałych ofert."
    if not wszystkie:
        return ("Nikt teraz nie schodzi z ceny na tyle, żeby było o czym pisać.\n"
                f"Szukam ogłoszeń z co najmniej {min_obnizek} obniżkami.")

    # Rama S i XS odpadają: właściciel nie sprzedaje ich w Polsce, więc
    # najhojniejsza nawet przecena nic tu nie zmienia.
    def rama_ok(b):
        r = rozmiar_ramy(b.get("tytul") or "", "")
        return not (r and r.split(" /")[0].strip().upper() in ("S", "XS"))

    # CO DOZORCA WIE O ŻYCIU TYCH OGŁOSZEŃ.
    #
    # Bez tego lista była bezużyteczna: zmierzone 13.09.2026 na ośmiu
    # najmocniej przecenionych ofertach - SIEDEM było już zdjętych. Właściciel
    # to zobaczył pierwszy: "tylko jedna to bylo istniejace ogloszenie,
    # reszta usuniete".
    #
    # Sprawdzanie NA ŻĄDANIE odpada: przy 12% żywych trzeba by ~50 zapytań na
    # jedną listę, a zmierzony próg dławienia Kleinanzeigen to ~22 zapytania
    # w kilka minut z jednego adresu (sprawdzone tego samego dnia, własnymi
    # żądaniami - po dwudziestu paru strony zaczynają wracać bez znaczników
    # kontaktu). Wiedza musi więc przychodzić z dozorcy, który pyta powoli
    # i w tle, a tu ją tylko czytamy. Zero zapytań na komendę.
    try:
        stan_de = json.loads(Path("de_stan.json").read_text(encoding="utf-8"))
    except Exception:
        stan_de = {}

    def zyje(b):
        """True/False/None - None znaczy 'dozorca jeszcze nie sprawdził'."""
        rec = stan_de.get(str(b.get("id")))
        if not rec:
            return None
        if rec.get("zdjete"):
            return False
        return True if rec.get("ostatni_zywy") else None

    w_budzecie = [b for b in wszystkie
                  if b.get("cena") and b.get("maks") and b["cena"] <= b["maks"]
                  and rama_ok(b)]
    zdjetych = sum(1 for b in w_budzecie if zyje(b) is False)
    w_budzecie = [b for b in w_budzecie if zyje(b) is not False]
    # KOLEJNOŚĆ: potwierdzone żywe, potem niesprawdzone.
    #
    # Wśród POTWIERDZONYCH sortujemy po wielkości przeceny - tam wiemy, że
    # ogłoszenie istnieje, więc liczy się tylko okazja.
    #
    # Wśród NIESPRAWDZONYCH po tym, JAK DAWNO sprzedawca ruszał cenę. To
    # proteza na czas, zanim dozorca objedzie cały zbiór (kilka dni), ale
    # proteza uczciwa: obniżka sprzed dwóch dni znaczy, że ktoś tego
    # ogłoszenia dotykał, a obniżka sprzed 36 dni nie znaczy nic. Sortowanie
    # po samej przecenie wypychało na czoło listy oferty najstarsze, czyli
    # te z największą szansą, że już ich nie ma - i dokładnie to właściciel
    # zobaczył 13.09.
    w_budzecie.sort(key=lambda b: (zyje(b) is not True,
                                   0 if zyje(b) is True else b.get("dni_od_obnizki", 999),
                                   -b.get("spadek_pct", 0)))

    if not w_budzecie:
        return (f"Wszystkie {zdjetych} dojrzałe oferty w Twoim budżecie "
                f"są już zdjęte. Nic do pokazania.")

    L = [f"🍐 <b>Kto schodzi z ceny</b> ({len(w_budzecie)} w Twoim budżecie "
         f"z {len(wszystkie)} dojrzałych)"]
    if zdjetych:
        L.append(f"<i>Pominięte {zdjetych} już zdjętych.</i>")
    L.append("")
    for b in w_budzecie[:DOJRZALE_NA_RAZ]:
        sciezka = " → ".join(str(c) for _, c in b["sciezka"])
        L.append(f"<b>{html_mod.escape(b['tytul'][:70])}</b>")
        L.append(f"{sciezka} €   (−{b['spadek_pct']}%, {b['obnizek']} obniżek)")
        fakty = [x for x in (b.get("przebieg"), str(b["rocznik"]) if b.get("rocznik") else None) if x]
        if fakty:
            L.append(" · ".join(fakty))
        stan_txt = {True: "✅ sprawdzone, żyje",
                    None: "❔ dozorca jeszcze nie sprawdził"}[zyje(b)]
        L.append(f"stoi {b['dni_od_pierwszego']} dni, ostatnia obniżka "
                 f"{b['dni_od_obnizki']} dni temu · {stan_txt}")
        L.append(b["url"])
        L.append("")
    if len(w_budzecie) > DOJRZALE_NA_RAZ:
        L.append(f"<i>…i jeszcze {len(w_budzecie) - DOJRZALE_NA_RAZ}. "
                 f"Pisz /dojrzale 3, żeby zobaczyć tylko mocniej przecenione.</i>")
    niesprawdzone = sum(1 for b in w_budzecie[:DOJRZALE_NA_RAZ] if zyje(b) is None)
    if niesprawdzone:
        L.append(f"<i>{niesprawdzone} z powyższych nie było jeszcze sprawdzone "
                 f"przez dozorcę — te mogą już nie istnieć. Ustawione tak, że "
                 f"najpierw idą te, przy których sprzedawca ruszał cenę "
                 f"najświeżej.</i>")
    return "\n".join(L)


# === PRZEGLĄDANIE PO ROZMIARZE RAMY =========================================
# Właściciel 15.09.2026: "chce zobaczyc tylko najnowsze ogloszenia w rozmiarze
# l i wyswietlaja mi sie tylko te l lub te o ktorych nie ma info w ogloszeniu,
# bo lepiej kilka wiecej przegladnac niz ominac; innego dnia chce przegladac
# tylko najnowsze m size".
#
# TO JEST PRZEGLĄDARKA TEGO, CO JUŻ POSZŁO, a nie drugi filtr powiadomień.
# Rozróżnienie jest celowe i pilnuj go: gdyby rozmiar zaczął DŁAWIĆ wysyłkę,
# jeden dzień z ustawieniem "L" oznaczałby ciszę o każdym M i S, których
# właściciel nigdy by nie zobaczył - a rozmiaru nie znamy w 86% ogłoszeń
# (zmierzone niżej), więc dławiłby przede wszystkim rowery bez opisu. Kanał
# sypie dalej wszystkim, a ta komenda pozwala usiąść i przejrzeć wycinek.
#
# ILE ODSIEWA, uczciwie i z dwóch pomiarów tego samego dnia (15.09.2026).
# Na CAŁYM zbiorze 90 dni (2 504 oferty) rozmiar czytelny w 349 (14%) - tyle
# daje sam tytuł. Na oknie 3-dniowym, gdzie wpisy mają już pole `rama` czytane
# z OPISU: 61 z 159 (38%), w tym 30 rowerów L. Różnica to nie szum, tylko wiek
# pola: bot zapisuje je od 12.09.2026, więc udział rośnie z każdym dniem.
# Dlatego licznik jedzie w NAGŁÓWKU KAŻDEJ WIADOMOŚCI, a nie w dokumentacji,
# która zestarzeje się w tydzień: właściciel ma widzieć, ile TA lista odsiała,
# zanim uzna, że przejrzał wszystkie L na rynku.
ROZMIAR_PEWNE_MAX = 8        # sufity na jedną wiadomość; limit Telegrama to 4096 znaków
ROZMIAR_BEZ_INFO_MAX = 6
ROZMIAR_ZNAKI_MAX = 3800     # limit Telegrama to 4096; zapas na emoji (liczą się podwójnie)
ROZMIARY_PRZYCISKI = ("L", "M", "S", "XL")   # XS pomijamy: właściciel go nie kupuje
ROZMIAR_OKNA = (1, 3, 7)
# Sufit okna. `seen.json` trzyma 90 dni (SEEN_MAX_AGE_DAYS), ale ogłoszenie
# sprzed miesiąca to w 90% trup - przeglądanie go jest stratą czasu.
# Ten sam sufit stoi w `rozmiary.DNI_MAX`, bo moduł ma się bronić sam,
# gdy ktoś zawoła go z linii poleceń.
ROZMIAR_DNI_MAX = 30


def _dni_slownie(d):
    return "1 dzień" if d == 1 else f"{d} dni"


def _okno_slownie(d):
    """Nagłówek odmienia się inaczej niż napis na przycisku: "ostatni dzień",
    ale "1 dzień" na guziku."""
    return "ostatni dzień" if d == 1 else f"ostatnie {d} dni"


def parse_rozmiar_command(text):
    """'/rozmiar L 7', '/L', '/ramy m' → (litera, dni). None gdy to nie ta komenda.

    `litera=None` znaczy przegląd wszystkich rozmiarów naraz.

    SKRÓT JEDNOLITEROWY WYMAGA UKOŚNIKA i to nie jest ozdoba: samo "M"
    w wiadomości porwałoby każdą normalną rozmowę z botem. Ukośnik odróżnia
    komendę od tekstu, tak jak w pozostałych wzorcach niżej."""
    t = (text or "").strip()
    m = re.match(r'/?(?:rozmiary|rozmiar|rozm|ramy|rama)\b(.*)$', t, re.I)
    if m:
        ogon = m.group(1)
    else:
        m = re.match(r'/(xs|xxl|xl|s|m|l)\b(.*)$', t, re.I)
        if not m:
            return None
        ogon = f"{m.group(1)} {m.group(2)}"
    ml = re.search(r'\b(xs|xxl|xl|s|m|l)\b', ogon, re.I)
    md = re.search(r'\b(\d{1,2})\b', ogon)
    litera = ml.group(1).upper() if ml else None
    dni = int(md.group(1)) if md else ROZMIAR_OKNA[1]
    return litera, max(1, min(ROZMIAR_DNI_MAX, dni))


def komenda_z_przycisku(dane):
    """'rozm|L|3' → '/rozmiar L 3'. None, gdy to nie jest nasz przycisk.

    Przycisk NIE jest osobną ścieżką w kodzie - wpisuje za właściciela tę samą
    komendę, którą mógłby napisać palcem. Jedna droga to jeden zestaw błędów
    do naprawienia, a nie dwa rozjeżdżające się z każdą poprawką."""
    czesci = (dane or "").split("|")
    if czesci and czesci[0] == "of":
        # Pełna oferta mieszka w `oferta.py` razem z własnym przyciskiem -
        # jeden moduł, jedna reguła. Tu tylko przekazujemy dalej.
        #
        # Awaria TEGO modułu nie ma prawa zabrać CAŁEJ kolejki: wyjątek stąd
        # wypada do szerokiego `except` w `read_telegram_commands`, które
        # zwraca wtedy pustą listę - czyli /rozmiar, /status i reszta
        # zamilkłyby razem z nim, po cichu.
        try:
            import oferta
            return oferta.komenda_z_przycisku(dane)
        except Exception as e:
            log.error(f"przycisk oferty: {e}")
            return None
    if len(czesci) != 3 or czesci[0] != "rozm" or not czesci[2].isdigit():
        return None
    litera, dni = czesci[1].upper(), czesci[2]
    if litera == "*":
        return f"/rozmiar {dni}"
    if litera in ("XS", "S", "M", "L", "XL", "XXL"):
        return f"/rozmiar {litera} {dni}"
    return None


def klawiatura_rozmiarow(litera, dni):
    """Rozmiar i okno jednym kciukiem. Aktywny wybór w cudzysłowie ostrym,
    bo Telegram nie umie podświetlić przycisku, a bez znacznika nie widać,
    co się właśnie ogląda."""
    def txt(napis, aktywny):
        return f"«{napis}»" if aktywny else napis
    return {"inline_keyboard": [
        [{"text": txt(l, l == litera), "callback_data": f"rozm|{l}|{dni}"}
         for l in ROZMIARY_PRZYCISKI],
        [{"text": txt(_dni_slownie(d), d == dni),
          "callback_data": f"rozm|{litera or '*'}|{d}"} for d in ROZMIAR_OKNA],
        [{"text": "📊 wszystkie rozmiary", "callback_data": f"rozm|*|{dni}"}],
    ]}


def _kiedy_slownie(iso, dzis=None):
    try:
        ile = ((dzis or date.today()) - date.fromisoformat(iso)).days
    except (TypeError, ValueError):
        return "kiedyś"
    return {0: "dziś", 1: "wczoraj"}.get(ile, f"{ile} dni temu")


def _kafelek_rozmiaru(o, dzis=None):
    """Trzy linijki na rower: co to jest, za ile i czy warto otworzyć.

    Link jest PODPIĘTY POD SŁOWO, nie wklejony gołym adresem. Goły adres
    w liście ośmiu ofert to osiem linijek śmiecia i osiem podglądów stron."""
    # Tytuł w seen.json jest JUŻ raz zakodowany przez parser strony
    # ("dustyolive&#39;n&#39;gold") - kodowanie drugi raz wychodzi na ekranie
    # jako "&amp;#39;". Ta sama proteza co w najlepsze.zbuduj_wiadomosc.
    tytul = html_mod.escape(html_mod.unescape(o["tytul"] or ""))[:90]
    region = region_ogloszenia({"url": o["url"], "loc": o.get("loc")})
    # 2,6% ofert nie ma ceny w ogóle ("VB", "brak ceny"). Sama "VB" w linijce
    # z faktami wygląda jak cena i nią nie jest - to samo rozróżnienie co
    # w najlepsze.zbuduj_wiadomosc.
    cena = str(o.get("cena") or "")
    fakty = [x for x in (
        f"rama {o['rama']}" if o.get("rama") else "rozmiar nie podany",
        cena if re.search(r'\d', cena) else "cena do ustalenia",
        o.get("przebieg"),
        str(o["rocznik"]) if o.get("rocznik") else None,
        region or o.get("loc")) if x]
    zysk = o.get("zysk")
    if zysk is None:
        ogon = ["❔ zysku nie liczyłem"]
    else:
        znak = "🔥" if zysk > 500 else "🟡" if zysk > 0 else "🔴"
        ogon = [f"{znak} ~{zysk:+,.0f} zł".replace(",", " ")]
    ogon.append(_kiedy_slownie(o.get("data"), dzis))
    ogon.append(f'<a href="{html_mod.escape(o["url"], quote=True)}">otwórz ↗</a>')
    return f"<b>{tytul}</b>\n" + " · ".join(fakty) + "\n" + " · ".join(ogon)


def _przeglad_rozmiarow(w):
    """Ekran startowy: z czego składa się okno. Tabelka idzie w <pre>, bo
    tylko czcionka o stałej szerokości ustawia liczby w kolumnie."""
    c = w["licznik"]
    wiersze = [f"{'rozmiar':<10}{'ofert':>6}"]
    for l in ("L", "M", "S", "XS", "XL", "XXL"):
        if c[l] or l in ROZMIARY_PRZYCISKI:
            wiersze.append(f"{l:<10}{c[l]:>6}")
    if c["cm"]:
        wiersze.append(f"{'tylko cm':<10}{c['cm']:>6}")
    wiersze.append(f"{'bez info':<10}{c['brak']:>6}")
    return ["📐 <b>ROZMIARY RAM</b> · " + _okno_slownie(w["dni"]),
            f"Z {w['w_oknie']} ofert, które bot Ci wysłał.",
            "",
            "<pre>" + "\n".join(wiersze) + "</pre>",
            "Stuknij rozmiar pod spodem albo napisz <code>/L</code>, "
            "<code>/M</code>, <code>/rozmiar L 7</code>."]


def handle_rozmiar(litera=None, dni=None):
    """Lista ofert w jednym rozmiarze. Zwraca (tekst, klawiatura).

    Nic tu nie jest pobierane - komplet pochodzi z `seen.json` i `de_stan.json`.
    Ogłoszenie mogło w międzyczasie zniknąć i mówimy to wprost, zamiast udawać
    wiedzę, której nie mamy (ta sama zasada co w `/dojrzale`)."""
    dni = dni or ROZMIAR_OKNA[1]
    try:
        import rozmiary
        w = rozmiary.zbierz(litera, dni)
    except Exception as e:
        log.error(f"handle_rozmiar: {e}")
        return "⚠️ Nie udało się przejrzeć ofert po rozmiarze.", None

    klawiatura = klawiatura_rozmiarow(litera, w["dni"])
    # Reguła 7: pusta lista z nieczytelnego pliku wygląda IDENTYCZNIE jak
    # spokojny rynek, a znaczy coś zupełnie innego. Cisza jest tu gorsza
    # od błędu, więc mówimy wprost, że to awaria, a nie brak rowerów.
    if w.get("awaria"):
        return (f"⚠️ Nie mogę odczytać pliku <code>{w['awaria']}</code>, więc nie "
                f"wiem, co bot Ci ostatnio wysyłał.\nTo awaria pliku, a NIE pusty "
                f"rynek - powiadomienia idą dalej normalnie.", klawiatura)
    if not litera:
        return "\n".join(_przeglad_rozmiarow(w)), klawiatura

    pewne, bez_info = w["pewne"], w["bez_info"]
    if not pewne and not bez_info:
        powod = (f"Bot nie wysłał w tym oknie ani jednej oferty."
                 if not w["w_oknie"] else
                 f"Z {w['w_oknie']} ofert w tym oknie każda z czytelnym "
                 f"rozmiarem ({w['odsiane']}) była inna niż {litera}, "
                 f"a bez rozmiaru nie było żadnej.")
        return ("\n".join([
            f"📐 <b>ROZMIAR {litera}</b> · {_okno_slownie(w['dni'])}",
            "", powod, "Spróbuj szerszego okna przyciskiem niżej."]), klawiatura)

    L = [f"📐 <b>ROZMIAR {litera}</b> · {_okno_slownie(w['dni'])}",
         f"✅ pewne: {len(pewne)}  ·  ❔ bez info: {len(bez_info)}  ·  "
         f"🚫 odsiane: {w['odsiane']}"]
    if w["zdjete"]:
        L.append(f"🗑 {w['zdjete']} już zdjętych - pominięte")

    # Reguła 6: każda liczba z etykietą. To jest ZMIERZONE na tym oknie,
    # nie wzięte z dokumentacji - właściciel ma wiedzieć, ile ta lista
    # naprawdę odsiała, zanim uzna, że widział wszystkie rowery tego rozmiaru.
    udzial = round(w["z_rozmiarem"] / w["w_oknie"] * 100) if w["w_oknie"] else 0
    stopka = (f"<i>Rozmiar czytam z tytułu i opisu ogłoszenia. W tym oknie znam "
              f"go w {w['z_rozmiarem']} z {w['w_oknie']} ofert ({udzial}%) - "
              f"reszta jest na liście, bo lepiej przejrzeć kilka za dużo niż "
              f"ominąć swój rower. Opisy czytam od 12.09.2026, starsze "
              f"ogłoszenia mają rozmiar tylko wtedy, gdy stał w tytule.</i>")

    # BUDŻET LICZONY NA CAŁEJ WIADOMOŚCI, nie na samych kafelkach. Adres
    # ogłoszenia to ~120 znaków ukrytych pod słowem "otwórz", więc osiem
    # rowerów potrafi wyjść dwa razy dłużej, niż wygląda na ekranie -
    # a wiadomość ponad limit Telegrama nie dochodzi W CAŁOŚCI.
    budzet = ROZMIAR_ZNAKI_MAX - len(stopka) - sum(len(x) + 1 for x in L)
    # KOLEJNOŚĆ: najpierw pewne trafienia, w każdej grupie od najnowszej.
    # Mieszanie obu grup po samej dacie zakopywałoby jedyne cztery rowery,
    # o których cokolwiek wiemy, pod dwiema setkami "nie wiadomo".
    for naglowek, grupa, sufit in (
            (f"✅ <b>PEWNE {litera}</b>", pewne, ROZMIAR_PEWNE_MAX),
            ("❔ <b>BEZ INFO O ROZMIARZE</b>", bez_info, ROZMIAR_BEZ_INFO_MAX)):
        if not grupa:
            continue
        pokazane, koszt = 0, len(naglowek) + 3
        czesc = ["", naglowek, ""]
        for o in grupa[:sufit]:
            kafelek = _kafelek_rozmiaru(o)
            if koszt + len(kafelek) + 2 > budzet:
                break
            czesc += [kafelek, ""]
            koszt += len(kafelek) + 2
            pokazane += 1
        if not pokazane:
            continue
        if len(grupa) > pokazane:
            czesc[1] += f" ({pokazane} z {len(grupa)}, od najnowszej)"
        L += czesc
        budzet -= koszt

    L.append(stopka)
    return "\n".join(L), klawiatura


def process_telegram_commands():
    """Przetwarza komendy z Telegrama (/wycen, /segmenty). Odporne na błędy."""
    for cmd in read_telegram_commands():
        try:
            if re.match(r'/?(status|zdrowie|dziala)', cmd.strip(), re.I):
                log.info(f"komenda /status: {cmd}")
                send_telegram(handle_status())
                continue
            # /rynek - ile ogloszen stoi i w ktora strone idzie (spis_rynku.py)
            # /plynnosc, /segment - ile z nich schodzi i po ilu dniach (plynnosc.py)
            #
            # DO 20.09.2026 obie te komendy wysylaly `format_segments`, czyli
            # "schodzi 80% w 30 dni, mediana 9 dni". Ta liczba byla zawyzona:
            # wiek oferty liczono od naszego pierwszego widzenia, a oferta zyje
            # mediane 30 dni, zanim bot ja zobaczy. Teraz kazda z komend oddaje
            # osobny, mierzony fakt, a nie jeden wniosek z zepsutego wejscia.
            if re.match(r'/?(rynek|spis)', cmd.strip(), re.I):
                log.info(f"komenda /rynek: {cmd}")
                try:
                    import spis_rynku
                    send_telegram(spis_rynku.raport())
                except Exception as e:
                    send_telegram(f"🧮 Spis rynku niedostepny: {type(e).__name__}. "
                                  f"Dziennik: <code>spis_rynku.jsonl</code>")
                continue
            if re.match(r'/?(polka|przeplyw)', cmd.strip(), re.I):
                log.info(f"komenda /polka: {cmd}")
                try:
                    import przeplyw
                    send_telegram(przeplyw.raport())
                except Exception as e:
                    send_telegram(f"🔁 Przeplyw polki niedostepny: {type(e).__name__}. "
                                  f"Dane: <code>polka_zdarzenia.jsonl</code>")
                continue
            if re.match(r'/?(plynnosc|segment)', cmd.strip(), re.I):
                log.info(f"komenda /plynnosc: {cmd}")
                try:
                    import plynnosc
                    send_telegram(plynnosc.raport())
                except Exception as e:
                    send_telegram(f"📉 Pomiar plynnosci niedostepny: {type(e).__name__}. "
                                  f"Dane: <code>zdarzenia/olx-*.jsonl</code>")
                continue
            # "ł" i "ż" MUSZĄ być w klasie znaków. `\w` w Pythonie owszem je
            # obejmuje, ale wzorzec "dojrzal\w*" wymaga litery "l", więc
            # "/dojrzałe" pisane po polsku NIE trafiało - a właściciel pisze
            # po polsku. Zgłoszone 13.09.2026 słowami "napisalem i nic".
            m = re.match(r'/?(dojrza[lł]\w*|przecen\w*)\s*(\d)?',
                         cmd.strip(), re.I)
            if m:
                log.info(f"komenda /dojrzale: {cmd}")
                send_telegram(handle_dojrzale(int(m.group(2)) if m.group(2) else 2))
                continue
            if re.match(r'/?(zycie|życie|oferty)', cmd.strip(), re.I):
                log.info(f"komenda /zycie: {cmd}")
                send_telegram(handle_zycie())
                continue
            # PO wzorcach wyżej, bo skrót jednoliterowy jest łakomy: "/s"
            # musi zostać rozmiarem S, ale "/status" ma nadal być statusem.
            # Tamte wzorce nie mają `\b`, więc łapią pierwsze i wygrywają.
            parsed = parse_rozmiar_command(cmd)
            if parsed:
                log.info(f"komenda /rozmiar: {cmd}")
                tekst, klawiatura = handle_rozmiar(*parsed)
                send_telegram(tekst, klawiatura, bez_podgladu=True)
                continue
            # PRZED `/zycie`: tamten wzorzec ma w sobie słowo "oferty", więc
            # samo "/oferty" nadal trafia do dozorcy, tak jak dotąd. Ta komenda
            # odzywa się na "/oferta", "/of" i na link wklejony ZA którymś
            # z nich.
            #
            # SAM goły link tu NIE działa i to jest decyzja, nie przeoczenie
            # (właściciel, 19.09.2026: "chce zeby to dzialalo tylko na
            # bestdealhawku"). Rozpoznaje go `oferta.komenda_z_linku`, wołane
            # wyłącznie z `najlepsze.py`. Do 19.09 stało tu, że komenda
            # odzywa się "na wklejony link" - i była to nieprawda, czyli ta
            # sama klasa wpadki co "komentarz opisujący zasadę to NIE jest
            # zasada" z 18.09. Dziś pilnują tego testy po obu stronach.
            import oferta as _oferta
            parsed = _oferta.parse_oferta_command(cmd)
            if parsed:
                log.info(f"komenda /oferta: {cmd}")
                send_telegram(_oferta.handle_oferta(*parsed), bez_podgladu=True)
                continue
            parsed = parse_transakcja_command(cmd)
            if parsed:
                log.info(f"komenda transakcji: {cmd}")
                send_telegram(handle_transakcja(*parsed))
                continue
            parsed = parse_wycen_command(cmd)
            if parsed:
                log.info(f"komenda /wycen: {cmd}")
                send_telegram(handle_wycen(*parsed))
                continue
            # NIEROZPOZNANA KOMENDA MA ODPOWIEDZIEĆ, NIE MILCZEĆ.
            # Do 13.09.2026 wiadomość, której żaden wzorzec nie złapał, ginęła
            # bez śladu. Właściciel napisał komendę, nie dostał nic i nie miał
            # jak odróżnić "bot nie działa" od "bot nie zrozumiał" - zgłosił to
            # słowami "napisalem i nic". Cisza jest tu gorsza od błędu, tak samo
            # jak przy cichych awariach w regule 7.
            if cmd.strip().startswith("/"):
                log.info(f"nieznana komenda: {cmd}")
                send_telegram(f"Nie znam komendy <code>{html_mod.escape(cmd.strip()[:40])}</code>.\n\n"
                              + POMOC_KOMENDY)
                continue
            # WKLEJONY GOŁY LINK NIE MOŻE GINĄĆ W CISZY (19.09.2026).
            # `read_telegram_commands` przepuszcza KAŻDY tekst, a odpowiedź
            # dostawały dotąd wyłącznie wiadomości z ukośnikiem - więc
            # wklejony adres znikał bez śladu, tak samo jak na kanale.
            # Właściciel zgłosił to słowami "wyslalem link i cisza bez
            # reakcji" i nie miał jak odróżnić "nie zrozumiałem" od
            # "bot padł". Ta sama zasada co przy nieznanej komendzie wyżej.
            #
            # To NIE jest obejście jego decyzji "goły link tylko na
            # bestdealhawku": bot tu oferty NIE składa, tylko mówi, gdzie
            # ten link zadziała i jak go tu użyć. Pilnuje tego test.
            # Import LOKALNY, nie poleganie na tym, ze blok `/oferta` wyzej
            # zdazyl sie wykonac - przestawienie kolejnosci dispatchu daloby
            # wtedy NameError zamiast odpowiedzi.
            import oferta as _of_link
            if _of_link.wyglada_na_probe_linku(cmd):
                log.info(f"goły link na DealHawku: {cmd[:60]}")
                send_telegram(
                    "Sam wklejony link działa na kanale najlepszych ofert.\n\n"
                    "Tutaj dopisz komendę przed adresem:\n"
                    "<code>/oferta &lt;wklejony link&gt;</code>\n\n"
                    "Albo podaj sam numer: <code>/oferta 3517059558</code>.",
                    bez_podgladu=True)
        except Exception as e:
            log.error(f"process_telegram_commands błąd dla '{cmd}': {e}")
            send_telegram("⚠️ Nie udało się przetworzyć.\n\n" + POMOC_KOMENDY)


def parse_price(price_str: str) -> object:
    m = re.search(r'[\d.,]+', price_str.replace(".", "").replace(",", ""))
    if m:
        try:
            return int(m.group())
        except ValueError:
            pass
    return None


def parse_mileage(mileage_str: str) -> object:
    if not mileage_str or mileage_str == "brak danych":
        return None
    m = re.search(r'[\d.,]+', mileage_str.replace(".", "").replace(",", ""))
    if m:
        try:
            return int(m.group())
        except ValueError:
            pass
    return None


def score_listing(listing: dict, median_price, odniesienie=None) -> int:
    """`odniesienie` to mediana WŁASNEJ rodziny i baterii tego roweru
    (`sygnal_rozrzutu`). Gdy jest, bije medianę półki i to ona wyznacza punkty
    za cenę.

    Dlaczego w ogóle: mediana półki liczy się po wszystkim, co na niej leży —
    Cube za 900 € i Levo za 4 000 € naraz. Zniżka wobec takiej mediany mówi,
    jaki to model, a nie czy oferta jest dobra, więc tani model dostawał
    komplet 40 punktów zawsze, a drogi nigdy. Mediana półki zostaje wyłącznie
    jako zapas na modele bez własnej próbki (2,0% ruchu ma pełny kubełek,
    zmierzone 29.08.2026) — bez niej te oferty straciłyby punkty za cenę
    w całości."""
    score = 0
    title_lower = listing["title"].lower()

    # 1. Cena vs mediana WŁASNEGO modelu, a dopiero z braku danych vs półka (0-40 pkt)
    price_num = listing.get("price_num")
    baza = odniesienie or median_price
    if price_num and baza:
        discount_pct = (baza - price_num) / baza * 100
        score += max(0, min(40, int(discount_pct * 1.5)))

    # 2. Przebieg (0-30 pkt)
    km = listing.get("mileage_num")
    if km is not None:
        score += max(0, int(30 - (km / 100)))
    else:
        score += 15  # brak danych = neutralne

    # 3. Stan (0-15 pkt)
    for kw in GOOD_CONDITION:
        if kw in title_lower:
            score += 15
            break

    # 4. Marka z dobrym resale value w PL (0-15 pkt)
    for brand in PREMIUM_BRANDS:
        if brand in title_lower:
            score += 15
            break

    return score


def is_junk(title: str) -> bool:
    t = title.lower().strip()
    if any(kw in t for kw in SKIP_KEYWORDS):
        return True
    if any(re.search(p, t) for p in SKIP_PATTERNS):
        return True
    first_word = t.split()[0] if t.split() else ""
    return first_word in PART_TITLE_PREFIXES


MOTOR_BRANDS = [
    "bosch",
    "specialized turbo", "specialized kenevo", "specialized levo",
]

# RODZINY MODELI, KTÓRE MAJĄ BOSCHA Z DEFINICJI - druga droga do tej samej
# wiedzy. Sprzedawca nie ma obowiązku napisać "Bosch": producent zamontował
# ten silnik fabrycznie i nie oferował w tej linii żadnego innego.
#
# Wpadka 01.09.2026, od której to powstało: "Cube Stereo Hybrid 160 HPC SLX
# 750 E-Bike Mountainbike" (3498596629, 2 980 EUR, wystawiony 30.08 o 11:49,
# bot zobaczył go po 7 minutach) wypadł na `obcy_silnik`. Ani tytuł, ani cały
# opis nie zawierały słowa "Bosch" - sprzedawca wypisał kolor, rozmiar ramy,
# opony i pojemność akumulatora. Rower jest Boschem, filtr tego nie wiedział.
# Tego samego dnia na 14 odrzutów `obcy_silnik` 10 było z tych rodzin.
#
# LISTA NIE SIEDZI W KODZIE, tylko w `silniki_bosch.json`, i to jest celowe:
# właściciel ma ją czytać i poprawiać sam, a `sprawdz_silniki.py` przelicza
# jej liczby na aktualnych danych i krzyczy, gdy któraś rodzina przestała się
# bronić. Wiedza o sprzęcie zmienia się z rocznikami, kod nie musi.
SILNIKI_FILE = Path("silniki_bosch.json")
_silniki_cache = None


def _wz_frazy(fraza):
    """Fraza na wzorzec odporny na spacje i dywizy: "e power" = "e-power".

    UWAGA: wzorce z tej rodziny liczy się na tekście JUŻ zamienionym na małe
    litery (patrz `has_known_motor`), więc nie mają `re.IGNORECASE`. Puszczone
    po surowym tytule przegapią "Yamaha" z dużej litery - ta pomyłka
    zafałszowała pierwszy pomiar do tej poprawki.

    Koniec frazy to `(?![a-z0-9])`, a nie `\b`, bo producenci piszą nazwy
    z indeksem górnym: "Thron²", "Jam²", "Jarifa²". Dla Pythona "²" jest
    znakiem słowa, więc `\bthron\b` NIE trafia w "thron²" - zmierzone
    02.09.2026, ta jedna granica gubiła 72 ogłoszenia Focusa."""
    return re.compile(r"\b" + r"[\s-]*".join(re.escape(w) for w in fraza.split())
                      + r"(?![a-z0-9])")


_obserwowane_cache = None


def load_obserwowane(force=False):
    """Modele, których właściciel NIE MOŻE przegapić (`obserwowane.json`).

    BRAK PLIKU TO AWARIA, NIE STAN NATURALNY (reguła 7). Bez niego bot wraca
    do zwykłych bramek i po cichu przestaje dowozić rower, o który właściciel
    prosił imiennie - a on zobaczyłby tylko ciszę i uznał, że takich ofert nie
    ma. Ta sama decyzja co przy `topowe_modele.json`.

    Wiedza siedzi w PLIKU, nie w kodzie, bo to lista życzeń właściciela i ma
    ją zmieniać sam - tak jak `silniki_bosch.json` i `topowe_modele.json`."""
    global _obserwowane_cache
    if _obserwowane_cache is None or force:
        wpisy = []
        try:
            dane = json.loads(OBSERWOWANE_FILE.read_text(encoding="utf-8"))
            for w in dane.get("obserwowane", []):
                if isinstance(w, dict) and w.get("nazwa") and w.get("wymaga"):
                    wpisy.append(w)
        except FileNotFoundError:
            zglos_problem("brak_obserwowanych",
                          f"{OBSERWOWANE_FILE} nie istnieje - modele z listy "
                          f"życzeń przechodzą przez zwykłe bramki")
        except Exception as e:
            zglos_problem("obserwowane_nieczytelne", f"{OBSERWOWANE_FILE}: {e}")
        _obserwowane_cache = wpisy
    return _obserwowane_cache


def obserwowany(tytul):
    """Wpis z listy życzeń pasujący do tytułu, albo None.

    WSZYSTKIE fragmenty z `wymaga` muszą pasować naraz. Sama nazwa wersji
    („tm") trafia w cudze tytuły, więc wpis zawsze wymienia też model -
    zmierzone 19.09.2026: „Cube Reaction Hybrid 160 TM" ma trzy z czterech
    fragmentów i słusznie odpada na „stereo"."""
    low = (tytul or "").lower()
    for wpis in load_obserwowane():
        try:
            if all(re.search(w, low) for w in wpis["wymaga"]):
                return wpis
        except re.error:
            zglos_problem("obserwowane_zly_wzorzec", wpis.get("nazwa", "?"))
    return None


# Domyślne warunki OZNACZENIA obserwowanej oferty. Właściciel nadpisuje je
# per wpis w `obserwowane.json`, bo to jego lista życzeń, nie stała kodu.
# Co właściciel ma sprawdzić SAM, gdy sprzedawca pola nie podał. Osobno na
# pole, bo rady są różne: przy ramie chodzi o to, że rower może być nie do
# jazdy, przy przebiegu - że może być zajeżdżony.
NIEZNANE_PODPOWIEDZI = {
    "rama": "może być M albo S, sprawdź przed dojazdem",
    "przebieg": "może być zajeżdżony, zapytaj przed dojazdem",
}

OZNACZ_DOMYSLNE = {
    "rama": ["L"],
    "rama_nieznana_liczy_sie": True,
    "przebieg_max": 2000,
    # Właściciel 19.09.2026: "to niech przychodza tez te nieznane". Domyślna
    # wartość jedzie razem z jego wpisem w pliku, żeby model dopisany jutro
    # zachowywał się tak samo, jak ten, o który prosił dziś.
    "przebieg_nieznany_liczy_sie": True,
}


def oznacz_obserwowany(wpis, oferta):
    """Czy ta sztuka obserwowanego modelu zasługuje na DRUGĄ wiadomość.

    Zwraca `(tak, powody)`. Powody idą wprost do wiadomości, bo oznaczenie
    twierdzi coś o rowerze i musi powiedzieć, co ZMIERZYŁO, a czego nie
    (reguła 6). Rower z nieznaną ramą oznaczony jako "L" byłby kłamstwem.

    Właściciel 19.09.2026: "jezeli oznaczasz obserwowane to interesuje mnie
    l size i w miare niski przebieg np do 2k km, wszystkie inne i ponad nie
    oznaczasz".

    NIEZNANE POLE PRZEPUSZCZAMY, OBA. Rama - bo tak zdecydował właściciel przy
    `/rozmiar` (15.09): "lepiej kilka wiecej przegladnac niz ominac". Przebieg -
    bo o to poprosił wprost 19.09, zobaczywszy pomiar: "to niech przychodza tez
    te nieznane".

    Argument PRZECIW nieznanemu przebiegowi jest nadal prawdziwy i warto go
    znać, zanim ktoś tę wartość przestawi z powrotem: reguła z pierwszego dnia
    kanału najlepszych mówi, że "niska cena przy nieznanym stanie NIE jest
    dowodem okazji", a rower bez odczytu może mieć 15 000 km. Zmienił się
    właściciel decyzji, nie pomiar - i dlatego wiadomość ma o tym powiedzieć
    wprost, zamiast stawiać ptaszek przy polu, którego nikt nie zmierzył.

    Zmierzone 19.09.2026 na 34 wysłanych sztukach tego modelu z 43 dni:

        L i przebieg <= 2000 km, dosłownie           0   reguła martwa
        L albo nieznana, przebieg <= 2000 km         9
        L albo nieznana, przebieg też nieznany      15   <- ta, 0,35 dziennie

    Z tych 15 tylko 2 mają OBA pola odczytane, a 3 nie mają ANI JEDNEGO.
    """
    cfg = dict(OZNACZ_DOMYSLNE)
    cfg.update(wpis.get("oznacz") or {})
    powody = []

    litera = litera_ramy(oferta)
    if litera is None:
        if not cfg["rama_nieznana_liczy_sie"]:
            return False, []
        surowa = rama_oferty(oferta)
        powody.append(f"rama: sprzedawca nie podał ({surowa})" if surowa
                      else "rama: sprzedawca nie podał")
    elif litera in cfg["rama"]:
        powody.append(f"rama {litera}")
    else:
        return False, []

    km = oferta.get("mileage_num")
    if km is None:
        if not cfg["przebieg_nieznany_liczy_sie"]:
            return False, []
        powody.append("przebieg: sprzedawca nie podał")
    elif km <= cfg["przebieg_max"]:
        powody.append(f"przebieg {km:,} km".replace(",", "\u00a0")
                      + f" (próg {cfg['przebieg_max']:,})".replace(",", "\u00a0"))
    else:
        return False, []

    return True, powody


def wiadomosc_oznaczenia(wpis, tytul, naglowek, url, powody) -> str:
    """Druga wiadomość o obserwowanym rowerze - sama treść, bez wysyłki.

    Wyjęta z pętli do funkcji czystej z tego samego powodu co `licz_kanal_zle`
    i `ile_kluczowych` 01.09.2026: inaczej jedynym sposobem sprawdzenia, co
    właściciel naprawdę zobaczy, jest grep po źródle.

    Krótka z rozmysłu. Pełna analiza - rynek, zysk, negocjacja, opis - poszła
    wiadomość wyżej i powtarzanie jej drugi raz zamieniłoby oznaczenie w ścianę
    tekstu. Tu ma być widać: który to model z listy, co konkretnie się zgadza
    i czego nie wiemy."""
    L = [f"⭐ <b>OBSERWOWANY: {html_mod.escape(wpis['nazwa'])}</b>",
         "<i>To ogłoszenie masz wyżej - powtarzam je, bo ta sztuka spełnia "
         "Twoje warunki.</i>",
         "",
         f"<b>{html_mod.escape(tytul)}</b>",
         naglowek,
         ""]
    # Czego NIE zmierzyliśmy, tego oznaczenie nie podaje jako faktu (reguła 6).
    # Ptaszek znaczy ODCZYTANE ze strony ogłoszenia, znak zapytania znaczy
    # "sprzedawca tego nie napisał". Niewiadome są tu wariantem DOMYŚLNYM,
    # nie brzegowym: 13 z 15 oznaczeń ma co najmniej jedno pole puste
    # (zmierzone 19.09.2026 na 34 wysłanych sztukach tego modelu).
    znane = 0
    for powod in powody:
        if "nie podał" in powod:
            pole = powod.split(":", 1)[0]
            L.append(f"❓ {html_mod.escape(powod)} - {NIEZNANE_PODPOWIEDZI[pole]}")
        else:
            znane += 1
            L.append(f"✅ {html_mod.escape(powod)}")
    if not znane:
        # Oznaczenie bez ANI JEDNEGO odczytanego pola nie mówi o tym rowerze
        # nic poza nazwą modelu, a gwiazdka sugeruje, że coś sprawdziliśmy.
        # Zmierzone 19.09.2026: 3 z 15 oznaczeń są właśnie takie. Wychodzą,
        # bo właściciel tak ustawił listę - ale mają to powiedzieć wprost,
        # zamiast udawać werdykt.
        L.append("")
        L.append("<i>Nic z tego nie jest potwierdzone - sprzedawca nie podał "
                 "ani rozmiaru, ani przebiegu. Wchodzi, bo tak masz ustawioną "
                 "listę.</i>")
    L += ["", url]
    return "\n".join(L)


def load_silniki(force=False):
    """Pary (marka, model) z pliku wiedzy, jako wzorce.

    Wymagamy MARKI I MODELU naraz, bo same nazwy modeli bywają zwykłymi
    słowami: "Patron", "Image", "Sinus" i "Wild" znaczą po niemiecku coś
    swojego, a "e-power" pada w opisach jako zwrot reklamowy.

    Pusta lista to CISZA W WIEDZY, nie stan naturalny (reguła 7). Filtr wraca
    wtedy do samego `MOTOR_BRANDS`, czyli do zachowania sprzed 02.09.2026 -
    bot działa dalej, tylko znowu odsiewa rowery, przy których sprzedawca nie
    napisał marki silnika."""
    global _silniki_cache
    if _silniki_cache is None or force:
        pary = []
        try:
            dane = json.loads(SILNIKI_FILE.read_text(encoding="utf-8"))
            for w in dane.get("bosch_z_definicji", []):
                if isinstance(w, dict) and w.get("marka") and w.get("model"):
                    pary.append((_wz_frazy(w["marka"]), _wz_frazy(w["model"])))
        except Exception as e:
            zglos_problem("silniki", f"nie wczytano {SILNIKI_FILE}: {e}")
        if not pary:
            zglos_problem("silniki", "lista rodzin Boschowych pusta - "
                                     "filtr silnika działa jak przed 02.09.2026")
        _silniki_cache = pary
    return _silniki_cache


def silnik_z_rodziny(tekst) -> bool:
    """Czy tekst (JUŻ małymi literami) nazywa rodzinę, która ma Boscha
    z definicji. Zmierzone 02.09.2026 na 51 841 tytułach z Kleinanzeigen
    i willhaben oraz 677 adresach OLX: 26 par marka+model, 1 758 razy ktoś
    napisał przy nich "Bosch", ani razu marki konkurencji."""
    return any(m.search(tekst) and mod.search(tekst) for m, mod in load_silniki())


# ...ale nazwany wprost RYWAL bije wiedzę o rodzinie - ta sama zasada co
# `_MOTOR_DO_WYMIANY` nad `_MOTOR_WYMIENIONY` i `sprzeczne_warianty` nad
# dowodem tożsamości: dowód RÓŻNICY jest mocniejszy niż domniemanie.
# Domniemanie z rodziny jest słabsze od napisu w ogłoszeniu, więc przegrywa
# z nim zawsze - także przy przeróbce, o której producent nic nie wie.
# Weto NIE dotyczy trafienia w MOTOR_BRANDS: tam nie zgadujemy, tylko czytamy,
# i ta ścieżka zostaje dokładnie taka, jaka była.
# Zmierzone 01.09.2026: na 5 043 tytułach z tych pięciu rodzin weto nie
# zapaliło się ANI RAZU, a na całym korpusie łapie 1 364 tytuły (Yamaha PW-X,
# Bafang, Fazua, Panasonic...) - czyli odsiewa to, co ma odsiewać.
_SILNIK_RYWAL = re.compile(
    r'yamaha|\bpw[\s-]?x\b|\bep[68]\b|\bep\d{3}\b|shimano\s*steps'
    r'|\bsteps\s*e\d{4}\b|\bbrose\b|bafang|fazua|\btq[\s-]?hpr\b'
    r'|panasonic|pinion\s*mgu|syncdrive')


def has_known_motor(title: str, description_text) -> bool:
    """Zwraca True jeśli tytuł lub opis zawiera markę silnika elektrycznego.
    description_text=None (błąd pobrania) → kredyt zaufania, nie odrzucamy."""
    if description_text is None:
        return True
    combined = (title + " " + description_text).lower()
    if any(brand in combined for brand in MOTOR_BRANDS):
        return True
    # Marki nikt nie napisał - pytamy rodziny modelu. Milczenie sprzedawcy
    # o silniku jest normą, a nie sygnałem, że silnik jest obcy.
    return silnik_z_rodziny(combined) and not _SILNIK_RYWAL.search(combined)


# Levo/Kenevo FSR (2016-2019) to silnik Brose Drive S napędzany PASKIEM
# zębatym — pokoleniowa wada, nie pech pojedynczej sztuki: pasek zużywa się
# pod momentem i pęka, a uszczelnienie łapie wodę. Wymiana jednostki po
# gwarancji kosztuje rzędu całego zysku z takiej transakcji, więc NIE ma ceny,
# przy której to się opłaca — dlatego odsiew jest bezwarunkowy, inaczej niż
# przy małej baterii, która przechodzi przy dużej przecenie.
# Decyzja właściciela 25.08.2026: "złom nam nie potrzebny".
# Warunek jest podwójny (FSR ORAZ marka), bo "FSR" to znak towarowy zawieszenia
# Specialized używany też w nazwach cudzych ram — sam skrót odsiewałby za dużo.
_FSR_WZ = re.compile(r'\bfsr\b')
_SPEC_WZ = re.compile(r'specialized|\blevo\b|kenevo')


# Wymieniona jednostka znosi odsiew: wada jest POKOLENIOWA, więc dotyczy
# silnika, nie ramy. Sztuka z 2017 z silnikiem wstawionym w 2023 ma pasek
# młodszy niż niejedno Levo Gen 3.
_MOTOR_WYMIENIONY = re.compile(
    r'\b(austausch|tausch)motor\b'
    r'|\bneue[rn]?\s+(motor|antrieb)\b'
    r'|\bmotor\s+(ist\s+)?neu\b'
    r'|\bmotor\s+(wurde\s+)?(auf\s+garantie\s+)?(neu\s+)?'
    r'(getauscht|ausgetauscht|ersetzt|erneuert)\b')
# ...ale "neuer Motor nötig" to ogłoszenie o WRAKU, nie o naprawie. Bez tego
# weta jedno słowo różnicy zamieniałoby najgorszy możliwy egzemplarz
# w rzekomo naprawiony. Dokładnie ta pułapka co "NIEAKTUALNE" w boilerplate OLX.
_MOTOR_DO_WYMIANY = re.compile(
    r'\b(motor|antrieb)\s+(ist\s+)?(defekt|kaputt|hin|hinüber|hinueber)\b'
    r'|\bneue[rn]?\s+(motor|antrieb)\s+'
    r'(nötig|noetig|erforderlich|fällig|faellig|benötigt|benoetigt|muss)'
    r'|\b(braucht|benötigt|benoetigt|bräuchte|braeuchte)\s+'
    r'(einen\s+)?neuen\s+(motor|antrieb)\b'
    r'|\bmotor\s+(muss|müsste|muesste)\b')


def is_stary_brose(title: str, description_text=None) -> bool:
    """Czy to Levo/Kenevo FSR — generacja na pasku Brose Drive S.

    Odrzucamy WYŁĄCZNIE na trafienie pozytywne. Brak opisu (błąd pobrania)
    nie jest przesłanką do odrzucenia — decyduje wtedy sam tytuł, który przy
    tej generacji i tak prawie zawsze zawiera "FSR".

    Wyjątek: udokumentowana wymiana silnika. Weto na "silnik DO wymiany"
    jest ważniejsze od wyjątku i wygrywa z nim."""
    combined = (title + " " + (description_text or "")).lower()
    if not (_FSR_WZ.search(combined) and _SPEC_WZ.search(combined)):
        return False
    if _MOTOR_WYMIENIONY.search(combined) and not _MOTOR_DO_WYMIANY.search(combined):
        return False
    return True


def is_too_worn(mileage_num) -> bool:
    if mileage_num is None:
        return False
    return mileage_num > MAX_MILEAGE


SMALL_BATTERY_WH = 500  # poniżej = słaba odsprzedaż w PL (kupujący patrzą na zasięg)


def battery_wh(title, desc):
    """Największa pojemność baterii w Wh z tytułu+opisu (zakres 200-1000)."""
    text = f"{title} {desc or ''}"
    vals = [int(m) for m in re.findall(r'(\d{3,4})\s*wh\b', text, re.I)]
    vals = [v for v in vals if 200 <= v <= 1000]
    return max(vals) if vals else None


def is_small_battery(title, desc) -> bool:
    """Model 'SL' (Super Light) lub bateria <500 Wh — lekki rower, ale w PL
    trudny do odsprzedaży (mały zasięg = mała pula kupujących)."""
    t = title.lower()
    if re.search(r'levo sl|kenevo sl|\bsl comp\b|\bsl expert\b', t):
        return True
    wh = battery_wh(title, desc)
    return wh is not None and wh < SMALL_BATTERY_WH


# Zdjecia galerii siedza w atrybucie data-imgsrc. NIE wolno brac wszystkich
# obrazkow ze strony: sprawdzone 23.08.2026 — na stronie Scotta Ransome bylo
# 13 zdjec, z czego 3 jego, a 10 CUDZYCH ROWEROW z sekcji "moze cie
# zainteresuje" (Bulls, Canyon, Orbea...). Wyslanie ich jako zdjec tego
# ogloszenia oznaczaloby dojazd po rower, ktorego na fotce w ogole nie ma.
_ZDJ = r'https://img\.kleinanzeigen\.de/api/v1/prod-ads/images/[0-9a-f]{2}/[0-9a-f-]{36}'
GALERIA_WZ = re.compile(r'data-imgsrc="(' + _ZDJ + r')')
# Drugi układ strony, spotkany 23.08 na żywo: bez data-imgsrc, za to z dużym
# zdjęciem jako tłem. Oba wzorce biorą TYLKO zdjęcia z galerii — na dole strony
# siedzą jeszcze "podobne ogłoszenia" (imagebox srpimagebox) z cudzymi rowerami,
# a te mają zwykłe src= i nie mogą tu wpaść.
GALERIA_TLO = re.compile(r"galleryimage-large--cover[^>]*?background-image:\s*url\('(" + _ZDJ + r")")


# Trzeci układ (zmierzony 23.08 — tego dnia PRZEWAŻAŁ): zero data-imgsrc,
# zero galleryimage; zdjęcia siedzą wyłącznie w blokach JSON-LD "ImageObject".
# Bez tego czytnika album był pusty, a bot wysyłał samą miniaturę z listy.
GALERIA_JSON = re.compile(r'"contentUrl":\s*"(' + _ZDJ + r')')


def _zdjecia_z_json(html: str) -> list:
    """Zdjęcia z bloków JSON-LD. Bierzemy tylko te opisane jako należące do
    strony (`representativeOfPage`) — inne ImageObject na stronie opisują
    cudze ogłoszenia."""
    adresy = []
    for kawalek in html.split('"ImageObject"')[1:]:
        kawalek = kawalek[:4000]
        if "representativeOfPage" not in kawalek:
            continue
        m = GALERIA_JSON.search(kawalek)
        if m:
            adresy.append(m.group(1))
    return adresy


def galeria_ze_strony(html: str) -> list:
    """Adresy zdjęć NALEŻĄCYCH do tego ogłoszenia, w kolejności z galerii.

    Kleinanzeigen serwuje stronę ogłoszenia w kilku układach — zmierzone
    23.08, wariant zmienia się z sesji na sesję. Każdy trzyma zdjęcia gdzie
    indziej, więc czytamy po kolei, aż któryś odpowie."""
    html = html or ""
    # "Podobne ogłoszenia" na dole strony to CUDZE rowery (10 z 13 zdjęć na
    # stronie potrafi należeć do kogoś innego) — ucinamy stronę przed nimi.
    ciach = html.find("srpimagebox")
    gora = html[:ciach] if ciach > 0 else html
    adresy = (list(GALERIA_WZ.findall(gora))
              or list(GALERIA_TLO.findall(gora))
              or _zdjecia_z_json(gora))
    return [f"{u}?rule=$_59.AUTO" for u in dict.fromkeys(adresy)]


# Kleinanzeigen podaje stronę ogłoszenia w dwóch układach — stary ma
# id="viewad-price", nowy id="vip-ad-price". Zmierzone 23.08: na nowym
# układzie odczyt ceny ze strony milczał. To ten sam rodzaj cichej awarii,
# co brak przebiegu, więc oba układy muszą być obsłużone jawnie.
CENA_ZE_STRONY = re.compile(r'id="(?:viewad-price|vip-ad-price)"[^>]*>\s*([^<]+)')
STRONA_OGLOSZENIA = re.compile(r'id="(?:viewad-price|vip-ad-price)"|'
                               r'id="viewad-description-text"')


# Rezerwacja. Znaleziona 23.08 po uwadze użytkownika ("przecież pierwsze
# zdjęcie jest reserviert"): sprzedawca stempluje RESERVIERT na zdjęciu, czego
# bez AI nie odczytamy, ale Kleinanzeigen dokłada do tego własną plakietkę przy
# galerii. Pułapka: plakietka jest TYLKO w jednym z układów strony (tym z
# galerią w JSON-LD). W drugim nie ma o rezerwacji ani słowa — i wtedy uczciwa
# odpowiedź brzmi "nie wiem", a nie "wolne".
REZERWACJA_WZ = re.compile(r'badge-unavailable|icon-reserved-flag', re.I)
# Część sprzedawców pisze to wprost w tytule albo opisie — to darmowy dodatek
# do plakietki. "nicht reserviert" i "keine Reservierung" znaczą coś odwrotnego.
REZERWACJA_TEKST = re.compile(r'(?<!nicht )(?<!keine )reserviert', re.I)


def czy_zarezerwowane(html: str, title: str = "", desc: str = ""):
    """True / False / None. None = ten układ strony nic o tym nie mówi."""
    if REZERWACJA_TEKST.search(title or "") or REZERWACJA_TEKST.search(desc or ""):
        return True
    if REZERWACJA_WZ.search(html or ""):
        return True
    # Plakietka jedzie razem z galerią w JSON-LD; brak plakietki jest dowodem
    # na "wolne" tylko w tym układzie, bo tylko on ją w ogóle pokazuje.
    if '"representativeOfPage"' in (html or ""):
        return False
    return None


# ŁAMANIE LINII SPRZEDAWCY TO GRANICA POLA, NIE SPACJA.
# Zmierzone 15.09.2026 na żywym ogłoszeniu: sprzedawca napisał
# "Rahmengröße L<br />29 Zoll<br />", czyli rozdzielił rozmiar ramy od rozmiaru
# koła tak jasno, jak się da. `re.sub('<[^>]+>', ' ')` zamieniało oba znaczniki
# na spacje i robiło z tego jedno zdanie "rahmengröße l 29 zoll" - a wtedy
# strażnik "to koło, nie rama" w `rozmiar_ramy` słusznie odrzucał WŁASNĄ
# sklejkę razem z literą L. Bot gubił rozmiar, który stał w ogłoszeniu wprost.
#
# Ta funkcja oddaje DRUGI widok tego samego opisu - z granicami pól - i jedzie
# WYŁĄCZNIE do czytnika rozmiaru. `desc_text` zostaje bajt w bajt taki jak był,
# bo czytają go przebieg, bateria, zużycie i targ, a każdy z nich decyduje
# o tym, czy oferta w ogóle pójdzie. Jeden widok dla wszystkich znaczyłby, że
# poprawka rozmiaru przestawia wysyłkę - a na to nie ma zgody.
#
# Znak "|" nie jest przypadkowy: klasa ogona w `_RAMA_ETYKIETA` już go wyklucza
# (`[^,;.|]`), więc granica pola działa tą samą drogą co przecinek i kropka.
_POLA_HTML = re.compile(r'<\s*(?:br\s*/?|/p|/div|/li|/tr|/td)\s*>', re.I)
_POLA_TEKST = re.compile(r'\s[*•·]\s|[\r\n]+')


def opis_z_polami(desc_html: str) -> str:
    """Opis z ZACHOWANYMI granicami pól. Tylko dla czytnika rozmiaru ramy.

    Poza znacznikami HTML granicę stawia też wypunktowanie - sprzedawcy piszą
    "* Rahmenhöhe: 44 cm * 29-Zoll-Laufräder *" i gwiazdka znaczy tam dokładnie
    to samo co nowa linia."""
    if not desc_html:
        return ""
    z_polami = _POLA_HTML.sub(" | ", desc_html)
    z_polami = re.sub(r'<[^>]+>', ' ', z_polami)
    return _POLA_TEKST.sub(" | ", z_polami)


def fetch_listing_details(url: str, title: str = "", proba: int = 1) -> tuple:
    """Pobiera stronę ogłoszenia. Zwraca (przebieg, opis, cena|None, zdjęcia, stan, meta).

    `stan` to jedyna uczciwa odpowiedź na pytanie "czy my to w ogóle
    przeczytaliśmy":
        "ok"       — strona wczytana, opis mamy, brak przebiegu = fakt
        "usuniete" — ogłoszenie zdjęte (404/410), nie ma czego czytać
        "blad"     — NIE UDAŁO SIĘ pobrać; niczego o tym rowerze nie wiemy

    Rozróżnienie jest sednem. Sprawdzone 23.08: z 18 powiadomień 7 miało
    zapisane "brak danych", choć przebieg stoi w opisie (m.in. Cannondale
    Moterra z 10 328 km). Strona pobiera się dziś bez problemu — tamte odczyty
    padły na chwilowej awarii, a bot zapisał to jako fakt "sprzedawca nie
    podał" i puścił złom dalej, bo brak przebiegu przepuszcza filtr zużycia.
    Przy "blad" wolno tylko jedno: spróbować jeszcze raz (patrz do_odczytania)."""
    try:
        r = scraper.get(url, timeout=15)
        if r.status_code in (404, 410):
            return "brak danych", None, None, [], "usuniete", {}
        r.raise_for_status()
        r.encoding = "utf-8"
        html = r.text

        # Cena ze strony ogłoszenia (ratunek gdy lista jej nie miała)
        price_m = CENA_ZE_STRONY.search(html)
        detail_price = " ".join(price_m.group(1).split()) if price_m else None

        # Wyciągnij opis
        desc_match = re.search(
            r'id="viewad-description-text"[^>]*>(.*?)</p>',
            html, re.DOTALL | re.IGNORECASE
        )
        if not desc_match:
            desc_match = re.search(
                r'class="[^"]*ad-description[^"]*"[^>]*>(.*?)</(?:div|section)>',
                html, re.DOTALL | re.IGNORECASE
            )
        # Pusty opis wolno uznać za fakt tylko wtedy, gdy to NA PEWNO strona
        # ogłoszenia. Bez tego sprawdzenia zmiana układu HTML albo strona
        # przejściowa ("zbyt wiele żądań") wyglądałaby jak rower bez opisu
        # i cicho przepadła — a to dokładnie ten błąd, który naprawiamy.
        if not desc_match and not STRONA_OGLOSZENIA.search(html):
            # Zdjęte ogłoszenie NIE oddaje 404 — sprawdzone 23.08 na żywym
            # przykładzie: HTTP 200, ten sam adres, a w środku strona
            # kategorii z komunikatem "nicht mehr verfügbar". Bez tego
            # rozpoznania bot dobijałby się do nieistniejącego roweru osiem
            # razy i na koniec zawracał głowę alarmem o rowerze, którego nie ma.
            if re.search(r"nicht mehr verf(?:ü|ue)gbar|wurde gel(?:ö|oe)scht",
                         html, re.IGNORECASE):
                return "brak danych", None, None, [], "usuniete", {}
            raise ValueError("strona bez opisu i bez ceny — to nie ogłoszenie")

        desc_html = desc_match.group(1) if desc_match else ""
        desc_text = re.sub(r'<[^>]+>', ' ', desc_html)
        zdjecia = galeria_ze_strony(html)
        meta = {"zarezerwowane": czy_zarezerwowane(html, title, desc_text),
                "opis_pola": opis_z_polami(desc_html)}

        return (_przebieg_z_opisu(title, desc_text), desc_text, detail_price,
                zdjecia, "ok", meta)

    except Exception as e:
        log.error(f"Listing fetch error ({proba}/{ODCZYT_PROBY}): {e}")
        if proba < ODCZYT_PROBY:           # ponowna próba, po dłuższym oddechu
            time.sleep(2 * proba)
            return fetch_listing_details(url, title, proba + 1)
    # None = fetch się nie udał (odróżnialne od pustego opisu)
    return "brak danych", None, None, [], "blad", {}


def _format_km(km: int) -> str:
    return f"{km:,} km".replace(",", ".")


def _przebieg_z_opisu(title: str, desc_text) -> str:
    """Przebieg z tytułu i opisu. JEDEN czytnik dla Kleinanzeigen i willhaben.

    Wspólny z rozmysłem: opis jest po niemiecku po obu stronach granicy, a dwa
    równoległe zestawy wzorców znaczą, że poprawka trafia tylko do jednego —
    to jest dokładnie ta wpadka, dla której powstał wspólny `olx.py` (wzorzec
    łapał 38% ofert i przez tygodnie nikt tego nie widział)."""
    llm = llm_extract_mileage(title, desc_text)
    if llm is not None:
        _, km = llm
        if km:
            return _format_km(km)
        # Haiku mówi "nie ma przebiegu" — to NIE JEST dowód, że go nie ma.
        # Cannondale Moterra (23.08) miał w opisie "10.328 km", model tego
        # nie zwrócił, a bot zapisał "brak danych" i puścił dalej rower po
        # 10 tys. km, bo is_too_worn(None) przepuszcza. Odpowiedź "nie wiem"
        # nie może wyłączać drugiego czytnika — regex dostaje swoją szansę.
        mileage = _extract_mileage(title, desc_text)
        if mileage != "brak danych":
            log.info(f"Przebieg pominięty przez model, znaleziony wzorcem: {mileage}")
        return mileage
    return _extract_mileage(title, desc_text)


def llm_extract_mileage(title: str, desc_text: str):
    """Czyta przebieg z tytułu+opisu przez Claude Haiku.
    Zwraca ("ok", km|None) przy powodzeniu, None przy błędzie (→ fallback regex)."""
    if not ANTHROPIC_API_KEY:
        return None
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5",
                "max_tokens": 100,
                "system": (
                    "Czytasz niemieckie ogłoszenia sprzedaży rowerów elektrycznych. "
                    "Wyciągnij CAŁKOWITY PRZEBIEG roweru w km (Laufleistung/Kilometerstand/gefahren). "
                    "NIE myl przebiegu z zasięgiem akumulatora (Reichweite) ani pojemnością (Wh). "
                    "Jeśli ogłoszenie dotyczy kilku rowerów, podaj przebieg najmniejszy. "
                    "Jeśli przebieg nie jest podany, zwróć null."
                ),
                "output_config": {
                    "format": {
                        "type": "json_schema",
                        "schema": {
                            "type": "object",
                            "properties": {
                                "mileage_km": {"type": ["integer", "null"]},
                            },
                            "required": ["mileage_km"],
                            "additionalProperties": False,
                        },
                    }
                },
                "messages": [
                    {"role": "user", "content": f"Tytuł: {title}\n\nOpis: {desc_text[:3000]}"}
                ],
            },
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        text = next(b["text"] for b in data["content"] if b["type"] == "text")
        km = json.loads(text).get("mileage_km")
        if km is None:
            return ("ok", None)
        if isinstance(km, int) and 0 < km <= 50000:
            return ("ok", km)
        return ("ok", None)
    except Exception as e:
        log.error(f"LLM mileage error: {e}")
    return None


# JEDNOSTKA PRZEBIEGU: skrót "km" ALBO słowo "Kilometer(n)".
#
# Sprzedawcy piszą przebieg zdaniem, nie tabelką: "Ich bin erst ca 1500
# Kilometer damit gefahren" (ogłoszenie 3512362512, właściciel zapytał
# 17.09.2026, czemu bot ma tam "brak danych"). Stary wzorzec znał wyłącznie
# skrót, więc taki rower szedł jako przebieg nieznany: `is_too_worn(None)`
# go przepuszcza, a na kanale najlepszych traci sygnał niskiego przebiegu.
# Zmierzone na 106 952 tytułach z dziennika rynku: 67 ogłoszeń zyskuje
# przebieg, z czego 12 stoi ponad `MAX_MILEAGE` i dopiero teraz odpadnie.
# W OPISACH tego nie policzymy, bo bot opisów nie przechowuje - a to właśnie
# tam ludzie piszą zdaniami, więc zysk jest tam większy niż te 67.
#
# "Kilometerstand" NIE wpadnie tu przypadkiem: po "kilometer" stoi "stand",
# więc granica słowa \b nie zachodzi i wzorzec się nie dopasuje. Ta sama
# pułapka co "NIEAKTUALNE" w boilerplate OLX (reguła 8).
_KM_JEDNOSTKA = r'(?:km|kilometern?)\b'

# Sama liczba MUSI kończyć się cyfrą. Klasa `[\d.,]*` zjadała przecinek
# rozdzielający, więc "Gr. 47, Kilometer: 570" czytało się jako 47 km, czyli
# rozmiar ramy wchodził jako przebieg roweru (zmierzone 17.09.2026 na tytule
# z dziennika rynku). Zaniżony przebieg jest groźniejszy niż jego brak:
# rower wygląda wtedy na prawie nowy.
_KM_LICZBA = r'(\d(?:[\d.,]*\d)?)'


def _km_liczba(raw: str):
    """Kilometry z niemieckiego zapisu liczby. Kropka to tysiące ("1.519 km"),
    przecinek to UŁAMEK ("495,6 km"), nie tysiące.

    Stary kod kasował oba znaki, więc z 495,6 km robiło się 4 956 km i rower
    po pół tysiąca kilometrów wypadał jako zajeżdżony (`MAX_MILEAGE`).
    Zmierzone 17.09.2026 na 106 952 tytułach z `market.jsonl`: 54 przebiegi
    policzone dziesięciokrotnie za wysoko, w tym 17 wypchniętych ponad próg
    zajeżdżenia (Giant Fathom E+ z 423,9 km liczył się jako 4 239 km).
    Przy okazji znikają dwa odczyty fałszywie NISKIE ("Gr. 47, Kilometer",
    "07/22,km-Stand"), a te są groźniejsze: robiły z roweru prawie nowy."""
    raw = raw.replace(".", "")
    if "," in raw:
        calosc, _, ulamek = raw.partition(",")
        # 1-2 cyfry po przecinku to ułamek, więcej to zapis tysięcy po angielsku
        raw = calosc if 1 <= len(ulamek) <= 2 else calosc + ulamek
    return int(raw) if raw.isdigit() else None


def _extract_mileage(title: str, desc_text: str) -> str:
    # 1. Przebieg zadeklarowany w TYTULE — najbardziej wiarygodne źródło
    #    ("Nur 800km", "Erst 516 km", "2337km", "Nur 800 Kilometer")
    t = re.search(
        r'(nur|erst)?\s*' + _KM_LICZBA + r'\s*' + _KM_JEDNOSTKA,
        title, re.IGNORECASE
    )
    if t:
        before = title[max(0, t.start() - 25):t.start()].lower()
        # "nur/erst" przed liczbą = na pewno przebieg; bez tego prefiksu
        # odrzucamy gdy w pobliżu Reichweite/Akku (to zasięg, nie przebieg)
        explicit = bool(t.group(1))
        if explicit or not re.search(r'reichweite|bis\s*(?:zu)?$|akku', before):
            km = _km_liczba(t.group(2))
            if km is not None and 10 <= km <= 25000:
                return _format_km(km)

    # 2. Atrybut/deklaracja przebiegu w OPISIE — słowo kluczowe musi być
    #    BLISKO liczby (max 40 znaków), żeby nie łączyć odległych fragmentów
    if desc_text:
        attr = re.search(
            r'(?:Kilometerstand|Laufleistung|km[\s-]?Stand|Tachostand|km)\s*[:=]\s*'
            + _KM_LICZBA + r'\s*' + _KM_JEDNOSTKA + '|'
            r'(?:Kilometerstand|Laufleistung|km[\s-]?Stand|Tachostand)[^\d]{0,40}'
            + _KM_LICZBA + r'\s*' + _KM_JEDNOSTKA,
            desc_text, re.IGNORECASE
        )
        if attr:
            km = _km_liczba(attr.group(1) or attr.group(2))
            if km is not None and 10 <= km <= 25000:
                return _format_km(km)

        # 3. System punktowy — TYLKO w tekście opisu, nigdy w pełnym HTML
        RANGE_CONTEXT = [
            "reichweite", "wh", "akku", "batterie", "kapazität",
            "ladung", "range", "motorleistung",
            # zaobserwowane 23.08 na LEMMO ONE: "kann eine Fahrt von 100 km
            # unterstützen" to zasięg opisany zdaniem, a nie słowem kluczowym
            "fahrt von", "unterstütz", "je ladung", "pro ladung",
            "auf einer", "bis zu", "schafft", "weit kommen",
            # "190km auf Eco und 73 auf Turbo" to zasieg w trybie wspomagania,
            # a nie przebieg — ten sam opis podawal prawdziwe "Km: 5250km"
            "auf eco", "auf turbo", "im eco", "im turbo", "modus", "tour-modus",
        ]

        # Zwroty, ktorymi Niemcy podaja przebieg BEZ slowa "gefahren".
        # Bez nich gubilismy prawdziwe odczyty: "hat 1100km", "mit 113 km",
        # "nur 188km auf dem Buckel" — wszystkie z zywych ogloszen 23.08.
        PRZED_LICZBA = r"(?:hat|mit|nur|erst|knapp|gerade)\s*$"
        PO_LICZBIE = r"^\s*(?:auf dem buckel|auf der uhr|drauf|runter|gelaufen|gefahren)"

        candidates = []
        for m in re.finditer(_KM_LICZBA + r'\s*' + _KM_JEDNOSTKA,
                             desc_text, re.IGNORECASE):
            km = _km_liczba(m.group(1))
            if km is None or not (50 <= km <= 25000):
                continue

            # szerokie okno dla słów przebiegu, WĄSKIE dla kary zasięgu —
            # "Reichweite" stoi zawsze tuż przy liczbie, a "Akku" z listy
            # komponentów obok nie może kasować prawdziwego przebiegu
            ctx = desc_text[max(0, m.start() - 120):m.end() + 120].lower()
            ctx_near = desc_text[max(0, m.start() - 40):m.end() + 40].lower()

            score = 5

            mileage_ctx = bool(re.search(
                r'gefahren|gelaufen|laufleistung|kilometerstand|tachostand|tacho|km.?stand|insgesamt|bisher|gesamt',
                ctx
            ))
            # Zwrot tuż PRZED liczbą albo tuż PO niej — dowód na przebieg, ale
            # SŁABSZY niż słowo zasięgu obok. "Reichweite ca. 120 km" ma i jedno,
            # i drugie, więc pierwszeństwo musi mieć zasięg, inaczej wracamy do
            # mylenia zasięgu z przebiegiem.
            zasieg_obok = any(kw in ctx_near for kw in RANGE_CONTEXT)
            tuz_przed = desc_text[max(0, m.start() - 14):m.start()].lower()
            tuz_po = desc_text[m.end():m.end() + 22].lower()
            if not mileage_ctx and not zasieg_obok and (
                    re.search(PRZED_LICZBA, tuz_przed) or re.search(PO_LICZBIE, tuz_po)):
                mileage_ctx = True
            if mileage_ctx:
                score += 15
            else:
                for kw in RANGE_CONTEXT:
                    if kw in ctx_near:
                        score -= 20
                        break
                else:
                    # Liczba bez ŻADNEGO kontekstu przebiegu przechodziła
                    # domyślnie i tak "100 km" z opisu zasięgu robiło z roweru
                    # prawie nowy. Bez dowodu przyjmujemy ją tylko wtedy, gdy
                    # jest za duża na zasięg jednego ładowania (>1500 km).
                    if km < 1500:
                        score -= 20

            if km in (400, 500, 600, 625, 630, 700, 750, 800, 1000):
                score -= 10

            candidates.append((score, km))

        if candidates:
            best = max(candidates, key=lambda x: x[0])
            if best[0] > 0:
                return _format_km(best[1])

    # 4. Brak opisu / brak liczb → uczciwe "brak danych", NIE zgadujemy z HTML
    return "brak danych"


# --- Samonaprawiający parser: pule wzorców w kolejności od najlepszego. ---
# Gdy Kleinanzeigen zmieni layout i pierwszy wzorzec przestanie łapać,
# kolejny automatycznie przejmuje robotę (a monitor skuteczności alarmuje).
TITLE_PATTERNS = [
    r'href="(/s-anzeige/[^"]+)">([^<\n]+)</a>',
    r'href="(/s-anzeige/[^"]+)"[^>]*class="[^"]*ellipsis[^"]*"[^>]*>([^<]+)',
    r'href="(/s-anzeige/[^"]+)"[^>]*>\s*([^<\n]{5,})',
]
PRICE_PATTERNS = [
    r'"adlist--item--price">([^<]+)<',
    r'class="aditem-main--middle--price-shipping--price">\s*([^\n<]+)',
    r'>(\d[\d.]*\s*€(?:\s*VB)?)<',
]
PARSE_HEALTH_MIN_RATE = 0.5    # poniżej = prawdopodobna zmiana layoutu
PARSE_HEALTH_MIN_BLOCKS = 10   # nie alarmuj przy garstce ofert (naturalne wahania)
REPLAY_DIR = os.environ.get("DEALHAWK_REPLAY_DIR")  # tryb odtwarzania (czarna skrzynka)


def _match_pool(patterns, block):
    """Próbuje wzorce po kolei; zwraca (match, indeks) pierwszego trafienia."""
    for i, p in enumerate(patterns):
        m = re.search(p, block)
        if m:
            return m, i
    return None, None


def fetch_listings(search: dict):
    """Parsuje listę per-blok ogłoszenia. Zwraca (lista_ofert, statystyki_zdrowia)."""
    results = []
    seen_ids = set()
    stats = {"name": search["name"], "blocks": 0, "title_hits": 0, "price_hits": 0,
             "time_hits": 0, "html": None, "status": None}
    try:
        if REPLAY_DIR:  # odtwarzanie zapisanego HTML zamiast sieci
            fp = Path(REPLAY_DIR) / (re.sub(r'[^\w]+', "_", search["name"]) + ".html")
            html = fp.read_text(encoding="utf-8") if fp.exists() else ""
            stats["status"] = 200
        else:
            # CIASTKA CZYSZCZONE PRZED KAŻDYM ŻĄDANIEM. Zmierzone 22.08.2026:
            # pierwsze żądanie w sesji dostaje pełną stronę (27 kart z datami),
            # a każde następne — wariant BEZ bloku z datą wystawienia.
            # Bot robił 23 żądania w jednej sesji, więc 22 z nich czytały
            # okrojoną wersję strony. Z czyszczeniem: 5/5 pełnych odpowiedzi.
            scraper.cookies.clear()
            r = scraper.get(search["url"], timeout=15)
            stats["status"] = r.status_code   # do diagnozy: awaria serwisu vs zmiana HTML
            r.raise_for_status()
            r.encoding = "utf-8"  # bez tego wariant odpowiedzi bez charset psuje umlauty
            html = r.text

        blocks = re.split(r'(?=data-adid=")', html)
        for block in blocks:
            id_m = re.match(r'data-adid="(\d+)"', block)
            if not id_m:
                continue
            ad_id = id_m.group(1)
            if ad_id in seen_ids:
                continue
            seen_ids.add(ad_id)
            stats["blocks"] += 1

            tm, _ = _match_pool(TITLE_PATTERNS, block)
            if tm:
                stats["title_hits"] += 1
                href, title = tm.group(1), tm.group(2).strip()
            else:
                href, title = f"/s-anzeige/{ad_id}", "Brak tytułu"

            pm, _ = _match_pool(PRICE_PATTERNS, block)
            if pm:
                stats["price_hits"] += 1
                price_str = pm.group(1).strip()
            else:
                price_str = "brak ceny"

            # lokalizacja (PLZ + miasto) — do ekonomii transportu / geografii
            # Po kodzie pocztowym musi stać NAZWA (wielka litera), nie cyfry.
            # Bez tego wzorzec łapał współrzędne ze ścieżki SVG i do dziennika
            # trafiało "09163 10.1363 5.62761 12.0003" — 34% wpisów market.jsonl.
            lm = re.search(r'\b(\d{5})\s+([A-ZÄÖÜ][^<\n\d]{1,38})', block)
            loc = None
            if lm:
                loc = lm.group(1) + " " + re.sub(r'\s+', ' ', lm.group(2)).strip()

            # czas wystawienia — jest w karcie, tylko brakuje go płatnym
            # "Top-Anzeigen" na górze listy (stąd tolerancja na None)
            am, _ = _match_pool(AD_TIME_PATTERNS, block)
            posted = parse_ad_time(am.group(1)) if am else None
            if posted:
                stats["time_hits"] += 1

            # Miniatura roweru — siedzi w tej samej karcie. Wymuszamy wariant
            # 960x720 (~160 kB): Telegram pokazuje go DUŻO większy jako zdjęcie
            # niż jako podgląd linka, a to jest pierwsza rzecz, którą widzisz.
            im = re.search(r'https://img\.kleinanzeigen\.de/api/v1/prod-ads/images/[^"?\s\\]+',
                           block)
            foto = f"{im.group(0)}?rule=$_59.AUTO" if im else None

            results.append({
                "id": ad_id,
                "title": title,
                "price": price_str,
                "price_num": parse_price(price_str),
                "loc": loc,
                "foto": foto,
                "posted": posted,
                "age_min": ad_age_minutes(posted),
                "url": f"https://www.kleinanzeigen.de{href}",
            })

        # ZACHOWAJ HTML, GDY STRONA WYGLĄDA NA ZEPSUTĄ — to jest czarna skrzynka.
        # Warunki są TRZY, nie jeden, i każdy odpowiada innej awarii:
        #   1. zły odsetek tytułów/cen — dryf parsera, serwis przebudował HTML,
        #   2. kafelki bez ANI JEDNEJ daty — podstawiona lista,
        #   3. zero kafelków — pusta odpowiedź.
        # Do 01.09.2026 był tylko pierwszy i dlatego przez sześć godzin ciszy
        # nie zachował się ŻADEN dowód: podstawiona lista ma tytuły i ceny
        # w 100% (rate = 1,0), więc warunek nie zachodził, `stats["html"]`
        # zostawało None, a zapis do blackbox/ w pętli kanałów milczał, bo
        # nie miał czego zapisać. Sama czujka działała — brakowało próbki.
        zly_odsetek = (stats["blocks"] >= PARSE_HEALTH_MIN_BLOCKS
                       and min(stats["title_hits"], stats["price_hits"])
                       / stats["blocks"] < PARSE_HEALTH_MIN_RATE)
        if zly_odsetek or strona_zepsuta(stats) or not stats["blocks"]:
            stats["html"] = html

    except Exception as e:
        log.error(f"Scrape error [{search['name']}]: {e}")
    return results, stats


# === KANAŁ KATEGORII =======================================================
# Zapytania kluczowe ("e-mtb fully") to loteria: Kleinanzeigen dopasowuje je
# rozmyto, także po opisie, i ustawia wyniki po TRAFNOŚCI. Ogłoszenie może
# wejść do takiego zbioru wiele godzin po wystawieniu — tak zginął Scott
# Ransome 22.08 (wystawiony 00:41, zauważony 17:31).
#
# Kanał kategorii jest inny: "Typ: Elektrofahrräder" to pole z formularza
# sprzedawcy, nie zgadywanka po tytule, a lista jest posortowana PO DACIE.
# Żadnych filtrów w URL-u — cena i reszta kryteriów sprawdzane w kodzie,
# tak samo jak w bocie samochodowym. Jedna strona ≈ 10 minut ogłoszeń.
#
# DWIE PÓŁKI, nie jedna. Rubrykę „Typ" wybiera sprzedawca i myli się regularnie:
# Specialized Levo FSR wystawiony 22.08 o 21:41 miał zaznaczone „Mountainbikes",
# więc na półce e-bike'ów go nie było i złapało go dopiero pytanie po nazwie —
# 34 minuty zamiast 2. Obie rubryki, w które trafiają e-MTB, są teraz pod
# obserwacją. Koszt: jedno żądanie na skan więcej.
FEED_BAZA = "https://www.kleinanzeigen.de/s-fahrraeder/{strona}c217+fahrraeder.type_s:{typ}"
KANALY = [
    {"typ": "ebike", "nazwa": "kanał e-bike"},
    # `szum`: półka w większości bez silnika, obserwowana tylko po to, żeby
    # wyłapać e-MTB źle otagowane przez sprzedawcę. Do dziennika rynku idą
    # z niej WYŁĄCZNIE elektryki — patrz `log_market` w pętli głównej.
    {"typ": "mountainbike", "nazwa": "kanał MTB", "szum": True},
]

# --- WSZYSTKIE PÓŁKI, OBIE GIEŁDY ------------------------------------------
# Jedna lista dla pętli głównej. Każda półka wie, czym się pobiera, więc
# `main` nie musi wiedzieć, z którego serwisu jest — a nowa giełda to jeden
# wpis tutaj, nie rozgałęzienie w pętli.
#
# Willhaben.at (Austria) dołożone 25.08.2026. Zmierzone tego dnia na oknie
# 200 ogłoszeń (~4,7 h): półka e-bike daje 5 kandydatów po komplecie filtrów
# (cena 800-3000 €, fully, elektryk, marka premium), czyli ~26 na dobę —
# wszystkie od sprzedawców prywatnych. Półka MTB dała w tym oknie zero.
# Ta sama waluta co Kleinanzeigen, więc cała ekonomia liczy się bez zmian;
# TRANSPORT_PLN = 300 to jednak stała ustawiona pod Niemcy i pod austriackie
# rowery NIE była weryfikowana — Wiedeń jest bliżej niż Nadrenia, Vorarlberg
# znacznie dalej, a bot nie ma z czego tego policzyć. Do korekty ręcznej.
POLKI = (
    [dict(k, serwis="Kleinanzeigen",
          pobierz=lambda od, k=k: fetch_feed(od, k["typ"], k["nazwa"]))
     for k in KANALY]
    + [dict(w, serwis="willhaben",
            pobierz=lambda od, w=w: willhaben.fetch_feed(od, w["kat"], w["nazwa"]))
       for w in willhaben.POLKI]
)
NAZWY_POLEK = {p["nazwa"] for p in POLKI}
POLKI_SZUM = {p["nazwa"] for p in POLKI if p.get("szum")}


def zrodlo_historii(listing):
    """Etykieta giełdy do dzienników. None = Kleinanzeigen (cała historia
    sprzed 25.08.2026 nie ma tego pola i nie ma być przepisywana)."""
    return "wh" if serwis_ogloszenia(listing) == "willhaben" else None


def uzupelnij_wiek(listings):
    """JEDEN ZEGAR NA OBIE GIEŁDY — wiek każdego ogłoszenia z półki.

    Liczony tutaj, a nie w module każdego serwisu, bo od tej liczby zależy
    trzy razy więcej, niż widać: kolejność wysyłki powiadomień (najświeższe
    idą pierwsze), alarm "BOT SIĘ SPÓŹNIŁ" i pole `op` w dzienniku rynku,
    czyli jedyna miara tego, ile bot naprawdę zwleka.

    Półka, która tego nie ustawi, cicho traci wszystkie trzy naraz. Willhaben
    tracił je od pierwszego dnia — nic nie krzyczało, bo powiadomienia
    przychodziły, tylko bez wieku i zawsze na końcu kolejki. Wyszło dopiero
    na biegu na sucho, 25.08.2026."""
    for l in listings:
        l["age_min"] = ad_age_minutes(l.get("posted"))
    return listings


def serwis_ogloszenia(listing) -> str:
    """Z której giełdy jest to ogłoszenie. Po adresie, nie po polu w słowniku —
    bo wpisy wracające z `seen.json` (zaległe odczyty) mają tylko adres."""
    return "willhaben" if willhaben.czy_nasze(listing.get("url") or "") else "Kleinanzeigen"


def region_ogloszenia(listing):
    """Region po ludzku. Kod pocztowy w Austrii ma CZTERY cyfry, w Niemczech
    pięć — jedna tablica dla obu czytałaby „5071 Siezenheim" jako Nadrenię."""
    if serwis_ogloszenia(listing) == "willhaben":
        return listing.get("region") or willhaben.region_z_plz(listing.get("loc"))
    return region_z_plz(listing.get("loc"))


def czytaj_ogloszenie(url: str, title: str = "") -> tuple:
    """Strona ogłoszenia z DOWOLNEJ giełdy. Zwraca to samo, co
    `fetch_listing_details`: (przebieg, opis, cena|None, zdjęcia, stan, meta).

    Przebieg wyciągamy TYM SAMYM czytnikiem dla obu serwisów — opis jest po
    niemiecku po obu stronach granicy, a dwa równoległe zestawy wzorców
    znaczyłyby, że poprawka trafia tylko do jednego (dokładnie ta wpadka,
    dla której powstał wspólny `olx.py`)."""
    if not willhaben.czy_nasze(url):
        return fetch_listing_details(url, title)
    opis, cena, zdjecia, stan, meta = willhaben.szczegoly(url)
    if stan != "ok":
        return "brak danych", None, None, [], stan, meta
    return _przebieg_z_opisu(title, opis), opis, cena, zdjecia, "ok", meta


def feed_url(typ: str, n: int = 1) -> str:
    return FEED_BAZA.format(strona="" if n == 1 else f"seite:{n}/", typ=typ)
FEED_MAX_STRON = 12        # ~2 h ogłoszeń — zapas na najdłuższą zaobserwowaną
# Znacznik starszy niż tyle minut = luki i tak nie domkniemy w FEED_MAX_STRON
# stronach. Nie ma wtedy sensu przechodzić ich wszystkich: to 24 żądania na
# skan, czyli DOKŁADNIE ta dawka, którą zmierzyliśmy jako receptę na
# stronę-śmieć. Bierzemy stronę 1 i przesuwamy znacznik — okno przepadło,
# ale bot wraca do pracy zamiast dobijać się w kółko.
FEED_LUKA_MAX_MIN = 100
                           # przerwę w harmonogramie GitHuba (55 min)
FEED_MARGINES_MIN = 3      # ile cofnąć się za znacznik, na styk zegarów
FEED_STATE_FILE = Path("feed_stan.json")


# Ponawianie uderza w ten sam adres, który właśnie jest dławiony — trzy próby
# potrafiły zaszkodzić bardziej niż pomóc. Jedna powtórka wystarcza na blip.
FEED_PROBY = 2

# --- OSZCZĘDZANIE ZAPYTAŃ --------------------------------------------------
# Bot wysyłał 24 zapytania co 5 minut, ponad 7 tys. dziennie z jednego adresu,
# i Kleinanzeigen zaczęło mu oddawać podstawioną stronę (32 kafelki, bez dat,
# w losowej kolejności) zamiast prawdziwej listy. Kanał zastępuje te zapytania
# JEDNYM, więc reszta chodzi teraz rotacyjnie: po kilka na skan, każde ląduje
# w kolejce mniej więcej raz na godzinę. Zapytania kluczowe i tak są tylko
# zapasem na rowery, których sprzedawca nie oznaczył jako e-bike.
# Zeszło z 2 na 1, żeby zapłacić za drugą półkę (MTB). Zmierzone 22.08:
# przy 5 żądaniach na skan kanał zaczął padać co drugi raz i mediana wykrycia
# poszła z 3 na 12 minut. Zapytania kluczowe są tylko zapasem — półki łapały
# 19 z 19 ogłoszeń — więc to one mają ustąpić miejsca, nie kanał.
KLUCZOWE_NA_SKAN = 1       # gdy kanał żyje — ruch trzymany przy ziemi
KLUCZOWE_AWARYJNE = 8      # gdy kanał leży mimo oszczędzania — trzeba nadrobić
KANAL_CIERPLIWOSC = 12     # tyle skanów (~1 h) dajemy hipotezie o dławieniu


def wybierz_kluczowe(ile, idx):
    """Kolejny kawałek listy zapytań, z zawijaniem. Zwraca (zapytania, nowy_idx)."""
    if ile <= 0 or not SEARCHES:
        return [], idx
    ile = min(ile, len(SEARCHES))
    start = idx % len(SEARCHES)
    wybrane = [SEARCHES[(start + i) % len(SEARCHES)] for i in range(ile)]
    return wybrane, (start + ile) % len(SEARCHES)


def pobierz_z_datami(search):
    """fetch_listings, ale nie odpuszcza stronie bez dat wystawienia.

    Kleinanzeigen bywa kapryśne: potrafi oddać wariant strony bez bloku
    z datą (a czasem zupełnie inny zestaw kafelków). Bez daty nie da się
    cofać po kanale, więc prosimy ponownie, ze świeżymi ciastkami.
    Zmierzone: z czyszczeniem ciastek 8/8 poprawnych.

    TYLKO DLA KANAŁU. Objęcie tym 23 zapytań kluczowych wydłużyło bieg
    z 0,5 do 5 minut i biegi zaczęły się kasować nawzajem — a tam data
    jest wyłącznie ozdobą, bo świeżość i tak pilnuje kanał."""
    listings, stats = fetch_listings(search)
    for proba in range(2, FEED_PROBY + 1):
        if REPLAY_DIR or stats["blocks"] < 5 or stats["time_hits"] > 0:
            break
        # Gdy poprzedni skan już oberwał podstawioną stroną, ponawianie jest
        # nie tylko bezcelowe — dokłada żądań DOKŁADNIE tam, gdzie serwis
        # właśnie przykręca kurek. Zmierzone 22.08: przy dwóch półkach zły
        # skan robił 4 żądania zamiast 2 i kanał zaczął padać co drugi raz.
        if _stan().get("kanal_zle", 0) > 0:
            log.warning(f"[{search['name']}] strona bez dat — nie ponawiam, "
                        f"kanał już dławiony")
            break
        log.warning(f"[{search['name']}] strona bez dat — próba {proba}/{FEED_PROBY}")
        time.sleep(2.0)
        scraper.cookies.clear()
        listings, stats = fetch_listings(search)
    return listings, stats


def kanal_niemy(stats) -> bool:
    """Czy ta półka NIC nie oddała w tym skanie. Powód nas tu nie obchodzi.

    DWA SPOSOBY, NA JAKIE PÓŁKA MILCZY, i przez pół dnia liczył się tylko
    jeden. `strona_zepsuta` rozpoznaje podstawioną listę: kafelki SĄ, dat
    NIE MA (wymaga `blocks >= 5`). Drugi sposób to strona bez ani jednego
    kafelka - i ona nie zapalała niczego, bo `blocks >= 5` jest wtedy
    fałszywe. Dla parsera "zero ogłoszeń" wyglądało jak spokojny rynek.

    ZMIERZONE 01.09.2026, i to jest cena tej dziury: obie niemieckie półki
    zamilkły o 11:15 (ostatnie ogłoszenie złapane minutę po wystawieniu,
    potem nic). Bot chodził dalej, wszystkie biegi zielone, `check_feed_health`
    poprawnie krzyknął `feed_martwe: ["Kleinanzeigen"]` - ale licznik
    `kanal_zle`, od którego zależy tryb awaryjny, stał na ZERZE przez
    5,5 godziny. Tryb awaryjny (KLUCZOWE_AWARYJNE = 8 zapytań zamiast 1)
    miał się włączyć po KANAL_CIERPLIWOSC = 12 skanach, czyli po godzinie.
    Nie włączył się ani razu. Półka dawała ~4 000 ogłoszeń dziennie, tego
    dnia 767 - i ocenione oferty stanęły: 25 o 14:00, 26 o 16:37.

    Pusta półka kategorii nie jest stanem naturalnym: e-bike i MTB mają na
    Kleinanzeigen tysiące ogłoszeń dziennie, zero znaczy blokadę albo awarię.
    Liczymy `blocks`, nie długość wyniku - półka może zgodnie z prawdą nie
    mieć NOWYCH ogłoszeń, ale zawsze ma jakieś."""
    return bool(stats.get("zepsuty")) or not stats.get("blocks")


BLACKBOX_PROBEK = 6   # tyle RÓŻNYCH odpowiedzi na dobę i półkę wystarczy do diagnozy


def zapisz_czarna_skrzynke(nazwa, html, staty=None, dzis=None):
    """Zapisuje SUROWĄ odpowiedź niemej półki do `blackbox/`. Zwraca ścieżkę
    albo None, gdy nic nie zapisano.

    PO CO, skoro czujka dryfu parsera już coś takiego robi: bo ona się na tę
    awarię nie zapala. Podstawiona lista ma tytuły i ceny w 100% - brakuje
    wyłącznie DAT - więc `check_parser_health` widzi zdrowy parser i nie
    zapisuje niczego. Przy odpowiedzi bez ani jednego kafelka jest jeszcze
    gorzej: `blocks` nie dobija do PARSE_HEALTH_MIN_BLOCKS i sprawdzanie
    kończy się na `continue`.

    Skutek zmierzony 01.09.2026: półki milczały sześć godzin, alarm poszedł,
    a JEDYNYM zapisem w blackbox/ był plik z 09.07. Nie dało się orzec, czy to
    blokada zakresu IP, dławienie, czy przebudowa serwisu - a od tej odpowiedzi
    zależy, czy się czeka, czy przepina ruch przez przekaźnik. Logi biegów
    GitHuba są zamknięte (403 nawet przy publicznym repo), więc jedynym
    świadkiem tego, co dostaje runner, jest sam runner.

    NAZWA NIESIE ODCISK TREŚCI, a nie sam dzień. Pierwsza wersja zapisywała
    jeden plik na dobę i półkę - i sama się zablokowała: 01.09.2026 półka
    trafiała 5 skanów na 8, a próbki tych trzech PUDEŁ nie dało się już
    zdobyć, bo plik z tego dnia istniał (z wcześniejszej awarii, sprzed
    naprawy wzorca daty). Diagnoza stanęła na pytaniu „czy zła strona nie ma
    dat, czy ma je inaczej" i nie było czym odpowiedzieć.

    Odcisk rozwiązuje oba końce naraz: ta sama odpowiedź daje tę samą nazwę,
    więc powtórki nie robią commita co 5 minut, a odpowiedź INNA dostaje
    własny plik. Limit BLACKBOX_PROBEK trzyma to w ryzach, gdyby serwis
    zmieniał w odpowiedzi choćby znacznik czasu i każdy odcisk był nowy."""
    if not html:
        return None
    try:
        Path("blackbox").mkdir(exist_ok=True)
        dzis = dzis or date.today().isoformat()
        bezpieczna = re.sub(r'[^\w]+', '_', nazwa)
        odcisk = hashlib.sha256(html.encode("utf-8", "replace")).hexdigest()[:8]
        plik = Path(f"blackbox/niema-{bezpieczna}-{dzis}-{odcisk}.html")
        if plik.exists():
            return None                    # tę samą odpowiedź już mamy
        rodzenstwo = list(Path("blackbox").glob(f"niema-{bezpieczna}-{dzis}-*.html"))
        if len(rodzenstwo) >= BLACKBOX_PROBEK:
            return None                    # dość na dziś, repo to nie archiwum
        plik.write_text(html, encoding="utf-8")
        # Metryki obok, nie w środku: dopisane do HTML-a zmieniłyby dowód.
        plik.with_suffix(".json").write_text(json.dumps({
            "polka": nazwa, "kiedy": datetime.now(TZ_DE).isoformat(),
            "status": (staty or {}).get("status"),
            "blocks": (staty or {}).get("blocks"),
            "time_hits": (staty or {}).get("time_hits"),
            "stron": (staty or {}).get("stron"),
            "kB": len(html) // 1024,
            "odcisk": odcisk,
        }, ensure_ascii=False), encoding="utf-8")
        return plik
    except Exception as e:
        log.error(f"blackbox [{nazwa}]: {e}")
        return None


def licz_kanal_zle(poprzednio: int, niemych_ka: int, ile_ka: int) -> int:
    """Licznik skanów, w których zamilkły WSZYSTKIE półki Kleinanzeigen.

    Jedna czynna półka wystarcza, żeby rowery płynęły, więc licznik zeruje
    się przy pierwszym udanym odczycie którejkolwiek."""
    return poprzednio + 1 if ile_ka and niemych_ka == ile_ka else 0


def ile_kluczowych(kanal_zle: int) -> int:
    """Ile zapytań kluczowych puścić w tym skanie.

    Mało, dopóki wierzymy, że oszczędzanie ruchu odblokuje kanał. Gdy kanał
    leży mimo tego dłużej niż KANAL_CIERPLIWOSC skanów, hipoteza była zła
    i wracamy do większej liczby zapytań, żeby rowery nie przestały płynąć
    przez naszą teorię. Wyciągnięte z pętli 01.09.2026, żeby dało się na to
    napisać test — bez tego przez pół dnia nikt nie zauważył, że próg nie
    zostaje przekroczony NIGDY."""
    return KLUCZOWE_NA_SKAN if kanal_zle < KANAL_CIERPLIWOSC else KLUCZOWE_AWARYJNE


def strona_zepsuta(stats) -> bool:
    """Czy to podstawiona strona-śmieć zamiast prawdziwej listy?

    Kleinanzeigen pod obciążeniem oddaje HTTP 200, właściwy adres i właściwy
    tytuł, ale w środku losowy zestaw starych ogłoszeń i kafelki BEZ daty
    wystawienia (zmierzone 22.08.2026: 32 kafelki, 0 dat, najstarsze id
    sprzed lat). Taka odpowiedź wygląda dla parsera na zdrowy rynek, więc
    jedynym pewnym rozpoznaniem jest brak dat na całej stronie."""
    return stats["blocks"] >= 5 and stats["time_hits"] == 0


def cena_w_widelkach(price_num) -> bool:
    """Nieznana cena NIE jest odrzuceniem — ratuje ją strona ogłoszenia."""
    return price_num is None or MIN_PRICE <= price_num <= MAX_PRICE


def _czas_z_zapisu(s):
    """Czas wystawienia z seen.json z powrotem na datę. None gdy go nie było."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def zapisz_nieodczytane(seen, listing, prev, stan, today, nazwa_zrodla=None,
                       teraz=None):
    """Zapamiętuje NIEUDANY odczyt strony — bez oceny i bez powiadomienia.

    To jest sedno gwarancji: chwilowa awaria pobierania nie ma prawa zostać
    zapisana jako wiedza o rowerze. Wpis nie ma `score`, więc pętla potraktuje
    go w następnym skanie jak nowe ogłoszenie, a `url` pozwala wrócić po stronę
    bez pośrednictwa półki (znacznik czasu już go minął).

    Zwraca numer podejścia, albo None gdy ogłoszenie zostało zdjęte."""
    ad_id = listing["id"]
    if stan == "usuniete":
        # 404/410 — ogłoszenia nie ma. To jest fakt, a nie awaria: nie ma
        # czego czytać i nie ma czego kupować.
        seen[ad_id] = {"date": today, "powod": "zdjete"}
        return None
    stare = prev if isinstance(prev, dict) else {}
    n = stare.get("nieodczytane", 0) + 1
    seen[ad_id] = {
        "date": today,
        "title": listing.get("title", ""),
        "price": listing.get("price"),
        "price_num": listing.get("price_num"),
        "foto": listing.get("foto"),
        "loc": listing.get("loc"),
        # czas wystawienia z karty — inaczej ponowione ogłoszenie skłamałoby
        # w wiadomości, że wieku "nie podano", i zgubiłoby własny alarm o
        # spóźnieniu; wiek liczymy na nowo przy każdym podejściu
        "posted": (listing["posted"].isoformat()
                   if listing.get("posted") else None),
        "url": listing.get("url"),
        "search": stare.get("search") or nazwa_zrodla,
        "nieodczytane": n,
        "od": stare.get("od") or (teraz or datetime.now(TZ_DE)).isoformat(),
    }
    return n


def wraca_jak_nowe(prev) -> bool:
    """Czy wpis po NIEUDANYM odczycie ma iść ścieżką nowego ogłoszenia.

    Wpis bez `score` to zapis nieudanej próby, a nie ocena roweru — o tym
    rowerze nadal nic nie wiemy, więc ma przejść całą drogę jeszcze raz.

    ALE TYLKO DO `ODCZYT_PODEJSC` PRÓB, i ten sufit jest tu sednem. Bez niego
    ogłoszenie, którego strona nie wstaje, wracało z PÓŁKI przy każdym skanie
    bez końca — z kolejki wypadało, ale półka oddaje je dalej. Zmierzone
    20.09.2026 na wh-904689464: **4 288 pobrań tej samej strony**, ~1 000
    dziennie przy 111 pobraniach dziennie całego bota.

    Funkcja jest CZYSTA i osobna od pętli z rozmysłu — inaczej nie da się na
    ten warunek napisać testu, a dokładnie tak przez sześć dni nikt nie
    zauważył, że próg nie zostaje przekroczony nigdy (ta sama nauka co przy
    `licz_kanal_zle` z 01.09)."""
    if not isinstance(prev, dict):
        return False
    if not prev.get("nieodczytane") or prev.get("score") is not None:
        return False
    return prev["nieodczytane"] < ODCZYT_PODEJSC


def do_odczytania(seen, teraz=None):
    """Zaległe ogłoszenia do ponownego przeczytania — [(id, wpis), ...].

    Najświeższe naprzód, bo starszy rower i tak jest już mniej wart uwagi.
    Lista jest krótka z rozmysłem: żądania do Kleinanzeigen to twarda waluta
    (patrz KLUCZOWE_NA_SKAN), więc zaległości nie mogą wypchnąć bieżącego
    skanu. Wypadają wpisy bez adresu, po ODCZYT_PODEJSC próbach i starsze niż
    ODCZYT_WAZNE_H."""
    teraz = teraz or datetime.now(TZ_DE)
    czeka = []
    for ad_id, w in seen.items():
        if not isinstance(w, dict) or not w.get("nieodczytane") or not w.get("url"):
            continue
        if w["nieodczytane"] >= ODCZYT_PODEJSC:
            continue
        try:
            od = datetime.fromisoformat(w["od"])
        except (KeyError, TypeError, ValueError):
            continue
        if (teraz - od).total_seconds() > ODCZYT_WAZNE_H * 3600:
            continue
        czeka.append((od, ad_id, w))
    czeka.sort(key=lambda x: x[0], reverse=True)
    return [(ad_id, w) for _, ad_id, w in czeka[:ODCZYT_NA_SKAN]]


def wpis_jako_ogloszenie(ad_id, w):
    """Zaległy wpis z seen.json z powrotem w kształt ogłoszenia z listy."""
    return {
        "id": ad_id,
        "title": w.get("title", ""),
        "price": w.get("price"),
        "price_num": w.get("price_num"),
        "loc": w.get("loc", ""),
        "foto": w.get("foto"),
        "posted": _czas_z_zapisu(w.get("posted")),
        "age_min": ad_age_minutes(_czas_z_zapisu(w.get("posted"))),
        "url": w["url"],
        "ponowienie": w.get("nieodczytane", 0),
    }


# Powody odrzutu, KTÓRE MOŻE COFNĄĆ SPADEK CENY. Trzy z nich zależą od ceny
# wprost (nisza i mała bateria przechodzą przy dużej przecenie), czwarty —
# rzekoma powtórka — jest tu, bo dedup bywa w błędzie i pomyłka nie ma prawa
# być dożywotnia. Zmierzone 01.09.2026: na 60 dniach dedup zdławił 158 ofert,
# z czego co najmniej 60 było innym rowerem. Bez tej furtki taki rower milczy
# już zawsze — 3492497177 stanial po zdlawieniu z 2 550 na 2 400 € i bot nie
# pisnął, bo ścieżka obniżki wymaga wpisu z `score`, a odrzucony go nie ma.
POWODY_PO_CENIE = {"cena", "nisza", "bateria", "relisting"}


def odrzuc(seen, listing, today, powod, **extra):
    """Zapisuje odrzucenie RAZEM Z POWODEM.

    Do 01.09.2026 każdy odrzut poza cenowym zapisywał gołe `{"date": ...}`
    i przyczyna ginęła. Zmierzone tego dnia: 29 147 z 79 479 wpisów w
    `seen.json` to takie gołe wpisy. Kiedy właściciel zapytał, czemu nie
    dostał powiadomienia o konkretnym rowerze, odpowiedzi NIE DAŁO SIĘ
    odczytać z pliku — trzeba ją było odtwarzać symulacją. Narzędzie, które
    nie umie powiedzieć, dlaczego zamilkło, jest czarną skrzynką (patrz
    nagłówek CLAUDE.md).

    `p` to cena w chwili odrzutu — bez niej `wraca_po_przecenie` nie ma
    czego porównać i furtka po przecenie jest tylko na papierze."""
    wpis = {"date": today, "powod": powod}
    if listing.get("price_num"):
        wpis["p"] = listing["price_num"]
    wpis.update(extra)
    seen[listing["id"]] = wpis


def wraca_po_przecenie(prev, price_num):
    """Czy to rower odrzucony kiedyś z powodu, który cena może cofnąć?

    Zwraca dawną cenę albo None. To jedyna droga powrotu dla ogłoszenia bez
    `score` — ścieżka 'obniżki' obsługuje wyłącznie rowery, które wcześniej
    przeszły filtry. Odrzut cenowy wraca po samym wejściu w widełki, reszta
    (patrz POWODY_PO_CENIE) dopiero po realnym spadku ceny."""
    if not isinstance(prev, dict) or price_num is None:
        return None
    if not (MIN_PRICE <= price_num <= MAX_PRICE):
        return None
    stara = prev.get("cena_odrzut")
    if stara:
        return stara                      # format sprzed 01.09.2026
    if prev.get("powod") not in POWODY_PO_CENIE:
        return None
    stara = prev.get("p")
    if not stara:
        return None
    if prev["powod"] == "cena":
        return stara
    # Odrzut NIE-cenowy (nisza, mała bateria, rzekoma powtórka) wraca
    # wyłącznie przy REALNYM spadku. Bez tego warunku każdy taki rower
    # wracałby przy każdym skanie i dostawał podpis "PRZECENIONE" albo
    # "NOWE WIDEŁKI" — a żadne z dwojga nie byłoby prawdą.
    return stara if price_num < stara else None


def jest_przecena(przecena_z, price_num) -> bool:
    """Czy cena SPADŁA, czy tylko MY poszerzyliśmy widełki?

    `wraca_po_przecenie` mówi wyłącznie tyle, że rower odrzucony kiedyś po
    cenie mieści się dziś w widełkach. Wpaść tam może dwiema drogami: bo
    sprzedawca zbił cenę, albo bo podnieśliśmy sufit (2500 → 3000 €,
    25.08.2026). Tylko pierwsza jest okazją i tylko ona ma prawo nazywać się
    przeceną oraz iść na początek kolejki."""
    return bool(przecena_z and price_num is not None and price_num < przecena_z)


def load_feed_znacznik(typ: str = "ebike"):
    """Czas najnowszego ogłoszenia z poprzedniego skanu TEJ półki.

    Każda półka ma własny znacznik — to osobne listy i cofanie się po jednej
    nic nie mówi o drugiej. Klucz `ostatnie` to format sprzed dołożenia drugiej
    półki; czytamy go jako znacznik e-bike'ów, żeby wdrożenie nie zaczynało
    od zera i nie zassało godzin historii."""
    try:
        stan = json.loads(FEED_STATE_FILE.read_text())
        raw = stan.get(typ) or (stan.get("ostatnie") if typ == "ebike" else None)
        if not raw:
            return None
        dt = datetime.fromisoformat(raw)
        return dt if dt.tzinfo else dt.replace(tzinfo=TZ_DE)
    except Exception:
        return None


def save_feed_znacznik(dt, typ: str = "ebike"):
    if not dt:
        return
    try:
        stan = {}
        if FEED_STATE_FILE.exists():
            try:
                stan = json.loads(FEED_STATE_FILE.read_text())
            except Exception:
                stan = {}
        stan[typ] = dt.isoformat()
        stan.pop("ostatnie", None)          # stary klucz już niepotrzebny
        FEED_STATE_FILE.write_text(json.dumps(stan))
    except Exception as e:
        log.error(f"zapis znacznika kanału: {e}")


def fetch_feed(od=None, typ="ebike", nazwa=None, teraz=None):
    """Czyta jedną półkę wstecz, aż dojdzie do ogłoszeń starszych niż `od`.

    Zwraca (ogłoszenia, statystyki, dosiegl). `dosiegl=False` znaczy, że
    limit stron skończył się, ZANIM domknęliśmy lukę — czyli część ogłoszeń
    przepadła i trzeba o tym krzyknąć, a nie milczeć."""
    nazwa = nazwa or f"kanał {typ}"
    wynik, widziane, stats_all = [], set(), []
    dosiegl = od is None          # pierwszy bieg: bierzemy tylko stronę 1
    zepsuty = False
    # Luka za duża, żeby ją domknąć? Nie zaczynaj — patrz FEED_LUKA_MAX_MIN.
    za_stary = od is not None and (ad_age_minutes(od, teraz) or 0) > FEED_LUKA_MAX_MIN
    if za_stary:
        log.error(f"[{nazwa}] znacznik sprzed {format_age(ad_age_minutes(od, teraz))} — "
                  f"luki nie da się domknąć, biorę stronę 1 i ruszam znacznik")
        od = None
    prog = (od - timedelta(minutes=FEED_MARGINES_MIN)) if od else None

    for n in range(1, FEED_MAX_STRON + 1):
        url = feed_url(typ, n)
        karty, stats = pobierz_z_datami({"name": f"{nazwa} s.{n}", "url": url})
        stats_all.append(stats)
        if strona_zepsuta(stats):
            # Nie wolno tego policzyć jako "obejrzany rynek": znacznik
            # zostałby przesunięty, a prawdziwe ogłoszenia z tego okna
            # przepadłyby na zawsze. Lepiej zgłosić awarię i spróbować za minutę.
            log.error(f"[{nazwa}] strona {n}: podstawiona lista bez dat — "
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
            # strona bez ani jednej daty — dalsze cofanie się jest ślepe
            log.warning(f"[{nazwa}] strona {n} bez dat — przerywam cofanie")
            break

    if zepsuty:
        wynik = []                # nic z tego biegu nie jest wiarygodne
        dosiegl = False
    czasy = [l["posted"] for l in wynik if l["posted"]]
    stats = {"name": nazwa, "typ": typ,
             "zepsuty": zepsuty,
             "blocks": sum(s["blocks"] for s in stats_all),
             "title_hits": sum(s["title_hits"] for s in stats_all),
             "price_hits": sum(s["price_hits"] for s in stats_all),
             "time_hits": sum(s["time_hits"] for s in stats_all),
             "html": next((s["html"] for s in stats_all if s["html"]), None),
             "status": stats_all[-1]["status"] if stats_all else None,
             "stron": len(stats_all),
             "za_stary": za_stary,
             "najnowsze": max(czasy) if czasy else None}
    return wynik, stats, dosiegl


def stars(score: int) -> str:
    if score >= 80:
        return "🔥🔥🔥"
    if score >= 60:
        return "🔥🔥"
    if score >= 40:
        return "🔥"
    return ""


def persist_seen_git() -> bool:
    """Commituje i pushuje seen.json NATYCHMIAST (przed wysyłką powiadomień).
    Dzięki temu przerwany run nigdy nie powoduje duplikatów — najwyżej
    brak powiadomienia. Działa tylko na GitHub Actions.

    ODDAJE WERDYKT, bo od niego zależy, czy wolno wysyłać (18.09.2026).
    `False` znaczy „stan NIE trafił na main", czyli następne ogniwo zobaczy
    te same rowery. Wysłanie ich wtedy to nie ryzyko duplikatu, tylko
    pewność - właściciel dostał kilka ofert PO CZTERDZIEŚCI RAZY."""
    if not os.environ.get("GITHUB_ACTIONS"):
        return True          # lokalnie nie ma czego pushować
    import subprocess
    # CO GIT POWIEDZIAŁ, MUSI TRAFIĆ DO LOGU (18.09.2026). Ta funkcja przez
    # miesiące zjadała `stderr` przez `capture_output=True` i zostawiała po
    # sobie jedno zdanie „nie udało się wypchnąć" bez ani słowa powodu.
    # Gdy 17.09 push zaczął padać, w logu biegu nie było CZYM odpowiedzieć na
    # pytanie, czy to brak uprawnień, konflikt, czy sieć - a od tej odpowiedzi
    # zależy, co się robi dalej. Cicha awaria jest gorsza od głośnej (reguła 7).
    #
    # LIMIT CZASU, bo awaria bywa ZAWIESZENIEM, nie błędem. Zmierzone tego
    # dnia w kroku „Zapisz seen.json": `git pull --rebase` nie wypisał ani
    # jednej linijki przez 11 min 53 s, aż runner dostał SIGTERM (kod 143).
    # Ogniwo zżarło 13 minut zamiast półtorej, przez co grupa `concurrency`
    # trzymała kolejkę i szturchnięcia co 5 minut kasowały się nawzajem.
    # Bez limitu jeden zawieszony git zatyka cały łańcuszek.
    skargi = []

    def run(*args, limit=90):
        try:
            w = subprocess.run(args, capture_output=True, text=True, timeout=limit)
        except subprocess.TimeoutExpired:
            skargi.append(f"{' '.join(args[:3])}: ZAWIESIŁ SIĘ, ubity po {limit} s")
            return False
        if w.returncode != 0:
            powod = (w.stderr or w.stdout or "").strip().replace("\n", " ")
            skargi.append(f"{' '.join(args[:3])}: {powod[:200]}")
        return w.returncode == 0

    run("git", "config", "user.name", "DealHawk Bot")
    run("git", "config", "user.email", "bot@dealhawk")
    # TRZY USTAWIENIA Z NIEUDANEJ DIAGNOZY 18.09 - zostają, ale NIE TŁUMACZĄ
    # NICZEGO. Szukałem wtedy awarii po stronie pushu, a siedziała po stronie
    # poboru: płytki klon `actions/checkout` ciągnął przy `git pull --rebase`
    # całe repozytorium. Stało tu zdanie „to jedyna hipoteza, która tłumaczy
    # WSZYSTKIE pomiary naraz" - nieprawdziwe, i jego zdjęcie jest jedyną
    # treścią tej zmiany.
    #
    # Zostają, bo bot chodzi z nimi zmierzoną dobę w tempie sprzed awarii,
    # a zdejmowanie działającej konfiguracji bez powodu to ryzyko za darmo.
    # Samodzielnie broni się tylko `lowSpeed*`: każe gitowi PRZERWAĆ martwy
    # transfer i wrócić błędem z powodem, zamiast czekać na limit z zewnątrz.
    # Pełny wywód: CLAUDE.md, „Zawieszał się POBÓR, nie wysyłka".
    run("git", "config", "--local", "http.postBuffer", "524288000")
    run("git", "config", "--local", "http.version", "HTTP/1.1")
    run("git", "config", "--local", "http.lowSpeedLimit", "1000")
    run("git", "config", "--local", "http.lowSpeedTime", "15")
    # każdy plik OSOBNO — brakująca ścieżka (np. blackbox) nie może przerwać
    # dodawania pozostałych (git add wielu ścieżek pęka gdy jedna nie istnieje)
    # Kawałki dziennika rynku (`market-RRRR-MM.jsonl`) dokładamy z nazwy,
    # bo `git add` dostaje tu gotową ścieżkę, a nie wzorzec powłoki. Bez tego
    # nowy miesiąc wypadłby z commita po cichu.
    # KAWAŁKI STANU NA LIŚCIE JUŻ TERAZ, zanim zapis na nie przejdzie.
    # Dopisane po fakcie znaczyłoby, że pierwszy kawałek wypada z commita
    # po cichu, a stan dedupu ginie razem z jednorazowym runnerem - ta sama
    # klasa awarii co `blackbox` i `market-*` poza `git add`.
    for path in ["seen.json", "history.jsonl", "market.jsonl", "parser_health.json",
                 "feed_stan.json", "blackbox"] + [str(k) for k in market_kawalki()] \
                + [str(k) for k in seen_kawalki()]:
        run("git", "add", path)
    if subprocess.run(["git", "diff", "--staged", "--quiet"]).returncode == 0:
        return True          # brak zmian = nie ma czego zgubić
    run("git", "commit", "-m", "update seen.json")

    def cicho(*args):
        """Polecenie, którego niepowodzenie NIE jest skargą (np. `rebase
        --abort`, gdy żadnego rebase'u nie było). Bez tego log pełen jest
        błędów, które niczego nie znaczą, i prawdziwy powód w nich ginie."""
        try:
            subprocess.run(args, capture_output=True, text=True, timeout=15)
        except subprocess.TimeoutExpired:
            pass

    # ZAWIESZAŁ SIĘ POBÓR, NIE WYSYŁKA (18.09.2026, bieg 35391355748).
    # Pełny wywód stoi przy tej samej pętli w kroku „Zapisz seen.json"
    # w `tracker.yml`; w skrócie: `actions/checkout` robi klon PŁYTKI, a gołe
    # `git pull --rebase` prosi wtedy o historię, której ten klon nie ma.
    # Dziennik gita pokazał paczkę `--pack_header=2,134839`, czyli CAŁE
    # repozytorium, i `index-pack`, który nie kończył pracy do limitu.
    #
    # `--depth=1 origin main` ogranicza pobór do samego wierzchołka, a
    # `rebase --onto FETCH_HEAD <baza>` przenosi na niego NASZE commity.
    # Samo `pull --rebase --depth=1` to pułapka sprawdzona i odrzucona:
    # po płytkim poborze nie ma wspólnego przodka, więc git odwraca role
    # i przekłada commity main-a na nasz.
    for _ in range(3):
        # BAZA czytana przed KAŻDYM poborem, bo pobór ją przesuwa.
        baza = subprocess.run(["git", "rev-parse", "refs/remotes/origin/main"],
                              capture_output=True, text=True).stdout.strip()
        if not baza:
            skargi.append("git rev-parse: brak refs/remotes/origin/main")
            break
        if (run("git", "fetch", "--depth=1", "origin", "main", limit=15)
                and run("git", "rebase", "--onto", "FETCH_HEAD", baza, limit=15)
                and run("git", "push", "origin", "HEAD:main", limit=15)):
            log.info("seen.json zapisany do repo przed wysyłką powiadomień")
            return True
        cicho("git", "rebase", "--abort")
        time.sleep(5)
    log.error("Nie udało się wypchnąć seen.json przed wysyłką! Git wypisał: "
              + " || ".join(skargi[-4:] or ["nic, co jest osobnym dziwactwem"]))
    return False


PARSE_STATE_FILE = Path("parser_health.json")


def check_parser_health(all_stats):
    """Monitor #2: skuteczność ekstrakcji per pole, OSOBNO DLA KAŻDEJ GIEŁDY.

    Osobno, bo to dwa różne parsery czytające dwa różne serwisy, a jedna
    wspólna średnia potrafi je nawzajem zamaskować w obie strony: przy
    proporcjach z 25.08 (setki ogłoszeń z willhaben, dziesiątki z półek
    Kleinanzeigen) całkowita śmierć parsera Kleinanzeigen zeszłaby poniżej
    progu razem z willhaben i nic by nie krzyknęło — a to dokładnie ta cicha
    awaria, przed którą stoi ta funkcja (reguła 7)."""
    try:
        wg_serwisu = {}
        for st in all_stats:
            wg_serwisu.setdefault(st.get("serwis", "Kleinanzeigen"), []).append(st)

        prev = {}
        if PARSE_STATE_FILE.exists():
            try:
                prev = json.loads(PARSE_STATE_FILE.read_text())
            except Exception:
                prev = {}
        serwisy = prev.get("serwisy") or {}
        chore, do_zapisu = [], []

        for serwis, staty in wg_serwisu.items():
            blocks = sum(x["blocks"] for x in staty)
            if blocks < PARSE_HEALTH_MIN_BLOCKS:
                continue
            title_rate = sum(x["title_hits"] for x in staty) / blocks
            price_rate = sum(x["price_hits"] for x in staty) / blocks
            was_ok = (serwisy.get(serwis) or {}).get("ok", True)
            now_ok = (title_rate >= PARSE_HEALTH_MIN_RATE
                      and price_rate >= PARSE_HEALTH_MIN_RATE)
            serwisy[serwis] = {"ok": now_ok, "title_rate": round(title_rate, 2),
                               "price_rate": round(price_rate, 2),
                               "blocks": blocks}
            if not now_ok:
                chore.append(f"{serwis}: tytuł {int(title_rate*100)}%, "
                             f"cena {int(price_rate*100)}%")
                if was_ok:
                    do_zapisu += [x for x in staty if x.get("html")]
                    log.error(f"Parser drift [{serwis}]: title={title_rate:.2f} "
                              f"price={price_rate:.2f} — HTML w blackbox/")

        if do_zapisu:                      # czarna skrzynka: co przyszło zamiast listy
            Path("blackbox").mkdir(exist_ok=True)
            for x in do_zapisu[:2]:
                safe = re.sub(r'[^\w]+', '_', x['name'])
                try:
                    Path(f"blackbox/{safe}-{date.today().isoformat()}.html").write_text(
                        x["html"], encoding="utf-8")
                except Exception:
                    pass

        # Klucze bez przedrostka zostają przy Kleinanzeigen — czyta je /status
        # i stary format pliku, a przenoszenie ich znaczyłoby cichą zmianę
        # znaczenia liczby, którą ktoś już ogląda.
        ka = serwisy.get("Kleinanzeigen") or {}
        prev.update({"serwisy": serwisy, "checked": date.today().isoformat()})
        if ka:
            prev.update({"ok": ka["ok"], "title_rate": ka["title_rate"],
                         "price_rate": ka["price_rate"]})
        PARSE_STATE_FILE.write_text(json.dumps(prev))

        if chore:
            zglos_problem("slepy", "parser — " + "; ".join(chore))
    except Exception as e:
        log.error(f"check_parser_health error: {e}")


def diagnose_empty_scan(all_stats) -> str:
    """Z kodów HTTP wnioskuje PRZYCZYNĘ pustego skanu. Zwraca gotową wiadomość.
    Lekcja z 2026-07-28: awaria Akamai (503 wszędzie) była alertowana jako
    'zmiana HTML' — zła diagnoza kosztuje debugowanie nie tego, co trzeba.

    Ta sama lekcja każe nazwać SERWIS. Od 25.08 bot czyta dwie giełdy i zdanie
    "Kleinanzeigen zmieniło HTML" przy awarii willhaben wysłałoby na cały
    wieczór szukania nie tam, gdzie trzeba."""
    statuses = [s.get("status") for s in all_stats]
    n = len(statuses) or 1
    # Które giełdy w ogóle brały udział w tym skanie
    serwis = " + ".join(sorted({s.get("serwis", "Kleinanzeigen") for s in all_stats}))
    n_5xx = sum(1 for st in statuses if isinstance(st, int) and st >= 500)
    n_4xx = sum(1 for st in statuses if isinstance(st, int) and 400 <= st < 500)
    n_net = sum(1 for st in statuses if st is None)
    n_200 = sum(1 for st in statuses if st == 200)
    if n_5xx >= n * 0.5:
        return (f"⏸ <b>DealHawk — {serwis} leży (5xx).</b>\n\n"
                f"{n_5xx}/{n} wyszukiwań dostało błąd serwera — to awaria "
                "po ICH stronie, nie parsera. Nic nie rób, bot sam wznowi "
                "skan i da znać, gdy serwis wstanie.")
    if n_4xx >= n * 0.5:
        return (f"🚫 <b>DealHawk — {serwis} blokuje scraper (4xx).</b>\n\n"
                f"{n_4xx}/{n} wyszukiwań odrzuconych. Prawdopodobnie antybot "
                "(IP runnera / fingerprint). Jeśli potrwa — trzeba zmienić "
                "sposób pobierania.")
    if n_200 >= n * 0.5:
        return (f"🚨 <b>DealHawk — zmiana HTML ({serwis})!</b>\n\n"
                f"{n_200}/{n} wyszukiwań zwróciło stronę (200), ale zero "
                f"ogłoszeń do sparsowania. {serwis} zmieniło strukturę — "
                "parser wymaga naprawy.")
    return ("🌐 <b>DealHawk — problemy sieciowe.</b>\n\n"
            f"{n_net}/{n} wyszukiwań bez odpowiedzi (timeout/DNS). "
            "Możliwa awaria po drodze — obserwuję.")


# Warianty wejścia do OLX — sprawdzamy, czy blokada jest na całą domenę,
# czy tylko na główny serwis. Zapytania idą raz dziennie, po jednym na wariant.
KANAREK_WPADKI_DO_ALARMU = 2   # jedna nieudana proba = zwykle timeout, nie awaria

SONDA_WEJSCIA = [
    ("www_api", "https://www.olx.pl/api/v1/offers/?offset=0&limit=5&query=cube"),
    ("www_api", "https://www.olx.pl/api/v1/offers/?offset=0&limit=5&query=cube"),
    ("m_html", "https://m.olx.pl/sport-hobby/rowery/q-cube-stereo-hybrid/"),
    ("apex", "https://olx.pl/sport-hobby/rowery/q-cube-stereo-hybrid/"),
    ("oferta", "https://www.olx.pl/d/oferta/rower-CID767-ID1abc.html"),
    ("sitemap", "https://www.olx.pl/sitemap.xml"),
]


def diagnoza_dostepu_olx(status, kart, dlugosc) -> str:
    """Rozróżnia TRZY różne awarie, bo każda wymaga czego innego:
    padł przekaźnik / OLX blokuje mimo przekaźnika / zmienił się layout."""
    kb = dlugosc // 1024
    if OLX_RELAY_URL:
        if przekaznik_zyje() is False:
            return ("🔌 <b>DealHawk — padł przekaźnik Cloudflare!</b>\n\n"
                    "Sam Worker nie odpowiada. Możliwe przyczyny: skasowany,\n"
                    "przekroczony darmowy limit (100 tys./dobę) albo awaria Cloudflare.\n\n"
                    "<b>Co zrobić:</b> zajrzyj na dash.cloudflare.com → Workers.\n"
                    "<i>Monitorowanie rynku PL stoi.</i>")
        # status=None/0 znaczy, że odpowiedź nie doszła do OLX-a wcale —
        # czyli odrzucił nas sam Worker. Kod odmowy mówi, dlaczego.
        if status in (None, 0):
            d = olx_diag()
            kod = d.get("przekaznik_status")
            if kod is None:
                # Nie było ODMOWY — połączenie w ogóle nie doszło do skutku.
                # Realny przypadek z 21.08: timeout przy przesyłaniu strony
                # ważącej 3,5 MB wyglądał jak "przekaźnik odmawia, kod None".
                return ("⏱ <b>DealHawk — przekaźnik nie odpowiedział w czasie.</b>\n\n"
                        f"Rodzaj awarii: <code>{d.get('ostatni_wyjatek', 'nieznany')}</code>\n"
                        "Zwykle chwilowe — wolne łącze albo OLX mieli stronę.\n"
                        "<i>Bot spróbuje ponownie za 5 minut.</i>")
            powod = {
                401: "ZŁY KLUCZ — sekret <code>OLX_RELAY_KEY</code> w GitHubie\n"
                     "musi być IDENTYCZNY jak zmienna <code>KLUCZ</code> w Workerze.",
                429: "PRZEKROCZONY LIMIT zapytań na minutę. Albo boty oszalały,\n"
                     "albo ktoś zna Twój klucz i z niego korzysta — wymień go.",
                503: "BRAK KLUCZA w konfiguracji Workera (zmienna <code>KLUCZ</code>).",
                403: "Worker odrzucił adres — poza dozwoloną listą ścieżek OLX.",
            }.get(kod, f"kod odmowy: {kod}")
            return ("🔑 <b>DealHawk — przekaźnik odmawia.</b>\n\n"
                    f"{powod}\n<i>Monitorowanie rynku PL stoi.</i>")
        return (f"🚫 <b>DealHawk — OLX blokuje MIMO przekaźnika.</b>\n\n"
                f"HTTP {status}, kafelków {kart}, {kb} kB.\n"
                "Cloudflare też trafił na czarną listę — trzeba zmienić drogę.\n"
                "<i>Monitorowanie rynku PL stoi.</i>")
    return ("🚫 <b>DealHawk — OLX nas nie wpuszcza!</b>\n\n"
            f"Zapytanie kontrolne: HTTP {status}, kafelków {kart}, {kb} kB.\n"
            "Nie ustawiono przekaźnika — bot pyta OLX wprost, a serwerownia\n"
            "GitHuba jest zablokowana.\n"
            "<i>(dokładnie to działo się po cichu od 10 sierpnia)</i>")


def olx_kanarek():
    """Jedno zapytanie kontrolne do OLX przy KAŻDYM przebiegu trackera (co 5 min).
    Dzięki temu blokada wychodzi na jaw w 5 minut, a nie po 11 dniach ciszy jak
    w sierpniu 2026. Wynik ląduje w parser_health.json — pliku i tak commitowanym
    — więc widać go z zewnątrz, bez dostępu do logów runnera."""
    try:
        # LEKKIE zapytanie kontrolne: API (~50 kB) zamiast strony (3,5 MB).
        # Poprzednio kanarek ciągnął przez przekaźnik 3,5 MB CO 5 MINUT — czyli
        # ~1 GB dziennie — i regularnie łapał timeout, wywołując fałszywe alarmy.
        r = olx_get("https://www.olx.pl/api/v1/offers/?offset=0&limit=5"
                    "&query=rower+elektryczny", timeout=25)
        status = r.status_code if r is not None else None
        kart, dlugosc = 0, (len(r.text) if r is not None else 0)
        if r is not None and r.status_code == 200:
            try:
                kart = len(((r.json() or {}).get("data")) or [])
            except Exception:
                kart = 0
        zdrowy = kart > 0

        stan = {}
        if PARSE_STATE_FILE.exists():
            try:
                stan = json.loads(PARSE_STATE_FILE.read_text())
            except Exception:
                stan = {}
        poprzedni = stan.get("olx") or {}
        bylo_ok = poprzedni.get("ok", True)
        # Licznik wpadek z rzędu. Jedna nieudana próba to najczęściej chwilowy
        # timeout, nie awaria — alarmowanie po niej to wilk, który nie przyszedł.
        wpadki = 0 if zdrowy else poprzedni.get("wpadki", 0) + 1
        stan["olx"] = {"ok": zdrowy, "status": status, "kart": kart,
                       "kb": dlugosc // 1024, "wpadki": wpadki,
                       "kiedy": date.today().isoformat()}

        # SONDA WEJŚĆ (raz dziennie): czy blokada obejmuje KAŻDĄ drogę do OLX,
        # czy tylko www? Jeśli którekolwiek wejście przejdzie z serwerowni,
        # mamy darmowe rozwiązanie — bez proxy i bez trzymania laptopa włączonego.
        if (stan.get("sonda") or {}).get("kiedy") != date.today().isoformat():
            wyniki = {}
            for nazwa, u in SONDA_WEJSCIA:
                try:
                    rr = olx_get(u, timeout=15)
                    wyniki[nazwa] = rr.status_code if rr is not None else "brak"
                except Exception as e:
                    wyniki[nazwa] = f"exc:{str(e)[:25]}"
                time.sleep(0.4)
            stan["sonda"] = {"kiedy": date.today().isoformat(), "wyniki": wyniki}
        PARSE_STATE_FILE.write_text(json.dumps(stan))

        # Stanem jest LICZNIK wpadek, a nie flaga ok/nie-ok. Przy fladze druga
        # nieudana próba miała już bylo_ok=False i alarm nigdy by nie poleciał.
        if not zdrowy and wpadki >= KANAREK_WPADKI_DO_ALARMU:
            # diagnoza zostaje w logu — na Telegram idzie wspólna, prosta
            # wiadomość, i dopiero gdy awaria utrzyma się przez godzinę
            zglos_problem("olx", re.sub("<[^>]+>", "",
                                        diagnoza_dostepu_olx(status, kart, dlugosc)
                                        .splitlines()[0]))
        elif not zdrowy:
            log.warning(f"OLX kanarek: wpadka {wpadki} — jeszcze bez alarmu")
    except Exception as e:
        log.error(f"olx_kanarek error: {e}")


def check_feed_health(all_stats, total_found):
    """Alert gdy skan pusty — z trafną diagnozą przyczyny i bez spamu:
    wiadomość tylko przy przejściu działa→nie działa (raz, nie co 5 minut)
    oraz jedna, gdy skan wróci. Stan w parser_health.json (klucz feed_ok).

    OSOBNO NA GIEŁDĘ, z tego samego powodu co `check_parser_health`. Warunek
    "cały skan pusty" przy dwóch serwisach jest znacznie słabszy niż przy
    jednym: całkowita ślepota na Kleinanzeigen nie robi skanu pustym, dopóki
    Austria cokolwiek oddaje. Bez tego rozbicia dołożenie drugiej giełdy
    po cichu WYŁĄCZYŁOBY istniejący alarm — a to gorsze niż brak nowej giełdy."""
    try:
        state = {}
        if PARSE_STATE_FILE.exists():
            try:
                state = json.loads(PARSE_STATE_FILE.read_text())
            except Exception:
                state = {}
        wg_serwisu = {}
        for st in all_stats:
            wg_serwisu.setdefault(st.get("serwis", "Kleinanzeigen"), []).append(st)

        martwe = {}
        for serwis, staty in wg_serwisu.items():
            if sum(x.get("blocks", 0) for x in staty) == 0:
                martwe[serwis] = staty
        state["feed_ok"] = not martwe and total_found > 0
        state["feed_martwe"] = sorted(martwe)
        PARSE_STATE_FILE.write_text(json.dumps(state))
        if not martwe:
            return
        # diagnoza (serwis leży / blokada / zmiana HTML) trafia do logu;
        # do użytkownika idzie jedno proste zdanie z bramki, po godzinie.
        # Liczymy ją z odpowiedzi TEGO serwisu, nie z całego skanu — inaczej
        # zdrowe odpowiedzi drugiej giełdy rozmyłyby kody HTTP i diagnoza
        # wskazałaby "problemy sieciowe" tam, gdzie jest twarda blokada.
        for serwis, staty in martwe.items():
            msg = re.sub("<[^>]+>", "", diagnose_empty_scan(staty).splitlines()[0])
            zglos_problem("slepy", f"pusty skan: {msg}")
    except Exception as e:
        log.error(f"check_feed_health error: {e}")


def main(tylko_feed=False):
    process_telegram_commands()   # najpierw odpowiedz na /wycen z telefonu
    _problemy.clear()             # awarie zbierane w trakcie tego skanu
    seen = prune_seen(load_seen())
    new_count = 0
    total_found = 0
    today = date.today().isoformat()
    olx_cache = {}
    price_hist = build_price_history(seen)
    # Rozrzut przeliczany razem z cennikiem historycznym, w JEDNYM miejscu.
    # Produkcja to łańcuszek krótkich biegów (świeży proces = świeży cache),
    # ale tryb zapasowy woła `main` w pętli w tym samym procesie — bez tego
    # odświeżenia pula odniesienia zamarzłaby na godziny.
    load_rozrzut(force=True)
    recent_index = build_recent_index(seen)
    pending_msgs = []
    all_stats = []
    zrodla = []

    # 1. PÓŁKI KATEGORII — źródło odpowiedzialne za czas reakcji. Każda czytana
    #    wstecz aż do własnego znacznika, więc przerwa w harmonogramie opóźnia
    #    powiadomienie, ale niczego nie gubi.
    kanal_zle = _stan().get("kanal_zle", 0)
    feed_ids, opisy_kanalow, padly = set(), [], 0
    padly_ka = 0        # osobno: TYLKO Kleinanzeigen — patrz niżej, przy tempie
    for kan in POLKI:
        znacznik = load_feed_znacznik(kan["typ"])
        listings, stats, dosiegl = kan["pobierz"](znacznik)
        all_stats.append(stats)
        uzupelnij_wiek(listings)
        # Ten sam rower może siedzieć tylko w jednej rubryce, ale gdyby
        # Kleinanzeigen kiedyś pokazało go w obu, nie chcemy dwóch wiadomości.
        listings = [l for l in listings if l["id"] not in feed_ids]
        feed_ids.update(l["id"] for l in listings)
        total_found += len(listings)
        log.info(f"[{kan['nazwa']}] {len(listings)} ogłoszeń z {stats['stron']} stron"
                 f"{'' if dosiegl else ' — LUKA, limit stron'}")

        if kanal_niemy(stats):
            padly += 1
            padly_ka += kan["serwis"] == "Kleinanzeigen"
            # Rozróżnienie w opisie jest po to, żeby alarm mówił, CO widzi:
            # podstawiona lista to inna choroba niż strona bez ogłoszeń,
            # a leczyć trzeba tę, która naprawdę zaszła.
            czym = ("odpowiedź nie do przyjęcia" if stats.get("zepsuty")
                    else "zero ogłoszeń na stronie")
            opisy_kanalow.append(f"{kan['nazwa']}: {czym}")
            log.error(f"[{kan['nazwa']}] półka nieczynna — {czym}")
            # DOWÓD, nie tylko alarm. Bez surowej odpowiedzi nie da się
            # odróżnić blokady zakresu od dławienia i od przebudowy strony,
            # a to trzy różne decyzje.
            zapis = zapisz_czarna_skrzynke(kan["nazwa"], stats.get("html"), stats)
            if zapis:
                log.error(f"[{kan['nazwa']}] surowa odpowiedź zapisana: {zapis}")
        elif not dosiegl and znacznik:
            # PUŁAPKA, w którą bot wpadł 23.08 wieczorem: gdy luka nie domyka
            # się w FEED_MAX_STRON stronach, a znacznik zostaje w miejscu, to
            # KAŻDY następny skan znowu przechodzi wszystkie 12 stron — 24
            # żądania na skan, czyli zmierzona recepta na stronę-śmieć. Luka
            # rośnie, żądań przybywa, ślepota się pogłębia. Spirala.
            #
            # Dlatego znacznik rusza mimo nieudanego domknięcia. To okno
            # ogłoszeń jest stracone tak czy inaczej — wybór jest między
            # "stracone raz" a "stracone i nigdy z tego nie wychodzimy".
            opisy_kanalow.append(f"{kan['nazwa']}: luka poza limitem stron")
            log.error(f"[{kan['nazwa']}] nie domknięto luki od "
                      f"{format_age(ad_age_minutes(znacznik))} — "
                      f"przesuwam znacznik, żeby nie zapętlić 12 stron co skan")
            if stats["najnowsze"]:
                save_feed_znacznik(stats["najnowsze"], kan["typ"])
        else:
            opisy_kanalow.append(f"{kan['nazwa']}: ok")
            if stats["najnowsze"]:
                # Znacznik przesuwamy od razu po udanym odczycie TEJ półki —
                # awaria drugiej nie może cofnąć postępu pierwszej.
                save_feed_znacznik(stats["najnowsze"], kan["typ"])

        # Mediana liczona osobno dla półki i tylko z rowerów porównywalnych
        # (fully + elektryk) — siedzą tam też miejskie i dziecięce, a one
        # zaniżyłyby próg "okazji" dla marek niszowych.
        ceny = [l["price_num"] for l in listings
                if l["price_num"] and is_fully(l["title"]) and is_electric(l["title"])]
        zrodla.append(({"name": kan["nazwa"]}, listings,
                       statistics.median(ceny) if ceny else None))

    # Padnięcie półek NIE jest ślepotą, dopóki zapytania kluczowe oddają
    # ogłoszenia — bot działa wtedy gorzej, nie wcale, a wiadomość "nie przyjdą
    # nowe rowery" byłaby po prostu nieprawdziwa. O ślepocie decyduje na końcu
    # check_feed_health, po policzeniu WSZYSTKICH źródeł. Licznik rośnie tylko
    # gdy padły OBIE półki — jedna czynna wystarcza, żeby rowery płynęły.
    kanal_zle = licz_kanal_zle(kanal_zle, padly_ka, len(KANALY))
    # "padly" czyta pętla, żeby wiedzieć, czy wolno przyspieszyć. Liczy się
    # KAŻDA podstawiona półka, nie dopiero obie: jedna to już sygnał, że
    # serwis zaczyna dławić, a czekanie na obie znaczyłoby uczyć się po fakcie.
    #
    # ALE LICZĄ SIĘ TYLKO PÓŁKI KLEINANZEIGEN. Tempo adaptacyjne i awaryjne
    # zapytania kluczowe to odpowiedź na JEDNO zmierzone zjawisko: dławienie
    # per adres IP na Kleinanzeigen (23.08: ~50 żądań w 10 min = strona-śmieć
    # na 20 minut). Willhaben zniósł 8 żądań pod rząd bez śladu kary
    # (zmierzone 25.08), więc jego awaria nie mówi NIC o tym, czy wolno
    # przyspieszyć tam. Wrzucona do wspólnego worka robiłaby dwie szkody
    # naraz: spowalniałaby skan Kleinanzeigen bez powodu, a jednocześnie
    # rozcieńczała warunek "padły OBIE półki" tak, że awaryjne zapytania
    # kluczowe nie odpaliłyby się nigdy.
    _stan({"kanal": " · ".join(opisy_kanalow), "kanal_zle": kanal_zle,
           "padly": padly_ka, "padly_wszystkie": padly})

    # 2. ZAPYTANIA KLUCZOWE — zapas. Łapią to, czego sprzedawca nie oznaczył
    #    jako e-bike, oraz rowery, które weszły w widełki po edycji ogłoszenia.
    if not tylko_feed:
        # Ile zapytań w tym skanie: mało, dopóki wierzymy, że oszczędzanie
        # ruchu odblokuje kanał. Gdy kanał leży mimo tego dłużej niż
        # KANAL_CIERPLIWOSC skanów, hipoteza była zła — wracamy do większej
        # liczby zapytań, żeby rowery nie przestały płynąć przez moją teorię.
        ile = ile_kluczowych(kanal_zle)
        wybrane, nowy_idx = wybierz_kluczowe(ile, _stan().get("kluczowe_idx", 0))
        _stan({"kluczowe_idx": nowy_idx})
        log.info(f"zapytania kluczowe: {len(wybrane)} z {len(SEARCHES)} "
                 f"(kanał zły od {kanal_zle} skanów)")
        for search in wybrane:
            # bez ponawiania — patrz pobierz_z_datami: tu data jest ozdobą,
            # a 23 zapytania z odczekiwaniem rozdymały bieg do 5 minut
            listings, stats = fetch_listings(search)
            all_stats.append(stats)
            total_found += len(listings)
            log.info(f"[{search['name']}] znaleziono {len(listings)} ogłoszeń")
            # Data w karcie jest podstawą oceny świeżości — gdy Kleinanzeigen zmieni
            # układ HTML, ma to wyjść w logu, a nie zniknąć po cichu jak wycena 10.08
            if stats["blocks"] >= 5 and stats["time_hits"] == 0:
                log.warning(f"[{search['name']}] żadna karta nie ma daty — "
                            f"zmiana HTML? wiek ogłoszeń przestał działać")
            prices_in_search = [l["price_num"] for l in listings if l["price_num"]]
            zrodla.append((search, listings,
                           statistics.median(prices_in_search) if prices_in_search else None))

    # 3. ZALEGŁE — ogłoszenia, których strony nie udało się przeczytać.
    #    Idą po własny adres, bo półka ich już nie odda: jej znacznik czasu
    #    przesunął się dalej. Dokładamy je do źródła, z którego przyszły, żeby
    #    odziedziczyły tę samą medianę cen — inaczej marka niszowa wypadłaby
    #    tylko dlatego, że nie ma z czym porównać ceny.
    zalegle = do_odczytania(seen)
    if zalegle:
        wg_nazwy = {}
        for ad_id, w in zalegle:
            wg_nazwy.setdefault(w.get("search") or "zaległe odczyty", []).append(
                wpis_jako_ogloszenie(ad_id, w))
        indeks = {s["name"]: i for i, (s, _, _) in enumerate(zrodla)}
        for nazwa, lst in wg_nazwy.items():
            if nazwa in indeks:
                s, l, m = zrodla[indeks[nazwa]]
                juz = {x["id"] for x in l}
                zrodla[indeks[nazwa]] = (s, l + [x for x in lst if x["id"] not in juz], m)
            else:
                zrodla.append(({"name": nazwa}, lst, None))
        log.info("zaległe odczyty: " + ", ".join(
            f"{w.get('title','?')[:30]} (podejście {w['nieodczytane'] + 1})"
            for _, w in zalegle))

    for search, listings, median_price in zrodla:
        for listing in listings:
            prev = seen.get(listing["id"])
            # PRAWDZIWY wpis sprzed tego skanu. `prev` bywa niżej cofane do
            # None i to jest celowe, ale `zapisz_nieodczytane` potrzebuje
            # ORYGINAŁU: liczy z niego numer podejścia i czas pierwszej próby.
            # Podane cofnięte `prev` dawało zawsze "podejście 1" i datę TERAZ,
            # więc ani próg ODCZYT_PODEJSC, ani wiek ODCZYT_WAZNE_H, ani alarm
            # do właściciela nie miały jak zadziałać (zmierzone 20.09.2026).
            wpis_przed = prev
            # Rower widziany wcześniej, ale odrzucony WYŁĄCZNIE przez cenę,
            # który właśnie wszedł w widełki. Dla nas to pierwszy moment,
            # w którym jest ofertą — więc idzie pełną ścieżką nowego ogłoszenia.
            przecena_z = wraca_po_przecenie(prev, listing["price_num"])
            # Czy cena FAKTYCZNIE spadła, czy to MY poszerzyliśmy widełki?
            # Bez tego rozróżnienia podniesienie sufitu (2500 → 3000 €,
            # 25.08.2026) ogłosiłoby setki NIEZMIENIONYCH ofert jako
            # "PRZECENIONE" i wypchnęło je na początek kolejki. Rower,
            # któremu nikt nie zbił ceny, nie jest przeceną — i tyle.
            przecena_realna = jest_przecena(przecena_z, listing["price_num"])
            if przecena_z:
                prev = None
            # Ogłoszenie, którego strony wcześniej NIE UDAŁO SIĘ przeczytać,
            # wraca jak nowe — bo o tym rowerze nadal nic nie wiemy. Wpis bez
            # `score` to zapis nieudanej próby, a nie ocena roweru.
            if wraca_jak_nowe(prev):
                prev = None
            if prev is not None:
                # KAŻDA zmiana ceny do dziennika, także drobna. Powiadomienie
                # leci dopiero od 5%, ale próg powiadomienia nie może decydować
                # o tym, co WIEMY: bez tego zapisu nie da się policzyć, ile
                # sprzedawcy realnie opuszczają przed sprzedażą, a to jedyna
                # publicznie dostępna droga do ceny domykającej.
                if (isinstance(prev, dict) and listing["price_num"]
                        and prev.get("price_num")
                        and listing["price_num"] != prev["price_num"]):
                    append_history(olx_query_for(listing["title"], None),
                                   listing["price_num"], ad_id=listing["id"],
                                   year=prev.get("year"), ev="cena",
                                   zr=zrodlo_historii(listing))
                # Obniżka ceny na ogłoszeniu, które wcześniej przeszło filtry
                if (isinstance(prev, dict) and prev.get("score") is not None
                        and listing["price_num"] and prev.get("price_num")
                        and listing["price_num"] < prev["price_num"] * 0.95):
                    # świeża weryfikacja przebiegu — dane w bazie mogą być stare/błędne
                    fresh_mileage, _, _, _, stan_sw, _meta_sw = czytaj_ogloszenie(
                        listing["url"], listing["title"])
                    old_price = prev["price_num"]
                    if stan_sw == "ok":
                        fresh_num = parse_mileage(fresh_mileage)
                        prev["mileage"] = fresh_mileage
                        prev["mileage_num"] = fresh_num
                    else:
                        # Nieudany odczyt nie może skasować tego, co już wiemy —
                        # zostaje przebieg z pierwszego, udanego czytania.
                        fresh_mileage = prev.get("mileage", "brak danych")
                        fresh_num = prev.get("mileage_num")
                        log.warning(f"Obniżka: nie odczytano strony, "
                                    f"zostaje znany przebieg {fresh_mileage}")
                    prev["price"] = listing["price"]
                    prev["price_num"] = listing["price_num"]
                    if is_too_worn(fresh_num):
                        log.info(f"Obniżka pominięta (przebieg {fresh_mileage}): {listing['title'][:50]}")
                        continue
                    pending_msgs.append((
                        -1,   # obniżki idą przodem — okazja jest świeża, nie rower
                        f"📉 <b>DealHawk — obniżka ceny!</b>\n\n"
                        f"📌 <b>{html_mod.escape(listing['title'])}</b>\n"
                        f"💰 {old_price} € → <b>{listing['price']}</b>\n"
                        f"🚵 {fresh_mileage}\n"
                        + ("🔒 <b>ZAREZERWOWANY</b> — ktoś był pierwszy.\n"
                           if _meta_sw.get("zarezerwowane") is True else "")
                        + f"🔗 {listing['url']}",
                        listing.get("foto"), None, []))
                    # trajektoria obniżki do dziennika finalistów
                    append_history(olx_query_for(listing["title"], None), listing["price_num"],
                                   ad_id=listing["id"], mileage_num=fresh_num, ev="drop",
                                   zr=zrodlo_historii(listing))
                    log.info(f"Obniżka {old_price} -> {listing['price_num']}: {listing['title'][:50]}")
                continue

            # LOG RYNKU — każde nowe ogłoszenie, PRZED filtrami cenowymi
            # i jakościowymi (przecenione już tam jest z pierwszego spotkania).
            #
            # WYJĄTEK: półki "Mountainbikes" (obu giełd) to w większości zwykłe
            # rowery bez silnika — na Kleinanzeigen 96%, na willhaben w oknie
            # 200 ogłoszeń z 25.08 ani jednego elektryka. Są obserwowane tylko
            # po to, żeby wyłapać e-MTB, które sprzedawca źle otagował.
            # Logowanie ich w całości zalało dziennik: 8 826 wierszy jednego
            # dnia, czyli 47% całego pliku od czerwca. To nie jest "nasz
            # rynek", tylko szum — więc z takiej półki zapisujemy wyłącznie
            # to, co wygląda na elektryk.
            if not przecena_z and (search["name"] not in POLKI_SZUM
                                   or is_electric(listing["title"])):
                log_market(listing, search["name"])

            # WIDEŁKI CENOWE W KODZIE. Kanał kategorii nie ma filtra ceny
            # w URL-u i to jest celowe: rower za 3000 € ma być ZOBACZONY
            # i zapamiętany, żeby po przecenie do 2300 € dało się go rozpoznać.
            # Brak ceny na liście przepuszczamy — ratuje ją strona ogłoszenia.
            # LISTA ŻYCZEŃ WŁAŚCICIELA (`obserwowane.json`). Model z tej listy
            # omija bramki, które ucinają oferty SŁABE BIZNESOWO - budżet,
            # przebieg, niszę, małą baterię i dedup re-listingu. Nie omija
            # silnika, śmieci ani bramek na ruch: tam nie chodzi o opłacalność.
            #
            # To jest świadome ODWRÓCENIE domyślnej zasady repo. DealHawk woli
            # zgubić niż zdublować, bo powtórka wygląda jak awaria. Tutaj
            # właściciel powiedział wprost „chcę ten rower i mam na pewno nie
            # przegapić żadnego ogłoszenia", więc rachunek jest odwrotny.
            pilny = obserwowany(listing["title"])

            if not pilny and not cena_w_widelkach(listing["price_num"]):
                log.info(f"Pominięto (cena {listing['price_num']} € poza widełkami): "
                         f"{listing['title'][:50]}")
                odrzuc(seen, listing, today, "cena",
                       cena_odrzut=listing["price_num"])
                continue

            if is_junk(listing["title"]):
                log.info(f"Pominięto (śmieć): {listing['title'][:50]}")
                odrzuc(seen, listing, today, "smiec")
                continue

            # BRAMKA OTWARTA DLA LISTY ŻYCZEŃ, tak samo jak budżet i przebieg.
            # Za duża rama ucina ofertę SŁABĄ BIZNESOWO (trudny zbyt w Polsce),
            # a nie ogłoszenie, które nie jest rowerem - więc model z listy
            # życzeń przechodzi, a cała reszta rynku jest odsiewana dokładnie
            # jak dotąd. Powód zapisany osobno, żeby plik mówił prawdę:
            # do 20.09.2026 te rowery leżały w `seen.json` jako "smiec".
            if not pilny and za_duza_rama(listing["title"]):
                log.info(f"Pominięto (rama XL): {listing['title'][:50]}")
                odrzuc(seen, listing, today, "za_duza_rama")
                continue

            if not is_fully(listing["title"]):
                log.info(f"Pominięto (nie fully): {listing['title'][:50]}")
                odrzuc(seen, listing, today, "nie_fully")
                continue

            if not is_electric(listing["title"]):
                log.info(f"Pominięto (analogowy): {listing['title'][:50]}")
                odrzuc(seen, listing, today, "analogowy")
                continue

            # Marka spoza whitelisty PL → tylko przy wyjątkowej okazji cenowej
            if not is_premium_brand(listing["title"]):
                discount_ok = (
                    listing["price_num"] and median_price
                    and (median_price - listing["price_num"]) / median_price * 100 >= NICHE_MIN_DISCOUNT_PCT
                )
                if not discount_ok and not pilny:
                    log.info(f"Pominięto (niszowa marka bez okazji): {listing['title'][:50]}")
                    odrzuc(seen, listing, today, "nisza")
                    continue

            mileage, desc_text, detail_price, zdjecia, stan_odczytu, meta = \
                czytaj_ogloszenie(listing["url"], listing["title"])

            # NIE PRZECZYTALIŚMY strony — więc nic o tym rowerze nie orzekamy.
            # Ani "brak przebiegu", ani oceny, ani powiadomienia. Wpis wraca do
            # kolejki i przyjdzie po swój adres w kolejnym skanie.
            if stan_odczytu != "ok":
                n = zapisz_nieodczytane(seen, listing, wpis_przed, stan_odczytu,
                                        today, search["name"])
                if n is None:
                    log.info(f"Ogłoszenie zdjęte: {listing['title'][:50]}")
                elif n >= ODCZYT_PODEJSC:
                    # Ostatnie podejście. Strona nie wstaje, a rower jest
                    # w widełkach — więc zamiast po cichu go zgubić, mówimy
                    # wprost, czego nie wiemy, i oddajemy link do ręki.
                    log.error(f"Nie odczytano po {n} podejściach: {listing['url']}")
                    pending_msgs.append((
                        -1,
                        f"⚠️ <b>DealHawk — nie mogę odczytać ogłoszenia</b>\n\n"
                        f"📌 <b>{html_mod.escape(listing['title'])}</b>\n"
                        f"💰 {listing['price']}\n\n"
                        f"Strona nie wstaje od {n} prób — nie znam ani przebiegu, "
                        f"ani stanu. <b>Nic o tym rowerze nie liczę.</b>\n"
                        f"Zerknij sam, jest w widełkach:\n{listing['url']}",
                        listing.get("foto"), None, []))
                else:
                    log.warning(f"Odczyt nieudany ({n}/{ODCZYT_PODEJSC}), "
                                f"wróci w kolejnym skanie: {listing['title'][:50]}")
                continue

            zlicz_odczyt(zdjecia, detail_price)
            zarezerwowany = meta.get("zarezerwowane")
            mileage_num = parse_mileage(mileage)

            # Ratunek ceny ze strony ogłoszenia gdy lista jej nie dała
            if not listing["price_num"] and detail_price:
                listing["price"] = detail_price
                listing["price_num"] = parse_price(detail_price)
                # cena znana dopiero teraz — widełki trzeba sprawdzić ponownie
                # (obserwowany model przechodzi tak samo jak przy pierwszej bramce)
                if not pilny and not cena_w_widelkach(listing["price_num"]):
                    log.info(f"Pominięto (cena ze strony {listing['price_num']} € "
                             f"poza widełkami): {listing['title'][:50]}")
                    odrzuc(seen, listing, today, "cena",
                           cena_odrzut=listing["price_num"])
                    continue

            if not has_known_motor(listing["title"], desc_text):
                log.info(f"Pominięto (brak marki silnika): {listing['title'][:50]}")
                odrzuc(seen, listing, today, "obcy_silnik")
                continue

            if is_stary_brose(listing["title"], desc_text):
                log.info(f"Pominięto (Levo FSR — Brose na pasku): {listing['title'][:50]}")
                odrzuc(seen, listing, today, "stary_brose")
                continue

            if is_too_worn(mileage_num) and not pilny:
                log.info(f"Pominięto (za duży przebieg {mileage}): {listing['title'][:50]}")
                odrzuc(seen, listing, today, "przebieg", km=mileage_num)
                continue

            # Mała bateria / model SL → jak nisza: tylko przy wyjątkowej okazji
            small_battery = is_small_battery(listing["title"], desc_text)
            if small_battery:
                discount_ok = (
                    listing["price_num"] and median_price
                    and (median_price - listing["price_num"]) / median_price * 100 >= NICHE_MIN_DISCOUNT_PCT
                )
                if not discount_ok and not pilny:
                    log.info(f"Pominięto (mała bateria/SL bez okazji): {listing['title'][:50]}")
                    odrzuc(seen, listing, today, "bateria")
                    continue

            # Re-listing? Ten sam rower pod nowym ID w ostatnich 14 dni → pomiń
            relisted_from = find_relisting(recent_index, listing["title"],
                                           listing["price_num"], mileage_num,
                                           listing.get("loc"))
            if relisted_from and not pilny:
                log.info(f"Pominięto (re-listing z {relisted_from}): {listing['title'][:50]}")
                odrzuc(seen, listing, today, "relisting", z=relisted_from)
                continue

            model_year = extract_year(listing["title"]) or extract_year(desc_text)

            listing["mileage"] = mileage
            listing["mileage_num"] = mileage_num
            # Rozrzut własnego modelu+baterii: liczony PRZED punktacją, bo to
            # jego mediana ma wyznaczać punkty za cenę, nie mediana półki.
            _, rozrzut_ceny = kubelek_rozrzutu(listing["title"], desc_text)
            rozrzut_med = kwartyle(rozrzut_ceny)[1] if rozrzut_ceny else None
            sc = score_listing(listing, median_price, rozrzut_med)

            rozrzut_line, rozrzut_bonus = sygnal_rozrzutu(
                listing["title"], desc_text, listing["price_num"])
            sc = min(100, sc + rozrzut_bonus)

            # Sygnał z własnego cennika historycznego modelu (per rocznik).
            # Zostaje jako SŁABSZY zapas: liczy cały model bez rozbicia na
            # baterię, więc miesza 500 Wh z 750 Wh. Gdy zadziałał kubełek,
            # nie dokładamy drugi raz tej samej informacji.
            hist_line, hist_bonus = price_history_signal(
                listing["title"], listing["price_num"], model_year, price_hist)
            if rozrzut_line:
                hist_line, hist_bonus = None, 0
            sc = min(100, sc + hist_bonus)

            # Szacowany zysk z odsprzedazy w Polsce — zapytanie per model
            olx_query = olx_query_for(listing["title"], search["name"])
            if olx_query not in olx_cache:
                try:
                    olx_cache[olx_query] = olx_relevant_offers(olx_query, fetch_olx_offers(olx_query))
                except Exception as e:
                    log.error(f"OLX fetch error [{olx_query}]: {e}")
                    olx_cache[olx_query] = {}
            olx_offers = olx_cache[olx_query]

            # === ILE TEN ROWER JEST WART W POLSCE ===============================
            # Cennik cech przelicza KAŻDĄ polską ofertę na specyfikację tego
            # konkretnego roweru z Niemiec — zamiast szukać bliźniaka i udawać,
            # że nieznany atrybut pasuje (to kosztowało zakup Cube'a 2018).
            # Pojemność do WYCENY czytana luźniejszym czytnikiem: w nazwach
            # modeli siedzi goła liczba ("HPC SLX 750 Carbon"), bez "Wh".
            # `battery_wh` takiej nie widzi i cecha o największej wadze
            # w cenniku (20,3% na 100 Wh) po cichu wypadała z wyceny.
            # `is_small_battery` zostaje przy czytniku ŚCISŁYM z rozmysłu:
            # tam brak odczytu znaczy "przepuść", więc luźniejszy czytnik
            # dokładałby ODRZUTY, a nie wiedzę.
            de_wh = bateria_z_nazwy(listing["title"], desc_text)
            de_spec = parse_spec_fields(desc_text)            # osprzęt z niemieckiego opisu
            # Rozmiar ramy liczony RAZ: trafia i do wiadomości, i do listy
            # braków, o które pytamy sprzedawcę — muszą się zgadzać co do joty.
            # Rozmiar czytany z widoku Z GRANICAMI PÓL (patrz opis_z_polami).
            # `de_spec["rozmiar"]` jest tą samą funkcją puszczoną po sklejonym
            # tekście, więc zostaje tylko jako zapas dla giełd, które widoku
            # z polami nie oddają (willhaben).
            rama_txt = (rozmiar_ramy(listing["title"], meta["opis_pola"])
                        if meta.get("opis_pola") else
                        (de_spec.get("rozmiar")
                         or rozmiar_ramy(listing["title"], desc_text)))
            olx_price, olx_price_label, comparable = None, "OLX", None
            pewnosc_wyceny = None   # zmierzone: "niska" myli się 2x w 14% wycen
            skorygowana = False
            # Oferty do porównania: najpierw na żywo, a gdy OLX blokuje runnera
            # (HTTP 403) — z rynku zapisanego w repo. Bez tego wycena po prostu
            # znika, tak jak zniknęła po cichu 10.08.
            porownawcze = oferty_z_cechami(olx_offers, load_olx_details())
            zrodlo = "na żywo"
            if len(porownawcze) < OLX_MIN_SAMPLES:
                z_repo = oferty_z_rynku(olx_query)
                if len(z_repo) >= OLX_MIN_SAMPLES:
                    porownawcze, zrodlo = z_repo, "rynek z repo"
            if len(porownawcze) >= OLX_MIN_SAMPLES:
                _ref = {"y": model_year, "km": mileage_num, "wh": de_wh,
                        "poziom": de_spec.get("poziom")}
                # Najpierw TA SAMA WERSJA (patrz `wariant_modelu`), a gdy nie
                # da wyceny - pula szeroka jak dotąd. Zapas liczony na WYNIKU,
                # nie na liczebności puli: cennik odrzuca oferty bez żadnej
                # znanej cechy i wymaga czterech przeliczonych, więc pula
                # "pięć ofert" potrafi nie dać nic. Rower nie może stracić
                # wyceny przez to, że próbowaliśmy ją zawęzić.
                _zawezone, _wariant = zawez_do_wariantu(porownawcze, listing["title"])
                wyc = (wycen_z_cennikiem(_zawezone, _ref)
                       if _wariant and len(_zawezone) >= OLX_MIN_SAMPLES else None)
                # Wąska pula TYLKO przy pewności co najmniej "średniej". Pomiar
                # z 17.09.2026 złapał to na Cube Stereo Hybrid ONE22: pula tej
                # wersji miała równo 5 ofert (sam próg), surowa mediana prawie
                # ta sama co w szerokiej (12 500 wobec 12 552 zł), a wycena
                # spadała o 34% - bo przy pięciu różnorodnych ofertach jedna
                # podejrzanie tania (7 250 zł za ONE22 800 Wh) i przeliczenie
                # cennikiem przewracają wynik. Żadnej nowej liczby: to istniejąca
                # skala `wycen_z_cennikiem`, a "niska" myli się dwukrotnie
                # w 14% wycen.
                if wyc and wyc.get("pewnosc") not in ("wysoka", "srednia"):
                    wyc = None
                if wyc:
                    zrodlo += f", ta sama wersja: {_wariant}"
                else:
                    wyc = wycen_z_cennikiem(porownawcze, _ref)
                if wyc:
                    olx_price, skorygowana = wyc["cena"], True
                    pewnosc_wyceny = wyc["pewnosc"]
                    olx_price_label = (f"cennik cech ({wyc['cech_znanych']} cech, "
                                       f"{wyc['n']} ofert, {zrodlo})")
                    # Cennik stoi na cenach WYWOŁAWCZYCH. Gdy znamy realny poziom
                    # domykający tego modelu, ścinamy o zaobserwowaną różnicę.
                    demand = get_demand_price(olx_query)
                    # `olx_offers` MUSI być niepuste, i to nie jest ostrożność
                    # na wyrost. Cena popytu przychodzi z ZAPISANEGO pliku
                    # (`olx_watch.json`), a oferty na żywo z sieci — OLX
                    # blokuje serwerownię GitHuba, więc jedno bywa znane przy
                    # drugim pustym. `statistics.median({})` rzuca wtedy
                    # StatisticsError i wywraca CAŁY skan.
                    # Zmierzone 01.09.2026: bieg 33529264710 padł dokładnie tu,
                    # bo tryb awaryjny puścił 8 zapytań kluczowych zamiast 1
                    # i do wyceny doszło więcej rowerów niż zwykle. Błąd siedział
                    # w kodzie od dawna — zmiana tempa tylko go odsłoniła.
                    # Bez ofert po prostu nie ścinamy do ceny domykającej:
                    # brak mnożnika to brak korekty, nie zero (reguła 4).
                    if demand and olx_offers:
                        wywolawcza = statistics.median(olx_offers.values())
                        if wywolawcza > 0:
                            hair = demand / wywolawcza
                            if 0.6 <= hair <= 1.05:
                                olx_price = int(olx_price * hair)
                                olx_price_label += f", −{(1 - hair) * 100:.0f}% do domykającej"
            if not olx_price:                     # brak cennika → jak dotąd
                olx_price = get_demand_price(olx_query)
                olx_price_label = "cena popytu OLX" if olx_price else "OLX"
                if not olx_price and len(olx_offers) >= OLX_MIN_SAMPLES:
                    comparable = olx_comparable_price(olx_offers, model_year, mileage_num,
                                                      de_wh, details=load_olx_details())
                    if comparable[0]:
                        olx_price = comparable[0]
                        olx_price_label = f"OLX {comparable[1]}"

            # Realna cena zakupu po negocjacji — zysk liczymy OD NIEJ, nie od wywoławczej
            buy_price, nego_pct, nego_reasons = realistic_buy_price(
                listing["price_num"], listing["price"], desc_text)

            olx_line = olx_compare_str(olx_query, olx_offers, comparable)
            # Zysk liczymy od tego, co REALNIE zapłacisz (po targu na miejscu),
            # a proponujemy w wiadomości cenę zdalną — bo tyle wypada napisać
            # nieznajomemu, resztę zbija się dopiero stojąc przy rowerze.
            cena_realna, nego_laczny = cena_po_ogledzinach(
                listing["price_num"], nego_pct, nego_reasons)
            profit = (calc_profit(cena_realna, olx_price, mileage_num, model_year,
                                  juz_skorygowana=skorygowana)
                      if buy_price and olx_price else None)

            # Płynność (dni do sprzedaży w PL) i ROI roczne z zaangażowanego kapitału
            liquidity_days = get_liquidity(olx_query, cena=olx_price)
            roi_annual = annual_roi(profit, cena_realna, liquidity_days)

            seen[listing["id"]] = {
                "title": listing["title"],
                "price": listing["price"],
                "price_num": listing["price_num"],
                "buy_price": buy_price,
                "nego_pct": nego_pct,
                "mileage": mileage,
                "mileage_num": mileage_num,
                "year": model_year,
                "url": listing["url"],
                "search": search["name"],
                "loc": listing.get("loc"),
                "date": today,
                "score": sc,
                "profit": profit,
                "olx_median": olx_price,
                "liquidity_days": liquidity_days,
                "roi_annual": roi_annual,
                # ZAPIS TRZECH FAKTÓW, KTÓRE BOT I TAK JUŻ POLICZYŁ.
                # Nic nie zmieniają w decyzjach DealHawka - są wyłącznie
                # zapisywane, żeby `najlepsze.py` nie musiał ich zgadywać
                # z samego tytułu. Zmierzone 12.09.2026: rozmiar ramy da się
                # odczytać z tytułu tylko w 14% ofert, a z opisem razem
                # znacznie częściej. Właściciel: "wypierdol S size w ogóle,
                # według mnie to jest niesprzedawalne, M też średnio,
                # głównie chodzi o L" - bez tego pola kanał nie ma czym
                # tego rozstrzygnąć.
                "rama": rama_txt,
                "wh": de_wh,
                "poziom": de_spec.get("poziom"),
            }
            # Ten bieg może mieć własne dublety — dołóż do indeksu KOMPLET
            # pól. Wpis miał ich cztery z sześciu, więc dla ogłoszeń z tego
            # samego skanu nie działał ani dowód z miejscowości, ani z tytułu
            # — a to właśnie w jednym skanie przychodzą kopie od jednego
            # sprzedawcy. Cztery wiadomości o KTM Macina Lycan 25.08.2026
            # poszły dokładnie tędy.
            recent_index.append((dedup_key(listing["title"]), listing["price_num"],
                                 mileage_num, today, listing.get("loc"),
                                 _tytul_znormalizowany(listing["title"]),
                                 listing["title"]))

            # dziennik historii (append-only, nigdy kasowany) — trwały zapis rynku
            model_key = olx_query_for(listing["title"], None)
            zr = zrodlo_historii(listing)
            append_history(model_key, listing["price_num"], ad_id=listing["id"],
                           mileage_num=mileage_num, year=model_year, olx_median=olx_price,
                           profit=profit, buy_price=buy_price, zr=zr)
            # Trend liczymy z TEGO rynku, z którego jest rower — inaczej
            # austriacka oferta dostawałaby w podpisie niemiecką dynamikę.
            trend = price_trend(model_key, zrodlo=zr)

            new_count += 1
            rating = stars(sc)

            discount_str = ""
            if median_price and listing["price_num"]:
                pct = int((median_price - listing["price_num"]) / median_price * 100)
                discount_str = f" ({pct:+d}% vs DE)"

            profit_str = ""
            if profit is not None:
                emoji = "🟢" if profit > 500 else "🟡" if profit > 0 else "🔴"
                profit_str = f"\n{emoji} Zysk PL: ~{profit:+,.0f} zł ({olx_price_label}: {olx_price:,} zł, transport osobno)"
            elif olx_price and buy_price and mileage == "brak danych":
                max_km = max_profitable_mileage(buy_price, olx_price, year=model_year)
                profit_str = f"\n⚠️ Brak przebiegu — opłacalne jeśli {max_km}"

            # Marża negocjacyjna — realna cena zakupu, nie wywoławcza
            nego_str = ""
            if buy_price and nego_pct >= 0.03 and buy_price < listing["price_num"]:
                off = listing["price_num"] - buy_price
                why = f" ({', '.join(nego_reasons)})" if nego_reasons else ""
                nego_str = f"\n🎯 Realnie ~{buy_price:,} € po negocjacji (−{off} €, luz {int(nego_pct*100)}%{why})".replace(",", " ")

            # Płynność + zwrot z kapitału — jak szybko i z jakim zyskiem wraca kasa
            liq_str = ""
            if liquidity_days:
                speed = "szybki obrót" if liquidity_days <= 14 else "średni" if liquidity_days <= 30 else "wolny — kapitał zamrożony"
                liq_str = f"\n⚡ Płynność PL: ~{liquidity_days} dni do sprzedaży ({speed})"
                if profit is not None and buy_price:
                    invested = buy_price * get_eur_pln() + TRANSPORT_PLN
                    if invested > 0:
                        liq_str += f"\n💹 Zwrot: {profit / invested * 100:+.0f}% z kapitału w tym czasie"

            year_str = f"  📅 {model_year}" if model_year else ""

            # Trend cen modelu z własnego dziennika (rynek DE)
            trend_str = ""
            if trend is not None and abs(trend) >= 8:
                if trend < 0:
                    trend_str = f"\n📉 Ceny modelu {trend}% / 3 tyg (rynek DE tanieje — dobry moment)"
                else:
                    trend_str = f"\n📈 Ceny modelu +{trend}% / 3 tyg (rynek DE drożeje)"

            # Czego bot NIE wyczytał — o to i tylko o to zapytamy sprzedawcy.
            braki = []
            if mileage == "brak danych":
                braki.append("przebieg")
            if not rama_txt:
                braki.append("rama")
            if not de_wh:
                braki.append("bateria")
            festpreis = "Festpreis (mur)" in (nego_reasons or [])
            oferta_eur = (buy_price if buy_price and nego_pct >= 0.06
                          and buy_price < listing["price_num"] and not festpreis
                          else None)
            # DWA osobne przyciski: najpierw wyciągasz informacje, targujesz się
            # dopiero gdy sprzedawca odpisze. Oferta doklejona do pierwszego
            # kontaktu potrafi zabić rozmowę — za niska kwota i cisza w odpowiedzi.
            do_skopiowania = [
                (("📋 Kopiuj pytanie" if braki else "📋 Kopiuj zapytanie"),
                 wiadomosc_do_sprzedawcy(braki)),
            ]
            if oferta_eur:
                do_skopiowania.append(
                    (f"💶 Potem: oferta {oferta_eur} €",
                     wiadomosc_oferta(oferta_eur, po_pytaniach=bool(braki))))
            przycisk = klawiatura_kopiuj(do_skopiowania)
            # GUZIKA PEŁNEJ OFERTY TU NIE MA I TO JEST DECYZJA WŁAŚCICIELA
            # (18.09.2026): „a nie jak do tej pory, że z automatu każde
            # powiadomienie z nową ofertą na DealHawku ma już ofertę".
            # Twarda oferta zostaje na BestDealHawku, pod guzikiem
            # (`najlepsze.klawiatura_pod_oferta`), czyli tam, gdzie właściciel
            # ogląda rowery, po które realnie pojedzie.
            #
            # KOMENDA ŻYJE DALEJ. `/oferta <id>` i wklejony link działają na
            # obu kanałach - zdjęty jest wyłącznie guzik pod powiadomieniem
            # DealHawka, nie sam generator.
            #
            # CZEGO TO NIE ZAŁATWIA, żeby nikt nie szukał tu oszczędności:
            # guzik kosztował ~40 bajtów w JSON-ie wysyłanym do Telegrama
            # (etykieta plus `of|<id>`), nie robił ANI JEDNEGO żądania i nie
            # czytał żadnego pliku - treść oferty powstaje dopiero po
            # stuknięciu. Ciężar bota to `seen.json` (24,6 MB) i
            # `market.jsonl` (27,5 MB) pchane w każdym commicie, i to tam
            # trzeba szukać, a nie tutaj.
            # Zdjęcie główne bierzemy z galerii ogłoszenia, a miniatura z listy
            # jest zapasem — galeria bywa pusta, gdy strona się nie pobrała.
            glowne = zdjecia[0] if zdjecia else listing.get("foto")
            reszta = zdjecia[1:ALBUM_MAX] if len(zdjecia) > 1 else []

            niche_str = ""
            if not is_premium_brand(listing["title"]):
                niche_str = "\n💎 Niszowa marka — przeszła tylko dzięki wyjątkowej cenie (sprawdź płynność na OLX!)"
            if small_battery:
                niche_str += "\n🔋 Mała bateria/SL — trudniejsza i wolniejsza odsprzedaż w PL"

            # Wiek ogłoszenia wprost w wiadomości. Cichy alarm, gdy rower wisi
            # od godzin: nie jesteśmy pierwsi, więc "nikt nie odpisuje" ma
            # wtedy inną przyczynę niż cena, a decyzja o dojeździe też.
            age = listing.get("age_min")
            if age is None:
                age_str = "\n🕐 Wystawione: nie podano (płatne Top-Anzeige)"
            else:
                age_str = f"\n🕐 Wystawione: {format_age(age)}"
                if age > SWIEZOSC_MIN and not przecena_z:
                    # Bot ma się przyznać do spóźnienia SAM, w wiadomości —
                    # inaczej regres wróci po cichu, jak 22.08. Ale przyczyna
                    # bywa różna i trzeba ją rozróżnić, bo tylko jedna jest
                    # do naprawy po naszej stronie.
                    if search["name"] in NAZWY_POLEK:
                        age_str += ("\n🐌 <b>BOT SIĘ SPÓŹNIŁ</b> — było w kanale, "
                                    "a nie zauważył od razu; to do naprawy, zgłoś")
                        log.error(f"SPÓŹNIENIE {format_age(age)} — {listing['url']}")
                    else:
                        age_str += ("\n🔎 Poza kanałem — sprzedawca nie oznaczył "
                                    "roweru jako e-bike, złapane zapytaniem")
                        log.warning(f"spoza kanału, {format_age(age)} — {listing['url']}")
            if przecena_realna:
                # nie spóźnienie, tylko nowa informacja: sprzedawca zmiękł
                age_str += (f"\n📉 <b>PRZECENIONE</b> — bot widział to za "
                            f"{przecena_z} €, teraz weszło w widełki")
            elif przecena_z:
                # cena bez zmian — zmieniły się nasze widełki. Ogłoszenie jest
                # stare i bot NIE jest spóźniony; to my dopiero teraz patrzymy.
                age_str += ("\n🆕 <b>NOWE WIDEŁKI</b> — cena bez zmian, "
                            "bot pomijał ten rower przy niższym suficie")

            safe_title = html_mod.escape(listing["title"])

            # === WIADOMOŚĆ =================================================
            # Pierwsza linijka to WYNIK FINANSOWY, bo tyle widać na ekranie
            # blokady telefonu, zanim cokolwiek otworzysz. Nazwa roweru druga.
            # Dalej idzie od najważniejszego: rynek, zakup, wyjątki, opis.
            # NAZWA ROWERU NA CZELE. Wcześniej pierwszą linijką był szacowany
            # zysk — a to najmniej pewna liczba w całej wiadomości: sierpniowy
            # Cube wyceniony na ~5 500 zł sprzedał się z zyskiem 1 300 (pomyłka
            # ~4x). Stawianie zgadywanki przed faktami to fałszywa pewność.
            # Na wierzchu jest więc to, co wiemy na pewno: co, za ile, kiedy.
            naglowek = [f"kupno {listing['price']}"]
            if age is not None:
                naglowek.append(format_age(age))
            # Skąd rower. Kleinanzeigen jest domyślne i milczy (tak było zawsze
            # i tak zostaje), a każda inna giełda MUSI się przedstawić — bo
            # "kupno 2300 €" wygląda tak samo dla Saksonii i dla Vorarlbergu,
            # a to zupełnie inna trasa i inna decyzja o dojeździe.
            _serwis = serwis_ogloszenia(listing)
            if _serwis != "Kleinanzeigen":
                naglowek.append(f"🇦🇹 {_serwis}")
            L = [f"<b>{safe_title}</b>", "  ·  ".join(naglowek), ""]

            # OBSERWOWANY MODEL NIE OZNACZA SIĘ TUTAJ (19.09.2026).
            # Właściciel: "niech przychodzi ale nie oznaczasz obserwowane
            # (...) na zwyklym dealhawku leci wszystko (...) niech leci
            # w dealhawku normalnie bo jego celem jest szybkosc". Gwiazdka
            # z nazwą modelu poszła stąd do OSOBNEJ, drugiej wiadomości -
            # dostają ją tylko te sztuki, które spełniają jego warunki
            # (`oznacz_obserwowany`). Tu zostaje zwykły strumień.
            #
            # Jedno zdanie jednak zostaje i nie jest oznaczeniem, tylko
            # odpowiedzią na pytanie "czemu ja to widzę" (reguła 6): rower
            # za 4 050 € w kanale obiecującym okazje do 3 000 bez słowa
            # wyjaśnienia wygląda jak usterka bota. Wypisujemy WYŁĄCZNIE te
            # bramki, które ta oferta naprawdę by oblała, więc zdrowa oferta
            # obserwowanego modelu nie dostaje ani znaku więcej niż każda inna.
            if pilny:
                obeszlo = []
                if not cena_w_widelkach(listing["price_num"]):
                    obeszlo.append("cena poza budżetem")
                if is_too_worn(mileage_num):
                    obeszlo.append("przebieg powyżej progu")
                if relisted_from:
                    obeszlo.append(f"dedup widział podobny ({relisted_from})")
                if obeszlo:
                    L.insert(2, "<i>Poza zwykłymi progami ("
                             + html_mod.escape(", ".join(obeszlo))
                             + ") - masz ten model na liście życzeń.</i>")

            # Rezerwacja — od razu pod nazwą, bo zmienia sens całej wiadomości.
            # Sprzedawcy stemplują RESERVIERT na zdjęciu (tego bez AI nie
            # odczytamy), ale Kleinanzeigen dokłada własną plakietkę — widoczną
            # niestety tylko w jednym z układów strony. Dlatego są trzy
            # odpowiedzi, nie dwie, i "nie wiem" też jest wypisane wprost.
            # Rezerwacji przy NOWYM ogłoszeniu nie pokazujemy w ogóle. Nie
            # dlatego, że nie umiemy — dlatego, że nie ma czego pokazać.
            # Stronę czytamy RAZ, po medianie 4 minut od wystawienia, a
            # sprzedawcy rezerwują godziny później. Zmierzone 23.08 na 14
            # własnych powiadomieniach z tego dnia: 14/14 niezarezerwowanych.
            # Plakietka złapałaby więc tylko rower zarezerwowany w pierwszych
            # minutach — czyli prawie nigdy. Zostaje tam, gdzie stronę czytamy
            # PONOWNIE (obniżka ceny), bo tylko tam może w ogóle zdążyć się
            # pojawić. Użytkownik: "chcę być tym pierwszym, który go zarezerwuje".

            # Przebieg, RAMA i BATERIA na wierzchu: to trzy rzeczy, które
            # decydują, czy rower da się odsprzedać. Rozmiar bywa ważniejszy
            # od ceny — na L kupca szuka się tygodniami, XS potrafi nie
            # znaleźć go wcale. Czego nie wiemy, tego nie zmyślamy: brakujące
            # pole po prostu znika z linijki.
            fakty = [x for x in (mileage if mileage != "brak danych" else None,
                                 f"rama {rama_txt}" if rama_txt else None,
                                 f"{de_wh} Wh" if de_wh else None,
                                 str(model_year) if model_year else None,
                                 region_ogloszenia(listing)) if x]
            if fakty:
                L.append(" · ".join(fakty))
            if olx_price:
                # Wystawisz za tyle, dostaniesz mniej — Twój kupujący też
                # przyjedzie i też będzie zbijał. Obie kwoty na wierzchu,
                # żeby zysk nie brał się znikąd.
                dostaniesz = cena_sprzedazy_realna(olx_price)
                _sp = lambda n: f"{n:,}".replace(",", "\u00a0")   # spacja tysięcy
                rynek = (f"W PL wystawisz za ~{_sp(olx_price)} zł, "
                         f"dostaniesz ~{_sp(dostaniesz)} zł")
                if liquidity_days:
                    rynek += f" · schodzą w ~{liquidity_days} dni"
                L.append(rynek)
            if buy_price and nego_pct >= 0.03 and buy_price < listing["price_num"]:
                # Dwie kwoty, bo to dwa etapy: tyle piszesz, tyle celujesz na miejscu.
                def _zl(n):                      # spacja jako separator tysięcy
                    return f"{n:,}".replace(",", "\u00a0")
                cel = (f", na miejscu celuj w {_zl(cena_realna)} €"
                       if cena_realna and cena_realna < buy_price else "")
                L.append(f"Zaproponuj {_zl(buy_price)} €{cel}")
            elif mileage == "brak danych":
                # Do tego miejsca docierają WYŁĄCZNIE przeczytane strony —
                # nieudany odczyt wraca do kolejki i nigdy tu nie dochodzi.
                # Więc "brak danych" znaczy tu dokładnie tyle, ile mówi:
                # sprzedawca przebiegu nie napisał (albo ma go na zdjęciu
                # licznika, czego bez AI nie odczytamy).
                L.append("⚠️ Sprzedawca nie podał przebiegu — zapytaj przed dojazdem")

            # Wyjątki: to, co zmienia decyzję. Nigdy nie ucinane.
            for wyjatek in (
                    (f"📉 <b>PRZECENIONE</b> — bot widział to za {przecena_z} €"
                     if przecena_realna else
                     "🆕 <b>NOWE WIDEŁKI</b> — cena bez zmian, doszedł przez podniesiony sufit"
                     if przecena_z else None),
                    ("🐌 <b>BOT SIĘ SPÓŹNIŁ</b> — zgłoś to"
                     if (age is not None and age > SWIEZOSC_MIN and not przecena_z
                         and search["name"] in NAZWY_POLEK) else None),
                    ("🔎 Poza kanałem — sprzedawca nie oznaczył go jako e-bike"
                     if (age is not None and age > SWIEZOSC_MIN and not przecena_z
                         and search["name"] not in NAZWY_POLEK) else None),
                    rozrzut_line.strip() if rozrzut_line else None,
                    "🏆 " + hist_line.strip().lstrip("🏆").strip() if hist_line and "NAJTAŃSZ" in hist_line.upper() else None,
                    "💎 Niszowa marka — przeszła tylko ceną, sprawdź zbyt" if not is_premium_brand(listing["title"]) else None,
                    "🔋 Mała bateria — wolniejsza odsprzedaż w PL" if small_battery else None):
                if wyjatek:
                    L.append(wyjatek)

            # SZACUNEK ZYSKU — w każdym ogłoszeniu, ale na samym dole i zawsze
            # podpisany. Zmierzone 23.08 na 502 wycenach bez przecieku: mediana
            # błędu 20%, a przy pewności "niska" co siódma myli się ponad
            # dwukrotnie. Dlatego pewność jedzie razem z kwotą — bez niej liczba
            # udaje wiedzę. Gdy nie ma z czego liczyć, bot mówi to wprost,
            # zamiast milczeć albo zgadywać.
            if profit is not None:
                znak = "🔥" if profit > 500 else "🟡" if profit > 0 else "🔴"
                # Brak etykiety pewności = wycena NIE przeszła przez cennik cech,
                # tylko przez prostsze porównanie. Taka liczba wygląda tak samo,
                # więc musi się przedstawić — inaczej udaje mocniejszą, niż jest.
                ogon = (f" (pewność: {pewnosc_wyceny})" if pewnosc_wyceny
                        else " (szacunek zgrubny)")
                L.append(f"{znak} <i>Szacowany zysk: ~{profit:+,.0f} zł{ogon}</i>"
                         .replace(",", " "))
            elif not olx_price:
                L.append("❔ <i>Zysku nie liczę — za mało podobnych ofert na OLX</i>")
            else:
                L.append("❔ <i>Zysku nie liczę — brak danych do porównania</i>")

            # Opis po polsku — OZDOBNIK. Żadna liczba decyzyjna z niego nie
            # pochodzi (patrz tlumacz_opis), więc wolno go przyciąć albo pominąć.
            szkielet = "\n".join(L) + f"\n\n{listing['url']}"
            zapas = TELEGRAM_PODPIS_MAX - len(szkielet) - 20
            opis_pl = tlumacz_opis(desc_text) if zapas > 120 else None
            if opis_pl:
                if len(opis_pl) > zapas:
                    opis_pl = opis_pl[:zapas].rsplit(" ", 1)[0] + "…"
                L += ["", "<i>„" + html_mod.escape(opis_pl) + "”</i>"]

            L += ["", listing["url"]]
            msg = "\n".join(L)
            klucz = -1 if przecena_realna else (age if age is not None else 10 ** 9)
            pending_msgs.append((klucz, msg, glowne, przycisk, reszta))
            log.info(f"Nowe (score {sc}, wiek {format_age(age)}): {listing['title']}")

            # DRUGA WIADOMOŚĆ O TYM SAMYM ROWERZE (19.09.2026). Właściciel:
            # "chce obserwowane miec na dealhawku drugi raz dodane".
            #
            # To jedyne miejsce w całym repo, gdzie bot ŚWIADOMIE dubluje
            # powiadomienie - wszędzie indziej powtórka wygląda jak awaria
            # i obowiązuje "zgubić jest tańsze niż zdublować". Tutaj rachunek
            # jest odwrotny, bo to lista życzeń: pierwsza wiadomość ma być
            # szybka i bez ceregieli, a druga ma zatrzymać wzrok na rowerze,
            # po który właściciel naprawdę pojedzie.
            #
            # Osobny wpis w kolejce, nie doklejka do tamtej wiadomości, bo
            # dwa brzęknięcia widać na ekranie blokady, a jedna dłuższa
            # wiadomość znika w strumieniu razem z resztą dnia.
            #
            # TEN SAM KLUCZ CO ZWYKŁA WIADOMOŚĆ. `pending_msgs.sort` jest
            # stabilny, więc przy równych kluczach kolejność wstawiania się
            # nie zmienia i oznaczenie przychodzi ZARAZ PO swoim oryginale,
            # nie na drugim końcu paczki.
            #
            # Zdjęcia nie dokładamy: ten sam rower dwa razy z tą samą fotką
            # to nie jest dodatkowa informacja, a galeria poszła już wyżej.
            if pilny:
                warto, powody = oznacz_obserwowany(pilny, seen[listing["id"]])
                if warto:
                    pending_msgs.append((klucz, wiadomosc_oznaczenia(
                        pilny, listing["title"], "  ·  ".join(naglowek),
                        listing["url"], powody), None, None, None))
                    log.info(f"OBSERWOWANY oznaczony ({', '.join(powody)}): "
                             f"{listing['title']}")

    if new_count == 0:
        log.info("Brak nowych ogłoszeń.")

    # Alert zdrowia: 0 ogłoszeń we WSZYSTKICH wyszukiwaniach — z diagnozą
    # przyczyny (serwis leży / blokada / zmiana HTML) i bez spamu co 5 min
    check_feed_health(all_stats, total_found)

    # Monitor zdrowia parsera (#2) — alert przy spadku skuteczności odczytu
    check_parser_health(all_stats)

    # Kanarek OLX i pełna diagnostyka tylko w biegu z zapytaniami kluczowymi —
    # w pętli co minutę byłoby to tysiące zbędnych zapytań na dobę
    if not tylko_feed:
        olx_kanarek()

    # 1. Zapisz bazę (plik + git) — DOPIERO POTEM wysyłka.
    # Przerwany run = co najwyżej brak powiadomienia, nigdy duplikat.
    # W pętli push idzie tylko przy powiadomieniach albo co PUSH_CO_MIN —
    # inaczej byłby commit co minutę, a bez pushu przy powiadomieniu
    # ubity bieg wysłałby te same rowery drugi raz.
    save_seen(seen)
    global _ostatni_push
    zapisane = True
    if pending_msgs or (time.time() - _ostatni_push) > PUSH_CO_MIN * 60:
        zapisane = persist_seen_git()
        _ostatni_push = time.time()

    # NIEZAPISANY STAN ZATRZYMUJE WYSYŁKĘ (18.09.2026). Właściciel: "to jest
    # zapetlone, kilka ofert wysyla juz 40 razy".
    #
    # Zdanie nad `save_seen` stało tu od zawsze - "przerwany run = co najwyżej
    # brak powiadomienia, nigdy duplikat" - ale kod go NIE PILNOWAŁ. Wołał
    # `persist_seen_git()` i szedł dalej niezależnie od wyniku. Dopóki push
    # zawsze przechodził, nikt tego nie zauważył. Gdy zaczął się zawieszać
    # (5 prób na 6 zmierzone tego dnia), wyszło co następuje:
    #
    #   1. ogniwo zapisuje seen.json LOKALNIE i wysyła powiadomienia,
    #   2. push pada, więc plik ginie razem z runnerem,
    #   3. następne ogniwo robi `checkout main` i widzi stan SPRZED,
    #   4. te same rowery lecą znowu. I znowu. I czterdzieści razy.
    #
    # Zapis lokalny bez pusha jest w tej konstrukcji ZEREM: runner jest
    # jednorazowy, a jedyną pamięcią bota jest `main`.
    #
    # Rower NIE JEST STRACONY - jest nadal nieznany dla `seen.json`, więc
    # pierwsze ogniwo z udanym pushem wyśle go DOKŁADNIE RAZ. To działa samo,
    # bez kolejki i bez stanu do pilnowania.
    #
    # Wybór jest ten sam, co na kanale najlepszych i tam już opisany:
    # ZGUBIĆ JEST TAŃSZE NIŻ ZDUBLOWAĆ. Powtórka wygląda jak awaria bota
    # i zalewa telefon, brak powtórki jest niewidoczny i mija sam.
    if pending_msgs and not zapisane:
        log.error(f"STAN NIE TRAFIŁ NA MAIN - NIE WYSYŁAM {len(pending_msgs)} "
                  f"powiadomień, żeby nie poszły drugi raz z następnego ogniwa: "
                  + ", ".join(str(m[1])[:40] for m in pending_msgs[:3]))
        zglos_problem("brak_zapisu",
                      f"{len(pending_msgs)} powiadomień wstrzymanych - push nie przeszedł")
        pending_msgs = []

    # 2. Wyślij zaległe powiadomienia (odstęp — limit Telegrama ~1 msg/s).
    # Najświeższe idą pierwsze: przy paczce kilku ogłoszeń liczy się minuta,
    # a przy 1,2 s odstępu kolejność wysyłki jest realną przewagą.
    pending_msgs.sort(key=lambda x: x[0])
    # HAMULEC PRZED PĘTLĄ, nie w środku: sortowanie już było, więc ucinamy
    # NAJSTARSZE, a właściciel dostaje to, co najświeższe - przy powiadomieniu
    # o okazji liczy się minuta.
    _przed_hamulcem = pending_msgs
    pending_msgs, _uciete = utnij_lawine(pending_msgs)
    if _uciete:
        # Przykłady z WSTRZYMANYCH, nie z wysyłanych - właściciel ma zobaczyć,
        # co stracił, a nie to, co i tak dostanie za chwilę.
        _wstrzymane = _przed_hamulcem[len(pending_msgs):]
        log.error(f"LAWINA: {_uciete} powiadomień wstrzymanych "
                  f"(sufit {MAX_WYSYLEK_NA_BIEG} na bieg)")
        zglos_problem("lawina", f"{_uciete} powiadomień wstrzymanych")
        send_telegram(wiadomosc_o_lawinie(
            _uciete, len(pending_msgs),
            [re.sub(r"<[^>]+>", "", str(m[1])).strip().split("\n")[0]
             for m in _wstrzymane[:3]]))
    for i, (_, m, foto, przycisk, reszta) in enumerate(pending_msgs):
        if i:
            time.sleep(1.2)
        # zdjęcie roweru z podpisem; gdy się nie da — zwykły tekst, byle
        # powiadomienie doszło (ozdobnik nigdy nie może zjeść treści)
        if not send_telegram_photo(foto, m, przycisk):
            send_telegram(m, przycisk)
        # reszta galerii pod spodem, po cichu — jeden rower, jedno brzęknięcie
        if reszta:
            time.sleep(0.5)
            send_telegram_album(reszta)

    # 3. Na samym końcu: JEDYNE miejsce, które może zaalarmować o awarii.
    # Rowery mają pierwszeństwo przed narzekaniem bota na własne zdrowie.
    sprawdz_uklad()

    # Czujniki są DIAGNOSTYKĄ i nie mają prawa wywrócić skanu. Powiadomienia
    # poszły już wyżej, ale wyjątek tutaj zabrałby ze sobą zapis tempa i całą
    # ocenę zdrowia — czyli narzędzie do wykrywania awarii samo stałoby się
    # awarią. To dokładnie ten błąd, którego pilnują.
    try:
        zgubione = sprawdz_pokrycie(zrodla, seen)
        if zgubione:
            log.error(f"ZGUBIONE bez decyzji: {len(zgubione)} — "
                      + ", ".join(l.get("title", "?")[:30] for l in zgubione[:5]))
            zglos_problem("zgubione", f"{len(zgubione)} ogłoszeń bez zapisanej decyzji")

        spoznione = sprawdz_zaleglosc()
        for nazwa, wiek in spoznione:
            log.error(f"[{nazwa}] znacznik sprzed {format_age(wiek)} — jesteśmy w tyle")
        if spoznione:
            zglos_problem("zaleglosc",
                          "; ".join(f"{n}: {format_age(w)}" for n, w in spoznione))
    except Exception as e:
        log.error(f"czujniki: {e}")

    ocen_zdrowie(_problemy)


if __name__ == "__main__":
    if PETLA_MINUT <= 0:
        # Jeden krótki bieg = jeden runner = jeden adres IP = 2-3 żądania.
        # Tempo nadaje ŁAŃCUSZEK takich biegów (patrz tracker.yml), a brama
        # pilnuje, żeby nie skanowały gęściej, niż serwis znosi.
        pora, powod = czy_pora_na_skan(_stan())
        log.info(f"brama: {powod}")
        if not pora:
            process_telegram_commands()   # /wycen ma odpowiadać zawsze
        else:
            # Znacznik idzie PRZED skanem: gdyby bieg padł w połowie, następny
            # ma odczekać swoje, zamiast dobijać się do dławiącego serwisu.
            kluczowe = czy_pora_na_kluczowe(_stan())
            _stan({"ostatni_skan": time.time()})
            main(tylko_feed=not kluczowe)
            if kluczowe:
                _stan({"kluczowe_ts": time.time()})
            s = _stan()
            nowe_tempo = tempo_po_skanie(bool(s.get("padly")), s)
            _stan(nowe_tempo)
            if nowe_tempo["tempo_s"] != int(s.get("tempo_s") or TEMPO_START_S):
                log.info(f"tempo → {nowe_tempo['tempo_s']} s"
                         + (" (podstawiona strona — cofam się)" if s.get("padly") else ""))
    else:
        # Tryb zapasowy: jeden bieg żyje dłużej i sam się rytmizuje. Zostaje na
        # wypadek, gdyby łańcuszek zawiódł — wtedy wystarczy ustawić zmienną.
        koniec = time.time() + PETLA_MINUT * 60
        log.info(f"Pętla awaryjna: {PETLA_MINUT} min")
        while time.time() < koniec:
            start = time.time()
            pora, powod = czy_pora_na_skan(_stan())
            if pora:
                s0 = _stan()
                _stan({"ostatni_skan": time.time()})
                try:
                    kluczowe = czy_pora_na_kluczowe(s0)
                    main(tylko_feed=not kluczowe)
                    if kluczowe:
                        _stan({"kluczowe_ts": time.time()})
                except Exception:
                    log.exception("Skan przerwany błędem, próbuję dalej")
                s = _stan()
                _stan(tempo_po_skanie(bool(s.get("padly")), s))
            else:
                # KOMENDY MAJĄ ODPOWIADAĆ NIEZALEŻNIE OD TEMPA (18.09.2026).
                # W trybie krótkim robi to gałąź `else` przy zamkniętej bramie;
                # tutaj tego brakowało, bo `process_telegram_commands` siedzi
                # WEWNĄTRZ `main`, a `main` przy zamkniętej bramie się nie woła.
                # Po wpadce tempo cofa się do 300 s, więc `/oferta`, `/rozmiar`
                # i przycisk pod powiadomieniem milczałyby do pięciu minut -
                # dokładnie wtedy, gdy właściciel stuka w telefon i nie wie,
                # czy bot żyje. Cisza jest gorsza od błędu (reguła z 13.09).
                try:
                    process_telegram_commands()
                except Exception:
                    log.exception("komendy w pętli")
            spij = min(30.0, koniec - time.time())
            if spij > 0:
                time.sleep(spij)

    # ZGUBIONE POWIADOMIENIE MUSI BYĆ WIDAĆ BEZ TELEGRAMA (18.09.2026).
    # Jedyne miejsce, w którym ta czujka zapala się na zewnątrz. Stoi na
    # samym końcu, po zapisie i po pushu, więc czerwony kolor kosztuje
    # wyłącznie kolor.
    sys.exit(zakoncz())
