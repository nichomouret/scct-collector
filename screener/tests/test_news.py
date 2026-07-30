#!/usr/bin/env python3
"""
Tests de la classification de news (§5.3, P4) — hors ligne, sans appel LLM.
Teste les fonctions PURES (parsing, gating, mapping overlay) sur des payloads
synthétiques, plus la dégradation gracieuse sans clé.

    python -m unittest screener.tests.test_news
"""
from __future__ import annotations

import os
import unittest
from datetime import date
from unittest import mock

from screener.models import CauseClass, Permanence
from screener.ingestion.news import (
    NewsItem, clean_company_name, company_query, fetch_news,
)
from screener.qualification.news_classifier import (
    NewsClassification, parse_classification, passes_gating, classify_news,
)


def _payload(**over):
    d = {
        "cause_class": "SECTOR_CONTAGION", "permanence": "TRANSITORY",
        "expected_resolution_days": 25, "cash_flow_impact_pct": -12.0,
        "source_reliability": "TIER1_MEDIA", "confidence": 0.8,
        "evidence_urls": ["https://example.com/a"],
        "analysis": "Baisse par contagion sectorielle sans dépôt réglementaire ; "
                    "sur-réaction probable, risque = confirmation de la rumeur.",
    }
    d.update(over)
    return d


class TestParsing(unittest.TestCase):
    def test_parse_full(self):
        c = parse_classification(_payload())
        self.assertIs(c.cause_class, CauseClass.SECTOR_CONTAGION)
        self.assertIs(c.permanence, Permanence.TRANSITORY)
        self.assertEqual(c.expected_resolution_days, 25)
        self.assertAlmostEqual(c.cash_flow_impact_pct, -12.0)
        self.assertAlmostEqual(c.confidence, 0.8)
        self.assertEqual(c.evidence_urls, ["https://example.com/a"])
        self.assertIn("contagion", c.analysis)

    def test_parse_tolerates_garbage(self):
        c = parse_classification({"cause_class": "PAS_UNE_CLASSE", "permanence": "?",
                                  "expected_resolution_days": "x", "confidence": None})
        self.assertIsNone(c.cause_class)
        self.assertIs(c.permanence, Permanence.UNKNOWN)
        self.assertIsNone(c.expected_resolution_days)
        self.assertEqual(c.confidence, 0.0)


class TestGating(unittest.TestCase):
    def test_gating_cause_and_horizon(self):
        # cause éligible + horizon <= 40 -> passe
        self.assertTrue(passes_gating(parse_classification(_payload(
            cause_class="FORCED_SELLING", permanence="UNKNOWN",
            expected_resolution_days=10))))

    def test_gating_transitory_rescues_ineligible_cause(self):
        # cause hors liste mais permanence TRANSITORY + horizon ok -> passe
        self.assertTrue(passes_gating(parse_classification(_payload(
            cause_class="GUIDANCE_CUT", permanence="TRANSITORY",
            expected_resolution_days=30))))

    def test_gating_fails_on_long_horizon(self):
        self.assertFalse(passes_gating(parse_classification(_payload(
            expected_resolution_days=90))))

    def test_gating_fails_permanent_ineligible(self):
        self.assertFalse(passes_gating(parse_classification(_payload(
            cause_class="FUNDAMENTAL_NEGATIVE", permanence="PERMANENT",
            expected_resolution_days=10))))


class TestOverlayMapping(unittest.TestCase):
    def test_as_overlay_row(self):
        row = parse_classification(_payload()).as_overlay_row("aapl")
        self.assertEqual(row["ticker"], "AAPL")
        self.assertEqual(row["cause_class"], "SECTOR_CONTAGION")
        self.assertEqual(row["permanence"], "TRANSITORY")
        self.assertEqual(row["expected_resolution_days"], 25)
        self.assertEqual(row["evidence_url"], "https://example.com/a")
        self.assertIn("contagion", row["analysis"])


class TestKeystoneIntegration(unittest.TestCase):
    """La classification news fournit les champs qui font passer S3 et les routes."""

    def _uni(self):
        return dict(ticker="TST", symbol="TST", name="Test", sector="Tech",
                    place="NYSE", region="US", market_cap="150000000000",
                    analyst_coverage="20", target_position_value="1000000")

    def test_news_classification_unlocks_admission(self):
        from screener.universe.candidate_builder import build_candidate
        from screener.engine import evaluate_candidate
        from screener.models import Route
        from screener.tests.test_vertical import _market, _stock_with_shock
        stock, mkt = _stock_with_shock(), _market()

        # champs route B, mais SANS cause/horizon (comme si la couche news manquait)
        route_b = dict(capi_effacee="40000000000", impact_flux_actualise="10000000000",
                       net_debt_ebitda="2.0", recovery_precedents="3")
        without = build_candidate(self._uni(), stock, mkt, overlay=route_b).candidate
        res_without = evaluate_candidate(without, bars=stock, shock_idx=0)
        self.assertFalse(res_without.admitted)   # S3 échoue : horizon inconnu

        # la classification news fournit cause_class / permanence / horizon
        news = parse_classification(_payload(cause_class="SECTOR_CONTAGION",
                                             permanence="TRANSITORY",
                                             expected_resolution_days=25))
        merged = {**news.as_overlay_row("TST"), **route_b}
        bc = build_candidate(self._uni(), stock, mkt, overlay=merged)
        res = evaluate_candidate(bc.candidate, bars=bc.bars, shock_idx=bc.shock_idx,
                                 stab_features=bc.stab_features)
        self.assertTrue(res.admitted)
        self.assertIn(Route.B, res.route_labels)


class TestCompanyQuery(unittest.TestCase):
    def test_strips_corporate_suffixes(self):
        self.assertEqual(clean_company_name("NORWEGIAN CRUISE LINE HOLDINGS LTD."),
                         "NORWEGIAN CRUISE LINE")
        self.assertEqual(clean_company_name("FirstService Corp"), "FirstService")
        self.assertEqual(clean_company_name("Xylem Inc."), "Xylem")

    def test_keeps_at_least_first_token(self):
        # un nom entièrement composé de désignations ne doit pas se vider
        self.assertTrue(clean_company_name("Holdings Corp"))

    def test_query_uses_quoted_clean_name(self):
        self.assertEqual(company_query("NORWEGIAN CRUISE LINE HOLDINGS LTD.", "NCLH"),
                         '"NORWEGIAN CRUISE LINE"')

    def test_query_falls_back_to_ticker(self):
        # nom == ticker (ou vide) -> on interroge le ticker
        self.assertEqual(company_query("NCLH", "NCLH"), "NCLH")
        self.assertEqual(company_query("", "ABC"), "ABC")


class TestGracefulDegradation(unittest.TestCase):
    # `api_key=""` retombe sur la variable d'environnement (convention du paquet) :
    # on la neutralise pour tester la VRAIE absence de clé, sinon le test échoue
    # sur une machine où ANTHROPIC_API_KEY/NEWS_API_KEY sont exportées.
    @mock.patch.dict(os.environ, {"NEWS_API_KEY": ""}, clear=False)
    def test_fetch_news_no_key_returns_empty(self):
        self.assertEqual(fetch_news("Apple", api_key="", from_date=date(2026, 7, 1)), [])

    @mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}, clear=False)
    def test_classify_news_no_key_returns_none(self):
        items = [NewsItem("t", "d", "src", "2026-07-25", "http://x")]
        self.assertIsNone(classify_news("AAPL", "Apple", items, api_key=""))


if __name__ == "__main__":
    unittest.main()
