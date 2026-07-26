#!/usr/bin/env python3
"""
Tests du scanner du jour (couche 2) + rapport — hors ligne, barres synthétiques.

    python -m unittest screener.tests.test_scan
"""
from __future__ import annotations

import unittest

from screener.scan import ScanConfig, scan_ticker, scan_universe
from screener.output.scan_report import render_html
from screener.tests.test_backtest import _series


class TestScan(unittest.TestCase):
    def test_fresh_dislocation_detected(self):
        # choc capitulation à 3 séances de la fin -> dislocation FRAÎCHE aujourd'hui
        bars, mkt = _series(n=180, shock_at=177, kind="recover")
        c = scan_ticker("TST", "Test SA", bars, mkt, ScanConfig(dis_min=1.5, fresh_max_days=5))
        self.assertIsNotNone(c)
        self.assertEqual(c["ticker"], "TST")
        self.assertLessEqual(c["days_since_shock"], 5)
        self.assertGreater(c["dis"], 1.5)
        self.assertFalse(c["blocked"])          # capitulation -> entrée tranche 1
        self.assertTrue(c["tranche1_ok"])
        self.assertIsNotNone(c["stop_level"])

    def test_old_shock_not_fresh(self):
        # choc ancien (à 40 séances) -> pas dans la short-list du jour
        bars, mkt = _series(n=180, shock_at=140, kind="recover")
        self.assertIsNone(scan_ticker("TST", "Test", bars, mkt, ScanConfig(fresh_max_days=5)))

    def test_scan_universe_sorts_by_dis(self):
        b1, m = _series(n=180, shock_at=177, kind="recover")
        data = {"A": ("A", b1, m), "B": ("B", b1, m)}
        out = scan_universe(data, ScanConfig(dis_min=1.0))
        self.assertEqual(len(out), 2)


class TestScanReport(unittest.TestCase):
    def test_renders_candidates(self):
        result = {"as_of": "2026-07-24", "n_scanned": 44,
                  "config": {"dis_min": 1.5, "fresh_max_days": 5},
                  "candidates": [{"ticker": "FRME", "name": "First Merchants",
                                  "price": 38.2, "days_since_shock": 1, "z_res": -2.9,
                                  "dis": 1.6, "z_volume": 3.1, "archetype": "GRINDING_DECLINE",
                                  "blocked": True, "stab": 8, "tranche1_ok": False,
                                  "tranche2_armed": True, "blocked_reason": "vendeur actif",
                                  "stop_level": 36.5, "stop_pct": 0.044}]}
        h = render_html(result, standalone=True)
        self.assertIn("<!doctype html>", h)
        self.assertIn("FRME", h)
        self.assertIn("Attendre stabilisation", h)   # archétype bloquant
        self.assertIn("Stop structurel", h)
        self.assertIn("candidats, pas des ordres", h)  # cadrage honnête

    def test_empty(self):
        h = render_html({"as_of": "2026-07-24", "n_scanned": 44, "candidates": [],
                         "config": {"dis_min": 1.5}}, standalone=False)
        self.assertIn("Aucune dislocation", h)
        self.assertNotIn("<!doctype", h)


if __name__ == "__main__":
    unittest.main()
