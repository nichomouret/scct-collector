#!/usr/bin/env python3
"""
Classification de news — CLI de la P4.
======================================
Pour chaque titre de l'univers : récupère les news 72h (NewsAPI) et les classe
via Claude (§5.3) en `cause_class` / `permanence` / `expected_resolution_days`.
Écrit un `news.built.csv` au format overlay, consommé par `screener.run --news`.
C'est ce qui automatise le remplissage des champs qualitatifs jusque-là saisis
à la main — le cœur du « premier livrable utilisable au quotidien » (§11, P4).

Nécessite `NEWS_API_KEY` (récupération) et `ANTHROPIC_API_KEY` (classification).
Sans l'un ou l'autre, écrit les lignes qu'il peut et laisse les autres vides ;
la chaîne retombe sur l'overlay manuel.

⚠️ Live / forward uniquement — voir la garde anti-fuite LLM (§10.3) dans
`qualification/news_classifier.py`. Ne pas utiliser pour peupler un backtest.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from typing import List

from .ingestion.news import fetch_news
from .qualification.news_classifier import classify_news, passes_gating

_HERE = os.path.dirname(__file__)
_DATA = os.path.join(_HERE, "data")

_OUT_COLS = ["ticker", "cause_class", "permanence", "expected_resolution_days",
             "cash_flow_impact_pct", "source_reliability", "confidence",
             "evidence_url", "gating"]


def _load_universe(path: str) -> List[dict]:
    with open(path) as f:
        return [r for r in csv.DictReader(f) if (r.get("ticker") or "").strip()]


def build(universe_path: str, out_path: str, sleep_s: float) -> int:
    universe = _load_universe(universe_path)
    if not os.getenv("NEWS_API_KEY"):
        print("⚠ NEWS_API_KEY absent — aucune news récupérée.", file=sys.stderr)
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("⚠ ANTHROPIC_API_KEY absent — aucune classification (overlay manuel requis).",
              file=sys.stderr)

    rows = []
    for i, u in enumerate(universe):
        tk = u["ticker"].strip().upper()
        name = (u.get("name") or tk).strip()
        query = f'"{name}"' if name and name != tk else tk
        items = fetch_news(query)
        cls = classify_news(tk, name, items)
        if cls is None:
            rows.append({c: "" for c in _OUT_COLS} | {"ticker": tk})
        else:
            row = cls.as_overlay_row(tk)
            row["gating"] = "oui" if passes_gating(cls) else "non"
            rows.append(row)
        if os.getenv("ANTHROPIC_API_KEY") and sleep_s and i < len(universe) - 1:
            time.sleep(sleep_s)

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_OUT_COLS)
        w.writeheader()
        w.writerows(rows)

    classified = sum(1 for r in rows if r.get("cause_class"))
    gated = sum(1 for r in rows if r.get("gating") == "oui")
    print(f"Classification news : {classified}/{len(rows)} titres classés, "
          f"{gated} passent le gating (§5.3) -> {out_path}")
    for r in rows:
        if r.get("cause_class"):
            print(f"  {r['ticker']:<8} {r['cause_class']:<18} {r['permanence']:<11} "
                  f"résol.~{r['expected_resolution_days']}j  gating={r.get('gating')}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Classification de news LLM (P4, §5.3)")
    ap.add_argument("--universe", default=os.path.join(_DATA, "universe.sample.csv"))
    ap.add_argument("--out", default=os.path.join(_DATA, "news.built.csv"))
    ap.add_argument("--sleep", type=float, default=1.0, help="pause entre titres (s)")
    args = ap.parse_args(argv)
    if not os.path.exists(args.universe):
        sys.exit(f"univers introuvable : {args.universe}")
    return build(args.universe, args.out, args.sleep)


if __name__ == "__main__":
    sys.exit(main())
