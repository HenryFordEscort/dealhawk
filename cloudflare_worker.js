/**
 * DealHawk — przekaźnik do OLX (Cloudflare Worker, darmowy plan).
 *
 * PO CO TO JEST:
 * OLX blokuje serwerownię GitHub Actions (HTTP 403 na całą domenę — sprawdzone
 * 21.08.2026 na sześciu wejściach, łącznie z sitemap.xml). Boty nie mogą więc
 * pytać OLX-a bezpośrednio. Ten Worker stoi w sieci Cloudflare, która blokady
 * nie ma, i przekazuje zapytania dalej.
 *
 * ZAŁOŻENIE BEZPIECZEŃSTWA:
 * Ten plik leży w PUBLICZNYM repozytorium. Napastnik zna więc każdą linijkę.
 * Cała ochrona stoi wyłącznie na sekrecie KLUCZ, którego w repo NIE MA
 * (siedzi w zmiennych Workera i w sekretach GitHuba). Kod może być jawny —
 * klucz nie.
 *
 * WARSTWY OCHRONY:
 *  1. klucz w NAGŁÓWKU, nie w adresie — adresy trafiają do logów, nagłówki nie
 *  2. porównanie klucza w stałym czasie — brak podatności na atak czasowy
 *  3. brak klucza w konfiguracji = Worker odmawia WSZYSTKIEGO (fail closed)
 *  4. wyłącznie GET i wyłącznie https://*.olx.pl
 *  5. lista dozwolonych ścieżek — nie da się przez to chodzić po całym serwisie
 *  6. limit zapytań na minutę — nawet z wykradzionym kluczem nie spali się
 *     darmowego limitu ani nie zrobi z Twojego konta narzędzia do scrapowania
 *  7. brak echa danych wejściowych w treści błędów (żadnych podpowiedzi)
 */

const NAGLOWKI_PRZEGLADARKI = {
  "User-Agent":
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 " +
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
  Accept:
    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
  "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.8",
  "Sec-Fetch-Dest": "document",
  "Sec-Fetch-Mode": "navigate",
  "Sec-Fetch-Site": "none",
  "Upgrade-Insecure-Requests": "1",
};

// Tylko te ścieżki OLX-a są nam potrzebne. Reszta odrzucana.
const DOZWOLONE_SCIEZKI = [
  /^\/api\/v1\/offers\/?$/,           // API wyszukiwania (rowery i auta)
  /^\/d\/oferta\//,                   // pojedyncza oferta
  /^\/sport-hobby\/rowery\//,         // listy rowerowe
  /^\/motoryzacja\/samochody\//,      // listy samochodowe
  /^\/sitemap\.xml$/,
];

const LIMIT_NA_MINUTE = 120; // nasze boty robią ~30/2h; 120/min to i tak ogrom

/** Porównanie odporne na atak czasowy (nie zdradza, ile znaków się zgadza). */
function kluczePasuja(podany, prawdziwy) {
  if (typeof podany !== "string" || typeof prawdziwy !== "string") return false;
  if (podany.length !== prawdziwy.length) return false;
  let roznica = 0;
  for (let i = 0; i < podany.length; i++) {
    roznica |= podany.charCodeAt(i) ^ prawdziwy.charCodeAt(i);
  }
  return roznica === 0;
}

/** Zgrubny licznik zapytań na minutę (Cache API — bez potrzeby płatnego KV). */
async function limitPrzekroczony() {
  try {
    const minuta = Math.floor(Date.now() / 60000);
    const klucz = new Request(`https://licznik.dealhawk/${minuta}`);
    const cache = caches.default;
    const poprzednia = await cache.match(klucz);
    const ile = poprzednia ? parseInt(await poprzednia.text(), 10) || 0 : 0;
    if (ile >= LIMIT_NA_MINUTE) return true;
    await cache.put(
      klucz,
      new Response(String(ile + 1), {
        headers: { "Cache-Control": "max-age=120" },
      })
    );
    return false;
  } catch (e) {
    return false; // licznik nie może blokować normalnej pracy
  }
}

function odmowa(status) {
  // Żadnych szczegółów w treści — nie podpowiadamy, co poszło nie tak.
  return new Response("nie", { status });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // Sprawdzenie życia — bez klucza, ale nic nie zdradza poza tym, że żyje.
    if (url.pathname === "/zdrowie") {
      return new Response("ok", { status: 200 });
    }

    // FAIL CLOSED: brak skonfigurowanego klucza = nie działa nic.
    if (!env.KLUCZ) return odmowa(503);

    if (request.method !== "GET") return odmowa(405);

    // Klucz w nagłówku. Adresy URL bywają logowane po drodze, nagłówki nie.
    if (!kluczePasuja(request.headers.get("x-klucz") || "", env.KLUCZ)) {
      return odmowa(401);
    }

    if (await limitPrzekroczony()) return odmowa(429);

    const adres = url.searchParams.get("u");
    if (!adres) return odmowa(400);

    let cel;
    try {
      cel = new URL(adres);
    } catch (e) {
      return odmowa(400);
    }
    if (cel.protocol !== "https:" || !cel.hostname.endsWith(".olx.pl")) {
      return odmowa(403);
    }
    if (!DOZWOLONE_SCIEZKI.some((wzor) => wzor.test(cel.pathname))) {
      return odmowa(403);
    }

    let odp;
    try {
      odp = await fetch(cel.toString(), {
        headers: NAGLOWKI_PRZEGLADARKI,
        redirect: "manual", // 301/302 (oferta zdjęta) ma dotrzeć do bota
      });
    } catch (e) {
      return new Response("", { status: 200, headers: { "x-cel-status": "0" } });
    }

    // Prawdziwy kod OLX-a wraca w nagłówku, a Worker zawsze odpowiada 200 —
    // dzięki temu przekierowania nie są po drodze "połykane".
    const naglowki = new Headers();
    naglowki.set("x-cel-status", String(odp.status));
    naglowki.set("content-type", odp.headers.get("content-type") || "text/plain");
    const loc = odp.headers.get("location");
    if (loc) naglowki.set("x-cel-location", loc);

    return new Response(odp.body, { status: 200, headers: naglowki });
  },
};
