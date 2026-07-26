#!/usr/bin/env python3
"""
Tests de la tranche verticale — détection résiduelle + assemblage de Candidate.
Hors ligne : barres synthétiques, aucun accès réseau.

    python -m unittest screener.tests.test_vertical
"""
from __future__ import annotations

import unittest
from typing import List

from screener.detection.path_archetype import PriceBar
from screener.detection.residuals import compute_detection
from screener.universe.candidate_builder import build_candidate
from screener.engine import evaluate_candidate
from screener.models import Archetype, Route


def _market(n: int = 260) -> List[PriceBar]:
    bars = []
    c = 100.0
    for i in range(n):
        c = 100.0 * (1 + 0.0002 * i)              # dérive lente déterministe
        wig = 0.05 * (1 if i % 2 else -1)
        bars.append(PriceBar(f"2025-{i:04d}", c, c + 0.3, c - 0.3, c + wig, 1_000_000))
    return bars


def _stock_with_shock(n: int = 260) -> List[PriceBar]:
    """Suit le marché (bêta ~1) puis subit un gap idiosyncratique -12 % à j=256,
    suivi de 3 séances plates de volume décroissant → GAP_AND_FLAT + choc résiduel."""
    mkt = _market(n)
    bars = []
    close = 50.0
    for i in range(n):
        mret = (mkt[i].close / mkt[i - 1].close - 1.0) if i else 0.0
        noise = 0.0006 * (1 if i % 2 else -1)
        ret = mret + noise
        if i == 256:
            ret = -0.12                            # gap idiosyncratique
        close = close * (1 + ret)
        if i == 256:
            # barre de choc large (range 3.0, clôture au milieu) : les séances
            # plates qui suivent seront étroites en comparaison -> GAP_AND_FLAT.
            o, h, l, cl, v = close / (1 - 0.12), close + 1.5, close - 1.5, close, 5_000_000
        elif i in (257, 258, 259):
            span = 0.15
            o = h = l = cl = close
            h = close + span
            l = close - span * 0.3
            cl = close + span * 0.7                # clôture haute dans le range
            v = {257: 1_500_000, 258: 1_200_000, 259: 900_000}[i]
        else:
            o, h, l, cl, v = close, close + 0.2, close - 0.2, close, 1_000_000 + (i % 5) * 20_000
        bars.append(PriceBar(f"2025-{i:04d}", o, h, l, cl, v))
    return bars


class TestDetection(unittest.TestCase):
    def setUp(self):
        self.mkt = _market()
        self.stock = _stock_with_shock()

    def test_shock_detected_and_fresh(self):
        det = compute_detection(self.stock, self.mkt)
        self.assertEqual(det.shock_idx, 256)
        self.assertEqual(det.days_since_shock, 3)
        self.assertTrue(det.fresh)
        self.assertLess(det.z_res_at_shock, -2.5)   # résidu marché-neutre franchement négatif
        self.assertGreater(det.dis, 0.0)

    def test_no_shock_on_clean_series(self):
        det = compute_detection(self.mkt, self.mkt)  # titre = marché -> résidu nul
        self.assertIsNone(det.shock_idx)
        self.assertEqual(det.dis, 0.0)


class TestBuilder(unittest.TestCase):
    def _uni(self):
        return dict(ticker="TST", symbol="TST", name="Test", sector="Tech",
                    place="NYSE", region="US", market_cap="150000000000",
                    analyst_coverage="20", target_position_value="1000000")

    def test_builds_archetype_and_stab(self):
        bc = build_candidate(self._uni(), _stock_with_shock(), _market())
        self.assertEqual(bc.archetype, Archetype.GAP_AND_FLAT)
        self.assertIsNotNone(bc.stab_features)
        self.assertGreaterEqual(bc.stab_features.score(), 4)   # stabilisation partielle

    def test_route_b_admission_via_overlay(self):
        overlay = dict(
            cause_class="GUIDANCE_CUT", permanence="TRANSITORY",
            expected_resolution_days="25",
            capi_effacee="40000000000", impact_flux_actualise="10000000000",  # ratio 4.0
            net_debt_ebitda="2.0", recovery_precedents="3",
        )
        bc = build_candidate(self._uni(), _stock_with_shock(), _market(), overlay=overlay)
        res = evaluate_candidate(bc.candidate, standard_size=1.0,
                                 bars=bc.bars, shock_idx=bc.shock_idx,
                                 stab_features=bc.stab_features)
        self.assertTrue(res.admitted)
        self.assertIn(Route.B, res.route_labels)
        # non décoté (pas de fv_low) -> S4 tombe sur taille réduite 0.5x
        self.assertAlmostEqual(res.sizing.final_size, 0.5, places=6)

    def test_not_admitted_without_overlay(self):
        # Détection seule, sans cause ni horizon -> échec S3, non admis.
        bc = build_candidate(self._uni(), _stock_with_shock(), _market())
        res = evaluate_candidate(bc.candidate, standard_size=1.0,
                                 bars=bc.bars, shock_idx=bc.shock_idx,
                                 stab_features=bc.stab_features)
        self.assertFalse(res.admitted)


if __name__ == "__main__":
    unittest.main()
