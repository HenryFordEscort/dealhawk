#!/usr/bin/env python3
"""Czy to, co bot nazywa okazja, rynek niemiecki bierze szybciej.

PYTANIE, KTOREGO NIKT DOTAD NIE ZADAL. Bot od sierpnia ocenia, czy rower jest
tani w Niemczech, i nie ma zadnego sposobu sprawdzenia, czy sie myli:
zapisanych transakcji wlasciciela jest 0, a rynek niemiecki nie publikuje
sprzedazy. Ale rynek glosuje nogami. Ogloszenie na Kleinanzeigen NIE WYGASA
samo (zmierzone, patrz rozdzial o roznicach OLX/Kleinanzeigen), wiec jego
zniknieciem jest zawsze czyjas decyzja, a zniknieciem w trzy godziny prawie
zawsze sprzedaz. Jesli rowery, ktore bot nazwal okazja, schodza szybciej niz
reszta tego, co wysyla - wycena ma moc rozdzielcza. Jesli schodza tak samo -
nie ma, i zaden prog na niej zbudowany nie jest wart wiecej niz rzut moneta.

CZEGO TEN PLIK NIE ROBI: nie liczy niczego z zaleglosci dozorcy. Zmierzone
09.11.2026 na 3 203 znikniecach: 3 140 ma `ostatni_zywy: null`, a odstep od
wystawienia do zauwazenia zgonu ma mediane 37 dni przy kwartylach 36 i 38 -
to rozklad KOLEJKI, nie rynku. Liczy sie wylacznie kohorta (`koh: true`),
czyli ogloszenia przyjete swieze i pytane codziennie.

ROZDZIELCZOSC JEST MIERZONA, NIE ZAKLADANA (poprawka z 10.11.2026). Raport
pisal "jeden dzien", bo tyle wynikalo z crona dozorcy - a dozorca chodzi
4 razy na dobe, nie 12 (zmierzone na 21 dniach dziennika), wiec przy oknie
10 dni kazdy rower byl pytany raz na 2,8 dnia. Dzis okno ma tyle dni, ile
mowi `dozorca_de.KOHORTA_DNI` (3), zeby pytanie bylo codzienne - a raport nie
wpisuje tej liczby z pamieci, patrz `OKNO_DNI`. `rozdzielczosc()` liczy
prawdziwy odstep miedzy
pytaniami z dziennika i podaje go w naglowku. Zalozenie w miejscu pomiaru
jest tu grozniejsze niz brak liczby: "zeszlo 1. dnia" przy pytaniu raz na
trzy dni znaczy co innego niz to, co czyta czlowiek.

Uruchom: python3 rynek_ocenia.py
"""
import json
import sys
from datetime import datetime
from pathlib import Path

ZDARZENIA_DIR = Path("zdarzenia_de")

# Ponizej tylu ZEJSC w grupie nie podajemy zadnej liczby. Nie jest to prog
# statystyczny z podrecznika, tylko granica, ponizej ktorej mediana skacze
# o kilka dni po dolozeniu jednej obserwacji - czyli nie jest medianą, tylko
# ostatnim zdarzeniem w przebraniu pomiaru (regula 6).
MIN_ZEJSC_NA_GRUPE = 15
PROG_OKAZJI = 5000          # zl; ta sama warstwa, ktora wpuszcza do kohorty

# Dlugosc okna gestej obserwacji - TA SAMA liczba, ktora dozorca naprawde
# stosuje (`dozorca_de.KOHORTA_DNI`). Stoi tu jako wlasna stala, a nie import,
# bo ten modul nie pobiera niczego i nie ma po co wciagac klienta HTTP. Zgody
# obu liczb pilnuje test - 10.11.2026 okno zjechalo z 4 dni na 3, a werdykt
# dalej pisal "oknie 4 dni", czyli raport podawal parametr, ktorego kod
# nie uzywa (regula 6).
OKNO_DNI = 3


def _dt(s):
    try:
        return datetime.strptime(str(s)[:16], "%Y-%m-%dT%H:%M")
    except (ValueError, TypeError):
        return None


def wczytaj_kohorte(katalog=None):
    """Sklada kohorte z samego dziennika. {id: rekord obserwacji}.

    Dziennik jest jedynym zrodlem - stan (`de_stan.json`) jest odtwarzalny
    i nie wolno na nim opierac wnioskow, bo nie pamieta historii."""
    katalog = Path(katalog or ZDARZENIA_DIR)
    koh = {}
    for plik in sorted(katalog.glob("de-*.jsonl")):
        for linia in plik.open(encoding="utf-8"):
            try:
                z = json.loads(linia)
            except ValueError:
                continue
            if not z.get("koh"):
                continue
            oid, ev = z.get("id"), z.get("ev")
            r = koh.setdefault(oid, {"wyst": None, "werdykt": {}, "od": None,
                                     "zywy": None, "zeszlo": None, "rez": False,
                                     "pytania": []})
            if z.get("wyst") and not r["wyst"]:
                r["wyst"] = z["wyst"]
            if ev == "kohorta":
                r["od"] = z.get("ts")
                r["werdykt"] = z.get("werdykt") or {}
            elif ev == "zyje":
                r["zywy"] = z.get("ts")
                # KAZDE pytanie, nie tylko ostatnie: z odstepow miedzy nimi
                # liczy sie PRAWDZIWA rozdzielczosc pomiaru. Do 10.11.2026
                # raport pisal "ROZDZIELCZOSC: jeden dzien" bez patrzenia
                # w dane - a budzet dozorcy na to nie wystarczal.
                if z.get("ts"):
                    r["pytania"].append(z["ts"])
            elif ev == "rezerwacja":
                r["rez"] = True
            elif ev == "znikla":
                r["zeszlo"] = z.get("ts")
                if z.get("ostatni_zywy"):
                    r["zywy"] = z["ostatni_zywy"]
    return koh


def obserwacja(rec, dzis=None):
    """(dni, czy_zeszlo) albo None, gdy nie ma od czego liczyc.

    Czas liczymy OD WYSTAWIENIA, nie od przyjecia do kohorty: to drugie jest
    nasza data i ten sam blad kosztowal juz raz caly pomiar plynnosci OLX.
    Ogloszenie wciaz zywe jest obserwacja UCIETA (cenzurowana), nie brakiem
    danych - wiemy o nim tyle, ze dozylo swojego wieku."""
    a = _dt(rec.get("wyst"))
    if a is None:
        return None
    if rec.get("zeszlo"):
        b = _dt(rec["zeszlo"])
        return (max((b - a).total_seconds() / 86400.0, 0.0), True) if b else None
    b = _dt(rec.get("zywy")) or _dt(dzis or datetime.utcnow().strftime("%Y-%m-%dT%H:%M"))
    return (max((b - a).total_seconds() / 86400.0, 0.0), False) if b else None


def krzywa(obserwacje):
    """Kaplan-Meier. [(dni, udzial_wciaz_wiszacych)] - cenzurowane nie licza
    sie jako zejscia, tylko znikaja z mianownika."""
    zdarz = sorted(obserwacje)
    n = len(zdarz)
    wynik, s, ryzyko = [], 1.0, n
    i = 0
    while i < n:
        t = zdarz[i][0]
        zeszlo = sum(1 for d, e in zdarz[i:] if d == t and e)
        ile = sum(1 for d, _ in zdarz[i:] if d == t)
        if zeszlo and ryzyko > 0:
            s *= (1 - zeszlo / ryzyko)
            wynik.append((t, s))
        ryzyko -= ile
        i += ile
    return wynik


def mediana_zycia(obserwacje):
    """Dzien, w ktorym przezywa <= 50%. None, gdy krzywa tam nie dochodzi -
    i wtedy mowimy 'nie wiem', zamiast podawac ostatni punkt jako mediane."""
    for t, s in krzywa(obserwacje):
        if s <= 0.5:
            return t
    return None


def udzial_do_dnia(obserwacje, dzien):
    """Jaka czesc zeszla do dnia N. None, gdy obserwacja tam nie siega."""
    przezylo = None
    for t, s in krzywa(obserwacje):
        if t > dzien:
            break
        przezylo = s
    if przezylo is None:
        # Zadnego zejscia do tego dnia. To albo "nikt nie zszedl" (0%), albo
        # "nikt tak dlugo nie byl obserwowany" - i te dwa trzeba rozroznic,
        # bo drugie to brak danych udajacy wynik.
        return 0.0 if any(d >= dzien for d, _ in obserwacje) else None
    return 1 - przezylo


def rozdzielczosc(koh):
    """(mediana odstepu miedzy pytaniami w dniach, ile par) albo None.

    Mierzy to, co raport dotad TWIERDZIL. Przy jednym pytaniu na ogloszenie
    nie ma z czego liczyc odstepu i wtedy mowimy "nie wiem" - liczba wzieta
    z crona albo z budzetu jest zalozeniem, nie pomiarem (regula 6)."""
    odstepy = []
    for rec in koh.values():
        ts = sorted(t for t in (rec.get("pytania") or []) if _dt(t))
        for a, b in zip(ts, ts[1:]):
            d = (_dt(b) - _dt(a)).total_seconds() / 86400.0
            if d > 0:
                odstepy.append(d)
    if not odstepy:
        return None
    odstepy.sort()
    n = len(odstepy)
    med = (odstepy[n // 2] if n % 2
           else (odstepy[n // 2 - 1] + odstepy[n // 2]) / 2)
    return med, n


def grupa(rec):
    z = (rec.get("werdykt") or {}).get("profit")
    if not isinstance(z, (int, float)):
        return "bez wyceny"
    return "okazja" if z >= PROG_OKAZJI else "kontrola"


def raport(koh, dzis=None):
    linie = []
    grupy = {}
    bez_daty = 0
    for rec in koh.values():
        o = obserwacja(rec, dzis)
        if o is None:
            bez_daty += 1
            continue
        grupy.setdefault(grupa(rec), []).append((o, rec))
    linie.append(f"KOHORTA: {len(koh)} ogloszen w gestej obserwacji "
                 f"({bez_daty} bez daty wystawienia, poza rachunkiem)")
    # Zastrzezenie stoi W NAGLOWKU, nie przy werdykcie, bo kazda liczba nizej
    # jest nim obarczona - a raport bez werdyktu (za chuda probka) i tak
    # pokazuje mediany grup (regula 6).
    r = rozdzielczosc(koh)
    if r is None:
        linie.append("ROZDZIELCZOSC: jeszcze NIE ZMIERZONA - zaden rower nie "
                     "dostal drugiego pytania. Budzet dozorcy pozwala na "
                     "codzienne, ale to zalozenie, nie pomiar.")
    else:
        med, par = r
        linie.append(f"ROZDZIELCZOSC ZMIERZONA: mediana odstepu miedzy "
                     f"pytaniami {med:.1f} dnia (n={par} par). "
                     f"'Zeszlo 1. dnia' obejmuje wiec i trzy godziny, "
                     f"i {med*24:.0f}. Roznice mniejsze niz kilka punktow "
                     f"to szum.")
    for nazwa in ("okazja", "kontrola", "bez wyceny"):
        poz = grupy.get(nazwa) or []
        obs = [o for o, _ in poz]
        zejsc = sum(1 for _, e in obs if e)
        linie.append(f"\n{nazwa.upper()}: {len(obs)} ogloszen, {zejsc} zejsc")
        if zejsc < MIN_ZEJSC_NA_GRUPE:
            linie.append(f"  NIE WIEM - przy {zejsc} zejsciach kazda liczba "
                         f"bylaby ostatnim zdarzeniem w przebraniu pomiaru "
                         f"(prog: {MIN_ZEJSC_NA_GRUPE})")
            continue
        med = mediana_zycia(obs)
        linie.append("  mediana zycia: "
                     + (f"{med:.0f} dni" if med is not None
                        else "nie wiem (ponad polowa wciaz wisi)"))
        for d in (1, 3, 7):
            u = udzial_do_dnia(obs, d)
            if u is not None:
                linie.append(f"  zeszlo do dnia {d}: {u*100:.0f}%")
        rez = sum(1 for _, r in poz if r.get("rez"))
        linie.append(f"  z rezerwacja (najmocniejsza poszlaka sprzedazy): {rez}")
    ok, kon = grupy.get("okazja") or [], grupy.get("kontrola") or []
    zejsc_ok = sum(1 for (o, _) in ok if o[1])
    zejsc_kon = sum(1 for (o, _) in kon if o[1])
    linie.append("\nWERDYKT O RZECZOZNAWCY:")
    if zejsc_ok < MIN_ZEJSC_NA_GRUPE or zejsc_kon < MIN_ZEJSC_NA_GRUPE:
        linie.append(f"  NIE WIEM. Kohorta zbiera od 09.11.2026, przy "
                     f"zmierzonych 16,5 przyjeciach na dobe i oknie "
                     f"{OKNO_DNI} dni pierwsza odpowiedz moze byc za "
                     f"2-3 tygodnie. Do tego czasu kazdy wniosek stad "
                     f"bylby zgadywaniem.")
    else:
        a = udzial_do_dnia([o for o, _ in ok], 7)
        b = udzial_do_dnia([o for o, _ in kon], 7)
        if a is None or b is None:
            linie.append("  NIE WIEM - obserwacja nie siega jeszcze dnia 7.")
        else:
            linie.append(f"  do dnia 7 schodzi {a*100:.0f}% okazji wobec "
                         f"{b*100:.0f}% kontroli.")
    return "\n".join(linie)


def main():
    kat = sys.argv[1] if len(sys.argv) > 1 else None
    koh = wczytaj_kohorte(kat)
    print(raport(koh))


if __name__ == "__main__":
    main()
