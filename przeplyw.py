#!/usr/bin/env python3
"""Przeplyw polki premium - CALA polka co dobe, nie probka.

PO CO (20.09.2026). Dwa pytania wlasciciela, na ktore spis kategorii nie
odpowiada: ile rowerow miesiecznie wchodzi na rynek i ile go opuszcza. Spis
(`spis_rynku.py`) mowi tylko, ILE STOI - a stan to wynik obu przeplywow razem
i z jednej liczby nie da sie ich rozdzielic.

Dozorca (`dozorca.py`) mierzy przeplyw, ale na 41 zapytaniach modelowych, wiec
jego rama jest NIEPELNA i zmienna: dopisanie zapytania wygladalo jak przyrost
rynku (zmierzone: 26 z 30 zapytan "urosло" rowno 4,7x w dniu, gdy zmienil sie
zbieracz). Rama, ktora sie rusza, nie mierzy trendu.

Ten plik bierze rame PELNA i STALA: wszystkie ogloszenia z kategorii rowerow
elektrycznych od 8000 zl w gore, co do jednego. To jest polka, na ktorej bot
realnie handluje.

DLACZEGO PASMAMI PO 1-2 TYS. ZL. API OLX-a oddaje najwyzej 1000 rekordow na
zapytanie (`total_elements` zatrzymuje sie na 1000, choc `visible_total_count`
podaje prawde). Cala polka 8k+ to ~3900 ofert, wiec jednym zapytaniem nie da
sie jej przejsc. Pasma sa tak dobrane, zeby kazde bylo pod tysiacem (zmierzone
20.09.2026: najgrubsze ma 735). Gdy ktores przekroczy 1000, `spisz()` KRZYCZY
zamiast po cichu obciac ogon - bo obcięty ogon to wlasnie brakujace dane.

Pliki:
  polka_stan.json        - kto stoi na polce teraz (nadpisywany, odtwarzalny)
  polka_zdarzenia.jsonl  - dziennik wejsc i wyjsc, append-only, NIGDY nie kasowany

Uruchom:
  python3 przeplyw.py            # przejdz polke i zapisz zmiany
  python3 przeplyw.py --raport   # ile wchodzi, ile wychodzi, jak dlugo stoi
"""
import json
import statistics
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from olx import olx_get

STAN_FILE = Path("polka_stan.json")
ZDARZENIA_FILE = Path("polka_zdarzenia.jsonl")

KATEGORIA_EBIKE = 1649

# Pasma cenowe polki. Kazde musi zmiescic sie pod limitem 1000 rekordow API.
PASMA = [(8000, 9000), (9000, 10000), (10000, 12000), (12000, 14000),
         (14000, 16000), (16000, 20000), (20000, 25000), (25000, None)]

LIMIT_API = 40          # ile rekordow na jedno zapytanie
SUFIT_API = 1000        # tyle najwyzej oddaje OLX na jedno zapytanie
PAUZA = 0.7             # grzecznosc wobec OLX

# Ogloszenie musi zniknac z DWOCH przebiegow z rzedu, zeby uznac je za zejscie.
# Jeden brak to moze byc chwilowa dziura w wynikach API (to samo zalozenie, co
# BRAKI_DO_SPRAWDZENIA w dozorcy - tam zmierzone: 18% zniknięć to falszywy alarm).
BRAKI_DO_ZEJSCIA = 2


def _adres(lo, hi, offset):
    u = (f"https://www.olx.pl/api/v1/offers/?offset={offset}&limit={LIMIT_API}"
         f"&category_id={KATEGORIA_EBIKE}&filter_float_price:from={lo}")
    return u + (f"&filter_float_price:to={hi}" if hi else "")


def przejdz_pasmo(lo, hi):
    """Wszystkie ogloszenia z pasma. Zwraca (oferty, ostrzezenia).

    Oferta to same FAKTY z API: id, cena, data wystawienia, czy firma, adres.
    Zadnych wnioskow - te liczy `raport()` z dziennika."""
    oferty, ostrz = {}, []
    ile = None
    offset = 0
    while True:
        r = olx_get(_adres(lo, hi, offset), timeout=25)
        if r is None or r.status_code != 200:
            ostrz.append(f"pasmo {lo}-{hi or 'wyzej'}: OLX nie odpowiedzial "
                         f"na offset {offset} - pasmo NIEPELNE")
            return oferty, ostrz            # niepelne pasmo, ale zwracamy co jest
        try:
            d = r.json()
        except Exception:
            ostrz.append(f"pasmo {lo}-{hi or 'wyzej'}: odpowiedz nie jest JSON-em")
            return oferty, ostrz
        meta = d.get("metadata") or {}
        if ile is None:
            ile = meta.get("visible_total_count")
            if isinstance(ile, int) and ile > SUFIT_API:
                # KRZYCZYMY. Ciche obciecie ogona to dokladnie ten rodzaj
                # niepelnych danych, przed ktorym ten plik ma chronic.
                ostrz.append(f"pasmo {lo}-{hi or 'wyzej'} ma {ile} ofert, a API "
                             f"oddaje najwyzej {SUFIT_API}: PODZIEL TO PASMO "
                             f"w przeplyw.PASMA, inaczej ogon jest niewidoczny")
        for o in (d.get("data") or []):
            oid = str(o.get("id") or "")
            if not oid:
                continue
            cena, stan_rzeczy = None, None
            for par in (o.get("params") or []):
                if par.get("key") == "price":
                    cena = ((par.get("value") or {}).get("value"))
                elif par.get("key") == "state":
                    stan_rzeczy = ((par.get("value") or {}).get("key"))
            oferty[oid] = {
                "p": cena if isinstance(cena, int) else None,
                "stan": stan_rzeczy,
                "wystawiono": o.get("created_time"),
                "odswiezono": o.get("last_refresh_time"),
                "wazne_do": o.get("valid_to_time"),
                "firma": bool(o.get("business")),
                "url": o.get("url"),
                "woj": ((o.get("location") or {}).get("region") or {}).get("name"),
            }
        if not (d.get("data") or []) or offset + LIMIT_API >= min(
                SUFIT_API, (ile if isinstance(ile, int) else SUFIT_API)):
            break
        offset += LIMIT_API
        time.sleep(PAUZA)
    return oferty, ostrz


def przejdz_polke():
    """Cala polka: {id: fakty}, plus lista ostrzezen o niepelnosci."""
    wszystkie, ostrz = {}, []
    for lo, hi in PASMA:
        o, w = przejdz_pasmo(lo, hi)
        wszystkie.update(o)                 # granice pasm zachodza, id odsiewa dublе
        ostrz += w
        time.sleep(PAUZA)
    return wszystkie, ostrz


def _wiek_dni(wystawiono, do_kiedy: datetime):
    try:
        w = datetime.fromisoformat((wystawiono or "").replace("Z", "+00:00"))
        return max((do_kiedy - w.replace(tzinfo=None)).days, 0)
    except Exception:
        return None


def spisz(teraz: str = None) -> dict:
    """Przechodzi polke, porownuje z poprzednim stanem, dopisuje wejscia i wyjscia.

    Wyjscie dostaje do dziennika WIEK OD WYSTAWIENIA, nie od naszego pierwszego
    widzenia. To ta sama poprawka, ktora trzeba bylo zrobic w pomiarze plynnosci:
    ogloszenie zyje mediane 30 dni, zanim bot je zobaczy, wiec wiek liczony od
    nas zanizal czas stania ponad dwukrotnie."""
    teraz = teraz or datetime.now().strftime("%Y-%m-%dT%H:%M")
    biezace, ostrz = przejdz_polke()
    if not biezace:
        # Pusta polka nie istnieje: 3900 ofert nie znika w jeden dzien. To awaria.
        return {"ok": False, "blad": "zero ofert z calej polki - blokada albo "
                                     "zmiana API; stan NIE nadpisany",
                "ostrzezenia": ostrz}
    stan = json.loads(STAN_FILE.read_text(encoding="utf-8")) if STAN_FILE.exists() else {}
    dt = datetime.strptime(teraz, "%Y-%m-%dT%H:%M")
    zdarzenia = []

    for oid, f in biezace.items():
        rec = stan.get(oid)
        if rec is None:
            stan[oid] = {**f, "pierwszy": teraz, "ostatni": teraz, "braki": 0}
            zdarzenia.append({"ts": teraz, "ev": "weszla", "id": oid, "p": f["p"],
                              "firma": f["firma"], "wystawiono": f["wystawiono"],
                              "woj": f["woj"],
                              # wiek w chwili wejscia do naszej ramy: >0 znaczy,
                              # ze ogloszenie istnialo, zanim zaczelismy patrzec
                              "wiek_przy_wejsciu": _wiek_dni(f["wystawiono"], dt)})
        else:
            if rec.get("p") != f["p"] and f["p"] is not None:
                zdarzenia.append({"ts": teraz, "ev": "cena", "id": oid,
                                  "p": f["p"], "p_stara": rec.get("p")})
            rec.update(f)
            rec["ostatni"] = teraz
            rec["braki"] = 0

    for oid in [o for o in stan if o not in biezace]:
        rec = stan[oid]
        rec["braki"] = rec.get("braki", 0) + 1
        if rec["braki"] < BRAKI_DO_ZEJSCIA:
            continue                        # jeden brak to jeszcze nie zejscie
        zdarzenia.append({"ts": teraz, "ev": "wyszla", "id": oid,
                          "p": rec.get("p"), "p0": rec.get("p0", rec.get("p")),
                          "firma": rec.get("firma"),
                          "wystawiono": rec.get("wystawiono"),
                          "wazne_do": rec.get("wazne_do"), "woj": rec.get("woj"),
                          "wiek": _wiek_dni(rec.get("wystawiono"), dt),
                          "u_nas_dni": _wiek_dni(rec.get("pierwszy"), dt)})
        stan.pop(oid)

    if zdarzenia:
        with ZDARZENIA_FILE.open("a", encoding="utf-8") as f:
            for z in zdarzenia:
                f.write(json.dumps(z, ensure_ascii=False) + "\n")
    STAN_FILE.write_text(json.dumps(stan, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "na_polce": len(biezace),
            "weszlo": sum(1 for z in zdarzenia if z["ev"] == "weszla"),
            "wyszlo": sum(1 for z in zdarzenia if z["ev"] == "wyszla"),
            "zmian_ceny": sum(1 for z in zdarzenia if z["ev"] == "cena"),
            "ostrzezenia": ostrz}


def juz_dzis() -> bool:
    """Czy polka byla dzis przechodzona. Przejscie to ~100 zapytan i 2 minuty,
    wiec robimy je raz na dobe - ale probujemy przy kazdym przebiegu dozorcy,
    bo cron GitHuba spoznia sie nieregularnie (mediana 3,6 h, p90 5,7 h,
    zmierzone 15.09.2026). Tak dziennik nie ma dziur w dniach, w ktore GitHub
    mial zly dzien."""
    if not STAN_FILE.exists():
        return False
    try:
        stan = json.loads(STAN_FILE.read_text(encoding="utf-8"))
    except Exception:
        return False
    dzis = date.today().isoformat()
    return any((r.get("ostatni") or "").startswith(dzis) for r in stan.values())


def wczytaj_zdarzenia() -> list:
    if not ZDARZENIA_FILE.exists():
        return []
    out = []
    for linia in ZDARZENIA_FILE.read_text(encoding="utf-8").splitlines():
        if not linia.strip():
            continue
        try:
            out.append(json.loads(linia))
        except json.JSONDecodeError:
            continue
    return out


def przeplyw(dni: int = 28) -> dict:
    """WNIOSEK z dziennika: ile wchodzi i wychodzi na dobe.

    Pierwszy przebieg wpisuje CALA polke jako "weszla" - to nie jest naplyw,
    tylko inwentaryzacja. Dlatego wejscia z ogloszeniem starszym niz doba
    (`wiek_przy_wejsciu` > 1) nie licza sie do naplywu: ogloszenie wystawione
    trzy tygodnie temu nie weszlo dzis na rynek, tylko dzis weszlo w nasze pole
    widzenia. Bez tego rozdzielenia pierwszy dzien pomiaru pokazalby naplyw
    3900 sztuk na dobe."""
    ev = wczytaj_zdarzenia()
    if not ev:
        return {"dni_danych": 0}
    od = (date.today() - timedelta(days=dni)).isoformat()
    swieze = [z for z in ev if (z.get("ts") or "") >= od]
    wejscia = [z for z in swieze if z["ev"] == "weszla"]
    swieze_wejscie = lambda z: (isinstance(z.get("wiek_przy_wejsciu"), int)
                                and z["wiek_przy_wejsciu"] <= 1)
    nowe = [z for z in wejscia if swieze_wejscie(z)]
    stare_wejscia = len(wejscia) - len(nowe)
    wyszly = [z for z in swieze if z["ev"] == "wyszla"]
    dni_obs = len({(z.get("ts") or "")[:10] for z in swieze})
    wieki = sorted(z["wiek"] for z in wyszly if isinstance(z.get("wiek"), int))
    return {
        "dni_danych": dni_obs,
        "nowe_ogloszenia": len(nowe),
        "nowe_na_dobe": round(len(nowe) / dni_obs, 1) if dni_obs else None,
        "wejscia_starych": stare_wejscia,
        "zejscia": len(wyszly),
        "zejscia_na_dobe": round(len(wyszly) / dni_obs, 1) if dni_obs else None,
        "mediana_wieku_zejscia": int(statistics.median(wieki)) if len(wieki) >= 10 else None,
        "udzial_firm_w_nowych": (round(sum(1 for z in nowe if z.get("firma")) / len(nowe) * 100)
                                 if nowe else None),
    }


def struktura(stan=None) -> dict:
    """Co stoi na polce TERAZ, w rozbiciu. Liczone z pelnej ramy, wiec bez
    doboru probki - ale UWAGA NA JEDNO: zbior stojacych ofert z natury
    przewaza wolno schodzace. Ogloszenie, ktore zeszlo w tydzien, jest w takim
    zdjeciu widoczne tylko przez tydzien, a takie, ktore wisi rok, przez caly
    rok. Dlatego "mediana wieku stojacych" NIE JEST czasem sprzedazy i nie
    wolno jej tak podawac. Czas do zejscia liczy `plynnosc.py`."""
    stan = stan if stan is not None else (
        json.loads(STAN_FILE.read_text(encoding="utf-8")) if STAN_FILE.exists() else {})
    if not stan:
        return {}
    teraz = datetime.now()
    wieki = sorted(x for x in (_wiek_dni(v.get("wystawiono"), teraz)
                               for v in stan.values()) if x is not None)
    prywatne_uzywane = [v for v in stan.values()
                        if not v.get("firma")
                        and v.get("stan") in ("used", "almost-new")]
    pu_wieki = sorted(x for x in (_wiek_dni(v.get("wystawiono"), teraz)
                                  for v in prywatne_uzywane) if x is not None)
    out = {
        "na_polce": len(stan),
        "firm_pct": round(sum(1 for v in stan.values() if v.get("firma"))
                          / len(stan) * 100),
        "nowe_pct": round(sum(1 for v in stan.values() if v.get("stan") == "new")
                          / len(stan) * 100),
        "mediana_wieku_stojacych": int(statistics.median(wieki)) if wieki else None,
        "starsze_niz_90_pct": (round(sum(1 for x in wieki if x > 90) / len(wieki) * 100)
                               if wieki else None),
        "nasza_nisza": len(prywatne_uzywane),
    }
    if pu_wieki:
        out["nisza_mediana_wieku"] = int(statistics.median(pu_wieki))
        out["nisza_starsze_niz_90_pct"] = round(
            sum(1 for x in pu_wieki if x > 90) / len(pu_wieki) * 100)
        # Dolna granica naplywu: ogloszenia wystawione w ostatnim tygodniu,
        # ktore JESZCZE stoja. Dolna, bo czesc juz zeszla i tu jej nie widac.
        out["nisza_naplyw_min_na_dobe"] = round(
            sum(1 for x in pu_wieki if x <= 7) / 7, 1)
    return out


def raport() -> str:
    z = lambda v: f"{int(v):,}".replace(",", " ")
    stan = json.loads(STAN_FILE.read_text(encoding="utf-8")) if STAN_FILE.exists() else {}
    p = przeplyw()
    L = ["🔁 <b>Polka 8k+ zl: co wchodzi, co wychodzi</b>",
         f"<i>cala kategoria e-rowery od 8000 zl, nie probka</i>", ""]
    if not stan:
        return ("🔁 <b>Polka 8k+ zl</b>\nJeszcze nie przeszedlem polki. "
                "Uruchom <code>python3 przeplyw.py</code>.")
    sk = struktura(stan)
    L.append(f"Na polce stoi <b>{z(sk['na_polce'])}</b> ogloszen: "
             f"{sk['firm_pct']}% od firm, {sk['nowe_pct']}% to rowery nowe")
    L.append(f"Uzywane od osob prywatnych, czyli nasza konkurencja: "
             f"<b>{z(sk['nasza_nisza'])}</b>")
    if sk.get("nisza_mediana_wieku") is not None:
        L.append(f"· wisza juz mediane {sk['nisza_mediana_wieku']} dni, "
                 f"{sk['nisza_starsze_niz_90_pct']}% dluzej niz 90 dni")
        L.append(f"· naplyw co najmniej {sk['nisza_naplyw_min_na_dobe']} na dobe "
                 f"(~{sk['nisza_naplyw_min_na_dobe'] * 30:.0f} na miesiac)")
    L.append(f"\n<i>ⓘ Mediana wieku STOJACYCH ofert to nie czas sprzedazy. "
             f"Szybko schodzace widac w takim zdjeciu tylko przez chwile, a wiszace "
             f"rok widac caly rok, wiec ta liczba jest z natury przesunieta w gore. "
             f"Ile realnie schodzi - <code>plynnosc.py</code>.</i>")
    if p.get("dni_danych", 0) < 2:
        L.append(f"\n<i>ⓘ Przeplywu jeszcze nie ma: {p.get('dni_danych', 0)} dzien pomiaru. "
                 f"Pierwszy przebieg to inwentaryzacja, nie naplyw. Wejscia i wyjscia "
                 f"beda liczone od drugiej doby.</i>")
        return "\n".join(L)
    L += ["", f"<b>Przeplyw z {p['dni_danych']} dni</b>",
          f"· nowych ogloszen: {z(p['nowe_ogloszenia'])} "
          f"({p['nowe_na_dobe']} na dobe, czyli ~{p['nowe_na_dobe'] * 30:.0f} na miesiac)",
          f"· zejsc z polki: {z(p['zejscia'])} ({p['zejscia_na_dobe']} na dobe)"]
    if p["mediana_wieku_zejscia"] is not None:
        L.append(f"· mediana wieku przy zejsciu: <b>{p['mediana_wieku_zejscia']} dni</b> "
                 f"od wystawienia")
    if p["udzial_firm_w_nowych"] is not None:
        L.append(f"· firmy wystawiaja {p['udzial_firm_w_nowych']}% nowych ogloszen")
    if p["wejscia_starych"]:
        L.append(f"\n<i>ⓘ {z(p['wejscia_starych'])} ogloszen weszlo w pole widzenia, "
                 f"ale bylo starszych niz doba - to nie naplyw, tylko nadrobienie "
                 f"zaleglosci, i nie jest liczone.</i>")
    L.append("\n<i>ⓘ Zejscie z polki to nie sprzedaz. Moze byc sprzedaz, "
             "wygasniecie, zdjecie albo obnizka pod 8000 zl. Ktore z tych - mowi "
             "<code>plynnosc.py</code> na danych dozorcy.</i>")
    return "\n".join(L)


if __name__ == "__main__":
    if "--raport" in sys.argv:
        import re as _re
        print(_re.sub(r"</?(b|i|code)>", "", raport()))
    elif "--raz-na-dobe" in sys.argv and juz_dzis():
        print(json.dumps({"ok": True, "pominiete": "polka juz przeszla dzis"},
                         ensure_ascii=False))
    else:
        w = spisz()
        print(json.dumps(w, ensure_ascii=False))
        for o in w.get("ostrzezenia") or []:
            print("UWAGA:", o)
