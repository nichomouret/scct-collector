#!/usr/bin/env python3
"""
Tests du tableau de bord interactif (couche 4) — hors ligne, sans réseau.

    python -m unittest screener.tests.test_dashboard
"""
from __future__ import annotations

import unittest

from screener.demo_dashboard import build_results
from screener.engine import evaluate_candidate
from screener.models import Candidate, Region
from screener.output.dashboard import render_html


class TestDashboard(unittest.TestCase):
    def setUp(self):
        self.results = build_results()

    def test_demo_covers_all_routes(self):
        routes = {rt.value for r in self.results for rt in r.route_labels}
        self.assertSetEqual(routes, {"A", "B", "C", "D", "E"})

    def test_admitted_and_watch(self):
        # cinq admis (A-E), un seul en surveillance
        admitted = [r for r in self.results if r.admitted]
        self.assertEqual(len(admitted), 5)
        self.assertTrue(any(not r.admitted for r in self.results))

    def test_route_c_size_capped(self):
        c = next(r for r in self.results if any(rt.value == "C" for rt in r.route_labels))
        self.assertLessEqual(c.sizing.final_size, 0.5)  # queue gauche → cap 0.5x

    def test_renders_standalone(self):
        h = render_html(self.results, as_of="2026-07-24", universe="démo")
        self.assertIn("<!doctype html>", h)
        self.assertIn("Short-list du jour", h)
        self.assertIn("IBM", h)
        self.assertIn("ADMIS", h)
        self.assertIn("surveiller", h)          # le non-admis apparaît
        self.assertIn("Ce qui invalide", h)     # bloc dépliable
        self.assertIn("<script>", h)            # filtres/tri client
        self.assertIn("data-routes", h)         # attributs de filtrage
        self.assertIn('class="plan"', h)        # bloc entrée/stop/objectif
        self.assertIn("Objectif", h)
        self.assertIn("time-stop 40", h)

    def test_renders_embedded(self):
        h = render_html(self.results, standalone=False)
        self.assertNotIn("<!doctype", h)
        self.assertIn("<style>", h)

    def test_filter_and_sort_controls(self):
        h = render_html(self.results)
        for f in ('data-f="all"', 'data-f="adm"', 'data-f="A"', 'data-f="E"'):
            self.assertIn(f, h)
        self.assertIn('id="sort"', h)
        for opt in ("conviction", "dislocation", "horizon"):
            self.assertIn(opt, h)

    def test_sorted_admitted_first_then_conviction(self):
        h = render_html(self.results)
        # le non-admis (XYZ) doit apparaître après tous les admis
        pos_xyz = h.index("XYZ")
        for tk in ("IBM", "Abivax", "SES-imagotag"):
            self.assertLess(h.index(tk), pos_xyz)

    def test_empty(self):
        h = render_html([], standalone=True)
        self.assertIn("Aucun candidat", h)
        self.assertIn("<!doctype", h)

    def test_admitted_ordering_by_conviction(self):
        # une carte data-conv doit être présente et numérique
        c = Candidate(ticker="T", name="T", region=Region.US, market_cap=1e9,
                      price=10.0, adv_20d=5e6, analyst_coverage=3,
                      target_position_value=1e6, dis=1.0)
        res = evaluate_candidate(c)
        h = render_html([res])
        self.assertIn('data-conv="', h)


if __name__ == "__main__":
    unittest.main()
