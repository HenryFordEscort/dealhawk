#!/usr/bin/env python3
"""Dozorca Kleinanzeigen — ile ogłoszenie realnie wisi, zanim zejdzie.

ZASADA NACZELNA (ta sama co w dozorca.py): zapisujemy to, co WIDZIELIŚMY,
nigdy tego, co z tego wynika. Dziennik mówi "ogłoszenie X żyło 25.08 o 14:00
za 2 600 €" — nie "ten rower sprzedał się w 5 dni". Wnioski liczy warstwa
wyżej i można je przeliczyć od zera, gdy reguły okażą się błędne.

DLACZEGO OSOBNY PLIK, A NIE DOPISEK DO tracker.py:
Skan nowych ogłoszeń jest ścieżką gorącą — od niego zależy, czy w ogóle
zobaczysz okazję. Ten moduł chodzi wolno i wykonuje setki zapytań do stron
ogłoszeń, więc nie wolno mu dzielić z tamtym ani pętli, ani stanu, ani limitu
zapytań. Czyta seen.json i market.jsonl WYŁĄCZNIE do odczytu i nie zapisuje
do żadnego pliku trackera.

JAK POZNAĆ ZDJĘTE OGŁOSZENIE (zmierzone 25.08.2026 na 5 lipcowych i 2 żywych):
Kleinanzeigen NIE zwraca 404 ani 410. Zdjęte ogłoszenie to HTTP 200 i ciche
przekierowanie na listę kategorii w miejscowości sprzedawcy:
    /s-anzeige/<tytul>/<id>  ->  /s-fahrraeder/<miasto>/c217l<id>
Sygnał ma trzy niezależne potwierdzenia: adres końcowy bez "/s-anzeige/",
brak elementu #viewad-price, oraz skok rozmiaru strony (żywe 224-228 kB,
zdjęte 289-306 kB — strona kategorii jest większa od ogłoszenia).
Sprawdzanie po samym 404 nie wykryłoby ANI JEDNEGO zdjęcia.

Pliki:
  zdarzenia_de/de-RRRR-MM.jsonl — dziennik zdarzeń, append-only, NIGDY nie kasowany
  de_stan.json                  — bieżący stan (kto żyje); ODTWARZALNY z dziennika

Uruchom: python3 dozorca_de.py [ile_ogloszen]
"""
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import tracker

STAN_FILE = Path("de_stan.json")
ZDARZENIA_DIR = Path("zdarzenia_de")

# Ogłoszenie prywatne na Kleinanzeigen wisi do ~60 dni i wygasa samo. Powyżej
# tego progu zniknięcie jest podejrzane o wygaśnięcie, nie o sprzedaż — ale
# to rozstrzyga warstwa wniosków, tutaj tylko zapisujemy fakt.
MAX_SPRAWDZEN_NA_PRZEBIEG = 60      # grzeczność wobec serwisu + czas przebiegu
ODSTEP_S = 1.5                      # przerwa między zapytaniami
PROB_ZANIM_ODPUSCIMY = 4            # tyle nieudanych odczytów i przestajemy pytać
SPRAWDZAJ_CO_H = 20                 # nie ma sensu pytać częściej niż raz na dobę

# Znaczniki stanu oferty w HTML-u, KTÓRY DOSTAJE BOT. Uzasadnienie w ocen_strone.
KONTAKT_WYLACZONY = "icon-mail-disabled"
KONTAKT_AKTYWNY = 'id="viewad-contact-button-login"' 


def teraz_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M")


# --- CZYSTE FUNKCJE (bez sieci, bez plików) --------------------------------

def ocen_strone(status, adres_koncowy, html):
    """Czy ogłoszenie żyje. Zwraca 'zyje' | 'zdjete' | 'nieznane'.

    'nieznane' to NIE jest "zniknęło". Nieudany odczyt zapisany jako zniknięcie
    zamieniłby awarię sieci w masową wyprzedaż — dokładnie ten błąd kosztował
    kiedyś skasowanie danych OLX. W razie wątpliwości: 'nieznane'."""
    if status != 200 or not html:
        return "nieznane"
    if adres_koncowy and "/s-anzeige/" not in adres_koncowy:
        return "zdjete"                      # przekierowanie na kategorię
    if re.search(r"nicht mehr verf(?:ü|ue)gbar|wurde gel(?:ö|oe)scht"
                 r"|Anzeige ist nicht mehr", html, re.I):
        return "zdjete"
    # Skasowane PRZEZ SPRZEDAWCĘ ogłoszenie nie przekierowuje i nie niesie
    # żadnej z powyższych fraz. Renderuje się w całości: tytuł, cena, opis,
    # zdjęcia. Plakietkę "Gelöscht" widać w przeglądarce, ale w odpowiedzi dla
    # cloudscrapera NIE MA JEJ W OGÓLE - serwis oddaje botowi okrojoną stronę
    # (zmierzone 29.08.2026 na tej samej ofercie: 245 kB dla bota, 2779 kB
    # w przeglądarce). Szukanie tekstu jest tu ślepe z definicji.
    # Jedyna różnica, którą bot widzi: przyciski "napisz wiadomość" i "obserwuj"
    # są WYŁĄCZONE. Do martwej oferty nie da się napisać.
    # ZMIERZONE 29.08.2026 na 24 ogłoszeniach o stanie ustalonym w przeglądarce
    # (9 żywych, 15 skasowanych): rozdziela zbiór BEZBŁĘDNIE, zero pomyłek
    # w obie strony. Poprzednia reguła "jest cena, czyli żyje" przepuszczała
    # 15 z tych 24 jako żywe.
    # Sprawdzane są OBA znaczniki, nie jeden: gdy serwis zmieni nazwę klasy,
    # ma wyjść "nieznane", a nie cicha masowa wyprzedaż.
    kontakt_martwy = KONTAKT_WYLACZONY in html
    kontakt_zywy = KONTAKT_AKTYWNY in html
    if kontakt_martwy and not kontakt_zywy:
        return "zdjete"
    if kontakt_zywy and not kontakt_martwy:
        return "zyje"
    return "nieznane"                        # antybot, obcięta strona, przebudowa


def czy_parser_oslepl(wyniki):
    """REGUŁA 7: cisza na WSZYSTKICH stronach to przebudowa serwisu, nie rynek.

    Gdy cały przebieg wraca jednym stanem, to znaczniki przestały pasować,
    a nie że rynek naraz opustoszał. Zwraca powód albo None. Czysta funkcja."""
    stany = [w.get("stan") for w in wyniki.values()]
    if len(stany) < 5:
        return None                     # za mało obserwacji na jakikolwiek wniosek
    if all(s == "nieznane" for s in stany):
        return f"wszystkie {len(stany)} stron nieczytelne"
    if all(s == "zdjete" for s in stany):
        return f"wszystkie {len(stany)} ogłoszeń naraz zdjęte"
    return None


def cena_ze_strony(html):
    """Aktualna cena z żywej strony ogłoszenia. None gdy nieczytelna."""
    m = tracker.CENA_ZE_STRONY.search(html or "")
    return tracker.parse_price(" ".join(m.group(1).split())) if m else None


def wykryj_zdarzenia_de(stan, wyniki, teraz):
    """SERCE DOZORCY — czysta funkcja, testowalna bez sieci.

    stan:    {id: rekord} — co wiedzieliśmy do tej pory
    wyniki:  {id: {"stan": 'zyje'|'zdjete'|'nieznane', "p":…, "rez":…,
                   "url":…, "wyst":…}} — co zobaczyliśmy w tym przebiegu

    Zwraca (lista_zdarzeń, nowy_stan). Zdarzenie 'zyje' zapisujemy najwyżej RAZ
    NA DOBĘ — codzienne potwierdzenie jest potrzebne, żeby stan dało się odtworzyć
    z samego dziennika, ale częstsze tylko zapchałoby plik."""
    zdarzenia = []
    stan = {k: dict(v) for k, v in stan.items()}

    for oid, ob in wyniki.items():
        rec = stan.get(oid)
        wynik = ob.get("stan")

        if wynik == "nieznane":
            # Fakt "nie udało się sprawdzić" też jest faktem i musi być w dzienniku,
            # inaczej dziura w obserwacji wygląda jak cisza, a nie jak brak danych.
            if rec is not None:
                rec["prob"] = rec.get("prob", 0) + 1
                if rec["prob"] >= PROB_ZANIM_ODPUSCIMY:
                    rec["odpuszczone"] = True
            zdarzenia.append({"ts": teraz, "ev": "nie_sprawdzono", "id": oid})
            continue

        if rec is None:                       # pierwszy kontakt
            rec = {"url": ob.get("url"), "wyst": ob.get("wyst"),
                   "pierwszy": teraz, "ostatni_zywy": None,
                   "p": ob.get("p"), "p0": ob.get("p"), "rez": False, "prob": 0}
            stan[oid] = rec
            zdarzenia.append({"ts": teraz, "ev": "nowa", "id": oid,
                              "url": rec["url"], "wyst": rec["wyst"],
                              "p": rec["p"]})
            if wynik == "zdjete":
                # Zdjęte już przy pierwszym kontakcie: wiemy, że nie żyje, ale
                # NIE wiemy kiedy zeszło. Warstwa wniosków musi to widzieć.
                rec["zdjete"] = teraz
                zdarzenia.append({"ts": teraz, "ev": "znikla", "id": oid,
                                  "ostatni_zywy": None, "wyst": rec.get("wyst")})
                continue

        rec.pop("prob", None)
        rec["prob"] = 0

        if wynik == "zdjete":
            # Cena MUSI iść do dziennika razem ze zniknięciem. Stan jest
            # odtwarzalny z dziennika i tylko z niego, a "zeszło" bez kwoty
            # nie odpowiada na jedyne pytanie, dla którego to zbieramy:
            # po jakiej cenie oferta przestała wisieć. Kwoty nie da się
            # dobrać później - martwa strona jej już nie poda.
            # DATA WYSTAWIENIA IDZIE RAZEM ZE ZNIKNIECIEM, z tego samego
            # powodu co cena dwie linie wyzej: stan odtwarza sie z dziennika
            # i tylko z niego, a martwa strona nie poda juz ani kwoty, ani
            # daty. Bez tego pola "znikla" mowi wylacznie, ze cos zeszlo, i nie
            # ma od czego liczyc, ile to wisialo. Zmierzone 25.09.2026: z 868
            # dotychczasowych zniknięć data wystawienia jest do odzyskania dla
            # ZERA - te dane sa bezpowrotnie nieme i dlatego to pole musi tu byc.
            zdarzenia.append({"ts": teraz, "ev": "znikla", "id": oid,
                              "ostatni_zywy": rec.get("ostatni_zywy"),
                              "p": rec.get("p"), "p0": rec.get("p0"),
                              "wyst": rec.get("wyst")})
            rec["zdjete"] = teraz
            continue

        # --- żyje ---
        if ob.get("p") is not None and rec.get("p") is not None \
                and ob["p"] != rec["p"]:
            zdarzenia.append({"ts": teraz, "ev": "cena", "id": oid,
                              "p": ob["p"], "p_stara": rec["p"]})
        if ob.get("p") is not None:
            rec["p"] = ob["p"]
            rec.setdefault("p0", ob["p"])

        if ob.get("rez") and not rec.get("rez"):
            # Rezerwacja to najmocniejszy dostępny sygnał sprzedaży. Zniknięcie
            # PO rezerwacji znaczy sprzedane; samo zniknięcie nie znaczy nic.
            zdarzenia.append({"ts": teraz, "ev": "rezerwacja", "id": oid})
        if ob.get("rez") is not None:
            rec["rez"] = bool(ob["rez"])

        if (rec.get("ostatni_zywy") or "")[:10] != teraz[:10]:
            zdarzenia.append({"ts": teraz, "ev": "zyje", "id": oid,
                              "p": rec.get("p"), "rez": rec.get("rez")})
        rec["ostatni_zywy"] = teraz

    return zdarzenia, stan


# Ile budzetu przebiegu idzie na ogloszenia z DATA WYSTAWIENIA. Reszta na
# pozostale, po najdawniej sprawdzonych.
UDZIAL_MIERZALNYCH = 0.7


def do_sprawdzenia(stan, teraz, limit=MAX_SPRAWDZEN_NA_PRZEBIEG):
    """Które ogłoszenia odpytać w tym przebiegu. Zdjęte i odpuszczone odpadają,
    reszta czeka swoje SPRAWDZAJ_CO_H.

    KOLEJNOSC DZIELONA, nie sama "najdawniej sprawdzone" (zmiana 25.09.2026).
    Powod: sprawdzenie ogloszenia BEZ daty wystawienia daje zdarzenie, z ktorego
    nie policzy sie wieku, wiec do krzywej przezycia nie wnosi nic. A kolejka po
    samym czasie ustawiala na przodzie wlasnie takie: najstarsza zaleglosc to
    ogloszenia sprzed 22.08, czyli sprzed czytania daty z kanalu. Zmierzone
    25.09.2026: 868 zniknięć w dzienniku i data wystawienia do odzyskania dla
    ZERA z nich, a odstep od naszego widzenia do zgonu mial mediane 63 dni przy
    kwartylach 59 i 68 - tak ciasny rozklad to podpis przerabianej kolejki,
    nie rozkladu zycia ofert.

    Zaleglosci nie wolno jednak zaglodzic: to dla niej dozorca DE powstal
    (13.09.2026 w `/dojrzale` 7 z 8 ogloszen bylo juz zdjetych). Stad podzial
    budzetu, a nie wybor jednego albo drugiego."""
    mierzalne, reszta = [], []
    for oid, rec in stan.items():
        if rec.get("zdjete") or rec.get("odpuszczone"):
            continue
        ost = rec.get("ostatni_zywy") or rec.get("pierwszy") or ""
        godz = _godzin_od(ost, teraz)
        if godz is None or godz >= SPRAWDZAJ_CO_H:
            (mierzalne if wyst_jest_faktem(rec.get("wyst")) else reszta).append((ost, oid))
    mierzalne.sort()
    reszta.sort()
    ile_m = int(limit * UDZIAL_MIERZALNYCH)
    wybor = [oid for _, oid in mierzalne[:ile_m]]
    wybor += [oid for _, oid in reszta[:limit - len(wybor)]]
    # niewykorzystany budzet jednej grupy przechodzi na druga, zeby przebieg
    # nigdy nie chodzil na pol gwizdka
    if len(wybor) < limit:
        wybor += [oid for _, oid in mierzalne[ile_m:limit - len(wybor) + ile_m]]
    return wybor[:limit]


def _godzin_od(a, b):
    try:
        ta = datetime.strptime(a[:16], "%Y-%m-%dT%H:%M")
        tb = datetime.strptime(b[:16], "%Y-%m-%dT%H:%M")
    except (ValueError, TypeError):
        return None
    return (tb - ta).total_seconds() / 3600.0


# --- WEJŚCIE/WYJŚCIE -------------------------------------------------------

def sprawdz_ogloszenie(url):
    """Jedno zapytanie do strony ogłoszenia. Nigdy nie rzuca wyjątkiem —
    każdy problem zwraca 'nieznane', bo brak wiedzy to nie jest zniknięcie."""
    try:
        r = tracker.scraper.get(url, timeout=20, allow_redirects=True)
        html = r.text if r.status_code == 200 else ""
        st = ocen_strone(r.status_code, r.url, html)
        if st != "zyje":
            return {"stan": st}
        return {"stan": "zyje", "p": cena_ze_strony(html),
                "rez": tracker.czy_zarezerwowane(html, "", "")}
    except Exception as e:
        tracker.log.info(f"dozorca_de: nie sprawdzono {url[:60]}: {e}")
        return {"stan": "nieznane"}


def wczytaj_stan():
    if STAN_FILE.exists():
        try:
            return json.loads(STAN_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def zapisz_zdarzenia(zdarzenia):
    if not zdarzenia:
        return
    ZDARZENIA_DIR.mkdir(exist_ok=True)
    plik = ZDARZENIA_DIR / f"de-{datetime.now(timezone.utc):%Y-%m}.jsonl"
    with plik.open("a", encoding="utf-8") as f:
        for z in zdarzenia:
            f.write(json.dumps(z, ensure_ascii=False) + "\n")


def wyst_jest_faktem(w) -> bool:
    """Czy `wyst` to naprawde znacznik wystawienia z Kleinanzeigen.

    FAKT ma godzine ("2026-09-25T20:37:00+02:00") - tak wyglada to, co kanal
    czyta ze strony listy. ZALOZENIE to gola data ("2026-06-17"), bo tyle
    niesie `seen.json` i tyle podstawialo tu dawne dosiewanie.

    Rozpoznanie MUSI isc po godzinie, nie po tym, czy data rowna sie naszemu
    pierwszemu widzeniu. Kanal lapie niemieckie ogloszenia w godzinach, wiec
    prawdziwy znacznik zwykle wypada tego samego dnia co nasze widzenie -
    test po rownosci uznal 2331 faktow za podstawione (zmierzone 25.09.2026)."""
    return isinstance(w, str) and "T" in w


def wyst_z_dziennika_rynku() -> dict:
    """{id: znacznik wystawienia} z dziennika rynku. Same FAKTY - wpisy bez
    godziny sa pomijane, zeby stara zgadywanka nie wrocila ta droga.

    Dziennik rynku jest tu jedynym zrodlem daty wystawienia i tak ma zostac.
    Strona ogloszenia jej nie oddaje: pobranie 25.09.2026 wrocilo HTTP 200
    z wlasciwym tytulem, ale bez tresci ogloszenia (zero wystapien
    "Eingestellt", 19 razy "consent"), czyli w lzejszym ukladzie za zgoda
    na ciasteczka. Doklejanie tu drugiego, kruchego odczytu strony tylko
    po date nie ma sensu, skoro kanal podaje ja dla ~100% nowych ofert."""
    out = {}
    try:
        for line in tracker.market_wiersze():
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("id") and wyst_jest_faktem(r.get("wyst")):
                out.setdefault(r["id"], r["wyst"])
    except Exception as e:
        tracker.log.warning(f"dozorca_de: nie odczytalem dziennika rynku ({e}) - "
                            f"daty wystawienia zostaja takie, jakie sa")
    return out


def odkaz_wyst(stan, z_rynku: dict) -> dict:
    """Wyrzuca ze stanu podstawione daty wystawienia i dokłada prawdziwe.

    Chodzi co przebieg, nie raz: dziennik rynku rosnie, wiec ogloszenie bez
    daty dzis moze ja miec za tydzien. Samonaprawa zamiast jednorazowego
    skryptu - narzedzie odpalone raz zostawia ten sam brud w kazdym pliku,
    ktory powstanie potem.

    Zwraca licznik {'uzupelnione', 'wyczyszczone'}. Nie rusza `pierwszy`:
    nasza data pierwszego widzenia jest osobnym, prawdziwym faktem i ma
    zostac tam, gdzie jest."""
    licz = {"uzupelnione": 0, "wyczyszczone": 0}
    for oid, rec in stan.items():
        if not isinstance(rec, dict):
            continue
        prawda = z_rynku.get(oid)
        if prawda and not wyst_jest_faktem(rec.get("wyst")):
            rec["wyst"] = prawda
            licz["uzupelnione"] += 1
        elif rec.get("wyst") is not None and not wyst_jest_faktem(rec["wyst"]):
            # Nie ma czym zastapic, wiec zostaje "nie wiem". Zostawienie tu
            # naszej daty daloby wiek liczony od naszego widzenia - dokladnie
            # ten blad, ktory po stronie polskiej robil z 45 dni 9.
            rec["wyst"] = None
            licz["wyczyszczone"] += 1
    return licz


def zasiej_ze_sledzonych(stan):
    """Dosiewa stan o ogłoszenia, które bot już widział (seen.json ma URL-e,
    market.jsonl datę wystawienia). Czyta OBA PLIKI TYLKO DO ODCZYTU."""
    wyst = {}
    try:
    # KAWAŁKI MIESIĘCZNE od 18.09.2026: `market.jsonl` to dziś tylko
    # najstarszy kawałek dziennika, więc czytamy przez trackera, który
    # zna je wszystkie. Sam ten plik dałby ułamek danych, a wynik nadal
    # wyglądałby wiarygodnie i nikt by tego nie zauważył (reguła 7).
        for line in tracker.market_wiersze():
            try:
                r = json.loads(line)
            except Exception:
                continue
            # tylko FAKTY: wpis bez godziny to gola data i nie jest
            # znacznikiem wystawienia (patrz wyst_jest_faktem)
            if r.get("id") and wyst_jest_faktem(r.get("wyst")):
                wyst.setdefault(r["id"], r["wyst"])
    except Exception:
        pass

    dodane = 0
    try:
        # Przez `load_seen`, nie po nazwie pliku: od 19.09.2026 stan bywa
        # w kawałkach, a moduł czytający sam `seen.json` dostałby ułamek
        # danych i NIE KRZYKNĄŁBY - wynik nadal wyglądałby wiarygodnie.
        seen = tracker.load_seen()
    except Exception:
        return 0
    for oid, v in seen.items():
        if not isinstance(v, dict) or not v.get("url") or oid in stan:
            continue
        # `wyst` BEZ podkladki z naszej daty. Do 25.09.2026 stalo tu
        # `wyst.get(oid) or v.get("date")`, czyli przy braku faktu wpisywana
        # byla data NASZEGO pierwszego widzenia - i nic jej potem nie
        # odrozniało od prawdziwej. 1108 z 3439 wpisow (32%) niosło tak
        # zalozenie w przebraniu faktu. Brak znaczy "nie wiem".
        stan[oid] = {"url": v["url"], "wyst": wyst.get(oid),
                     "pierwszy": (v.get("date") or "") + "T00:00",
                     "ostatni_zywy": None, "p": v.get("price_num"),
                     "p0": v.get("price_num"), "rez": False, "prob": 0}
        dodane += 1
    return dodane


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else MAX_SPRAWDZEN_NA_PRZEBIEG
    teraz = teraz_utc()
    stan = wczytaj_stan()
    dodane = zasiej_ze_sledzonych(stan)
    odkazone = odkaz_wyst(stan, wyst_z_dziennika_rynku())
    kolejka = do_sprawdzenia(stan, teraz, limit)
    faktow = sum(1 for r in stan.values()
                 if isinstance(r, dict) and wyst_jest_faktem(r.get("wyst")))
    print(f"dozorca_de: w stanie {len(stan)} ogłoszeń (dosiano {dodane}), "
          f"sprawdzam {len(kolejka)}")
    print(f"  data wystawienia: {faktow} faktow z {len(stan)} "
          f"(uzupelnione {odkazone['uzupelnione']}, "
          f"wyczyszczone z podstawionych {odkazone['wyczyszczone']})")

    wyniki = {}
    for i, oid in enumerate(kolejka, 1):
        rec = stan[oid]
        wynik = sprawdz_ogloszenie(rec["url"])
        wynik["url"] = rec.get("url")
        wynik["wyst"] = rec.get("wyst")
        wyniki[oid] = wynik
        if i % 10 == 0:
            print(f"  ...{i}/{len(kolejka)}")
        time.sleep(ODSTEP_S)

    alarm = czy_parser_oslepl(wyniki)
    if alarm:
        # Cicha awaria jest gorsza od głośnej. Zapis takiego przebiegu wpisałby
        # do dziennika masową wyprzedaż, której nie było, a dziennika się nie
        # kasuje - błąd zostałby tam na zawsze.
        tracker.log.warning(f"dozorca_de: PARSER OSLEPL ({alarm}) - nic nie zapisuje")
        print(f"!!! dozorca_de: {alarm} - przebieg odrzucony, nic nie zapisano")
        return

    zdarzenia, stan = wykryj_zdarzenia_de(stan, wyniki, teraz)
    zapisz_zdarzenia(zdarzenia)
    STAN_FILE.write_text(json.dumps(stan, ensure_ascii=False), encoding="utf-8")

    from collections import Counter
    licz = Counter(z["ev"] for z in zdarzenia)
    print(f"zdarzenia: {dict(licz)}")


if __name__ == "__main__":
    main()
