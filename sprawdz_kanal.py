#!/usr/bin/env python3
"""Sprawdza, czy bot umie pisać na wskazanym czacie. Uruchamiane RĘCZNIE,
raz, przy zakładaniu kanału BestDealHawk.

    TELEGRAM_BOT_TOKEN='123:ABC' python sprawdz_kanal.py -1001234567890

Po co osobne narzędzie: numer czatu wpisany do sekretu GitHuba działa albo
nie działa dopiero przy pierwszej prawdziwej ofercie, czyli po godzinach.
Literówka albo brak uprawnień administratora wychodziłyby wtedy jako CISZA,
nieodróżnialna od "nie było dobrych ofert". To sprawdzenie zamyka pętlę
w dziesięć sekund.

NIE czyta `getUpdates` z rozmysłu. DealHawk odpytuje ten sam strumień
z przesuwanym wskaźnikiem (`read_telegram_commands`), a Telegram po takim
odczycie kasuje starsze wpisy. Narzędzie, które by tam zaglądało, ścigałoby
się z botem i przegrywało losowo.
"""
import os
import sys

import requests


def sprawdz(token: str, chat_id: str) -> int:
    api = f"https://api.telegram.org/bot{token}"

    # 1. Czy czat w ogóle istnieje i czy bot go widzi.
    try:
        r = requests.get(f"{api}/getChat", params={"chat_id": chat_id}, timeout=15)
        d = r.json()
    except Exception as e:
        print(f"BŁĄD: nie udało się połączyć z Telegramem: {e}")
        return 1

    if not d.get("ok"):
        opis = d.get("description", "")
        print(f"NIE DZIAŁA: {opis}")
        if "chat not found" in opis.lower():
            print()
            print("  Najczęstsze przyczyny, w tej kolejności:")
            print("  1. Bot NIE jest administratorem tego kanału.")
            print("     Kanał -> Zarządzaj -> Administratorzy -> Dodaj -> Twój bot")
            print("  2. Numer bez minusa albo bez przedrostka -100.")
            print("     Kanał prywatny ma numer w postaci -1001234567890.")
            print("  3. Przekleiłeś numer wiadomości zamiast numeru kanału.")
        elif "unauthorized" in opis.lower():
            print()
            print("  Token bota jest zły. Weź go od @BotFather komendą /mytoken.")
        return 1

    czat = d["result"]
    print(f"Czat znaleziony: {czat.get('title') or czat.get('username') or chat_id}")
    print(f"  typ: {czat.get('type')}")

    # 2. Czy bot MOŻE tam pisać. Samo istnienie czatu nie wystarcza -
    #    administrator kanału bez prawa "Publikowanie wiadomości" widzi czat
    #    i milczy, a to jest dokładnie ta cicha awaria, której unikamy.
    try:
        r = requests.post(f"{api}/sendMessage", json={
            "chat_id": chat_id,
            "text": ("✅ <b>BestDealHawk</b>\n\nTen kanał jest poprawnie "
                     "podłączony. Wpisz teraz numer tego czatu jako sekret "
                     "<code>TELEGRAM_BEST_CHAT_ID</code> w GitHubie.\n\n"
                     "Tę wiadomość możesz skasować."),
            "parse_mode": "HTML",
        }, timeout=15)
        d = r.json()
    except Exception as e:
        print(f"BŁĄD przy wysyłce: {e}")
        return 1

    if not d.get("ok"):
        print(f"NIE DZIAŁA: bot widzi czat, ale nie może na nim pisać.")
        print(f"  Telegram mówi: {d.get('description')}")
        print()
        print("  Zwykle znaczy to, że bot jest administratorem, ale BEZ prawa")
        print("  'Publikowanie wiadomości'. Włącz je w ustawieniach kanału.")
        return 1

    print()
    print("DZIAŁA. Wiadomość testowa poszła na kanał - sprawdź telefon.")
    print()
    print("Teraz wpisz ten numer jako sekret w GitHubie:")
    print()
    print(f"    nazwa:    TELEGRAM_BEST_CHAT_ID")
    print(f"    wartosc:  {chat_id}")
    print()
    print("  GitHub -> Twoje repo -> Settings -> Secrets and variables")
    print("  -> Actions -> New repository secret")
    return 0


if __name__ == "__main__":
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("Brak tokenu bota. Uruchom tak (token weź od @BotFather):")
        print()
        print("    TELEGRAM_BOT_TOKEN='123456:ABC-DEF...' python sprawdz_kanal.py -1001234567890")
        sys.exit(1)
    if len(sys.argv) != 2:
        print("Podaj numer czatu, na przykład:")
        print()
        print("    TELEGRAM_BOT_TOKEN='...' python sprawdz_kanal.py -1001234567890")
        sys.exit(1)
    sys.exit(sprawdz(token, sys.argv[1].strip()))
