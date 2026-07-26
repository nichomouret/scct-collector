#!/usr/bin/env python3
"""
Tests de l'ingestion de fondamentaux (TwelveData) — parsing pur, hors ligne.

    python -m unittest screener.tests.test_fundamentals
"""
from __future__ import annotations

import os
import unittest

from screener.ingestion.fundamentals import (
    Fundamentals, fetch_fundamentals, fundamentals_from_payloads,
)


class TestFundamentalsParsing(unittest.TestCase):
    def test_nested_statistics_schema(self):
        stats = {"statistics": {
            "valuations_metrics": {"market_capitalization": 2.5e12},
            "stock_statistics": {"shares_outstanding": 1.5e10}}}
        quote = {"name": "Apple Inc", "close": "190.5",
                 "fifty_two_week": {"low": "160.0", "high": "200.0"}}
        f = fundamentals_from_payloads("AAPL", stats, quote)
        self.assertAlmostEqual(f.market_cap, 2.5e12)
        self.assertAlmostEqual(f.shares_outstanding, 1.5e10)
        self.assertAlmostEqual(f.price, 190.5)
        self.assertAlmostEqual(f.fifty_two_week_low, 160.0)
        self.assertEqual(f.name, "Apple Inc")
        self.assertEqual(f.ticker, "AAPL")

    def test_flat_schema(self):
        f = fundamentals_from_payloads("X", {"market_capitalization": "8.0e8"},
                                       {"price": "12.3"})
        self.assertAlmostEqual(f.market_cap, 8.0e8)
        self.assertAlmostEqual(f.price, 12.3)

    def test_market_cap_fallback_from_price_times_shares(self):
        stats = {"statistics": {"stock_statistics": {"shares_outstanding": 1.0e8}}}
        quote = {"close": "5.0"}
        f = fundamentals_from_payloads("Y", stats, quote)
        self.assertAlmostEqual(f.market_cap, 5.0e8)   # 5 × 1e8

    def test_missing_payloads_degrade_to_none(self):
        f = fundamentals_from_payloads("Z", None, None)
        self.assertIsNone(f.market_cap)
        self.assertIsNone(f.price)
        self.assertEqual(f.ticker, "Z")

    def test_error_status_ignored_by_fetch_without_key(self):
        # sans clé, fetch renvoie des fondamentaux vides sans lever d'exception
        old = os.environ.pop("TWELVEDATA_API_KEY", None)
        try:
            f = fetch_fundamentals("ANY")
            self.assertIsInstance(f, Fundamentals)
            self.assertIsNone(f.market_cap)
        finally:
            if old is not None:
                os.environ["TWELVEDATA_API_KEY"] = old

    def test_tolerant_number_parsing(self):
        f = fundamentals_from_payloads("Q", {"market_capitalization": "NA"},
                                       {"close": ""})
        self.assertIsNone(f.market_cap)
        self.assertIsNone(f.price)


if __name__ == "__main__":
    unittest.main()
