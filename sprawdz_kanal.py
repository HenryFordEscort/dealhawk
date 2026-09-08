#!/usr/bin/env python3
"""Sprawdza, czy bot umie pisać na wskazanym czacie. Uruchamiane RĘCZNIE,
raz, przy zakładaniu kanału BestDealHawk.

    python sprawdz_kanal.py

Pyta o token i numer kanalu, wiec nie trzeba niczego wpisywac w linie
polecen. To NIE jest wygoda, tylko usuwanie realnego zrodla bledow: token
podany jako zmienna srodowiskowa lapie cudzyslowy, spacje i znak konca linii
z kopiowania, a Telegram odpowiada na to samym "Unauthorized", ktore
wyglada jak zly token, choc token jest dobry.

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
import re
import sys

import requests


_WZ_TOKENU = re.compile(r"^\d{6,}:[A-Za-z0-9_-]{30,}$")


def wyczysc_token(surowy: str) -> str:
    """Zdejmuje smieci z kopiowania: spacje, cudzyslowy, 'bot' z przodu.

    Najczestsza pomylka to skopiowanie razem z cudzyslowem albo ze spacja
    na koncu. Telegram odpowiada wtedy 'Unauthorized', czyli komunikatem
    nieodrozninalnym od naprawde zlego tokenu - i szukanie idzie w zla strone.
    """
    t = (surowy or "").strip().strip("'\"").strip()
    # Adres API to ".../bot<token>", wiec czesc ludzi kopiuje razem z "bot".
    if t.lower().startswith("bot") and re.match(r"^\d{6,}:", t[3:]):
        t = t[3:]
    return t


def sprawdz(token: str, chat_id: str) -> int:
    if not _WZ_TOKENU.match(token):
        print("To nie wyglada na token bota.")
        print()
        print("  Token ma postac:  1234567890:AAH-cos-tam-dlugiego")
        print("  czyli liczba, dwukropek, i dlugi ciag liter i cyfr.")
        print()
        print(f"  Dostalem cos o dlugosci {len(token)} znakow"
              + (f", zaczynajace sie od '{token[:12]}...'" if token else " (puste)"))
        print()
        print("  Wez go od @BotFather:  /mybots -> wybierz bota -> API Token")
        return 1
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
        if "unauthorized" in opis.lower():
            print()
            print("  Telegram nie uznaje tego tokenu. Dwie mozliwosci:")
            print("  1. To token INNEGO bota niz ten dodany do kanalu.")
            print("  2. Token zostal uniewazniony (Revoke) i jest juz martwy.")
            print()
            print("  Wez swiezy: @BotFather -> /mybots -> bot -> API Token")
            return 1
        if "chat not found" in opis.lower():
            print()
            print("  Najczęstsze przyczyny, w tej kolejności:")
            print("  1. Bot NIE jest administratorem tego kanału.")
            print("     Kanał -> Zarządzaj -> Administratorzy -> Dodaj -> Twój bot")
            print("  2. Numer bez minusa albo bez przedrostka -100.")
            print("     Kanał prywatny ma numer w postaci -1001234567890.")
            print("  3. Przekleiłeś numer wiadomości zamiast numeru kanału.")
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


def numer_z_linku(tekst: str) -> str:
    """Przyjmuje i gotowy numer, i link do wiadomosci z kanalu.

    Wlasciciel ma pod reka LINK ("Kopiuj link do wiadomosci"), a nie numer
    z przedrostkiem -100. Kazanie mu przepisywac liczbe i doklejac -100 to
    krok, w ktorym nie ma czego sie nauczyc, a mozna sie pomylic.
    """
    t = (tekst or "").strip()
    m = re.search(r"t\.me/c/(\d+)", t)
    if m:
        return "-100" + m.group(1)
    if re.fullmatch(r"-?\d+", t):
        return t if t.startswith("-") else "-100" + t
    return t


if __name__ == "__main__":
    # Sprawdzamy TEN bot, ktory bedzie pisal na kanal najlepszych: osobny,
    # gdy wlasciciel taki zalozyl, a DealHawkowy, gdy nie.
    token = wyczysc_token(os.environ.get("TELEGRAM_BEST_BOT_TOKEN")
                          or os.environ.get("TELEGRAM_BOT_TOKEN") or "")
    if not token:
        print("Token bota (@BotFather -> /mybots -> bot -> API Token).")
        print("Nic sie nie wyswietli podczas wklejania, to normalne.")
        try:
            import getpass
            token = wyczysc_token(getpass.getpass("  wklej token i enter: "))
        except (EOFError, KeyboardInterrupt):
            print("\nprzerwane")
            sys.exit(1)

    czat = sys.argv[1] if len(sys.argv) > 1 else ""
    if not czat:
        print()
        print("Numer kanalu albo link do wiadomosci z niego")
        print("(Telegram -> przytrzymaj wiadomosc -> Kopiuj link do wiadomosci).")
        try:
            czat = input("  wklej i enter: ")
        except (EOFError, KeyboardInterrupt):
            print("\nprzerwane")
            sys.exit(1)
    print()
    sys.exit(sprawdz(token, numer_z_linku(czat)))
