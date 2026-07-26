#!/usr/bin/env python3
"""
Socle commun S1-S5 (§7.1) — la porte non négociable, commune aux cinq routes.
=============================================================================
Le socle décide de l'*admissibilité brute* : un candidat qui échoue une seule
condition S1..S5 est rejeté, quelle que soit la route. S4 (bornage de perte)
ne rejette presque jamais — il *sélectionne le mécanisme* et donc le facteur
de taille (§7.2). C'est le déplacement central de la v1.2 : la discipline
passe de l'admission au sizing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

from ..models import Candidate, LossBoundMechanism, S4_SIZE_FACTOR, Region

# §3.1 — seuils d'éligibilité de couche 1, par région.
ELIGIBILITY = {
    "market_cap_min": {Region.US: 200e6, Region.EU: 150e6, Region.IL: 80e6},
    "adv_20d_min": {Region.US: 2e6, Region.EU: 1e6, Region.IL: 0.5e6},
    "price_min": 3.0,
    "analyst_coverage_min": 2,
    "listing_age_min_days": 250,
    "exclude_sectors": frozenset({"SPAC", "Shell", "Closed-End Fund"}),
    "exclude_flags": frozenset({"going_concern", "delisting_notice", "trading_halt_pending"}),
    "exit_liquidity_max_days_adv": 2.0,   # S2
    "resolution_max_days": 40,            # S3
    "coussin_min": 0.15,                  # S4(a)
    "hard_stop_max_distance": 0.12,       # S4(b)
    "debt_wall_min_days": 90,             # S5
}

Check = Tuple[str, bool, str]  # (nom, ok, détail)


@dataclass
class FloorResult:
    passed: bool
    checks: List[Check]
    mechanism: LossBoundMechanism        # mécanisme S4 retenu (inscrit dans la fiche)
    size_factor: float                   # facteur_S4

    def failures(self) -> List[Check]:
        return [c for c in self.checks if not c[1]]


# --------------------------------------------------------------------------- #
# S1 — éligibilité univers                                                     #
# --------------------------------------------------------------------------- #
def _s1_eligibility(c: Candidate) -> Check:
    reasons = []
    if c.market_cap < ELIGIBILITY["market_cap_min"][c.region]:
        reasons.append(f"capi {c.market_cap:.0f} < {ELIGIBILITY['market_cap_min'][c.region]:.0f}")
    if c.adv_20d < ELIGIBILITY["adv_20d_min"][c.region]:
        reasons.append(f"ADV {c.adv_20d:.0f} < {ELIGIBILITY['adv_20d_min'][c.region]:.0f}")
    if c.price < ELIGIBILITY["price_min"]:
        reasons.append(f"prix {c.price:.2f} < {ELIGIBILITY['price_min']}")
    if c.analyst_coverage < ELIGIBILITY["analyst_coverage_min"]:
        reasons.append(f"couverture {c.analyst_coverage} < {ELIGIBILITY['analyst_coverage_min']}")
    if c.listing_age_days < ELIGIBILITY["listing_age_min_days"]:
        reasons.append(f"âge cotation {c.listing_age_days}j < {ELIGIBILITY['listing_age_min_days']}")
    if set(c.sector_flags) & ELIGIBILITY["exclude_sectors"]:
        reasons.append(f"secteur exclu {set(c.sector_flags) & ELIGIBILITY['exclude_sectors']}")
    if set(c.exclude_flags) & ELIGIBILITY["exclude_flags"]:
        reasons.append(f"flag exclu {set(c.exclude_flags) & ELIGIBILITY['exclude_flags']}")
    ok = not reasons
    return ("S1 éligibilité univers", ok, "ok" if ok else " ; ".join(reasons))


# --------------------------------------------------------------------------- #
# S2 — liquidité de sortie                                                     #
# --------------------------------------------------------------------------- #
def _s2_exit_liquidity(c: Candidate) -> Check:
    d = c.days_of_adv
    if d is None:
        return ("S2 liquidité de sortie", False, "ADV inconnu")
    ok = d <= ELIGIBILITY["exit_liquidity_max_days_adv"]
    return ("S2 liquidité de sortie", ok, f"{d:.2f} j d'ADV (max {ELIGIBILITY['exit_liquidity_max_days_adv']})")


# --------------------------------------------------------------------------- #
# S3 — horizon                                                                 #
# --------------------------------------------------------------------------- #
def _s3_resolution(c: Candidate) -> Check:
    r = c.expected_resolution_days
    if r is None:
        return ("S3 horizon <= 40j", False, "résolution attendue inconnue")
    ok = r <= ELIGIBILITY["resolution_max_days"]
    return ("S3 horizon <= 40j", ok, f"{r}j attendus (max {ELIGIBILITY['resolution_max_days']})")


# --------------------------------------------------------------------------- #
# S4 — bornage de perte : sélection du mécanisme + facteur de taille           #
# --------------------------------------------------------------------------- #
def _s4_select_mechanism(c: Candidate) -> Tuple[LossBoundMechanism, str]:
    """
    Retient le mécanisme au meilleur facteur de taille parmi ceux applicables,
    dans l'ordre de préférence : (a) coussin, (c) option, (b) stop, (d) réduit.
    (a) et (c) sont à 1.0x ; on préfère (a) quand il existe car c'est un
    coussin fondamental, pas seulement une structure.
    """
    coussin = c.coussin
    if coussin is not None and coussin > ELIGIBILITY["coussin_min"]:
        return (LossBoundMechanism.VALUATION_CUSHION,
                f"coussin {coussin:+.1%} > {ELIGIBILITY['coussin_min']:.0%}")
    if c.option_structure_capped:
        return (LossBoundMechanism.OPTION_STRUCTURE, "structure optionnelle à perte plafonnée")
    if (c.hard_stop_distance is not None
            and c.hard_stop_distance <= ELIGIBILITY["hard_stop_max_distance"]
            and c.hard_stop_level_motivated):
        return (LossBoundMechanism.HARD_STOP,
                f"stop dur -{c.hard_stop_distance:.0%} (<= {ELIGIBILITY['hard_stop_max_distance']:.0%}), niveau motivé")
    if c.allow_reduced_size_fallback:
        return (LossBoundMechanism.REDUCED_SIZE, "taille réduite seule (0.5x)")
    return (LossBoundMechanism.NONE, "aucun mécanisme de bornage identifié")


def _s4_loss_bound(c: Candidate) -> Tuple[Check, LossBoundMechanism, float]:
    mech, detail = _s4_select_mechanism(c)
    ok = mech is not LossBoundMechanism.NONE
    factor = S4_SIZE_FACTOR[mech]
    return (("S4 perte bornée", ok, f"{mech.value} — {detail}"), mech, factor)


# --------------------------------------------------------------------------- #
# S5 — risque existentiel                                                      #
# --------------------------------------------------------------------------- #
def _s5_existential(c: Candidate) -> Check:
    reasons = []
    if c.covenant_breach:
        reasons.append("covenant breach")
    if c.debt_wall_days is not None and c.debt_wall_days < ELIGIBILITY["debt_wall_min_days"]:
        reasons.append(f"mur de dette {c.debt_wall_days}j < {ELIGIBILITY['debt_wall_min_days']}")
    if c.going_concern:
        reasons.append("going concern")
    if c.trading_suspension:
        reasons.append("suspension de cotation")
    ok = not reasons
    return ("S5 pas de risque existentiel", ok, "ok" if ok else " ; ".join(reasons))


def evaluate_floor(c: Candidate) -> FloorResult:
    """Évalue le socle complet. Retourne le mécanisme S4 et le facteur de taille."""
    s1 = _s1_eligibility(c)
    s2 = _s2_exit_liquidity(c)
    s3 = _s3_resolution(c)
    s4_check, mech, factor = _s4_loss_bound(c)
    s5 = _s5_existential(c)
    checks = [s1, s2, s3, s4_check, s5]
    passed = all(ok for _, ok, _ in checks)
    return FloorResult(passed=passed, checks=checks, mechanism=mech, size_factor=factor)
