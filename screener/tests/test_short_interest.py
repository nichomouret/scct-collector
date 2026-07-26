#!/usr/bin/env python3
"""
Tests short interest / emprunt (Ortex) — hors ligne, payloads synthétiques.

    python -m unittest screener.tests.test_short_interest
"""
from __future__ import annotations

import unittest
from datetime import date

from screener.ingestion.short_interest import (
    flow_confirmers, parse_borrow, parse_short_interest, signals_from_payloads,
)
from screener.universe.candidate_builder import build_candidate
from screener.output import dossier
from screener.engine import evaluate_candidate
from screener.tests.test_vertical import _market, _stock_with_shock

# Payloads calqués sur le schéma Ortex confirmé (ortex_c4_pull.py).
_SI_JSON = {"rows": [
    {"shortInterestPcFreeFloat": 13.0, "daysToCover": 5.0},
    {"shortInterestPcFreeFloat": 14.2, "daysToCover": 5.5},
]}
_CTB_JSON = {"rows": [
    {"costToBorrow": 4.0, "utilization": 80},
    {"costToBorrow": 4.5, "utilization": 85},
    {"costToBorrow": 5.0, "utilization": 88},
    {"costToBorrow": 6.4, "utilization": 91},
]}


class TestParsing(unittest.TestCase):
    def test_short_interest(self):
        si, dtc = parse_short_interest(_SI_JSON)
        self.assertAlmostEqual(si, 0.142, places=4)
        self.assertAlmostEqual(dtc, 5.5, places=4)

    def test_borrow_and_jump(self):
        borrow, util, jump = parse_borrow(_CTB_JSON)
        self.assertAlmostEqual(borrow, 0.064, places=4)
        self.assertAlmostEqual(util, 0.91, places=4)
        self.assertAlmostEqual(jump, 240.0, places=1)   # (0.064-0.040)*10000

    def test_empty_payloads(self):
        self.assertEqual(parse_short_interest(None), (None, None))
        self.assertEqual(parse_borrow({}), (None, None, None))


class TestConfirmers(unittest.TestCase):
    def test_two_confirmers_fire(self):
        sig = signals_from_payloads("TST", _SI_JSON, _CTB_JSON, as_of=date(2026, 7, 26))
        n, labels = flow_confirmers(sig)
        self.assertEqual(n, 2)                          # saut >=200 bps ET util >=90 %
        self.assertEqual(len(labels), 2)

    def test_no_confirmers_when_below_threshold(self):
        weak = {"rows": [{"costToBorrow": 4.0, "utilization": 50},
                         {"costToBorrow": 4.0, "utilization": 50},
                         {"costToBorrow": 4.0, "utilization": 50},
                         {"costToBorrow": 4.05, "utilization": 50}]}
        sig = signals_from_payloads("TST", _SI_JSON, weak)
        self.assertEqual(flow_confirmers(sig)[0], 0)

    def test_none_signals(self):
        self.assertEqual(flow_confirmers(None), (0, []))


class TestBuilderIntegration(unittest.TestCase):
    def _uni(self):
        return dict(ticker="TST", symbol="TST", name="Test", sector="Tech",
                    place="NYSE", region="US", market_cap="150000000000",
                    analyst_coverage="20", target_position_value="1000000")

    def _si_row(self):
        return {"short_interest_pct": "0.142", "borrow_fee": "0.064",
                "float_utilization": "0.91", "borrow_jump_bps_3d": "240",
                "days_to_cover": "5.5"}

    def test_ortex_confirmers_raise_dis(self):
        stock, mkt = _stock_with_shock(), _market()
        without = build_candidate(self._uni(), stock, mkt).candidate
        withsi = build_candidate(self._uni(), stock, mkt,
                                 short_interest=self._si_row()).candidate
        # 2 confirmateurs Ortex -> facteur (1+0.15*2)=1.3 sur DIS
        self.assertGreater(withsi.dis, without.dis)
        self.assertAlmostEqual(withsi.dis / without.dis, 1.30, places=2)
        self.assertAlmostEqual(withsi.short_interest_pct, 0.142, places=4)

    def test_fiche_renders_short_interest(self):
        bc = build_candidate(self._uni(), _stock_with_shock(), _market(),
                             short_interest=self._si_row())
        res = evaluate_candidate(bc.candidate, bars=bc.bars, shock_idx=bc.shock_idx,
                                 stab_features=bc.stab_features)
        text = dossier.render(res)
        self.assertIn("Short interest", text)
        self.assertIn("Utilisation float", text)
        self.assertIn("utilisation du float", text)   # ligne confirmateur


if __name__ == "__main__":
    unittest.main()
