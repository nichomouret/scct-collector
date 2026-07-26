#!/usr/bin/env python3
"""
Construction du snapshot short interest / emprunt — CLI Ortex.
=============================================================
Interroge Ortex pour chaque titre de l'univers et écrit un `short_interest.built.csv`
consommé par `screener.run --short-interest`. Alimente les confirmateurs §4.3
(saut du taux d'emprunt, utilisation du float) et la section POSITIONNEMENT de
la fiche (§7.5).

Nécessite `ORTEX_API_KEY` (et éventuellement `ORTEX_BASE` pour un autre segment
de marché — défaut : .../stock/us ; à ADAPTER pour l'Europe selon le plan Ortex).
Sans clé, écrit un fichier vide : la chaîne continue, DIS retombe sur les
confirmateurs de prix.

Usage :
    export ORTEX_API_KEY=xxx
    python -m screener.build_short_interest
    python -m screener.build_short_interest --universe u.csv --out si.csv --sleep 8
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from datetime import date
from typing import List

from .ingestion.short_interest import fetch_ortex_signals

_HERE = os.path.dirname(__file__)
_DATA = os.path.join(_HERE, "data")

_OUT_COLS = ["ticker", "short_interest_pct", "borrow_fee", "float_utilization",
             "borrow_jump_bps_3d", "days_to_cover", "as_of"]


def _load_universe(path: str) -> List[dict]:
    with open(path) as f:
        return [r for r in csv.DictReader(f) if (r.get("ticker") or "").strip()]


def build(universe_path: str, out_path: str, as_of: date, base: str,
          skip_ctb: bool, sleep_s: float) -> int:
    universe = _load_universe(universe_path)
    api_key = os.getenv("ORTEX_API_KEY", "")
    if not api_key:
        print("⚠ ORTEX_API_KEY absent — snapshot vide (DIS retombera sur les "
              "confirmateurs de prix).", file=sys.stderr)

    rows = []
    for i, u in enumerate(universe):
        tk = u["ticker"].strip().upper()
        sig = fetch_ortex_signals(tk, api_key=api_key or None, base=base,
                                  as_of=as_of, skip_ctb=skip_ctb)
        rows.append({
            "ticker": tk,
            "short_interest_pct": _num(sig.short_interest_pct),
            "borrow_fee": _num(sig.borrow_fee),
            "float_utilization": _num(sig.float_utilization),
            "borrow_jump_bps_3d": _num(sig.borrow_jump_bps_3d),
            "days_to_cover": _num(sig.days_to_cover),
            "as_of": sig.as_of,
        })
        if api_key and sleep_s and i < len(universe) - 1:
            time.sleep(sleep_s)

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_OUT_COLS)
        w.writeheader()
        w.writerows(rows)

    filled = sum(1 for r in rows if r["short_interest_pct"] != "")
    print(f"Snapshot short interest (as_of={as_of.isoformat()}) : "
          f"{filled}/{len(rows)} titres renseignés -> {out_path}")
    for r in rows:
        if r["short_interest_pct"] != "" or r["borrow_jump_bps_3d"] != "":
            print(f"  {r['ticker']:<8} SI={r['short_interest_pct']} "
                  f"util={r['float_utilization']} borrow={r['borrow_fee']} "
                  f"jump={r['borrow_jump_bps_3d']}bps")
    return 0


def _num(x):
    return "" if x is None else x


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Snapshot short interest / emprunt (Ortex)")
    ap.add_argument("--universe", default=os.path.join(_DATA, "universe.sample.csv"))
    ap.add_argument("--out", default=os.path.join(_DATA, "short_interest.built.csv"))
    ap.add_argument("--as-of", default=date.today().isoformat())
    ap.add_argument("--base", default=os.getenv("ORTEX_BASE",
                                                "https://api.ortex.com/api/v1/stock/us"))
    ap.add_argument("--skip-ctb", action="store_true",
                    help="ne pas interroger l'endpoint cost-to-borrow (économise des appels)")
    ap.add_argument("--sleep", type=float, default=1.0, help="pause entre titres (s)")
    args = ap.parse_args(argv)

    try:
        as_of = date.fromisoformat(args.as_of)
    except ValueError:
        sys.exit(f"--as-of invalide : {args.as_of}")
    if not os.path.exists(args.universe):
        sys.exit(f"univers introuvable : {args.universe}")
    return build(args.universe, args.out, as_of, args.base, args.skip_ctb, args.sleep)


if __name__ == "__main__":
    sys.exit(main())
