#!/usr/bin/env python3
"""
SCCT-Social · Générateur d'univers de tickers US (NASDAQ / NYSE / NYSE American=AMEX / Arca)
============================================================================================
Télécharge les répertoires officiels Nasdaq Trader et produit `tickers.txt`
(1 symbole majuscule par ligne) à pointer via TICKER_UNIVERSE_FILE pour que
l'extracteur reconnaisse TOUS les tickers, micro-caps comprises.

Sources (publiques, mises à jour quotidiennement) :
  - https://www.nasdaqtrader.com/dynamic/SymbolDirectory/nasdaqlisted.txt
  - https://www.nasdaqtrader.com/dynamic/SymbolDirectory/otherlisted.txt   (NYSE, AMEX, Arca…)

Usage :
    python build_ticker_universe.py                 # tout, hors test issues
    python build_ticker_universe.py --no-etf        # exclut les ETF
    python build_ticker_universe.py --exchanges A N Q   # filtre (A=AMEX, N=NYSE, Q/autre=Nasdaq)
    python build_ticker_universe.py --out tickers.txt

Sans réseau ? Télécharge les 2 fichiers à la main et lance :
    python build_ticker_universe.py --local nasdaqlisted.txt otherlisted.txt
"""
from __future__ import annotations
import argparse, sys, urllib.request

NASDAQ_URL = "https://www.nasdaqtrader.com/dynamic/SymbolDirectory/nasdaqlisted.txt"
OTHER_URL  = "https://www.nasdaqtrader.com/dynamic/SymbolDirectory/otherlisted.txt"
UA = {"User-Agent": "SCCT-Social ticker-universe builder"}

# Codes d'échange dans otherlisted.txt :
#   A = NYSE American (AMEX) · N = NYSE · P = NYSE Arca · Z = Cboe BZX · V = IEX
EXCHANGE_NAME = {"A": "NYSE American (AMEX)", "N": "NYSE", "P": "NYSE Arca",
                 "Z": "Cboe BZX", "V": "IEX"}


def _fetch(url: str) -> str:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def _rows(text: str) -> list[dict]:
    lines = [ln for ln in text.splitlines() if ln and not ln.startswith("File Creation Time")]
    if not lines:
        return []
    header = lines[0].split("|")
    out = []
    for ln in lines[1:]:
        parts = ln.split("|")
        if len(parts) != len(header):
            continue
        out.append(dict(zip(header, parts)))
    return out


def collect(nasdaq_txt: str, other_txt: str, *, drop_etf: bool,
            exchanges: set[str] | None) -> tuple[set[str], dict]:
    syms: set[str] = set()
    stats = {"nasdaq": 0, "other": 0, "etf_dropped": 0, "test_dropped": 0}

    for r in _rows(nasdaq_txt):
        if r.get("Test Issue", "N") == "Y":
            stats["test_dropped"] += 1; continue
        if drop_etf and r.get("ETF", "N") == "Y":
            stats["etf_dropped"] += 1; continue
        sym = (r.get("Symbol") or "").strip().upper()
        if sym and sym.isascii():
            syms.add(sym); stats["nasdaq"] += 1

    for r in _rows(other_txt):
        if r.get("Test Issue", "N") == "Y":
            stats["test_dropped"] += 1; continue
        if drop_etf and r.get("ETF", "N") == "Y":
            stats["etf_dropped"] += 1; continue
        exch = (r.get("Exchange") or "").strip().upper()
        if exchanges and exch not in exchanges:
            continue
        # 'ACT Symbol' = symbole de cotation ; fallback 'NASDAQ Symbol'
        sym = (r.get("ACT Symbol") or r.get("NASDAQ Symbol") or "").strip().upper()
        if sym and sym.isascii():
            syms.add(sym); stats["other"] += 1
    return syms, stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="tickers.txt")
    ap.add_argument("--no-etf", action="store_true", help="exclure les ETF")
    ap.add_argument("--exchanges", nargs="*", default=None,
                    help="filtre otherlisted : A N P Z V (défaut: tous)")
    ap.add_argument("--local", nargs=2, metavar=("NASDAQ", "OTHER"),
                    help="fichiers locaux au lieu du téléchargement")
    args = ap.parse_args()

    try:
        if args.local:
            nasdaq_txt = open(args.local[0]).read()
            other_txt = open(args.local[1]).read()
        else:
            print("Téléchargement nasdaqlisted.txt…", file=sys.stderr)
            nasdaq_txt = _fetch(NASDAQ_URL)
            print("Téléchargement otherlisted.txt…", file=sys.stderr)
            other_txt = _fetch(OTHER_URL)
    except Exception as e:
        sys.exit(f"Échec récupération sources : {e}\n"
                 f"Astuce : télécharge les 2 .txt à la main puis --local nasdaqlisted.txt otherlisted.txt")

    exch = set(x.upper() for x in args.exchanges) if args.exchanges else None
    syms, stats = collect(nasdaq_txt, other_txt, drop_etf=args.no_etf, exchanges=exch)

    with open(args.out, "w") as f:
        f.write("# SCCT-Social ticker universe — NASDAQ/NYSE/AMEX\n")
        f.write("# Régénérer avec build_ticker_universe.py\n")
        for s in sorted(syms):
            f.write(s + "\n")

    print(f"\n{len(syms)} tickers -> {args.out}")
    print(f"  nasdaq={stats['nasdaq']}  other={stats['other']}  "
          f"etf_exclus={stats['etf_dropped']}  test_exclus={stats['test_dropped']}")
    print(f"Pointe le collecteur dessus :  export TICKER_UNIVERSE_FILE={args.out}")


if __name__ == "__main__":
    main()
