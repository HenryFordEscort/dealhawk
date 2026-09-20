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
kod wyjścia 1, gdy wpis przestał się bronić, `--nowe` podpowiada kandydatów),
`najlepsze.py` (**BestDealHawk** - drugi kanał Telegrama, tylko najlepsze
oferty; czyta wyłącznie pliki zapisane przez `tracker.py`, nie pobiera nic
z sieci poza wysyłką, ma własny stan `best_wyslane.json` i milczy bez
`TELEGRAM_BEST_CHAT_ID`; testy: `python test_najlepsze.py`),
`sprawdz_modele.py` (przelicza `topowe_modele.json` - odpowiednik
`sprawdz_silniki.py` dla hierarchii modeli; kod wyjścia 1 przy różnicy),
`rozmiary.py` (czyta `seen.json` i wypisuje WYSŁANE oferty w jednym rozmiarze
ramy; nic nie pobiera i nic nie zapisuje, komenda `/rozmiar` na Telegramie),
`oferta.py` (składa gotową wiadomość z TWARDĄ ofertą do sprzedawcy; czyta
`seen.json` i `de_stan.json`, nic nie pobiera, nic nie wysyła - właściciel
kopiuje i wysyła sam; komenda `/oferta <id>` i przycisk pod powiadomieniem).

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

## Odblokowanie to za mało - rower musi mieć jak DOJECHAĆ (02.09.2026)

Skasowanie wpisu z `seen.json` daje ogłoszeniu pozwolenie na wejście, ale nie
daje mu drogi. Zmierzone: 01.09 zdjęliśmy 351 wpisów, a rower z pytania
właściciela (3492497177, żywy, przeceniony do 2 400 €) NIE WRÓCIŁ przez trzy
godziny. Powód jest strukturalny - półka pokazuje ogłoszenia ŚWIEŻE, a
zapytanie kluczowe sortuje po TRAFNOŚCI, więc ogłoszenie sprzed tygodnia
przepada na dalszych stronach.

> **SPROSTOWANIE z 19.09.2026: TO NIEPRAWDA.** Zmierzone z runnera na trzech
> frazach: Kleinanzeigen podaje wyniki wyszukiwania **już posortowane od
> najnowszych**. Wiek pierwszych pozycji rośnie (19, 22, 32 min dla „cube
> stereo hybrid"; 124, 217, 238 dla „trek rail"). Ogłoszenie nie przepada
> przez sortowanie - strona 1 frazy modelowej sięga ~3 h wstecz. Prawdziwą
> przyczyną opóźnienia jest ROTACJA, patrz rozdział „Sortowanie po dacie". Zapytanie „Cube Stereo Hybrid" chodziło
normalnie (44 wiersze 01.09) i tego ogłoszenia nie było w wynikach ani razu.

**`odblokuj.py --wznow` wpisuje ZALEGŁY ODCZYT, nie kasuje.** Wpis z `url`
i `nieodczytane` trafia do kolejki `do_odczytania` i bot pobiera go WPROST
PO ADRESIE - `ODCZYT_NA_SKAN` sztuk na skan, przez `ODCZYT_WAZNE_H` godzin.

**Adres odtwarzamy z samego numeru.** `market.jsonl` nie zapisuje `url`, ale
`/s-anzeige/a/<id>` oddaje pełne ogłoszenie - sprawdzone 02.09.2026 na
3492497177: tytuł, cena 2.400 € VB i numer ogłoszenia się zgadzają.

**Rozpoznanie „to my go zdjęliśmy" jest pewne, nie heurystyczne:** każde
ogłoszenie zapisane do `market.jsonl` dostaje wpis w `seen.json`, a
`seen.json` nigdy nie jest przycinany. Obecne w dzienniku rynku i nieobecne
w `seen.json` może pochodzić tylko od nas. Zmierzone: 349 takich na 50 932
wiersze od 20.08, przy 351 zdjętych (dwa zdążyły wrócić same).

> **SPROSTOWANIE z 18.09.2026: `seen.json` JEST przycinany.** Robi to
> `prune_seen` przy `SEEN_MAX_AGE_DAYS = 90`, wołane w `main`. Zdanie „nigdy
> nie jest przycinany" stało tu od 02.09 i jest nieprawdą - a rozdział
> o `/rozmiar` z 15.09 mówi poprawnie, że plik „zdąży się przyciąć", więc
> dokument przeczył sam sobie. Wniosek powyżej obowiązuje WYŁĄCZNIE w oknie
> 90 dni: ogłoszenie starsze wypadło z `seen.json` samo i jego nieobecność
> nie dowodzi, że to my je zdjęliśmy.

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

## BestDealHawk - drugi kanał (02.09.2026)

Właściciel: „wystarczy dzień przerwy i jestem totalnie zawalony nieodczytanymi
powiadomieniami".

**STRUMIEŃ ROŚNIE I TO ZMIENIA RACHUNEK.** Zmierzone 08.09.2026 na 1 424
ocenionych ofertach z 30 dni: połowa sierpnia to ~15 ofert dziennie, koniec
sierpnia 40-60, a 01-08.09 już **64-148 dziennie**. Po dniu przerwy to nie
sześćdziesiąt nieprzeczytanych wiadomości, tylko sto.

`najlepsze.py` wybiera z tego **2,5 oferty dziennie licząc całe 30 dni,
a 5,1 dziennie w samym wrześniu** (najgorszy dzień 7). Kanał jest więc
UŁAMKIEM strumienia, nie stałą liczbą - i tak ma być, bo próg stoi na
percentylu wobec własnej historii, nie na liczbie wpisanej na sztywno.
Mediana szacowanego zysku wybranych: 4 329 zł wobec 2 652 zł na całości.

**Nie dotyka DealHawka i to jest sedno konstrukcji.** Osobny proces, osobny
krok w `tracker.yml` z `continue-on-error`, własny plik stanu. Czyta `seen.json`
i `market.jsonl`, nie zapisuje do nich. Nie dokłada ani jednego żądania do
Kleinanzeigen - dławienie per adres IP jest w tym repo zmierzone i realne.
Podział ról ten sam co `dozorca.py` wobec `zycie_ofert.py`: tracker zapisuje
fakty, ten moduł wyciąga wnioski, więc zmiana reguły to PRZELICZENIE
(`--sucho --od`), a nie tydzień czekania na dane.

**Reguła wejścia:** rower musi zebrać wagę 2. Piętro „szczyt" daje 2 i wchodzi
samo; „wysoka" i „górna półka" dają 1 i potrzebują drugiego powodu (cena
w dolnym decylu swojego modelu albo przebieg w dolnym kwartylu modelu).

**PRZEBIEG LICZY SIĘ OD KWARTYLA, NIE OD MEDIANY.** Mediana była błędem
widocznym dopiero na skali: „poniżej mediany" spełnia z definicji POŁOWA
rowerów, więc to rzut monetą, a wnosiło pełną wagę. Zmierzone 08.09.2026:
kombinacja „górna półka + przebieg pod medianą" była największym workiem
na kanale (23 wejścia na 30 dni) i zarazem najsłabszym.

**Wagi 3 NIE wolno tu wpisać, choć kusi.** Sprawdzone: przy progu 3 druga
droga wejścia (model + cena + przebieg) odpala RAZ na 30 dni, czyli jest
martwa, a kanał zwęża się do samych modeli „szczyt". Powód jest w danych:
przebieg zna tylko 62% ofert, więc rower bez odczytu nie dobije do trzech
sygnałów nigdy. Reguła, która nie odpala, to nie reguła.

**Grupa porównawcza to sam MODEL, bez klasy baterii, i to jest świadoma
wymiana.** Para (model, bateria) daje dokładniejsze kwartyle, ale ma je tylko
23% ofert - przy takim pokryciu reguła dawała **0,1 roweru dziennie**, czyli
kanał martwy. Sam model daje 65%. Cenę tego poszerzenia płacimy wetem na małą
baterię i unieważnianiem argumentu ceny przy starszym roczniku.

**Rocznik NIE jest wetem, tylko unieważnia argument „tanio".** Pierwsza wersja
odrzucała cały rower poniżej mediany roczników modelu i wylatywały tak zdrowe
oferty (Cube Stereo Hybrid 160 HPC z 2021, Scott Patron eRide 910 z 2022).
Dwa powody: mediana roczników liczy się z pola `y` w `market.jsonl`, czyli
z TYTUŁU, a rocznik pisze w tytule głównie ten, kto ma świeży rower - mediana
jest zawyżona z definicji. I drugi: rocznik już siedzi w wycenie
(`year_factor`, 7,2% na rok), więc weto liczyło go drugi raz.

**Bateria czytana LUŹNIEJ niż w DealHawku, odwrotnie niż w regule 6.**
`is_small_battery` stoi na `battery_wh`, który wymaga literalnego „Wh", bo
tam brak odczytu znaczy „przepuść" i luźniejszy czytnik dokładałby ODRZUTY.
Tutaj brak odczytu znaczy „wpuść", więc luźniejszy czytnik dokłada WIEDZĘ.

## Pierwszy dzień pracy kanału - trzy poprawki (09.09.2026)

Właściciel po dobie: „przyszło coś fajnie bo poniżej ceny średniej rynkowej
ale to jest złom totalnie zużyty (...) interesują nas nowo dodane topowe
wersje". Rower, o którym mowa: **CUBE Stereo Hybrid 160 HPC SL 625 za 1 500 €,
bez przebiegu i bez rocznika w ogłoszeniu**. Wszedł jako „górna półka + tanio".

**1. Niska cena przy nieznanym stanie NIE jest dowodem okazji.** Najczęstszy
powód, dla którego rower jest bardzo tani, to zużycie. Gdy nie znamy ani
przebiegu, ani rocznika, nie da się tego wykluczyć, więc niska cena mówi
„nie wiem", a nie „okazja". Zmierzone na 82 wyborach z 30 dni: **17 (21%)**
stało wyłącznie na cenie przy zerowej wiedzy o stanie - i to z nich pochodził
złom. Ta sama zasada co przy roczniku i ta sama co w regule 6.

**2. „Górna półka" przestała wpuszczać sama.** To modele pospolite - sam Cube
Stereo 160 ma 328 sztuk na rynku - więc bycie nim niczego nie dowodzi.
Wszystkie oferty, które właściciel uznał pierwszego dnia za dobre, były ze
„szczytu" albo „wysokiej". Wpis zostaje w pliku, bo dalej NAZYWA model
i buduje grupę porównawczą; przestaje tylko wnosić wagę.

**3. Świeża generacja wnosi własną wagę** (`NOWY_ROCZNIK_OD`, liczone od
dzisiaj, nie wpisane na sztywno - inaczej za dwa lata kod chwaliłby rowery
czteroletnie). Rocznik jest JEDYNYM twardym odczytem generacji: pojemność
baterii do tego nie służy, bo zmierzone 400 Wh wychodzi nowsze niż 625
(producenci wracają do małych baterii w modelach lekkich).

**Czego świadomie NIE zrobiono: nie wymagamy piętra modelu.** Kusiło, bo
właściciel napisał „topowe wersje", i zdejmuje to 1,2 wiadomości dziennie.
Ale zmierzone: wypadłyby wtedy rowery typu Cube Stereo Hybrid 120 Race 750
z 2024 roku i **60 km przebiegu** za 2 699 €. To nie jest topowa wersja,
tylko rower praktycznie nowy za pół ceny - i to też jest „nowo dodane".

**Przy okazji: 2,6% ofert nie ma ceny w ogóle** („VB", „brak ceny"). Nagłówek
wypisywał wtedy „kupno VB", co w kanale o okazjach cenowych jest gorsze niż
przyznanie się. Dziś mówi wprost, że sprzedawca ceny nie podał.

Po tych trzech poprawkach: **2,9 oferty dziennie licząc 30 dni, 6,3 we
wrześniu**, mediana szacowanego zysku 4 324 zł wobec 2 645 zł na całości.

**Obniżki wchodzą OSOBNĄ drogą, przez `history.jsonl`.** Obniżka nie zmienia
pola `date` we wpisie (tracker aktualizuje cenę i przebieg, datę zostawia
z pierwszego spotkania), więc przez zwykłą ścieżkę przeceniony rower nie
wróciłby NIGDY - a to dokładnie zdarzenie, dla którego ten kanał powstał.
Klucz stanu to `id@cena`, nie samo `id`: ta sama przecena nie brzęczy dwa
razy, kolejna i niższa owszem. Zmierzone: 3,3 obniżki dziennie, z czego
4 na 30 dni kwalifikują się na kanał.

**Brak `topowe_modele.json` to CICHA awaria i dlatego krzyczy.** Zmierzone
08.09.2026: bez tego pliku kanał wybiera **0 ofert na 30 dni** zamiast 40,
bo piętro modelu wnosi wagę, bez której nic nie dobija do progu. Właściciel
widziałby pustą skrzynkę i myślał „słaby tydzień". Alarm leci RAZ (przy
84 biegach dziennie kanał zamieniłby się w alarm o samym sobie) i drugi raz,
gdy plik wróci - bo inaczej nie wiadomo, czy cisza jest już prawdziwa.

**ŚCIEŻKA NIGDY W DOMYŚLNYM ARGUMENCIE** (`def f(plik=WYSLANE_FILE)`).
Wiąże wartość w chwili definicji modułu, więc podmiana zmiennej modułowej nie
ma skutku. Ten błąd wyszedł tu DWA RAZY: raz zapis stanu szedł w stare
miejsce, drugi raz uciszył alarm o braku pliku modeli - alarm był napisany,
przetestowany i MARTWY. Pilnuje tego test na sam wzorzec, nie na pojedynczą
funkcję.

**Pierwszy bieg nie wysyła nic.** Zapisuje wszystko jako załatwione i milczy.
Bez tego włączenie kanału to jednorazowa lawina kilkudziesięciu rowerów
z ostatniego miesiąca, w większości dawno sprzedanych - ta sama pułapka co
przy `odblokuj.py`.

## Hierarchia modeli - `topowe_modele.json` (02.09.2026)

Właściciel: „stare cube stereo hybrid 160 to topowy model starszych cubów;
tych nowszych to cube stereo one 77, trek rail itd". Sprawdzone na 38 123
elektrykach (rowery, nie ogłoszenia) i potwierdzone co do joty:

| model | rowerów | mediana | w widełkach do 3000 € |
|---|---|---|---|
| Stereo Hybrid ONE77 | 33 | 4 399 € | 1 (3%) |
| Stereo Hybrid ONE44 | 174 | 4 000 € | 21 (12%) |
| Stereo Hybrid 160 | 347 | 2 490 € | 291 (84%) |
| Stereo Hybrid 120 | 796 | 2 200 € | 773 (97%) |

**`MODEL_PATTERNS` nie zna ONE22/44/55/77**, więc `olx_query_for` nazywa je
wszystkie „cube stereo hybrid" i porównuje topowy model z podstawowym.
Zmierzony skutek: Cube Stereo Hybrid ONE44 HPC SLX z 2025 za 2 499 € dostał
**score 30** przy medianie 44, mimo że własna wycena bota dała mu 5 463 zł
zysku. W kanale zbudowanym na górnym decylu `score` tego roweru by NIE BYŁO.

Plik ma kształt `silniki_bosch.json`: właściciel czyta i poprawia,
`sprawdz_modele.py` przelicza. Cztery pułapki, każda złapana na własnym
błędnym pomiarze, siedzą w `_PULAPKI` w pliku:

- **Liczba po nazwie bywa BATERIĄ.** Pierwszy przemiał zrobił „modele" ONE62,
  ONE75 i ONE80 z „Reaction Hybrid ONE **625**", „ONE **750**", „ONE **800**".
  Ta sama pułapka co usunięty „RockShox 30" w `wiedza_sprzet.json`.
- **Niemieckie słowa pospolite udają modele.** „Fahrrad" wyszło jako topowy
  model Haibike o medianie 3 799 €.
- **`re.escape` na nazwie modelu.** „powerfly+" wpisane wprost do regexpa
  znaczy „powerfl i co najmniej jedno y", więc zwykły Trek Powerfly 4
  (mediana 1 900 €) wchodził jako topowy Powerfly+ (3 299 €).
- **Marka PIERWSZA w tytule wygrywa.** „Mondraker Chaser e MTB Fully ÄHNLICH
  Cube Stereo Hybrid 160" wchodziło jako topowy Cube. Sprytu ze słowami
  porównania nie da się obronić: „ähnlich" stoi PRZED marką, a „wie neu"
  jest w co drugim niemieckim tytule i znaczy co innego.

**Werdykt o silniku jest w pliku przy każdym modelu i decyduje o wpuszczeniu
na kanał.** Bez tego lista byłaby ładna i bezużyteczna: 9 z 39 „topowych"
modeli bot nie kupi nigdy, bo filtr silnika odrzuca je wcześniej. Zmierzone:
Giant Stance 0 Boschów wobec 8 rywali, Raymon Trailray 0 wobec 16, Merida
eOne 0 wobec 6, **Canyon Torque:ON 0 Boschów na 21 ogłoszeń, trzy razy
wprost Shimano EP8** - co przeczy komentarzowi przy `PREMIUM_BRANDS`, że
nowsze Torque:ON od ~2023 mają Boscha. Filtr silnika i tak je odsiewa, więc
nic się nie psuje, ale komentarz jest nieaktualny.

## Filtr fully mierzył słowo, nie rower (02.09.2026)

Właściciel: „przecież to musiało mieć jakiś cel ten filtr, jeżeli nie ma
znaczenia i jest bezużyteczny to go wywal". Sprawdzone: **cel ma i to ważny,
ale mierzył co innego, niż myślał.**

**Po co jest.** `is_fully` stoi PRZED `czytaj_ogloszenie`, więc jest bramką
na RUCH, nie ozdobą. Zmierzone na 59 409 ogłoszeniach z 55 dni: dziś dochodzi
do pobrania strony **122 ogłoszenia dziennie**, a bez tego filtru doszłoby
**353**. Przy zmierzonym dławieniu Kleinanzeigen (~50 żądań w 10 minut
z jednego adresu = strona-śmieć na 20 minut) potrojenie ruchu to nie
oszczędność, tylko ślepota. **Kasowanie tego filtru jest jedyną opcją, która
jest wprost zła.**

**Co było zepsute.** Ta sama choroba co w `has_known_motor` przed poprawką
z 01-02.09: filtr mierzył, czy sprzedawca RACZYŁ napisać słowo. Dowód: na
37 modelach, przy których sprzedawcy sami piszą „Fully" w co najmniej 40%
ogłoszeń, odsetek przepuszczonych przez `is_fully` jest RÓWNY odsetkowi tych,
którzy to słowo napisali.

| model | pisze „fully" | przechodzi `is_fully` |
|---|---|---|
| Bulls Sonic | 63% | 63% |
| Conway Xyron | 67% | 69% |
| Giant Stance | 66% | 65% |
| Focus Jam² | 44% | 45% |

Nazwy modeli z `FULLY_KEYWORDS` nie wnosiły do tych rowerów ANI JEDNEJ własnej
informacji, bo żadnego z nich na liście nie było.

**Zmierzona cena pomyłki:** ~15 prawdziwych fully dziennie odrzucanych przed
pobraniem strony. Większość to marki niszowe, które i tak potrzebują 30%
zniżki, więc realna szkoda była skupiona w dwóch modelach z whitelisty:

```
Specialized Kenevo    65 rowerów, przechodziło 18 (28%), 27 w widełkach
KTM Macina Prowler    28 rowerów, przechodziło  5 (18%)
```

Oba przechodzą filtr silnika i nie są Levo FSR, więc to były gotowe oferty
tracone na słowie. Ani „kenevo" (71 ogłoszeń), ani „macina prowler" (30) nie
mają w danych ANI JEDNEGO tytułu ze słowem „hardtail".

**Poprawka:** trzy nazwy dopisane do `FULLY_KEYWORDS` (`kenevo`,
`macina prowler`, `cube stereo`). Koszt policzony przed wdrożeniem:
**+2,4 pobrania stron dziennie (+2%) i +0,5 powiadomienia dziennie.**
Reguła 1: `topowe_modele.json` liczy `fully_pct` przez `is_fully`, więc
został przeliczony w tym samym zadaniu - KTM Macina Prowler doszedł do listy
topowych jako „szczyt".

**PUŁAPKA PRZY DOPISYWANIU KOLEJNYCH: nie szukaj ich, mierząc, kto pisze
„Fully".** Modele, których na liście brakuje, to z definicji te, przy których
nikt tego nie pisze. Ta droga jest kołowa i przy pierwszym podejściu sama
odrzuciła Kenevo i Prowlera (próg 40% odsiał je przy ich 28% i 18%). Wiedza
musi przyjść z ROZPOZNANIA ROWERU, jak w `silniki_bosch.json`.

## Pętla zwrotna: przycisk „to szrot" (13.09.2026)

Przez tydzień reguła kanału była poprawiana **cztery razy** i za każdym razem
właściciel musiał przysłać LINK, a dwa razy i tak trafiono obok. Jedno
kliknięcie w chwili, gdy jest wkurzony, jest oznaczonym przykładem, a nie
anegdotą.

Pod każdą wiadomością na kanale są cztery przyciski, a ich powody wzięte
są WPROST z tego, co właściciel już powiedział: `zuzyty` („to jest złom
totalnie zużyty"), `cena` („czy według ciebie to jest mega okazja?"),
`rozmiar` („wypierdol S size, to jest niesprzedawalne"), `stary` (rower
z 2018 wysłany na samej nazwie modelu).

Kliknięcia lądują w `odrzuty.jsonl` (append-only) z KOMPLETEM kontekstu -
tytuł, cena, rocznik, przebieg, rama, powody wejścia - a nie z samym `id`.
Dzięki temu plik da się czytać za pół roku bez sklejania go z `seen.json`,
który do tego czasu zdąży się przyciąć.

**Nic z tego jeszcze nie wpływa na regułę.** Najpierw fakty, potem wnioski -
ten sam podział co `dozorca.py` wobec `zycie_ofert.py`.

**ODPYTUJEMY WYŁĄCZNIE WŁASNEGO BOTA** (`TELEGRAM_BEST_BOT_TOKEN`). Gdyby
kanał chodził na tokenie DealHawka, dwa procesy czytałyby tę samą kolejkę
`getUpdates` z przesuwanym wskaźnikiem, a Telegram po odczycie kasuje starsze
wpisy - więc raz jeden, raz drugi gubiłby zdarzenia, losowo i po cichu.
`czytaj_odrzuty` sprawdza to i przy wspólnym tokenie nie odpytuje wcale.

## Rocznik i przebieg z ADRESU oferty OLX (13.09.2026)

Polscy sprzedawcy rzadko wypełniają pola strukturalne. Zmierzone na 910
unikalnych ofertach z `rynek_pl.jsonl`: **rocznik znany w 22%, przebieg
w 24%**. A wycena porównuje do nich niemieckie rowery, więc dla trzech
czwartych porównań nie wie, z którego roku jest odniesienie.

To jest przyczyna błędu, który właściciel wychwycił 12.09: Specialized Levo
z 2020 dostał medianę PL 14 098 zł, policzoną głównie z nowszych roczników,
i zysk „5 043 zł" zamiast realnych kilkuset.

Tytuł siedzi w ADRESIE OLX (`/oferta/370-km-cube-stereo-...-CID767-ID...`),
a adres zapisujemy od zawsze - tylko nikt go stamtąd nie czytał. Robi to
`uzupelnij_z_adresu`, wołane przy wczytywaniu `rynek_pl.jsonl` ORAZ na wejściu
`zbuduj_cennik`. Zmierzony odzysk: **rocznik 22% → 27%, przebieg 24% → 35%**,
zero nowych żądań.

**NIE nadpisuje pól, które już są.** Pole strukturalne pochodzi z formularza
OLX i jest pewniejsze niż tytuł, w którym „2023" bywa numerem modelu.

Reguła 1: `cennik_cech.json` przeliczony w tym samym zadaniu. Skutek: rocznik
liczony na 246 rowerach zamiast 202, a jego waga drgnęła **z 6,6% na 7,7%
na rok**; przebieg na 316 zamiast 218.

## `/dojrzale` - kto schodzi z ceny i nadal stoi (13.09.2026)

`dojrzale.py` liczył to od sierpnia i **nie było go czym wywołać**: był
narzędziem z linii poleceń, a właściciel pracuje z telefonu.

Trzecie pytanie, obok dwóch dotychczasowych. DealHawk pyta „co nowego",
kanał najlepszych „co najlepsze", a to pyta **„kto już chce się tego
pozbyć"**. Sprzedawca po dwóch obniżkach negocjuje inaczej niż ten, który
wystawił wczoraj, i tam siedzi marża.

Komenda `/dojrzale` (albo `/dojrzale 3` dla mocniej przecenionych) pokazuje
sześć ofert w budżecie, posortowanych po wielkości przeceny. Rama S i XS
odpada tą samą regułą co na kanale najlepszych. Pierwszy bieg pokazał m.in.
Trek Powerfly FS 4 Gen 3, rama L, rocznik 2024, 65 km, przeceniony
z 2 700 na 1 700 €.

Nic nie pobiera - cały wynik pochodzi z dziennika, więc ogłoszenie mogło
w międzyczasie zniknąć, i wiadomość mówi to wprost.

## `/rozmiar` - przeglądanie po rozmiarze ramy (15.09.2026)

Właściciel: „chce zobaczyc tylko najnowsze ogloszenia w rozmiarze l
i wyswietlaja mi sie tylko te l lub te o ktorych nie ma info w ogloszeniu, bo
lepiej kilka wiecej przegladnac niz ominac; innego dnia chce przegladac tylko
najnowsze m size".

Czwarte pytanie obok trzech dotychczasowych. DealHawk pyta „co nowego", kanał
najlepszych „co najlepsze", `/dojrzale` „kto chce się tego pozbyć", a to pyta
**„pokaż mi to, po co realnie pojadę"**. Rozmiar decyduje o zbycie w Polsce
mocniej niż cena.

**TO JEST PRZEGLĄDARKA TEGO, CO JUŻ POSZŁO, A NIE DRUGI FILTR POWIADOMIEŃ** -
i tego rozróżnienia pilnuj. Gdyby rozmiar zaczął DŁAWIĆ wysyłkę, jeden dzień
z ustawieniem „L" oznaczałby ciszę o każdym M i S, których właściciel nigdy by
nie zobaczył. A że rozmiaru nie znamy w 86% ogłoszeń, dławiłby przede
wszystkim rowery, o których wiemy najmniej. Kanał sypie dalej wszystkim,
`/rozmiar` pozwala usiąść i przejrzeć wycinek.

**ILE TO NAPRAWDĘ ODSIEWA - dwa pomiary z 15.09.2026, nie jeden.** Na całym
zbiorze 90 dni (2 504 wysłane oferty) rozmiar czytelny w **349 (14%)**: L 156,
M 126, S 63, XS 4. To jest skuteczność SAMEGO TYTUŁU. Na oknie 3-dniowym,
którego wpisy mają już pole `rama` czytane z OPISU: **61 ze 159 (38%)**, w tym
30 rowerów L, 16 M, 9 S, 6 XL i 9 z samym centymetrem. Reszta ląduje w grupie
„bez info" i ZOSTAJE na liście - to nie usterka, tylko decyzja właściciela.
Odsiewamy wyłącznie rowery o znanym INNYM rozmiarze.

**Ta różnica 14% wobec 38% to wiek pola, nie szum.** `rama` powstaje od
12.09.2026, starsze wpisy mają wyłącznie protezę tytułową. Udział rośnie sam,
więc liczby jadą W NAGŁÓWKU KAŻDEJ WIADOMOŚCI, a nie w dokumentacji, która
zestarzeje się w tydzień - właściciel ma widzieć, ile TA lista odsiała, zanim
uzna, że przejrzał wszystkie L na rynku (reguła 6).

**Czego czytnik NIE umie, zmierzone na 2 155 ofertach bez rozmiaru:** 52 z nich
(26 L, 14 M, 12 S) mają rozmiar w sklepowym kształcie „Cube STEREO HYBRID 120
RACE 750 2023 **- L -** 175-185cm", czyli litera odizolowana myślnikami obok
zakresu WZROSTU. To +2,1 pkt proc. do odzyskania. **Nie dorobione świadomie:**
`rozmiar_ramy` karmi `_rama_czesci` → `sprzeczne_warianty`, czyli dedup, który
decyduje, czy NOWA oferta w ogóle pójdzie na Telegram. Poszerzenie czytnika
zmienia więc wysyłkę i musi iść osobno, z własnym pomiarem par sklejonych
i rozklejonych - nie przy okazji dokładania przeglądarki.

**Centymetry to „nie wiem".** „53 cm" znaczy co innego u Cube'a i Specialized,
więc taki rower zostaje w grupie bez info - ale wartość jest wypisana, żeby
właściciel ocenił sam. Ta sama decyzja co w `najlepsze.py`.

**Jeden czytnik litery dla całego repo:** `tracker.litera_ramy`. `najlepsze.py`
miał własną kopię i został na niego przepięty. Dwie kopie reguły, która
decyduje, czy rower w ogóle się pokaże, rozjeżdżają się przy pierwszej
poprawce.

**Przycisk NIE jest drugą ścieżką w kodzie.** `komenda_z_przycisku` zamienia
`rozm|L|3` na `/rozmiar L 3`, czyli na tę samą komendę, którą właściciel może
wpisać palcem - dalej idzie jedna droga i jeden zestaw błędów do naprawienia.
Pilnuje tego test obiegiem zamkniętym (przycisk → komenda → ta sama para).

**Przyciski DealHawka czyta `tracker`, bo to JEGO kolejka.** Ostrzeżenie
z `czytaj_odrzuty` (dwa procesy na jednym `getUpdates`) tu nie obowiązuje:
`najlepsze.py` odpytuje kolejkę wyłącznie przy WŁASNYM tokenie, więc czytelnik
kolejki DealHawka jest jeden. Callback z cudzego czatu przechodzi bokiem i nie
jest nawet potwierdzany. Odpowiedź przychodzi z opóźnieniem jednego ogniwa
(do ~minuty), więc `answerCallbackQuery` bywa odrzucone jako spóźnione - to nie
awaria i nie ma prawa zabrać komendy.

**Limit Telegrama liczony na CAŁEJ wiadomości, nie na kafelkach.** Adres
ogłoszenia to ~120 znaków ukrytych pod słowem „otwórz", a wiadomość ponad 4 096
znaków nie dochodzi W CAŁOŚCI. Zmierzone: pełna lista L z 7 dni to 3 857
jednostek UTF-16 przy sufitach 8 ofert pewnych i 6 bez rozmiaru.

## Rozmiar ginął na WŁASNEJ sklejce opisu (15.09.2026)

Właściciel po pierwszym dniu z `/rozmiar`: „160 ogłoszeń w 3 dni i tylko 30
pewnych? wyglada to narazie slabo". Sprawdzone na 20 żywych stronach ogłoszeń,
których bot nie umiał przeczytać.

**Sprzedawca napisał to wprost, bot sam sobie to zepsuł.** Żywy przykład:

```
Rahmengröße L<br />29 Zoll<br />
```

`re.sub('<[^>]+>', ' ', desc_html)` zamieniało oba `<br>` na spacje i robiło
z tego jedno zdanie „rahmengröże l 29 zoll". Wtedy strażnik „to koło, nie rama"
w `rozmiar_ramy` **słusznie** odrzucał całe dopasowanie razem z literą L.
Sprzedawca rozdzielił pola tak jasno, jak się da, a parser skleił je z powrotem
i odrzucił własną sklejkę. To samo działo się na wypunktowaniu:
`* Rahmenhöhe: 44 cm * 29-Zoll-Laufräder *`.

**Poprawka to DRUGI WIDOK tego samego opisu, nie zmiana pierwszego.**
`opis_z_polami` zamienia `<br>`, `</p>`, `</li>` i wypunktowanie na „|", bo
klasa ogona w `_RAMA_ETYKIETA` już ten znak wyklucza - granica pola działa więc
tą samą drogą co przecinek. Jedzie WYŁĄCZNIE do czytnika rozmiaru.
**`desc_text` zostaje bajt w bajt taki jak był** i to nie jest ostrożność na
wyrost: czytają go przebieg, bateria, zużycie i targ, a każdy z nich decyduje,
czy oferta w ogóle pójdzie na Telegram. Jeden wspólny widok znaczyłby, że
poprawka rozmiaru przestawia wysyłkę.

**Zmierzone na 20 stronach, pełną ścieżką produkcyjną: 2/20 → 5/20.** Zero
odczytów nadpisanych innym wynikiem - poprawka zamienia wyłącznie „nie wiem"
na odpowiedź. Willhaben nie oddaje widoku z polami i dostaje ścieżkę identyczną
ze starą, co do joty.

**Działa TYLKO W PRZÓD.** `seen.json` nie trzyma opisów, więc wpisów sprzed
poprawki nie da się przeliczyć (reguła 1 jest tu niewykonalna, nie pominięta).
Pole `rama` wypełnia się dla ofert przerobionych od tej chwili.

**Dlaczego reszta nadal milczy - rozbite na 12 ogłoszeniach:** 3 nie mówią
o rozmiarze NIC, 4 mają słowa wyglądające na rozmiar, które należą do czegoś
innego (tabelka sztycy „S size 125mm / M & L size 150mm", punkty montażowe
„Mounting Points Größe 1x S und 1x M", sklepowe „vergrößern uns", „45 km/h"),
2 gubiły się na sklejce (naprawione), 2 mają zapis nietypowy (`Gr. 18" (M)`,
„medium" w tytule), 1 ma samą literę w tytule przy mylącym opisie.

**Te cztery ze środka są powodem, dla którego czytnik ma dalej milczeć.**
Luźniejszy parser wpisałby tam rozmiar sztycy albo bidonu i wstawił rower do
cudzej listy - a wtedy znika właścicielowi z oczu. Luka jest tańsza od kłamstwa,
bo rower bez rozmiaru nadal ląduje na liście.

**Czego brakuje najbardziej, a nie jest parserem:** bot wyrzuca opis po
przeczytaniu, więc każdej zmiany czytnika nie da się PRZELICZYĆ - trzeba po nią
jechać do Kleinanzeigen, a sufit to ~22 żądania. Ten pomiar kosztował 33 pobrania.
Zanim ruszysz czytnik kolejny raz, dołóż zapis 200 znaków opisu wokół słów
rozmiarowych przy KAŻDYM nieudanym odczycie (~20 kB/dobę) - po tygodniu będzie
~700 prawdziwych przykładów i reguła 1 znowu zacznie obowiązywać.

## Przeliczenie wstecz pola `rama` (15.09.2026)

Poprawka `opis_z_polami` działa tylko w przód, a okno `/rozmiar` to 3 dni.
Żeby właściciel nie czekał na jego wymianę, strony 84 wysłanych ofert z 3 dni
bez litery rozmiaru pobrano jeszcze raz, z adresu lokalnego, nie z runnera:
odstęp 25 s, czujka dławienia `dozorca_de.ocen_strone`. 74 żywe, 10 zdjętych,
**zero stron okrojonych**.

**Zmierzone na tych samych 74 stronach: stary kod 13 rozmiarów, nowy 22.**
W `seen.json` dopisane 14 wartości, wyłącznie `"rama": null` → rozmiar
(sprawdzone porównaniem każdego wpisu z HEAD i `git diff`: 14 linijek).
`/L` z 3 dni: pewne L **34 → 38**, bez info **110 → 99**, pokrycie **39% → 45%**.

**PUŁAPKA - drugi kanał przestawia werdykt.** BestDealHawk co bieg ocenia od
nowa każdą NIEWYSŁANĄ ofertę z 2 dni, a rama L waży tam 1 przy progu 2. Rozmiar
dopisany wstecz potrafi więc wysłać wiadomość o rowerze sprzed doby. Liczone
przed wdrożeniem, najgorszy wariant: 14 wiadomości naraz, czyli jeszcze alarm
sufitu 8. Realnie wyszła 1 (Cube Stereo Hybrid 140 HPC SLX 750, powody
`rama` + `przebieg`). Właściciel zgodził się na zaległe powiadomienia wprost.
Gdyby nie - te oferty trzeba oznaczyć w `best_wyslane.json` jako załatwione
W TYM SAMYM commicie, bo każdy stan pośredni na `main` to bieg, który wyśle.

**PUŁAPKA - kara za dławienie zostaje na adresie.** Pierwsza próba tego dnia,
po ~35 wcześniejszych pobraniach, dostała strony 116-120 kB z opisem, ale bez
znaczników kontaktu - i słusznie stanęła po dwóch. Po 20 minutach przerwy
84 strony przeszły bez jednej wpadki.

**Wysyłka `seen.json` przy żyjącym bocie: łatka, nie scalanie.** Wynik trzymany
poza repo i nakładany na plik świeżo pobrany tuż przed pushem. Git scala ten
plik liniami, bo `save_seen` zapisuje go wieloliniowo: 6 z 6 czystych scaleń na
prawdziwych commitach bota. Test w piaskownicy, w którym bot wpycha zmianę
TEGO SAMEGO wpisu w trakcie pusha, zachował i jego cenę, i nasz rozmiar. Po
wdrożeniu bot zapisał dwa razy na naszym commicie, 22 z 22 rozmiarów przetrwało.

**PUŁAPKA - narzędzie jednorazowe, które pushuje, musi mieć bezpiecznik.**
Moduł z łatką przestawiał katalog procesu PRZY IMPORCIE, więc test „w
piaskownicy" po cichu robił `git pull` na żywym repo bota. Zatrzymał go tylko
warunek „nic do dopisania, nie commituj". Katalog ustawiaj PO importach,
a w trybie testu odmawiaj pracy, gdy `origin` wskazuje na GitHuba.

**DWIE SESJE NA JEDNYM KATALOGU.** Tego dnia równolegle pracowały dwie sesje,
a plik roboczy zmieniał się między dwoma poleceniami. Nie `stash` i nie
`pull --rebase` na cudzych niezacommitowanych zmianach - własny commit buduj
w osobnej kopii (`git worktree add --detach`), a druga sesja ściągnie go
zwykłym `pull`.

## Wycena porównywała rower z innymi modelami (17.09.2026)

**`olx_relevant_offers` wyrzuca z zapytania wszystkie liczby**
(`not t.isdigit()`), więc „trek rail 5", „trek rail 9" i „trek rail" dostają
TĘ SAMĄ pulę - zmierzone: 42 oferty o identycznych adresach, w tym Rail 5, 7,
9.5, 9.7, 9.8 i 9.9 naraz. Cube Stereo Hybrid 120, 140 i 160 dzieliły 284
oferty razem z ONE44.

**NIE ZDEJMUJ tego wyrzucania liczb.** To cicha łatka na inny błąd: wzorce
w `MODEL_PATTERNS` (`cube stereo hybrid\s*\d*`) łapią KAŻDĄ liczbę po nazwie,
więc w `olx_watch.json` stoją klucze „cube stereo hybrid 2021" (rocznik),
„750" (bateria), „29" (koło), „2000" (cena). Linijka weszła 09.07.2026
w commicie o precyzji bez żadnego uzasadnienia - uzasadnienie odtworzone
dopiero pomiarem. Zdjęta wprost zabiłaby wycenę tych rowerów: klucz „cube
stereo hybrid 2021" zostałby z pulą ZERO.

**Naprawa nie rusza kluczy ani danych popytu.** `wariant_modelu` czyta wersję
osobno (z tytułu i ze sluga OLX, gdzie „9.7" staje się „9-7"), a
`zawez_do_wariantu` zawęża pulę tylko przy wycenie. Wersje wyłącznie tam, gdzie
mieszanie zmierzono: Cube Stereo Hybrid (120-160, ONE22-77), Trek Rail
(5, 7, 9, 9.5-9.9), Specialized Levo/Kenevo (wersja). Dwa zapasy:

- **na WYNIKU, nie na liczebności puli** - cennik odrzuca oferty bez znanej
  cechy i wymaga czterech przeliczonych, więc pula „pięć ofert" potrafi nie
  dać nic, a rower nie może stracić wyceny przez próbę zawężenia;
- **wąska pula tylko przy pewności co najmniej „średniej"**. Bez tego Cube
  ONE22 spadał o 34%: pula tej wersji miała równo 5 ofert, surowa mediana
  prawie ta sama co szeroka (12 500 wobec 12 552 zł), a jedna podejrzanie
  tania oferta i przeliczenie cennikiem przewracały wynik.

Zmierzone na 1 914 ofertach ocenionych od 18.08: **607 wycen z tej samej
wersji, zero znikniętych**, zmiany w przedziale ±15% i w kolejności, jakiej
należy się spodziewać: Stereo 120 -4,7%, 140 -2,0%, 160 +5,5%, ONE44 +14,1%;
Levo Alloy +2,3%, Comp Alloy +4,9%, Comp (karbon) +12,7%. Wersje z cienkimi
danymi w Polsce (Levo Pro, Rail 9.7, ONE22/55/77) zostają przy szerokiej puli.
Cennik cech bez zmian (sprawdzone przeliczeniem).

**S-Works nie miał klucza wyceny w ogóle.** 55 z 89 tytułów S-Works
Levo/Kenevo dawało `olx_query_for` = None, a `main` podstawiał wtedy nazwę
WYSZUKIWANIA („kanał MTB") - najdroższe rowery wyceniane względem wszystkich
elektryków naraz. Zapas w `olx_query_for` działa tylko, gdy wzorzec nic nie
znalazł: 0 z 105 855 tytułów zmienia dotychczasowy klucz, 51 z 55 trafia
w klucze z danymi popytu.

## Twarda oferta jedną wiadomością - `/oferta` (17.09.2026)

Właściciel przysłał zrzut własnej rozmowy z Kleinanzeigen. Przy ofercie
2 800 € VB napisał sprzedawcy **2 200 € „fest"** i dołożył cztery rzeczy naraz:
przyznanie, ile tamten woła, odbiór osobisty za gotówkę, obietnicę BRAKU
DOGADYWANIA NA MIEJSCU i propozycję zaliczki na rezerwację. Komentarz: „chodzi
o ogólny sens, mogą być inne słowa".

**Ta wiadomość nie sprzedaje ceny, tylko PEWNOŚĆ.** Sprzedawca oddaje kilkaset
euro, a dostaje koniec z oglądaczami, koniec z targiem pod domem i termin
ustawiony pod siebie. Dlatego kwota musi być jedna i twarda - „od 2 200 wzwyż"
nie kupuje niczego, a zdanie o braku nachodzenia jest tu ważniejsze od kwoty.

**Kwota idzie z `cena_po_ogledzinach`, nie z `realistic_buy_price`, i to jest
sedno.** Tamta zostawia drugi etap targu na spotkanie; ta wiadomość ten etap
SPRZEDAJE, więc zapas musi siedzieć w kwocie albo przepada. Zgodność z realnym
zachowaniem właściciela: przy 2 800 € VB generator daje **2 200 €**, czyli
dokładnie tę kwotę, którą wpisał ręcznie. Jeden przypadek, więc to zgodność,
a nie pomiar - ale sufit `NEGO_MAX_LACZNIE` (22%) i jego -21,4% stoją obok
siebie nieprzypadkowo.

**Zaokrąglenie W DÓŁ do 50 €, ale SUFIT BIJE ZAOKRĄGLENIE.** Twarda oferta jest
decyzją, a nie wynikiem dzielenia: „2.268 €" widać, że policzyła maszyna,
i zaprasza do kontroferty „2.400 €", czyli do targu, którym ta wiadomość ma nie
być. Pierwsza wersja cięła w dół bez zabezpieczenia i wypychała tanie rowery
na **25% przy sufirce 22%** - przy rowerze za 1 000 € pięćdziesiątka to całe
5 punktów procentowych. Zmierzone na 2 730 złożonych ofertach z `seen.json`:
23 naruszenia na 80 kombinacjach cena×luz. Gdy cięcie w dół łamie sufit,
zaokrąglamy w GÓRĘ - wolimy oddać 50 € niż wysłać kwotę, na którą sprzedawca
nie odpisze.

**ŻADNEGO WYMYŚLONEGO DNIA ODBIORU.** Wersja ze zrzutu miała „Mittwochabend",
ale bot nie wie, kiedy właściciel jeździ, a zły dzień w wiadomości do obcego
trzeba potem odkręcać. Decyzja właściciela: „bez konkretnego dnia, w przeciągu
kilku dni albo po umówieniu". Zamiast daty idzie elastyczność („kurzfristig",
„wie es dir passt") - zobowiązanie zostaje, zgadywanie znika. Pilnuje tego test
szukający w treści nazw dni tygodnia.

**Zaliczka TYLKO na życzenie** (`/oferta <id> zaliczka`). To najmocniejszy
dowód powagi w całej wiadomości i zarazem jedyne zdanie, w którym ryzyko jest
po naszej stronie - przy ogłoszeniu, które może być naciągane, ma nie wychodzić
samo z siebie.

**Wiadomość twierdzi o sprzedawcy rzeczy faktyczne, więc wolno jej powołać się
WYŁĄCZNIE na sygnał niesprzeczny.** „VB" czytamy WPROST z pola ceny, bo to ten
sam napis, który sprzedawca widzi u siebie na ogłoszeniu - nie z opisu i nie
z `nego_pct`. Gdy plakietka mówi „VB", a opis „Festpreis", sprzedawca przeczy
sam sobie: wtedy nie twierdzimy ŻADNEGO z tych dwóch, a kwota zostaje ta
ostrożniejsza. Zmierzone: 1 taki wpis na 2 800 wysłanych ofert. Pomyłka w tę
stronę jest natychmiast widoczna dla adresata i kompromituje resztę.

**„Festpreis" ODTWARZAMY z `nego_pct`, bo `seen.json` nie zapisuje powodów -
i to odtworzenie jest pewne, nie zgadywane.** Gałąź Festpreis w
`negotiation_headroom` ma natychmiastowy return i oddaje `NEGO_BASE_FIXED` co do
joty, a każda inna ścieżka startuje z 0,05 albo 0,10 i wyłącznie DOKŁADA.
Pilnuje tego test przemiatający wszystkie kombinacje opisów i cen: gdyby ktoś
kiedyś ustawił `NEGO_BASE_OPEN` na 0,02, funkcja zaczęłaby kłamać PO CICHU.

**Nie mieści się w przycisku i dlatego idzie osobną drogą.** `copy_text` w API
Telegrama ma limit 256 znaków, a ta wiadomość ma 651-795 (zależnie od wariantu).
Ucięta traciłaby dokładnie zdanie o braku dogadywania, czyli to, po co jest.
Idzie więc w bloku `<pre>` osobną wiadomością, po komendzie.

**Przycisk NIE jest drugą ścieżką w kodzie** - `of|<id>` zamienia się na
`/oferta <id>`, tę samą komendę, którą właściciel może wpisać palcem. Ta sama
zasada co przy `/rozmiar` i pilnuje jej test obiegiem zamkniętym. Bez przycisku
komenda byłaby martwa: z telefonu nikt nie przepisuje dziesięciu cyfr numeru
ogłoszenia z ekranu. Z tego samego powodu komenda przyjmuje WKLEJONY LINK -
i wycina jego ogon (`-217-1745`), bo 1745 mieści się w widełkach ceny i bez
tego wygrywało z prawdziwą kwotą podaną obok.

**Słowo „oferty" należy do `/zycie`** od sierpnia i zostaje tam. Nowa komenda
odzywa się na `/oferta`, `/of` i na wklejony link - dopisanie „oferty" zabrałoby
tamtej komendzie jej własną nazwę po cichu.

**Stare krótkie wiadomości zostają nietknięte.** `wiadomosc_do_sprzedawcy`
i `wiadomosc_oferta` dalej obsługują pierwszy kontakt, kiedy jeszcze nie
wiadomo, o czym się rozmawia, i dalej piszą na „Sie". Nowa pisze na „du", bo
tak napisał właściciel i tak piszą do siebie prywatni na Kleinanzeigen. Zmiana
działającego, sprawdzonego tekstu bez powodu to ryzyko za darmo.

**PRZEKŁAD NA POLSKI JEDZIE POD KAŻDĄ WIADOMOŚCIĄ.** Właściciel: „tłumaczenie
też daj". Wysyła ten tekst pod WŁASNYM nazwiskiem do obcego człowieka, a
niemieckiego nie czyta - bez przekładu podsuwamy mu dokładnie tę czarną
skrzynkę, przed którą ostrzega pierwszy akapit tego pliku.

- **Oba języki powstają w JEDNYM rozgałęzieniu** (`_akapity` oddaje pary
  niemiecki-polski). Osobna funkcja tłumacząca rozjechałaby się z oryginałem
  przy pierwszej poprawce, i to PO CICHU, bo nikt nie czyta niemieckiego, żeby
  porównać. Pilnuje tego test przemiatający 120 wejść: liczba akapitów, obie
  kwoty, akapit o zaliczce, wariant VB i wariant Festpreis. Na sfingowanym
  rozjeździe łapie 50 przypadków.
- **NIE tłumaczymy maszynowo.** `tlumacz_opis` (MyMemory) zrobił z „Nur 2000 km
  gelaufen" zdanie „Spacerowaliśmy niecałe 2000 km". Przy opisie-ozdobniku to
  nic nie kosztuje, przy tekście wysyłanym obcemu kosztuje rower.
- **Przekład stoi POZA blokiem `<pre>`** i mówi wprost „tego NIE wysyłaj".
  W środku bloku jedno stuknięcie wysłałoby Niemcowi polski tekst.
- **Bez `<blockquote>`**, choć czytałoby się lepiej: `send_telegram` NIE MA
  zapasu na błąd składni HTML, więc nieznany znacznik to trzy nieudane próby
  i wiadomość przepada z samym wpisem w logu. Znaczniki wyłącznie takie, jakie
  w tym repo już chodzą.
- Kwoty po polsku ze spacją (`2 250`), po niemiecku z kropką (`2.250`).
  „2.250" czytane po polsku znaczy 2,25.

Najdłuższy wariant z przekładem: 2 010 znaków przy limicie Telegrama 4 096.

**Bot tego NIE WYSYŁA.** Składa tekst, właściciel kopiuje i wysyła ze swojego
konta. Twarde ograniczenie „bot NIE negocjuje sam" stoi dalej: w całym module
nie ma ani jednego żądania poza czytaniem dwóch plików.

**Czego ten generator NIE wie:** czy te wiadomości w ogóle działają. Zapisanych
transakcji właściciela jest nadal **0**, więc -18% (mediana zejścia na 2 730
ofertach) to ZAŁOŻENIE złożone ze stałych `NEGO_*`, a te są w kodzie wprost
opisane jako założenie, nie pomiar. Wiadomość na Telegramie mówi to wprost
(reguła 6). Pierwsze do zbudowania jest dalej to samo: `/kupilem` i `/sprzedalem`.

## Dublet na kanale najlepszych: stan zapisywany raz po pętli (18.09.2026)

Właściciel: „wyslales dwa razy te same kilka ogloszen". Zmierzone i odtworzone
w piaskownicy na kodzie sprzed naprawy: **2 wiadomości poszły, 0 zapisanych**,
czyli obie wychodzą drugi raz z następnego ogniwa.

`najlepsze.main` trzymał `wyslane[ad_id]` W PAMIĘCI przez całą pętlę i wołał
`save_wyslane` dopiero po niej. A w tej pętli siedzi żądanie sieciowe
(`czy_zyje` na ofertę) i pauza 1,2 s, przy suficie ogniwa 8 minut. Trzy rzeczy
składają się tu w cichy dublet:

1. Wiadomość u właściciela to fakt, którego nie da się cofnąć, a ślad po niej
   powstawał dopiero na końcu.
2. Bieg jest jednym z **siedmiu ogniw** matrycy w `tracker.yml`, więc następne
   ogniwo startuje kilkadziesiąt sekund później i widzi stan sprzed wywrotki.
3. Krok ma `continue-on-error: true`, więc wywrotka NIE zapala się w Actions -
   widać ją dopiero na telefonie.

**Zapis idzie teraz PRZED wysyłką**, dokładnie tą samą zasadą, która stoi
w `tracker.main` od dawna i jest tam opisana jednym zdaniem:

> Zapisz bazę (plik + git) — DOPIERO POTEM wysyłka.
> Przerwany run = co najwyżej brak powiadomienia, nigdy duplikat.

Pośrednia wersja (zapis PO każdej wysyłce) zamykała 99% dziury, ale nie całą:
wywrotka między wysłaniem a zapisem nadal dublowała tę jedną wiadomość.
Przy zapisie PRZED wysyłką najgorszy przypadek to jedna wiadomość, która nie
dojdzie - a ten rower i tak poszedł wcześniej na DealHawka, bo ten kanał
wybiera WYŁĄCZNIE spośród ofert, które tamten już wysłał. **Zgubić jest tu
tańsze niż zdublować** i nie jest to wybór estetyczny: powtórka wygląda jak
awaria bota, a brak powtórki jest niewidoczny.

Nieudana wysyłka też zostaje oznaczona jako załatwiona - inaczej wracałaby
co bieg, czyli zamieniłaby jedną cichą stratę w pętlę hałasu.

**Anulowany bieg to nie teoria.** Zmierzone 18.09.2026 na pięciu ostatnich
biegach: **trzy skończyły się jako `cancelled`**. Obok crona lecą biegi
`workflow_dispatch` co równe 5 minut (źródła nie ma w repo - dispatch idzie
z zewnątrz albo z ręki), a `concurrency` zdejmuje wtedy ten oczekujący.
Każdy taki zgon w środku pętli był jednym dubletem.

To ta sama rodzina co „alarm działał, kompensacja nie" z 01.09: mechanizm był
napisany i przetestowany, tylko odpalał się w złym momencie.

**Przycisk pełnej oferty musi być OSOBNO na kanale najlepszych.** Kliknięcia
z BestDealHawka trafiają do kolejki `getUpdates` JEGO bota, a `tracker` jej nie
czyta i czytać nie może - dwa procesy na jednej kolejce gubiłyby zdarzenia
losowo i po cichu (patrz `czytaj_odrzuty`). Wpięcie guzika wyłącznie
w `tracker.py` znaczyło więc, że na tym kanale go nie ma w ogóle. Robi to
`klawiatura_pod_oferta` plus gałąź `of|` w `czytaj_odrzuty`.

**KLUCZ OBNIŻKI TRZEBA OBCIĄĆ.** Na tym kanale `ad_id` bywa w postaci
„id@cena" (ścieżka przecen), a `/oferta` przyjmuje sam numer - z ogonem
komenda odbiłaby się o własną walidację i przycisk wyglądałby na zepsuty.
Przyciski odrzutu zostają przy PEŁNYM kluczu, bo tam chodzi o oznaczenie
konkretnej wiadomości, także przeceny.

## Wydłużenie ogniwa było BŁĘDEM - cofnięte tego samego dnia (18.09.2026)

Zapisane, bo kusi ponownie i wygląda na oczywistą poprawę.

**Co zrobiłem źle.** Bot stał 2,5 h, biegi `tracker.yml` masowo kończyły się
jako `cancelled` bez utworzenia zadania, a pokrycie rynku wyszło ~5% czasu przy
konstrukcji projektowanej na skan co 40-60 s. Włączyłem `DEALHAWK_PETLA_MINUT=5`,
żeby jeden bieg pokrywał ~35 min rynku zamiast ~3. Policzone, przetestowane,
wdrożone - i wywrócone do góry nogami przez rzecz, której nie sprawdziłem.

**Czego nie sprawdziłem: skąd bierze się tempo.** Stoi to w nagłówku
`otomoto.yml` od 15.09.2026:

> Bot rowerowy tego nie odczuł, bo wyzwala go ZEWNĘTRZNY workflow_dispatch
> co 5 minut.

Powstał, bo GitHub od 27.08 dowoził z crona `*/30` medianę **jeden bieg na
3,6 h** (p90 5,7 h, maksimum 11,4 h). To szturchanie nie jest usterką ani
cudzym śmieciem - **to jest linia ratunkowa DealHawka** i szukałem go jako
winowajcy, zamiast przeczytać własną dokumentację repo.

**Dlaczego pętla ten układ zabija.** Grupa `concurrency` trzyma jeden bieg
w toku i jeden czekający, a nowy czekający kasuje starszego. Przy biegu
2-4 min wszystko się domyka przed następnym szturchnięciem i nikt nikogo nie
kasuje. Siedem ogniw po 5 minut to ~35 min na bieg, czyli **siedem zabitych
szturchnięć z rzędu** i bot stoi.

**Reguła, która z tego zostaje: DŁUGOŚĆ BIEGU JEST ZWIĄZANA Z ODSTĘPEM
WYZWALACZA, a wyzwalacz siedzi POZA tym repo.** Zanim ruszysz tempo, czas
życia ogniwa albo `concurrency`, sprawdź najpierw, co i jak często odpala
workflow - bo tego nie widać w żadnym pliku tutaj. Pilnuje tego test czytający
`tracker.yml`: pada, gdy ktoś znowu wstawi tam pętlę.

**Co zostało z tej wpadki jako zysk.** Tryb zapasowy `PETLA_MINUT` był
NIETESTOWANY, a miał zostać ścieżką produkcyjną. Uruchomienie go w piaskówce
(brama zamknięta, zero żądań: 61 s, kod 0) pokazało, że **pętla nie odpytywała
komend** - `process_telegram_commands` siedzi wewnątrz `main`, a `main` przy
zamkniętej bramie się nie woła. Po wpadce tempo cofa się do 300 s, więc
`/oferta`, `/rozmiar` i przycisk milczałyby do pięciu minut. Poprawka została,
choć sam tryb jest znowu wyłączony.

**Śmieci w kolejce, znalezione przy okazji.** Biegi zakleszczone w stanie
`queued` od 15.06, 27.08 i 13.09 - trzech miesięcy nikt nie sprzątał. GitHub
nie pozwala ich anulować („Cannot cancel a workflow run that has not been
queued yet"), więc najpewniej są duchami, ale warto o nich wiedzieć przy
następnej diagnozie zatkanej kolejki.

**Czego NADAL nie wiadomo:** dlaczego 18.09 od ~08:45 biegi przestały dostawać
runnera mimo krótkich ogniw. Rachunek jest czysty (0 z 2 000 minut, publiczne
repo ma Actions za darmo, 248 $ zużycia w całości pokryte zniżką), grupy
`concurrency` nikt inny nie dzieli, a OtomotoHawk w tym samym repo chodzi
normalnie. To zostaje otwarte.

## Zakleszczony bieg zatkał DealHawka na 16 godzin (17-18.09.2026)

Właściciel: „od wczoraj coś jebło". Miał rację i liczby to pokazują co do minuty.

| kiedy | skanów na godzinę |
|---|---|
| 17.09, godz. 21 | **63** |
| 17.09, godz. 22 | **68** |
| po 22:39:22 | **1-3**, przez 16 godzin |

Bot robił skan mniej więcej co minutę, czyli dokładnie tyle, ile zakłada
konstrukcja. Po 22:39:22 stracił 95% tempa w jednej minucie i sam nie wrócił.

> **SPROSTOWANIE METODY z 19.09.2026: liczb 63 i 68 nie da się odtworzyć
> i nie wiadomo, co liczyły.** Przeliczone tego dnia na tym samym oknie
> (17.09, 21:21-22:39) wychodzi **100 commitów albo 51 skanów na godzinę**
> i żadna z tych liczb nie jest tamtą. Różnica bierze się stąd, że JEDNO
> ogniwo zostawia DWA commity `update seen.json` - jeden z `persist_seen_git`
> przed wysyłką, drugi z kroku „Zapisz seen.json" - więc commity trzeba
> sklejać w pary, zanim policzy się skany.
>
> **Licząc tempo, podawaj metodę razem z liczbą.** Bez tego następny pomiar
> nie ma się do czego przyłożyć: porównywanie 48 z 63 przy dwóch różnych
> definicjach „skanu" daje fałszywy ubytek jednej czwartej tempa.

**Mechanizm.** Do kolejki `tracker.yml` wszedł bieg, który nigdy nie
wystartował i nigdy nie umarł - stan `queued` bez końca. Grupa
`dealhawk-tracker` przepuszcza jeden bieg naraz, więc każde kolejne
szturchnięcie stawało za nim jako `pending` i ginęło skasowane przez
następne. Ręczne anulowanie zakleszczonego NATYCHMIAST przepuściło skan -
to jest dowód, nie hipoteza.

**Zombie leżały tam od miesięcy:** 15.06, 27.08, 13.09 i 18.09. Nikt ich nie
sprzątał, bo nic ich nie sprzątało. Starszych GitHub nie pozwala już anulować
(„Cannot cancel a workflow run that has not been queued yet").

**Sprzątacz siedzi w `otomoto.yml`, nie w `tracker.yml`, i to nie jest
przypadek:** zakleszczony bieg blokuje WŁASNY workflow, więc sprzątacz w nim
nigdy by nie ruszył. Łańcuszek Otomoto chodzi niezależnie co 30 minut i ma już
`actions: write`. Zatkanie trwa więc najwyżej pół godziny zamiast 16.

**Czego NIE wolno zrobić, choć leczy objaw jednym słowem:**
`cancel-in-progress: true` u DealHawka. Wtedy nowy bieg kasuje pracujący
i zatkanie znika - ale ginie skan w trakcie, a **założenie tego bota jest
odwrotne: powiadomienie ma przyjść najszybciej, jak się da**. Zgubiony skan
łamie sens całej konstrukcji tak samo jak zatkana kolejka. Właściciel odrzucił
to wprost, słusznie. Sprzątacz nie dotyka ANI JEDNEGO biegu, który pracuje:
czyta wyłącznie `status=queued`, nigdy `in_progress` ani `pending`.

**Próg 10 minut.** Zdrowy bieg dostaje runnera w sekundy i kończy się w 2-4
min. `queued` dłuższy niż dwa cykle szturchnięć to zakleszczenie, nie
zatłoczenie. `pending` zostaje nietknięty - to normalne czekanie na grupę
i rozwiązuje się samo.

**Wniosek ogólny: awaria może siedzieć POZA kodem i poza plikami repo.**
Pół dnia szukałem jej w bocie, potem w rachunku GitHuba (czysty: 0 z 2 000
minut, publiczne repo ma Actions za darmo), a leżała w kolejce zadań, gdzie
nikt nie zaglądał. Przy następnej ciszy bota sprawdź kolejkę PRZED kodem:
`gh api "repos/.../actions/workflows/tracker.yml/runs?status=queued"`.

## Alarm o zerwanej drodze nie może jechać tą drogą (18.09.2026)

Tego dnia token bota przestał działać. Każda wysyłka padała 3 na 3,
`send_telegram` zapisywała błąd do logu i **wracała bez słowa**, a bieg kończył
się kodem 0. W Actions świeciło się na zielono przez cały czas trwania awarii.
Właściciel stracił pięć powiadomień i dowiedział się o wszystkim dopiero wtedy,
gdy sam zapytał, czemu jest cicho.

Diagnostyka „wszystko ok" też nie miała jak dojść, bo jechała tą samą drogą,
która padła. To jest sedno tej wpadki i dlatego ma własny rozdział obok reguły
7: tam chodzi o to, żeby cichą awarię ZAUWAŻYĆ, a tu zauważona była - tylko
jedyny kanał raportowania biegł przez zepsutą rurę.

Rodzina ta sama co „alarm działał, kompensacja nie" z 01.09: mechanizm
istniał, był przetestowany i sygnał szedł tam, gdzie nikt go nie odbierał.

**Poza Telegramem zostaje jeden świadek: KOD WYJŚCIA biegu.** Zgubiona
wiadomość maluje więc krok w Actions na czerwono. Robią to `ZGUBIONE_WYSYLKI`
i `zakoncz()` w `tracker.py` oraz zwrot 1 z `najlepsze.main`.

Cztery warunki, których nie ruszać:

- **Liczymy WIADOMOŚCI, nie próby.** `send_telegram` ponawia 3 razy przez ~6 s,
  więc jeden wpis znaczy „ta wiadomość nie doszła i już nie dojdzie". Liczenie
  prób zapalałoby się przy zwykłym 429.
- **Kod ≠ 0 dopiero na KOŃCU biegu**, nie w miejscu awarii. `main` zapisuje
  `seen.json` i pushuje PRZED wysyłką, a krok „Zapisz seen.json" ma
  `if: always()`. Czerwony bieg kosztuje więc wyłącznie kolor - nie gubi ani
  jednego ogłoszenia.
- **`fail-fast: false` w `tracker.yml` jest warunkiem koniecznym tej czujki.**
  Bez niego pierwsze czerwone ogniwo kasuje sześć pozostałych, czyli robi
  dokładnie to, czego robić nie wolno: gubi skan. Wartość już tam stała, ale
  teraz od niej coś zależy, więc pilnuje jej test.
- **Kanał najlepszych oddaje 1, a nie wywraca biegu.** Krok ma
  `continue-on-error: true` i tak zostaje - rowery są ważniejsze od tego
  kanału. W Actions zostaje czerwony znacznik przy samym kroku i to wystarczy.

**Czego ta czujka NIE robi: nie ponawia.** DealHawk gubi wiadomość świadomie
(„zgubić jest tu tańsze niż zdublować"), więc czerwony kolor mówi tylko tyle,
że coś przepadło. OtomotoHawk ma pod tym względem więcej - kolejkuje nieudane
wysyłki i ponawia je przez dobę. Przeniesienie tego do DealHawka jest otwarte
i nie było robione przy okazji.

**Pułapka przy pisaniu testu na to.** Wcześniejsze bloki w `test.py` podmieniają
`tracker.send_telegram` na atrapy i nie oddają oryginału. Test czujki sprawdzał
przez to cudzą lambdę i przechodził zawsze - pieczątka, nie strażnik (reguła 2).
Prawdziwa funkcja jest łapana do `_PRAWDZIWY_SEND` zaraz po imporcie.

## Zawieszony git zatykał łańcuszek od środka (17-18.09.2026)

To jest przyczyna ciszy, o którą właściciel pytał dwa dni („od wczoraj coś
jebło"). Nie kod bota, nie rachunek GitHuba, nie blokada Kleinanzeigen.

Zmierzone na logu biegu 35360782054, ogniwo 1:

| krok | czas |
|---|---|
| tracker | 15:15:29 → 15:16:25, **56 s** |
| kanał najlepszych | 15:16:25 → 15:16:32, **7 s** |
| „Zapisz seen.json" | 15:16:32 → 15:28:25, **11 min 53 s**, zakończony SIGTERM |

Git wypisał `[main f428f01] update seen.json` i **zamilkł na dwanaście minut**.
Ani jednej linijki, ani błędu, ani postępu - aż runner dostał kod 143, czyli
sufit `timeout-minutes: 8`. Ogniwo zżarło 13 minut zamiast półtorej.

**Zatkanie idzie stąd prosto w kolejkę.** Grupa `concurrency` trzyma wtedy
jeden bieg przez kwadrans, a szturchnięcia z zewnątrz przychodzą co 5 minut -
więc kolejny wchodzi jako `pending` i ginie skasowany przez następny. Efekt
w Actions wygląda jak masowe `cancelled` i łatwo wziąć go za usterkę kolejki.
To ta sama awaria co zakleszczony bieg z poprzedniego rozdziału, tylko widziana
od środka: tam bieg nie startował, tu nie umiał się skończyć.

**Diagnozę opóźniło zjadanie stderr.** `persist_seen_git` wołała gita przez
`capture_output=True` i zostawiała jedno zdanie „Nie udało się wypchnąć
seen.json przed wysyłką!" - bez ani słowa powodu. W logu nie było CZYM
odpowiedzieć na pytanie, czy to brak uprawnień, konflikt, czy sieć, a od tej
odpowiedzi zależy, co się robi dalej. Dziś ta funkcja dopisuje do logu to, co
git naprawdę powiedział.

**Limit czasu na gita, 90 s, w obu miejscach** (`persist_seen_git` i krok
`Zapisz seen.json`). Zawieszenie kosztuje wtedy 3 minuty zamiast całego
ogniwa, a bieg zdąży się skończyć i zwolnić grupę. Nieudany zapis maluje krok
na CZERWONO, bo niezapisany stan znaczy, że następne ogniwo zobaczy te same
rowery - albo powtórka na Telegramie, albo zmarnowany skan.

**Wniosek ogólny, trzeci już w tym pliku:** awaria potrafi siedzieć poza
logiką bota. Raz był to zakleszczony bieg w kolejce GitHuba, raz martwy token
Telegrama, a tu zawieszony `git pull`. Wspólne mają jedno - nic tego nie
mierzyło. **Przy każdym poleceniu zewnętrznym, które może stanąć, dawaj limit
czasu i zapisuj, co powiedziało.**

## Dwa błędy w samej naprawie zawieszonego gita (18.09.2026)

Obie wyszły przy sprawdzaniu, czy poprawka zadziała, i obie były ciche.

**1. Limit czasu łamał własny sufit.** Pierwsza wersja dawała gitowi 90 s
i trzy próby, czyli `3 x (90 + 90) + 2 x 5 = 550 s` przy `timeout-minutes: 8`,
to jest 480 s. Krok zostałby ucięty w połowie tak samo jak przedtem, tylko
później. Dziś 45 s i trzy próby: **280 s**, a reszta ogniwa (setup ~15 s,
tracker 56-67 s zmierzone na biegach 35360782054 i 35363854542, kanał 7-8 s)
to ~95 s. Pilnuje tego test, który LICZY oba progi z pliku - podniesienie
któregokolwiek bez policzenia reszty pada.

**2. Sprzątacz kolejki kasowałby ZDROWE biegi.** Zmierzone na biegu
35363854542: status CAŁEGO biegu to `queued`, a jego `check (1)` był wtedy
`in_progress` i normalnie skanował. Tak wygląda KAŻDY zdrowy bieg matrycy przy
`max-parallel: 1` - ogniwa 2-7 czekają na swoją kolej, więc bieg raportuje
`queued` przez całe swoje życie.

Sprzątacz z 17-18.09 czytał wyłącznie ten status i próg 10 minut. Dopóki
ogniwo wisiało 13 minut, trafiał w zakleszczone biegi. **Po naprawie gita
siedem ogniw po ~1,5 min to ~10,5 min, czyli dokładnie próg** - więc
zacząłby kasować zdrowe biegi w trakcie pracy. Dokładnie to, czego robić
nie wolno.

Dziś pyta o OGNIWA (`/jobs`) i zdejmuje wyłącznie bieg, w którym nie ruszyło
ANI JEDNO. Logika powłoki sprawdzona na sztucznych danych: bieg 20 min
z jednym pracującym ogniwem zostaje, bieg 25 min z zerem ruszonych leci,
bieg 2 min nietknięty.

**Wniosek: naprawa jednej awarii przestawia warunki drugiej.** Próg
sprzątacza był dobrany do świata, w którym ogniwo trwa 13 minut. Naprawa
gita ten świat zlikwidowała i cicho unieważniła próg. Przy każdej zmianie
tempa sprawdź progi, które od tego tempa zależą.

## Skan trwa 5 minut, nie 100 s - i to przewraca rachunek (18.09.2026)

Zmierzone na biegu 35364858243, krok „Uruchom tracker" po ogniwach:

| ogniwo | skan | zapis |
|---|---|---|
| 1 | **100 s** | 3 min 14 s, UDANY (próba 3) |
| 2 | **302 s** | nieudany po 3 próbach |
| 3 | **309 s** | nieudany |
| 4 | **313 s** | nieudany |
| 5 | **323 s** | nieudany |

Pięć minut na ogniwo 2-5 to nie szum ani nadrabianie zaległości - cztery
pomiary w przedziale 302-323 s. **Hipoteza „bot nadrabia i samo się skróci"
została obalona własnym pomiarem.** Ogniwo 1 jest krótkie, bo robi sam skan
półek; reszta dokłada zapytania kluczowe.

**Skutek dla budżetu:** ze sufitu `timeout-minutes: 8` (480 s) po skanie
zostaje ~150 s, nie ~380 s. Limit 45 s przy trzech próbach (280 s) tam NIE
wchodzi. Dziś 25 s przy dwóch próbach, czyli 125 s.

**Pierwsza wersja tego testu zakładała 95 s na resztę ogniwa**, bo policzyłem
ją na ogniwie 1 i wziąłem JEDEN pomiar za regułę. To ta sama pomyłka co
„żywe 224-228 kB" w starym docstringu dozorcy - liczba z jednej obserwacji
wygląda jak pomiar i nią nie jest.

**Zawieszony git nie umiera od SIGTERM.** Log ogniwa 2 kończy się sześcioma
wpisami `Terminate orphan process: git` - `timeout` posłał TERM, a proces to
zignorował i dopiero sprzątanie runnera go dobiło. Dlatego `timeout -k 5`,
które po grzecznościowej chwili wysyła KILL.

**Ile razy git staje: 5 z 6 prób na tym biegu.** A gdy przechodzi, robi
pobranie, rebase i push w **3 SEKUNDY**. To nie jest wolny push ani duże
pliki - to zawieszenie albo natychmiastowe przejście, bez stanów pośrednich.
Odpada przy tym hipoteza o spuchniętym repo: GitHub podaje rozmiar
**75 200 kB (73 MB)**, przy progach ostrzegawczych liczonych w gigabajtach.

**CO Z TEGO ZOSTAJE NIEROZWIĄZANE:** siedem ogniw po ~5 minut skanu to
~35-70 minut na bieg, a szturchnięcie przychodzi co 5 minut. Łańcuszek
w tej długości NIE MIEŚCI SIĘ w tempie wyzwalacza, więc kolejka stoi zatkana
niezależnie od tego, jak tani jest zapis. To jest ta sama arytmetyka co przy
wydłużeniu ogniwa tego samego dnia, tylko wynika ze skanu, a nie z pętli.
Decyzja o liczbie ogniw należy do właściciela - zmiana tempa bez jego zgody
już raz dziś położyła bota.

## Kilka ofert wysłanych PO CZTERDZIEŚCI RAZY (18.09.2026)

Właściciel: „jest poprawa bo cos przychodzi ale to jest zapetlone, kilka ofert
ktore wysyla juz 40 razy".

**Zdanie stało w kodzie od zawsze, a kod go nie pilnował.** Nad `save_seen`
w `tracker.main` od miesięcy jest komentarz:

> Zapisz bazę (plik + git) — DOPIERO POTEM wysyłka.
> Przerwany run = co najwyżej brak powiadomienia, nigdy duplikat.

A linijkę niżej stało `persist_seen_git()` i wykonanie szło dalej **niezależnie
od wyniku**. Funkcja nie oddawała nawet werdyktu - logowała błąd i wracała.
Dopóki push zawsze przechodził, nikt tego nie zauważył.

Gdy push zaczął się zawieszać (5 prób na 6 zmierzone tego dnia), wyszło to:

1. ogniwo zapisuje `seen.json` LOKALNIE i wysyła powiadomienia,
2. push pada, więc plik ginie razem z jednorazowym runnerem,
3. następne ogniwo robi `checkout main` i widzi stan SPRZED,
4. te same rowery lecą znowu - i tak przy każdym ogniwie, przy każdym biegu.

**Zapis lokalny bez pusha jest w tej konstrukcji ZEREM.** Runner jest
jednorazowy, a jedyną pamięcią bota jest `main`. To jest sedno i warto to
mieć przed oczami przy każdej zmianie dotyczącej stanu.

**Dziś `persist_seen_git` oddaje `True`/`False`, a `main` przy `False` NIE
WYSYŁA.** Rower nie jest stracony: zostaje nieznany dla `seen.json`, więc
pierwsze ogniwo z udanym pushem wyśle go DOKŁADNIE RAZ. Samo się domyka,
bez kolejki i bez stanu do pilnowania.

**Trzy przypadki liczą się jako „zapisane", żeby czujka nie uciszyła bota
bez powodu:** brak `GITHUB_ACTIONS` (bieg z ręki - nie ma czego pushować),
brak zmian do commitu (nie ma czego zgubić) i udany push.

Wybór ten sam co na kanale najlepszych: **zgubić jest tańsze niż zdublować.**
Powtórka wygląda jak awaria bota i zalewa telefon, brak powtórki jest
niewidoczny i mija sam.

**Nauka ogólna: komentarz opisujący zasadę to NIE jest zasada.** Ten stał
nad kodem, który go łamał, przez cały czas istnienia obu. Zasady pilnuje
test albo nic - dlatego spięcie (`main` pyta o werdykt PRZED pętlą wysyłki)
ma dziś własnego strażnika, osobnego od testu samej funkcji.

## Guzik oferty tylko na BestDealHawku + CO NAPRAWDĘ WAŻY (18.09.2026)

Właściciel: „wczesniej kazalem ci robic do kazdego powiadomienia
spersonalizowana oferte, wiec w takim ksztalcie to nie moze dzialac (...)
ewentualnie w bestdealhawku na moje zyczenie na przycisk".

Guzik zdjęty z powiadomień DealHawka, zostaje na BestDealHawku
(`klawiatura_pod_oferta`). Komenda `/oferta` i wklejony link działają dalej
na obu kanałach - zdjęty jest guzik, nie generator. Pilnują tego testy
sprawdzające OBA końce naraz.

**Przy okazji obalone przekonanie, że to generator ciąży.** Guzik to
~40 bajtów w JSON-ie do Telegrama (etykieta plus `of|<id>`), zero żądań,
zero odczytów plików - treść powstaje dopiero po stuknięciu.

**GDZIE NAPRAWDĘ SIEDZI CIĘŻAR, zmierzone tego dnia:**

| plik | rozmiar | zawartość |
|---|---|---|
| `seen.json` | **24,7 MB** | **282 573 wpisy**, z czego **279 766 to ODRZUTY** i tylko **2 807** to realnie wysłane oferty |
| `market.jsonl` | **27,5 MB** | 133 938 wierszy od 09.07 |

Czyli **99% `seen.json` to dziennik odrzutów**, nie stan powiadomień.
Najcięższe pola: `date` 3,2 MB, `powod` 1,1 MB, `cena_odrzut` 0,5 MB -
po 91 bajtów na wpis, ale wpisów jest ćwierć miliona.

**I sedno, o które łatwo się potknąć: GIT NIE ZAPISUJE RÓŻNIC.** Każdy
commit tworzy NOWY obiekt z CAŁYM plikiem. Zmiana jednej linijki w pliku
na 24,7 MB to nowy obiekt na 24,7 MB. Dlatego commit wyglądający w logu jak
„1 file changed, 1 insertion(+), 1 deletion(-)" niesie 52 MB, a bot robi
taki siedem razy na bieg, co pięć minut.

**Co z tym zrobić - propozycja, NIE wdrożona:** podzielić `seen.json` na
kawałki miesięczne (`seen-2026-09.json`). Zmienia się wtedy wyłącznie
bieżący kawałek (~2-3 MB), reszta leży nietknięta, a gwarancja „nic się nie
przycina" zostaje w mocy co do joty. Dotyka rdzenia dedupu, więc wymaga
zgody właściciela i własnego pomiaru.

## Dziennik rynku w kawałkach miesięcznych (18.09.2026)

Powód siedzi w gicie, nie w danych: **git NIE ZAPISUJE RÓŻNIC, tylko cały
plik od nowa.** Dopisanie jednego wiersza do `market.jsonl` (27,5 MB)
tworzyło nowy obiekt na 27,5 MB, a bot commitował siedem razy na bieg, co
pięć minut. Stąd brały się paczki, na których push się zawieszał.

Od tej zmiany `log_market` dopisuje do `market-RRRR-MM.jsonl`, a czytniki
chodzą przez `market_wiersze()`, które składa wszystkie kawałki po kolei.

**MIGRACJA NIE BYŁA POTRZEBNA i to jest najładniejsza część tej poprawki.**
Git wysyła wyłącznie to, co się zmieniło. Stary `market.jsonl` zostaje
nietknięty jako najstarszy kawałek, przestaje rosnąć - i git przestaje go
dotykać w ogóle. 27,5 MB znika z każdego commita bez przenoszenia
i bez kasowania jednego bajta. Kuszące „posprzątajmy przy okazji" byłoby tu
jednym commitem na 27,5 MB i oknem, w którym ogniwo na starym kodzie nie
widzi dziennika.

Cztery rzeczy, których nie ruszać:

- **Kolejność kawałków: legacy PIERWSZY, najświeższy OSTATNI.**
  `zbuduj_rozrzut` i `odzyskaj_silnik.py` liczą „ostatnie spotkanie wygrywa",
  więc przestawienie kolejności cofnęłoby ceny do stanu sprzed miesięcy.
- **`market-*.jsonl` MUSI być na liście `git add`** w `tracker.yml`
  i w `persist_seen_git`. Bez wzorca nowy miesiąc wypadłby z commita po cichu
  i dziennik urwałby się pierwszego dnia miesiąca - ta sama klasa awarii co
  `blackbox` poza `git add`.
- **Nazwa kawałka liczona WZGLĘDEM `MARKET_FILE`**, nie wpisana na sztywno.
  Testy i narzędzia podstawiają tam własną ścieżkę; sztywna nazwa robiłaby
  z każdej piaskownicy zapis do katalogu bota (ta sama pułapka co „ŚCIEŻKA
  NIGDY W DOMYŚLNYM ARGUMENCIE" z 09.09).
- **Każdy czytnik idzie przez kawałki**, także te spoza trackera
  (`dozorca_de`, `odzyskaj_silnik`, `najlepsze`, a `dojrzale` ma własną kopię,
  bo nie importuje trackera). Moduł patrzący na sam `market.jsonl` dostałby
  ułamek danych i NIE KRZYKNĄŁBY - wynik nadal wyglądałby wiarygodnie
  (reguła 7). Pilnuje tego test czytający te pliki, zamiast ufać, że
  pamiętałem o wszystkich.

**Co ZOSTAJE do zrobienia: `seen.json` (24,7 MB).** Ten zmienia się w każdym
biegu, więc samo zamrożenie nic nie da i podział musi być prawdziwy, ze
scalaniem przy odczycie. Idzie osobno i ostrożniej, bo to stan dedupu -
pomyłka znaczy albo lawinę powtórek, albo ciszę.

## Zawieszał się POBÓR, nie wysyłka (18.09.2026)

Cały dzień szukałem tej awarii po stronie pushu i wszystkie trzy poprawki
z tego dnia celowały w zdrowy koniec. Rozstrzygnął dopiero `GIT_TRACE`,
włączony wieczorem właśnie dlatego, że zgadywanie kosztowało już dobę.

Zmierzone na biegu 35391355748, siedem ogniw:

| ogniwo | skan | zapis | werdykt |
|---|---|---|---|
| 1 | 303 s | 60 s | padł |
| 2 | 48 s | 60 s | padł |
| 3 | 45 s | 60 s | padł |
| 4 | 47 s | **4 s** | **przeszedł** |
| 5 | 302 s | 60 s | padł |
| 6 | 316 s | 60 s | padł |

**Ogniwo 4 różni się od reszty jedną rzeczą: NIE MIAŁO CO POBRAĆ.** Jego
dziennik mówi wprost „Current branch main is up to date", pobór trwał 0,57 s,
a push 3,4 s i przeszedł. Na sześciu pozostałych pobór miał do przywiezienia
paczkę `--pack_header=2,134839`, czyli **całe repozytorium**: sam transfer szedł
6,8 s, po czym `git index-pack` nie kończył pracy do końca limitu.
**Do pushu wykonanie nie dochodziło ANI RAZU** - a to jego naprawiałem.

**Przyczyna: `actions/checkout` robi klon PŁYTKI, na jeden commit.** Gołe
`git pull --rebase` prosi wtedy o historię, której ten klon nie ma, więc
serwer dosyła ją całą. Odtworzone na replice runnera (klon `--depth=1`
czternaście commitów za main): stara droga nie skończyła w 90 s, nowa
przywiozła **25 obiektów w 3 s**, a rebase i push zajęły razem 1 s.

**`pull --rebase --depth=1` TO PUŁAPKA, nie skrót.** Sprawdzone na tej samej
replice i odrzucone: po płytkim poborze nie ma wspólnego przodka, więc git
ODWRACA ROLE i przekłada commity main-a na nasz - w pomiarze próbował
przełożyć cudze „Dziennik rynku w kawałkach miesięcznych (#13)". Dlatego
`rebase --onto FETCH_HEAD "$BAZA"`, czyli jawnie: NASZE commity na świeży
wierzchołek, z bazą w punkcie, z którego wyszedł checkout.

**BAZA czytana na początku KAŻDEJ próby, przed jej poborem.** Pobór przesuwa
`origin/main`, więc po udanym rebasie druga próba dostaje poprawny punkt
odniesienia, a po nieudanym poborze baza nie rusza się wcale.

**Konflikt na OGONIE pliku jest realny i nie znika z tą poprawką.** Zmierzone
na replikach: przy odstępie 2 commitów scalenie jest czyste (2 s), przy 6 i 14
wychodzi jeden konflikt na końcu `seen.json`, bo obie strony dopisują nowe
wpisy w to samo miejsce. Stara droga miała to samo - zmienia się wyłącznie to,
że pobór w ogóle się kończy. W produkcji odstęp to jedno ogniwo, czyli
przypadek czysty.

**SPROSTOWANIE do trzech rozdziałów z tego samego dnia.** `http.version
HTTP/1.1`, `http.lowSpeedLimit`/`lowSpeedTime` i `http.postBuffer` uderzały
w wysyłkę, która nigdy nie była chora. Zdanie „to jedyna hipoteza, która
tłumaczy WSZYSTKIE pomiary naraz" przy buforze wysyłki jest **nieprawdziwe**:
tłumaczy je dopiero płytki klon. Ustawienia zostają, bo nic nie kosztują
i żadnego z nich nie zmierzyłem jako szkodliwe, ale **nie wyjaśniają niczego**
i nie wolno się na nie powoływać przy następnej diagnozie.

**Budżet przeliczony po raz trzeci tego dnia:** trzy polecenia na próbę zamiast
dwóch, więc 15 s zamiast 25 s. Daje `2 x 3 x (15 + 5) + 5 = 125 s`, a przy
zmierzonym skanie 330 s mieści się w suficie 480 s. 15 s to czterokrotność
zdrowego zapisu - git tu albo przechodzi w 3-4 sekundy, albo wisi bez końca,
a na wiszącego dłuższy limit nie pomaga. Test LICZY oba progi z pliku i sam
zlicza polecenia w pętli; wcześniej miał wpisaną dwójkę, więc dołożenie
trzeciego polecenia przepuściłby po cichu.

**Zmierzony odzysk, doba po wdrożeniu (19.09.2026).** Oba okresy liczone
TĄ SAMĄ metodą (commity `update seen.json` sklejone w pary, patrz sprostowanie
metody wyżej):

| | przed awarią (17.09, 1,3 h) | po poprawce (18/19.09, 11 h) |
|---|---|---|
| skanów na godzinę | 51 | **48** |
| mediana odstępu | 64 s | **63 s** |
| p90 odstępu | 111 s | **115 s** |
| najdłuższa przerwa | 140 s | **297 s** |
| przerw dłuższych niż 5 min | 0 | **0** |

Czyli **96% tempa sprzed awarii przy praktycznie tej samej medianie**.
Biegi: 19 z 20 kolejnych `success`, ani jednego `cancelled` i ani jednego
`failure` - a czerwony bieg znaczy dziś zgubioną wiadomość, więc zero
czerwonych to zero strat na Telegramie.

**Najdłuższa przerwa NIE jest porównywalna i nie udawaj, że jest.** Okno
sprzed awarii ma 1,3 godziny, a po poprawce 11 - dłuższy pomiar z definicji
złapie gorszy ogon. Zdrowej doby sprzed awarii w danych nie ma, więc to
pytanie zostaje otwarte.

**Nauka ogólna, czwarta już w tym pliku o awarii poza logiką bota:** miałem
hipotezę spisaną w repo jako „jedyna tłumacząca wszystkie pomiary" i była
pewna siebie, i była błędna. Obaliła ją nie kolejna hipoteza, tylko jedna
linijka dziennika z liczbą obiektów w paczce. **Włącz dziennik, ZANIM
wymyślisz trzecie wyjaśnienie** - reguła „zapisuj, co powiedziało polecenie
zewnętrzne" stoi w tym pliku od rana tego samego dnia i to ona rozwiązała
sprawę, gdy wreszcie jej posłuchałem.

## Podział `seen.json` - KROK PIERWSZY, sam odczyt (19.09.2026)

Powód ten sam co przy dzienniku rynku: **git nie zapisuje różnic**, tylko cały
plik od nowa. `seen.json` ma 24,9 MB i zmienia się w KAŻDYM biegu, więc jest
dziś jedynym dużym plikiem, który git przepisuje co pięć minut.

Rozkład wieku, zmierzony na 285 069 wpisach:

| miesiąc | wpisów | udział |
|---|---|---|
| 2026-09 | 175 862 | **61,7%** |
| 2026-08 | 100 975 | 35,4% |
| 2026-07 | 8 203 | 2,9% |
| 2026-06 | 29 | 0,0% |

Zamrożenie starszych miesięcy zdejmuje z commita ~38%, a pierwszego dnia
miesiąca prawie wszystko. **Uwaga na rachunek:** `prune_seen` i tak wycina
wpisy starsze niż 90 dni, więc plik nie rośnie bez końca - przy tempie
~5 800 wpisów dziennie stanie na ~520 tys. wpisów, czyli ~45 MB. Podział
utrzymuje POJEDYNCZY commit przy ~15 MB zamiast ~45.

**DWA KROKI, BO TO STAN DEDUPU.** Pomyłka tutaj znaczy albo lawinę powtórek
na telefonie właściciela, albo ciszę - i jedno, i drugie wychodzi na jaw
dopiero u niego. Krok pierwszy rusza WYŁĄCZNIE odczyt i jest z założenia
niewidoczny na produkcji; zapis idzie osobno, po dobie obserwacji.

**Gwarancja bezpieczeństwa kroku pierwszego jest sprawdzalna, nie deklarowana:**
dopóki nie istnieje ani jeden kawałek, `seen_kawalki()` oddaje jeden plik
i `load_seen` zachowuje się co do joty tak jak przedtem. Zmierzone na PRAWDZIWYM
pliku produkcyjnym (285 069 wpisów): wynik identyczny, zero różnic, zero
zgubionych, zero dorobionych. Pilnuje tego osobny test.

Trzy rzeczy, których nie ruszać:

- **PÓŹNIEJSZY KAWAŁEK WYGRYWA.** Odwrotnie niż przy dzienniku rynku tylko
  z pozoru: tam „ostatnie spotkanie wygrywa" dotyczy ceny, tu tego samego
  ogłoszenia dotkniętego ponownie (przecena, dopisany rozmiar, `score`).
  Zła kolejność cofa cenę do stanu sprzed tygodni.
- **BŁĘDU ODCZYTU NIE POŁYKAMY** (reguła 7). Uszkodzony plik zamieniony po
  cichu na pusty stan znaczy, że bot uzna CAŁY rynek za nowy i wyśle kilkaset
  powiadomień naraz. Wyjątek wywraca bieg, krok świeci na czerwono i nic nie
  wychodzi - to jest tańszy koniec tej historii.
- **`seen-*.json` na liście `git add` JUŻ TERAZ**, zanim zapis na kawałki
  przejdzie. Dopisane po fakcie znaczyłoby, że pierwszy kawałek wypada
  z commita po cichu, a stan ginie razem z jednorazowym runnerem - ta sama
  klasa awarii co `blackbox` i `market-*` poza `git add`.

**Narzędzia przepisujące CAŁY plik STAJĄ przy kawałkach** (`odblokuj.py`,
`odzyskaj_silnik.py`). Czytają złączony widok, a zapisują jeden plik, więc
przy podzielonym stanie zlałyby kawałki w jeden i zdublowały wpisy. Stanąć
głośno z kodem 1 jest tańsze niż po cichu zepsuć dedup. Sprawdzone
uruchomieniem w piaskownicy, nie samym czytaniem kodu: bez kawałka oba chodzą
i kończą zerem, z kawałkiem oba stają i zostawiają pliki nietknięte.

**ZNALEZIONE PRZY OKAZJI: `odblokuj.py` został POMINIĘTY przy podziale
dziennika rynku 18.09.** Do 19.09 czytał sam `market.jsonl`, czyli zamrożony
najstarszy kawałek - a od dnia podziału nic nowego już tam nie przybywa.
Wynik wyglądał wiarygodnie i opisywał rynek sprzed tygodnia, czyli dokładnie
ta cicha awaria, przed którą ostrzega reguła 7. Strażnik z 18.09 wymieniał
`dozorca_de`, `odzyskaj_silnik`, `najlepsze` i `dojrzale`, a tego pliku nie -
**więc lista w teście musi wymieniać KAŻDY moduł z nazwy.** Zaufanie, że
pamiętałem o wszystkich, zawiodło po jednym dniu.

**ŚCIEŻKA W DOMYŚLNYM ARGUMENCIE - TRZECI RAZ W TYM REPO.** Pierwsza wersja
`_wczytaj_seen` w `rozmiary.py` i `oferta.py` pytała o kawałki
`tracker.SEEN_FILE`, a te moduły mają WŁASNĄ stałą `SEEN`, którą testy
podmieniają. Cztery testy padły od razu i dobrze - inaczej komenda `/rozmiar`
czytałaby cudzy plik. Dlatego `seen_kawalki(plik=None)` rozwiązuje ścieżkę
W WYWOŁANIU, a nie w definicji, i dostaje ją od wołającego.

**SĄSIEDZI Z PODŁOGĄ W NAZWIE TO CUDZY STAN.** W repo leżą `seen_olx.json`,
`seen_otomoto.json` i `seen_wystawcy.json` - stan INNYCH botów. Wzorzec kawałka
żąda MYŚLNIKA i kształtu `RRRR-MM` z rozmysłu: sprawdzone, że poluzowany do
`seen*.json` albo `seen_*.json` wciąga tamte pliki do dedupu rowerowego, czyli
uciszyłby losowe ogłoszenia albo wysłał je drugi raz - i nic by nie krzyknęło.
Tego nie było w pierwszej wersji testów i dopisałem to dopiero po zobaczeniu
tych plików obok na produkcji.

**Co zostaje do zrobienia - KROK DRUGI:** przełączenie zapisu na
`seen-RRRR-MM.json`. Wtedy `save_seen` musi zapisywać do bieżącego kawałka
WYŁĄCZNIE wpisy różne od tego, co trzymają starsze kawałki, inaczej pierwszy
zapis przepisze cały stan do nowego pliku i nic nie oszczędzi. Oba narzędzia
wyżej trzeba wtedy przerobić - dlatego dziś głośno stają.

## Wersja wyposażenia w wycenie - SPRAWDZONE I NIE WDROŻONE (19.09.2026)

Właściciel zapytał, czy bot znajduje Cube Stereo w wersjach Race 750, SLX 750,
TM 750 i Actionteam 750. Znajduje i wysyła - zmierzone na 135 729 ogłoszeniach:

| wersja | rowerów | wysłanych |
|---|---|---|
| Race 750 | 493 | 131 |
| SLX 750 | 384 | 77 |
| TM 750 | 169 | 36 |
| Actionteam 750 | 47 | 4 |

Odrzuty są słuszne. Wszystkie 69 sztuk „nie_fully" przy Race 750 to Cube
**Reaction**, czyli hardtail, a nie Stereo - ani jednej pomyłki.

**Hardtailowego TM nie ma ANI JEDNEGO** (443 ogłoszenia Cube+TM to Stereo albo
AMS Hybrid, oba fully). **Actionteam na hardtailu owszem istnieje, ale to
rowery DZIECIĘCE**: 7 ogłoszeń „Cube Acid 200 Disc Actionteam, Kinderfahrrad
20 Zoll". Żadne nie przechodzi `is_fully` ani `is_electric`. Wniosek dla kodu:
wersję wolno czytać WYŁĄCZNIE wewnątrz gałęzi „stereo hybrid", bo puszczona po
całym tytule wpuściłaby rower dziecięcy do wyceny rowerów za 12 000 zł.

### Czemu pomysł wygląda dobrze

`wariant_modelu` czyta sam rozmiar modelu (120/140/160), więc Actionteam, TM,
SLX, Race i SL dzielą JEDNĄ pulę porównawczą. Na stronie niemieckiej, gdzie
dane są liczone po numerze ogłoszenia i jest ich dużo, różnica jest wyraźna:
Stereo Hybrid 140 Actionteam ma medianę **3 500 €** (52 rowery) wobec Race
**2 350 €** (401 rowerów), czyli 49% rozrzutu wewnątrz jednego rozmiaru.

### Czemu MIMO TO nie wdrożono

**Bo policzyłem ogłoszenia zamiast rowerów i to zmieniło wynik** (reguła 5,
złamana przeze mnie w pierwszym podejściu tego dnia). `rynek_pl.jsonl` jest
DZIENNIKIEM: ta sama oferta wraca w nim przy każdym skanie. Zmierzone:
**8 567 wierszy to 237 unikalnych rowerów, czyli 97% powtórek.** Pierwsze
liczby, którymi uzasadniałem zmianę („566 ofert Actionteamu"), były wierszami.

Po odduplikowaniu pula POLSKA, czyli ta, z której liczy się zysk, wygląda tak:

| | rowerów | premia nad medianą rozmiaru |
|---|---|---|
| Stereo 140 Actionteam | 17 | **+34,7%** |
| Stereo 140 TM | 16 | +14,5% |
| Stereo 140 SL | 14 | -7,6% |
| Stereo 160 Actionteam | 12 | **+0,4%** |
| Stereo 160 SL | 13 | -10,8% |

**I to Stereo 160 przesądziło sprawę.** Wdrożona na próbę zmiana została
zmierzona na 1 082 prawdziwych wysłanych ofertach: 379 wycen się zmieniło,
mediana -1,0%, p10 -10,2%, p90 +10,7%. Ale rozbicie na wersje pokazało, że
z dziewięciu Actionteamów SZEŚĆ to Stereo 160 i wszystkie sześć dostały
**-11,1%** - przy premii wersji wynoszącej **+0,4%**. Czyli zawężenie do puli
11 rowerów ruszyło wycenę o 11 punktów, nie mając ku temu ŻADNEGO sygnału
w danych. To jest dokładnie pułapka opisana przy ONE22 z 17.09, tylko że tam
progiem było 5 ofert, a tu nie pomogło nawet 11. Stereo 140 zachowało się
zgodnie z zamiarem (+44,8%), bo tam premia jest prawdziwa.

**Próg dobrany tak, żeby przepuścić 140 i zatrzymać 160, byłby progiem wziętym
z głowy** - a takich ten plik zabrania. Dlatego zmiana została cofnięta w
całości, a nie okrojona.

**PUŁAPKA PRZY SPRAWDZANIU TAKICH RZECZY: nie buduj „prawdy odniesienia"
z tych samych pul, które nią testujesz.** Zrobiłem sweep progu puli i wyszło
0,0 punktu rozjazdu przy każdym progu - bo odniesienie było sumą tych samych
zapytań. Liczba, która wychodzi idealnie, jest pierwszym podejrzanym.

### Co z tego zostaje na później

- **Kaskada jest dobrym pomysłem niezależnie od reszty:** wersja → sam rozmiar
  modelu → pula szeroka. Bez niej 55 rowerów na 1 082 traciło wąską pulę
  i spadało od razu do porównania ze wszystkimi elektrykami naraz. Z nią
  70 rowerów lądowało na piętrze „rozmiar" zamiast na dnie.
- **`oferty_z_rynku` odduplikowuje po adresie** (`po_url[r["url"]] = r`), więc
  `OLX_MIN_SAMPLES` liczy rowery, nie ogłoszenia. Tej dziury tam NIE MA
  i nie trzeba jej sprawdzać drugi raz.
- **Blokadą jest cienkość polskiej puli, nie kod.** 237 unikalnych Stereo
  w całym dzienniku, po 9-29 na parę (rozmiar, wersja). Do tego wraca reguła
  nadrzędna: nie stroimy rzeczoznawcy, dopóki nie ma danych o realnych
  sprzedażach, a tych jest nadal **0**.

## Lista życzeń - model, którego nie wolno przegapić (19.09.2026)

Właściciel: „chce koniecznie kupic wersje 160 tm, zadbaj o to abym napewno nie
przegapil zadnego nowego ogloszenia".

**Zmierzone, zanim cokolwiek powstało:** na 90 rowerów Cube Stereo Hybrid 160 TM
w dzienniku bot wysłał **22**, a **29 zdławił bramkami biznesowymi** - 20 ceną
i 9 przebiegiem. Wśród uciszonych: rocznik 2023 z 49 km za 3 600 € i 2024
z 1 250 km za 4 050 €. Reszta (38) to nieme wpisy sprzed 01.09, bez powodu.

`obserwowane.json` wymienia modele, które mają iść na Telegram ZAWSZE.
Plik, nie kod - to lista życzeń właściciela i ma ją zmieniać sam, tak jak
`silniki_bosch.json` i `topowe_modele.json`.

**TO JEST ŚWIADOME ODWRÓCENIE DOMYŚLNEJ ZASADY REPO.** Wszędzie indziej
„zgubić jest tańsze niż zdublować", bo powtórka wygląda jak awaria bota.
Tutaj właściciel powiedział wprost, że chce ten rower i ma nie przegapić ani
jednego ogłoszenia - więc rachunek jest odwrotny i trzeba to wiedzieć, zanim
ktoś „naprawi" tę niespójność.

**Bramki OTWARTE - wyłącznie te, które ucinają ofertę SŁABĄ BIZNESOWO:**
budżet (obie, z listy i ze strony ogłoszenia), przebieg, nisza, mała bateria,
dedup re-listingu. Dedup jest na tej liście z rozmysłu: pomyłka dedupu znaczy
tu rower przegapiony, a repo ma udokumentowane, że dedup bywa w błędzie
(rower 3492497177 z 01.09).

**Bramki ZAMKNIĘTE i to jest ważniejsze od reszty:** silnik (twarde
ograniczenie „tylko Bosch" stoi dalej), śmieć (ogłoszenie o samej ramie to nie
rower), fully i elektryk (bramki na RUCH, przed pobraniem strony - otwarcie ich
potroiłoby ruch przy zmierzonym dławieniu Kleinanzeigen). Pilnuje tego test
wymieniający każdą z nich z nazwy.

**Wpis wymaga WSZYSTKICH fragmentów naraz, w tym modelu.** Sama nazwa wersji
trafia w cudze tytuły: „Cube Reaction Hybrid 160 TM" ma trzy z czterech
fragmentów i odpada dopiero na „stereo".

**LICZBA 160 W TYTULE BYWA ZAKRESEM WZROSTU, NIE MODELEM** - i to wyszło
dopiero przy drugim, uczciwym sprawdzianie. „Cube STEREO HYBRID **140** HPC
TM 750 2023 - S - **160-170cm**" to Stereo 140, a wzorzec łapał je na wzroście
rowerzysty. Zmierzone: **3 takie na 90 trafień**. Dlatego 160 nie może stać
przed myślnikiem i cyfrą. To ta sama pułapka co „Liczba po nazwie bywa
BATERIĄ" przy `topowe_modele.json` i „- L - 175-185cm" przy `/rozmiar`.

**PIERWSZY SPRAWDZIAN BYŁ BEZWARTOŚCIOWY i to jest tu najważniejsza nauka.**
Napisałem „90 trafień, zero fałszywych", bo porównałem wzorzec z WŁASNĄ KOPIĄ
tego samego wzorca - obie robiły ten sam błąd, więc zgadzały się co do joty.
Rozstrzygnął dopiero czytnik z INNEJ drogi (`wariant_modelu`, który żąda
liczby tuż po „stereo hybrid"). To ta sama pomyłka metodologiczna, którą
zapisałem godzinę wcześniej w rozdziale o wersjach wyposażenia - i popełniłem
ją drugi raz tego samego dnia. **Sprawdzaj wzorzec czymś, co nie pochodzi
od niego.**

Po poprawce: **87 trafień, zero fałszywych**, w tym dwa tytuły, których
`wariant_modelu` NIE umie rozczytać („Cube, Stereo Hybrid, 160 TM" i „Cube
Stereo Hybrid HPC TM 160") - obserwacja jest tu więc szersza niż wycena,
i tak ma być. Wszystkie 29 wcześniej zdławionych wpadłoby w obserwację.

**SPRZEDAWCY SKLEJAJĄ CZŁONY, a granica słowa tego NIE łapie.** Właściciel
przysłał link i pytanie „dlaczego to nie przyszło": ogłoszenie 3517059558,
**„Cube Stereo Hybrid 160tm. 750wh Performance Cx."** za 2 200 € - zdławione
przebiegiem 6 600 km. Między „0" a „t" w „160tm" NIE MA granicy słowa, bo oba
są znakami słowa, więc `\b160\b` nie trafia. To ta sama pułapka co „Thron²"
przy filtrze silnika, gdzie `\bthron\b` gubiło 72 ogłoszenia Focusa - i
weszedłem w nią mimo że stoi w tym pliku opisana.

Zmierzone czytnikiem zbudowanym INNĄ drogą: **2 prawdziwe zgubienia na 92**
(„160tm" i „TM750"), oba zdławione przebiegiem. Dziś granice są niesymetryczne
z rozmysłu: **160 nie może sąsiadować z CYFRĄ** (żeby nie łapać „1600"),
a **„tm" nie może sąsiadować z LITERĄ** (żeby nie łapać marki **KTM**) - ale
sklejenie cyfry z literą jest dozwolone. Po poprawce: **89 trafień, zero
fałszywych**, 86 potwierdzonych niezależnym czytnikiem.

**TO BYŁ TRZECI ZŁY WZORZEC W JEDNEJ DOBIE** na tej samej liście: najpierw
zakres wzrostu, potem sklejenie. Wniosek na przyszłość: przy wzorcu na tytuły
z ogłoszeń **wypisz sobie najpierw, jak SPRZEDAWCA może to napisać** - z
kropką, przecinkiem, sklejone, w jednym słowie z liczbą - i dopiero potem
pisz wyrażenie. Tytuł z Kleinanzeigen nie jest polem formularza.

**Koszt policzony przed wdrożeniem: +1,0 wiadomości dziennie** licząc 69 dni,
**+1,9 w ostatnich 14 dniach**, najgorszy dzień 6.

**POWIADOMIENIE MUSI SIĘ WYTŁUMACZYĆ** (reguła 6). Rower za 4 050 € w kanale
obiecującym okazje do 3 000 bez słowa wyjaśnienia wygląda jak usterka bota,
więc nagłówek mówi wprost, że to model z listy, i wymienia dokładnie te bramki,
które ta oferta naprawdę by oblała. Zdrowa oferta obserwowanego modelu nie ma
się czym tłumaczyć i nic dodatkowego nie dostaje.

> **ZMIENIONE TEGO SAMEGO DNIA, patrz „Wpuszczamy szeroko, oznaczamy wąsko":**
> gwiazdka z nazwą modelu przeniosła się stąd do osobnej, drugiej wiadomości.
> W zwykłym powiadomieniu zostało samo wyjaśnienie obejścia bramek - reszta
> tego akapitu obowiązuje bez zmian.

**Brak pliku to AWARIA, nie cisza** (reguła 7). Bez niego bot wraca do zwykłych
bramek i po cichu przestaje dowozić rower, o który właściciel prosił imiennie -
a on widziałby tylko brak ofert i uznał, że takich nie ma. Ta sama decyzja co
przy `topowe_modele.json`.

**DZIAŁA TYLKO W PRZÓD.** Wpis w `seen.json` jest terminalny, więc 29 rowerów
zdławionych wcześniej NIE wróci samo z siebie - dokładnie ta sama dziura co
przy `odblokuj.py` z 01.09. Do odzyskania tych, które jeszcze żyją, służy
`odblokuj.py --wznow` z `--od`, i trzeba pamiętać, że samo zdjęcie wpisu nie
daje ogłoszeniu drogi: półka pokazuje świeże, a zapytanie kluczowe wraca do
tej samej frazy dopiero po ~110 minutach (patrz „Sortowanie po dacie" -
zdanie o sortowaniu po trafności było nieprawdą).

## Wpuszczamy szeroko, oznaczamy wąsko (19.09.2026)

Pierwszy dzień listy życzeń. Właściciel: „niech przychodzi ale nie oznaczasz
obserwowane i chce obserwowane miec na dealhawku drugi raz dodane, a na
zwyklym dealhawku leci wszystko (...) jesli przyjdzie nowe ogloszenie niech
leci w dealhawku normalnie bo jego celem jest szybkosc".

**To są DWIE różne rzeczy i pomylenie ich kosztuje w obie strony.** Wpuszczenie
(pole `wymaga`) decyduje, czy rower w ogóle przejdzie bramki - i ma być szerokie,
bo przegapiony rower jest nie do odzyskania. Oznaczenie (pole `oznacz`) decyduje,
czy dostanie DRUGĄ wiadomość z gwiazdką - i ma być wąskie, bo gwiazdka przy
każdym ogłoszeniu nie znaczy nic.

Gwiazdka zniknęła więc ze zwykłego powiadomienia. Zostało tam jedno zdanie
i nie jest oznaczeniem, tylko odpowiedzią na „czemu ja to widzę": rower za
4 050 € w kanale obiecującym okazje do 3 000 bez słowa wyjaśnienia wygląda
jak usterka bota. Zdrowa oferta obserwowanego modelu nie dostaje ani znaku
więcej niż każda inna.

**REGUŁA W JEGO SŁOWACH ODPALAŁA ZERO RAZY.** „L size i w miare niski przebieg
np do 2k km" wzięte dosłownie spełnia **0 z 22** wysłanych sztuk tego modelu
z 43 dni. Nie dlatego, że takich rowerów nie ma - dlatego, że tych dwóch pól
nie znamy naraz:

| | odczytane | ile |
|---|---|---|
| rama | 41% | L 3, M 4, S 2, nieznana 13 |
| przebieg | 68% | do 2 000 km 10, powyżej 5, nieznany 7 |

Wszystkie trzy rowery z ramą L mają „brak danych" przy przebiegu. To ta sama
klasa wpadki co waga 3 na kanale najlepszych: **reguła, która nie odpala, to
nie reguła.**

**OBA NIEZNANE POLA PRZEPUSZCZAMY.** Ramę - decyzją właściciela z `/rozmiar`:
„lepiej kilka wiecej przegladnac niz ominac". Przebieg - bo poprosił o to
wprost tego samego dnia, zobaczywszy pomiar: **„to niech przychodza tez te
nieznane"**.

Zmierzone na 34 wysłanych sztukach tego modelu z 43 dni:

| wariant | odpala |
|---|---|
| L i przebieg do 2 000 km, dosłownie | **0** |
| L albo nieznana, przebieg do 2 000 km | 9 |
| L albo nieznana, przebieg też nieznany | **15** (wdrożone) |

Koszt: **0,35 oznaczenia dziennie** licząc 43 dni, 8 w ostatnich 14.

**ARGUMENT PRZECIW NIEZNANEMU PRZEBIEGOWI JEST NADAL PRAWDZIWY** i trzeba go
znać, zanim ktoś tę wartość przestawi z powrotem „dla spójności": reguła
z pierwszego dnia kanału najlepszych mówi, że „niska cena przy nieznanym stanie
NIE jest dowodem okazji", a rower bez odczytu może mieć 15 000 km. **Zmienił
się właściciel decyzji, nie pomiar** - a to jest jego lista życzeń.

Rachunek za poszerzenie jest konkretny: z 15 oznaczeń tylko **2 mają oba pola
odczytane**, a **3 nie mają ANI JEDNEGO**. Dlatego ptaszek stoi wyłącznie przy
polu odczytanym, znak zapytania przy pustym, a oznaczenie bez żadnego odczytu
mówi wprost „nic z tego nie jest potwierdzone, wchodzi, bo tak masz ustawioną
listę". Gwiazdka bez tego zdania sugerowałaby, że cokolwiek sprawdziliśmy.

**OZNACZENIE TWIERDZI COŚ O ROWERZE, więc mówi, czego NIE zmierzyło**
(reguła 6). Wiadomość ze znakiem zapytania jest tu wyglądem DOMYŚLNYM, nie
przypadkiem brzegowym: 13 z 15 oznaczeń ma co najmniej jedno pole puste. Gdyby
oznaczenie pisało „rama L" bez odczytu, właściciel jechałby po rower
w rozmiarze M. Podpowiedź jest osobna na pole, bo rady są różne - przy ramie
chodzi o to, że na rowerze nie da się jeździć, przy przebiegu, że może być
zajeżdżony.

**Warunki siedzą w `obserwowane.json`, nie w kodzie** - tak samo jak
`silniki_bosch.json` i `topowe_modele.json`. Właściciel zmienia próg przebiegu
sam, bez dotykania trackera. Pilnuje tego test, który podmienia próg we wpisie
i sprawdza, czy werdykt naprawdę się zmienia; inaczej plik byłby ozdobą.

**Drugi wpis w kolejce pod TYM SAMYM kluczem.** `pending_msgs.sort` sortuje po
samym kluczu, a sort Pythona jest stabilny, więc oznaczenie przychodzi zaraz
za swoim oryginałem. Dołożenie tam drugiego pola albo `reverse` rozerwałoby parę
i oznaczenie wylądowałoby na drugim końcu paczki - stąd test na samą linijkę
sortowania.

**Zdjęcia druga wiadomość nie dostaje.** Ten sam rower z tą samą fotką drugi raz
nie wnosi informacji, a galeria poszła już wyżej.

**To jedyne miejsce w repo, gdzie bot ŚWIADOMIE dubluje powiadomienie.**
Wszędzie indziej obowiązuje „zgubić jest tańsze niż zdublować", bo powtórka
wygląda jak awaria. Kto będzie to kiedyś „naprawiał" jako niespójność, niech
najpierw przeczyta ten akapit.

## Sortowanie po dacie: dla Otomoto TAK, dla Kleinanzeigen NIE MA CZEGO NAPRAWIAĆ (19.09.2026)

Hipoteza brzmiała dobrze i była w połowie fałszywa. Zapisana, bo kusi
ponownie i bo obaliła ją jedna tabelka, a nie kolejne rozumowanie.

**Co miało być naprawione.** Zmierzone na 8 dniach: ogłoszenie z półki bot
łapie po medianie **2 minut**, a złapane wyłącznie zapytaniem kluczowym po
**93** (p90 437). Wyglądało to na skutek sortowania po trafności, bo tak stało
w tym pliku od 02.09. Sortowanie po dacie kosztuje zero żądań - to człon
adresu - więc wyglądało na darmową poprawkę.

**Pomiar z runnera** (`sprawdz_sortowanie.py`, raport na gałęzi
`diagnoza/raporty`; z kontenera sesji Kleinanzeigen jest niedostępne, proxy
odmawia 403). Trzy frazy, stary i nowy adres pobrane w odstępie ośmiu sekund,
porównane ROZKŁADEM WIEKU, a nie tym, czy strona się pobrała:

| fraza | bez sortowania | z sortowaniem |
|---|---|---|
| cube stereo hybrid | 19 / 123 / 203 min | 19 / 123 / 203 min |
| trek rail | 124 / 18 286 / 49 966 | 125 / 18 287 / 49 967 |
| emtb | 2 / 143 / 216 | 2 / 143 / 216 |

(najmłodsze / mediana / najstarsze; różnica jednej minuty przy „trek rail" to
te osiem sekund między pobraniami)

**Wynik podwójny i oba człony są ważne.** Parametr `sortierung:SORTIERUNG_DATUM`
jest na tej ścieżce IGNOROWANY - rozkłady są identyczne co do minuty. Ale
przede wszystkim **NIE BYŁO CO NAPRAWIAĆ**: wiek pierwszych pozycji ROŚNIE
(19, 22, 32 dla Cube'a; 124, 217, 238 dla Treka), czyli Kleinanzeigen podaje
wyniki wyszukiwania już od najnowszych. Zdanie o sortowaniu po trafności
stało w tym pliku od 02.09 i jest nieprawdą.

**GDYBY TEN TEST PYTAŁ „CZY STRONA SIĘ POBRAŁA", WDROŻYŁBYM TO.** Oba adresy
oddały status 200, po 30-32 kafelki, z datami. Zignorowany parametr wygląda
dokładnie jak działający. To ta sama klasa co „alarm działał, kompensacja nie"
z 01.09: sprawdzać trzeba SKUTEK, nie to, czy mechanizm się odpalił.

**Prawdziwa przyczyna 93 minut to ROTACJA, nie sortowanie.**
`KLUCZOWE_CO_MIN = 5` przepuszcza zapytania kluczowe najwyżej co 5 minut,
a `KLUCZOWE_NA_SKAN = 1` daje wtedy jedną frazę. Przy 22 frazach ta sama
wraca co **110 minut**, a zmierzona mediana to 93. Strona 1 frazy modelowej
sięga ~3 h wstecz, więc ogłoszenia NIE wypadają z niej między cyklami - bot
po prostu zagląda rzadziej, niż sam sobie szkodzi. To decyzja o RUCHU,
czyli należy do właściciela, i ma za sobą własny pomiar (zapytania kluczowe
raz już położyły kanał przy 5 żądaniach na skan). **Nie ruszane.**

**Trzecia półka na ogłoszenia bez ustawionego typu: ODRZUCONA po pomiarze.**
Strona 1 pełnej kategorii rowerów (`/s-fahrraeder/c217`) obejmuje
**JEDNĄ MINUTĘ** rynku - wiek kafelków 2-3 min, rozpiętość 1 min. Strona 2 tak
samo. Przy skanie co ~64 s i marginesie `FEED_MARGINES_MIN = 3` domknięcie
luki wymagałoby 4-5 stron na skan, czyli potrojenia ruchu przy zmierzonym
dławieniu per adres IP. Lekarstwo byłoby gorsze od choroby, a dziura, którą
miało załatać, to 0,65% wolumenu.

**Dla Otomoto ten sam parametr DZIAŁA i został wdrożony.** Wiek pierwszych
pięciu ofert w minutach:

```
bez sortowania:  14570, 12734,  7063, 22757, 21766   <- kolejność losowa
z sortowaniem:     359,  1099,  1253,  1253,  1404   <- rosnąco
```

Mediana strony spadła z 18 645 na 15 444 minut, najmłodsza oferta z 475 na
359. Oba adresy oddały po 32 edges przez ten sam `_fetch_page`, więc parametr
nie psuje parsowania. Robi to `adres_po_dacie` - **w jednym miejscu, nie przy
każdym wpisie w `SEARCHES`**, żeby wpis dodany za pół roku dostał sortowanie
bez pamiętania o nim. Pilnują tego dwa testy: jeden na wszystkie wpisy, drugi
na to, czy pętla pobierania naprawdę tę funkcję woła.

Przy okazji nieaktualny stał się komentarz przy alarmie o pełnej ostatniej
stronie: ucięte są teraz NAJSTARSZE oferty, nie najnowsze. Alarm zostaje, bo
pula do porównania cen jest wtedy i tak niepełna.

## Symlink w piaskownicy unieważnił sprawdzian reguły 2 (19.09.2026)

Sprawdzian „nowy test pada na starym kodzie" robiłem w katalogu pełnym
dowiązań do prawdziwego repo, podmieniając tylko moduł na wersję z `git show`.
Przy teście Otomoto wyszło **zero padnięć** - i to była wada sprawdzianu,
nie dowód, że test jest pieczątką.

**Powód:** `test_otomoto.py` w piaskownicy był DOWIĄZANIEM. Python ustawia
`sys.path[0]` na katalog pliku skryptu po rozwinięciu dowiązania, czyli na
prawdziwe repo - więc `import otomoto_tracker` wciągnął NOWY moduł, nie ten
podstawiony.

**Poprawka: plik testu kopiuj NAPRAWDĘ, nie dowiązuj.** Dane wolno dowiązać,
skrypt nie. Po poprawce test Otomoto pada na starym kodzie zgodnie z regułą 2.

Przy okazji przeliczone oba dzisiejsze sprawdziany, na które zdążyłem się już
powołać: **7 padnięć na kodzie sprzed listy życzeń i 6 sprzed poszerzenia
o nieznany przebieg**. Tamte były prawdziwe - zepsuł się dopiero ten trzeci.

## Przycisk oferty: czego Telegram NIE UDŹWIGNIE (19.09.2026)

Właściciel: „to ma byc przycisk do skopiowania, wiec ja klikam i mam
wiadomosc skopiowana w schowku".

**Dosłownie tak się nie da i to jest zmierzone.** `copy_text` w API Telegrama
przyjmuje **256 znaków**, a niemiecki tekst oferty ma **641-688** - policzone na
17 prawdziwych ofertach, które poszły na kanał od 18.09. Zero się mieści.
Ucięty traciłby zdanie o braku dogadywania na miejscu, czyli to, po co ta
wiadomość w ogóle jest (patrz rozdział o `/oferta`).

Najbliższa rzecz, jaka jest możliwa, to **jedno stuknięcie w pasek bloku**:
`<pre><code class="language-...">` klient Telegrama rysuje z nagłówkiem
i przyciskiem kopiowania, a gołe `<pre>` wymaga przytrzymania i wybrania
„kopiuj" z menu. Ta sama treść, o dwa gesty mniej. Przekład na polski zostaje
POZA blokiem, bo w środku jedno stuknięcie wysłałoby Niemcowi polski tekst.

**BŁĄD SKŁADNI HTML PRZESTAŁ ZJADAĆ WIADOMOŚĆ.** Przy okazji ruszania
znaczników wyszło, że `send_telegram` ponawiał TO SAMO trzy razy, gdy Telegram
odpowiadał 400 i „can't parse entities" - czyli jeden znak `<` w tytule
ogłoszenia kosztował cały rower. Dziś przy TYM błędzie leci jedno ponowienie
bez `parse_mode`, ze zdjętymi znacznikami: brzydko, ale treść dochodzi.
Przy 429 i przy błędzie sieci ponawiamy po staremu, bo tam składnia jest
w porządku - pilnuje tego osobny test.

**CZEGO NIE ZROBIONO I DLACZEGO.** Przy tej samej okazji napisałem alarm na
konfigurację, w której kanał pisze botem DealHawka (patrz niżej) - i **wywalił
8 działających testów**, bo w ich atrapach oba tokeny są tym samym
placeholderem, więc alarm zapalał się w każdym biegu i doliczał wiadomość.
Przerobienie pięciu atrap tylko po to, żeby dołożyć rzecz drugorzędną, jest
dokładnie tym grzebaniem w sprawnym kodzie, którego ten plik zabrania. Alarm
cofnięty w całości, do zrobienia osobno i z własną robotą przy atrapach.

## Kliknięcie w przycisk kanału ginie po cichu, gdy brak osobnego bota (19.09.2026)

Zdiagnozowane, **NIE naprawione** - naprawa czeka na potwierdzenie, w której
konfiguracji stoi produkcja.

Objaw: przycisk pod wiadomością na BestDealHawku nie robi nic, bez śladu
w logu, bieg kończy się kodem zero.

Mechanizm, gdy sekret `TELEGRAM_BEST_BOT_TOKEN` jest pusty:

```
BEST_BOT_TOKEN = os.environ.get("TELEGRAM_BEST_BOT_TOKEN") or T.TELEGRAM_BOT_TOKEN
```

1. Wiadomości NADAL przychodzą, bo wysyłka ma czym pisać. Wszystko wygląda zdrowo.
2. Kliknięcie ląduje w kolejce `getUpdates` bota **DealHawka**.
3. `najlepsze.czytaj_odrzuty` odmawia czytania tej kolejki - słusznie, bo dwa
   procesy na jednym wskaźniku gubiłyby zdarzenia losowo i po cichu.
4. `tracker.read_telegram_commands` ją czyta, ale wyrzuca kliknięcie na
   filtrze czatu (`if str(rozmowa) != str(TELEGRAM_CHAT_ID): continue`) -
   **i przesuwa przy tym wskaźnik**, więc kliknięcie przepada bezpowrotnie.

Nikt go nie odbiera. To ta sama rodzina co „alarm działał, kompensacja nie"
z 01.09: każdy element z osobna zachowuje się poprawnie, a razem tworzą ciszę.

**Dowód pośredni:** `best_offset.json` (wskaźnik kolejki kanału) nie zmienił
się od 17.09 21:21, mimo klikania. Gdyby odpytywanie działało, każde
kliknięcie by go przesunęło. Dla kontrastu przyciski odrzutu zapisały się
7 razy 14-16.09, czyli kolejka kiedyś działała - i przestała mniej więcej
wtedy, gdy 18.09 padł token bota.

**Czego nie dało się sprawdzić z sesji:** sekretów GitHuba nie widać. Test dla
właściciela zajmuje pięć sekund: **kto podpisuje wiadomości na kanale
najlepszych.** Ten sam bot co DealHawk = sekretu nie ma.

Dwie drogi naprawy, obie do przemyślenia z liczbami:
- uzupełnić sekret (zero zmian w kodzie),
- albo nauczyć `tracker` obsługi callbacków z czatu kanału i odpowiadania
  W TYM czacie. To znaczy `send_telegram` z opcjonalnym `chat_id` i zmianę
  kontraktu `read_telegram_commands`, którą pinuje kilka testów.

## Goly link dziala TYLKO na BestDealHawku (19.09.2026)

Wlasciciel wkleil sam adres ogloszenia i nie stalo sie nic. Sprawdzone:
`parse_oferta_command` zada slowa „oferta" albo „/of" na POCZATKU, wiec goly
link odbijal sie bez sladu - **nie dzialal NIGDZIE**, ani na DealHawku, ani na
kanale najlepszych.

**A komentarz nad rozbiorem komend w `tracker.py` twierdzil, ze dziala.** Stal
tam od 17.09 i byl nieprawda. To ta sama klasa wpadki co „komentarz opisujacy
zasade to NIE jest zasada" z 18.09, tylko tym razem komentarz nie opisywal
zasady lamanej przez kod obok - opisywal funkcje, ktorej nigdy nie bylo.
Zaufalem mu przy odpowiedzi wlascicielowi i podalem mu droge, ktora nie
dziala. **Zanim powiesz uzytkownikowi, co ma wpisac, URUCHOM to.**

**Decyzja wlasciciela: „popraw ALE ja chce zeby to dzialalo tylko na
bestdealhawku".** Rozpoznaje link `oferta.komenda_z_linku`, a wola je
WYLACZNIE `najlepsze.czytaj_odrzuty`. `tracker` tej funkcji nie wola i na
DealHawku goly link ma nadal nie robic nic. Sama obecnosc funkcji w
`oferta.py` niczego nie wlacza - cala decyzja siedzi w tym, KTO ja wola,
wiec test pyta o wywolanie w kodzie z wycietymi komentarzami, nie o samo
slowo. Pierwsza wersja testu szukala slowa i padla na wlasnym komentarzu
tlumaczacym decyzje.

Trzy rzeczy, ktorych nie ruszac:

- **Zadamy PELNEGO ADRESU jednego z dwoch serwisow, nie samych cyfr.** Gola
  liczba w czacie bywa kwota („2200"), a rozpoznanie jej jako ogloszenia
  zamienialoby kazda wpisana cene w wiadomosc do obcego czlowieka. Adres
  polki (`/s-fahrraeder/c217`) tez odpada - to nie ogloszenie.
- **OLX odrzucony z rozmyslem.** To strona SPRZEDAZY, nie ma tam do kogo
  pisac oferty kupna.
- **Zwykla rozmowa ma byc nadal pomijana.** Poluzowanie warunku o ukosnik nie
  moze zamienic kanalu w automat odpowiadajacy na kazde zdanie. Ten test
  przechodzi na obu wersjach, wiec nie spelnia reguly 2 - ale sprawdzony
  niedbala wersja poprawki (kazdy tekst leci do handlera) pada, czyli jest
  straznikiem regresji, a nie pieczatka. **Przy tescie, ktory przechodzi na
  obu wersjach, popsuj kod celowo i pokaz, ze pada.**

**TA POPRAWKA JEST MARTWA, DOPOKI KANAL NIE MA WLASNEGO BOTA.**
`czytaj_odrzuty` przy `BEST_BOT_TOKEN == T.TELEGRAM_BOT_TOKEN` wraca od razu
zerem i nie czyta z kanalu NICZEGO - ani klikniec, ani wklejonych linkow.
To ta sama przyczyna co martwy przycisk oferty (patrz rozdzial wyzej) i ten
sam pieciosekundowy test dla wlasciciela: kto podpisuje wiadomosci na kanale
najlepszych. Alarm na te konfiguracje jest dalej NIEZROBIONY - wywalil 8
dzialajacych testow 19.09 i zostal cofniety.

**W `tracker.py` nie zmieniono ANI JEDNEJ linijki kodu**, wylacznie ten
nieprawdziwy komentarz. Sprawdzone diffem.

## Podsumowanie czytalo ZAMROZONY dziennik - "dzis brak pomiarow" (19.09.2026)

Wlasciciel: "w podsumowaniu na dealhawku (...) pisze czujnosc: dzis brak
pomiarow to samo w sobie jest ostrzezeniem".

**Zdanie bylo prawdziwe co do joty i wskazywalo NIE NA TO.** Bot mierzyl
normalnie - 5 284 obejrzane ogloszenia tego dnia, polowa zlapana w 2 minuty
od wystawienia, 81% w ciagu 5 minut. Milczalo samo podsumowanie, bo
`summary.py` czytal `MARKET_FILE = Path("market.jsonl")`, czyli ZAMROZONY
najstarszy kawalek. Od podzialu dziennika 18.09.2026 nic w nim nie przybywa:
ostatni wiersz ma date 18.09, a zywe dane leza w `market-2026-09.jsonl`.

Czyli czujka odpalala sie codziennie od 18.09, poprawnie, i mowila
wlascicielowi cos, co brzmialo jak awaria bota - a bylo awaria czytnika.
**Ostrzezenie, ktore wskazuje zly organ, jest gorsze od milczenia**, bo
kieruje diagnoze w slepa uliczke.

**TO BYL TRZECI MODUL POMINIETY PRZY PODZIALE Z 18.09.** Pierwszy -
`odblokuj.py`, zlapany 19.09 rano. Teraz `summary.py`, `sprawdz_modele.py`
i `sprawdz_silniki.py`. Dwa ostatnie liczyly pliki wiedzy wlasciciela
(`topowe_modele.json`, `silniki_bosch.json`) na danych sprzed podzialu -
i wynik nadal wygladal wiarygodnie (regula 7).

**Lista nazw w tescie ZAWIODLA DRUGI RAZ, wiec przestala byc jedynym
straznikiem.** 18.09 zapisalem, ze "lista w tescie musi wymieniac KAZDY modul
z nazwy" - i nazajutrz brakowalo w niej czterech. Dzis obok listy stoi blok,
ktory PRZEMIATA WSZYSTKIE pliki `.py` w repo i pyta kazdy: siegasz po
dziennik rynku, to czy idziesz przez kawalki. Nowy modul wpada tam sam,
bez dopisywania go gdziekolwiek. Trzy pliki maja jawne zwolnienie z powodem:
`tracker.py` (sam definiuje czytnik), `zdrowie_danych.py` (swiadomie patrzy
na BIEZACY kawalek) i `sprawdz_sortowanie.py` (jednorazowa diagnoza
przekierowujaca `MARKET_FILE` do piaskownicy).

Regula 2: na kodzie sprzed poprawki pada 4 sprawdzenia, w tym WSZYSTKIE
TRZY zlapane przez przemiatanie, a nie przez liste.

**PLIKI WIEDZY BYLY NIEAKTUALNE JUZ PRZED TA POPRAWKA** i to jest osobna
sprawa, nie skutek podzialu. Zmierzone przez uruchomienie STAREJ i NOWEJ
wersji obu narzedzi na tych samych danych:

| narzedzie | stara wersja | nowa wersja |
|---|---|---|
| `sprawdz_silniki.py` | 4 wpisy nie broni sie | **5** |
| `sprawdz_modele.py` | 30 roznic | 29 |

Czyli poprawka dolozyla JEDNO nowe znalezisko (winora sinus), a reszta
lezala tam wczesniej i nikt tego nie czytal.

**Pieciu wpisow silnikowych NIE RUSZAM - to plik wlasciciela.** Rozbite na
pojedyncze tytuly, bo "usun wpis" bez dowodu jest bezwartosciowe:

- `conway xyron` - 2 ogloszenia "conway xyron 629 **ep8** motor 85 nm",
  czyli Shimano EP8 wprost. PRAWDZIWY rywal.
- `kalkhoff endeavour` - 2 ogloszenia "kalkhoff endeavour e-bike -
  **bafang**". PRAWDZIWY.
- `winora sinus` - "winora sinus as city e-bike **pinion mgu** motor".
  PRAWDZIWY.
- `flyer gotour` - "flyer gotour 5 e-bike - **panasonic** mittelmotor".
  PRAWDZIWY.
- `ktm macina` - **FALSZYWY ALARM**: jedyne trafienie to "flyer uproc 4
  (**panasonic** motor...) **oder** ktm macina lfc (...) **mit bosch"**,
  czyli JEDNO ogloszenie o DWOCH rowerach. Panasonic nalezy do Flyera,
  a KTM ma w tym samym tytule napisane "mit bosch". To ta sama pulapka co
  "Mondraker Chaser (...) AHNLICH Cube Stereo Hybrid 160" przy
  `topowe_modele.json`.

Cztery pierwsze sa realnym wyciekiem z twardego ograniczenia "tylko Bosch",
ale zawezenie listy zmienia to, co bot kupuje, a plik jest z rozmyslu
wlasnoscia wlasciciela ("wlasciciel ma ja czytac i poprawiac sam").
**Decyzja nalezy do niego, narzedzie ma tylko flagowac.**

## Link bez "https://" i cisza po nim (19.09.2026, druga runda)

Wlasciciel po wdrozeniu poprzedniej poprawki: "wyslalem link i cisza bez
reakcji". Zmierzone, zanim cokolwiek ruszylem:

- wskaznik kolejki kanalu przeskoczyl o **1 o 20:21:34**, a poprawka gologo
  linku weszla na main o 20:15:40 - sprawdzone `git merge-base
  --is-ancestor`, wiec **bieg mial juz nowy kod**,
- czyli bot wiadomosc PRZECZYTAL i sam postanowil nic nie zrobic.

To wystarczylo, zeby odrzucic hipotezy "nie zdazylo sie wdrozyc" i "kanal
nie czyta kolejki" bez zgadywania. **Wskaznik kolejki jest tu najlepszym
swiadkiem** - mowi, czy wiadomosc w ogole zostala odebrana.

**Dziura pierwsza: wzorzec zadal schematu `https://` na sztywno.** Telegram
rysuje "www.kleinanzeigen.de/..." jako klikalny odnosnik, wiec dla czlowieka
wyglada to jak kazdy inny link. Dzis schemat jest opcjonalny, a host moze
miec dowolny przedrostek (`www.`, `m.`, zaden).

**Dziura druga, wazniejsza: CISZA.** Nierozpoznana wiadomosc przesuwala
wskaznik i przepadala bez slowa - po OBU stronach. Na DealHawku
`read_telegram_commands` przepuszcza KAZDY tekst, a odpowiedz dostawaly
wylacznie wiadomosci z ukosnikiem, wiec wklejony adres ginal tak samo.
Jedyna roznica miedzy "nie zrozumialem" a "bot padl" byla cisza. To ta sama
wpadka co "napisalem i nic" z 13.09, tylko rok pozniej i na drugiej drodze.

Dzis `wyglada_na_probe_linku` decyduje, kiedy odpowiedziec:

- **kanal najlepszych** mowi, ze nie wyjal numeru, i podaje oba sposoby,
- **DealHawk** mowi, ze goly link dziala na kanale najlepszych, i pokazuje
  `/oferta <link>`. Oferty tam NIE sklada - decyzja wlasciciela stoi.

**Predykat jest WASKI z rozmyslu.** Lapie tekst z "http", "www.", nazwa
serwisu albo ciagiem 9-12 cyfr. Zwykle zdanie ("dzieki, fajny rower") nadal
przechodzi bez odpowiedzi, inaczej kanal zamienilby sie w automat
odpowiadajacy na kazde slowo. Gola kwota "2200" tez nie lapie - za krotka.

Regula 2: 2 z 2 nowych testow kanalu padaja na starym kodzie, a sprawdzenie
po stronie DealHawka wywraca sie na `AttributeError`.

**Nauka: po wdrozeniu poprawki, o ktora prosil wlasciciel, sprawdz nie tylko
czy KOD jest na main, ale czy jego wejscie wyglada tak, jak wlasciciel je
poda.** Wzorzec testowalem wylacznie na adresach z `seen.json`, ktore
ZAWSZE maja schemat, bo zapisuje je bot. Czlowiek kopiujacy z telefonu
podaje co innego.

## Wiadomosc do bota MOZE DO NIEGO NIE DOTRZEC (20.09.2026)

Wlasciciel wklejal prawdziwy link z pelnym `https://` i dalej nic.
**Tym razem kod nie byl winny i pomiar to pokazal, zanim cokolwiek ruszylem:**

- `komenda_z_linku` na TYM adresie oddaje poprawna komende, numer
  3517412684 wyciaga sie dobrze, a `handle_oferta` sklada pelna wiadomosc
  (1 942 znaki, cena 2 850 € VB, propozycja 2 300 €),
- symulacja CALEJ drogi kanalu na wdrozonym kodzie: **1 odpowiedz**,
- krok „Kanal najlepszych ofert" konczy sie `success` we wszystkich
  7 ogniwach, wiec `czytaj_odrzuty` naprawde sie wykonuje.

**Rozstrzygnal WSKAZNIK KOLEJKI.** Oba pliki (`best_offset.json`
i `telegram_offset.json`) stoja od 19.09 wieczorem, a bot commituje co
minute. Offset przesuwa sie przy KAZDYM odebranym zdarzeniu, nawet takim,
ktore kod pomija - wiec nieruchomy wskaznik znaczy jedno: **do kolejki
`getUpdates` nic nie przyszlo.** Wiadomosc nie dotarla do bota.

**Telegram nie dostarcza botowi wszystkiego i to jest przyczyna poza kodem:**

- w **grupie** z wlaczonym trybem prywatnosci bot dostaje WYLACZNIE komendy
  (tekst od `/`) i odpowiedzi na wlasne wiadomosci - goly wklejony link nie
  jest ani jednym, ani drugim, wiec bot go nigdy nie widzi;
- w **kanale** bot dostaje wpisy tylko, gdy jest administratorem;
- w rozmowie PRYWATNEJ z botem dostarczane jest wszystko.

To tlumaczy komplet objawow naraz: `/dojrzale` dziala, `/oferta <link>`
dziala, a sam link znika bez sladu - bo komenda jest dostarczana, a link nie.

**Zanim znowu zaczniesz poprawiac parser, sprawdz wskaznik kolejki.**
Stoi mimo zdrowych biegow = problem jest po stronie Telegrama, nie w repo.
Trzy poprawki parsera z 19.09 celowaly w zdrowy koniec dokladnie tak samo
jak trzy poprawki pusha z 18.09, zanim `GIT_TRACE` pokazal, ze chory jest
pobor.

**Luka w kodzie znaleziona przy okazji i naprawiona:**
`najlepsze.czytaj_odrzuty` czytalo WYLACZNIE `upd["message"]`, a
`tracker.read_telegram_commands` od dawna czyta `message` **albo**
`channel_post`. Gdyby bot zostal administratorem kanalu, wpis przyszedlby
jako `channel_post`, przesunal wskaznik i przepadl bez sladu w logu. Dwie
kopie tej samej reguly rozjechaly sie dokladnie tak, jak ostrzega akapit
o `litera_ramy`. Dzis pilnuje tego test czytajacy OBA pliki.

## Za duza rama to NIE smiec - rozdzielone (20.09.2026)

Wlasciciel przyslal link i pytanie „dlaczego to nie przyszlo": **CUBE Stereo
Hybrid 160 HPC TM 750 | XL, nur 894km, Service 03/26** (3517638486, 2 990 €).
To jest MODEL Z JEGO LISTY ZYCZEN, przy ktorym prosil imiennie, zeby nie
przegapic ani jednego ogloszenia.

Bot zobaczyl go tego samego dnia, zapisal do dziennika rynku i odrzucil
z powodem **`smiec`**. Wyciagniete wprost z `seen.json`, nie z symulacji.

**Przyczyna: `\bxl\b` i `\bxxl\b` stały w `SKIP_PATTERNS`**, czyli w liscie
„to nie jest rower, tylko czesc" - razem z „Rahmen", „Motor" i „Akku".
Ale XL opisuje rower KOMPLETNY, tylko za duza rame: to decyzja BIZNESOWA
o zbycie w Polsce, ta sama rodzina co odrzut ramy S, a nie rozpoznanie
ogloszenia, ktore rowerem nie jest.

Pomylenie tych dwoch rzeczy kosztowalo dokladnie tyle: `is_junk` jest bramka
ZAMKNIETA dla listy zyczen, a uzasadnienie w tym pliku brzmialo doslownie
„ogloszenie o samej ramie to nie rower" - zdanie prawdziwe, ktore o XL nie
mowi nic. **Zmierzone na 137 029 unikalnych ogloszeniach: 5 059 ma XL/XXL
w tytule, a wsrod 90 rowerow obserwowanego modelu jest ich 8.** Czyli co
dziewiaty egzemplarz tego, o co wlasciciel prosil imiennie, ginal na regule
z cudzej listy.

**Poprawka jest rozdzieleniem, nie poluzowaniem.** `za_duza_rama` to osobna
funkcja i osobna bramka, OTWARTA dla listy zyczen tak samo jak budzet
i przebieg. Rower SPOZA listy odpada dokladnie jak dotad - tylko pod wlasna
nazwa, bo `odrzuc` zapisuje teraz `za_duza_rama` zamiast `smiec`.

Koszt policzony przed wdrozeniem: **0,11 wiadomosci dziennie** liczac 74 dni
dziennika, 0,14 w ostatnich 14 dniach. Nie obserwowanych XL dalej odrzucamy:
**5 051 z 5 059**.

**Test pilnuje OBU polowek**, bo sama zmiana etykiety bez odrzutu byłaby
cichym poszerzeniem rynku, a sama nowa funkcja bez wyjecia `\bxl\b`
ze `SKIP_PATTERNS` bylaby ozdoba. Stary test „XL odpada" padl przy tej
zmianie i to bylo poprawne zachowanie - pilnowal starego swiata.

**GRANICA SLOWA TRZYMA:** „XLC" (marka osprzetu) nie jest rozmiarem ramy
i wzorzec jej nie lapie. Sprawdzone testem.

**DZIALA TYLKO W PRZOD** - ta sama dziura co zawsze. Wpis w `seen.json` jest
terminalny, a `smiec` nie jest na liscie `POWODY_PO_CENIE`, wiec osiem
wczesniej zdlawionych rowerow NIE wroci samo.

**`odblokuj.py --wznow` TEGO NIE ZALATWIA** i sprawdzenie tego zajelo minute:
ta funkcja pomija wpisy, ktore w `seen.json` SA (`if ad_id in seen: continue`),
a nasze sa - z powodem `smiec`. Robi to `odzyskaj_rame.py`, blizniak
`odzyskaj_silnik.py` dla tej samej klasy wpadki. Bierze WYLACZNIE rowery
z listy zyczen; zwykly rower w XL ma dalej odpadac, bo poprawka rozdzielila
etykiety, a nie poszerzyla rynku. Pilnuja tego cztery testy, w tym jeden na
zwyklym rowerze w XL i jeden na ogloszeniu o samej RAMIE.

Zmierzone tego dnia: z osmiu obserwowanych rowerow w XL **piec ma nieme wpisy
sprzed 01.09** (bez powodu, wiec nie do odroznienia), a zywe sa **dwa** -
z 19.09 (4 000 €) i 20.09 (2 990 €, ten z pytania wlasciciela). Reszta to
lipiec i sierpien, czyli ogloszenia dawno martwe. **Podawaj `--od`.**

## Dwa bezpieczniki przed zmiana z cudzej sesji (20.09.2026)

Wlasciciel: „chce zebys stworzyl jakies mechanizmy ktore uchronia bota przed
zjebaniem sie; czesto wpadam na nowe pomysly i zlecam ci (...) takze w innych
czatach ktore moga nie znac do konca kontekstu".

Zmierzone tego dnia, zanim cokolwiek powstalo - **siatka ochronna miala trzy
dziury i kazda jest sprawdzalna w pliku:**

1. **`tests.yml` mial `ref: main`.** Automatyczne testy pobieraly WERSJE,
   KTORA JUZ DZIALA, i wykonywaly sie na niej niezaleznie od tego, co ktos
   przyslal. **Wychodzily zielone zawsze.** Jedyna realna obrona byla
   dyscyplina autora zmiany.
2. **`test_najlepsze.py` nie uruchamial sie w CI ANI RAZU** - 125 sprawdzen
   kanalu najlepszych lezalo w repo i nie wolal ich nikt.
3. **Lista plikow budzacych testy byla RECZNA**, wiec nowy plik nie budzil
   niczego. Ta sama pamiec zawiodla juz dwa razy przy liscie czytnikow
   kawalkow (18. i 19.09).

**Poprawka pierwsza: testy sprawdzaja PRZYSLANA zmiane.** Checkout bez
`ref`, wyzwalacz `pull_request` obok `push`, wszystkie TRZY zestawy.
**Lista plikow jest teraz ODWROTNA** (`paths-ignore`): domyslnie testujemy
wszystko, a wymieniamy tylko stan, ktory boty zapisuja same co kilka minut -
wyciagniety z `git add` we wszystkich pieciu zadaniach. Nowy modul jest
chroniony od pierwszej sekundy, bez dopisywania go gdziekolwiek.

**Poprawka druga: hamulec na lawine powiadomien** (`utnij_lawine`,
`MAX_WYSYLEK_NA_BIEG = 20`). Kanal najlepszych ma sufit od 09.09, DealHawk
NIE MIAL GO NIGDY - a kazda bramka tego bota da sie poluzowac jedna linijka
i wtedy nic nie stoi miedzy rynkiem a telefonem.

**PROG Z POMIARU, NIE Z GLOWY.** Zmierzone na 296 ostatnich commitach
`history.jsonl` (wiersze dopisane przez JEDEN bieg): **mediana 1, p90 3,
p99 8, najwiekszy zdrowy 11**. Jeden wynik odstajacy (5 740 wierszy, commit
03925394 z 17.09 21:21) wylaczony swiadomie - to nadrabianie po
16-godzinnej awarii, nie skan; tej samej minuty drgnely oba wskazniki
kolejek. Dla porownania CALY dzien to mediana 18 wyslanych ofert, p90 83,
maksimum 148 (liczone na wpisach ze `score` w `seen.json` - `history.jsonl`
liczy wszystkie wycenione oferty i daje 564, wiec **nie mylic tych dwoch
liczb**).

Trzy rzeczy, ktorych nie ruszac:

- **Wstrzymane NIE wracaja w nastepnym biegu i to jest swiadome.**
  `seen.json` jest zapisany PRZED wysylka, wiec te rowery sa juz widziane.
  Gdyby wracaly, hamulec zamienilby jedna lawine w lawine powtarzana co
  bieg - ta sama pulapka, ktora opisuje rozdzial o kanale najlepszych.
  Cene placimy RAZ, a wlasciciel dostaje o tym **osobna wiadomosc**: cisza
  tutaj bylaby gorsza od lawiny.
- **Hamulec stoi PRZED petla**, nie w srodku. W srodku setki wiadomosci
  zdazylyby wyjsc, zanim ktokolwiek policzy.
- **Ucinamy NAJSTARSZE.** Sortowanie juz bylo, wiec wlasciciel dostaje
  najswiezsze - przy powiadomieniu o okazji liczy sie minuta.

**Test pyta, czy hamulec jest WPIETY, nie czy istnieje.** Funkcja obok
martwej petli to ozdoba - ta sama wpadka co alarm o braku
`topowe_modele.json`, ktory byl napisany, przetestowany i MARTWY (09.09).

**I ta sama pulapka co zawsze przy testach na pliki:** pierwsza wersja
sprawdzenia „nie ma `ref: main`" **padla na moim wlasnym komentarzu**
tlumaczacym, czemu tego tam nie ma. Trzeci raz w tym repo. Komentarze
wycinamy, a pytamy o DZIALANIE.

Regula 2: `test.py` wywraca sie na starym `tracker.py` od razu
(`AttributeError: MAX_WYSYLEK_NA_BIEG`), a **wszystkie 5** sprawdzen
workflow pada na starej wersji `tests.yml`.

**CO ZOSTAJE DO ZROBIENIA - PROBA NA SUCHO.** Najmocniejszy z trzech
mechanizmow i jeszcze go nie ma: przepuscic ostatnie 14 dni dziennika przez
nowa wersje, policzyc, ile wiadomosci by wyslala, i porownac z zapisanym
wzorcem. To lapie zepsucie NIEZALEZNIE od tego, ktora linijke ktos ruszyl,
bo mierzy SKUTEK, a nie kod - czyli robi mechanizmem to, co dzis jest
dyscyplina („zmierzone przed wdrozeniem: +0,11 wiadomosci dziennie").
Prog trzeba zmierzyc, zeby nie krzyczal przy zwyklych wahaniach rynku.

## Styl

Polski, bez żargonu w wiadomościach do użytkownika. Komentarz w kodzie tłumaczy
**dlaczego**, nie co — najlepiej z liczbą i datą pomiaru. Współczynnik wyceny nigdy
nie bierze się z głowy; ma wynikać z odduplikowanych danych albo go nie ma.
