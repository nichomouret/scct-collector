#!/usr/bin/env python3
"""
Test placebo — §10.10.
======================
« Même chaîne sur signaux aléatoires. Si le placebo sort un Sharpe de 0.8, votre
1.0 ne vaut rien. » On rejoue EXACTEMENT la même simulation de position (embargo,
coûts, time-stop, MAE) mais sur des dates d'entrée tirées au hasard, en nombre
égal aux signaux réels de chaque titre — pour un comparatif équitable.

`random`/seed sont utilisés ici (code Python normal ; l'interdiction de
`random`/`Date.now` ne concerne que les scripts de workflow).
"""
from __future__ import annotations

import random
from typing import Dict, List, Optional, Tuple

from ..detection.path_archetype import PriceBar
from .engine import BacktestConfig, Entry, Trade, simulate_position, technical_entries


def placebo_ticker(ticker: str, bars: List[PriceBar], market_bars: List[PriceBar],
                   cfg: BacktestConfig, rng: random.Random,
                   n: Optional[int] = None) -> List[Trade]:
    if n is None:
        n = len(technical_entries(bars, market_bars, cfg))
    hi = len(bars) - 2
    if n <= 0 or hi <= cfg.warmup:
        return []
    idxs = sorted(rng.sample(range(cfg.warmup, hi + 1), min(n, hi + 1 - cfg.warmup)))
    trades: List[Trade] = []
    next_free = 0
    for si in idxs:
        if si < next_free:
            continue
        # entrée aléatoire : pas de vrai choc → point de capitulation = bas du jour
        e = Entry(signal_idx=si, shock_idx=si, dis=cfg.dis_min, archetype="RANDOM")
        tr = simulate_position(bars, e, cfg, ticker)
        if tr is None:
            continue
        tr.route = "PLACEBO"
        trades.append(tr)
        next_free = tr.exit_idx + 1
    return trades


def placebo_universe(data: Dict[str, Tuple[List[PriceBar], List[PriceBar]]],
                     cfg: BacktestConfig, seed: int = 12345) -> List[Trade]:
    rng = random.Random(seed)
    out: List[Trade] = []
    for ticker, (bars, mkt) in data.items():
        out.extend(placebo_ticker(ticker, bars, mkt, cfg, rng))
    return out
