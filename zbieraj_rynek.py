#!/usr/bin/env python3
"""Zbiera próbkę rynku PL do pliku rynek_pl.jsonl (append-only).

Jedna linia = jedna zaobserwowana oferta z rozpoznanymi cechami. To jest
surowy materiał, z którego liczy się CENNIK CECH — ile realnie kosztuje rok
starości, słabsza bateria czy gorszy osprzęt. Nic tu nie jest wnioskiem,
same fakty z konkretnego dnia; wnioski liczy się osobno i można je przeliczyć
od nowa, gdy poprawimy reguły.

Uruchom: python3 zbieraj_rynek.py [ile_modeli] [ile_ofert_na_model]
"""
import json
import sys
import time
from datetime import date
from pathlib import Path

import tracker
# przez wspólne wejście — bez tego zbieranie leci wprost do OLX i dostaje 403
# z serwerowni GitHuba (a summary.yml uruchamia ten skrypt codziennie)
from olx import olx_get, parse_olx_ad_json

PLIK = Path("rynek_pl.jsonl")


def modele_do_zbadania(limit):
    """Modele, którymi realnie handlujemy — z listy obserwowanej na OLX."""
    try:
        w = json.loads(Path("olx_watch.json").read_text())
        # najpierw te z największą liczbą obserwacji = najlepiej znane rynki
        q = sorted(w, key=lambda k: -len((w[k] or {}).get("offers", {})))
    except Exception:
        q = []
    q = [x for x in q if x and not x[0].isupper()]      # bez duplikatów z wielkiej litery
    return q[:limit] or ["cube stereo hybrid", "trek rail", "specialized turbo levo"]


def main():
    ile_modeli = int(sys.argv[1]) if len(sys.argv) > 1 else 25
    na_model = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    dzis = date.today().isoformat()
    juz = set()
    if PLIK.exists():                       # nie pobieraj drugi raz tego samego dnia
        for line in PLIK.open(encoding="utf-8"):
            try:
                r = json.loads(line)
                if r.get("ts") == dzis:
                    juz.add(r.get("url"))
            except Exception:
                pass
    zapisane = 0
    with PLIK.open("a", encoding="utf-8") as f:
        for q in modele_do_zbadania(ile_modeli):
            url = "https://www.olx.pl/sport-hobby/rowery/q-" + q.replace(" ", "-") + "/"
            r = olx_get(url, timeout=25)
            if r is None or r.status_code != 200:
                print(f"  [{q}] brak odpowiedzi (status {getattr(r, 'status_code', '-')})")
                continue
            karty = tracker.olx_relevant_offers(
                q, {c["url"]: c["price"] for c in tracker.parse_olx_cards(r.text)})
            n = 0
            for u, cena in list(karty.items()):
                if n >= na_model or u in juz:
                    continue
                rs = olx_get(u, timeout=25)
                if rs is None or rs.status_code != 200:
                    continue
                strona = rs.text
                rec = tracker._parse_detail_fields(strona)
                rec.update({"ts": dzis, "url": u, "cena": cena, "model": q})
                # Do 24.08.2026 stał tu regexp po '"seller"..."id":N' i nie trafił
                # ANI RAZU — pole `sprzedawca` miało 0 z 1427 wierszy. Skutkiem
                # była cicha awaria dwóch rzeczy naraz: odsiewania spamu sklepów
                # i deduplikacji (reguła 5: liczymy rowery, nie ogłoszenia).
                # Strona niesie całe ogłoszenie w JSON-ie, więc bierzemy stamtąd.
                # Przy okazji dochodzi lokalizacja, której ten plik nigdy nie
                # zapisywał, choć OLX podaje ją przy każdej ofercie.
                fakty = tracker._fakty_z_ad_json(parse_olx_ad_json(strona))
                fakty.pop("status", None)      # tu z definicji zawsze "active"
                rec.update(fakty)
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                f.flush()
                zapisane += 1
                n += 1
                time.sleep(0.35)
            print(f"  [{q}] +{n}")
    print(f"zapisano {zapisane} ofert do {PLIK}")

    # przelicz cennik cech na CAŁOŚCI zebranych obserwacji
    wszystko = []
    for line in PLIK.open(encoding="utf-8"):
        try:
            wszystko.append(json.loads(line))
        except Exception:
            pass
    cennik = tracker.zbuduj_cennik(wszystko)
    if cennik:
        Path("cennik_cech.json").write_text(
            json.dumps(cennik, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"cennik przeliczony z {cennik['n_ofert']} ofert:")
        for v in cennik["cechy"].values():
            print(f"   {v['opis']}: {v['zmiana_ceny_pct']:+.1f}%  (n={v['n_ofert']})")
    else:
        print("za malo danych na cennik — zostawiam poprzedni")


if __name__ == "__main__":
    main()
