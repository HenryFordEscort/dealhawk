#!/usr/bin/env python3
"""Krzyczy, gdy DealHawk PRZESTAL ZAPISYWAC - czyli gdy po prostu stanal.

PO CO TO JEST. 17-18.09.2026 bot stal 16 GODZIN (zakleszczony bieg w kolejce
GitHuba) i wlasciciel dowiedzial sie o tym dlatego, ze SAM zapytal „od wczoraj
cos jeblo". Przyczyny tamtej awarii sa naprawione - sprzatacz kolejki
w `otomoto.yml`, limity czasu na gita, plytki klon przy poborze. WYKRYWANIE
nie bylo: jedyna czujka na to, ze bot w ogole stanal, to dzienne podsumowanie
o 18:00 UTC. Szesnastogodzinna cisza pokazuje sie wiec najwczesniej nazajutrz,
a bot, ktorego cala wartoscia jest minuta przewagi, stoi przez ten czas.

## Trzy decyzje konstrukcyjne, kazda z powodem

**NIE SIEDZI W DEALHAWKU, bo martwy bot nie krzyknie.** To ta sama zasada co
„alarm o zerwanej drodze nie moze jechac ta droga" (18.09.2026). Czujka jedzie
w lancuszku Otomoto - ten chodzi co 30 minut niezaleznie i ma juz `actions:
write`, dokladnie jak sprzatacz zakleszczonej kolejki obok.

**PISZE BOTEM SAMOCHODOWYM** (`TELEGRAM_BOT_TOKEN_OTOMOTO`), nie rowerowym.
18.09.2026 padl token DealHawka i kazda jego wysylka szla w prozne przez pol
dnia, a bieg konczyl sie zielono. Alarm wysylany tamtym tokenem nie mialby
wtedy jak dojsc - a to wlasnie wtedy jest najbardziej potrzebny.

**NIE IMPORTUJE `tracker.py` ANI ZADNEGO MODULU BOTA.** Gdyby ktos zepsul
trackera - a przed tym wlasnie broni cala siatka z 20.09 - czujka oparta na
jego imporcie padlaby razem z nim i zamilkla w jedynej chwili, ktora sie
liczy. Stad gole `requests` i wlasny, maly czytnik stanu.

## Co dokladnie mierzy

Date ostatniego commita na `main`, ktory ruszyl `seen.json`. NIE „czy bieg sie
odpalil": zapis lokalny bez pusha jest w tej konstrukcji ZEREM (18.09.2026),
bo runner jest jednorazowy, a jedyna pamiecia bota jest `main`. Brak commita
znaczy wiec, ze pamiec bota nie idzie do przodu - i dokladnie to boli, bez
wzgledu na to, czy przyczyna siedzi w kolejce GitHuba, w gicie, czy w kodzie.

Date podaje WOLAJACY (`--minut`), bo pobranie jej to jedno `gh api` i nalezy
do workflow. Dzieki temu caly modul da sie przetestowac bez sieci.

    python czujka_ciszy.py --minut 75
    python czujka_ciszy.py --minut 3 --sucho
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

STAN_FILE = Path("cisza_stan.json")

# PROG 60 MINUT, oparty na DWoCH kotwicach, nie na przeczuciu:
#
# 1. Czujka chodzi co 30 minut, wiec dwa jej cykle to 60. To ta sama logika,
#    ktora dobiera prog sprzataczowi kolejki obok („stan queued dluzszy niz
#    dwa cykle szturchniec to zakleszczenie, nie zatloczenie"). Jeden
#    przegapiony cykl nie robi wtedy falszywego alarmu.
# 2. Najdluzsza ZMIERZONA zdrowa przerwa miedzy zapisami to 297 s (pomiar
#    z 11 godzin po naprawie poboru, 18/19.09.2026), przy medianie 63-73 s.
#    Szescdziesiat minut to dwunastokrotnosc tamtego ogona.
#
# Ponizej 30 minut nie da sie i tak niczego wykryc - czujka po prostu nie
# chodzi czesciej.
CISZA_PROG_MIN = 60


def werdykt(minut_ciszy, stan, prog=CISZA_PROG_MIN):
    """Co zrobic: „alarm", „powrot" albo nic. Funkcja CZYSTA.

    Alarm leci RAZ na wejsciu w cisze i RAZ przy powrocie. Ta sama zasada co
    przy alarmie o braku `topowe_modele.json` (09.09.2026): bez wiadomosci
    o powrocie nie wiadomo, czy cisza jest juz prawdziwa, a przy 48 biegach
    dziennie powtarzanie alarmu zamienilo by go w halas.

    Zwraca `(co, nowy_stan)`."""
    bylo = bool((stan or {}).get("cisza"))
    jest = minut_ciszy >= prog
    if jest and not bylo:
        return "alarm", {"cisza": True, "minut": int(minut_ciszy)}
    if bylo and not jest:
        return "powrot", {"cisza": False, "minut": 0}
    return None, dict(stan or {})


def _godziny(minut):
    return f"{minut} min" if minut < 90 else f"{minut / 60:.1f} h"


def wiadomosc_alarm(minut, prog=CISZA_PROG_MIN):
    return (f"🚨 <b>DealHawk MILCZY od {_godziny(int(minut))}</b>\n\n"
            f"Bot nie zapisał ani jednego ogłoszenia od {_godziny(int(minut))}, "
            f"a normalnie robi to co minutę. To znaczy, że stanął - nie że "
            f"rynek jest pusty.\n\n"
            f"Piszę do Ciebie botem samochodowym, bo rowerowy może być "
            f"właśnie tą zepsutą częścią.\n\n"
            f"Zajrzyj do zakładki Actions: najczęstsze przyczyny to "
            f"zakleszczony bieg w kolejce albo zepsuty token.")


def wiadomosc_powrot(minut_ciszy):
    return (f"✅ <b>DealHawk wrócił</b>\n\n"
            f"Znowu zapisuje ogłoszenia. Cisza trwała "
            f"{_godziny(int(minut_ciszy))}.")


def wczytaj_stan(plik=None):
    # SCIEZKA NIGDY W DOMYSLNYM ARGUMENCIE - wiaze wartosc w chwili definicji
    # modulu, wiec podmiana stalej w tescie nie mialaby skutku. Ta pomylka
    # wyszla w tym repo cztery razy.
    plik = Path(plik) if plik else STAN_FILE
    try:
        return json.loads(plik.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def zapisz_stan(stan, plik=None):
    plik = Path(plik) if plik else STAN_FILE
    plik.write_text(json.dumps(stan, ensure_ascii=False) + "\n",
                    encoding="utf-8")


def wyslij(tekst):
    """Telegram botem SAMOCHODOWYM. Zwraca True, gdy doszlo."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("BLAD: brak TELEGRAM_BOT_TOKEN albo TELEGRAM_CHAT_ID")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": tekst, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=20)
        r.raise_for_status()
        return True
    except Exception as e:
        print(f"BLAD wysylki: {e}")
        return False


def minut_od(iso):
    """Ile minut minelo od podanej daty ISO (z 'Z' albo z przesunieciem)."""
    t = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - t).total_seconds() / 60


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--minut", type=float, help="ile minut od ostatniego zapisu")
    g.add_argument("--ostatni", help="data ostatniego zapisu (ISO)")
    ap.add_argument("--sucho", action="store_true", help="nie wysylaj, tylko powiedz")
    a = ap.parse_args(argv)

    minut = a.minut if a.minut is not None else minut_od(a.ostatni)
    stan = wczytaj_stan()
    co, nowy = werdykt(minut, stan)
    print(f"cisza: {minut:.0f} min (prog {CISZA_PROG_MIN}), werdykt: {co or 'nic'}")

    if co is None:
        return 0
    tekst = (wiadomosc_alarm(minut) if co == "alarm"
             else wiadomosc_powrot(stan.get("minut", 0)))
    if a.sucho:
        print(tekst)
        return 0
    # STAN ZAPISUJEMY NIEZALEZNIE OD TEGO, CZY WYSYLKA SIE UDALA. Inaczej
    # nieudana wysylka wracalaby co 30 minut i jedna cicha strate zamienila
    # w petle halasu - ta sama zasada co przy nieudanej wysylce na kanale
    # najlepszych (18.09.2026).
    zapisz_stan(nowy)
    return 0 if wyslij(tekst) else 1


if __name__ == "__main__":
    sys.exit(main())
