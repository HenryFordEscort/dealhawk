#!/usr/bin/env python3
"""Czujka na zoll-auktion.de - aukcje urzedow i biur rzeczy znalezionych.

PO CO TO JEST. 23.10.2026 zmierzylem ten kanal: ceny mlotkowe schodza za 0,35
mediany Kleinanzeigen (19 zakonczonych aukcji e-bike, mediana po marce
i modelu 0,35x przy 8 dopasowaniach, po samej marce 0,37x przy 10, rozrzut
0,20-0,59x; pula odniesienia to 176 580 unikalnych rowerow z dziennika rynku
z 09.07-10.10.2026). To JEDYNY ze zmierzonych kanalow DE, ktory jest TANSZY od
Kleinanzeigen - BIKE2FUTURE wyszedl 1,04x, a Vinted, eBay, Bikemarkt
i buycycle nie dawaly nic.

I ZARAZEM: w 20 zywych i 19 zakonczonych aukcjach z 23.10.2026 nie bylo ANI
JEDNEGO fully z Boschem. Z 19 zakonczonych 6 mialo w tytule "nicht fahrbereit"
albo "defekt", 3 byly konwolutami po 10 rowerow, 1 bez akumulatora. Marki to
Fischer, Telefunken, Saxxx, Engwe, Liqbike, Hercules, Pegasus, Gazelle Arroyo
i Cube Kathmandu, czyli miasto i trekking. Najblizej naszego profilu stalo
Specialized Turbo Levo SL Comp Carbon (575 EUR po 24 ofertach, wlasny silnik
Specialized) i Cube Access Hybrid EXC 500 (499 EUR po 12 ofertach, Bosch, ale
hardtail).

Czyli TANIO, ALE PUSTO - i z tego wychodzi czujka, a nie kanal zakupowy. Nie
ma sensu czytac codziennie dwudziestu miejskich rowerow bez akumulatora; ma
sens dowiedziec sie w ciagu doby, gdy raz na kwartal trafi sie tam fully
z Boschem albo Levo.

## Czego ta czujka NIE robi

- **NIE LICYTUJE.** Bot nie wystawia i nie negocjuje sam (AGENTS.md punkt 3),
  a zlozenie oferty w licytacji jest dzialaniem na zewnatrz, nie
  powiadomieniem. Czujka mowi, co sie pojawilo, i podaje adres.
- **NIE WYCENIA po polsku i nie liczy zysku.** Wycena stoi na przebiegu
  i stanie, a tutaj przebiegu nie ma, akumulator jest wprost nieprzetestowany
  i urzad pisze, ze nie ocenia dopuszczenia do ruchu. Liczba podana tu byla by
  zmyslona (regula 6), wiec ida same fakty z aukcji.
- **NIE DOTYKA plikow DealHawka** - ani `seen.json`, ani `history.jsonl`, ani
  `market-*.jsonl`. Wlasny stan, wlasny dziennik, wlasny czat. Ten sam wzorzec
  co `best_wyslane.json` w kanale najlepszych i `polka_de_stan.json` w zapasie
  polki DE.
- **NIE MA WLASNYCH REGUL O ROWERACH.** Silnik, fully i pietro modelu
  pozyczam z `tracker.py` i `najlepsze.py`, bo dwie kopie tej samej reguly
  rozjezdzaja sie przy pierwszej poprawce - to repo zaplacilo za te nauke przy
  `litera_ramy` i przy `czytaj_odrzuty`. Tutaj zostaje WYLACZNIE wiedza
  o samym serwisie: jak czytac strone aukcji i co w jego opisach znaczy stan.

## Co zmierzone o samym serwisie (23.10.2026) - nie sprawdzac od nowa

1. Kategoria "E-Bikes" ma id **1167** i caly jej stan to 20 pozycji, czyli
   jedna strona listy po 20 plus druga na zapas. Adres:
   `auktionsuebersicht.php?n0=search&t=t1&s=12` z `n1[]=1167&pagination=N`.
2. Zakonczona aukcja ZOSTAJE pod `/auktion/produkt/x/<id>` razem z cena
   koncowa i liczba ofert, ale nie ma na nie ani wyszukiwarki, ani filtra:
   `t=t1` i `t=t2` oddaja to samo, `t=t0` i `t=t3` to 301, a
   `sitemaps/produkte.xml` (2263 pozycje) zawiera wylacznie zywe. Historii
   dochodzi sie wylacznie omiataniem ID (zakres roboczy 954000-987200,
   e-bike'i to ~1% pozycji i siedza w paczkach po jednym biurze rzeczy
   znalezionych). Czujce historia nie jest potrzebna, bo patrzy w przod, ale
   stoi to tu, zeby nikt nie szukal drugi raz.
3. Strona pozycji ma w opisie gotowe pola: "Motor: Bosch Performance Line CX",
   rozmiar ramy, kolor, osprzet, miasto odbioru, land. Zwykle HTTP wystarcza,
   zero ciastek i zero Cloudflare - inaczej niz Kleinanzeigen i buycycle.
4. **PULAPKA, zmierzona na ID 986192:** slowo "defekt" w opisie potrafi
   dotyczyc LAMPKI ("Beleuchtung vorhanden - defekt!") na rowerze, ktory jest
   "optisch in einem sehr guten Zustand". Odrzucanie po samym slowie "defekt"
   w opisie wyciszyloby wlasnie ten rower, czyli najlepszego Boscha w calej
   kategorii. Stan odrzucamy wiec po TYTULE i po zwrotach o CALYM rowerze.
5. Akumulator jest nietestowany Z ZASADY, nie wyjatkowo: "Zur
   Leistungsfaehigkeit und Haltbarkeit des Akku's koennen keine Aussagen
   getroffen werden" stoi w opisie urzedowym. To nie powod do odrzucenia (bo
   wtedy kanal jest pusty w 100%), tylko powod, zeby stalo w kazdej wiadomosci.
6. Wysylki nie ma - "Versand: Nein", wylacznie odbior osobisty, a po terminie
   dochodzi oplata za przechowanie (Freiburg: 1 EUR za dobe).

## Bramki alarmu - dzwoni, gdy spelnione WSZYSTKIE naraz

1. **Silnik:** `tracker.has_known_motor`, czyli dokladnie ta bramka, ktora
   pilnuje twardego ograniczenia "tylko Bosch (plus wlasny silnik
   Specialized)". Urzad wpisuje silnik w pole "Motor:", wiec tutaj akurat
   zwykle nie trzeba zgadywac z rodziny.
2. **Fully:** `tracker.is_fully` z tytulu albo z opisu, a przy niemieckim
   opisie urzedowym dodatkowo `OPIS_FULLY` (vollgefedert, Vollfederung,
   Hinterbau). To NIE jest druga kopia bramki - `FULLY_KEYWORDS` opisuje
   tytuly z Kleinanzeigen i nie ruszam go bez pomiaru ruchu, a te trzy slowa
   padaja w opisie urzedowym, ktorego tamta lista nigdy nie widziala.
3. **Prog jakosci:** rozpoznane pietro w `topowe_modele.json`
   (`najlepsze.pietro_modelu`) ALBO rodzina Levo/Kenevo z nazwy.
4. **Stan:** tytul bez "nicht fahrbereit", "defekt", "Bastler",
   "Ersatzteilspender", "Konvolut", "ohne Akku" - zostaje przy regule "tylko
   sprawne rowery", bo kupowanie uszkodzonych do naprawy to inny biznes.
5. **Sufit ceny:** biezaca oferta ponizej `SUFIT_EUR`.

**PROG JAKOSCI JEST TU SZERSZY NIZ NA KANALE NAJLEPSZYCH I TO JEST SWIADOME.**
Tam "gorna polka" przestala wpuszczac sama 09.09.2026, bo to modele pospolite
(sam Cube Stereo 160 ma setki sztuk na rynku) i potrzebuja drugiego powodu -
ceny w dolnym decylu albo przebiegu w dolnym kwartylu. Tutaj drugi powod
niesie SAM KANAL: mediana cen mlotkowych to 0,35 mediany Kleinanzeigen
(pomiar z naglowka), a podaz to 20 pozycji naraz na cale Niemcy, w ktorych
przez caly zmierzony okres nie bylo ani jednego fully z Boschem. Zwezenie do
"szczyt" i "wysoka" odrzucilo by Cube Stereo Hybrid 160 HPC SLX, czyli rower,
po ktory ten bot istnieje, i zostawiloby kanal pusty - a regula, ktora nie
odpala, to nie regula (ta sama nauka co waga 3 na kanale najlepszych).

Pozycja dzwoni RAZ (stan pamieta wyslane). Odrzuty TAKZE ida do dziennika
razem z powodem - inaczej nie da sie potem powiedziec, czy czujka milczy, bo
nic nie bylo, czy bo bramka jest za ciasna.

    python zollauktion.py --sucho          # skan bez wysylki i bez zapisu
    python zollauktion.py                  # skan i alarm
    python zollauktion.py --pokaz-stan     # co juz widziane
"""
import argparse
import html as html_mod
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

import tracker as T
import najlepsze as N

log = logging.getLogger("zollauktion")

BAZA = "https://www.zoll-auktion.de"
KATEGORIA_EBIKE = 1167          # zmierzone 23.10.2026, punkt 1 w docstringu
LISTA = (BAZA + "/auktion/auktionsuebersicht.php"
         "?n0=search&t=t1&s=12&n1%5B%5D={kat}&pagination={strona}")
POZYCJA = BAZA + "/auktion/produkt/x/{id}"

STAN_FILE = Path("zollauktion_stan.json")
ZDARZENIA_FILE = Path("zollauktion_zdarzenia.jsonl")   # append-only

# STRON LISTY. Kategoria ma 20 pozycji (23.10.2026), czyli jedna strona po 20
# plus zapas, gdyby urzad wstawil wieksza paczke. Przy czterech przestaje to
# byc czujka i zaczyna byc skan - a wtedy trzeba policzyc ruch od nowa.
STRON_MAX = 4
PAUZA_S = 1.5                   # serwis urzedowy, zero powodu, zeby go dusic

# SUFIT CENY - ZALOZONY, nie zmierzony, i tak ma byc czytany (regula 6).
# Stoi na dwoch pomiarach z 23.10.2026: mediana ceny Bosch e-MTB w sklepie
# BIKE2FUTURE to 2 719 EUR (277 rowerow), a cala wartosc tego kanalu to kupno
# za jedna trzecia rynku. Aukcja, ktora przebila 1 200 EUR, przestaje byc
# okazja wzgledem kanalow dajacych gwarancje i wysylke, wiec dzwonek po tej
# cenie tylko kradnie uwage. Do przesuniecia jedna linijka i `--sufit`.
SUFIT_EUR = 1200

# Zwroty, po ktorych rower wypada na stanie. W TYTULE, nie w opisie - punkt 4
# docstringu mowi, co kosztowalo odrzucanie po slowie z opisu.
TYTUL_ZLY = re.compile(
    r"nicht\s+fahrbereit|defekt|bastler|ersatzteil|konvolut|ohne\s+akku|"
    r"akku\s+fehlt|unfall|rahmenbruch|nur\s+teile", re.I)
# Zwroty o CALYM rowerze - tych nie da sie przeczytac jako uwagi o lampce.
OPIS_ZLY = re.compile(
    r"nicht\s+fahrbereit|nicht\s+verkehrssicher\b|rahmen\s+(?:ist\s+)?gebrochen|"
    r"motor\s+(?:ist\s+)?defekt|akku\s+(?:ist\s+)?defekt|akku\s+fehlt|"
    r"ohne\s+akku|als\s+ersatzteilspender", re.I)

# Slowa fully z NIEMIECKIEGO OPISU URZEDOWEGO - patrz bramka 2 w docstringu.
OPIS_FULLY = re.compile(r"vollgefedert|vollfederung|hinterbaufederung|"
                        r"hinterbaud[äa]mpfer", re.I)
# Levo i Kenevo przechodza prog jakosci z nazwy, bo wlasciciel poprosil o nie
# wprost. Goly tytul "Specialized LEVO" nie trafia w zaden wpis pietra (wpisy
# to "levo r" i "levo comp"), wiec bez tego warunku wypadalby.
SPECIALIZED_SWOJ = re.compile(r"\b(?:turbo\s+)?(?:levo|kenevo)\b", re.I)


# --- pobieranie ------------------------------------------------------------

def _pobierz(url, sesja):
    r = sesja.get(url, timeout=30, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/129.0 Safari/537.36",
        "Accept-Language": "de-DE,de;q=0.9"})
    r.raise_for_status()
    return r.text


def tekst_strony(html):
    """HTML na jedna linie z pionowymi kreskami miedzy znacznikami.

    Pola na tej stronie stoja w tabelach ("Ort: | 79114 Freiburg"), wiec
    kreska jest granica pola i po niej czyta sie wartosci - ta sama mysl co
    `opis_z_polami` w `tracker.py`, tylko ze zoll-auktion oddaje tabele,
    a nie `<br>`. Skrypty i style wylatuja PRZED zamiana, bo inaczej ich
    tresc wchodzi do tekstu."""
    h = re.sub(r"<script.*?</script>|<style.*?</style>", "", html, flags=re.S)
    t = re.sub(r"<[^>]+>", "|", h)
    t = html_mod.unescape(t).replace("\xa0", " ")
    t = re.sub(r"\s+", " ", t)
    return re.sub(r"(\s*\|\s*)+", " | ", t)


def ids_z_listy(sesja, kat=KATEGORIA_EBIKE, stron_max=STRON_MAX):
    """Numery pozycji z calej kategorii, w kolejnosci podanej przez serwis."""
    znane, out = set(), []
    for strona in range(1, stron_max + 1):
        html = _pobierz(LISTA.format(kat=kat, strona=strona), sesja)
        # KAZDA KARTA PODAJE SWOJ NUMER DWA RAZY (odnosnik pod zdjeciem
        # i pod tytulem), wiec odsiewamy W PETLI, a nie po niej. Odsiew po
        # calej stronie przepuszczal oba wystapienia i pozycja byla czytana
        # dwukrotnie - zmierzone 24.10.2026: 40 "nowych" przy 20 w kategorii.
        nowe = []
        for i in re.findall(r"/auktion/produkt/[^\"/]+/(\d{6,})", html):
            if i in znane:
                continue
            znane.add(i)
            nowe.append(i)
            out.append(i)
        log.info(f"lista strona {strona}: {len(nowe)} nowych (razem {len(out)})")
        # KONIEC KATEGORII POZNAJEMY PO STRONIE BEZ ANI JEDNEJ NOWEJ POZYCJI,
        # nie po liczbie kart - ta sama pulapka, ktora przy zapasie polki DE
        # obcinala pasmo na trzeciej stronie z trzydziestu czterech.
        if not nowe:
            break
        time.sleep(PAUZA_S)
    return out


def czytaj_pozycje(i, sesja):
    """Fakty ze strony aukcji. Czego nie ma na stronie, tego nie zgadujemy."""
    t = tekst_strony(_pobierz(POZYCJA.format(id=i), sesja))
    if f"ID {i}" not in t:
        return None                      # zdjete albo cudza strona
    def pole(wz, grupa=1):
        m = re.search(wz, t)
        return m.group(grupa).strip() if m else None
    opis = pole(r"Gegenstandsbeschreibung \|(.*?)\| Besichtigung") or ""
    cena = pole(r"\| ([\d.]+),\d\d EUR")
    gebote = pole(r"\| (\d+) \| Gebote")
    return {
        "id": i,
        "tytul": pole(r"Zoll-Auktion - (.*?) \(ID " + re.escape(i) + r"\)"),
        "cena": int(cena.replace(".", "")) if cena else None,
        "gebote": int(gebote) if gebote else None,
        "koniec": pole(r"Auktionsende: \| ([^|]{1,40})"),
        "zostalo": pole(r"verbleibende Zeit: \| ([^|]{1,40})"),
        "anbieter": pole(r"Anbieter: \| ([^|]{1,60})"),
        "ort": pole(r"Ort: \| ([^|]{1,60})"),
        "land": pole(r"Bundesland: \| ([^|]{1,40})"),
        "wysylka": pole(r"Versand: \| ([^|]{1,10})"),
        # POLE "Motor:", NIE SLOWO "Motor" W ZDANIU. Opis urzedowy wypisuje
        # pola oddzielone kreskami ("| Motor: Bosch Performance Line CX |"),
        # a prozy w nim tez nie brakuje: bez wymogu dwukropka i kreski przed
        # nazwa pola wzorzec wyciagal "Motor scheint zu funktionieren"
        # z aukcji 986134 i wpisywal to jako marke silnika (24.10.2026).
        "motor": pole(r"\|\s*Motor:\s*([^|]{1,60})"),
        "opis": opis.strip()[:2500],
        "url": POZYCJA.format(id=i),
    }


# --- werdykt ---------------------------------------------------------------

def czy_fully(poz):
    """(bool, powod). Bramka `tracker.is_fully` plus slowa z opisu urzedowego."""
    tytul = poz.get("tytul") or ""
    opis = poz.get("opis") or ""
    if T.is_fully(tytul):
        return True, "fully z tytulu (bramka trackera)"
    if OPIS_FULLY.search(opis):
        return True, f"opis mowi {OPIS_FULLY.search(opis).group(0)}"
    if T.is_fully(opis):
        return True, "fully z opisu (bramka trackera)"
    return False, "nic nie mowi, ze to fully"


def ocen(poz, topowe, sufit=SUFIT_EUR):
    """{'alarm': bool, 'powody': [...], 'weta': [...]} - jawnie, nie samo tak/nie."""
    tytul = poz.get("tytul") or ""
    opis = poz.get("opis") or ""
    # Pole "Motor:" z opisu urzedowego dokladamy do tekstu, zeby bramka
    # silnika zobaczyla to, co urzad naprawde napisal.
    tekst_silnika = " ".join(x for x in (opis, poz.get("motor") or "") if x)
    powody, weta = [], []

    if T.has_known_motor(tytul, tekst_silnika):
        powody.append("silnik: Bosch albo wlasny Specialized (bramka trackera)"
                      + (f", pole Motor: {poz['motor'].strip()[:40]}"
                         if poz.get("motor") else ""))
    else:
        weta.append("silnik: nie Bosch albo urzad nie podal i rodzina nie "
                    "rozstrzyga")

    fully, czemu = czy_fully(poz)
    (powody if fully else weta).append(czemu)

    wpis = N.pietro_modelu(tytul, topowe) or N.pietro_modelu(opis[:200], topowe)
    if SPECIALIZED_SWOJ.search(tytul):
        powody.append("Levo/Kenevo z nazwy - wprost na zyczenie wlasciciela")
    elif wpis:
        powody.append(f"pietro {wpis['pietro']} ({wpis['model']})")
    else:
        weta.append("model nie jest ani Levo, ani rozpoznanym pietrem "
                    "z topowe_modele.json")

    if TYTUL_ZLY.search(tytul):
        weta.append(f"stan odrzucony po tytule: {TYTUL_ZLY.search(tytul).group(0)}")
    elif OPIS_ZLY.search(opis):
        weta.append(f"stan odrzucony po opisie: {OPIS_ZLY.search(opis).group(0)}")

    cena = poz.get("cena")
    if cena is None:
        weta.append("brak ceny na stronie")
    elif cena > sufit:
        weta.append(f"{cena} EUR powyzej sufitu {sufit} EUR")

    return {"alarm": not weta, "powody": powody, "weta": weta,
            "pietro": (wpis or {}).get("pietro")}


# --- wiadomosc -------------------------------------------------------------

def wiadomosc(poz, werdykt):
    """Same fakty z aukcji plus to, czego urzad NIE gwarantuje.

    Bez wyceny i bez gotowego tekstu do sprzedawcy - po drugiej stronie nie ma
    sprzedawcy, jest urzad i formularz licytacji. Sekund warte sa tu dwie
    rzeczy: termin konca i miasto odbioru."""
    esc = lambda s: html_mod.escape(str(s or ""))
    L = [f"🔔 <b>zoll-auktion</b>  ·  {esc(poz.get('tytul'))}", ""]
    glowa = []
    if poz.get("cena") is not None:
        glowa.append(f"{poz['cena']} EUR")
    if poz.get("gebote") is not None:
        glowa.append(f"{poz['gebote']} ofert")
    if poz.get("koniec"):
        glowa.append(f"koniec {esc(poz['koniec'])}")
    if glowa:
        L.append("  ·  ".join(glowa))
    if poz.get("ort"):
        L.append(f"odbior: {esc(poz['ort'])}"
                 + (f", {esc(poz['land'])}" if poz.get("land") else ""))
    if poz.get("anbieter"):
        L.append(f"wystawia: {esc(poz['anbieter'])}")
    L.append(poz["url"])
    if werdykt["powody"]:
        L += ["", "Dlaczego dzwoni:"] + [f"· {esc(p)}" for p in werdykt["powody"]]
    # TO NIE JEST OZDOBA. Urzad pisze wprost, ze nie bada akumulatora ani
    # dopuszczenia do ruchu, a to jest cala rozpietosc miedzy okazja i zlomem.
    L += ["",
          "Czego urzad nie gwarantuje: akumulator nieprzetestowany, brak oceny "
          "dopuszczenia do ruchu, mozliwe ukryte uszkodzenia.",
          "Wysylki nie ma, wylacznie odbior osobisty, a po terminie leci oplata "
          "za przechowanie.",
          "Licytuje wlasciciel - bot ofert nie sklada."]
    return "\n".join(L)


def wyslij(tekst):
    """Wysylka na kanal alarmowy PRZEZ `najlepsze.wyslij`.

    Jeden listonosz dla calego repo: tamta funkcja ma juz ponawianie, zapas
    bez `parse_mode` po bledzie skladni HTML i rozpoznanie tokenu wklejonego
    w pole id czatu. Bez `TELEGRAM_ALARM_CHAT_ID` (albo BEST jako zapas)
    czujka nic nie wysyla i konczy sie zerem - lokalne biegi i testy maja
    dzialac bez czatu."""
    return N.wyslij(tekst, chat_id=N.ALARM_CHAT_ID or N.BEST_CHAT_ID,
                    token=N.ALARM_BOT_TOKEN)


# --- stan i dziennik -------------------------------------------------------

def load_stan(plik=None):
    plik = plik or STAN_FILE           # NIGDY w domyslnym argumencie
    try:
        return json.loads(plik.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as e:
        # NIE zerujemy stanu po bledzie odczytu: pusty stan znaczy "wszystko
        # nowe", czyli powtorka wszystkich dzwonkow (regula 7).
        log.error(f"{plik} nie do odczytania ({e}) - koncze, zeby nie dzwonic "
                  f"drugi raz")
        raise


def zapisz_stan(stan, plik=None):
    plik = plik or STAN_FILE
    plik.write_text(json.dumps(stan, ensure_ascii=False, indent=1),
                    encoding="utf-8")


def dopisz_zdarzenie(rek, plik=None):
    """Append-only, nigdy nie przycinany. Dziennik odpowiada na pytanie, czy
    czujka milczy, bo nic nie bylo, czy bo bramka jest za ciasna - wiec ida
    tu TAKZE odrzuty."""
    plik = plik or ZDARZENIA_FILE
    with plik.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rek, ensure_ascii=False) + "\n")


# --- przebieg --------------------------------------------------------------

def skan(sucho=False, stron_max=STRON_MAX, sufit=SUFIT_EUR, sesja=None):
    sesja = sesja or requests.Session()
    topowe = N.load_topowe()
    stan = load_stan()
    teraz = datetime.now(timezone.utc).isoformat(timespec="seconds")
    pozycje = ids_z_listy(sesja, stron_max=stron_max)
    nowe = [i for i in pozycje if i not in stan]
    log.info(f"kategoria {KATEGORIA_EBIKE}: {len(pozycje)} pozycji, "
             f"{len(nowe)} nowych, {len(pozycje) - len(nowe)} juz widzianych")
    dzwonki = 0
    for i in nowe:
        time.sleep(PAUZA_S)
        try:
            poz = czytaj_pozycje(i, sesja)
        except Exception as e:
            # Nieudany odczyt NIE zapisuje stanu - pozycja wraca w nastepnym
            # biegu. Aukcje stoja po kilka tygodni, wiec jest na to czas.
            log.error(f"{i}: strona nie do odczytania ({e}) - zostawiam na "
                      f"nastepny bieg")
            continue
        if not poz:
            log.info(f"{i}: zdjete")
            stan[i] = {"widziane": teraz, "werdykt": "zdjete"}
            continue
        w = ocen(poz, topowe, sufit=sufit)
        # NA SUCHO NIE ZAPISUJEMY NICZEGO, takze dziennika. Pierwsza wersja
        # dopisywala go zawsze i trzy przebiegi probne zrobily 60 wierszy
        # z 20 aukcji (zmierzone 24.10.2026) - czyli dokladnie to, przed czym
        # ostrzega regula 5: dziennik ma liczyc ROWERY, nie moje uruchomienia.
        if not sucho:
            dopisz_zdarzenie({"ts": teraz, "id": i, "tytul": poz["tytul"],
                              "cena": poz["cena"], "gebote": poz["gebote"],
                              "koniec": poz["koniec"], "ort": poz["ort"],
                              "pietro": w["pietro"], "alarm": w["alarm"],
                              "powody": w["powody"], "weta": w["weta"]})
        stan[i] = {"widziane": teraz, "tytul": poz["tytul"], "cena": poz["cena"],
                   "werdykt": "alarm" if w["alarm"] else "odrzut",
                   "weta": w["weta"], "wyslane": False}
        log.info(f"{i} [{'DZWONEK' if w['alarm'] else 'cisza'}] "
                 f"{(poz['tytul'] or '')[:60]} | {poz['cena']} EUR | "
                 f"{'; '.join(w['weta']) or '; '.join(w['powody'])}")
        if not w["alarm"]:
            continue
        dzwonki += 1
        if sucho:
            print("\n" + wiadomosc(poz, w) + "\n")
            continue
        # ZAPIS PRZED WYSYLKA, jak w `tracker.main` i w kanale najlepszych:
        # przerwany bieg ma kosztowac najwyzej brak powiadomienia, nigdy
        # powtorke. Nieudana wysylka tez zostaje oznaczona, inaczej wracalaby
        # co bieg i zamienila jedna cicha strate w petle halasu.
        stan[i]["wyslane"] = True
        zapisz_stan(stan)
        wyslij(wiadomosc(poz, w))
    if not sucho:
        zapisz_stan(stan)
    log.info(f"koniec skanu: {dzwonki} dzwonkow na {len(nowe)} nowych pozycji")
    return dzwonki


def main():
    p = argparse.ArgumentParser(description="Czujka na zoll-auktion.de")
    p.add_argument("--sucho", action="store_true",
                   help="skan bez wysylki i bez zapisu stanu")
    p.add_argument("--stron", type=int, default=STRON_MAX)
    p.add_argument("--sufit", type=int, default=SUFIT_EUR)
    p.add_argument("--pokaz-stan", action="store_true")
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if a.pokaz_stan:
        stan = load_stan()
        print(f"{len(stan)} pozycji w stanie")
        for i, w in sorted(stan.items(), key=lambda kv: kv[1].get("widziane", "")):
            print(f"  {i} {w.get('werdykt', '?'):<7} "
                  f"{str(w.get('cena')):>5} EUR {(w.get('tytul') or '')[:60]}")
        return 0
    try:
        skan(sucho=a.sucho, stron_max=a.stron, sufit=a.sufit)
    except Exception as e:
        log.error(f"skan padl: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
