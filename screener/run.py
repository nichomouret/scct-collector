#!/usr/bin/env python3
"""
Tranche verticale runnable — de la watchlist à la short-list + fiches.
=====================================================================
Enchaîne : univers (CSV) → prix (Yahoo/cache) → détection résiduelle → assemblage
de `Candidate` → moteur de décision (socle + routes + conviction + taille + PATH)
→ short-list classée + fiches 1 page pour les dossiers admis.

Usage :
    python -m screener.run                         # watchlist d'exemple, données live
    python -m screener.run --universe u.csv --overlay o.csv --catalysts c.csv
    python -m screener.run --offline               # cache uniquement, aucun réseau

Fichiers d'entrée (voir screener/data/*.sample.csv) :
    --universe   ticker,symbol,name,sector,place,region,market_cap,analyst_coverage,
                 target_position_value,market_index
    --catalysts  ticker,catalyst_type,date_expected,date_certainty,days_to_catalyst,
                 binary,expected_move_pct,power,source_url
    --overlay    ticker,<champs qualitatifs>  (cause_class, permanence, fv_low, val_z,
                 aqs, insider_buy, capi_effacee, rebut_score, pms, ...)
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import textwrap
from typing import Dict, List, Optional

from .ingestion.prices import PriceFetchError, load_bars
from .universe.candidate_builder import build_candidate
from .engine import evaluate_candidate, EvaluationResult
from .models import Region
from .output import dossier

_HERE = os.path.dirname(__file__)
_DATA = os.path.join(_HERE, "data")

_DEFAULT_INDEX = {Region.US: "^GSPC", Region.EU: "^STOXX50E", Region.IL: "^TA125.TA"}


def _load_keyed(path: Optional[str]) -> Dict[str, dict]:
    if not path or not os.path.exists(path):
        return {}
    out: Dict[str, dict] = {}
    with open(path) as f:
        for row in csv.DictReader(f):
            tk = (row.get("ticker") or "").strip().upper()
            if tk:
                out[tk] = row
    return out


def _load_universe(path: str) -> List[dict]:
    with open(path) as f:
        return [r for r in csv.DictReader(f) if (r.get("ticker") or "").strip()]


def _market_symbol(uni: dict) -> str:
    if uni.get("market_index"):
        return uni["market_index"].strip()
    region = uni.get("region", "US").strip().upper()
    try:
        return _DEFAULT_INDEX[Region(region)]
    except ValueError:
        return "^GSPC"


def run(universe_path: str, catalysts_path: Optional[str], overlay_path: Optional[str],
        short_interest_path: Optional[str], news_path: Optional[str],
        social_path: Optional[str], cache_dir: str, standard_size: float,
        offline: bool, rng: str, show_all: bool,
        dashboard_path: Optional[str] = None) -> int:
    universe = _load_universe(universe_path)
    catalysts = _load_keyed(catalysts_path)
    overlay = _load_keyed(overlay_path)
    short_interest = _load_keyed(short_interest_path)
    news = _load_keyed(news_path)   # cause_class/permanence/résolution (LLM, §5.3)
    social = _load_keyed(social_path)   # tendance sociale Adanos — contexte, hors scoring

    market_cache: Dict[str, list] = {}
    results: List[EvaluationResult] = []
    errors: List[str] = []

    for uni in universe:
        tk = uni["ticker"].strip().upper()
        symbol = (uni.get("symbol") or tk).strip()
        mkt_sym = _market_symbol(uni)
        try:
            bars = load_bars(symbol, cache_dir=cache_dir, rng=rng, offline=offline)
            if mkt_sym not in market_cache:
                market_cache[mkt_sym] = load_bars(mkt_sym, cache_dir=cache_dir,
                                                  rng=rng, offline=offline)
            mkt = market_cache[mkt_sym]
        except PriceFetchError as e:
            errors.append(f"{tk}: {e}")
            continue

        # Contexte social + news LLM sous l'overlay manuel : l'humain a le dernier mot.
        merged_overlay = {**(social.get(tk) or {}), **(news.get(tk) or {}),
                          **(overlay.get(tk) or {})}
        bc = build_candidate(uni, bars, mkt,
                             catalyst_row=catalysts.get(tk), overlay=merged_overlay or None,
                             short_interest=short_interest.get(tk))
        res = evaluate_candidate(bc.candidate, standard_size=standard_size,
                                 bars=bc.bars, shock_idx=bc.shock_idx,
                                 stab_features=bc.stab_features)
        results.append(res)

    # Tri : admis d'abord, puis par conviction décroissante.
    results.sort(key=lambda r: (r.admitted, r.conviction.conv), reverse=True)
    _print_table(results, errors)
    _print_analyses(results)

    admitted = [r for r in results if r.admitted]
    to_show = results if show_all else admitted
    for r in to_show:
        print("\n" + "=" * 63)
        print(dossier.render(r))
    if not admitted:
        print("\n(aucun dossier admis aujourd'hui — normal : ~60-90 setups/an, §6.2)")

    if dashboard_path:
        from .output.dashboard import render_html
        # Date de référence = dernière barre du marché chargé (proxy « au »).
        as_of = next((mkt[-1].date for mkt in market_cache.values() if mkt), "")
        uni_label = os.path.basename(universe_path)
        with open(dashboard_path, "w") as f:
            f.write(render_html(results, as_of=as_of, universe=uni_label, standalone=True))
        print(f"\n-> tableau de bord : {dashboard_path}  (ouvrir/rafraîchir dans un navigateur)")
    return 0


def _print_table(results: List[EvaluationResult], errors: List[str]) -> None:
    print(f"\nSHORT-LIST — {sum(r.admitted for r in results)} admis / {len(results)} évalués")
    print("-" * 78)
    print(f"{'Ticker':<10}{'Admis':>6}{'Routes':>10}{'n':>3}{'Conv':>6}"
          f"{'Taille':>7}  {'Archétype':<16}{'DIS':>5}{'dsc':>5}  {'Social':<7}")
    print("-" * 86)
    for r in results:
        c = r.candidate
        routes = "".join(rt.value for rt in r.route_labels) or "-"
        arch = r.path.archetype.value if r.path.archetype else "-"
        dsc = c.days_since_shock if c.days_since_shock is not None else "-"
        social = c.social_trend or "-"
        print(f"{c.ticker:<10}{'✓' if r.admitted else '·':>6}{routes:>10}{r.n_routes:>3}"
              f"{min(r.conviction.conv,10.0):>6.1f}{r.sizing.final_size:>7.2f}  "
              f"{arch:<16}{c.dis:>5.1f}{str(dsc):>5}  {social:<7}")
    if errors:
        print("\nErreurs d'ingestion :")
        for e in errors:
            print(f"  ! {e}")


def _print_analyses(results: List[EvaluationResult]) -> None:
    """Analyse Claude par titre (§5.3, §8) — pour chaque titre qui en a une."""
    with_analysis = [r for r in results if r.candidate.analysis]
    if not with_analysis:
        return
    print("\nANALYSE CLAUDE PAR TITRE")
    print("-" * 78)
    for r in with_analysis:
        print(f"{r.candidate.ticker} :")
        for line in textwrap.wrap(r.candidate.analysis, width=74):
            print(f"  {line}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Screener de dislocation — tranche verticale")
    ap.add_argument("--universe", default=os.path.join(_DATA, "universe.sample.csv"))
    ap.add_argument("--catalysts", default=os.path.join(_DATA, "catalysts.sample.csv"))
    ap.add_argument("--overlay", default=os.path.join(_DATA, "overlay.sample.csv"))
    ap.add_argument("--short-interest", default=os.path.join(_DATA, "short_interest.built.csv"),
                    help="snapshot Ortex (build_short_interest) ; absent = ignoré")
    ap.add_argument("--news", default=os.path.join(_DATA, "news.built.csv"),
                    help="classification news LLM (build_news, §5.3) ; absent = ignoré")
    ap.add_argument("--social", default=os.path.join(_DATA, "social.built.csv"),
                    help="tendance sociale Adanos (build_social) — contexte, hors scoring")
    ap.add_argument("--cache-dir", default=os.path.join(_HERE, ".cache"))
    ap.add_argument("--standard-size", type=float, default=1.0)
    ap.add_argument("--range", default="2y", help="fenêtre d'historique Yahoo (ex. 1y, 2y, 5y)")
    ap.add_argument("--offline", action="store_true", help="cache uniquement, aucun réseau")
    ap.add_argument("--show-all", action="store_true",
                    help="imprime les fiches de tous les titres, pas seulement les admis")
    ap.add_argument("--dashboard", default=None, metavar="PATH",
                    help="écrit un tableau de bord HTML interactif (short-list à rafraîchir)")
    args = ap.parse_args(argv)

    if not os.path.exists(args.universe):
        sys.exit(f"univers introuvable : {args.universe}")
    return run(args.universe, args.catalysts, args.overlay, args.short_interest,
               args.news, args.social, args.cache_dir, args.standard_size,
               args.offline, args.range, args.show_all, args.dashboard)


if __name__ == "__main__":
    sys.exit(main())
