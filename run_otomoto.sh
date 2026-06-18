#!/bin/bash
# Uruchomienie trackera Otomoto.
# Ustaw zmienne środowiskowe przed użyciem:
#   export TELEGRAM_BOT_TOKEN="..."
#   export TELEGRAM_CHAT_ID="..."
# Lub wpisz je poniżej (tylko lokalnie, nie pushuj do repo!).

# export TELEGRAM_BOT_TOKEN=""
# export TELEGRAM_CHAT_ID=""

cd "$(dirname "$0")"
python3 otomoto_tracker.py
