#!/usr/bin/env python3
"""
Score de stabilisation STAB (0-10) + entrée par tranches — §7.3.2 / §7.3.3.
===========================================================================
Budget de features FERMÉ à 6 (§7.3.4, règle 1). Toute nouvelle feature en
remplace une et doit démontrer sa supériorité sur 40 trades de validation.
Aucun indicateur classique (règle 2). Barres closes uniquement (règle 4).

STAB n'est PAS une porte binaire (§7.3.3) : attendre la stabilisation a un
coût (certains titres repartent sans vous). On entre par tranches. Seuls les
trois archétypes de flux vendeur actif sont bloquants pour la tranche 1.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..models import Archetype, BLOCKING_ARCHETYPES

STAB_CONFIRMED = 6


@dataclass
class StabFeatures:
    """
    Les six features du barème (§7.3.2). Graduations = points de départ à
    calibrer (§7.3.4). Barres closes uniquement.
    """
    volume_contraction_ratio: Optional[float] = None  # vol 3 dern. / vol du choc
    closes_high_in_range: int = 0     # nb parmi les 3 dernières avec range_position>0.6 (0-3)
    no_new_low_3d: bool = False
    vwap_anchored_reconquered: bool = False
    first_higher_low: bool = False
    rv_iv_converging: bool = False

    def score(self) -> int:
        pts = 0
        r = self.volume_contraction_ratio
        if r is not None:                                  # meilleur prédicteur unique (§7.3.2)
            pts += 2 if r < 0.5 else (1 if r < 0.75 else 0)
        n = self.closes_high_in_range
        pts += 2 if n >= 2 else (1 if n == 1 else 0)       # acheteurs reprennent la séance
        pts += 2 if self.no_new_low_3d else 0              # condition de base
        pts += 2 if self.vwap_anchored_reconquered else 0  # acheteurs post-event en gains
        pts += 1 if self.first_higher_low else 0
        pts += 1 if self.rv_iv_converging else 0           # IV redescend vers RV
        return pts


def stabilization_score(features: StabFeatures) -> int:
    """STAB ∈ [0, 10]. STAB >= 6 = stabilisation confirmée."""
    return features.score()


@dataclass
class EntryPlan:
    """
    Plan d'entrée par tranches (§7.3.3). La tranche 1 est immédiate si la route
    et le socle valident ET si l'archétype n'est pas bloquant. La tranche 2 est
    armée sur STAB>=6, avec une deadline de 8 séances.
    """
    tranche1_ok: bool
    tranche1_size: float
    tranche2_armed: bool
    tranche2_size: float
    tranche2_deadline_sessions: int
    blocked_reason: str

    @property
    def total_immediate(self) -> float:
        return self.tranche1_size if self.tranche1_ok else 0.0


def entry_schedule(route_validated: bool, floor_ok: bool,
                   archetype: Archetype, stab: int) -> EntryPlan:
    blocked = archetype in BLOCKING_ARCHETYPES
    gate_ok = route_validated and floor_ok
    tranche1_ok = gate_ok and not blocked
    reason = ""
    if not gate_ok:
        reason = "route/socle non validés"
    elif blocked:
        reason = f"archétype bloquant {archetype.value} — vendeur encore actif"
    return EntryPlan(
        tranche1_ok=tranche1_ok,
        tranche1_size=0.5 if tranche1_ok else 0.0,
        tranche2_armed=gate_ok and stab >= STAB_CONFIRMED,
        tranche2_size=0.5,
        tranche2_deadline_sessions=8,
        blocked_reason=reason,
    )
