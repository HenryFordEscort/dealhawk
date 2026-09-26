#!/usr/bin/env bash
# HAK STARTOWY SESJI (26.09.2026)
#
# POWOD. Wlasciciel poprosil 20.09 o „mechanizmy ktore uchronia bota przed
# zjebaniem sie", bo zleca zmiany takze w innych czatach, ktore nie znaja
# kontekstu. Dotad cala obrona byla BIERNA: `CLAUDE.md` i `AGENTS.md` lezaly
# i czekaly, az ktos je przeczyta, a `tests.yml` lapal zepsucie dopiero PO
# fakcie. Ten hak odpala sie SAM, zanim padnie pierwsza linijka kodu.
#
# NIE MA TU ANI JEDNEJ WLASNEJ REGULY. Tresc jest WYCIAGANA z `AGENTS.md`,
# bo dwie kopie tej samej reguly rozjezdzaja sie przy pierwszej poprawce
# i ten projekt ma to opisane na wlasnych wpadkach. Gdy tamten plik sie
# zmieni, hak zmienia sie razem z nim, bez pamietania o nim.
#
# BEZ `set -e` Z ROZMYSLEM. Hak, ktory wywroci sie przed wypisaniem
# ostrzezen, jest gorszy niz jego brak - a ostrzezenia sa tu wazniejsze od
# instalacji. Kazdy krok radzi sobie z wlasna awaria osobno i zawsze
# dochodzimy do konca.
set -uo pipefail

KATALOG="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
cd "$KATALOG" 2>/dev/null || exit 0

# ---------------------------------------------------------------- zaleznosci
# TYLKO W SESJI ZDALNEJ. Na cudzym komputerze `pip install` do systemowego
# Pythona jest nieproszonym gosciem; w kontenerze sesji jest konieczny, bo
# bez `cloudscraper` nie zaimportuje sie ani jeden modul bota.
#
# `install -r`, nie `install --upgrade`: stan kontenera jest zapamietywany po
# wykonaniu haka, wiec drugie uruchomienie ma byc tanie.
if [ "${CLAUDE_CODE_REMOTE:-}" = "true" ] && [ -f requirements.txt ]; then
    if ! python3 -c "import requests, cloudscraper" 2>/dev/null; then
        pip install --quiet -r requirements.txt 2>&1 | tail -3
    fi
fi

# ------------------------------------------------------------------- atrapy
# ATRAPA TOKENU, NIE SEKRET. `tracker.py` czyta `os.environ["TELEGRAM_BOT_TOKEN"]`
# na poziomie modulu, wiec bez tego GOLY `import tracker` wywraca sie na
# `KeyError` - sprawdzone 26.09.2026. Kazdy, kto chcial tu cokolwiek
# uruchomic, musial najpierw sam odkryc to zaklecie.
#
# Prawdziwych sekretow w kontenerze sesji nie ma i byc nie moze, wiec te
# wartosci niczego nie przeslaniaja. Wysylka i tak nie ma dokad isc:
# zmierzone 26.09, Kleinanzeigen, OLX i willhaben oddaja z tej sieci kod 000.
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
    {
        echo 'export TELEGRAM_BOT_TOKEN=dummy'
        echo 'export TELEGRAM_CHAT_ID=0'
    } >> "$CLAUDE_ENV_FILE"
fi

echo "=============================================================="
echo " DealHawk - zanim ruszysz kod"
echo "=============================================================="
echo

# ------------------------------------------------------- czy baza jest zdrowa
# SESJA MA WIEDZIEC, CZY STARTUJE NA ZEPSUTYM KODZIE. Bez tego pierwsza
# godzina pracy moze isc na diagnoze awarii, ktora byla tu juz przed nami.
if TELEGRAM_BOT_TOKEN=dummy TELEGRAM_CHAT_ID=0 \
   python3 -c "import tracker, otomoto_tracker, najlepsze" 2>/tmp/dealhawk_import.err; then
    echo "Baza zdrowa: oba boty i kanal najlepszych importuja sie poprawnie."
else
    echo "!! UWAGA: MODULY BOTA NIE IMPORTUJA SIE. Sesja startuje na zepsutej"
    echo "!! bazie - napraw to, zanim cokolwiek dolozysz. Blad:"
    sed 's/^/!!   /' /tmp/dealhawk_import.err | tail -4
fi
echo

# --------------------------------------------- reguly, wyciagane z AGENTS.md
# Wyciagamy DWA rozdzialy: twarde ograniczenia i komendy do uruchomienia.
# Reszta drogowskazu zostaje w pliku - hak ma obudzic czujnosc, a nie
# przepisac dokumentacje do okna terminala.
if [ -f AGENTS.md ]; then
    awk '/^## Trzy rzeczy/,/^## Co cie zlapie|^## Co ciÄ™ zĹ‚apie|^## Co cię złapie/' AGENTS.md \
        | grep -v '^## Co ci' \
        | sed '/^[[:space:]]*$/d'
    echo
else
    echo "!! Brak AGENTS.md - drogowskaz zniknal z repo. To samo w sobie jest"
    echo "!! ostrzezeniem: sprawdz, czy nie zostal skasowany przy jakiejs zmianie."
fi

echo "Pelne uzasadnienia i pomiary: CLAUDE.md. Skrot: AGENTS.md."
echo
echo "Dwie rzeczy o TYM kontenerze, zmierzone 26.09.2026:"
echo "  - Klon jest PLYTKI. Gole 'git pull --rebase' potrafi tu wisiec"
echo "    kilkanascie minut. Uzywaj 'git fetch --depth=1 origin main'."
echo "  - Kleinanzeigen, OLX i willhaben sa z tej sieci NIEOSIAGALNE (kod 000),"
echo "    wiec pomiaru na zywym serwisie stad nie zrobisz."
echo
echo "Atrapy tokenow sa juz ustawione - testy odpalasz samym 'python3 test.py'."
echo "=============================================================="
