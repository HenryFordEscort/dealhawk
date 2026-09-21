#!/usr/bin/env python3
"""Plynnosc rynku PL - ile ogloszen naprawde schodzi i po ilu dniach.

PO CO OSOBNY PLIK (20.09.2026). Bot mial juz odpowiedz na to pytanie
(`sell_through_pct` w `summary.py`, `segment_liquidity` w `tracker.py`)
i ta odpowiedz byla ZAWYZONA. Mowila "schodzi 76-86% w 30 dni, mediana
9 dni". Zmierzone na tych samych danych tego samego dnia:

  - Wiek AKTYWNYCH ogloszen w `olx_stan.json`: mediana 45 dni, 42% wisi
    dluzej niz 60 dni, 18% dluzej niz pol roku.
  - Ogloszenie zyje MEDIANE 30 DNI, zanim bot pierwszy raz je zobaczy
    (49% ma w chwili wykrycia wiecej niz 30 dni). Pole `days` w sold_fast
    liczylo wiek od NASZEGO pierwszego widzenia, wiec kazde ogloszenie
    wygladalo na mlode. Maksimum w zbiorze to rowno 45, czyli sufit reguly
    - wszystko ponizej sufitu bylo ksiegowane jako szybka sprzedaz.
  - 3442 zapisow "sprzedane" na 56 "wygasle". Przy 42% ofert wiszacych
    ponad 60 dni taka proporcja nie jest mozliwa.

To nie byl blad w linijce, tylko zly rodzaj miary: udzial sprzedanych liczony
na obserwacjach uciętych z obu stron (z lewej - nie widzielismy poczatku,
z prawej - nie znamy konca wiszacych) zawsze wyjdzie za wysoki.

CO ROBI TEN PLIK. Liczy krzywa przezycia (Kaplan-Meier) z dziennika dozorcy,
ktory zapisuje FAKTY, nie wnioski:
  - wiek liczony od `wystawiono` z OLX-a, nie od naszego pierwszego widzenia,
  - ogloszenie wchodzi do grupy ryzyka w wieku, w jakim je zobaczylismy
    (opoznione wejscie), wiec brak poczatku nie udaje mlodosci,
  - ogloszenia wciaz wiszace sa CENZUROWANE, nie liczone jako niesprzedane,
  - "zdjeta" (sprzedawca zdjal przed wygasnieciem) i "wygasla" (dozyla daty
    waznosci, nikt nie kupil) to dwa rozne konce, nie jeden.

CZEGO TEN PLIK NADAL NIE WIE, i nie bedzie wiedzial:
  - CZY rower sie sprzedal. "Zdjeta" znaczy tylko, ze sprzedawca ja zdjal.
    Wznowienie pod nowym id jest rzadkie (zmierzone 20.09.2026: 1% zniknięć
    ma te same zdjecia albo ten sam tytul w nowym ogloszeniu), wiec zdjecie
    to najlepsza dostepna poszlaka sprzedazy - ale poszlaka.
  - ZA ILE sie sprzedal. Ostatnia widoczna cena to cena wywolawcza; targ
    odbyl sie po niej i nigdzie nie jest zapisany. To ustalone i zamkniete
    (korekta z 24.08.2026): zadne tygodnie zbierania tego nie zmienia.

Uruchom:
  python3 plynnosc.py            # raport
  python3 plynnosc.py --json     # te same liczby maszynowo
"""
import glob
import json
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ZDARZENIA_DIR = Path("zdarzenia")
STAN_FILE = Path("olx_stan.json")

# Tyle godzin luzu ma nasze wykrywanie zniknięcia (dozorca chodzi co ~2 h
# i potwierdza dopiero po 3 nieobecnosciach). Ta sama stala co w dozorca.py.
LUZ_WYKRYCIA_H = 8

# Ponizej tylu ogloszen w grupie ryzyka nie podajemy liczby. Krzywa przezycia
# na kilku sztukach to nie pomiar, a bot ma mowic "nie wiem".
MIN_W_RYZYKU = 20

PASMA = [(0, 3000, "do 3k zl"), (3000, 5000, "3-5k zl"), (5000, 8000, "5-8k zl"),
         (8000, 12000, "8-12k zl"), (12000, 16000, "12-16k zl"),
         (16000, 10 ** 9, "16k+ zl")]

WIEKI_RAPORTU = (14, 30, 60, 90, 180)


def _czas(s):
    """Znacznik czasu w dowolnym z formatow, ktore krazą po projekcie."""
    if not isinstance(s, str) or not s:
        return None
    t = s.replace("Z", "+00:00")
    for prob in (lambda x: datetime.fromisoformat(x),
                 lambda x: datetime.strptime(x, "%Y-%m-%dT%H:%M"),
                 lambda x: datetime.strptime(x[:10], "%Y-%m-%d")):
        try:
            d = prob(t)
            return d.replace(tzinfo=None) if d.tzinfo else d   # wszystko na czas naiwny
        except Exception:
            continue
    return None


def wczytaj_zdarzenia() -> dict:
    """Dziennik dozorcy -> {id: [zdarzenia po czasie]}."""
    ev = {}
    for f in sorted(glob.glob(str(ZDARZENIA_DIR / "olx-*.jsonl"))):
        for linia in open(f, encoding="utf-8"):
            if not linia.strip():
                continue
            try:
                d = json.loads(linia)
            except json.JSONDecodeError:
                continue
            if d.get("id"):
                ev.setdefault(d["id"], []).append(d)
    for lst in ev.values():
        lst.sort(key=lambda x: x.get("ts") or "")
    return ev


def zycia(ev=None, stan=None) -> list:
    """Dziennik + biezacy stan -> po jednym rekordzie na OGLOSZENIE.

    Kazdy rekord: {id, wystawiono, wejscie (wiek, w jakim je zobaczylismy),
    wyjscie (wiek na koniec obserwacji), zdarzenie (True=zeszlo,
    False=wisi dalej), powod, cena}.

    Wazne: ZNIKNIECIE NIE JEST OSTATECZNE. Zmierzone 20.09.2026: 61 z 336
    ogloszen uznanych za zniknięte pozniej wrocilo (18%). Dlatego za koniec
    zycia bierzemy tylko takie "znikla", po ktorym w dzienniku NIC juz nie ma
    i ktorego nie widac w biezacym stanie."""
    ev = ev if ev is not None else wczytaj_zdarzenia()
    stan = stan if stan is not None else (
        json.loads(STAN_FILE.read_text(encoding="utf-8")) if STAN_FILE.exists() else {})
    # koniec obserwacji: najswiezszy znacznik, jaki mamy w danych
    wszystkie_ts = [_czas(z.get("ts")) for lst in ev.values() for z in lst]
    wszystkie_ts += [_czas(v.get("ostatni")) for v in stan.values()]
    koniec = max([t for t in wszystkie_ts if t], default=datetime.now())

    out, bez_daty = [], 0
    for oid, lst in ev.items():
        rec_stan = stan.get(oid) or {}
        wyst = None
        for z in lst:                               # data wystawienia z faktow
            if z.get("wystawiono"):
                wyst = _czas(z["wystawiono"])
        wyst = wyst or _czas(rec_stan.get("wystawiono"))
        pierwszy = _czas((lst[0] or {}).get("ts")) or _czas(rec_stan.get("pierwszy"))
        if wyst is None or pierwszy is None:
            bez_daty += 1
            continue                                # "nie wiem" - poza pomiarem

        ostatnia_znikla = None
        for i, z in enumerate(lst):
            if z.get("ev") == "znikla" and not lst[i + 1:]:
                ostatnia_znikla = z                 # nic po niej = koniec zycia
        zywa = bool(rec_stan) and rec_stan.get("status") == "active"
        if ostatnia_znikla and not zywa:
            koniec_zycia = _czas(ostatnia_znikla.get("ts")) or koniec
            zdarzenie = True
            powod = _powod(ostatnia_znikla, lst, rec_stan)
            cena = ostatnia_znikla.get("p") or rec_stan.get("p")
        else:
            koniec_zycia = koniec
            zdarzenie = False                       # wisi dalej = obserwacja ucieta
            powod = None
            cena = rec_stan.get("p") or _ostatnia_cena(lst)

        wejscie = max((pierwszy - wyst).days, 0)
        wyjscie = max((koniec_zycia - wyst).days, wejscie)
        zapytanie = next((z.get("q") for z in reversed(lst) if z.get("q")),
                         rec_stan.get("q"))
        out.append({"id": oid, "wystawiono": wyst.date().isoformat(),
                    "wejscie": wejscie, "wyjscie": wyjscie, "q": zapytanie,
                    "zdarzenie": zdarzenie, "powod": powod, "cena": cena})
    if bez_daty:
        out.append({"__bez_daty__": bez_daty})
    return out


def _ostatnia_cena(lst):
    for z in reversed(lst):
        if isinstance(z.get("p"), int):
            return z["p"]
    return None


def _powod(znikla, lst, rec_stan):
    """"zdjeta" / "wygasla" / None - ta sama regula co w dozorca.powod_zniknienia,
    powtorzona tu, zeby ten plik dal sie policzyc z samego dziennika."""
    wazne = None
    for z in lst:
        if z.get("wazne_do"):
            wazne = z["wazne_do"]
    wazne = _czas(znikla.get("wazne_do") or wazne or rec_stan.get("wazne_do"))
    t = _czas(znikla.get("ts"))
    if wazne is None or t is None:
        return None
    if t < wazne:
        return "zdjeta"
    if t - timedelta(hours=LUZ_WYKRYCIA_H) > wazne:
        return "wygasla"
    return None                                     # okno niepewnosci


def krzywa(rekordy, wieki=WIEKI_RAPORTU) -> dict:
    """Kaplan-Meier z OPOZNIONYM WEJSCIEM: jakie jest prawdopodobienstwo, ze
    ogloszenie nadal stoi po N dniach od wystawienia.

    Opoznione wejscie jest tu calym sednem. Ogloszenie, ktore zobaczylismy
    dopiero w 100. dniu zycia, nie moze wchodzic do rachunku jako nowe, bo
    "przezylo" te 100 dni bez naszego udzialu. Wchodzi do grupy ryzyka w wieku
    100 dni. Bez tej poprawki krzywa spada o wiele za szybko i wlasnie tak
    powstalo "schodzi w 9 dni".

    Zwraca {wiek: {"s": prawdopodobienstwo, "w_ryzyku": n}} oraz "n" i "zejsc"."""
    dane = [r for r in rekordy if "wejscie" in r]
    w_ryzyku = lambda t: sum(1 for r in dane if r["wejscie"] < t <= r["wyjscie"])
    kroki, s = [], 1.0
    for t in sorted({r["wyjscie"] for r in dane if r["zdarzenie"]}):
        n = w_ryzyku(t)
        zejsc = sum(1 for r in dane if r["zdarzenie"] and r["wyjscie"] == t)
        if n > 0:
            s *= (1 - zejsc / n)
        kroki.append((t, s))
    wynik = {}
    for c in sorted(wieki):
        n_ryz = w_ryzyku(c)
        sc = 1.0
        for t, sv in kroki:
            if t > c:
                break
            sc = sv
        # Za cienka grupa ryzyka = "nie wiem". Krzywa policzylaby sie i tak,
        # ale na kilku sztukach jeden przypadek przesuwa ja o dziesiatki punktow.
        wynik[c] = {"s": sc if n_ryz >= MIN_W_RYZYKU else None, "w_ryzyku": n_ryz}
    return {"po_wieku": wynik, "n": len(dane),
            "zejsc": sum(1 for r in dane if r["zdarzenie"])}


def powody(rekordy) -> dict:
    """Z czego skladaja sie zejscia: zdjeta / wygasla / nie wiem."""
    z = [r for r in rekordy if r.get("zdarzenie")]
    c = {"zdjeta": 0, "wygasla": 0, "nie_wiem": 0}
    for r in z:
        c["nie_wiem" if r.get("powod") is None else r["powod"]] += 1
    wieki_zdjetych = sorted(r["wyjscie"] for r in z if r.get("powod") == "zdjeta")
    c["n"] = len(z)
    c["mediana_wieku_zdjetych"] = (int(statistics.median(wieki_zdjetych))
                                  if len(wieki_zdjetych) >= 5 else None)
    znane = c["zdjeta"] + c["wygasla"]
    # Udzial liczony TYLKO po znanych powodach. Licznik i mianownik z tego
    # samego zbioru, inaczej "nie wiem" przesuwaloby wynik w dol.
    c["udzial_zdjetych_pct"] = round(c["zdjeta"] / znane * 100) if znane >= 10 else None
    return c


def wg_pasma(rekordy) -> list:
    out = []
    for lo, hi, lbl in PASMA:
        w = [r for r in rekordy
             if isinstance(r.get("cena"), int) and lo <= r["cena"] < hi]
        if not w:
            continue
        out.append({"pasmo": lbl, "n": len(w),
                    "krzywa": krzywa(w), "powody": powody(w)})
    return out


def zmierz() -> dict:
    rek = [r for r in zycia() if "wejscie" in r]
    bez = next((r["__bez_daty__"] for r in zycia() if "__bez_daty__" in r), 0)
    return {"n_ogloszen": len(rek), "bez_daty_wystawienia": bez,
            "calosc": krzywa(rek), "powody": powody(rek), "pasma": wg_pasma(rek)}


# Tyle zdjetych ogloszen musi stac za mediana, zeby wolno bylo ja podac.
# Piec to nie pomiar: przy pieciu jedno ogloszenie przesuwa mediane o tygodnie.
MIN_ZDJETYCH = 12

_cache_zycia = None


def _rekordy():
    """Rekordy zycia z pamieci procesu. Dziennik czyta sie raz: `najlepsze`
    pyta o plynnosc raz na ogloszenie, a to setki wywolan w jednym przebiegu."""
    global _cache_zycia
    if _cache_zycia is None:
        _cache_zycia = [r for r in zycia() if "wejscie" in r]
    return _cache_zycia


def dni_do_zejscia(q: str = None, cena: int = None):
    """Po ilu dniach OD WYSTAWIENIA schodzi ogloszenie takiego roweru.

    Zwraca {"dni", "n", "skad"} albo None. `skad` mowi, na czym stoi liczba:
    "model" (te same zapytanie) albo "pasmo" (polka cenowa). Etykieta jest
    obowiazkowa, bo podmiana modelu na polke bez powiedzenia tego wprost to
    dokladnie ten rodzaj cichego zastepstwa, ktory zepsul wycene w sierpniu.

    None znaczy "nie wiem" i NIE jest podmieniane na zadna liczbe zastepcza."""
    rek = _rekordy()
    if q:
        w = [r for r in rek if r.get("q") == q]
        med = powody(w)["mediana_wieku_zdjetych"]
        n = sum(1 for r in w if r.get("powod") == "zdjeta")
        if med is not None and n >= MIN_ZDJETYCH:
            return {"dni": med, "n": n, "skad": "model"}
    if cena:
        for lo, hi, lbl in PASMA:
            if lo <= cena < hi:
                w = [r for r in rek
                     if isinstance(r.get("cena"), int) and lo <= r["cena"] < hi]
                med = powody(w)["mediana_wieku_zdjetych"]
                n = sum(1 for r in w if r.get("powod") == "zdjeta")
                if med is not None and n >= MIN_ZDJETYCH:
                    return {"dni": med, "n": n, "skad": f"pasmo {lbl}"}
    return None


def mediana_dni_do_zejscia(pasmo_ceny=None):
    """Zgodnosc wstecz: sama liczba dni albo None."""
    if pasmo_ceny:
        lo, hi = pasmo_ceny
        w = [r for r in _rekordy()
             if isinstance(r.get("cena"), int) and lo <= r["cena"] < hi]
        return powody(w)["mediana_wieku_zdjetych"]
    return powody(_rekordy())["mediana_wieku_zdjetych"]


def raport() -> str:
    d = zmierz()
    k = d["calosc"]["po_wieku"]
    p = d["powody"]
    L = ["📉 <b>Plynnosc rynku PL - co naprawde wiadomo</b>",
         f"<i>{d['n_ogloszen']} ogloszen z dziennika dozorcy, "
         f"{d['calosc']['zejsc']} potwierdzonych zejsc</i>", ""]
    L.append("<b>Ile ogloszen nadal stoi po N dniach od wystawienia</b>")
    for w in WIEKI_RAPORTU:
        r = k.get(w) or {}
        if r.get("s") is None:
            L.append(f"· {w} dni: nie wiem (w grupie ryzyka {r.get('w_ryzyku') or 0}, "
                     f"potrzeba {MIN_W_RYZYKU})")
        else:
            L.append(f"· {w} dni: stoi <b>{r['s'] * 100:.0f}%</b>, "
                     f"zeszlo {100 - r['s'] * 100:.0f}% (n={r['w_ryzyku']})")
    L += ["", "<b>Z czego skladaja sie zejscia</b>"]
    L.append(f"· zdjete przed wygasnieciem (mogly sie sprzedac): {p['zdjeta']}")
    L.append(f"· wygasle, czyli nikt nie kupil: {p['wygasla']}")
    L.append(f"· nie wiem (zniknelo w oknie niepewnosci): {p['nie_wiem']}")
    if p["udzial_zdjetych_pct"] is not None:
        L.append(f"· z zejsc o znanym powodzie zdjete to <b>{p['udzial_zdjetych_pct']}%</b>")
    if p["mediana_wieku_zdjetych"] is not None:
        L.append(f"· mediana wieku zdjetego ogloszenia: "
                 f"<b>{p['mediana_wieku_zdjetych']} dni</b> od wystawienia")
    if d["pasma"]:
        L += ["", "<b>Wg polki cenowej</b>"]
        for s in d["pasma"]:
            r30 = (s["krzywa"]["po_wieku"].get(30) or {}).get("s")
            med = s["powody"]["mediana_wieku_zdjetych"]
            czesci = [f"n={s['n']}"]
            czesci.append(f"po 30 dniach stoi {r30 * 100:.0f}%" if r30 is not None
                          else "po 30 dniach: nie wiem")
            if med:
                czesci.append(f"zdjete po ~{med} dniach")
            L.append(f"· <b>{s['pasmo']}</b>: {', '.join(czesci)}")
    L += ["", "<i>ⓘ \"Zdjete\" nie znaczy \"sprzedane\": znaczy, ze sprzedawca "
               "zdjal ogloszenie przed wygasnieciem. Wznowien pod nowym id jest "
               "malo (1% zniknięć), wiec to najlepsza dostepna poszlaka sprzedazy. "
               "Ceny, za ktora rower zeszedl, OLX nie publikuje i nie da sie jej "
               "policzyc.</i>"]
    if d["bez_daty_wystawienia"]:
        L.append(f"<i>ⓘ {d['bez_daty_wystawienia']} ogloszen pominieto: brak daty "
                 f"wystawienia, czyli nie ma od czego liczyc wieku.</i>")
    return "\n".join(L)


if __name__ == "__main__":
    if "--json" in sys.argv:
        print(json.dumps(zmierz(), ensure_ascii=False, indent=1))
    else:
        import re as _re
        print(_re.sub(r"</?(b|i)>", "", raport()))
