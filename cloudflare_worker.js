/**
 * DealHawk — przekaźnik do OLX (Cloudflare Worker, darmowy plan).
 *
 * PO CO TO JEST:
 * OLX blokuje serwerownię GitHub Actions (HTTP 403 na całą domenę — sprawdzone
 * 21.08.2026 na sześciu różnych wejściach). Boty nie mogą więc pytać OLX-a
 * bezpośrednio. Ten Worker stoi w sieci Cloudflare, która blokady nie ma,
 * i przekazuje zapytania dalej.
 *
 * ZABEZPIECZENIA (ważne — bez nich to byłoby otwarte proxy dla całego świata):
 *  1. wymagany klucz — bez niego 401
 *  2. wyłącznie adresy olx.pl — niczego innego nie pobierze
 *  3. tylko GET
 *
 * Prawdziwy kod odpowiedzi OLX-a wraca w nagłówku "x-cel-status", a sam Worker
 * zawsze odpowiada 200. Dzięki temu przekierowania (301/302 = oferta zdjęta)
 * docierają nienaruszone, zamiast być po drodze "połknięte".
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

export default {
  async fetch(request, env) {
    if (request.method !== "GET") {
      return new Response("tylko GET", { status: 405 });
    }

    const url = new URL(request.url);

    // Sprawdzenie życia: /zdrowie -> "ok" (bez klucza, do monitoringu)
    if (url.pathname === "/zdrowie") {
      return new Response("ok", { status: 200 });
    }

    if (url.searchParams.get("k") !== env.KLUCZ) {
      return new Response("nieautoryzowane", { status: 401 });
    }

    const adres = url.searchParams.get("u");
    if (!adres) return new Response("brak parametru u", { status: 400 });

    let cel;
    try {
      cel = new URL(adres);
    } catch (e) {
      return new Response("niepoprawny adres", { status: 400 });
    }
    if (cel.protocol !== "https:" || !cel.hostname.endsWith("olx.pl")) {
      return new Response("dozwolone tylko https://*.olx.pl", { status: 403 });
    }

    let odp;
    try {
      odp = await fetch(cel.toString(), {
        headers: NAGLOWKI_PRZEGLADARKI,
        redirect: "manual", // 301/302 ma dotrzeć do bota, a nie zostać wykonane
      });
    } catch (e) {
      return new Response("blad pobrania: " + e, {
        status: 200,
        headers: { "x-cel-status": "0" },
      });
    }

    const naglowki = new Headers();
    naglowki.set("x-cel-status", String(odp.status));
    naglowki.set("content-type", odp.headers.get("content-type") || "text/plain");
    const loc = odp.headers.get("location");
    if (loc) naglowki.set("x-cel-location", loc);

    return new Response(odp.body, { status: 200, headers: naglowki });
  },
};
