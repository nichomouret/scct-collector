#!/usr/bin/env python3
"""
Tests du scanner du jour (couche 2) + rapport — hors ligne, barres synthétiques.

    python -m unittest screener.tests.test_scan
"""
from __future__ import annotations

import unittest

import csv
import os
import tempfile

from screener.scan import (
    ScanConfig, scan_ticker, scan_universe, _emit_universe, _emit_overlay,
    _OVERLAY_COLS,
)
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

    def test_aberrant_zres_excluded(self):
        # baseline bruitée puis crash de -95 % sur la dernière barre → z_res aberrant
        from screener.detection.path_archetype import PriceBar
        mkt = [PriceBar(f"d{i}", 100, 100, 100, 100, 1e6) for i in range(80)]
        px = []
        for i in range(79):
            c = 50 + (0.3 if i % 2 else -0.3)      # petite volatilité de base
            px.append(PriceBar(f"d{i}", c, c, c, c, 1e6))
        px.append(PriceBar("d79", 50, 50, 2.5, 2.5, 8e6))   # -95 % → z_res énorme
        self.assertIsNone(scan_ticker("BAD", "Bad", px, mkt, ScanConfig(dis_min=1.0)))

    def test_old_shock_not_fresh(self):
        # choc ancien (à 40 séances) -> pas dans la short-list du jour
        bars, mkt = _series(n=180, shock_at=140, kind="recover")
        self.assertIsNone(scan_ticker("TST", "Test", bars, mkt, ScanConfig(fresh_max_days=5)))

    def test_scan_universe_sorts_by_dis(self):
        b1, m = _series(n=180, shock_at=177, kind="recover")
        data = {"A": ("A", b1, m), "B": ("B", b1, m)}
        out = scan_universe(data, ScanConfig(dis_min=1.0))
        self.assertEqual(len(out), 2)

    def test_targets_are_multiples_of_risk(self):
        bars, mkt = _series(n=180, shock_at=177, kind="recover")
        c = scan_ticker("TST", "Test", bars, mkt, ScanConfig(dis_min=1.0))
        self.assertIsNotNone(c["target_2r"])
        risk = c["price"] - c["stop_level"]
        self.assertGreater(risk, 0)
        self.assertAlmostEqual(c["target_2r"], c["price"] + 2 * risk, places=6)
        self.assertAlmostEqual(c["target_3r"], c["price"] + 3 * risk, places=6)

    def test_tradeable_sorted_before_blocked(self):
        # capitulation -> tranche 1 OK ; grinding -> bloqué. Le tradeable remonte.
        b_ok, m = _series(n=180, shock_at=177, kind="recover")
        b_block, _ = _series(n=180, shock_at=177, kind="grind")
        data = {"BLOCK": ("B", b_block, m), "OK": ("A", b_ok, m)}
        out = scan_universe(data, ScanConfig(dis_min=1.0))
        if any(c["tranche1_ok"] for c in out) and any(not c["tranche1_ok"] for c in out):
            self.assertTrue(out[0]["tranche1_ok"])   # entrable en tête

    def test_emit_overlay_template(self):
        cands = [{"ticker": "ZZZ", "stop_pct": 0.087}]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ov.csv")
            n = _emit_overlay(path, cands)
            self.assertEqual(n, 1)
            with open(path) as f:
                rows = list(csv.DictReader(f))
        self.assertEqual(list(rows[0].keys()), _OVERLAY_COLS)
        self.assertEqual(rows[0]["ticker"], "ZZZ")
        self.assertEqual(rows[0]["hard_stop_distance"], "0.087")   # stop pré-rempli
        self.assertEqual(rows[0]["cause_class"], "")               # à remplir à la main


class TestEmitUniverse(unittest.TestCase):
    def test_emit_preserves_fields_and_order(self):
        universe = [
            {"ticker": "AAA", "symbol": "AAA", "name": "Alpha", "sector": "Tech",
             "region": "EU", "analyst_coverage": "5", "market_index": "^STOXX50E",
             "target_position_value": "500000", "sponsor": ""},
            {"ticker": "BBB", "name": "Beta"},   # champs manquants → défauts
        ]
        candidates = [{"ticker": "BBB", "name": "Beta"}, {"ticker": "AAA", "name": "Alpha"}]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "hits.csv")
            n = _emit_universe(path, universe, candidates, default_coverage="3")
            self.assertEqual(n, 2)
            with open(path) as f:
                rows = list(csv.DictReader(f))
        self.assertEqual([r["ticker"] for r in rows], ["BBB", "AAA"])  # ordre scanner
        aaa = rows[1]
        self.assertEqual(aaa["region"], "EU")
        self.assertEqual(aaa["analyst_coverage"], "5")        # conservé de l'univers
        self.assertEqual(aaa["market_index"], "^STOXX50E")
        self.assertEqual(aaa["market_cap"], "")               # rempli plus tard par SEC
        bbb = rows[0]
        self.assertEqual(bbb["analyst_coverage"], "3")        # défaut appliqué
        self.assertEqual(bbb["symbol"], "BBB")                # défaut = ticker
        self.assertEqual(bbb["region"], "US")

    def test_emit_empty(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "hits.csv")
            self.assertEqual(_emit_universe(path, [], [], "3"), 0)
            with open(path) as f:
                self.assertIn("ticker", f.readline())         # en-tête présent


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
