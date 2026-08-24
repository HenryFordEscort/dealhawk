# Reguły pracy nad tym repo

Właściciel jest nietechniczny i nie zweryfikuje czarnej skrzynki. Narzędzie ma być
audytowalne — inaczej mu nie zaufa i słusznie. Te reguły powstały z pięciu realnych
wpadek, nie z teorii. Każda ma na końcu wpadkę, której miała zapobiec.

## Co tu żyje

Dwa **niezależne** boty w jednym katalogu:

| bot | wejście | serwisy | testy |
|---|---|---|---|
| rowerowy (DealHawk) | `tracker.py` | Kleinanzeigen + OLX | `python test.py` |
| samochodowy | `otomoto_tracker.py` | Otomoto + OLX | `python test_otomoto.py` |

Pomocnicze: `summary.py` (dzienne podsumowanie, liczy `olx_watch.json`),
`dozorca.py` (cykl życia ofert OLX → `zdarzenia/`), `zbieraj_rynek.py`
(→ `rynek_pl.jsonl`), `olx.py` (wspólne wejście do OLX przez Cloudflare Worker),
`zycie_ofert.py` (czyta `zdarzenia/` → ile rowerów zeszło, po ilu dniach
i czy zeszły, czy wygasły; komenda `/zycie` na Telegramie).

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
