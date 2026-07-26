#!/usr/bin/env python3
"""
Scanner du jour — dislocations techniques fraîches (couche 2, §4).
=================================================================
Répond à « quels titres trader maintenant ? ». Pour chaque titre de l'univers,
calcule la détection sur la DERNIÈRE barre : s'il y a une dislocation résiduelle
fraîche (§4) non bloquée par la discipline PATH (§7.3.3), le titre entre dans la
short-list du jour, avec l'archétype, le stop structurel et la reco d'entrée.

Prix seuls → aucune clé API requise. C'est le FILTRE d'entrée (couche 2), pas le
dossier complet : le backtest montre que la dislocation technique seule ne bat pas
les seuils (§10.9). À confirmer par une raison de trader (catalyseur, réfutation,
décote, news) — les routes qualitatives — avant d'agir. C'est exactement le rôle
de `run` (couche 4) quand les couches de données sont branchées.

    python -m screener.scan --universe screener/data/universe_us.csv --html sig.html
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .ingestion.prices import PriceFetchError, load_bars
from .detection.path_archetype import PriceBar, classify_archetype
from .detection.residuals import compute_detection
from .detection.stabilization import entry_schedule, stabilization_score
from .universe.candidate_builder import stab_features_from_bars
from .models import BLOCKING_ARCHETYPES

_HERE = os.path.dirname(__file__)
_DATA = os.path.join(_HERE, "data")
_DEFAULT_INDEX = {"US": "^GSPC", "EU": "^STOXX50E", "IL": "^TA125.TA"}


@dataclass
class ScanConfig:
    dis_min: float = 1.5
    fresh_max_days: int = 5       # fenêtre de fraîcheur (§4.2)
    rng: str = "2y"


def scan_ticker(ticker: str, name: str, bars: List[PriceBar],
                market_bars: List[PriceBar], cfg: ScanConfig) -> Optional[dict]:
    det = compute_detection(bars, market_bars)
    if det.shock_idx is None or not det.fresh:
        return None
    if det.days_since_shock is None or det.days_since_shock > cfg.fresh_max_days:
        return None
    if det.dis < cfg.dis_min:
        return None
    arch = classify_archetype(bars, det.shock_idx)
    blocked = arch in BLOCKING_ARCHETYPES
    stab = stabilization_score(stab_features_from_bars(bars, det.shock_idx))
    plan = entry_schedule(True, True, arch, stab)
    price = bars[-1].close
    shock_low = bars[det.shock_idx].low
    stop_pct = (price - shock_low) / price if price else None
    return {
        "ticker": ticker, "name": name, "price": price,
        "days_since_shock": det.days_since_shock,
        "z_res": det.z_res_at_shock, "dis": det.dis, "z_volume": det.z_volume,
        "archetype": arch.value, "blocked": blocked, "stab": stab,
        "tranche1_ok": plan.tranche1_ok, "tranche2_armed": plan.tranche2_armed,
        "blocked_reason": plan.blocked_reason,
        "stop_level": shock_low, "stop_pct": stop_pct,
    }


def scan_universe(data: Dict[str, Tuple[str, List[PriceBar], List[PriceBar]]],
                  cfg: ScanConfig) -> List[dict]:
    out = []
    for ticker, (name, bars, mkt) in data.items():
        c = scan_ticker(ticker, name, bars, mkt, cfg)
        if c:
            out.append(c)
    out.sort(key=lambda c: c["dis"], reverse=True)
    return out


def _load_universe(path: str) -> List[dict]:
    with open(path) as f:
        return [r for r in csv.DictReader(f) if (r.get("ticker") or "").strip()]


def _market_symbol(uni: dict) -> str:
    if uni.get("market_index"):
        return uni["market_index"].strip()
    return _DEFAULT_INDEX.get((uni.get("region") or "US").strip().upper(), "^GSPC")


def run(universe_path: str, offline: bool, cache_dir: str, cfg: ScanConfig,
        html_path: Optional[str], json_path: Optional[str]) -> int:
    universe = _load_universe(universe_path)
    market_cache: Dict[str, list] = {}
    data: Dict[str, Tuple[str, list, list]] = {}
    errors: List[str] = []
    for uni in universe:
        tk = uni["ticker"].strip().upper()
        symbol = (uni.get("symbol") or tk).strip()
        mkt_sym = _market_symbol(uni)
        try:
            bars = load_bars(symbol, cache_dir=cache_dir, rng=cfg.rng, offline=offline)
            if mkt_sym not in market_cache:
                market_cache[mkt_sym] = load_bars(mkt_sym, cache_dir=cache_dir,
                                                  rng=cfg.rng, offline=offline)
            data[tk] = (uni.get("name") or tk, bars, market_cache[mkt_sym])
        except PriceFetchError as e:
            errors.append(f"{tk}: {e}")

    candidates = scan_universe(data, cfg)
    asof = data[next(iter(data))][1][-1].date if data else ""
    result = {"as_of": asof, "n_scanned": len(data), "candidates": candidates,
              "config": {"dis_min": cfg.dis_min, "fresh_max_days": cfg.fresh_max_days}}

    print(f"\nSIGNAUX DU JOUR — {len(candidates)} dislocation(s) fraîche(s) / "
          f"{len(data)} titres scannés (au {asof})")
    print("-" * 78)
    if candidates:
        print(f"{'Ticker':<9}{'Choc':>6}{'DIS':>6}{'STAB':>6}  {'Archétype':<16}{'Entrée':<22}")
        for c in candidates:
            entry = "tranche 1 (50%)" if c["tranche1_ok"] else "attendre (bloqué)"
            print(f"{c['ticker']:<9}{'J-'+str(c['days_since_shock']):>6}{c['dis']:>6.1f}"
                  f"{c['stab']:>5}/9  {c['archetype']:<16}{entry:<22}")
    else:
        print("(aucune dislocation fraîche aujourd'hui)")

    if json_path:
        with open(json_path, "w") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"\n-> {json_path}")
    if html_path:
        from .output.scan_report import render_html
        with open(html_path, "w") as f:
            f.write(render_html(result, standalone=True))
        print(f"-> {html_path}  (ouvrir dans un navigateur)")
    for e in errors:
        print(f"  ! {e}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Scanner du jour — dislocations fraîches")
    ap.add_argument("--universe", default=os.path.join(_DATA, "universe.sample.csv"))
    ap.add_argument("--cache-dir", default=os.path.join(_HERE, ".cache"))
    ap.add_argument("--range", default="2y")
    ap.add_argument("--dis-min", type=float, default=1.5)
    ap.add_argument("--fresh-max-days", type=int, default=5)
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--html", default=None)
    ap.add_argument("--json", default=None)
    args = ap.parse_args(argv)
    if not os.path.exists(args.universe):
        sys.exit(f"univers introuvable : {args.universe}")
    cfg = ScanConfig(dis_min=args.dis_min, fresh_max_days=args.fresh_max_days, rng=args.range)
    return run(args.universe, args.offline, args.cache_dir, cfg, args.html, args.json)


if __name__ == "__main__":
    sys.exit(main())
