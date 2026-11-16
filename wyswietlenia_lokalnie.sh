#!/bin/zsh
# Tygodniowy pomiar wyswietlen, uruchamiany z komputera wlasciciela.
#
# DLACZEGO NIE Z GITHUBA. OLX blokuje serwerownie GitHub Actions - zmierzone
# 16.11.2026 na dwoch biegach, 45 prob i 45 odpowiedzi HTTP 403. Blokada jest
# na ADRESIE, nie na kliencie, wiec przegladarka na runnerze nic nie zmienia.
# Lacze domowe jest tu jedynym adresem, ktory OLX wpuszcza.
#
# OSOBNY KATALOG, NIE KATALOG ROBOCZY. Ten skrypt robi `reset --hard`, wiec
# nie wolno mu dotykac folderu, w ktorym cokolwiek sie pisze. Pracuje na
# wlasnej, plytkiej kopii repozytorium.
set -u

KAT="$HOME/dev/letaicook/dealhawk-wyswietlenia"
REPO="https://github.com/HenryFordEscort/dealhawk.git"
LOG="$KAT/ostatni.log"

if [ ! -d "$KAT/.git" ]; then
  mkdir -p "$(dirname "$KAT")"
  git clone --depth=1 "$REPO" "$KAT" >/dev/null 2>&1 || exit 1
fi
cd "$KAT" || exit 1

# JEDEN LOG, NADPISYWANY. Poprzedni LaunchAgent w tym projekcie wpadl w petle
# restartow i zostawil 146 MB tracebackow - tu log zaczyna sie od zera przy
# kazdym przebiegu i nie ma prawa urosnac.
: > "$LOG"
exec >>"$LOG" 2>&1
echo "=== $(date '+%Y-%m-%d %H:%M') start"

git fetch --depth=1 origin main && git reset --hard FETCH_HEAD || {
  echo "nie udalo sie pobrac repozytorium"; exit 1; }

python3 wyswietlenia.py || { echo "pomiar nieudany"; exit 1; }

git add wyswietlenia.jsonl 2>/dev/null
if git diff --staged --quiet; then
  echo "nic nowego do zapisania"
  exit 0
fi
git -c user.name="DealHawk Bot" -c user.email="bot@dealhawk" \
    commit -q -m "wyswietlenia: tygodniowa probka"

# Ta sama droga co w workflowach: jawna baza zamiast `git pull --rebase`,
# bo na plytkim klonie gole pull sciaga cale repozytorium.
for i in 1 2 3; do
  BAZA=$(git rev-parse HEAD~1) || exit 1
  if git fetch --depth=1 origin main \
     && git rebase --onto FETCH_HEAD "$BAZA" \
     && git push origin HEAD:main; then
    echo "zapisane (proba $i)"
    exit 0
  fi
  git rebase --abort 2>/dev/null
  sleep 5
done
echo "nie udalo sie wypchnac dziennika"
exit 1
