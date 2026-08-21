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
import re
import sys
import time
from datetime import date
from pathlib import Path

import requests

import tracker

PLIK = Path("rynek_pl.jsonl")
H = {"User-Agent": "Mozilla/5.0", "Accept-Language": "pl-PL"}


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
            try:
                url = "https://www.olx.pl/sport-hobby/rowery/q-" + q.replace(" ", "-") + "/"
                html = requests.get(url, headers=H, timeout=25).text
            except Exception as e:
                print(f"  [{q}] blad listy: {e}")
                continue
            karty = tracker.olx_relevant_offers(
                q, {c["url"]: c["price"] for c in tracker.parse_olx_cards(html)})
            n = 0
            for u, cena in list(karty.items()):
                if n >= na_model or u in juz:
                    continue
                try:
                    strona = requests.get(u, headers=H, timeout=25).text
                except Exception:
                    continue
                rec = tracker._parse_detail_fields(strona)
                rec.update({"ts": dzis, "url": u, "cena": cena, "model": q})
                sm = re.search(r'"seller"[^{]*\{[^}]*"id":(\d+)', strona)
                if sm:
                    rec["sprzedawca"] = sm.group(1)   # do odsiania spamu sklepów
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
