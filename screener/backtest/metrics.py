#!/usr/bin/env python3
"""
Métriques de backtest & seuils d'acceptation — §10.9.
=====================================================
Seuils révisés v1.1 : hit rate > 50 %, gain moyen / perte moyenne > 1.8,
Sharpe net > 1.0, drawdown max < 20 %, au moins 150 trades. En dessous de 150,
la significativité est nulle.

Décomposition par route (§10.11) : chaque route reportée séparément, min 40
trades pour être validée. Une route sous le seuil reste désactivée en prod.

Les objets `Trade` sont lus par canard : attributs `net_return`, `gross_return`,
`sessions_held`, `entry_date`, `route`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from math import sqrt
from statistics import mean, pstdev
from typing import Dict, List

# §10.9
HIT_RATE_MIN = 0.50
GAIN_LOSS_MIN = 1.8
SHARPE_MIN = 1.0
DRAWDOWN_MAX = 0.20
TRADES_MIN = 150
# §10.11
TRADES_PER_ROUTE_MIN = 40
SESSIONS_PER_YEAR = 252


@dataclass
class Metrics:
    n: int
    hit_rate: float
    avg_gain: float
    avg_loss: float
    gain_loss_ratio: float
    mean_return: float
    sharpe_per_trade: float
    sharpe_annual: float
    max_drawdown: float
    trades_per_year: float

    def acceptance(self, min_trades: int = TRADES_MIN) -> Dict[str, bool]:
        """Critères §10.9 (le seuil de trades est paramétrable pour la déco par route)."""
        return {
            "hit_rate>50%": self.hit_rate > HIT_RATE_MIN,
            "gain/perte>1.8": self.gain_loss_ratio > GAIN_LOSS_MIN,
            "sharpe_net>1.0": self.sharpe_annual > SHARPE_MIN,
            "drawdown<20%": self.max_drawdown < DRAWDOWN_MAX,
            f"trades>={min_trades}": self.n >= min_trades,
        }

    def passes(self, min_trades: int = TRADES_MIN) -> bool:
        return all(self.acceptance(min_trades).values())


def _try_year_span(trades) -> float:
    """Nombre d'années couvertes d'après les dates ISO d'entrée ; None si non parsable."""
    ds = []
    for t in trades:
        try:
            ds.append(date.fromisoformat(t.entry_date))
        except (ValueError, TypeError):
            return 0.0
    if len(ds) < 2:
        return 0.0
    return max((max(ds) - min(ds)).days / 365.25, 1e-9)


def _max_drawdown(trades) -> float:
    """Drawdown max sur la courbe d'équité multiplicative, trades triés par entrée."""
    ordered = sorted(trades, key=lambda t: t.entry_date)
    equity, peak, dd = 1.0, 1.0, 0.0
    for t in ordered:
        equity *= (1.0 + t.net_return)
        peak = max(peak, equity)
        dd = max(dd, (peak - equity) / peak)
    return dd


def compute_metrics(trades: List) -> Metrics:
    if not trades:
        return Metrics(0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    rets = [t.net_return for t in trades]
    gains = [r for r in rets if r > 0]
    losses = [-r for r in rets if r < 0]
    m = mean(rets)
    # Sharpe indéfini avec < 2 trades (écart-type nul) → 0, pas un nombre géant.
    sd = pstdev(rets) if len(rets) >= 2 else 0.0
    avg_gain = mean(gains) if gains else 0.0
    avg_loss = mean(losses) if losses else 0.0
    ratio = (avg_gain / avg_loss) if avg_loss else float("inf") if avg_gain else 0.0
    sharpe_pt = (m / sd) if sd > 0 else 0.0

    years = _try_year_span(trades)
    if years > 0:
        tpy = len(trades) / years
    else:
        avg_hold = mean(getattr(t, "sessions_held", 40) or 40 for t in trades)
        tpy = SESSIONS_PER_YEAR / max(avg_hold, 1.0)   # 1 créneau, annualisation approx.

    return Metrics(
        n=len(trades), hit_rate=len(gains) / len(trades),
        avg_gain=avg_gain, avg_loss=avg_loss, gain_loss_ratio=ratio,
        mean_return=m, sharpe_per_trade=sharpe_pt,
        sharpe_annual=sharpe_pt * sqrt(tpy), max_drawdown=_max_drawdown(trades),
        trades_per_year=tpy,
    )


def decompose_by_route(trades: List) -> Dict[str, Metrics]:
    """Métriques par route (§10.11). Ne valide pas ; le seuil 40 est appliqué à l'affichage."""
    routes: Dict[str, List] = {}
    for t in trades:
        routes.setdefault(getattr(t, "route", "?"), []).append(t)
    return {r: compute_metrics(ts) for r, ts in sorted(routes.items())}
