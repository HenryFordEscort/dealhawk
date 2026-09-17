#!/usr/bin/env python3
"""Generator GOTOWEJ WIADOMOŚCI z twardą ofertą do sprzedawcy (DE/AT).

SKĄD TO SIĘ WZIĘŁO: właściciel przysłał 17.09.2026 zrzut własnej rozmowy
z Kleinanzeigen. Przy ofercie 2 800 € VB napisał sprzedawcy 2 200 € "fest"
i dołożył cztery rzeczy, których bot dotąd nie umiał złożyć w jedno:
przyznanie, ile sprzedawca woła, odbiór osobisty za gotówkę, OBIETNICĘ BRAKU
DOGADYWANIA NA MIEJSCU i propozycję zaliczki na rezerwację.

CO TA WIADOMOŚĆ NAPRAWDĘ SPRZEDAJE: nie cenę, tylko PEWNOŚĆ. Sprzedawca
oddaje kilkaset euro, a w zamian dostaje koniec z oglądaczami, koniec
z targiem pod domem i termin ustawiony pod siebie. Dlatego kwota musi być
jedna i twarda - "od 2 200 wzwyż" nie kupuje niczego.

CZYM SIĘ RÓŻNI OD `tracker.wiadomosc_oferta`: tamta ma 256 znaków, bo jedzie
w przycisku "kopiuj" (limit `copy_text` w API Telegrama), i niesie samą kwotę.
Ta ma ~800 znaków, więc w przycisku się NIE MIEŚCI i idzie osobną wiadomością,
w bloku do skopiowania. Stare wiadomości zostają nietknięte - dalej obsługują
pierwszy kontakt, kiedy jeszcze nie wiadomo, o czym się rozmawia.

BOT TEGO NIE WYSYŁA. Składa tekst, właściciel kopiuje i wysyła sam, ze swojego
konta. Twarde ograniczenie z CLAUDE.md ("bot NIE negocjuje sam") stoi dalej
i ten moduł go nie rusza: nie ma tu ani jednego żądania poza czytaniem plików.

CZYTAMY WYŁĄCZNIE `seen.json` i `de_stan.json`, oba do odczytu. Zero żądań do
Kleinanzeigen - ta sama zasada co w `rozmiary.py` i `najlepsze.py`.

Uruchom: TELEGRAM_BOT_TOKEN=dummy TELEGRAM_CHAT_ID=0 python3 oferta.py 3515700088
"""
import html as html_mod
import json
import re
import sys
from datetime import date
from pathlib import Path

import tracker as T

SEEN = Path("seen.json")
DE_STAN = Path("de_stan.json")

# Zaokrąglenie kwoty W DÓŁ do pełnych 50 €. Twarda oferta jest DECYZJĄ, a nie
# wynikiem dzielenia - "2.268 €" widać, że policzyła maszyna, i zaprasza do
# kontroferty "2.400 €", czyli dokładnie do targu, którego ta wiadomość ma nie
# być. W dół, nie do najbliższych 50, bo to my nazywamy podłogę, z której nie
# schodzimy; zaokrąglenie w górę oddawałoby pieniądze za nic.
KROK_KWOTY = 50
# Widełki na kwotę wpisaną ręcznie. Poniżej 300 € to literówka (albo rower,
# po który nie jedzie się 800 km), powyżej 20 000 € nic tu nie kupujemy.
CENA_MIN, CENA_MAX = 300, 20000
# Po tylu dniach od zapisania ogłoszenie jest w większości trupem - ta sama
# ostrożność co w `/dojrzale`, gdzie właściciel zobaczył listę nieistniejących
# ofert 13.09.2026.
STARE_PO_DNIACH = 7


def _wczytaj(sciezka):
    """(słownik, czy_udalo_sie). Awaria pliku nie ma prawa wywrócić komendy,
    ale NIE WOLNO jej pokazać jako "nie znam tego ogłoszenia" - to reguła 7."""
    try:
        return json.loads(sciezka.read_text(encoding="utf-8")), True
    except Exception:
        return {}, False


def festpreis(nego_pct) -> bool:
    """Czy sprzedawca napisał "Festpreis" - ODTWORZONE z `nego_pct`.

    `seen.json` nie zapisuje powodów z `negotiation_headroom`, tylko wynik.
    Odtworzenie jest jednak pewne, a nie zgadywane: gałąź Festpreis ma
    NATYCHMIASTOWY return i oddaje `NEGO_BASE_FIXED` co do joty, a każda inna
    ścieżka startuje z 0,05 albo 0,10 i wyłącznie DOKŁADA. Żadna kombinacja
    bonusów nie trafi więc w tę jedną wartość od dołu. Pilnuje tego test, który
    przemiata wszystkie kombinacje - bo gdyby ktoś kiedyś ustawił
    NEGO_BASE_OPEN na 0,02, ta funkcja zaczęłaby kłamać po cichu.

    Zmierzone 17.09.2026 na 2 800 wysłanych ofertach: 52 wpisy (1,9%)."""
    return nego_pct is not None and abs(nego_pct - T.NEGO_BASE_FIXED) < 1e-9


def ma_vb(cena_str) -> bool:
    """Czy w ogłoszeniu stoi "VB" (Verhandlungsbasis, czyli "do negocjacji").

    Czytamy WPROST z pola ceny, a nie z opisu i nie z `nego_pct`: to jest ten
    sam napis, który sprzedawca widzi u siebie na ogłoszeniu. Wiadomość twierdzi
    o nim coś faktycznego ("wiem, że wołasz 2.800 € VB") - pomyłka w tę stronę
    jest natychmiast widoczna dla adresata i kompromituje resztę.

    Zmierzone 17.09.2026: 1 380 z 2 800 wysłanych ofert (49%) ma VB."""
    return bool(re.search(r'\bvb\b', (cena_str or ""), re.I))


def cena_oferty(cena_wywolawcza, nego_pct):
    """(kwota, realny procent zejścia). Kwota do TWARDEJ oferty.

    Bierzemy `cena_po_ogledzinach`, czyli OBA etapy targu naraz, a nie
    `realistic_buy_price`, który zostawia drugi etap na później. Powód jest
    w treści wiadomości: obiecujemy brak dogadywania pod domem, więc zapas na
    ten targ musi siedzieć już w tej kwocie, albo przepada.

    Właściciel przy 2 800 € VB zaproponował 2 200 €, czyli -21,4%. Sufit obu
    etapów w `NEGO_MAX_LACZNIE` stoi na 22% - jego realne zachowanie trafia
    w model prawie co do punktu (jeden przypadek, 17.09.2026, więc to zgodność,
    a nie pomiar).

    Procent zwracamy PO zaokrągleniu, nie przed. Liczba w nagłówku ma być tą
    samą, która idzie w wiadomości - dwie różne to reguła 6."""
    if not cena_wywolawcza or cena_wywolawcza <= 0:
        return None, 0.0
    powody = ["Festpreis (mur)"] if festpreis(nego_pct) else []
    kwota, _ = T.cena_po_ogledzinach(cena_wywolawcza, nego_pct or 0.0, powody)
    if not kwota:
        return None, 0.0
    kwota = int(kwota // KROK_KWOTY * KROK_KWOTY)
    # ZAOKRĄGLENIE NIE MA PRAWA PRZEBIĆ SUFITU. Zmierzone 17.09.2026 na 2 730
    # złożonych ofertach: samo cięcie w dół wypychało skrajne przypadki na 25%
    # przy `NEGO_MAX_LACZNIE` = 22%, bo przy rowerze za 1 000 € pięćdziesiątka
    # to całe 5 punktów procentowych. Sufit jest tam z powodu (niemieckie
    # poradniki: powyżej ~20% nikt nie traktuje oferty poważnie), więc gdy
    # cięcie w dół go łamie, zaokrąglamy w GÓRĘ - wolimy oddać 50 € niż wysłać
    # kwotę, na którą sprzedawca nie odpisze.
    if cena_wywolawcza - kwota > cena_wywolawcza * T.NEGO_MAX_LACZNIE:
        kwota += KROK_KWOTY
    if kwota < KROK_KWOTY or kwota >= cena_wywolawcza:
        # Tanie rowery: zaokrąglenie potrafi zjeść całą różnicę. Wtedy nie ma
        # o czym pisać twardej oferty - lepiej milczeć niż proponować tyle samo.
        return None, 0.0
    return kwota, (cena_wywolawcza - kwota) / cena_wywolawcza


def _de_kwota(n) -> str:
    """2250 → "2.250" (niemiecki separator tysięcy to kropka)."""
    return f"{int(n):,}".replace(",", ".")


def nazwa_modelu(tytul):
    """"E-Bike Fully Cube Stereo Hybrid 120 Pro 2023 Akku 750 Wh" →
    "Cube Stereo Hybrid 120". None, gdy wzorce nic nie rozpoznają.

    Bierzemy klucz, który bot i tak już liczy do wyceny, zamiast wklejać cały
    tytuł ogłoszenia - "Interesse an dem E-Bike Fully Cube Stereo Hybrid 120 Pro
    2023 Akku 750 Wh" czyta się jak wklejka, a nie jak zdanie od człowieka."""
    klucz = T.olx_query_for(tytul or "", None)
    if not klucz:
        return None
    return klucz.title()


def _pl_kwota(n) -> str:
    """2250 → "2 250" (polski separator tysięcy to spacja)."""
    return f"{int(n):,}".replace(",", " ")


def _akapity(*, cena_oferowana, cena_wywolawcza=None, cena_str=None,
             nego_pct=None, tytul=None, zaliczka=False):
    """Lista par (niemiecki, polski) - akapit po akapicie.

    OBA JĘZYKI POWSTAJĄ W TYM SAMYM ROZGAŁĘZIENIU i to jest cała istota tej
    funkcji. Przekład jest dla właściciela jedynym sposobem sprawdzenia, co
    wysyła pod własnym nazwiskiem, więc gdyby mieszkał w osobnej funkcji,
    rozjechałby się z oryginałem przy pierwszej poprawce - i to po cichu,
    bo nikt nie czyta niemieckiego, żeby porównać. Ta sama zasada co przy
    `tracker.litera_ramy`: jedna reguła, jedno miejsce.

    NIE tłumaczymy maszynowo. `tlumacz_opis` zrobił z "Nur 2000 km gelaufen"
    zdanie "Spacerowaliśmy niecałe 2000 km" - przy tekście, który idzie do
    obcego człowieka, taka wpadka kosztuje rower."""
    kwota_de, kwota_pl = _de_kwota(cena_oferowana), _pl_kwota(cena_oferowana)
    model = nazwa_modelu(tytul)
    co_de = f"an dem {model}" if model else "an deinem Rad"
    co_pl = f"twój {model}" if model else "twój rower"

    pary = [(f"Hallo, ich habe ernsthaftes Interesse {co_de} und würde dir "
             f"{kwota_de} € fest anbieten.",
             f"Cześć, poważnie interesuje mnie {co_pl} i oferuję ci za niego "
             f"{kwota_pl} €, na sztywno.")]

    wyw_de = _de_kwota(cena_wywolawcza) if cena_wywolawcza else None
    wyw_pl = _pl_kwota(cena_wywolawcza) if cena_wywolawcza else None
    # SPRZECZNE SYGNAŁY → NIE TWIERDZIMY NIC. Sprzedawca z plakietką "VB" na
    # ogłoszeniu i słowem "Festpreis" w opisie przeczy sam sobie. Napisanie mu
    # "ustawiłeś Festpreis", gdy u siebie widzi "VB", jest po prostu nieprawdą
    # i adresat wyłapie to natychmiast - a na tym zdaniu stoi wiarygodność
    # reszty wiadomości. Neutralne otwarcie niżej jest prawdziwe przy obu
    # odczytach. Kwota zostaje ta ostrożniejsza, z Festpreis.
    # Zmierzone 17.09.2026: 1 taki wpis na 2 800 wysłanych ofert.
    if wyw_de and festpreis(nego_pct) and not ma_vb(cena_str):
        otw_de = (f"Ich weiß, dass du {wyw_de} € als Festpreis angesetzt hast. "
                  f"Falls sich doch noch etwas machen lässt, mache ich dir die "
                  f"Abwicklung so unkompliziert wie möglich: ")
        otw_pl = (f"Wiem, że ustawiłeś {wyw_pl} € jako cenę sztywną. Jeśli "
                  f"jednak dałoby się coś zrobić, załatwiam ci sprawę tak "
                  f"prosto, jak się da: ")
    elif wyw_de and ma_vb(cena_str) and not festpreis(nego_pct):
        otw_de = (f"Mir ist klar, dass du {wyw_de} € VB aufgerufen hast. Dafür "
                  f"mache ich dir die Sache aber so unkompliziert wie möglich: ")
        otw_pl = (f"Wiem, że wołasz {wyw_pl} € do negocjacji. W zamian "
                  f"załatwiam ci sprawę tak prosto, jak się da: ")
    elif wyw_de:
        otw_de = (f"Mir ist klar, dass das unter deinen {wyw_de} € liegt. Dafür "
                  f"mache ich dir die Sache aber so unkompliziert wie möglich: ")
        otw_pl = (f"Wiem, że to poniżej twoich {wyw_pl} €. W zamian załatwiam "
                  f"ci sprawę tak prosto, jak się da: ")
    else:
        otw_de = "Ich mache dir die Sache so unkompliziert wie möglich: "
        otw_pl = "Załatwiam ci sprawę tak prosto, jak się da: "
    pary.append((
        otw_de +
        "ich hole das Rad persönlich ab und zahle bar. Beim Termin richte ich "
        "mich ganz nach dir - ich kann kurzfristig kommen, unter der Woche "
        "abends oder am Wochenende, ganz wie es dir passt.",
        otw_pl +
        "odbieram rower osobiście i płacę gotówką. Termin ustawiam pod ciebie - "
        "mogę przyjechać szybko, w tygodniu wieczorem albo w weekend, jak ci "
        "wygodnie."))

    # SEDNO CAŁEJ WIADOMOŚCI. To jest to, za co sprzedawca schodzi z ceny:
    # pewność, że nikt nie przyjedzie zbijać kolejnych dwustu euro na miejscu.
    pary.append((
        "Damit du weißt, woran du bist: wenn der Zustand der Beschreibung "
        "entspricht, wird vor Ort nicht mehr nachverhandelt. Was wir hier "
        "ausmachen, das gilt.",
        "Żebyś wiedział, na czym stoisz: jeśli stan zgadza się z opisem, na "
        "miejscu już nie negocjuję. Co ustalimy tutaj, to zostaje."))

    if zaliczka:
        pary.append((
            "Wenn du möchtest, überweise ich dir sofort eine Anzahlung, dann "
            "ist das Rad verbindlich reserviert. Den Rest bekommst du bar bei "
            "der Abholung.",
            "Jeśli chcesz, od razu przeleję ci zaliczkę i rower będzie wiążąco "
            "zarezerwowany. Resztę dostaniesz gotówką przy odbiorze."))

    pary.append((f"Wenn {kwota_de} € für dich in Ordnung sind, sag einfach "
                 f"Bescheid und wir machen einen Termin aus.",
                 f"Jeśli {kwota_pl} € ci pasuje, daj znać i umawiamy się "
                 f"na termin."))
    return pary


def tekst_oferty(**kw):
    """Gotowa wiadomość po niemiecku. None, gdy nie ma czego zaproponować.

    ŻADNEGO WYMYŚLONEGO DNIA ODBIORU. Wersja ze zrzutu miała "Mittwochabend",
    ale bot nie wie, kiedy właściciel jeździ, a zły dzień w wiadomości do
    obcego trzeba potem odkręcać. Zamiast daty idzie elastyczność ("kurzfristig",
    "wie es dir passt") - zobowiązanie zostaje, zgadywanie znika. Pilnuje tego
    test, który szuka w tekście nazw dni tygodnia."""
    if not kw.get("cena_oferowana"):
        return None
    return "\n\n".join(de for de, _ in _akapity(**kw))


def tekst_po_polsku(**kw):
    """To samo zdanie po zdaniu po polsku - do SPRAWDZENIA, nie do wysłania.

    Właściciel wysyła tę wiadomość pod własnym nazwiskiem do obcego człowieka,
    a niemieckiego nie czyta. Bez przekładu jest to czarna skrzynka, czyli
    dokładnie to, czego w tym repo nie wolno mu podsuwać."""
    if not kw.get("cena_oferowana"):
        return None
    return "\n\n".join(pl for _, pl in _akapity(**kw))


# --- KOMENDA ---------------------------------------------------------------
# "/oferta 3515700088", "/oferta <wklejony link> 2200", "/of 3515700088 zaliczka".
#
# SŁOWA "oferty" NIE MA na tej liście i to nie jest przeoczenie: ten wzorzec
# jest od dawna zajęty przez `/zycie` w `tracker.process_telegram_commands`.
# Dorzucenie go tutaj po cichu zabrałoby tamtej komendzie jej własną nazwę.
_SLOWA = r'(?:oferta|ofercie|ofert[ęe]|propozycja|propozycj[ęa])'


def _id_z_tekstu(body):
    """Numer ogłoszenia z tego, co właściciel wkleił.

    Link obsługujemy, bo na telefonie to jedyna rzecz, którą da się skopiować
    z powiadomienia jednym stuknięciem - przepisywanie dziesięciu cyfr z ekranu
    nie jest realną drogą i komenda, której nikt nie użyje, jest martwa."""
    b = body or ""
    m = re.search(r'\bwh-(\d{6,12})\b', b, re.I)
    if m:
        return "wh-" + m.group(1)
    if "willhaben" in b.lower():
        m = re.search(r'(\d{6,12})', b)
        return "wh-" + m.group(1) if m else None
    m = re.search(r'/s-anzeige/[^\s]*?/(\d{6,12})-', b, re.I)
    if m:
        return m.group(1)
    m = re.search(r'\b(\d{9,12})\b', b)
    return m.group(1) if m else None


def _cena_z_tekstu(body):
    """Kwota wpisana ręcznie, albo None.

    Najpierw wycinamy adresy i numery ogłoszeń Z OGONEM. Adres Kleinanzeigen
    kończy się na "-217-1745", a 1745 mieści się w widełkach ceny i bez tego
    cięcia wygrywałoby z prawdziwą kwotą podaną obok."""
    reszta = re.sub(r'https?://\S+', ' ', body or "")
    reszta = re.sub(r'\d{6,}(?:-\d+)*', ' ', reszta)
    for x in re.findall(r'\b(\d{3,5})\b', reszta):
        if CENA_MIN <= int(x) <= CENA_MAX:
            return int(x)
    return None


def parse_oferta_command(text):
    """'/oferta 3515700088 2200 zaliczka' → (id, cena|None, zaliczka).

    `id=None` znaczy "komenda rozpoznana, ale bez numeru" - wtedy handler ma
    powiedzieć, czego brakuje. Cisza jest gorsza od błędu (reguła z 13.09.2026:
    "napisalem i nic"). None zwracamy wyłącznie, gdy to w ogóle nie ta komenda.

    Skrót "/of" WYMAGA ukośnika, tak samo jak jednoliterowe skróty rozmiaru:
    bez niego porwałby każde zdanie zaczynające się od "of"."""
    t = (text or "").strip()
    m = re.match(rf'/?{_SLOWA}\b(.*)$', t, re.I | re.S)
    if not m:
        m = re.match(r'/of\b(.*)$', t, re.I | re.S)
        if not m:
            return None
    body = m.group(1)
    zal = bool(re.search(r'\b(zaliczk\w*|anzahlung|zal)\b', body, re.I))
    return _id_z_tekstu(body), _cena_z_tekstu(body), zal


def komenda_z_przycisku(dane):
    """'of|3515700088' → '/oferta 3515700088'. None, gdy to nie nasz przycisk.

    Przycisk wpisuje za właściciela tę samą komendę, którą mógłby napisać
    palcem - jedna droga w kodzie, jeden zestaw błędów do naprawienia."""
    czesci = (dane or "").split("|")
    if len(czesci) != 2 or czesci[0] != "of" or not czesci[1]:
        return None
    if not re.fullmatch(r'(?:wh-)?\d{6,12}', czesci[1], re.I):
        return None
    return f"/oferta {czesci[1]}"


def przycisk_oferty(ad_id):
    """Rząd klawiatury proszący o pełną ofertę. None, gdy nie ma o co prosić.

    Osobny guzik, a nie `copy_text`, bo ta wiadomość ma ~800 znaków, a limit
    `copy_text` w API Telegrama to 256 - tekst wjechałby uciety w połowie
    zdania o braku dogadywania, czyli straciłby dokładnie to, po co jest."""
    if not ad_id:
        return None
    return [{"text": "💬 Pełna oferta", "callback_data": f"of|{ad_id}"}]


# --- ODPOWIEDŹ NA TELEGRAMA ------------------------------------------------
def handle_oferta(ad_id, cena=None, zaliczka=False, seen=None, stan_de=None,
                  dzis=None):
    """Cała odpowiedź (HTML) na `/oferta`. Nigdy nie rzuca wyjątkiem."""
    import rozmiary          # `zyje` mieszka tam; druga kopia by się rozjechała

    czytelny = True
    if seen is None:
        seen, czytelny = _wczytaj(SEEN)
    if not czytelny:
        # Reguła 7: nieczytelny plik NIE MOŻE wyglądać jak "nie znam oferty".
        return ("⚠️ Nie mogę odczytać <code>seen.json</code> - to awaria, a nie "
                "brak ogłoszenia. Zgłoś to, bo bez tego pliku nie działa też "
                "odsiewanie powtórek.")
    if not ad_id:
        return ("Podaj numer ogłoszenia albo wklej link:\n"
                "<code>/oferta 3515700088</code>\n"
                "<code>/oferta 3515700088 2200</code> - własna kwota\n"
                "<code>/oferta 3515700088 zaliczka</code> - ze zdaniem o zaliczce")

    wpis = seen.get(str(ad_id))
    if not isinstance(wpis, dict):
        return (f"Nie mam ogłoszenia <code>{html_mod.escape(str(ad_id)[:20])}</code> "
                f"w zapisanych. Wklej link z powiadomienia albo sprawdź numer.")
    if wpis.get("score") is None:
        # Wpis bez `score` to odrzut albo nieudany odczyt - bot go nigdy nie
        # wysłał. POWÓD jest w pliku od 01.09.2026 i to jest dokładnie ten
        # moment, w którym ma się przydać: właściciel pyta o konkretny rower.
        powod = wpis.get("powod") or ("nieodczytane" if wpis.get("nieodczytane")
                                      else None)
        ogon = (f" Powód: <code>{html_mod.escape(str(powod)[:40])}</code>." if powod
                else " Wpis jest sprzed 01.09.2026, więc nie zapisał powodu.")
        return (f"Tego roweru bot nigdy nie wysłał, więc nie mam jego ceny ani "
                f"tytułu.{ogon}")

    cena_wyw = wpis.get("price_num")
    if cena is not None:
        if cena < CENA_MIN or cena > CENA_MAX:
            return (f"{cena} € wygląda na literówkę - przyjmuję kwoty od "
                    f"{CENA_MIN} do {_de_kwota(CENA_MAX)} €.")
        if cena_wyw and cena >= cena_wyw:
            # Oferta wyższa od wywoławczej daje wiadomość, która sama sobie
            # przeczy ("wiem, że wołasz 2.800, dam ci 3.000").
            return (f"Twoja kwota {_de_kwota(cena)} € nie jest niższa od "
                    f"wywoławczej {_de_kwota(cena_wyw)} €. Literówka?")
        kwota, pct = cena, ((cena_wyw - cena) / cena_wyw if cena_wyw else 0.0)
        skad = "Twoja kwota"
    else:
        kwota, pct = cena_oferty(cena_wyw, wpis.get("nego_pct"))
        skad = "policzone"
        if not kwota:
            brak = ("Sprzedawca nie podał ceny" if not cena_wyw
                    else "Przy tej cenie nie ma z czego schodzić")
            return (f"{brak}, więc nie mam od czego liczyć oferty. Podaj kwotę "
                    f"sam: <code>/oferta {ad_id} 2200</code>")

    argumenty = dict(cena_oferowana=kwota, cena_wywolawcza=cena_wyw,
                     cena_str=wpis.get("price"), nego_pct=wpis.get("nego_pct"),
                     tytul=wpis.get("title"), zaliczka=zaliczka)
    tekst = tekst_oferty(**argumenty)
    polski = tekst_po_polsku(**argumenty)
    if not tekst:
        return "Nie umiem złożyć tej oferty - zgłoś to, bo nie powinno się zdarzyć."

    if stan_de is None:
        stan_de, _ = _wczytaj(DE_STAN)
    stan = rozmiary.zyje(str(ad_id), stan_de)

    L = ["💬 <b>Oferta do wysłania</b>", ""]
    L.append(f"<b>{html_mod.escape((wpis.get('title') or 'bez tytułu')[:70])}</b>")
    if cena_wyw:
        znacznik = " VB" if ma_vb(wpis.get("price")) else ""
        if festpreis(wpis.get("nego_pct")):
            znacznik = " (Festpreis)"
        L.append(f"Woła {_de_kwota(cena_wyw)} €{znacznik} · "
                 f"proponujesz <b>{_de_kwota(kwota)} €</b> (−{pct * 100:.0f}%)")
    else:
        L.append(f"Bez ceny w ogłoszeniu · proponujesz <b>{_de_kwota(kwota)} €</b>")

    fakty = [x for x in (wpis.get("loc"),
                         wpis.get("mileage") if wpis.get("mileage_num") else None,
                         f"rocznik {wpis['year']}" if wpis.get("year") else None,
                         f"rama {wpis['rama']}" if wpis.get("rama") else None) if x]
    if fakty:
        L.append(" · ".join(str(f) for f in fakty))

    wiek = _dni_od(wpis.get("date"), dzis)
    stan_txt = {True: "dozorca potwierdził, że żyje",
                False: "⛔ dozorca widział je jako ZDJĘTE",
                None: "dozorca jeszcze nie sprawdzał"}[stan]
    L.append(f"🕐 zapisane {wpis.get('date')}"
             + (f" ({wiek} dni temu)" if wiek else "") + f" · {stan_txt}")
    if stan is False:
        L.append("<i>Tej oferty najpewniej już nie ma - wiadomość składam, ale "
                 "sprawdź link, zanim wyślesz.</i>")
    elif wiek and wiek >= STARE_PO_DNIACH:
        L.append(f"<i>Ogłoszenie stoi u nas od {wiek} dni i nikt go od tamtej "
                 f"pory nie sprawdzał - może być dawno sprzedane.</i>")

    L += ["", f"<pre>{html_mod.escape(tekst)}</pre>", ""]

    # PRZEKŁAD POZA BLOKIEM DO SKOPIOWANIA i to jest tu najważniejsze. Gdyby
    # wpadł do `<pre>`, jedno stuknięcie wysłałoby Niemcowi polski tekst.
    # Właściciel wysyła tę wiadomość pod własnym nazwiskiem, a niemieckiego
    # nie czyta - bez przekładu podsuwamy mu czarną skrzynkę.
    #
    # Bez `<blockquote>`: `send_telegram` nie ma zapasu na błąd składni HTML,
    # więc nieznany znacznik to trzy nieudane próby i wiadomość przepada
    # z samym wpisem w logu. Trzymamy się znaczników już sprawdzonych w repo.
    if polski:
        L.append("🇵🇱 <b>Co to znaczy</b> <i>(do sprawdzenia, tego NIE wysyłaj)</i>")
        L.append(f"<i>{html_mod.escape(polski)}</i>")
        L.append("")

    # REGUŁA 6: przy każdej liczbie ma stać, skąd się wzięła. Cały ten procent
    # to ZAŁOŻENIE - stałe NEGO_* w trackerze są wprost opisane jako założenie,
    # nie pomiar, bo zapisanych transakcji właściciela jest nadal zero.
    if skad == "policzone":
        L.append(f"<i>ZAŁOŻONE, nie zmierzone: te −{pct * 100:.0f}% to cały luz "
                 f"targu naraz - zdalny i ten z miejsca. Siedzi w kwocie, bo "
                 f"wiadomość obiecuje brak dogadywania pod domem. Własna kwota: "
                 f"<code>/oferta {ad_id} {kwota}</code></i>")
    if not zaliczka:
        L.append(f"<i>Zaliczka na rezerwację: <code>/oferta {ad_id} zaliczka</code></i>")
    if wpis.get("url"):
        L.append(wpis["url"])
    return "\n".join(L)


def _dni_od(data_str, dzis=None):
    try:
        y, m, d = (int(x) for x in str(data_str).split("-"))
        return ((dzis or date.today()) - date(y, m, d)).days
    except Exception:
        return None


if __name__ == "__main__":
    arg = " ".join(sys.argv[1:]) or ""
    parsed = parse_oferta_command("/oferta " + arg)
    print(handle_oferta(*parsed) if parsed else "podaj numer ogłoszenia")
