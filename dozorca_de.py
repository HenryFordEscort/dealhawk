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
    if 'id="viewad-price"' in html or 'id="viewad-title"' in html:
        return "zyje"
    return "nieznane"                        # np. strona antybota albo obcięta


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
                                  "ostatni_zywy": None})
                continue

        rec.pop("prob", None)
        rec["prob"] = 0

        if wynik == "zdjete":
            zdarzenia.append({"ts": teraz, "ev": "znikla", "id": oid,
                              "ostatni_zywy": rec.get("ostatni_zywy")})
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


def do_sprawdzenia(stan, teraz, limit=MAX_SPRAWDZEN_NA_PRZEBIEG):
    """Które ogłoszenia odpytać w tym przebiegu. Zdjęte i odpuszczone odpadają,
    reszta czeka swoje SPRAWDZAJ_CO_H. Najdawniej sprawdzone idą pierwsze."""
    kand = []
    for oid, rec in stan.items():
        if rec.get("zdjete") or rec.get("odpuszczone"):
            continue
        ost = rec.get("ostatni_zywy") or rec.get("pierwszy") or ""
        godz = _godzin_od(ost, teraz)
        if godz is None or godz >= SPRAWDZAJ_CO_H:
            kand.append((ost, oid))
    kand.sort()
    return [oid for _, oid in kand[:limit]]


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


def zasiej_ze_sledzonych(stan):
    """Dosiewa stan o ogłoszenia, które bot już widział (seen.json ma URL-e,
    market.jsonl datę wystawienia). Czyta OBA PLIKI TYLKO DO ODCZYTU."""
    wyst = {}
    try:
        for line in Path("market.jsonl").open(encoding="utf-8"):
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("id") and r.get("wyst"):
                wyst.setdefault(r["id"], r["wyst"])
    except Exception:
        pass

    dodane = 0
    try:
        seen = json.loads(Path("seen.json").read_text(encoding="utf-8"))
    except Exception:
        return 0
    for oid, v in seen.items():
        if not isinstance(v, dict) or not v.get("url") or oid in stan:
            continue
        stan[oid] = {"url": v["url"], "wyst": wyst.get(oid) or v.get("date"),
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
    kolejka = do_sprawdzenia(stan, teraz, limit)
    print(f"dozorca_de: w stanie {len(stan)} ogłoszeń (dosiano {dodane}), "
          f"sprawdzam {len(kolejka)}")

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

    zdarzenia, stan = wykryj_zdarzenia_de(stan, wyniki, teraz)
    zapisz_zdarzenia(zdarzenia)
    STAN_FILE.write_text(json.dumps(stan, ensure_ascii=False), encoding="utf-8")

    from collections import Counter
    licz = Counter(z["ev"] for z in zdarzenia)
    print(f"zdarzenia: {dict(licz)}")


if __name__ == "__main__":
    main()
