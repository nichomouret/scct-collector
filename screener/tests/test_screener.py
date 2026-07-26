#!/usr/bin/env python3
"""
Tests du moteur de décision (couche 4). Stdlib uniquement (unittest) :

    python -m unittest discover -s screener/tests

Couvre le socle S1-S5, les cinq routes avec leurs cas d'école (IBM→B,
2CRSi→C, Abivax→D), la renormalisation de conviction par composantes actives,
le sizing (multiplicateur, cap route C, facteur S4), la couche PATH
(archétypes, STAB, entrée par tranches) et les règles de sortie.
"""
from __future__ import annotations

import unittest

from screener.models import (
    Candidate, Catalyst, CatalystPower, CauseClass, DateCertainty,
    LossBoundMechanism, Permanence, Region, Route, Archetype,
)
from screener.scoring.floor import evaluate_floor
from screener.scoring.rebuttal import RebuttalInputs, rebuttal_score
from screener.scoring.routes import validated_routes
from screener.scoring import conviction as conv
from screener.detection.path_archetype import PriceBar, classify_archetype
from screener.detection.stabilization import (
    StabFeatures, stabilization_score, entry_schedule,
)
from screener.execution.exit_rules import (
    PositionState, ExitReason, evaluate_exit,
)
from screener.engine import evaluate_candidate
from screener.output import dossier


def base_eligible(**kw) -> Candidate:
    """Un candidat dont le socle passe ; surchargé par test via kwargs."""
    d = dict(
        ticker="TST", name="Test SA", sector="Tech", place="XPAR",
        region=Region.US, market_cap=1_000e6, price=20.0,
        adv_20d=5_000_000, analyst_coverage=5, listing_age_days=1000,
        target_position_value=5_000_000,   # 1.0 j d'ADV
        expected_resolution_days=25,
    )
    d.update(kw)
    return Candidate(**d)


# --------------------------------------------------------------------------- #
class TestFloor(unittest.TestCase):
    def test_passes_with_cushion(self):
        c = base_eligible(fv_low=25.0)   # coussin +25 %
        f = evaluate_floor(c)
        self.assertTrue(f.passed)
        self.assertIs(f.mechanism, LossBoundMechanism.VALUATION_CUSHION)
        self.assertEqual(f.size_factor, 1.0)

    def test_reduced_size_fallback(self):
        c = base_eligible()              # aucun mécanisme explicite
        f = evaluate_floor(c)
        self.assertTrue(f.passed)
        self.assertIs(f.mechanism, LossBoundMechanism.REDUCED_SIZE)
        self.assertEqual(f.size_factor, 0.5)

    def test_hard_stop_mechanism(self):
        c = base_eligible(hard_stop_distance=0.10, hard_stop_level_motivated=True)
        f = evaluate_floor(c)
        self.assertIs(f.mechanism, LossBoundMechanism.HARD_STOP)
        self.assertEqual(f.size_factor, 0.7)

    def test_s3_horizon_gate(self):
        c = base_eligible(expected_resolution_days=90)
        self.assertFalse(evaluate_floor(c).passed)

    def test_s2_exit_liquidity_gate(self):
        c = base_eligible(target_position_value=20_000_000)  # 4 j d'ADV
        self.assertFalse(evaluate_floor(c).passed)

    def test_s5_existential_gate(self):
        c = base_eligible(fv_low=25.0, going_concern=True)
        self.assertFalse(evaluate_floor(c).passed)

    def test_s1_small_cap_rejected(self):
        c = base_eligible(market_cap=50e6)
        self.assertFalse(evaluate_floor(c).passed)

    def test_no_fallback_fails_s4(self):
        c = base_eligible(allow_reduced_size_fallback=False)
        f = evaluate_floor(c)
        self.assertFalse(f.passed)
        self.assertIs(f.mechanism, LossBoundMechanism.NONE)


# --------------------------------------------------------------------------- #
class TestRebuttal(unittest.TestCase):
    def test_2crsi_like_passes_threshold(self):
        r = RebuttalInputs(point_by_point_response=2, independent_audit_mandated=2,
                           named_counterparty=1, insider_buys=2)
        self.assertEqual(rebuttal_score(r), 7)

    def test_caps_subscores(self):
        r = RebuttalInputs(point_by_point_response=9, attacker_track_record=9,
                           no_urgent_financing=9)
        self.assertEqual(rebuttal_score(r), 2 + 1 + 1)


# --------------------------------------------------------------------------- #
class TestRoutes(unittest.TestCase):
    def test_route_a_technical(self):
        c = base_eligible(dis=3.5, days_since_shock=2,
                          cause_class=CauseClass.INDEX_REBALANCE, z_volume=4.0,
                          expected_resolution_days=8)
        routes = [r.route for r in validated_routes(c)]
        self.assertIn(Route.A, routes)

    def test_route_b_ibm_non_discounted(self):
        # IBM -25 % : NON décoté (pas de fv_low) mais sur-réaction admise par B.
        c = base_eligible(
            capi_effacee=4_000e6, impact_flux_actualise=1_000e6,  # ratio 4.0
            days_since_shock=2, permanence=Permanence.TRANSITORY,
            net_debt_ebitda=2.0, recovery_precedents=3, dis=2.8,
        )
        routes = [r.route for r in validated_routes(c)]
        self.assertIn(Route.B, routes)
        # non décoté -> S4 tombe sur taille réduite
        self.assertIs(evaluate_floor(c).mechanism, LossBoundMechanism.REDUCED_SIZE)

    def test_route_c_rebuttal(self):
        c = base_eligible(cause_class=CauseClass.SHORT_REPORT, dis=3.2,
                          rebut_score=7, response_date_days=15)
        routes = [r.route for r in validated_routes(c)]
        self.assertIn(Route.C, routes)

    def test_route_c_fails_below_threshold(self):
        c = base_eligible(cause_class=CauseClass.SHORT_REPORT, dis=3.2,
                          rebut_score=5, response_date_days=15)
        self.assertNotIn(Route.C, [r.route for r in validated_routes(c)])

    def test_route_d_abivax_binary(self):
        cat = Catalyst("PHASE3_READOUT", "2026-09-15", DateCertainty.APPROXIMATE,
                       days_to_catalyst=30, binary=True, expected_move_pct=20.0,
                       power=CatalystPower.VERY_HIGH)
        c = base_eligible(catalyst=cat, manual_prob_gap=0.43, upside_thesis_pct=40.0,
                          scenario_documented=True, expected_resolution_days=32,
                          option_structure_capped=True, dis=0.0)
        routes = [r.route for r in validated_routes(c)]
        self.assertIn(Route.D, routes)  # dislocation NON requise (dis=0)

    def test_route_e_value_plus_catalyst(self):
        cat = Catalyst("EARNINGS", "2026-08-15", DateCertainty.CERTAIN,
                       days_to_catalyst=20, power=CatalystPower.HIGH)
        c = base_eligible(fv_low=25.0, val_z=1.5, catalyst=cat, dis=2.5,
                          aqs=1.5, expected_resolution_days=22)
        routes = [r.route for r in validated_routes(c)]
        self.assertIn(Route.E, routes)


# --------------------------------------------------------------------------- #
class TestConviction(unittest.TestCase):
    def test_active_component_renorm_not_penalized(self):
        # Une route B (active: dis) ne doit pas être pénalisée pour l'absence
        # de valorisation : CONV_base doit valoir norm_dis, pas norm_dis*poids.
        c = base_eligible(dis=5.0)
        cb = conv.conv_base(c, ("dis",))
        self.assertAlmostEqual(cb, conv.norm_dis(5.0), places=6)
        self.assertAlmostEqual(cb, 1.0, places=6)

    def test_absent_component_excluded_from_denominator(self):
        # E déclare 5 composantes ; sans catalyst/aqs/val/coussin, seul dis compte.
        c = base_eligible(dis=4.0)
        cb = conv.conv_base(c, ("dis", "catalyst", "aqs", "coussin", "val"))
        self.assertAlmostEqual(cb, conv.norm_dis(4.0), places=6)

    def test_route_multiplier(self):
        self.assertEqual(conv.route_multiplier(1), 1.00)
        self.assertEqual(conv.route_multiplier(2), 1.35)
        self.assertEqual(conv.route_multiplier(3), 1.60)
        self.assertEqual(conv.route_multiplier(5), 1.60)  # plafonné à 3


# --------------------------------------------------------------------------- #
class TestSizing(unittest.TestCase):
    def test_route_c_cap_absolute(self):
        # C + D valident (mult 1.35) mais la taille est plafonnée à 0.5x.
        s = conv.size_position(standard_size=1.0, s4_factor=1.0, n_routes=2,
                               validated=[Route.C, Route.D], regime_factor=1.0)
        self.assertTrue(s.capped)
        self.assertAlmostEqual(s.final_size, 0.5, places=6)

    def test_multiplier_applies_without_cap(self):
        s = conv.size_position(standard_size=1.0, s4_factor=1.0, n_routes=2,
                               validated=[Route.B, Route.D], regime_factor=1.0)
        self.assertFalse(s.capped)
        self.assertAlmostEqual(s.final_size, 1.35, places=6)

    def test_s4_factor_and_regime(self):
        s = conv.size_position(standard_size=1.0, s4_factor=0.7, n_routes=1,
                               validated=[Route.A], regime_factor=0.5)
        self.assertAlmostEqual(s.final_size, 0.35, places=6)


# --------------------------------------------------------------------------- #
class TestPathArchetypes(unittest.TestCase):
    def _bar(self, o, h, l, c, v, d="d"):
        return PriceBar(d, o, h, l, c, v)

    def test_capitulation_v(self):
        # baseline avec variance (σ>0) sinon pas de z-score de volume calculable
        base = [self._bar(100, 101, 99, 100, 900 + 40 * (i % 5)) for i in range(10)]
        shock = self._bar(100, 100, 80, 97, 9000)   # vol >>5σ, clôture haute, mèche basse
        post = [self._bar(97, 100, 96, 99, 3000),
                self._bar(99, 102, 98, 101, 2500),
                self._bar(101, 104, 100, 103, 2000)]
        bars = base + [shock] + post
        self.assertEqual(classify_archetype(bars, 10), Archetype.CAPITULATION_V)

    def test_gap_and_flat(self):
        base = [self._bar(100, 101, 99, 100, 1000) for _ in range(10)]
        shock = self._bar(100, 100, 78, 80, 2000)    # gros gap baissier
        post = [self._bar(80, 82, 79, 81, 1500),     # ranges étroits, volume décroissant
                self._bar(81, 82, 80, 81, 1200),
                self._bar(81, 82, 80, 81, 900)]
        bars = base + [shock] + post
        self.assertEqual(classify_archetype(bars, 10), Archetype.GAP_AND_FLAT)

    def test_grinding_decline_blocking(self):
        base = [self._bar(100, 101, 99, 100, 1000) for _ in range(10)]
        shock = self._bar(100, 100, 95, 96, 2000)
        post = [self._bar(96, 96, 92, 93, 2100),     # clôtures basses, nouveaux plus bas
                self._bar(93, 93, 89, 90, 2200),
                self._bar(90, 90, 86, 87, 2300),
                self._bar(87, 87, 83, 84, 2400),
                self._bar(84, 84, 80, 81, 2500)]
        bars = base + [shock] + post
        self.assertEqual(classify_archetype(bars, 10), Archetype.GRINDING_DECLINE)

    def test_failed_bounce(self):
        base = [self._bar(100, 101, 99, 100, 1000) for _ in range(10)]
        shock = self._bar(100, 100, 90, 91, 2000)
        post = [self._bar(91, 100, 91, 99, 1800),    # rebond >8 %
                self._bar(99, 99, 88, 89, 1900)]     # retour sous le point bas
        bars = base + [shock] + post
        self.assertEqual(classify_archetype(bars, 10), Archetype.FAILED_BOUNCE)


# --------------------------------------------------------------------------- #
class TestStabilization(unittest.TestCase):
    def test_score_and_threshold(self):
        f = StabFeatures(volume_contraction_ratio=0.4, closes_high_in_range=3,
                         no_new_low_3d=True, vwap_anchored_reconquered=True,
                         first_higher_low=True, rv_iv_converging=True)
        self.assertEqual(stabilization_score(f), 10)

    def test_partial_score(self):
        f = StabFeatures(volume_contraction_ratio=0.6, closes_high_in_range=1,
                         no_new_low_3d=True)
        self.assertEqual(stabilization_score(f), 1 + 1 + 2)

    def test_blocking_archetype_blocks_tranche1(self):
        plan = entry_schedule(True, True, Archetype.GRINDING_DECLINE, stab=8)
        self.assertFalse(plan.tranche1_ok)
        self.assertIn("bloquant", plan.blocked_reason)

    def test_good_archetype_tranche1_immediate(self):
        plan = entry_schedule(True, True, Archetype.GAP_AND_FLAT, stab=7)
        self.assertTrue(plan.tranche1_ok)
        self.assertEqual(plan.tranche1_size, 0.5)
        self.assertTrue(plan.tranche2_armed)


# --------------------------------------------------------------------------- #
class TestExitRules(unittest.TestCase):
    def test_time_stop(self):
        d = evaluate_exit(PositionState(sessions_held=40, pnl_pct=0.05))
        self.assertTrue(d.should_exit)
        self.assertIs(d.reason, ExitReason.TIME_STOP)

    def test_max_adverse_excursion(self):
        d = evaluate_exit(PositionState(sessions_held=5, pnl_pct=-0.16))
        self.assertIs(d.reason, ExitReason.MAX_ADVERSE_EXCURSION)

    def test_structural_stop(self):
        d = evaluate_exit(PositionState(sessions_held=5, pnl_pct=-0.05,
                                        new_low_below_capitulation=True))
        self.assertIs(d.reason, ExitReason.STRUCTURAL_STOP)

    def test_hold(self):
        d = evaluate_exit(PositionState(sessions_held=10, pnl_pct=0.03))
        self.assertFalse(d.should_exit)

    def test_catalyst_resolved(self):
        d = evaluate_exit(PositionState(sessions_held=10, pnl_pct=0.1,
                                        catalyst_resolved=True,
                                        sessions_since_catalyst=2))
        self.assertIs(d.reason, ExitReason.CATALYST_RESOLVED)


# --------------------------------------------------------------------------- #
class TestEngineIntegration(unittest.TestCase):
    def test_superposition_b_and_d(self):
        # Le meilleur dossier : sur-réaction (B) sur un titre à binaire daté (D).
        cat = Catalyst("EARNINGS", "2026-09-15", DateCertainty.CERTAIN,
                       days_to_catalyst=28, binary=True, expected_move_pct=18.0,
                       power=CatalystPower.HIGH)
        c = base_eligible(
            fv_low=25.0,                               # coussin -> S4 facteur 1.0
            capi_effacee=4_000e6, impact_flux_actualise=1_000e6,
            days_since_shock=2, permanence=Permanence.TRANSITORY,
            net_debt_ebitda=2.0, recovery_precedents=3, dis=3.0,
            catalyst=cat, pms=0.25, upside_thesis_pct=32.0,
            scenario_documented=True, expected_resolution_days=28,
        )
        res = evaluate_candidate(c, standard_size=1.0)
        self.assertTrue(res.admitted)
        self.assertEqual(set(res.route_labels), {Route.B, Route.D})
        self.assertEqual(res.n_routes, 2)
        self.assertAlmostEqual(res.conviction.multiplier, 1.35, places=6)
        self.assertAlmostEqual(res.sizing.final_size, 1.35, places=6)  # 1.0×1.35, non plafonné

    def test_not_admitted_when_floor_fails(self):
        c = base_eligible(dis=3.5, days_since_shock=2,
                          cause_class=CauseClass.INDEX_REBALANCE, z_volume=4.0,
                          expected_resolution_days=90)  # échoue S3
        res = evaluate_candidate(c)
        self.assertFalse(res.admitted)

    def test_path_does_not_create_admission(self):
        # Aucune route validée : même avec un beau chemin, non admis.
        c = base_eligible()
        res = evaluate_candidate(c)
        self.assertFalse(res.admitted)
        self.assertEqual(res.n_routes, 0)

    def test_route_c_sizing_capped_in_engine(self):
        c = base_eligible(cause_class=CauseClass.SHORT_REPORT, dis=3.2,
                          rebut_score=8, response_date_days=15)
        res = evaluate_candidate(c, standard_size=1.0)
        self.assertIn(Route.C, res.route_labels)
        self.assertLessEqual(res.sizing.final_size, 0.5 + 1e-9)

    def test_dossier_renders(self):
        cat = Catalyst("PHASE3_READOUT", "2026-09-15", DateCertainty.APPROXIMATE,
                       days_to_catalyst=30, binary=True, expected_move_pct=20.0,
                       power=CatalystPower.VERY_HIGH)
        c = base_eligible(fv_low=25.0, catalyst=cat, manual_prob_gap=0.43,
                          upside_thesis_pct=40.0, scenario_documented=True,
                          expected_resolution_days=30, dis=3.0, aqs=1.6,
                          insider_buy=True, z_res=-3.1, z_volume=4.2)
        res = evaluate_candidate(c, standard_size=1.0)
        text = dossier.render(res)
        self.assertIn("ROUTE(S)", text)
        self.assertIn("CE QUI INVALIDE LA THÈSE", text)
        self.assertIn("CATALYSEUR", text)
        self.assertIn("TAILLE CIBLE", text)


if __name__ == "__main__":
    unittest.main()
