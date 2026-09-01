# Reguły pracy nad tym repo

Właściciel jest nietechniczny i nie zweryfikuje czarnej skrzynki. Narzędzie ma być
audytowalne — inaczej mu nie zaufa i słusznie. Te reguły powstały z pięciu realnych
wpadek, nie z teorii. Każda ma na końcu wpadkę, której miała zapobiec.

## Co tu żyje

Dwa **niezależne** boty w jednym katalogu:

| bot | wejście | serwisy | testy |
|---|---|---|---|
| rowerowy (DealHawk) | `tracker.py` | Kleinanzeigen + willhaben + OLX | `python test.py` |
| samochodowy | `otomoto_tracker.py` | Otomoto + OLX | `python test_otomoto.py` |

Pomocnicze: `summary.py` (dzienne podsumowanie, liczy `olx_watch.json`),
`dozorca.py` (cykl życia ofert OLX → `zdarzenia/`), `zbieraj_rynek.py`
(→ `rynek_pl.jsonl`), `olx.py` (wspólne wejście do OLX przez Cloudflare Worker),
`willhaben.py` (druga giełda ZAKUPOWA — Austria; wszystko o niej siedzi tam,
`tracker.py` dostaje gotowe ogłoszenia w swoim kształcie),
`zycie_ofert.py` (czyta `zdarzenia/` → ile rowerów zeszło, po ilu dniach
i czy zeszły, czy wygasły; komenda `/zycie` na Telegramie),
`zdrowie_danych.py` (codzienna czujka na ciche awarie w rurze danych - pyta
tylko o to, czy pole NIGDY nie zadziałało i czy PRZESTAŁO działać, punktem
odniesienia jest własna historia pliku, nie próg wzięty z głowy; chodzi
z zadania cyklicznego o 8:10 i MILCZY, gdy jest zdrowo),
`dojrzale.py` (czyta dziennik i wypisuje oferty, których sprzedawcy schodzą
z ceną; nic nie pobiera i nic nie zapisuje),
`odblokuj.py` (jednorazowe narzędzie z 01.09.2026: zdejmuje z `seen.json` wpisy
rowerów zdławionych STARĄ regułą re-listingu; domyślnie chodzi na sucho),
`odzyskaj_silnik.py` (to samo dla rowerów zdławionych filtrem silnika, zanim
poznał rodziny modeli; też na sucho, też z `--od`),
`sprawdz_silniki.py` (przelicza `silniki_bosch.json` na aktualnych danych;
kod wyjścia 1, gdy wpis przestał się bronić, `--nowe` podpowiada kandydatów).

**Podział ról, którego nie mieszać:** `dozorca.py` zapisuje FAKTY do dziennika
i nigdy wniosków. `zycie_ofert.py` jest jedynym miejscem, gdzie z faktów robi
się wnioski. Dzięki temu zmiana reguły to przeliczenie, nie zbieranie od nowa.

Oba zestawy testów muszą przechodzić przed każdą zmianą. Uruchomienie lokalnie
wymaga atrapy tokenu:

```bash
TELEGRAM_BOT_TOKEN=dummy TELEGRAM_CHAT_ID=0 python test.py
```

## Twarde ograniczenia produktowe — nie negocjuj ich

- Silniki: **tylko Bosch** (plus własny silnik Specialized). Canyon wolno, ale filtr
  silnika nadal musi odrzucać Shimano EP8.
- `history.jsonl` **NIGDY** nie jest kasowany ani przycinany. Rośnie w nieskończoność.
- Bot **NIE** wystawia sam ogłoszeń i **NIE** negocjuje sam. Bany i klimat naciągacza.

## Osiem reguł

### 1. Zmieniasz kod liczący plik → przelicz plik w tym samym zadaniu
Stan mieszka w plikach (`cennik_cech.json`, `olx_watch.json`, `seen*.json`), nie tylko
w kodzie. Naprawa generatora bez przeliczenia wyniku to naprawa pozorna.
> Naprawa `odduplikuj()` z 23.08 nigdy nie trafiła do `cennik_cech.json`. Przez dwa dni
> wycena stała na współczynnikach liczonych na 1416 wierszach zamiast 413 rowerach.
> Poziom wyposażenia był zawyżony ponad dwukrotnie.

### 2. Każdy nowy test musi PAŚĆ na starym, zepsutym kodzie — sprawdź to
Test, który przechodzi na obu wersjach, jest pieczątką, nie strażnikiem. Wyciągnij
poprzednią wersję (`git show HEAD:plik.py`) i uruchom na niej nowy test.
> Stary test `build_price_reco` przechodził przez cały czas trwania błędu, bo jego
> atrapy ofert nie miały żadnych cech — obie porównywane liczby wypadały wtedy obok
> siebie i wszystko wyglądało zdrowo.

### 3. Testuj WŁASNOŚĆ, nie implementację
Ten sam błąd wracał trzykrotnie, za każdym razem inną ścieżką w kodzie. Łatanie
ścieżki nie pomaga. Zapisz regułę, która musi zachodzić zawsze — np. „rower bezspornie
gorszy nigdy nie dostaje ceny wyższej niż bezspornie lepszy" — i sprawdzaj ją we
wszystkich trybach naraz. Wtedy piąta ścieżka też wpadnie.
> Wzorzec: `market` liczony poprawnie, po czym nadpisywany medianą całej rodziny
> modelu. Jedna cena dla roweru z 2018 i z 2025. 90 błędnych par na 108.

### 4. POZIOM to nie PROPORCJA
Liczby rodzinowe (mediana modelu, cena domykająca, `demand_median`) wolno wstawiać
**wyłącznie jako mnożnik**, nigdy jako kwotę. Kwota kasuje wycenę po cechach.
Wzorzec do naśladowania: `hair` przy `get_demand_price` po stronie zakupu.

### 5. Licz rowery, nie ogłoszenia
Zawsze przez `odduplikuj()`. Przy każdym nowym współczynniku podaj, na ilu
**niezależnych** rowerach stoi.
> W wąskich wycinkach (model + bateria) powtórki sięgały 87% — akurat tam, gdzie
> dane są potrzebne. Reguła „500 Wh → rocznik 2025" wzięła się z jednego sklepu.

### 6. Każda liczba w wiadomości ma etykietę: ZMIERZONE czy ZAŁOŻONE
Zakaz mnożenia założeń bez oznaczenia. Wycena 9 436 zł składała się ze zmierzonych
9 986 zł przemnożonych przez 0,945 czystej hipotezy (zakładany zjazd 10% przy
zmierzonym 0,0% i zakładany targ 10% z poradników) — i była podawana co do złotówki.
Nie ma pomiaru → **widełki i słowo „nie wiem"**, nigdy punkt.
Nie podawaj efektu krańcowego przy medianie jako wartości cechy: „przebieg dopłaca 2%"
było liczone wobec 1 427 km, a wobec roweru z 5 000 km to ~15%.

### 7. Parsujesz cudzy HTML → dołóż czujkę na ciszę
Zero zdjęć albo zero cen na **wszystkich** stronach w skanie to nie pech, tylko
przebudowa serwisu. Cicha awaria jest gorsza od głośnej.
> Parser OLX czytał 20 z 52 kafelków (38% rynku, z przechyłem na droższe oferty
> promowane) i nikt tego nie zgłaszał, bo wyniki nadal wyglądały wiarygodnie.

### 8. Ze strony oferty OLX bierz JSON, nie regexpy
Całe ogłoszenie siedzi w `__PRERENDERED_STATE__` (JSON zakodowany w stringu
JSON-a): `createdTime`, `validToTime`, `lastRefreshTime`, `status`, `isBusiness`,
`user.id`, `location`, `photos`, `params`. Czyta to `olx.parse_olx_ad_json`,
mapuje `tracker._fakty_z_ad_json`. Regexp po HTML-u dubluje ułamek tego i myli
się na boilerplate — fraza „NIEAKTUALNE" i słowo `expired` siedzą w pakiecie
tłumaczeń KAŻDEJ strony OLX.
> Regexp na sprzedawcę w `zbieraj_rynek.py` nie trafił ANI RAZU: pole
> `sprzedawca` miało 0 z 1427 wierszy. Cicho wyłączyło to naraz odsiewanie
> spamu sklepów i deduplikację (reguła 5).

**Fakty zbiera się, DOPÓKI oferta żyje.** Martwa strona to samo
`{"statusCode": 410}` — ani powodu zdjęcia, ani daty wystawienia, ani
sprzedawcy (zmierzone 24.08.2026 na żywej i martwej ofercie). Kto tego nie
złapie za życia, ten po zniknięciu wie tylko tyle, że zniknęło.

## Dwie giełdy zakupowe — czego nie mieszać

Od 25.08.2026 bot czyta Kleinanzeigen (Niemcy) i willhaben (Austria). Ta sama
waluta, te same filtry, ten sam `seen.json`, ta sama wycena. Cztery rzeczy
muszą jednak zostać rozdzielone i każda ma za sobą konkretny powód:

1. **Identyfikatory.** Obie giełdy numerują ogłoszenia 9-cyfrowymi liczbami,
   a wpadają do jednego `seen.json`. Austria dostaje prefiks `wh-`. Bez niego
   kolizja numerów uciszyłaby rower na zawsze — bot uznałby go za widzianego.
2. **Tempo i dławienie.** `padly` (czyta je `tempo_po_skanie`) liczy WYŁĄCZNIE
   półki Kleinanzeigen. Tempo adaptacyjne to odpowiedź na jedno zmierzone
   zjawisko — dławienie per adres IP na Kleinanzeigen. Willhaben zniósł
   8 żądań pod rząd bez śladu kary, więc jego awaria nie mówi nic o tym,
   czy wolno przyspieszyć tam.
3. **Zdrowie parsera.** Liczone OSOBNO na serwis. Wspólna średnia maskuje:
   przy typowych proporcjach (setki ogłoszeń z willhaben, dziesiątki z półek
   Kleinanzeigen) całkowita śmierć tego drugiego parsera zeszłaby poniżej
   progu razem z tym pierwszym i nic by nie krzyknęło.
4. **Trend cen.** `price_trend` jest podpisany „rynek DE", więc liczy tylko
   wiersze niemieckie (pole `zr` w `history.jsonl`: brak = Kleinanzeigen,
   `wh` = Austria). `build_price_history` jest ŚWIADOMIE wspólny — odpowiada
   na pytanie „czy widziałem ten model taniej", a bot kupuje w obu krajach.

**Nowa giełda to jeden wpis w `POLKI` i jeden moduł**, nie rozgałęzienie
w pętli. Moduł oddaje ogłoszenia w kształcie `fetch_listings` i nie wie nic
o rowerach — dokładnie jak `olx.py`.

Trzy pułapki willhaben, drogo kupione 25.08.2026, nie sprawdzać od nowa:

- **Pole `PUBLISHED_String` kończy się na `Z`, a niesie czas WIEDEŃSKI.**
  Udowodnione dwiema drogami: liczbowe `PUBLISHED` (epoch ms) daje tę samą
  godzinę dopiero po przeliczeniu na Wiedeń, a to samo ogłoszenie na własnej
  stronie ma `publishedDate` z jawnym `+0200`. Wzięte za UTC przesuwa każde
  ogłoszenie 2 h w przód: wiek wychodzi ujemny, alarm o spóźnieniu nie odpala
  się nigdy, a znacznik półki staje w przyszłości i luka nie domyka się już
  nigdy. **Pierwszeństwo ma pole liczbowe.**
- **`BODY_DYN` z listy jest ucinany na 256 znakach** (125 z 200 ogłoszeń stało
  dokładnie na limicie). Wygląda jak pełny opis. Wzięty za pełny daje ciche
  „sprzedawca nie podał przebiegu" na rowerze, który ma przebieg w zdaniu
  drugim. Opis do decyzji bierzemy WYŁĄCZNIE ze strony ogłoszenia.
- **Parametr `rows` w adresie jest wart więcej niż cokolwiek innego:** 200
  ogłoszeń = 4,8 h rynku JEDNYM żądaniem. Trzygodzinna przerwa domyka się
  jednym pobraniem — nie ma tu odpowiednika chodzenia po dwunastu stronach.

Czego o willhaben NIE wiemy: czy Telegram przyjmie ich zdjęcia (CDN oddaje
`image/webp` niezależnie od nagłówka `Accept`; gdyby odmówił, powiadomienie
i tak dojdzie — bez zdjęcia, bo `send_telegram_photo` ma zapas tekstowy).
`TRANSPORT_PLN = 300` jest ustawione pod Niemcy i pod Austrię **nie było
weryfikowane** — Wiedeń jest bliżej niż Nadrenia, Vorarlberg znacznie dalej.

## Pułapki Kleinanzeigen, zmierzone 29.08.2026 - nie odkrywać drugi raz

**Skasowane ogłoszenie NIE przekierowuje i renderuje się w całości.** Ma tytuł,
cenę, opis, zdjęcia i element `viewad-price`. Jedyne, co je zdradza
w przeglądarce, to plakietka `Gelöscht` przy tytule. Poprzednia reguła
"jest cena, czyli żyje" myliła się na **15 z 24 ogłoszeń o ustalonym stanie**,
czyli 62% listy dojrzałych ofert było trupami.

To NIE jest ten sam stan co wygaszenie przez serwis, które przekierowuje na
`/s-fahrraeder/<miasto>/c217l<id>` (zmierzone 25.08.2026). Stany są dwa
i sprawdzać trzeba oba: przekierowanie ORAZ przycisk kontaktu.

**Bot dostaje INNĄ STRONĘ niż przeglądarka.** Ta sama oferta, ta sama minuta:
2 779 kB w przeglądarce, 245 kB dla `tracker.scraper`. W wersji dla bota słowa
`Gelöscht` NIE MA W OGÓLE, w żadnej postaci ani kodowaniu. Szukanie fraz
tekstowych jest tu ślepe z definicji i curl niczego nie rozstrzygnie.
Rozstrzyga tylko przeglądarka z prawdziwą sesją.

**Co bot widzi zamiast tego:** na martwej ofercie przyciski "napisz wiadomość"
i "obserwuj" są WYŁĄCZONE (`icon-mail-disabled`, brak
`id="viewad-contact-button-login"`). Do skasowanego ogłoszenia nie da się
napisać i to jedyna różnica dostępna botowi. Zmierzone na 24 ogłoszeniach
(9 żywych, 15 skasowanych): rozdziela bezbłędnie. Czyta to
`dozorca_de.ocen_strone`, sprawdzając OBA znaczniki naraz - przy zmianie nazwy
klasy ma wyjść "nieznane", a nie cicha masowa wyprzedaż.

**Rozmiar strony nie rozstrzyga.** Żywe 203-295 kB, skasowane 205-265 kB,
zakresy się nakładają. Zapis w starym docstringu dozorcy ("żywe 224-228 kB,
zdjęte 289-306 kB") pochodził z pięciu obserwacji i nie generalizuje.

**Dławienie objawia się okrojoną stroną, nie błędem HTTP.** Po około stu
zapytaniach z jednego adresu strony zaczynają wracać po ~105 kB, ze statusem
200 i bez żadnych znaczników kontaktu. `ocen_strone` daje wtedy "nieznane"
i tak ma być. Do sprawdzenia przy okazji: czy zdławiona strona zachowuje
element ceny - jeśli tak, to stary kod zapisywał "zyje" także przy dławieniu
i źródeł fałszywego życia były dwa, nie jedno.

**GRANICA WERSJI W DZIENNIKU DE.** Wszystko w `zdarzenia_de/` sprzed
29.08.2026 powstało przy zepsutym wykrywaniu, więc tamte wpisy `zyje` NIE
odróżniają żywego od skasowanego przez sprzedawcę. Dziennika się nie kasuje,
więc to warstwa licząca krzywe przeżycia musi te rekordy pomijać. Od 29.08
zdarzenie `znikla` niesie też `p` i `p0`, bo martwa strona ceny już nie poda
i albo zapisujesz ją w tej sekundzie, albo nigdy.

## Dedup re-listingu, przepisany 01.09.2026 - nie cofać bez nowych pomiarów

Powód: rower 3492497177 (Cube Stereo Hybrid 160 HPC **SLX** 750, 1350 km,
2 550 EUR) został 26.08.2026 zdławiony jako powtórka 3435648674 (HPC **SL**
750, 1100 km, 2 500 EUR, rocznik 2022). Dwa różne rowery. Różnica przebiegu
250 km mieściła się w tolerancji 300, różnica ceny 2,0% w tolerancji 3%.
Ogłoszenie żyło jeszcze 01.09 i zdążyło stanieć do 2 400 EUR.

**Przebieg PRZESTAŁ być dowodem tożsamości.** Zmierzone wierną powtórką
60 dni rynku (43 466 ogłoszeń wobec indeksu 1 459 ocenionych ofert):

| dowód | ile razy zdławił | ile z tego to INNY rower |
|---|---|---|
| tytuł identyczny co do znaku | 62 | 0 |
| ta sama miejscowość | 6 | 0 |
| sam zgodny przebieg | 90 | **co najmniej 60 (67%)** |

Przebieg dokładał więc same pomyłki. Te same 300 km (`DEDUP_KM_TOL`) mają dziś
odwrócone znaczenie: rower przy wznowieniu nie traci kilometrów, więc
rozjechany przebieg PRZECZY tożsamości, zamiast ją potwierdzać.

**Dowód RÓŻNICY bije dowód tożsamości** (`sprzeczne_warianty`), tak samo jak
`_MOTOR_DO_WYMIANY` bije `_MOTOR_WYMIENIONY`. Sprzeczne są: inna wersja
(SLX vs SL, Race vs Pro), inna bateria, inny rozmiar ramy. Dwa warunki, których
nie wolno poluzować:

- **Milczenie nie jest sprzecznością, a podzbiór to milczenie.** "HPC" i
  "HPC Pro" to ten sam rower opisany krócej. Sprzeczność jest dopiero wtedy,
  gdy KAŻDA strona mówi coś, czemu druga przeczy. Bez tego weto strzelało
  w tytuły skrócone: 4 997 par zamiast 4 254.
- **Rozmiar ramy: litera do litery, centymetry do centymetrów.** "L" kontra
  "L / 62 cm" to ta sama rama opisana dokładniej.

Weto jest w porę: na 8 594 parach sklejonych miejscowością sprzeczne są
4 254 (50%). Dowód z miejscowości fałszował dotąd rzadko TYLKO dlatego, że
pole `loc` jest młode - im więcej wpisów je ma, tym szerzej ta dziura się
otwiera.

**Każdy odrzut zapisuje POWÓD.** Do 01.09.2026 tylko odrzut cenowy zostawiał
ślad (`cena_odrzut`); reszta zapisywała gołe `{"date": ...}`. Zmierzone tego
dnia: **29 147 z 79 479 wpisów** w `seen.json` to takie nieme wpisy. Kiedy
właściciel zapytał, czemu nie dostał powiadomienia o konkretnym rowerze,
odpowiedzi NIE DAŁO SIĘ odczytać z pliku - trzeba ją było odtwarzać symulacją.
Zapisuje to `odrzuc()`, pole `powod`.

**GRANICA WERSJI: wpisy w `seen.json` bez pola `powod` pochodzą sprzed
01.09.2026** i nie mówią, dlaczego bot zamilkł. Nie zgaduj z nich.

**Odrzut przestał być dożywotni.** `POWODY_PO_CENIE` wymienia powody, które
cofa spadek ceny: `cena`, `nisza`, `bateria`, `relisting`. Ten ostatni jest
tam, bo dedup bywa w błędzie, a pomyłka nie ma prawa być wieczna - 3492497177
stanial po zdlawieniu o 5,9% (powyżej progu powiadomienia) i bot nie pisnął,
bo ścieżka obniżki wymaga wpisu ze `score`, którego odrzucony nie ma.
Powody NIE-cenowe wracają wyłącznie przy REALNYM spadku, inaczej dostałyby
w podpisie "PRZECENIONE" albo "NOWE WIDEŁKI" i żadne nie byłoby prawdą.

**Rocznik należy do roweru, nie do wymienionej części** (`_ROK_CUDZY`).
"Neuer Akku 02/2026" to rok baterii - wzięty za rocznik dał temu rowerowi
2026 zamiast 2023, czyli wycenę 17 162 zł zamiast 13 928 zł i zysk zawyżony
z ~1 840 do ~4 750 zł. Rocznik dopłaca 7,2% na rok, więc trzy lata pomyłki to
ćwierć ceny roweru. Weto jest CIASNE z rozmysłu: samo sąsiedztwo słowa "Akku"
nie wystarcza, bo w "625 Akku - 2022" rok najpewniej JEST rocznikiem.
Zmierzone na 39 550 tytułach: zmienia wynik w 9 (0,02%) i wszystkie 9 słusznie.

**Pojemność do WYCENY czyta `bateria_z_nazwy`, nie `battery_wh`.** W nazwach
modeli siedzi goła liczba ("HPC SLX 750 Carbon"), bez "Wh" - cecha o
największej wadze w cenniku (20,3% na 100 Wh) po cichu wypadała z wyceny
w 2 556 z 39 550 tytułów (6,5%). `is_small_battery` zostaje przy czytniku
ścisłym z rozmysłu: tam brak odczytu znaczy "przepuść", więc luźniejszy
czytnik dokładałby ODRZUTY, a nie wiedzę.

## Półka milczy na DWA sposoby - liczył się tylko jeden (naprawione 01.09.2026)

Zmierzone tego dnia: obie niemieckie półki zamilkły o **11:15** (ostatnie
ogłoszenie złapane minutę po wystawieniu, potem nic przez 5,5 godziny).
Bot chodził dalej, wszystkie biegi w Actions zielone, `check_feed_health`
poprawnie ustawił `feed_martwe: ["Kleinanzeigen"]` i wysłał alarm.

A mimo to tryb awaryjny nie włączył się ANI RAZU:

```
kanal_zle: 0        <- po 5,5 h martwego kanału
padly: 0
tempo_s: 300
```

Powód: `padly_ka` rósł wyłącznie na `stats["zepsuty"]`, czyli na PODSTAWIONEJ
liście, a `strona_zepsuta` wymaga `blocks >= 5`. Strona bez ANI JEDNEGO
kafelka daje `blocks == 0`, więc nie zapalała niczego - dla licznika
wyglądała jak spokojny rynek. `kanal_zle` stał na zerze, więc próg
`KANAL_CIERPLIWOSC = 12` (godzina) nie został przekroczony nigdy, więc
`ile_kluczowych` zwracało `KLUCZOWE_NA_SKAN = 1` zamiast
`KLUCZOWE_AWARYJNE = 8`. Bot oszczędzał ruch, licząc na odblokowanie kanału,
którego nie miał już jak odblokować.

Cena: półka daje ~4 000 ogłoszeń dziennie, tego dnia 767. Ocenione oferty
stanęły - 25 o 14:00, 26 o 16:37.

**Alarm działał, kompensacja nie.** To jest osobna klasa wpadki niż reguła 7:
tam chodzi o to, żeby cichą awarię ZAUWAŻYĆ, a tu awaria była zauważona
i zgłoszona, tylko układ, który miał na nią zareagować, czytał inny licznik.
Przy każdym czujniku sprawdzaj OBIE rzeczy osobno: czy krzyczy i czy to,
co ma po jego krzyku zadziałać, faktycznie dostaje sygnał.

Czyta to `kanal_niemy(stats)`: niema półka to `zepsuty` **albo** zero
kafelków. Pusta półka kategorii nie jest stanem naturalnym - e-bike i MTB
mają na Kleinanzeigen tysiące ogłoszeń dziennie. Liczymy `blocks`, nie
długość wyniku: półka może zgodnie z prawdą nie mieć NOWYCH ogłoszeń, ale
zawsze ma jakieś.

Decyzje o tempie wyjęte z pętli do funkcji czystych (`licz_kanal_zle`,
`ile_kluczowych`) właśnie po to, żeby dało się na nie napisać test. Bez tego
nikt przez pół dnia nie zauważył, że próg nie zostaje przekroczony nigdy.

## Czarna skrzynka na niemą półkę (01.09.2026)

Czujka dryfu parsera (`check_parser_health`) zapisuje HTML do `blackbox/`,
gdy spada odsetek odczytanych tytułów albo cen. **Na tę awarię się nie
zapala.** Podstawiona lista ma tytuły i ceny w 100% - brakuje wyłącznie DAT -
więc parser wychodzi zdrowy. Zmierzone na starym kodzie: przy 32 kafelkach
bez dat `serwisy.Kleinanzeigen` to `{"ok": true, "title_rate": 1.0,
"price_rate": 1.0}` i `blackbox/` zostaje pusty. Przy odpowiedzi bez ani
jednego kafelka jest jeszcze gorzej - `blocks` nie dobija do
`PARSE_HEALTH_MIN_BLOCKS` i sprawdzanie kończy się na `continue`.

Skutek: 01.09 półki milczały sześć godzin, alarm poszedł, a jedynym plikiem
w `blackbox/` był zapis z 09.07. Nie dało się orzec, czy to blokada zakresu
IP, dławienie, czy przebudowa serwisu - a od tej odpowiedzi zależy, czy się
czeka, czy przepina ruch przez przekaźnik Cloudflare. **Logi biegów GitHuba
są zamknięte (HTTP 403 nawet przy publicznym repo), więc jedynym świadkiem
tego, co dostaje runner, jest sam runner.**

**Sam zapis to za mało — trzeba jeszcze mieć CO zapisać.** Pierwsza wersja
tej naprawy była martwa: `zapisz_czarna_skrzynke` było wdrożone i wołane, a nie
powstał ani jeden plik, bo dostawało `html=None`. `fetch_listings` zachowywało
HTML WYŁĄCZNIE przy złym odsetku tytułów i cen, a podstawiona lista ma je
w 100%. Czujka działała, brakowało próbki. Dziś HTML zostaje przy TRZECH
warunkach: zły odsetek (dryf parsera), kafelki bez ani jednej daty
(podstawiona lista), zero kafelków (pusta odpowiedź). Zdrowa strona nie
zostawia nic — to ma być dowód, nie archiwum.

Robi to `zapisz_czarna_skrzynke`, wołane w gałęzi `kanal_niemy`. Trzy warunki,
których nie ruszać:

- **Nazwa niesie ODCISK TREŚCI, nie sam dzień** (`BLACKBOX_PROBEK` próbek na
  dobę i półkę). Pierwsza wersja zapisywała jeden plik na dobę i sama się
  zablokowała: 01.09.2026 półka trafiała 5 skanów na 8, a próbki tych trzech
  pudeł nie dało się już zdobyć, bo plik z tego dnia istniał - z awarii sprzed
  naprawy wzorca daty. Diagnoza stanęła na pytaniu „czy zła strona nie ma dat,
  czy ma je inaczej" i nie było czym odpowiedzieć. Odcisk domyka oba końce:
  ta sama odpowiedź nie robi commita co 5 minut, a INNA dostaje własny plik.
- **HTML zapisywany NIETKNIĘTY.** Metryki (status, `blocks`, `time_hits`, kB)
  idą do pliku `.json` obok. Dopisane do HTML-a zmieniałyby dowód.
- **`blackbox` musi zostać na liście `git add` w `tracker.yml`**, inaczej dowód
  ginie razem z runnerem.

## Przebudowa listy Kleinanzeigen, 01.09.2026 godz. 11:15

**Serwis wymienił warstwę HTML.** Klasy semantyczne (`aditem-main--top--right`)
zniknęły ze strony do zera i zastąpiły je klasy narzędziowe w stylu Tailwinda,
generowane - więc nie nadające się na kotwicę. Zmierzone na odpowiedzi, którą
dostał runner (`blackbox/niema-kanał_e_bike-2026-09-01.html`, HTTP 200,
638 kB, 27 kafelków): stary wzorzec daty trafiał **0 z 27**.

**Tytuł i cena przeżyły, data padła** - i to nie przypadek. Ich wzorce stoją
na `href="/s-anzeige/..."` i na kształcie kwoty, czyli na STRUKTURZE. Data
stała na nazwie klasy, czyli na dekoracji. Wniosek na przyszłość: kotwicz się
na tym, co serwis musi mieć, żeby działać, a nie na tym, co jego projektant
może przemalować w każdy poniedziałek.

**Objaw był mylący i kosztował pół dnia złej hipotezy.** Strona z kafelkami
i bez ani jednej daty to z definicji `strona_zepsuta`, czyli „podstawiona
lista" - a ta w tym repo od 22.08 znaczy DŁAWIENIE. Bot krzyczał więc
„dławienie" przy przebudowie serwisu. Tego samego dnia właściciel dostał
z Kleinanzeigen prawdziwe okno o blokadzie ZAKRESU IP, co idealnie pasowało
do złej hipotezy. Rozstrzygnęły dopiero dwa pomiary:

1. **Zapytania kluczowe też straciły daty, w tej samej minucie.** Ostatnie
   ogłoszenie z datą: 11:15. Po nim 116 wierszy DE bez ani jednej daty,
   z półek I z zapytań naraz. Dławienie jednej półki tak nie wygląda.
2. **Ten sam runner, ta sama sekunda.** 15:36:39 półka oddaje śmieć,
   15:36:42 zapytanie kluczowe oddaje prawdziwe ogłoszenia. Blokada adresu
   IP zabiłaby jedno i drugie. W całym logu ani jednego 403 czy 429.

**Nowa kotwica to KSZTAŁT TREŚCI, nie klasa:** goły `<span>` z samą datą,
stojący zaraz za ikoną. Trafia 25 z 27 kafelków, a dwa pudła są POPRAWNE:
reklama „Direkt kaufen" nie ma daty w ogóle, a sklep BESV ma `31.08.2026`
w TREŚCI ogłoszenia („NUR BIS ZUM 31.08.2026"). Wzorzec żąda `<span>`
z SAMĄ datą i dlatego odrzuca ją sam z siebie - to ta sama pułapka co
„NIEAKTUALNE" w boilerplate OLX (reguła 8), tylko po niemieckiej stronie.

**UKŁADY SĄ TRZY, nie dwa.** Naprawa na dwóch dawała 5 trafień na 8 skanów.
Trzeci złapała czarna skrzynka o 21:06: lżejsza odpowiedź, **183 kB zamiast
638 kB**, 32 kafelki, ani starej klasy, ani gołego `<span>` - za to 30 razy
„Heute" w `adlist--item--info--date`. To ta sama rodzina co
`adlist--item--price` w `PRICE_PATTERNS`, więc CENA z tej strony czytała się
od dawna, a data nie miała czym. Serwis oddaje te układy losowo, per żądanie:
obie półki padają razem na jednym ogniwie, a pięć minut później inne ogniwo
czyta bez problemu.

**`(?s)` MUSI STAĆ W SAMYM WZORCU, nie w `re.compile`.** `_match_pool` woła
`re.search(p, block)` bez flag, więc przeniesienie wzorca do puli po cichu
zabiera mu DOTALL. Kosztowało to regresję tego samego dnia: stary układ
wieloliniowy przestał się czytać, choć wcześniej działał. Dwa z trzech
wzorców mają datę w OSOBNEJ LINII wewnątrz diva, więc bez DOTALL są martwe.
Test na sztucznym kafelku w jednej linii tego nie wyłapie - musi być
wieloliniowy.

`AD_TIME_PATTERNS` jest PULĄ, czytaną przez `_match_pool` jak tytuły i ceny.
Stary wzorzec zostaje pierwszy: nic nie kosztuje, a serwis potrafi oddawać
kilka układów naraz (zmierzone 23.08 na galerii zdjęć).

## Filtr silnika mierzył pilność sprzedawcy, nie rower (01-02.09.2026)

Właściciel przysłał link i pytanie "czemu to nie przyszło": **Cube Stereo
Hybrid 160 HPC SLX 750** (3498596629, 2 980 EUR, wystawiony 30.08 o 11:49).
Bot zobaczył go **po 7 minutach**, zapisał do `market.jsonl` i zamilkł.
Przyczyna: `has_known_motor` szukał gołego napisu z `MOTOR_BRANDS`, a w tym
ogłoszeniu słowa "Bosch" nie ma ANI W TYTULE, ANI W CAŁYM OPISIE - sprzedawca
wypisał kolor, rozmiar ramy, opony, karbon i 750 Wh. Rower jest Boschem.

**To nie był pojedynczy pech.** Zmierzone tego dnia: na 14 odrzutów
`obcy_silnik` **10 należało do rodzin, które mają Boscha z definicji**. Filtr
stojący na tym, co sprzedawca RACZYŁ napisać, mierzy jego staranność, a nie
rower - a to ostatnia bramka przed oceną, więc odsiewał gotowe oferty
w widełkach.

Druga droga do tej samej wiedzy: `silniki_bosch.json` + `silnik_z_rodziny`.
**26 par marka+model, 1 783 potwierdzenia, zero rywali**, zmierzone 02.09.2026
na 51 841 unikalnych tytułach z Kleinanzeigen i willhaben oraz 677 adresach
OLX z `rynek_pl.jsonl` (tytuł siedzi tam w adresie). Próg wejścia: rywal nie
padł ANI RAZU, a "Bosch" co najmniej 10 razy.

**Lista siedzi w pliku, nie w kodzie, i to jest istota poprawki.** Wiedza
o sprzęcie starzeje się z rocznikami, a właściciel ma ją czytać i poprawiać
sam. `sprawdz_silniki.py` przelicza ją na aktualnych danych, kończy się kodem
1, gdy któryś wpis przestał się bronić, i z `--nowe` podpowiada rodziny,
które próg już spełniają.

**Trzeba MIERZYĆ, nie wpisywać z pamięci.** Trzy rodziny, które wpisałbym
z głowy jako Boschowe, mają w danych prawdziwe ogłoszenia z konkurencją:
Scott Strike eRide 920 i Genius eRide 920 wyszły z Shimano STEPS, a Bulls
Sonic Evo AM SL to cała linia na EP8. Pomiar odrzucił też Haibike SDURO
(24 Boschy wobec 62 Yamah), Focus Jam² (41 wobec 20), Orbea Rise (0 wobec 8),
Canyon Neuron:ON (2 wobec 5) i Raymona (0 wobec 23). Wszystkie te odrzuty
razem z liczbami siedzą w `silniki_bosch.json`, żeby nikt ich nie dopisał
drugi raz z głowy.

**Marka I model naraz, nigdy sam model.** "Patron", "Image", "Sinus" i "Wild"
to po niemiecku zwykłe słowa, a "e-power" pada w opisach jako zwrot
reklamowy. Wpis wymaga obu członów.

**Nazwany wprost rywal bije domniemanie z rodziny** (`_SILNIK_RYWAL`) - ta
sama zasada co `_MOTOR_DO_WYMIANY` nad `_MOTOR_WYMIENIONY`. Weto NIE dotyczy
trafienia w `MOTOR_BRANDS`: tam nie zgadujemy, tylko czytamy. Twarde
ograniczenie "tylko Bosch" stoi dalej i pilnują go testy.

Dwie pułapki pomiarowe, obie kosztowały jeden zły pomiar tego dnia:

- **Wzorce rodzin nie mają `re.IGNORECASE`,** bo `has_known_motor` podaje im
  tekst już zamieniony na małe litery. Puszczone po surowym tytule przegapiają
  "Yamaha" z dużej litery. Pierwszy przemiał całego korpusu wyszedł przez to
  tak, jakby Haibike SDURO był w 100% Boschem.
- **Koniec frazy to `(?![a-z0-9])`, a nie `\b`.** Producenci piszą "Thron²",
  "Jam²", "Jarifa²", a dla Pythona "²" jest znakiem słowa, więc `\bthron\b`
  NIE trafia w "thron²". Ta jedna granica gubiła 72 ogłoszenia Focusa.

**Brak pliku to awaria, nie stan naturalny** (reguła 7). `load_silniki` zgłasza
wtedy problem, a filtr wraca do samego `MOTOR_BRANDS`, czyli do zachowania
sprzed poprawki - bot działa dalej i znowu odsiewa rowery bez marki w tekście.
Nie przepuszcza wszystkiego. Pilnują tego trzy testy.

## Odblokowanie ≠ powrót. Półka już przeszła (zmierzone 01.09.2026)

To jest druga, OSOBNA dziura, ta sama co przy `odblokuj.py`. Zdjęcie wpisu
z `seen.json` mówi tylko tyle, że bot policzy ogłoszenie od nowa, **gdy je
jeszcze raz zobaczy**. A nie zobaczy: pierwsza strona zapytania „cube stereo
hybrid" (32 kafelki) sięgała tego dnia **8 godzin wstecz** - 23:52 do 15:09.
Ogłoszenie sprzed dwóch dni nie wróci samo.

Skutek dla roweru z tej wpadki: `odblokuj.py` zdjął go z `seen.json` już
01.09 o 18:36 jako domniemaną ofiarę starej reguły re-listingu, a mimo to
powiadomienie nie przyszło i nie przyjdzie. Odblokowanie ogłoszenia starszego
niż kilka godzin jest zapasem na wypadek, gdyby sprzedawca odświeżył ofertę,
a nie naprawą. **Przy każdym takim narzędziu podawaj `--od`** i nie licz
odblokowanych wpisów jako odzyskanych rowerów.

Robi to `odzyskaj_silnik.py` (reguła 1: naprawa `has_known_motor` nie wskrzesza
sama z siebie ani jednego wpisu w `seen.json`, bo wpis jest terminalny).

## Czego rzeczoznawca dziś NIE umie — nie udawaj, że umie

- Przewiduje cenę **wywoławczą** na OLX, nie kwotę, którą dostaniesz.
- Waga przebiegu jest **nieustalona**: -3,5% albo -7,0% na 1000 km zależnie od
  kolejności cech w `CECHY`, bo regresja liczy je po kolei na resztach i pierwsza
  zjada wariancję wspólną. Wspólna regresja wymagałaby rowerów z kompletem czterech
  cech — jest ich **23 z 413**. To ograniczenie danych, nie kod do poprawienia.
- Zapisanych transakcji właściciela: **0**. Dozorca zbiera od 21.08.2026.
- Dziennika `zdarzenia/` **nikt jeszcze nie czyta** — rośnie, ale żadna wycena
  z niego nie korzysta. `dozorca.powod_zniknienia` jest gotowe i przetestowane,
  brakuje warstwy, która policzy z tego krzywe przeżycia.
- Skan co ~2 h (zmierzone: mediana 1,9 h na 35 przebiegach). Co sprzedało się
  w godzinę, jest dla bota niewidzialne — a schodzą tak oferty wystawione
  najtaniej. Próbka „sprzedanych" jest więc przechylona w stronę droższych.
- **Cennik cech stoi w 36% na ofertach SKLEPÓW** (146 firm na 392 rozpoznane
  rowery, zmierzone 24.08.2026 przez sklejenie `rynek_pl.jsonl` z faktami
  dozorcy). Zmierzony skutek: współczynniki cech prawie się nie ruszają
  (największa zmiana to poziom wyposażenia 4,3% → 2,1%, a ten i tak jest
  niestabilny), ale POZIOM cen owszem — sklep woła +4% przy 625 Wh i **+11%
  przy 750 Wh**. Odsianie sklepów zabiera 45% danych (413 → 227 rowerów),
  więc to wymiana jednego błędu na drugi. **Nie ruszane świadomie** — patrz
  reguła nadrzędna niżej. Do rozstrzygnięcia, gdy będą realne sprzedaże.
- Odcisk wznowienia po ZDJĘCIACH jest **niesprawdzony**: na 448 ofertach nie
  było ani jednej pary wspólnych plików, co sugeruje, że OLX nadaje nowy
  identyfikator przy każdym wgraniu. Realnie łapie droga druga (ten sam
  sprzedawca + ten sam tytuł), na razie 2 scalenia i oba to sklepy
  dublujące własne ogłoszenie.

**Wynika z tego reguła nadrzędna: nie stroj rzeczoznawcy, dopóki nie ma danych o
realnych sprzedażach.** Strojenie na trzech dniach obserwacji to dopasowywanie się
do szumu. Pierwsze do zbudowania to `/kupilem <cena>` i `/sprzedalem <cena>` —
one wytwarzają prawdę, wobec której cokolwiek da się zweryfikować.

## Styl

Polski, bez żargonu w wiadomościach do użytkownika. Komentarz w kodzie tłumaczy
**dlaczego**, nie co — najlepiej z liczbą i datą pomiaru. Współczynnik wyceny nigdy
nie bierze się z głowy; ma wynikać z odduplikowanych danych albo go nie ma.
