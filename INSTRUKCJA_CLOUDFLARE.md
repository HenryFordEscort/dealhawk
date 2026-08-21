# Uruchomienie przekaźnika OLX (Cloudflare Worker)

**Po co:** OLX blokuje serwery GitHuba (HTTP 403 na całą domenę). Bez przekaźnika
bot rowerowy i samochodowy nie mają dostępu do polskiego rynku — nie liczą
zysków, nie obserwują cen. Twój domowy internet działa normalnie; przekaźnik
sprawia, że boty pytają OLX-a „z normalnego łącza", a nie z serwerowni.

**Czas:** ~10 minut. **Koszt:** 0 zł (darmowy plan, limit 100 000 zapytań/dobę,
zużyjemy kilkaset).

---

## Krok 1 — wygeneruj hasło (klucz)

W Terminalu na Macu:

```bash
openssl rand -base64 24
```

Wynik (ciąg ~32 znaków) skopiuj do notatnika. **Będzie potrzebny dwa razy:**
raz w Cloudflare, raz w GitHubie — i musi być **identyczny** w obu miejscach.

To jedyna rzecz chroniąca przekaźnik, bo kod jest jawny. Nie używaj „haslo123".

---

## Krok 2 — załóż konto Cloudflare

1. Wejdź na **https://dash.cloudflare.com/sign-up**
2. Podaj e-mail i hasło (karta płatnicza **nie** jest potrzebna)
3. Potwierdź link z maila

---

## Krok 3 — utwórz Workera

1. W panelu, w menu po lewej: **Workers & Pages**
   *(w nowszym układzie może się nazywać **Compute** → **Workers**)*
2. **Create** → zakładka **Workers** → **Start with Hello World!** → **Deploy**
3. Nadaj nazwę, np. `olx-przekaznik`
4. Po wdrożeniu zobaczysz adres — **skopiuj go**, wygląda tak:

   ```
   https://olx-przekaznik.twoja-nazwa.workers.dev
   ```

---

## Krok 4 — wklej właściwy kod

1. Kliknij **Edit code** (albo **</> Edit code**)
2. W edytorze zaznacz **całą** zawartość (Cmd+A) i skasuj
3. Wklej całą zawartość pliku **`cloudflare_worker.js`** z tego repo
4. **Deploy** (prawy górny róg)

---

## Krok 5 — ustaw klucz w Cloudflare

1. Wyjdź z edytora do widoku Workera
2. **Settings** → **Variables and Secrets** (lub **Variables**)
3. **+ Add**:
   - **Type:** `Secret` *(ważne — nie „Text")*
   - **Name:** `KLUCZ` *(dokładnie tak, wielkimi literami)*
   - **Value:** hasło z Kroku 1
4. **Save** / **Deploy**

---

## Krok 6 — powiedz o tym botom

1. Wejdź na swoje repo na GitHubie → **Settings**
2. Menu po lewej: **Secrets and variables** → **Actions**
3. **New repository secret** — dodaj **dwa** wpisy:

   | Name | Secret |
   |------|--------|
   | `OLX_RELAY_URL` | adres Workera z Kroku 3 (bez ukośnika na końcu) |
   | `OLX_RELAY_KEY` | **to samo hasło** co `KLUCZ` w Kroku 5 |

---

## Krok 7 — sprawdź, czy działa

Napisz na Telegramie do swojego bota:

```
/status
```

W ciągu ~5 minut przyjdzie odpowiedź. Szukasz linijki:

```
🇵🇱 OLX (strona): wpuszcza ✅
```

Jeśli tak — **gotowe**, oba boty mają z powrotem polski rynek.

---

## Gdyby coś nie zagrało

Bot sam powie, co jest nie tak — nie musisz zgadywać:

| Wiadomość na Telegramie | Co zrobić |
|---|---|
| 🔑 **ZŁY KLUCZ** | `KLUCZ` w Cloudflare ≠ `OLX_RELAY_KEY` w GitHubie. Ustaw oba od nowa, tym samym ciągiem. |
| 🔑 **BRAK KLUCZA** | Nie zapisał się sekret w Workerze — powtórz Krok 5 i zrób **Deploy**. |
| 🔑 **PRZEKROCZONY LIMIT** | Twoje boty tego nie wywołają. Oznacza, że ktoś zna klucz — **wygeneruj nowy** (Krok 1) i podmień w obu miejscach. |
| 🔌 **Padł przekaźnik** | Worker nie odpowiada. Zajrzyj: dash.cloudflare.com → Workers → czy jest wdrożony. |
| 🚫 **OLX blokuje MIMO przekaźnika** | Cloudflare też trafił na czarną listę. Napisz — trzeba zmienić drogę. |

Alarm przychodzi **raz** przy awarii (nie spamuje), a gdy wróci do normy —
jedna wiadomość „✅ OLX znów odpowiada".

---

## Dobre nawyki

- **Nie wklejaj klucza** nigdzie poza Cloudflare i GitHub Secrets — nie w kod,
  nie na czacie, nie w plikach repo.
- Jeśli kiedykolwiek pokażesz komuś ekran z kluczem — wygeneruj nowy.
- Klucz możesz wymienić w każdej chwili: nowe hasło w obu miejscach, gotowe.
