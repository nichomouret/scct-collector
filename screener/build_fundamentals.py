#!/usr/bin/env python3
"""
Construction du snapshot de fondamentaux d'éligibilité (capitalisation, S1 §3.1).
================================================================================
Comble la capitalisation laissée vide par `build_universe`, pour que `run` puisse
ADMETTRE des candidats. Écrit un `fundamentals.built.csv` (drop-in `run --fundamentals`).

Deux sources :
  • `sec` (DÉFAUT, SANS CLÉ) : nombre d'actions via SEC XBRL × cours Yahoo
    (déjà utilisé par le pipeline). Fonctionne pour tout le monde, US uniquement.
    ⚠ TwelveData en offre gratuite ne donne PAS la capitalisation (/statistics
    est réservé aux plans pro+), d'où ce choix par défaut.
  • `twelvedata` : endpoint /statistics (nécessite TWELVEDATA_API_KEY + plan pro).

⚠ Aucune source ici ne fournit le nombre d'analystes : `analyst_coverage` (S1)
reste à renseigner dans l'univers.

Usage :
    python -m screener.build_fundamentals                       # source SEC (sans clé)
    python -m screener.build_fundamentals --universe u.csv --out f.csv
    python -m screener.build_fundamentals --source twelvedata   # si plan pro
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from datetime import date
from typing import List

from .ingestion.fundamentals import (
    fetch_fundamentals, load_cik_map, sec_fundamentals,
)
from .ingestion.prices import PriceFetchError, load_bars

_HERE = os.path.dirname(__file__)
_DATA = os.path.join(_HERE, "data")

_OUT_COLS = ["ticker", "market_cap", "price", "shares_outstanding",
             "fifty_two_week_low", "fifty_two_week_high", "name", "as_of"]


def _load_universe(path: str) -> List[dict]:
    with open(path) as f:
        return [r for r in csv.DictReader(f) if (r.get("ticker") or "").strip()]


def _num(x):
    return "" if x is None else x


def _last_price(symbol: str, cache_dir: str, rng: str, offline: bool):
    try:
        bars = load_bars(symbol, cache_dir=cache_dir, rng=rng, offline=offline)
        return bars[-1].close if bars else None
    except PriceFetchError:
        return None


def build(universe_path: str, out_path: str, as_of: date, source: str, base: str,
          cache_dir: str, rng: str, offline: bool, sleep_s: float) -> int:
    universe = _load_universe(universe_path)

    cik_map = {}
    if source == "sec":
        cik_map = load_cik_map()
        if not cik_map:
            print("⚠ table ticker→CIK SEC indisponible (réseau ?) — capitalisation vide.",
                  file=sys.stderr)
    elif source == "twelvedata" and not os.getenv("TWELVEDATA_API_KEY"):
        print("⚠ TWELVEDATA_API_KEY absent — snapshot vide.", file=sys.stderr)

    rows = []
    for i, u in enumerate(universe):
        tk = u["ticker"].strip().upper()
        symbol = (u.get("symbol") or tk).strip()
        if source == "twelvedata":
            f = fetch_fundamentals(symbol, base=base)
        else:
            price = _last_price(symbol, cache_dir, rng, offline)
            f = sec_fundamentals(tk, price, cik_map, name=u.get("name", ""),
                                 as_of=as_of.isoformat())
        rows.append({
            "ticker": tk,
            "market_cap": _num(f.market_cap),
            "price": _num(f.price),
            "shares_outstanding": _num(f.shares_outstanding),
            "fifty_two_week_low": _num(f.fifty_two_week_low),
            "fifty_two_week_high": _num(f.fifty_two_week_high),
            "name": f.name,
            "as_of": as_of.isoformat(),
        })
        if source == "twelvedata" and sleep_s and i < len(universe) - 1:
            time.sleep(sleep_s)

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=_OUT_COLS)
        w.writeheader()
        w.writerows(rows)

    filled = sum(1 for r in rows if r["market_cap"] != "")
    print(f"Snapshot fondamentaux [{source}] (as_of={as_of.isoformat()}) : "
          f"{filled}/{len(rows)} titres avec capitalisation -> {out_path}")
    for r in rows:
        if r["market_cap"] != "":
            mc = float(r["market_cap"])
            print(f"  {r['ticker']:<8} capi={mc/1e6:,.0f} M  cours={r['price']}")
    if filled == 0 and source == "sec":
        print("  (0 rempli : titres hors US ? sans cache prix ? relance sans --offline)",
              file=sys.stderr)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Snapshot de fondamentaux (capi S1)")
    ap.add_argument("--universe", default=os.path.join(_DATA, "universe.sample.csv"))
    ap.add_argument("--out", default=os.path.join(_DATA, "fundamentals.built.csv"))
    ap.add_argument("--source", choices=("sec", "twelvedata"), default="sec",
                    help="sec = sans clé (défaut) ; twelvedata = plan pro requis")
    ap.add_argument("--as-of", default=date.today().isoformat())
    ap.add_argument("--base", default=os.getenv("TWELVEDATA_BASE",
                                                "https://api.twelvedata.com"))
    ap.add_argument("--cache-dir", default=os.path.join(_HERE, ".cache"))
    ap.add_argument("--range", default="1y", help="fenêtre Yahoo pour le dernier cours")
    ap.add_argument("--offline", action="store_true", help="cache prix uniquement")
    ap.add_argument("--sleep", type=float, default=8.0,
                    help="pause entre titres (source twelvedata, offre gratuite ~8/min)")
    args = ap.parse_args(argv)

    try:
        as_of = date.fromisoformat(args.as_of)
    except ValueError:
        sys.exit(f"--as-of invalide : {args.as_of}")
    if not os.path.exists(args.universe):
        sys.exit(f"univers introuvable : {args.universe}")
    return build(args.universe, args.out, as_of, args.source, args.base,
                 args.cache_dir, args.range, args.offline, args.sleep)


if __name__ == "__main__":
    sys.exit(main())
