#!/usr/bin/env python3
"""
Construction du registre de catalyseurs — CLI de la P1.
=======================================================
Agrège les fournisseurs gratuits (ClinicalTrials.gov + CSV manuel) en un
`catalysts.csv` normalisé, au format exact consommé par `screener.run`
(`--catalysts`). Aucun changement à `run.py` : c'est un remplacement drop-in du
`catalysts.sample.csv` maintenu à la main.

Usage :
    python -m screener.build_catalysts                       # univers d'exemple -> data/catalysts.built.csv
    python -m screener.build_catalysts --universe u.csv --manual m.csv --out c.csv
    python -m screener.build_catalysts --as-of 2026-01-15    # point-in-time (backtest)

ClinicalTrials.gov n'est interrogé que pour les titres santé/biotech (secteur
dans une liste, ou colonne `sponsor` renseignée) — sinon un nom comme « Apple »
ramènerait des études de santé sans rapport. `--all-clinical` force la requête.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import date
from typing import List

from .ingestion.catalysts import (
    CatalystFetchError, clinicaltrials_catalysts, csv_catalysts,
)
from .universe.catalyst_registry import CATALYST_MAX_DAYS, new_registry

_HERE = os.path.dirname(__file__)
_DATA = os.path.join(_HERE, "data")

_HEALTHCARE = {"BIOTECH", "BIOTECHNOLOGY", "PHARMA", "PHARMACEUTICALS",
               "HEALTHCARE", "SANTE", "SANTÉ", "HEALTH", "MEDTECH", "LIFE SCIENCES"}

_OUT_COLS = ["ticker", "catalyst_type", "date_expected", "date_certainty",
             "days_to_catalyst", "binary", "expected_move_pct", "power", "source_url"]


def _load_universe(path: str) -> List[dict]:
    with open(path) as f:
        return [r for r in csv.DictReader(f) if (r.get("ticker") or "").strip()]


def _is_healthcare(row: dict) -> bool:
    if (row.get("sponsor") or "").strip():
        return True
    return (row.get("sector") or "").strip().upper() in _HEALTHCARE


def build(universe_path: str, manual_path: str, out_path: str, as_of: date,
          all_clinical: bool, max_days: int) -> int:
    universe = _load_universe(universe_path)
    reg = new_registry()
    errors: List[str] = []
    clinical_hits = 0

    for row in universe:
        tk = row["ticker"].strip().upper()
        if all_clinical or _is_healthcare(row):
            sponsor = (row.get("sponsor") or row.get("name") or "").strip()
            if sponsor:
                try:
                    raws = clinicaltrials_catalysts(sponsor, tk, as_of=as_of)
                    reg.extend(raws)
                    clinical_hits += len(raws)
                except CatalystFetchError as e:
                    errors.append(str(e))

    if manual_path and os.path.exists(manual_path):
        reg.extend(csv_catalysts(manual_path, as_of=as_of))

    rows = reg.to_rows(as_of=as_of, max_days=max_days)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_OUT_COLS)
        w.writeheader()
        w.writerows(rows)

    print(f"Registre construit (as_of={as_of.isoformat()}, fenêtre <= {max_days}j) :")
    print(f"  {len(reg.raws)} catalyseurs bruts (dont {clinical_hits} ClinicalTrials.gov)")
    print(f"  {len(rows)} titres avec catalyseur éligible -> {out_path}")
    for r in rows:
        print(f"    {r['ticker']:<8} {r['catalyst_type']:<18} {r['date_expected']} "
              f"(J-{r['days_to_catalyst']}, {r['power']})")
    if errors:
        print("  Erreurs fournisseurs :")
        for e in errors:
            print(f"    ! {e}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Construction du registre de catalyseurs (P1)")
    ap.add_argument("--universe", default=os.path.join(_DATA, "universe.sample.csv"))
    ap.add_argument("--manual", default=os.path.join(_DATA, "catalysts.sample.csv"),
                    help="CSV de catalyseurs datés à la main (résultats, réglementaire, indices)")
    ap.add_argument("--out", default=os.path.join(_DATA, "catalysts.built.csv"))
    ap.add_argument("--as-of", default=date.today().isoformat(),
                    help="date de référence PIT (YYYY-MM-DD) pour days_to_catalyst")
    ap.add_argument("--max-days", type=int, default=CATALYST_MAX_DAYS)
    ap.add_argument("--all-clinical", action="store_true",
                    help="interroger ClinicalTrials.gov pour tous les titres (pas seulement santé)")
    args = ap.parse_args(argv)

    try:
        as_of = date.fromisoformat(args.as_of)
    except ValueError:
        sys.exit(f"--as-of invalide : {args.as_of} (attendu YYYY-MM-DD)")
    if not os.path.exists(args.universe):
        sys.exit(f"univers introuvable : {args.universe}")
    return build(args.universe, args.manual, args.out, as_of,
                 args.all_clinical, args.max_days)


if __name__ == "__main__":
    sys.exit(main())
