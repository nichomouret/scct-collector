#!/usr/bin/env python3
"""
Tests du filtrage de l'univers (build_universe) — exclusions §3.1, hors ligne.

    python -m unittest screener.tests.test_universe
"""
from __future__ import annotations

import unittest

from screener.build_universe import _EXCLUDE_SUFFIX, _VALID


class TestTickerFilters(unittest.TestCase):
    def test_excludes_preferred_and_derivatives(self):
        for sym in ("MS-PK", "BAC-PL", "XYZ-WT", "ABC-WS", "DEF-U", "GHI-R", "JKL-RT"):
            self.assertTrue(_EXCLUDE_SUFFIX.search(sym), f"{sym} devrait être exclu")

    def test_keeps_common_and_class_shares(self):
        for sym in ("AAPL", "BRK-B", "BRK-A", "MSFT", "GOOGL", "GL"):
            self.assertIsNone(_EXCLUDE_SUFFIX.search(sym), f"{sym} devrait être gardé")
            self.assertTrue(_VALID.match(sym))


if __name__ == "__main__":
    unittest.main()
