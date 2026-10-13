#!/usr/bin/env python3
"""PRÓBKA POWIADOMIENIA - wysyła jedną wiadomość w dokładnie takim kształcie,
w jakim przychodzi prawdziwa oferta: zdjęcie główne z podpisem i przyciskami,
a pod nim resztę galerii cichym albumem.

Po co to jest. Galeria weszła 13.10.2026 i chodziła na produkcji, ale NIE BYŁO
czego pokazać: ani jedno auto nie spełniało wtedy kryteriów, więc właściciel
miał wierzyć na słowo, że wygląda to tak jak w DealHawku. Poprosił wprost, żeby
to zobaczyć. Narzędzie jest RĘCZNE i chodzi wyłącznie z `workflow_dispatch`
(sekretów Telegrama nie ma poza Actions), nigdy z harmonogramu.

Trzy rzeczy, których nie ruszać, bo każda może zrobić szkodę:

 * NIC NIE ZAPISUJE. Żadnego `seen`, żadnego dziennika. Wpis w `seen` jest
   terminalny, więc próbka, która by go dołożyła, UCISZYŁABY prawdziwe
   powiadomienie o tym aucie na zawsze. Pilnuje tego test czytający źródło.
 * PRZYCISKI SĄ NIEAKTYWNE z rozmysłu (`odrz|probka|<id>`). `czytaj_przyciski`
   przepuszcza tylko powody z `POWODY_ODRZUTU`, więc kliknięcie w próbce nie
   dopisze nic do `odrzuty_auta.jsonl` - a ten plik jest dziennikiem FAKTÓW
   o prawdziwych ofertach i próbka nie ma prawa go zaśmiecić. Wiadomość mówi
   wprost, że w próbce nie działają.
 * NAGŁÓWEK MÓWI, ŻE TO PRÓBKA. Ogłoszenie wybrane dla zdjęć prawie nigdy nie
   spełnia kryteriów (dziś w regionie nie spełnia ich żadne), a wiadomość
   wyglądająca jak oferta, którą nie jest, to dokładnie ta nieuczciwość,
   przed którą stoi reguła 6 z CLAUDE.md.

Użycie (z Actions): `python wyslij_probke.py [--szukanie 2] [--url <adres>]`.
"""
import argparse
import sys

import otomoto_tracker as ot

NAGLOWEK = (
    "🧪 <b>PRÓBKA POWIADOMIENIA</b> (na Twoje życzenie, nie oferta)\n"
    "Tak wygląda powiadomienie z galerią: zdjęcie główne z podpisem, pod nim "
    "reszta zdjęć. Przyciski w próbce są nieaktywne.\n"
)
OGLOSZEN_DO_PROBY = 3        # ile ogłoszeń z puli próbować, nim się podda


def przyciski_probki(lid: str) -> list:
    """Ten sam układ co pod prawdziwą ofertą, ale martwy dla `czytaj_przyciski`:
    "probka" nie jest powodem z POWODY_ODRZUTU, więc kliknięcie jest pomijane
    i nie trafia do dziennika odrzutów."""
    return [[{"text": "Nie interesuje", "callback_data": f"odrz|probka|{lid}"}],
            [{"text": "Za duża szkoda", "callback_data": f"odrz|probka|{lid}"},
             {"text": "Za drogo", "callback_data": f"odrz|probka|{lid}"}]]


def wybierz(search: dict, url: str = None):
    """Ogłoszenie z galerią. Bierze pierwsze, które ma co najmniej dwa zdjęcia,
    bo próbka bez albumu nie pokazuje tego, po co powstała."""
    pula = ot.fetch_listings_otomoto(search)
    if url:
        pula = [o for o in pula if url.strip() in (o.get("url") or "")] or pula
    for listing in pula[:OGLOSZEN_DO_PROBY]:
        ot.uzupelnij_ze_strony(listing)
        if len(ot.galeria_ogloszenia(listing)) >= 2:
            return listing
    return pula[0] if pula else None


def powod_nie_oferta(listing: dict, search: dict) -> str:
    """Czemu to konkretne auto NIE jest ofertą. Bez tego próbka z cudzego
    województwa wygląda, jakby przeszła kryteria - a nazwa wyszukiwania stoi
    w wiadomości i sama z siebie sugeruje, że przeszła."""
    if not ot.in_allowed_region(listing.get("region") or ""):
        return f"stoi w {listing.get('region') or 'nieznanym województwie'}"
    pasuje, _ = ot.sprawdz_kryteria(listing, search["kryteria"])
    if not pasuje:
        nadwozie = listing.get("body") or "?"
        naped = {"awd": "xDrive", "rwd": "na tylne koła",
                 "fwd": "na przednie koła"}.get(listing.get("drive"), "nieznany napęd")
        return f"nadwozie {nadwozie}, {naped}, {listing.get('engine_cm3') or '?'} cm3"
    return "spełnia kryteria, ale nie wysyłam jej jako oferty, tylko jako próbkę"


def tresc(listing: dict, search: dict, wycena: dict) -> str:
    _, braki = ot.sprawdz_kryteria(listing, search["kryteria"])
    listing["braki"] = braki
    return (NAGLOWEK + f"Wybrane dla zdjęć, {powod_nie_oferta(listing, search)}.\n\n"
            + ot.tekst_oferty("Otomoto", listing, search["name"], wycena))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Wysyła jedną próbkę powiadomienia")
    p.add_argument("--szukanie", type=int, default=2,
                   help="indeks w SEARCHES (domyślnie 2, czyli BMW Seria 3)")
    p.add_argument("--url", default=None, help="adres konkretnego ogłoszenia z puli")
    args = p.parse_args(argv)

    search = ot.SEARCHES[args.szukanie]
    listing = wybierz(search, args.url)
    if not listing:
        print("Pula wyszukiwania jest pusta, nie ma z czego zrobić próbki")
        return 1
    galeria = ot.galeria_ogloszenia(listing)
    wycena = ot.wycena_sprawnego(ot.OLX_SEARCHES[args.szukanie], listing)
    tekst = tresc(listing, search, wycena)
    print(f"Próbka: {listing['title']} | zdjęć: {len(galeria)} "
          f"(główne 1, album {len(galeria[1:ot.ALBUM_MAX])}) | podpis: {len(tekst)} znaków")
    if len(tekst) > 1024:
        # sendPhoto nie przyjmie takiego podpisu i poszedłby sam tekst, czyli
        # próbka pokazałaby co innego, niż miała pokazać
        print("UWAGA: podpis dłuższy niż limit 1024 znaków Telegrama")
    if not ot.wyslij_z_galeria(tekst, galeria, przyciski_probki(listing["id"])):
        print("Telegram nie przyjął wiadomości")
        return 1
    print("Wysłane")
    return 0


if __name__ == "__main__":
    sys.exit(main())
