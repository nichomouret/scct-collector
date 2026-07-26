#!/usr/bin/env python3
"""
Tests du harnais de backtest (§10) — hors ligne, barres synthétiques déterministes.

    python -m unittest screener.tests.test_backtest

Couvre : embargo t+1, chaque motif de sortie (cible / MAE / structurel / time-stop),
coûts, détection de signal + PATH (entrée / blocage), garde anti-fuite, placebo,
métriques + acceptation §10.9.
"""
from __future__ import annotations

import unittest
from datetime import date, timedelta
from types import SimpleNamespace

from screener.detection.path_archetype import PriceBar
from screener.backtest.costs import Costs
from screener.backtest.engine import (
    BacktestConfig, Entry, simulate_position, run_ticker, technical_entries,
)
from screener.backtest.leakage_guard import signals_unaffected_by_future
from screener.backtest.placebo import placebo_ticker
from screener.backtest import metrics as M
import random


def _iso(i):
    return (date(2020, 1, 1) + timedelta(days=i)).isoformat()


def _bar(i, o, h, l, c, v=1_000_000):
    return PriceBar(_iso(i), o, h, l, c, v)


# --------------------------------------------------------------------------- #
# Simulation d'une position — motifs de sortie & embargo & coûts               #
# --------------------------------------------------------------------------- #
class TestSimulate(unittest.TestCase):
    def _cfg(self, **kw):
        kw.setdefault("costs", Costs(half_spread_bps=0, commission_bps=0,
                                     impact_coef_bps=0, tax_bps=0))  # coûts nuls pour l'égalité
        return BacktestConfig(**kw)

    def test_embargo_entry_at_next_open(self):
        # b0 = choc (close 100), b1 = jour d'entrée (open 98 ≠ close du choc)
        bars = [_bar(0, 113, 115, 90, 100), _bar(1, 98, 108, 99, 105),
                _bar(2, 105, 120, 104, 118)]
        tr = simulate_position(bars, Entry(0, 0, 5.0, "CAPITULATION_V"), self._cfg(), "T")
        self.assertEqual(tr.entry_price, 98)          # ouverture t+1, pas la clôture du choc

    def test_target_exit(self):
        bars = [_bar(0, 113, 115, 90, 100), _bar(1, 98, 102, 99, 101),
                _bar(2, 101, 108, 100, 105)]          # high 108 >= 98*1.08 = 105.84
        tr = simulate_position(bars, Entry(0, 0, 5.0, "X"), self._cfg(target_pct=0.08), "T")
        self.assertEqual(tr.exit_reason, "TARGET")
        self.assertAlmostEqual(tr.gross_return, 0.08, places=6)

    def test_mae_exit(self):
        # shock_low bas (80) pour que le stop structurel ne préempte pas la MAE
        bars = [_bar(0, 113, 115, 80, 100), _bar(1, 100, 101, 99, 100),
                _bar(2, 99, 101, 84, 95)]             # low 84 <= 100*0.85 = 85
        tr = simulate_position(bars, Entry(0, 0, 5.0, "X"), self._cfg(), "T")
        self.assertEqual(tr.exit_reason, "MAX_ADVERSE_EXCURSION")
        self.assertAlmostEqual(tr.gross_return, -0.15, places=6)

    def test_structural_stop(self):
        # low sous le point de capitulation (shock_low=90) mais au-dessus de -15 %
        bars = [_bar(0, 113, 115, 90, 100), _bar(1, 100, 101, 99, 100),
                _bar(2, 99, 101, 88, 92)]             # low 88 < 90, > 85
        tr = simulate_position(bars, Entry(0, 0, 5.0, "X"), self._cfg(), "T")
        self.assertEqual(tr.exit_reason, "STRUCTURAL_STOP")

    def test_time_stop(self):
        bars = [_bar(0, 113, 115, 90, 100)]           # choc
        for i in range(1, 46):                        # 45 séances plates
            bars.append(_bar(i, 100, 101, 99, 100))
        tr = simulate_position(bars, Entry(0, 0, 5.0, "X"),
                               self._cfg(target_pct=0.50), "T")
        self.assertEqual(tr.exit_reason, "TIME_STOP")
        self.assertEqual(tr.sessions_held, 40)

    def test_costs_reduce_return(self):
        bars = [_bar(0, 113, 115, 90, 100), _bar(1, 98, 102, 99, 101),
                _bar(2, 101, 108, 100, 105)]
        cfg = BacktestConfig(target_pct=0.08, costs=Costs(half_spread_bps=5,
                             commission_bps=1, impact_coef_bps=10, tax_bps=30))
        tr = simulate_position(bars, Entry(0, 0, 5.0, "X"), cfg, "T")
        self.assertAlmostEqual(tr.net_return,
                               tr.gross_return - cfg.costs.roundtrip_fraction(), places=9)
        self.assertLess(tr.net_return, tr.gross_return)


# --------------------------------------------------------------------------- #
# Scénarios de détection (pipeline complet)                                    #
# --------------------------------------------------------------------------- #
def _mkt(n=180):
    bars = []
    for i in range(n):
        c = 100.0 * (1 + 0.0002 * i)
        wig = 0.05 * (1 if i % 2 else -1)
        bars.append(PriceBar(_iso(i), c, c + 0.3, c - 0.3, c + wig, 1_000_000))
    return bars


def _series(n=180, shock_at=140, kind="recover"):
    """kind='recover' -> choc capitulation puis rebond (gagnant) ;
       kind='decline' -> choc à clôture médiane puis baisse continue (aucune entrée)."""
    mkt = _mkt(n)
    bars, c = [], 50.0
    for i in range(n):
        mret = (mkt[i].close / mkt[i - 1].close - 1) if i else 0.0
        ret = mret + 0.0005 * (1 if i % 2 else -1)
        if i == shock_at:
            ret = -0.15
        elif i > shock_at:
            ret = 0.04 if kind == "recover" else -0.03
        c *= (1 + ret)
        v = 1_000_000 + (i % 5) * 30_000
        if i == shock_at and kind == "recover":       # barre de capitulation (mèche basse, clôture haute)
            bars.append(PriceBar(_iso(i), c * 0.95, c * 1.01, c * 0.90, c, 6_000_000))
        elif i == shock_at:                            # choc à clôture médiane (non capitulation)
            bars.append(PriceBar(_iso(i), c * 0.97, c + 1.5, c - 1.5, c, 6_000_000))
        else:
            bars.append(PriceBar(_iso(i), c, c * 1.004, c * 0.996, c, v))
    return bars, mkt


class TestDetection(unittest.TestCase):
    def test_recover_produces_winning_trade(self):
        bars, mkt = _series(kind="recover")
        trades = run_ticker("T", bars, mkt, BacktestConfig(dis_min=2.0, target_pct=0.08))
        self.assertGreaterEqual(len(trades), 1)
        self.assertEqual(trades[0].exit_reason, "TARGET")
        self.assertGreater(trades[0].net_return, 0.0)

    def test_decline_blocks_entry(self):
        bars, mkt = _series(kind="decline")
        trades = run_ticker("T", bars, mkt, BacktestConfig(dis_min=2.0))
        self.assertEqual(len(trades), 0)   # archétype vendeur actif → PATH bloque

    def test_leakage_guard(self):
        bars, mkt = _series(kind="recover")
        cfg = BacktestConfig(dis_min=2.0)
        self.assertTrue(signals_unaffected_by_future(
            lambda b, m: technical_entries(b, m, cfg), bars, mkt))


# --------------------------------------------------------------------------- #
# Placebo & métriques                                                          #
# --------------------------------------------------------------------------- #
class TestPlaceboAndMetrics(unittest.TestCase):
    def test_placebo_runs(self):
        bars, mkt = _series(kind="recover")
        cfg = BacktestConfig(dis_min=2.0)
        trades = placebo_ticker("T", bars, mkt, cfg, random.Random(1), n=5)
        self.assertTrue(all(t.route == "PLACEBO" for t in trades))

    def _t(self, net, day=1, sessions=20):
        return SimpleNamespace(net_return=net, gross_return=net + 0.001,
                               entry_date=_iso(day), sessions_held=sessions, route="A")

    def test_metrics_hit_rate_and_ratio(self):
        trades = [self._t(0.10), self._t(0.10), self._t(-0.05)]
        m = M.compute_metrics(trades)
        self.assertEqual(m.n, 3)
        self.assertAlmostEqual(m.hit_rate, 2 / 3, places=6)
        self.assertAlmostEqual(m.gain_loss_ratio, 0.10 / 0.05, places=6)

    def test_metrics_drawdown(self):
        # +10 % puis -20 % -> drawdown ~ 20 %
        trades = [self._t(0.10, day=1), self._t(-0.20, day=2)]
        m = M.compute_metrics(trades)
        self.assertAlmostEqual(m.max_drawdown, 0.20, places=6)

    def test_acceptance_flags(self):
        trades = [self._t(0.10) for _ in range(120)]   # < 150 -> critère trades échoue
        acc = M.compute_metrics(trades).acceptance()
        self.assertFalse(acc["trades>=150"])
        self.assertTrue(acc["hit_rate>50%"])

    def test_decompose_by_route(self):
        trades = [self._t(0.1) for _ in range(3)]
        for t in trades[:1]:
            t.route = "B"
        d = M.decompose_by_route(trades)
        self.assertEqual(set(d), {"A", "B"})


if __name__ == "__main__":
    unittest.main()
