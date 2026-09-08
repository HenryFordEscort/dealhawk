"""Przelicza topowe_modele.json na aktualnych danych z market.jsonl.

Odpowiednik sprawdz_silniki.py, tylko dla hierarchii modeli. Wiedza o sprzecie
starzeje sie z rocznikami, wiec plik ma byc przeliczalny, a nie wykuty raz.

    python sprawdz_modele.py            # pokaz, co by sie zmienilo
    python sprawdz_modele.py --zapisz   # przepisz topowe_modele.json

Kod wyjscia 1, gdy przeliczenie rozni sie od pliku - zeby zadanie cykliczne
mialo na czym sie zatrzymac.

Wszystko, co tu wchodzi, jest POLICZONE. Jedyne liczby wziete z reki to progi
pieter, i sa w pliku oznaczone jako ZALOZONE.
"""
import argparse
import collections
import json
import re
import statistics as st
import sys
from datetime import date

import tracker as T

BATERIE = set(str(x) for x in T.POJEMNOSCI_BATERII) | {"400","440","480","504","540","650","696"}
STOP = {
    "e","ebike","bike","mtb","emtb","fully","hybrid","hardtail","gr","gr.","größe","grosse",
    "neu","neuwertig","top","zustand","wie","sehr","gut","guter","gebraucht","damen","herren",
    "mit","und","für","von","der","die","das","in","zu","inkl","kein","ohne","nur","ca",
    "bosch","yamaha","shimano","brose","bafang","performance","cx","line","smart","system",
    "wh","nm","km","zoll","cm","watt","w","akku","batterie","motor","rahmen","rh",
    "s","m","l","xl","xs","xxl","29","27,5","275","28","26","electric","elektro","pedelec",
    "verkaufe","biete","suche","preis","vb","festpreis","versand","abholung","store","b",
    # niemieckie slowa pospolite w tytulach - "fahrrad" zrobil z siebie
    # "topowy model Haibike" o medianie 3799 EUR przy pierwszym pomiarze
    "fahrrad","fahrräder","rad","mountainbike","mtb.","bike.","zoll","gang","gänge",
    "wie","neuwertig","neuwertiges","zustand","original","garantie","rechnung",
    "kinder","damenrad","herrenrad","trekking","city","cross","tour","touren",
}
def czy_smiec(tok: str) -> bool:
    t = tok.strip(".,;:!?-–/()\"'").lower()
    if not t or t in STOP: return True
    if t in BATERIE: return True
    if re.fullmatch(r'20[0-2]\d', t): return True          # rocznik
    if re.fullmatch(r'\d{4,}', t): return True             # przebieg, UVP, kod sklepu
    if re.fullmatch(r'i?\d+\s*wh', t): return True         # '720wh', 'i800wh' to bateria
    if re.fullmatch(r'2[6-9][.,]?\d?', t): return True     # rozmiar kola: 27.5, 29
    # OZNACZENIA SILNIKOW udaja modele. "haibike pw" wyszlo jako model
    # o medianie z gornej polki, a to Yamaha PW-X2 - czyli marker rywala,
    # przez ktory bot te rowery i tak odrzuca. Zlapane 08.09.2026 przy
    # przeliczeniu na swiezych danych.
    if re.fullmatch(r'pw(?:[-\s]?(?:x2?|se|st|te))?|cx|sx|ep8|ep801|steps|e8000', t):
        return True
    # "hard" to uciete "Hard Cross" (Husqvarna). Nie wpuszczamy, bo jeden znak
    # dzieli je od "hardtail", a is_fully odrzuca hardtaile z rozmyslem.
    if t in ('hard', 'cross', 'trekking', 'nine', 'seven'): return True
    if re.fullmatch(r'[\d.,]+', t) and len(t) <= 2: return True
    return False

def wczytaj():
    po_id = {}
    for l in open('market.jsonl'):
        try: r = json.loads(l)
        except Exception: continue
        if isinstance(r.get('p'), int) and r.get('t'): po_id[r['id']] = r
    widz, rowery = set(), []
    for r in po_id.values():                                # regula 5: rowery, nie ogloszenia
        od = (T._tytul_znormalizowany(r['t']), r['p'])
        if od in widz: continue
        widz.add(od); rowery.append(r)
    return [r for r in rowery if T.is_electric(r['t'])]

MARKI = ["cube","trek","ktm","haibike","bulls","specialized","giant","conway","focus",
         "scott","riese","winora","ghost","corratec","centurion","canyon","orbea",
         "bergamont","stevens","husqvarna","mondraker","cannondale","simplon","nox",
         "merida","lapierre","rotwild","flyer","kalkhoff","raymon","moustache","fantic"]

MIN_ROWEROW    = 12     # ten sam prog co ROZRZUT_MIN_ROWEROW w tracker.py
MIN_NA_MARKE   = 60
MIN_FULLY_PCT  = 50
TOLERANCJA     = 0.15   # mediany blizej siebie = ten sam rower opisany inaczej
PROGI = [(1.60, "szczyt"), (1.30, "wysoka"), (1.15, "gorna_polka")]
RYWALE = ("yamaha", "shimano", "brose", "bafang", "fazua", "panasonic")


def frazy(elek, marka):
    wz = re.compile(r"\b" + re.escape(marka) + r"\b(.*)", re.I)
    c, dane = collections.Counter(), collections.defaultdict(list)
    for r in elek:
        m = wz.search(r["t"])
        if not m:
            continue
        toks = [x for x in re.split(r"[\s\-–/|]+", m.group(1)) if not czy_smiec(x)]
        for ile in (2, 1):
            if len(toks) < ile:
                continue
            f = " ".join(t.strip(".,;:!?()\"'").lower() for t in toks[:ile])
            if f:
                c[f] += 1
                dane[f].append(r)
    return c, dane


def pietro(x):
    for prog, nazwa in PROGI:
        if x >= prog:
            return nazwa
    return None


def _klucz(m):
    return m.replace(" ", "").replace("-", "")


def _dodatek_liczbowy(dluzszy, krotszy):
    """Czy dluzsza nazwa rozni sie od krotszej samym NUMEREM modelu.

    Powod tej funkcji: bez niej "cube stereo 160" wpadalo do "cube stereo"
    (mediany 2499 i 2350, roznica 6%, wiec w tolerancji) i znikalo z listy.
    A "160" to nie ozdobnik, tylko cala roznica miedzy szczytem starych Cubow
    a podstawowym 120. "evo" po "sonic" wolno pochlonac, "160" po "stereo" nie.
    """
    ogon = _klucz(dluzszy)[len(_klucz(krotszy)):]
    return bool(re.search(r"\d", ogon))


def wzorzec(model: str) -> str:
    """Nazwa modelu na wyrazenie regularne. Cztery rzeczy, kazda kosztowala
    jeden zly pomiar przy budowie tego pliku:

    1. `re.escape` na kazdym czlonie. Bez tego "powerfly+" znaczy w regexpie
       "powerfl i co najmniej jedno y", wiec zwykly Trek Powerfly 4 (mediana
       1900 EUR) wchodzil jako topowy Powerfly+ (3299 EUR).
    2. LUKA miedzy czlonami, nie sama spacja. Nazwy skladam z tokenow po
       wyrzuceniu slow pospolitych, wiec "stereo one44" pochodzi ze "Stereo
       HYBRID ONE44" - miedzy czlonami stoi slowo. Wzorzec z "[\\s-]*"
       nie trafial w NIC: 0 dopasowan na 811 ofertach.
    3. LUZ miedzy litera a cyfra wewnatrz czlonu. Sprzedawcy pisza i "ONE44",
       i "ONE 44", i "ONE-44".
    4. Indeks gorny i zwykla dwojka to to samo: "Thron²" obok "Thron2".
       Podmiana MUSI byc jednym przejsciem - dwa kolejne `replace` wchodza
       sobie w wynik i robia z "sam²" wzorzec "sam[²[²2]]", ktory nie trafia
       juz w nic. CLAUDE.md notuje 72 ogloszenia Focusa zgubione na tej
       jednej granicy.
    """
    def czlon(w):
        # rozbij na przemienne ciagi liter i cyfr: "one44" -> ["one","44"]
        kawalki = re.findall(r"[^\W\d_]+|\d+|[^\w\s]+", w, re.UNICODE)
        wynik = []
        for k in kawalki:
            k = re.escape(k)
            k = re.sub(r"²|2", "[²2]", k)      # JEDNO przejscie, patrz punkt 4
            wynik.append(k)
        return r"[\s-]*".join(wynik)
    return r"[\s\S]{0,24}?".join(czlon(x) for x in model.split()) + r"(?![a-z0-9])"


def silnik(rr):
    """Ile razy przy tej rodzinie pada Bosch, a ile rywal. Ta sama droga co
    sprawdz_silniki.py: liczymy, co sprzedawcy NAPISALI, nie co pamietamy."""
    b = sum(1 for r in rr if "bosch" in r["t"].lower())
    ryw = collections.Counter()
    for r in rr:
        t = r["t"].lower()
        for m in RYWALE:
            if m in t:
                ryw[m] += 1
    return b, dict(ryw)


def werdykt_silnika(b, ryw, marka, model, odrzucone):
    # Specialized ma WLASNY silnik i twarde ograniczenie repo dopuszcza go
    # wprost ("tylko Bosch plus wlasny silnik Specialized"). has_known_motor
    # przepuszcza go przez _SPEC_WZ, bez slowa "Bosch" w tekscie - sprawdzone.
    # Bez tego wyjatku caly Specialized wychodzil "za malo danych o silniku",
    # czyli plik odradzalby rowery, ktore bot kupuje od zawsze.
    if marka == "specialized":
        return "specialized", "wlasny silnik Specialized - dopuszczony wprost"
    for (mk, ml), czemu in odrzucone.items():
        if mk == marka and (ml in model or model in ml):
            return "nie_bosch", f"silniki_bosch.json: {czemu}"
    suma = sum(ryw.values())
    if b == 0 and suma > 0:
        return "nie_bosch", f"ani jednego Boscha, rywale {suma}x ({', '.join(ryw)})"
    if suma > b:
        return "mieszany", f"Bosch {b}x, rywale {suma}x ({', '.join(ryw)}) - przewaga rywali"
    if b >= 5 and suma == 0:
        return "bosch", f"Bosch {b}x, zero rywali"
    if b > suma:
        return "mieszany", f"Bosch {b}x, rywale {suma}x"
    return "za_malo_danych", f"Bosch {b}x, rywale {suma}x"


def odrzucone_silniki():
    try:
        d = json.load(open("silniki_bosch.json", encoding="utf-8"))
    except Exception:
        return {}
    return {(w["marka"], w["model"]): w.get("czemu", "")
            for w in d.get("_ODRZUCONE_ZMIERZONYM_POMIAREM", []) if isinstance(w, dict)}


def main(zapisz=False):
    elek = wczytaj()
    daty = sorted(r["ts"] for r in elek)
    nie_bosch = odrzucone_silniki()

    surowe = []
    for marka in MARKI:
        wsz = [r for r in elek if re.search(r"\b" + re.escape(marka) + r"\b", r["t"], re.I)]
        if len(wsz) < MIN_NA_MARKE:
            continue
        med_marki = st.median([r["p"] for r in wsz])
        c, dane = frazy(elek, marka)
        for f, n in c.items():
            if n >= MIN_ROWEROW:
                surowe.append({"marka": marka, "model": f, "rr": dane[f], "n": n,
                               "med": st.median([r["p"] for r in dane[f]]),
                               "med_marki": med_marki})

    # Dedup po prefiksie nazwy. Krotsza fraza pochlania dluzsza, gdy mediany
    # stoja blisko - ale NIGDY, gdy roznica to numer modelu (patrz wyzej).
    wg = collections.defaultdict(list)
    for w in surowe:
        wg[w["marka"]].append(w)
    zostaw = []
    for lista in wg.values():
        for w in lista:
            k = _klucz(w["model"])
            pochloniety = False
            for inny in lista:
                if inny is w:
                    continue
                ki = _klucz(inny["model"])
                if not (k.startswith(ki) or ki.startswith(k)):
                    continue
                if abs(w["med"] - inny["med"]) / max(w["med"], inny["med"]) > TOLERANCJA:
                    continue
                dluzszy, krotszy = (k, ki) if len(k) > len(ki) else (ki, k)
                if _dodatek_liczbowy(dluzszy, krotszy):
                    continue
                if (inny["n"], ki) > (w["n"], k):
                    pochloniety = True
                    break
            # UCIETY NUMER. "cube stereo one" powstalo z "Stereo Hybrid ONE 44",
            # gdzie tokenizer wyrzucil "44" jako liczbe dwucyfrowa. Ale samo
            # "ONE" to u Cube'a POZIOM WYPOSAZENIA, nie model: "Stereo Hybrid
            # 120 ONE 625" to podstawowy 120 z bateria 625 Wh. Wpis bez numeru
            # dostawal przez to pietro "wysoka" i wpuszczal bazowe rowery na
            # kanal najlepszych - zlapane 02.09 na prawdziwym powiadomieniu.
            # Warunek: sa co najmniej DWA wpisy tej marki bedace ta sama nazwa
            # plus cyfry. Jeden moglby byc przypadkiem, dwa to juz rodzina.
            numerowane = sum(1 for inny in lista
                             if inny is not w
                             and re.fullmatch(re.escape(k) + r"\d+", _klucz(inny["model"])))
            if numerowane >= 2:
                continue
            if not pochloniety:
                zostaw.append(w)

    wpisy, nie_fully = [], []
    for w in zostaw:
        x = round(w["med"] / w["med_marki"], 2)
        p = pietro(x)
        if not p:
            continue
        rr = w["rr"]
        fully_pct = round(sum(1 for r in rr if T.is_fully(r["t"])) / w["n"] * 100)
        kms = [r["km"] for r in rr if r.get("km") is not None]
        b, ryw = silnik(rr)
        werdykt, czemu = werdykt_silnika(b, ryw, w["marka"], w["model"], nie_bosch)
        rec = {
            "marka": w["marka"], "model": w["model"], "pietro": p,
            # Wzorzec budowany DOKLADNIE jak _wz_frazy w tracker.py, ze
            # znakami uciekniętymi. Bez re.escape "powerfly+" znaczy w regexpie
            # "powerfl i co najmniej jedno y", wiec zwykly Trek Powerfly 4
            # (mediana 1900 EUR) wchodzil jako topowy Powerfly+ (3299 EUR).
            # Ten sam blad zjadlby "stance e+1" i kazdy model z "+" w nazwie.
            "wz": wzorzec(w["model"]),
            "rowerow": w["n"], "mediana": int(w["med"]),
            "mediana_marki": int(w["med_marki"]), "x_marka": x,
            "fully_pct": fully_pct,
            "w_widelkach": sum(1 for r in rr if 800 <= r["p"] <= 3000),
            "km_mediana": int(st.median(kms)) if len(kms) >= 5 else None,
            "silnik": werdykt, "silnik_czemu": czemu,
        }
        rec["w_widelkach_pct"] = round(rec["w_widelkach"] / w["n"] * 100)
        (wpisy if fully_pct >= MIN_FULLY_PCT else nie_fully).append(rec)

    kolejnosc = {"bosch": 0, "specialized": 0, "mieszany": 1,
                 "za_malo_danych": 2, "nie_bosch": 3}
    wpisy.sort(key=lambda w: (kolejnosc[w["silnik"]], -w["x_marka"]))
    nie_fully.sort(key=lambda w: -w["x_marka"])

    out = {
      "_JAK_TO_CZYTAC": [
        "Modele z GORNEJ POLKI swojej marki. Sluza do wyboru ofert na kanal",
        "'najlepsze'. NIE do wyceny i NIE do odrzucania czegokolwiek - rower",
        "spoza tej listy idzie dalej normalna sciezka DealHawka, tak jak dzis.",
        "",
        "'x_marka' = mediana ceny modelu / mediana calej marki. 1.95 znaczy:",
        "ten model kosztuje prawie dwa razy tyle co typowy rower tej marki.",
        "ZMIERZONE z ogloszen, nie przepisane z katalogu producenta.",
        "",
        "'pietro' to etykieta, a progi do niej sa ZALOZONE - patrz _PROGI_PIETER.",
        "Mozesz je przesunac i przeliczyc, liczby pod spodem sie nie zmienia.",
        "",
        "'silnik' mowi, czy bot w ogole moze taki rower kupic. 'nie_bosch' znaczy,",
        "ze filtr odrzuci go wczesniej; 'specialized' to wlasny silnik marki,",
        "dopuszczony przez twarde ograniczenie repo.",
        "ze filtr silnika odrzuci go WCZESNIEJ i wpis nie da ani jednego",
        "powiadomienia - zostaje na liscie po to, zebys wiedzial, ze to nie",
        "przeoczenie. Liczone tak samo jak w sprawdz_silniki.py: co sprzedawcy",
        "NAPISALI, nie co komukolwiek sie wydaje.",
        "",
        "Marka I model musza trafic naraz, nigdy sam model: 'Wild', 'Sonic',",
        "'Riot', 'Trance', 'Crafty' i 'Strike' to zwykle slowa. Ta sama zasada",
        "co w silniki_bosch.json.",
        "",
        "Mozesz poprawiac: dopisac model, usunac blednu, zmienic pietro."
      ],
      "_ZMIERZONE": (f"{date.today().isoformat()} na {len(elek)} elektrykach "
                     f"(rowery, nie ogloszenia - regula 5) z market.jsonl, "
                     f"okno {daty[0]}..{daty[-1]}"),
      "_PROG_WEJSCIA": {"min_rowerow": MIN_ROWEROW, "min_fully_pct": MIN_FULLY_PCT,
                        "min_rowerow_na_marke": MIN_NA_MARKE},
      "_PROGI_PIETER": {"_UWAGA": "ZALOZONE, nie zmierzone - do przesuwania",
                        "szczyt": ">= 1.60 x mediany marki", "wysoka": ">= 1.30",
                        "gorna_polka": ">= 1.15"},
      "_PULAPKI": [
        "Liczba po nazwie bywa BATERIA, nie modelem: 'Reaction Hybrid ONE 625',",
        "'ONE 750', 'ONE 800'. Pierwszy pomiar zrobil z tego modele ONE62, ONE75",
        "i ONE80, ktore nie istnieja. Kazda liczba z POJEMNOSCI_BATERII jest tu",
        "odsiewana, tak samo roczniki i przebiegi. Ta sama pulapka co usuniety",
        "'RockShox 30' w wiedza_sprzet.json.",
        "",
        "Niemieckie slowa pospolite udaja modele: 'Fahrrad' wyszlo przy pierwszym",
        "przemiale jako topowy model Haibike o medianie 3799 EUR.",
        "",
        "Konce frazy pisz jak _wz_frazy w tracker.py: '(?![a-z0-9])', nie \\b.",
        "Focus pisze 'Thron2' i 'Jam2' z indeksem gornym, a dla Pythona to",
        "znak slowa.",
        "",
        "Numer po nazwie rodziny to CALA roznica miedzy pietrami: 'stereo 160'",
        "nie wolno scalac ze 'stereo', choc mediany dzieli 6%."
      ],
      "topowe": wpisy,
      "_ODRZUCONE_BO_CHYBA_NIE_FULLY": [
        "Cenowo na gornej polce swojej marki, ale wiekszosc ogloszen nie wyglada",
        "na fully wg is_fully(). Czesc to naprawde hardtaile i trekkingi (KTM",
        "Macina Aera), a czesc to prawdziwe fully, ktorych nazw po prostu nie ma",
        "w FULLY_KEYWORDS (KTM Macina Prowler, Specialized Kenevo). Przeniesienie",
        "wpisu do 'topowe' NIE WYSTARCZY, zeby taki rower doszedl - is_fully",
        "odrzuca go wczesniej, w samym DealHawku."
      ] + nie_fully,
    }
    ile = collections.Counter(w["silnik"] for w in wpisy)
    print(f"przeliczone: {len(wpisy)} modeli fully ({dict(ile)}), "
          f"{len(nie_fully)} odrzuconych na fully")

    sciezka = "topowe_modele.json"
    stare = {}
    try:
        stare = {(w["marka"], w["model"]): w
                 for w in json.load(open(sciezka, encoding="utf-8")).get("topowe", [])}
    except Exception:
        pass
    nowe = {(w["marka"], w["model"]): w for w in wpisy}

    doszly = sorted(nowe.keys() - stare.keys())
    znikly = sorted(stare.keys() - nowe.keys())
    zmiana_pietra = [(k, stare[k]["pietro"], nowe[k]["pietro"])
                     for k in sorted(nowe.keys() & stare.keys())
                     if stare[k]["pietro"] != nowe[k]["pietro"]]
    # Zmiana werdyktu o silniku jest wazniejsza od zmiany pietra: model, ktory
    # przestal byc Boschem, przestaje dawac powiadomienia w ogole.
    zmiana_silnika = [(k, stare[k].get("silnik"), nowe[k].get("silnik"))
                      for k in sorted(nowe.keys() & stare.keys())
                      if stare[k].get("silnik") != nowe[k].get("silnik")]

    for tytul, lista in (("DOSZLY", doszly), ("ZNIKLY", znikly)):
        for k in lista:
            zr = nowe.get(k) or stare[k]
            print(f"  {tytul}: {k[0]} {k[1]} "
                  f"(pietro {zr.get('pietro')}, {zr.get('rowerow')} rowerow)")
    for k, a, b in zmiana_pietra:
        print(f"  PIETRO: {k[0]} {k[1]}: {a} -> {b}")
    for k, a, b in zmiana_silnika:
        print(f"  SILNIK: {k[0]} {k[1]}: {a} -> {b}")

    rozne = bool(doszly or znikly or zmiana_pietra or zmiana_silnika)
    if not rozne:
        print("  bez zmian - plik nadal sie broni")

    if zapisz:
        json.dump(out, open(sciezka, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        print(f"  zapisane do {sciezka}")
        return 0
    if rozne:
        print("\n  (nic nie zapisano - uruchom z --zapisz)")
    return 1 if rozne else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Przelicza topowe_modele.json na aktualnym market.jsonl")
    ap.add_argument("--zapisz", action="store_true",
                    help="przepisz plik (domyslnie tylko pokazuje roznice)")
    sys.exit(main(zapisz=ap.parse_args().zapisz))
