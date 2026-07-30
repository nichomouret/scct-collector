#!/usr/bin/env python3
"""
Classification de news — CLI de la P4.
======================================
Pour chaque titre de l'univers : récupère les news 72h (NewsAPI) et les classe
via Claude (§5.3) en `cause_class` / `permanence` / `expected_resolution_days`.
Écrit un `news.built.csv` au format overlay, consommé par `screener.run --news`.
C'est ce qui automatise le remplissage des champs qualitatifs jusque-là saisis
à la main — le cœur du « premier livrable utilisable au quotidien » (§11, P4).

News récupérées via Yahoo Finance (SANS clé). Seul `ANTHROPIC_API_KEY` est requis
pour la classification ; sans lui, écrit des lignes vides et la chaîne retombe
sur l'overlay manuel.

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

from .ingestion.news import fetch_yahoo_news
from .ingestion.social import SocialSignal
from .qualification.news_classifier import classify_news, passes_gating

_HERE = os.path.dirname(__file__)
_DATA = os.path.join(_HERE, "data")


def _load_keyed(path):
    if not path or not os.path.exists(path):
        return {}
    out = {}
    with open(path) as f:
        for row in csv.DictReader(f):
            tk = (row.get("ticker") or "").strip().upper()
            if tk:
                out[tk] = row
    return out


def _social_hint(row) -> str:
    """Reconstruit un indice social textuel depuis une ligne social.built.csv."""
    if not row:
        return ""
    def f(k):
        try:
            return float(row[k]) if row.get(k) not in (None, "") else None
        except (TypeError, ValueError):
            return None
    def i(k):
        v = f(k)
        return int(v) if v is not None else None
    return SocialSignal(ticker=row.get("ticker", ""), buzz_z=f("social_buzz_z"),
                        sentiment=f("social_sentiment"), mentions=i("social_mentions")).hint()

_OUT_COLS = ["ticker", "cause_class", "permanence", "expected_resolution_days",
             "cash_flow_impact_pct", "source_reliability", "confidence",
             "evidence_url", "analysis", "gating"]


def _load_universe(path: str) -> List[dict]:
    with open(path) as f:
        return [r for r in csv.DictReader(f) if (r.get("ticker") or "").strip()]


def build(universe_path: str, out_path: str, social_path: str, sleep_s: float) -> int:
    universe = _load_universe(universe_path)
    social = _load_keyed(social_path)   # indice social passé au LLM (contexte)
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("⚠ ANTHROPIC_API_KEY absent — aucune classification (overlay manuel requis).",
              file=sys.stderr)

    rows = []
    for i, u in enumerate(universe):
        tk = u["ticker"].strip().upper()
        name = (u.get("name") or tk).strip()
        # Source de news : Yahoo Finance (SANS clé), fenêtre 7j pour couvrir un
        # choc localisé jusqu'à J-5 (§4.2) et sa news explicative.
        items = fetch_yahoo_news(tk, days=7)
        cls = classify_news(tk, name, items, social_hint=_social_hint(social.get(tk)))
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
    ap.add_argument("--social", default=os.path.join(_DATA, "social.built.csv"),
                    help="tendance sociale (build_social) passée au LLM comme contexte")
    ap.add_argument("--sleep", type=float, default=1.0, help="pause entre titres (s)")
    args = ap.parse_args(argv)
    if not os.path.exists(args.universe):
        sys.exit(f"univers introuvable : {args.universe}")
    return build(args.universe, args.out, args.social, args.sleep)


if __name__ == "__main__":
    sys.exit(main())
