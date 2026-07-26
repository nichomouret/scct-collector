#!/usr/bin/env python3
"""
Backtest — CLI (P6, protocole §10).
===================================
Charge l'univers + l'historique de prix (Yahoo/cache), rejoue le signal
déterministe (proxy route A) sur tout l'historique, et imprime les métriques
d'acceptation (§10.9), la décomposition par route (§10.11), le split hors
échantillon (§10.8) et le comparatif placebo (§10.10).

    python -m screener.run_backtest --range 5y
    python -m screener.run_backtest --offline           # cache uniquement

⚠️ Périmètre : signal price-derived uniquement. Routes B/C/E non backtestées
(garde anti-fuite LLM §10.3) ; route D en attente de catalyseurs PIT archivés.
Voir `screener/backtest/__init__.py`.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from typing import Dict, List, Tuple

from .ingestion.prices import PriceFetchError, load_bars
from .detection.path_archetype import PriceBar
from .backtest.engine import BacktestConfig, run_universe, Trade
from .backtest.costs import Costs, COSTS_US
from .backtest import metrics as M
from .backtest.placebo import placebo_universe

_HERE = os.path.dirname(__file__)
_DATA = os.path.join(_HERE, "data")
_DEFAULT_INDEX = {"US": "^GSPC", "EU": "^STOXX50E", "IL": "^TA125.TA"}


def _load_universe(path: str) -> List[dict]:
    with open(path) as f:
        return [r for r in csv.DictReader(f) if (r.get("ticker") or "").strip()]


def _market_symbol(uni: dict) -> str:
    if uni.get("market_index"):
        return uni["market_index"].strip()
    return _DEFAULT_INDEX.get((uni.get("region") or "US").strip().upper(), "^GSPC")


def _fmt_metrics(m: M.Metrics, min_trades: int) -> List[str]:
    acc = m.acceptance(min_trades)
    L = [
        f"  trades           {m.n}",
        f"  hit rate         {m.hit_rate:.1%}",
        f"  gain/perte moyen {m.gain_loss_ratio:.2f}",
        f"  rendement moyen  {m.mean_return:+.2%}",
        f"  Sharpe (annuel)  {m.sharpe_annual:.2f}  (par trade {m.sharpe_per_trade:.2f})",
        f"  drawdown max     {m.max_drawdown:.1%}",
        f"  trades/an ~       {m.trades_per_year:.0f}",
    ]
    L.append("  acceptation §10.9 : " + " · ".join(
        f"{'✓' if ok else '✗'} {k}" for k, ok in acc.items()))
    return L


def _split_oos(trades: List[Trade]) -> Tuple[List[Trade], List[Trade]]:
    """Split calibration / test (§10.8) par date d'entrée. Calibration = pass-through
    (le moteur n'a pas de paramètre ajusté) → c'est un contrôle hors échantillon."""
    ordered = sorted(trades, key=lambda t: t.entry_date)
    cut = int(len(ordered) * 0.7)
    return ordered[:cut], ordered[cut:]


def run(universe_path: str, offline: bool, rng: str, cache_dir: str,
        cfg: BacktestConfig) -> int:
    universe = _load_universe(universe_path)
    market_cache: Dict[str, list] = {}
    data: Dict[str, Tuple[List[PriceBar], List[PriceBar]]] = {}
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
            data[tk] = (bars, market_cache[mkt_sym])
        except PriceFetchError as e:
            errors.append(f"{tk}: {e}")

    trades = run_universe(data, cfg)
    print(f"\nBACKTEST — signal {cfg.route} · {len(data)} titres · {len(trades)} trades")
    print("=" * 70)
    if not trades:
        print("Aucun trade généré (historique trop court ou aucun choc détecté).")
        for e in errors:
            print(f"  ! {e}")
        return 0

    m = M.compute_metrics(trades)
    print("GLOBAL")
    for line in _fmt_metrics(m, M.TRADES_MIN):
        print(line)

    print("\nDÉCOMPOSITION PAR ROUTE (§10.11 : min 40 trades pour valider)")
    for route, rm in M.decompose_by_route(trades).items():
        ok = rm.n >= M.TRADES_PER_ROUTE_MIN
        print(f"  [{route}] {'VALIDABLE' if ok else 'trop peu de trades — désactivée'} "
              f"({rm.n} trades, hit {rm.hit_rate:.0%}, g/p {rm.gain_loss_ratio:.2f}, "
              f"Sharpe {rm.sharpe_annual:.2f})")

    ins, oos = _split_oos(trades)
    print(f"\nHORS ÉCHANTILLON (§10.8 · calibration pass-through, aucun paramètre ajusté)")
    print(f"  in-sample  ({len(ins)}) : Sharpe {M.compute_metrics(ins).sharpe_annual:.2f}")
    print(f"  out-sample ({len(oos)}) : Sharpe {M.compute_metrics(oos).sharpe_annual:.2f}")

    placebo = placebo_universe(data, cfg)
    pm = M.compute_metrics(placebo)
    verdict = ("✗ le placebo égale/dépasse le signal — signal SANS valeur"
               if pm.sharpe_annual >= m.sharpe_annual
               else "✓ le signal bat le placebo")
    print(f"\nPLACEBO (§10.10) : Sharpe placebo {pm.sharpe_annual:.2f} "
          f"vs signal {m.sharpe_annual:.2f} → {verdict}")

    # Répartition des motifs de sortie (contrôle de la discipline)
    reasons: Dict[str, int] = {}
    for t in trades:
        reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1
    print("\nMOTIFS DE SORTIE : " + " · ".join(f"{k} {v}" for k, v in sorted(reasons.items())))

    if m.n < M.TRADES_MIN:
        print(f"\n⚠ {m.n} trades < {M.TRADES_MIN} (§10.9) — significativité insuffisante ; "
              f"élargir l'univers/l'historique avant de conclure.")
    if errors:
        print("\nErreurs d'ingestion :")
        for e in errors:
            print(f"  ! {e}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Backtest du screener de dislocation (§10)")
    ap.add_argument("--universe", default=os.path.join(_DATA, "universe.sample.csv"))
    ap.add_argument("--cache-dir", default=os.path.join(_HERE, ".cache"))
    ap.add_argument("--range", default="5y", help="historique Yahoo (2y, 5y, 10y, max)")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--dis-min", type=float, default=3.0)
    ap.add_argument("--target-pct", type=float, default=0.10)
    ap.add_argument("--tax-bps", type=float, default=0.0, help="taxe transaction (FR : 30)")
    args = ap.parse_args(argv)

    cfg = BacktestConfig(dis_min=args.dis_min, target_pct=args.target_pct,
                         costs=Costs(tax_bps=args.tax_bps))
    if not os.path.exists(args.universe):
        sys.exit(f"univers introuvable : {args.universe}")
    return run(args.universe, args.offline, args.range, args.cache_dir, cfg)


if __name__ == "__main__":
    sys.exit(main())
