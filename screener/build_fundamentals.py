#!/usr/bin/env python3
"""
Construction du snapshot de fondamentaux d'éligibilité — CLI TwelveData.
=======================================================================
Interroge TwelveData pour chaque titre de l'univers et écrit un
`fundamentals.built.csv` consommé par `screener.run --fundamentals`. Comble la
capitalisation (socle S1, §3.1) laissée vide par `build_universe`, de sorte que
`run` puisse ADMETTRE des candidats sur un univers généré depuis SEC.

Nécessite `TWELVEDATA_API_KEY` (offre gratuite : ~8 req/min → régler `--sleep`).
Sans clé, écrit un fichier vide : la chaîne continue, mais S1 restera bloqué sur
la capitalisation tant que l'univers ne la porte pas.

⚠ TwelveData n'expose pas le nombre d'analystes : `analyst_coverage` (S1) reste
à renseigner dans l'univers.

Usage :
    export TWELVEDATA_API_KEY=xxx
    python -m screener.build_fundamentals
    python -m screener.build_fundamentals --universe u.csv --out f.csv --sleep 8
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from datetime import date
from typing import List

from .ingestion.fundamentals import fetch_fundamentals

_HERE = os.path.dirname(__file__)
_DATA = os.path.join(_HERE, "data")

_OUT_COLS = ["ticker", "market_cap", "price", "shares_outstanding",
             "fifty_two_week_low", "fifty_two_week_high", "name", "as_of"]


def _load_universe(path: str) -> List[dict]:
    with open(path) as f:
        return [r for r in csv.DictReader(f) if (r.get("ticker") or "").strip()]


def _num(x):
    return "" if x is None else x


def build(universe_path: str, out_path: str, as_of: date, base: str,
          sleep_s: float) -> int:
    universe = _load_universe(universe_path)
    api_key = os.getenv("TWELVEDATA_API_KEY", "")
    if not api_key:
        print("⚠ TWELVEDATA_API_KEY absent — snapshot vide (S1 restera bloqué sur "
              "la capitalisation tant que l'univers ne la porte pas).", file=sys.stderr)

    rows = []
    for i, u in enumerate(universe):
        tk = u["ticker"].strip().upper()
        symbol = (u.get("symbol") or tk).strip()
        f = fetch_fundamentals(symbol, api_key=api_key or None, base=base)
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
        if api_key and sleep_s and i < len(universe) - 1:
            time.sleep(sleep_s)

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=_OUT_COLS)
        w.writeheader()
        w.writerows(rows)

    filled = sum(1 for r in rows if r["market_cap"] != "")
    print(f"Snapshot fondamentaux (as_of={as_of.isoformat()}) : "
          f"{filled}/{len(rows)} titres avec capitalisation -> {out_path}")
    for r in rows:
        if r["market_cap"] != "":
            print(f"  {r['ticker']:<8} capi={r['market_cap']} cours={r['price']}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Snapshot de fondamentaux (TwelveData)")
    ap.add_argument("--universe", default=os.path.join(_DATA, "universe.sample.csv"))
    ap.add_argument("--out", default=os.path.join(_DATA, "fundamentals.built.csv"))
    ap.add_argument("--as-of", default=date.today().isoformat())
    ap.add_argument("--base", default=os.getenv("TWELVEDATA_BASE",
                                                "https://api.twelvedata.com"))
    ap.add_argument("--sleep", type=float, default=8.0,
                    help="pause entre titres (s) — l'offre gratuite plafonne à ~8 req/min")
    args = ap.parse_args(argv)

    try:
        as_of = date.fromisoformat(args.as_of)
    except ValueError:
        sys.exit(f"--as-of invalide : {args.as_of}")
    if not os.path.exists(args.universe):
        sys.exit(f"univers introuvable : {args.universe}")
    return build(args.universe, args.out, as_of, args.base, args.sleep)


if __name__ == "__main__":
    sys.exit(main())
