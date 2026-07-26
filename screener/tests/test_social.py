#!/usr/bin/env python3
"""
Tests de la tendance sociale Adanos — hors ligne, payloads synthétiques.
Vérifie AUSSI l'invariant clé : le social ne touche pas le scoring (§ SPEC v1.1).

    python -m unittest screener.tests.test_social
"""
from __future__ import annotations

import unittest
from datetime import date

from screener.ingestion.social import SocialSignal, signal_from_payload, fetch_social
from screener.universe.candidate_builder import build_candidate
from screener.engine import evaluate_candidate
from screener.tests.test_vertical import _market, _stock_with_shock


class TestParsing(unittest.TestCase):
    def test_spike_from_payload(self):
        sig = signal_from_payload("TST", {"mentions_z": 4.2, "mentions": 1800,
                                          "sentiment": {"score": -0.3, "label": "negative"}},
                                  as_of=date(2026, 7, 26))
        self.assertAlmostEqual(sig.buzz_z, 4.2)
        self.assertEqual(sig.trend, "PIC")
        self.assertTrue(sig.is_spike)
        self.assertEqual(sig.mentions, 1800)
        self.assertAlmostEqual(sig.sentiment, -0.3)
        self.assertEqual(sig.sentiment_label, "negative")

    def test_calm_and_float_sentiment(self):
        sig = signal_from_payload("TST", {"buzz": 0.4, "sentiment": 0.1, "count": 12})
        self.assertEqual(sig.trend, "calme")
        self.assertFalse(sig.is_spike)
        self.assertAlmostEqual(sig.sentiment, 0.1)

    def test_empty_payload(self):
        sig = signal_from_payload("TST", None)
        self.assertEqual(sig.trend, "")
        self.assertIsNone(sig.buzz_z)

    def test_hint_mentions_spike_risk(self):
        sig = signal_from_payload("TST", {"mentions_z": 4.0})
        self.assertIn("rumeur/retail", sig.hint())


class TestNoScoringImpact(unittest.TestCase):
    """Le social est un contexte : il ne doit RIEN changer au scoring."""

    def _uni(self):
        return dict(ticker="TST", symbol="TST", name="Test", sector="Tech",
                    place="NYSE", region="US", market_cap="150000000000",
                    analyst_coverage="20", target_position_value="1000000")

    def test_social_does_not_change_dis_or_conviction(self):
        stock, mkt = _stock_with_shock(), _market()
        route_b = dict(cause_class="SECTOR_CONTAGION", permanence="TRANSITORY",
                       expected_resolution_days="25", capi_effacee="40000000000",
                       impact_flux_actualise="10000000000", net_debt_ebitda="2.0",
                       recovery_precedents="3")
        base = build_candidate(self._uni(), stock, mkt, overlay=route_b)
        res_base = evaluate_candidate(base.candidate, bars=base.bars,
                                      shock_idx=base.shock_idx, stab_features=base.stab_features)

        with_social = dict(route_b, social_buzz_z="4.5", social_sentiment="-0.4",
                           social_mentions="2000", social_trend="PIC")
        soc = build_candidate(self._uni(), stock, mkt, overlay=with_social)
        res_soc = evaluate_candidate(soc.candidate, bars=soc.bars,
                                     shock_idx=soc.shock_idx, stab_features=soc.stab_features)

        # DIS, conviction, taille, admission : identiques avec ou sans social.
        self.assertAlmostEqual(res_base.candidate.dis, res_soc.candidate.dis, places=9)
        self.assertAlmostEqual(res_base.conviction.conv, res_soc.conviction.conv, places=9)
        self.assertAlmostEqual(res_base.sizing.final_size, res_soc.sizing.final_size, places=9)
        self.assertEqual(res_base.admitted, res_soc.admitted)
        # …mais le contexte social est bien porté par le candidat.
        self.assertEqual(res_soc.candidate.social_trend, "PIC")
        self.assertAlmostEqual(res_soc.candidate.social_buzz_z, 4.5)

    def test_fiche_renders_social_context_and_risk(self):
        from screener.output import dossier
        soc = build_candidate(self._uni(), _stock_with_shock(), _market(),
                              overlay=dict(cause_class="RUMOR_UNCONFIRMED",
                                           permanence="TRANSITORY",
                                           expected_resolution_days="25",
                                           social_buzz_z="4.5", social_trend="PIC"))
        text = dossier.render(evaluate_candidate(
            soc.candidate, bars=soc.bars, shock_idx=soc.shock_idx,
            stab_features=soc.stab_features))
        self.assertIn("CONTEXTE SOCIAL", text)
        self.assertIn("pic de buzz", text)


class TestGracefulDegradation(unittest.TestCase):
    def test_fetch_social_no_key_returns_none(self):
        self.assertIsNone(fetch_social("AAPL", api_key=""))


if __name__ == "__main__":
    unittest.main()
