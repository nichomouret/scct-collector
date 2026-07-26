#!/usr/bin/env python3
"""
Moteur de backtest — la boucle temporelle + la comptabilité de trades (§10).
============================================================================
Signal DÉTERMINISTE (proxy route A, §7.1) : une dislocation résiduelle fraîche
(§4), filtrée par la discipline PATH (archétype non bloquant, §7.3.3), entrée
avec EMBARGO (signal à la clôture t → exécution à l'OUVERTURE t+1, §10.6), et
sortie par les règles codées en dur (§7.4) dont le TIME-STOP 40 séances importé
tel quel depuis `execution.exit_rules` (§10.7 : mêmes constantes backtest/prod).

Ordre des sorties intra-journalières (convention conservatrice : on suppose le
fill défavorable en premier) : stop dur MAE −15 % → stop structurel (nouveau
plus bas sous le point de capitulation) → cible (take-profit) → time-stop.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..detection.path_archetype import PriceBar, classify_archetype
from ..detection.residuals import compute_detection
from ..models import BLOCKING_ARCHETYPES
from ..execution.exit_rules import EXIT_RULES
from .costs import Costs


@dataclass
class BacktestConfig:
    dis_min: float = 3.0             # route A A1
    fresh_max_days: int = 3          # route A A1 (choc <= 3 séances)
    require_zvol: bool = False       # exiger z-volume >= 3 (route A A4)
    target_pct: float = 0.10         # take-profit (mean reversion)
    warmup: int = 120               # barres avant d'autoriser un signal
    costs: Costs = field(default_factory=Costs)
    route: str = "A-tech"


@dataclass
class Entry:
    signal_idx: int                  # date du signal (clôture t)
    shock_idx: int
    dis: float
    archetype: str


@dataclass
class Trade:
    ticker: str
    route: str
    entry_date: str
    exit_date: str
    entry_idx: int
    exit_idx: int
    entry_price: float
    exit_price: float
    gross_return: float
    net_return: float
    sessions_held: int
    exit_reason: str
    dis: float
    archetype: str


# --------------------------------------------------------------------------- #
# Détection des signaux d'entrée                                               #
# --------------------------------------------------------------------------- #
def technical_entries(bars: List[PriceBar], market_bars: List[PriceBar],
                      cfg: Optional[BacktestConfig] = None) -> List[Entry]:
    """
    Signaux d'entrée point-in-time. À chaque t, la détection ne reçoit que
    `bars[:t+1]` (l'alignement avec `market_bars` est borné par les dates du
    titre → aucune barre marché postérieure à t n'entre). Un choc n'est joué
    qu'une fois (dédup par date de choc).
    """
    cfg = cfg or BacktestConfig()
    entered: set = set()
    out: List[Entry] = []
    for t in range(cfg.warmup, len(bars) - 1):     # besoin de t+1 pour l'embargo
        det = compute_detection(bars[:t + 1], market_bars)
        if det.shock_idx is None or not det.fresh:
            continue
        if det.days_since_shock is None or det.days_since_shock > cfg.fresh_max_days:
            continue
        if det.dis < cfg.dis_min:
            continue
        if cfg.require_zvol and det.z_volume < 3.0:
            continue
        shock_date = bars[det.shock_idx].date
        if shock_date in entered:
            continue
        arch = classify_archetype(bars[:t + 1], det.shock_idx)
        if arch in BLOCKING_ARCHETYPES:            # PATH : tranche 1 bloquée (§7.3.3)
            continue
        entered.add(shock_date)
        out.append(Entry(signal_idx=t, shock_idx=det.shock_idx, dis=det.dis,
                         archetype=arch.value))
    return out


# --------------------------------------------------------------------------- #
# Simulation d'une position                                                    #
# --------------------------------------------------------------------------- #
def simulate_position(bars: List[PriceBar], entry: Entry, cfg: BacktestConfig,
                      ticker: str) -> Optional[Trade]:
    entry_idx = entry.signal_idx + 1               # embargo : ouverture t+1
    if entry_idx >= len(bars):
        return None
    entry_price = bars[entry_idx].open
    if entry_price <= 0:
        return None
    shock_low = bars[entry.shock_idx].low
    stop_price = entry_price * (1.0 + EXIT_RULES["max_adverse_excursion"])  # -15 %
    target_price = entry_price * (1.0 + cfg.target_pct)
    time_stop = EXIT_RULES["time_stop_sessions"]

    exit_idx = len(bars) - 1
    exit_price = bars[-1].close
    reason = "OPEN_EOD"                            # position tronquée en fin d'historique
    for k in range(entry_idx, len(bars)):
        bar = bars[k]
        held = k - entry_idx + 1                   # séance d'entrée = 1
        if bar.low <= stop_price:                  # 1. MAE −15 % (défavorable d'abord)
            exit_idx, exit_price, reason = k, stop_price, "MAX_ADVERSE_EXCURSION"
            break
        if bar.low < shock_low:                    # 2. stop structurel
            exit_idx, exit_price, reason = k, min(bar.close, shock_low), "STRUCTURAL_STOP"
            break
        if bar.high >= target_price:               # 3. cible (take-profit)
            exit_idx, exit_price, reason = k, target_price, "TARGET"
            break
        if held >= time_stop:                      # 4. time-stop inconditionnel
            exit_idx, exit_price, reason = k, bar.close, "TIME_STOP"
            break

    gross = exit_price / entry_price - 1.0
    net = gross - cfg.costs.roundtrip_fraction()
    return Trade(
        ticker=ticker, route=cfg.route,
        entry_date=bars[entry_idx].date, exit_date=bars[exit_idx].date,
        entry_idx=entry_idx, exit_idx=exit_idx,
        entry_price=entry_price, exit_price=exit_price,
        gross_return=gross, net_return=net,
        sessions_held=exit_idx - entry_idx + 1,
        exit_reason=reason, dis=entry.dis, archetype=entry.archetype,
    )


def run_ticker(ticker: str, bars: List[PriceBar], market_bars: List[PriceBar],
               cfg: Optional[BacktestConfig] = None) -> List[Trade]:
    """Backteste un titre. Pas de pyramidage : une position ouverte à la fois."""
    cfg = cfg or BacktestConfig()
    trades: List[Trade] = []
    next_free = 0
    for e in technical_entries(bars, market_bars, cfg):
        if e.signal_idx < next_free:               # position encore ouverte
            continue
        tr = simulate_position(bars, e, cfg, ticker)
        if tr is None:
            continue
        trades.append(tr)
        next_free = tr.exit_idx + 1
    return trades


def run_universe(data: Dict[str, Tuple[List[PriceBar], List[PriceBar]]],
                 cfg: Optional[BacktestConfig] = None) -> List[Trade]:
    cfg = cfg or BacktestConfig()
    out: List[Trade] = []
    for ticker, (bars, mkt) in data.items():
        out.extend(run_ticker(ticker, bars, mkt, cfg))
    return out
