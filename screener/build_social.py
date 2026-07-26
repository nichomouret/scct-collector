#!/usr/bin/env python3
"""
Tendance sociale — CLI Adanos (contexte de qualification).
==========================================================
Pour chaque titre de l'univers : récupère la tendance sociale X/Twitter via
Adanos et écrit un `social.built.csv` au format overlay, consommé par
`screener.run --social` et par `build_news` (indice de cause pour le LLM).

RAPPEL ARCHITECTURAL : le social est un CONTEXTE, jamais un signal de scoring
(SPEC v1.1 — social retiré du périmètre). Voir `ingestion/social.py`.

Nécessite `ADANOS_API_KEY` (même clé que le projet SCCT). Sans clé, écrit un
fichier vide et la chaîne continue sans contexte social.

Usage :
    export ADANOS_API_KEY=xxx
    python -m screener.build_social
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from typing import List

from .ingestion.social import fetch_social

_HERE = os.path.dirname(__file__)
_DATA = os.path.join(_HERE, "data")

_OUT_COLS = ["ticker", "social_buzz_z", "social_sentiment", "social_mentions",
             "social_trend"]


def _load_universe(path: str) -> List[dict]:
    with open(path) as f:
        return [r for r in csv.DictReader(f) if (r.get("ticker") or "").strip()]


def _n(x):
    return "" if x is None else x


def build(universe_path: str, out_path: str, sleep_s: float) -> int:
    universe = _load_universe(universe_path)
    if not os.getenv("ADANOS_API_KEY"):
        print("⚠ ADANOS_API_KEY absent — aucune tendance sociale récupérée.", file=sys.stderr)

    rows = []
    for i, u in enumerate(universe):
        tk = u["ticker"].strip().upper()
        sig = fetch_social(tk)
        if sig is None:
            rows.append({c: "" for c in _OUT_COLS} | {"ticker": tk})
        else:
            rows.append({
                "ticker": tk, "social_buzz_z": _n(sig.buzz_z),
                "social_sentiment": _n(sig.sentiment),
                "social_mentions": _n(sig.mentions), "social_trend": sig.trend,
            })
        if os.getenv("ADANOS_API_KEY") and sleep_s and i < len(universe) - 1:
            time.sleep(sleep_s)

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_OUT_COLS)
        w.writeheader()
        w.writerows(rows)

    filled = sum(1 for r in rows if r["social_trend"])
    print(f"Tendance sociale : {filled}/{len(rows)} titres renseignés -> {out_path}")
    for r in rows:
        if r["social_trend"]:
            print(f"  {r['ticker']:<8} tendance {r['social_trend']:<7} "
                  f"z={r['social_buzz_z']} sentiment={r['social_sentiment']}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Tendance sociale Adanos (contexte)")
    ap.add_argument("--universe", default=os.path.join(_DATA, "universe.sample.csv"))
    ap.add_argument("--out", default=os.path.join(_DATA, "social.built.csv"))
    ap.add_argument("--sleep", type=float, default=1.0)
    args = ap.parse_args(argv)
    if not os.path.exists(args.universe):
        sys.exit(f"univers introuvable : {args.universe}")
    return build(args.universe, args.out, args.sleep)


if __name__ == "__main__":
    sys.exit(main())
