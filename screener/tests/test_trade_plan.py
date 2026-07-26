#!/usr/bin/env python3
"""
Tests du plan de trade (niveaux entrée/stop/objectif) — §7.3.3, §7.4.

    python -m unittest screener.tests.test_trade_plan
"""
from __future__ import annotations

import unittest

from screener.execution.exit_rules import EXIT_RULES
from screener.execution.trade_plan import plan_levels
from screener.models import Candidate, Catalyst, CatalystPower


class TestTradePlan(unittest.TestCase):
    def test_hard_stop_tighter_than_mae(self):
        c = Candidate(ticker="T", price=100.0, hard_stop_distance=0.11,
                      fv_low=112.0, fv_mid=125.0)
        lv = plan_levels(c)
        self.assertAlmostEqual(lv.stop, 89.0, places=6)      # -11% plus serré que -15%
        self.assertIn("stop dur", lv.stop_basis)
        self.assertAlmostEqual(lv.stop_pct, -0.11, places=6)

    def test_mae_is_floor_when_no_hard_stop(self):
        c = Candidate(ticker="T", price=100.0, upside_thesis_pct=30.0)
        lv = plan_levels(c)
        self.assertAlmostEqual(lv.stop, 85.0, places=6)      # -15% MAE
        self.assertIn("MAE", lv.stop_basis)

    def test_mae_caps_a_looser_hard_stop(self):
        # un stop dur à -20% ne doit jamais dépasser la MAE -15%
        c = Candidate(ticker="T", price=100.0, hard_stop_distance=0.20)
        lv = plan_levels(c)
        self.assertAlmostEqual(lv.stop, 85.0, places=6)
        self.assertIn("MAE", lv.stop_basis)

    def test_targets_from_fv_band(self):
        c = Candidate(ticker="T", price=100.0, fv_low=110.0, fv_mid=130.0,
                      hard_stop_distance=0.10)
        lv = plan_levels(c)
        self.assertEqual(lv.target1, 110.0)
        self.assertEqual(lv.target2, 130.0)
        self.assertAlmostEqual(lv.rr, (130 - 100) / (100 - 90), places=6)  # 30/10 = 3.0

    def test_targets_from_upside_thesis(self):
        c = Candidate(ticker="T", price=100.0, upside_thesis_pct=40.0)
        lv = plan_levels(c)
        self.assertAlmostEqual(lv.target2, 140.0, places=6)
        self.assertAlmostEqual(lv.target1, 120.0, places=6)   # mi-chemin

    def test_targets_from_catalyst_move(self):
        cat = Catalyst(catalyst_type="readout", date_expected="2026-08-01",
                       expected_move_pct=50.0, power=CatalystPower.HIGH)
        c = Candidate(ticker="T", price=10.0, catalyst=cat)
        lv = plan_levels(c)
        self.assertAlmostEqual(lv.target2, 15.0, places=6)

    def test_no_target_when_no_valuation(self):
        c = Candidate(ticker="T", price=50.0, hard_stop_distance=0.10)
        lv = plan_levels(c)
        self.assertIsNone(lv.target2)
        self.assertIsNone(lv.rr)

    def test_structural_stop_when_shock_low_given(self):
        c = Candidate(ticker="T", price=100.0)
        lv = plan_levels(c, shock_low=88.0)
        self.assertEqual(lv.structural_stop, 88.0)

    def test_time_stop_matches_exit_rules(self):
        lv = plan_levels(Candidate(ticker="T", price=10.0))
        self.assertEqual(lv.time_stop_sessions, EXIT_RULES["time_stop_sessions"])

    def test_none_without_price(self):
        self.assertIsNone(plan_levels(Candidate(ticker="T", price=0.0)))


if __name__ == "__main__":
    unittest.main()
