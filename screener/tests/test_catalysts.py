#!/usr/bin/env python3
"""
Tests du registre de catalyseurs (P1) — hors ligne, date de référence figée.

    python -m unittest screener.tests.test_catalysts
"""
from __future__ import annotations

import unittest
from datetime import date

from screener.models import CatalystPower, DateCertainty
from screener.ingestion.catalysts import RawCatalyst, parse_partial_date
from screener.universe.catalyst_registry import new_registry

ASOF = date(2026, 7, 26)


def _raw(ticker, iso, power=CatalystPower.HIGH, ctype="CLINICAL_READOUT", binary=True):
    return RawCatalyst(ticker=ticker, catalyst_type=ctype, date_expected=iso,
                       date_certainty=DateCertainty.APPROXIMATE, binary=binary,
                       power=power, as_of=ASOF.isoformat())


class TestDateParsing(unittest.TestCase):
    def test_full_date(self):
        self.assertEqual(parse_partial_date("2026-09-15"), "2026-09-15")

    def test_year_month_anchors_mid_month(self):
        self.assertEqual(parse_partial_date("2026-09"), "2026-09-15")

    def test_year_only(self):
        self.assertEqual(parse_partial_date("2026"), "2026-06-30")

    def test_invalid(self):
        self.assertIsNone(parse_partial_date(""))
        self.assertIsNone(parse_partial_date("pas-une-date"))
        self.assertIsNone(parse_partial_date(None))


class TestRegistry(unittest.TestCase):
    def test_pit_days_to_catalyst(self):
        reg = new_registry()
        reg.add(_raw("TST", "2026-08-25"))          # J+30
        cat = reg.catalyst_for("TST", as_of=ASOF)
        self.assertIsNotNone(cat)
        self.assertEqual(cat.days_to_catalyst, 30)

    def test_window_filter(self):
        reg = new_registry()
        reg.add(_raw("FAR", "2026-10-01"))          # J+67 -> hors fenêtre 45j
        reg.add(_raw("PAST", "2026-07-01"))         # passé -> exclu
        self.assertIsNone(reg.catalyst_for("FAR", as_of=ASOF))
        self.assertIsNone(reg.catalyst_for("PAST", as_of=ASOF))

    def test_nearest_selected(self):
        reg = new_registry()
        reg.add(_raw("TST", "2026-09-10"))          # J+46 -> hors fenêtre
        reg.add(_raw("TST", "2026-08-10"))          # J+15 -> retenu (le plus proche éligible)
        cat = reg.catalyst_for("TST", as_of=ASOF)
        self.assertEqual(cat.days_to_catalyst, 15)

    def test_power_tiebreak_same_date(self):
        reg = new_registry()
        reg.add(_raw("TST", "2026-08-25", power=CatalystPower.MEDIUM, ctype="EARNINGS", binary=False))
        reg.add(_raw("TST", "2026-08-25", power=CatalystPower.VERY_HIGH, ctype="CLINICAL_READOUT"))
        cat = reg.catalyst_for("TST", as_of=ASOF)
        self.assertEqual(cat.catalyst_type, "CLINICAL_READOUT")
        self.assertIs(cat.power, CatalystPower.VERY_HIGH)

    def test_to_rows_format(self):
        reg = new_registry()
        reg.add(_raw("TST", "2026-08-25"))
        rows = reg.to_rows(as_of=ASOF)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        for col in ("ticker", "catalyst_type", "date_expected", "date_certainty",
                    "days_to_catalyst", "binary", "power", "source_url"):
            self.assertIn(col, r)
        self.assertEqual(r["ticker"], "TST")
        self.assertEqual(r["days_to_catalyst"], 30)


if __name__ == "__main__":
    unittest.main()
