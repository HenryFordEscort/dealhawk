#!/usr/bin/env python3
"""Przepuszcza ZAMROZONE okno dziennika rynku przez BIEZACY kod i porownuje
z zapisanym wzorcem. Kod 1, gdy cokolwiek sie ruszylo.

PO CO TO JEST (prosba wlasciciela z 20.09.2026): "czesto wpadam na nowe
pomysly i zlecam ci (...) takze w innych czatach ktore moga nie znac do konca
kontekstu". Zwykle testy pytaja, czy funkcja robi to, co autor testu wpisal.
To narzedzie pyta o co innego: **ile rowerow przeszloby bramki, gdyby ten kod
chodzil przez te same dwa tygodnie rynku**. Lapie wiec zepsucie NIEZALEZNIE
od tego, ktora linijke ktos ruszyl - mierzy skutek, a nie kod.

Dotad to bylo DYSCYPLINA autora zmiany ("zmierzone przed wdrozeniem: +0,11
wiadomosci dziennie" przy `za_duza_rama`, "+2,4 pobrania stron dziennie" przy
`FULLY_KEYWORDS`). Dyscyplina dziala, dopoki autor wie, ze ma ja miec. Ten
plik robi z niej mechanizm.

## Dlaczego prog wynosi ZERO, a nie "kilka procent"

Okno jest ZAMROZONE - wzorzec zapisuje konkretne dni, a `log_market` pisze
kazdy wiersz z data BIEZACA, wiec wiersze sprzed tygodnia juz sie nie zmienia.
Wejscie jest wiec stale co do wiersza i kazda roznica w wyniku pochodzi
WYLACZNIE z kodu. Prog "kilka procent na wahania rynku" byl by progiem
wzietym z glowy, a takich ten repozytorium zabrania - i zarazem przepuszczalby
male zepsucia.

Gdy zmiana jest SWIADOMA, autor uruchamia `--zapisz` i commituje nowy wzorzec.
Wtedy w samym PR widac, ile ta zmiana kosztuje - liczba obok liczby.

## Czego to NIE obejmuje - nie udawaj, ze obejmuje

Dziennik rynku nie zapisuje OPISOW ogloszen (bot wyrzuca opis po
przeczytaniu), wiec odtworzyc da sie wylacznie bramki stojace PRZED pobraniem
strony. Poza zasiegiem zostaja: filtr silnika z opisu, przebieg, mala bateria,
Levo FSR, dedup re-listingu i cala punktacja. Liczba `dociera` to "ile
ogloszen doszloby do pobrania strony", a NIE "ile powiadomien by poszlo".

Bramka `nisza` niesie JEDNO wejscie odtworzone: produkcja porownuje cene
z mediana DANEGO SKANU, a dziennik mediany nie zapisuje, wiec liczymy ja tutaj
na (dzien, zapytanie). Wynik jest deterministyczny, wiec porownanie ze
wzorcem jest scisle - ale to nie jest liczba produkcyjna.

Zmierzone 20.09.2026 na oknie 06-19.09 (59 421 rowerow): `dociera` wychodzi
111 dziennie. Niezalezny pomiar z 02.09 na 55 dniach dal 122 dziennie - dwie
rozne drogi, ten sam rzad wielkosci.

## Liczymy ROWERY, nie wiersze (regula 5)

Dziennik jest dziennikiem: to samo ogloszenie potrafi w nim wrocic. Zmierzone
na tym oknie: 63 074 wiersze to 59 421 rowerow, a jedno ogloszenie
(wh-904689464) zajmuje 3 633 wiersze - samo 5,8% okna.

    python sprawdz_zachowanie.py              # porownaj z wzorcem
    python sprawdz_zachowanie.py --zapisz     # zapisz wzorzec po SWIADOMEJ zmianie
    python sprawdz_zachowanie.py --zapisz --od 2026-09-06 --do 2026-09-19
"""
import argparse
import collections
import json
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path

import tracker as T

WZORZEC_FILE = Path("wzorzec_zachowania.json")

# KOLEJNOSC MA ZNACZENIE - to jest kaskada, wiec rower zatrzymuje sie na
# PIERWSZEJ bramce, ktora go nie przepuszcza. Przestawienie dwoch pozycji
# zmienia liczby, choc zaden warunek sie nie zmienil.
#
# Lista jest przepisana z `tracker.main` recznie, wiec sama z siebie moze sie
# z nia rozjechac - i wlasnie dlatego test wyciaga ja z ZRODLA `main` i
# porownuje. Nowa bramka dolozona do trackera wywraca test, zamiast po cichu
# wypasc z pomiaru.
BRAMKI_TYTULOWE = ("cena", "smiec", "za_duza_rama", "nie_fully", "analogowy",
                   "nisza")
DOCIERA = "dociera"

# Sygnaly liczone OBOK kaskady, nie w niej. Nie decyduja o niczym w tym
# pomiarze, ale pokazuja wprost, czy ruszyl sie konkretny plik wiedzy
# wlasciciela - bez nich zmiana w `obserwowane.json` rozlalaby sie po kilku
# bramkach naraz i nie dalo by sie powiedziec, co ja spowodowalo.
SYGNALY = ("obserwowany", "nie_premium", "silnik_z_tytulu")


def wczytaj_rynek(od, do):
    """Rowery z dziennika w oknie [od, do], po jednym na numer ogloszenia.

    Idzie przez `T.market_wiersze()`, czyli przez WSZYSTKIE kawalki dziennika.
    Czytnik patrzacy na sam `market.jsonl` dostalby zamrozony kawalek sprzed
    18.09.2026 i nie krzyknalby - wynik nadal wygladalby wiarygodnie."""
    wg_id = {}
    for linia in T.market_wiersze():
        linia = linia.strip()
        if not linia:
            continue
        try:
            r = json.loads(linia)
        except ValueError:
            continue
        ts = r.get("ts")
        if not ts or not (od <= ts <= do):
            continue
        if not r.get("id") or not r.get("t"):
            continue
        # OSTATNIE SPOTKANIE WYGRYWA - ta sama zasada, ktora `zbuduj_rozrzut`
        # stosuje do ceny. Kawalki ida od najstarszego, wiec nadpisanie daje
        # najswiezszy zapis tego ogloszenia.
        wg_id[r["id"]] = r
    return list(wg_id.values())


def mediany_odtworzone(wiersze):
    """Mediana ceny na (dzien, zapytanie) - jedyne odtworzone wejscie.

    Produkcja bierze mediane JEDNEGO skanu, a tej dziennik nie zapisuje.
    Tutaj chodzi o powtarzalnosc, nie o wierne odtworzenie tamtej liczby."""
    ceny = collections.defaultdict(list)
    for r in wiersze:
        if r.get("p"):
            ceny[(r.get("ts"), r.get("s"))].append(r["p"])
    return {k: statistics.median(v) for k, v in ceny.items()}


def _okazja_niszowa(cena, mediana):
    if not cena or not mediana:
        return False
    return (mediana - cena) / mediana * 100 >= T.NICHE_MIN_DISCOUNT_PCT


def werdykt(rekord, mediana):
    """Na ktorej bramce zatrzymuje sie to ogloszenie - albo `dociera`.

    Kolejnosc warunkow jest przepisana z `tracker.main` co do joty, razem
    z tym, ktore bramki omija model z listy zyczen (`pilny`)."""
    tytul = rekord["t"]
    cena = rekord.get("p")
    pilny = T.obserwowany(tytul) is not None
    if not pilny and not T.cena_w_widelkach(cena):
        return "cena"
    if T.is_junk(tytul):
        return "smiec"
    if not pilny and T.za_duza_rama(tytul):
        return "za_duza_rama"
    if not T.is_fully(tytul):
        return "nie_fully"
    if not T.is_electric(tytul):
        return "analogowy"
    if (not T.is_premium_brand(tytul) and not pilny
            and not _okazja_niszowa(cena, mediana)):
        return "nisza"
    return DOCIERA


def policz(od, do):
    """Pelny odcisk zachowania dla okna [od, do]."""
    wiersze = wczytaj_rynek(od, do)
    med = mediany_odtworzone(wiersze)
    dni = collections.defaultdict(collections.Counter)
    razem = collections.Counter()
    for r in wiersze:
        ts = r["ts"]
        w = werdykt(r, med.get((ts, r.get("s"))))
        dni[ts][w] += 1
        razem[w] += 1
        dni[ts]["rowerow"] += 1
        razem["rowerow"] += 1
        if T.obserwowany(r["t"]) is not None:
            dni[ts]["obserwowany"] += 1
            razem["obserwowany"] += 1
        if not T.is_premium_brand(r["t"]):
            dni[ts]["nie_premium"] += 1
            razem["nie_premium"] += 1
        # Sam tytul, bez opisu - wiec to NIE jest produkcyjny werdykt filtra
        # silnika. Chodzi o to, zeby zmiana w `silniki_bosch.json` albo we
        # wzorcach rodzin miala gdzie sie pokazac; twarde ograniczenie
        # "tylko Bosch" pilnuja osobne testy.
        if T.has_known_motor(r["t"], ""):
            dni[ts]["silnik_z_tytulu"] += 1
            razem["silnik_z_tytulu"] += 1
    pola = list(BRAMKI_TYTULOWE) + [DOCIERA] + list(SYGNALY)
    return {
        "okno": {"od": od, "do": do},
        "policzono": date.today().isoformat(),
        "rowerow": razem["rowerow"],
        "razem": {k: razem[k] for k in pola},
        "dni": {d: {"rowerow": dni[d]["rowerow"],
                    **{k: dni[d][k] for k in pola}}
                for d in sorted(dni)},
    }


def wejscie_sie_zmienilo(wzorzec, teraz):
    """Czy to samo okno dalo INNA liczbe rowerow.

    Nie powinno sie zdarzyc: `log_market` pisze kazdy wiersz z data biezaca,
    wiec dni sprzed tygodnia sa zamrozone. Gdy jednak sie zdarzy, porownanie
    nie mowi juz nic o kodzie - zmienilo sie wejscie. Najczestsza przyczyna
    to brakujacy kawalek dziennika, czyli awaria warta zobaczenia (regula 7),
    a nie powod do nadpisania wzorca."""
    return (wzorzec.get("okno") == teraz.get("okno")
            and wzorzec.get("rowerow") != teraz.get("rowerow"))


def roznice(wzorzec, teraz):
    """Lista zdan o tym, co sie ruszylo. Pusta = zachowanie bez zmian."""
    out = []
    if wzorzec.get("okno") != teraz.get("okno"):
        out.append(f"okno pomiaru: wzorzec {wzorzec.get('okno')} "
                   f"vs teraz {teraz.get('okno')}")
        return out
    if wejscie_sie_zmienilo(wzorzec, teraz):
        out.append(f"rowerow w oknie: {wzorzec.get('rowerow')} → "
                   f"{teraz.get('rowerow')} (zmienilo sie WEJSCIE, "
                   f"nie kod - brakuje kawalka dziennika?)")
    stare, nowe = wzorzec.get("razem", {}), teraz.get("razem", {})
    for k in sorted(set(stare) | set(nowe)):
        a, b = stare.get(k), nowe.get(k)
        if a != b:
            ile = (b or 0) - (a or 0)
            dni = max(1, len(teraz.get("dni", {})) or 1)
            out.append(f"{k}: {a} → {b} ({ile:+d}, czyli {ile / dni:+.2f} "
                       f"dziennie)")
    return out


def opisz(teraz):
    dni = len(teraz.get("dni", {})) or 1
    linie = [f"Okno {teraz['okno']['od']} .. {teraz['okno']['do']} "
             f"({dni} dni, {teraz['rowerow']} rowerow)"]
    for k in list(BRAMKI_TYTULOWE) + [DOCIERA] + list(SYGNALY):
        v = teraz["razem"].get(k, 0)
        linie.append(f"  {k:16s} {v:7d}   {v / dni:8.1f} dziennie")
    return "\n".join(linie)


def wczytaj_wzorzec(plik=None):
    # SCIEZKA NIGDY W DOMYSLNYM ARGUMENCIE - wiaze wartosc w chwili definicji
    # modulu, wiec podmiana stalej w tescie nie mialaby skutku. Ta pomylka
    # wyszla w tym repo trzy razy.
    plik = Path(plik) if plik else WZORZEC_FILE
    if not plik.exists():
        return None
    return json.loads(plik.read_text(encoding="utf-8"))


def zapisz_wzorzec(dane, plik=None):
    plik = Path(plik) if plik else WZORZEC_FILE
    plik.write_text(json.dumps(dane, ensure_ascii=False, indent=1) + "\n",
                    encoding="utf-8")


def domyslne_okno(dni=14, do=None):
    """Ostatnie pelne dni, konczac WCZORAJ.

    Dzisiejszy dzien odpada z rozmyslu: dziennik jeszcze go dopisuje, wiec
    wzorzec zapisany o poludniu rozjechalby sie z wlasnym pomiarem wieczorem."""
    koniec = date.fromisoformat(do) if do else date.today() - timedelta(days=1)
    return (koniec - timedelta(days=dni - 1)).isoformat(), koniec.isoformat()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--zapisz", action="store_true",
                    help="zapisz biezacy wynik jako nowy wzorzec")
    ap.add_argument("--od", help="pierwszy dzien okna (RRRR-MM-DD)")
    ap.add_argument("--do", help="ostatni dzien okna (RRRR-MM-DD)")
    ap.add_argument("--dni", type=int, default=14,
                    help="dlugosc okna, gdy nie podano --od (domyslnie 14)")
    a = ap.parse_args(argv)

    wzorzec = wczytaj_wzorzec()
    if bool(a.od) != bool(a.do):
        # Podane jedno z dwojga po cichu wpadalo do okna domyslnego, czyli
        # liczylo CO INNEGO, niz autor prosil. Cicha podmiana wejscia jest
        # w tym narzedziu najgorszym mozliwym bledem.
        print("BLAD: --od i --do podaje sie razem albo wcale.")
        return 1
    if a.od and a.do:
        od, do = a.od, a.do
    elif wzorzec and not (a.od or a.do):
        # Okno idzie ZE WZORCA, nie z dzisiejszej daty. Inaczej samo uplyniecie
        # doby zmienialoby wejscie i kazde uruchomienie krzyczalo by o roznicy,
        # ktorej nikt nie spowodowal.
        od, do = wzorzec["okno"]["od"], wzorzec["okno"]["do"]
    else:
        od, do = domyslne_okno(a.dni, a.do)

    teraz = policz(od, do)
    if not teraz["rowerow"]:
        print(f"BLAD: w oknie {od} .. {do} nie ma ANI JEDNEGO wiersza "
              f"dziennika. Bez danych nie ma czego porownywac.")
        return 1

    print(opisz(teraz))

    if a.zapisz:
        if wzorzec and wejscie_sie_zmienilo(wzorzec, teraz):
            print(f"\nBLAD: to samo okno dalo {teraz['rowerow']} rowerow "
                  f"zamiast {wzorzec['rowerow']}. Zmienilo sie WEJSCIE, wiec "
                  f"nowy wzorzec zapisalby awarie dziennika jako norme. "
                  f"Sprawdz kawalki `market-*.jsonl`.")
            return 1
        zapisz_wzorzec(teraz)
        print(f"\nZapisano wzorzec do {WZORZEC_FILE}. "
              f"Commituj go razem ze zmiana - diff pokaze jej koszt.")
        return 0

    if wzorzec is None:
        print(f"\nBLAD: brak pliku {WZORZEC_FILE}. Bez wzorca nie ma do czego "
              f"porownac - uruchom raz z --zapisz.")
        return 1

    r = roznice(wzorzec, teraz)
    if not r:
        print("\nZachowanie bez zmian wobec wzorca.")
        return 0
    print("\nZACHOWANIE SIE ZMIENILO wobec wzorca "
          f"(policzonego {wzorzec.get('policzono', '?')}):")
    for linia in r:
        print("  " + linia)
    if wejscie_sie_zmienilo(wzorzec, teraz):
        print("\nNIE zapisuj nowego wzorca - najpierw ustal, czemu dziennik "
              "oddaje inna liczbe wierszy za te same dni.")
    else:
        print("\nJesli ta zmiana jest ZAMIERZONA - uruchom --zapisz i wrzuc "
              "nowy wzorzec do tego samego commita.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
