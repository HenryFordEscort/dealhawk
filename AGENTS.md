# Zanim ruszysz ten kod - przeczytaj

To jest **drogowskaz, nie regulamin**. Wszystkie reguły, pomiary i uzasadnienia
siedzą w jednym pliku: **`CLAUDE.md`**. Tutaj jest tylko tyle, żeby nie zepsuć
czegoś w pierwszych pięciu minutach.

Plik istnieje, bo Claude Code czyta `CLAUDE.md` sam z siebie, a inne narzędzia
szukają `AGENTS.md`. Treść się nie dubluje z rozmysłu - dwie kopie tej samej
reguły rozjeżdżają się przy pierwszej poprawce i ten projekt ma to opisane
na własnych wpadkach.

## Trzy rzeczy, których nie negocjujesz

1. **Silniki: tylko Bosch** (plus własny silnik Specialized). Filtr silnika
   ma nadal odrzucać Shimano EP8.
2. **`history.jsonl` NIGDY nie jest kasowany ani przycinany.**
3. **Bot NIE wystawia sam ogłoszeń i NIE negocjuje sam.**

Pełne uzasadnienia: rozdział „Twarde ograniczenia produktowe" w `CLAUDE.md`.

## Co uruchomić, zanim cokolwiek wypchniesz

```bash
TELEGRAM_BOT_TOKEN=dummy TELEGRAM_CHAT_ID=0 python test.py
TELEGRAM_BOT_TOKEN=dummy TELEGRAM_CHAT_ID=0 python test_otomoto.py
TELEGRAM_BOT_TOKEN=dummy TELEGRAM_CHAT_ID=0 python test_najlepsze.py
TELEGRAM_BOT_TOKEN=dummy TELEGRAM_CHAT_ID=0 python sprawdz_zachowanie.py
```

Dokładnie te cztery chodzą też w CI przy każdym pull requeście.

## Co cię złapie, jeśli zepsujesz

| mechanizm | co robi |
|---|---|
| `tests.yml` | testuje PRZYSŁANĄ zmianę, nie wersję z `main` |
| `sprawdz_zachowanie.py` | przepuszcza dwa tygodnie prawdziwego rynku przez twój kod i porównuje z `wzorzec_zachowania.json`; **próg wynosi zero** |
| `utnij_lawine` w `tracker.py` | sufit 20 powiadomień na bieg, żeby jedna poluzowana bramka nie zalała telefonu |
| `czujka_ciszy.py` | krzyczy, gdy bot przestał zapisywać dłużej niż godzinę |

**Czerwona próba na sucho nie znaczy „test jest kapryśny".** Znaczy, że twoja
zmiana przestawiła zachowanie bota na prawdziwych danych. Jeśli zrobiłeś to
ŚWIADOMIE, uruchom `python sprawdz_zachowanie.py --zapisz` i wrzuć nowy
`wzorzec_zachowania.json` do TEGO SAMEGO commita - wtedy w diffie widać koszt
zmiany liczba obok liczby.

## Pięć pułapek, w które tu już wchodzono

Każda ma w `CLAUDE.md` własny rozdział z datą i pomiarem. Tu tylko nazwy,
żebyś je rozpoznał, zanim wejdziesz w nie po raz kolejny:

- **Licz rowery, nie ogłoszenia.** Dzienniki są dziennikami - ta sama oferta
  wraca w nich przy każdym skanie. Jedno ogłoszenie potrafiło zająć 5% pliku.
- **Komentarz opisujący zasadę to nie jest zasada.** Stał nad kodem, który ją
  łamał, przez cały czas istnienia obu.
- **Ścieżka nigdy w domyślnym argumencie** (`def f(plik=STAN_FILE)`). Wiąże
  wartość w chwili definicji modułu. Ten błąd wyszedł tu cztery razy.
- **Test pytający o SŁOWO w pliku padnie na twoim własnym komentarzu.**
  Pytaj o działanie, komentarze wycinaj. To też cztery razy.
- **Sprawdzaj wzorzec czymś, co nie pochodzi od niego.** Porównanie wzorca
  z własną kopią daje zgodność co do joty i zero wiedzy.

## Czego NIE zmieniaj bez pomiaru

Progów, tempa i bramek. W tym projekcie żadna liczba nie jest wzięta z głowy -
każda ma w `CLAUDE.md` datę pomiaru i wielkość próby. Jeśli chcesz zmienić
próg, najpierw policz, ile to kosztuje, i zapisz ten rachunek razem ze zmianą.
