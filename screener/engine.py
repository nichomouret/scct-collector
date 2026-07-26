#!/usr/bin/env python3
"""
Moteur de décision de la couche 4 — assemble socle, routes, conviction, taille,
et couche PATH. C'est l'orchestrateur de §7.

Ordre imposé par l'architecture :
    1. socle S1-S5 (§7.1)          -> admissibilité brute + mécanisme S4
    2. routes A..E (§7.1)          -> au moins une satisfaite (OU)
    3. conviction (§7.2)           -> sur l'union des composantes actives
    4. taille (§7.2)               -> facteur_S4 × mult_routes × cap route C × régime
    5. PATH (§7.3, transverse)     -> archétype, STAB, entrée par tranches, stop structurel

PATH ne crée aucune admission (§7.3) : un candidat non admis par le socle+route
n'entre jamais, quel que soit son chemin de prix.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .models import Archetype, Candidate, Route
from .scoring.floor import FloorResult, evaluate_floor
from .scoring.routes import validated_routes
from .scoring.routes.base import RouteResult
from .scoring import conviction as conv
from .detection.path_archetype import PriceBar, classify_archetype
from .detection.stabilization import (
    EntryPlan, StabFeatures, entry_schedule, stabilization_score,
)


@dataclass
class PathResult:
    archetype: Optional[Archetype]
    stab: Optional[int]
    entry_plan: Optional[EntryPlan]


@dataclass
class EvaluationResult:
    candidate: Candidate
    floor: FloorResult
    routes: List[RouteResult]                 # routes VALIDÉES uniquement
    n_routes: int
    admitted: bool
    active_components: Tuple[str, ...]
    conviction: conv.ConvictionResult
    sizing: conv.SizingResult
    path: PathResult

    @property
    def route_labels(self) -> List[Route]:
        return [r.route for r in self.routes]


def _union_active_components(routes: List[RouteResult]) -> Tuple[str, ...]:
    """Union ordonnée (A..E, ordre des poids) des composantes actives validées."""
    order = ("dis", "catalyst", "aqs", "coussin", "val")
    seen = set()
    for r in routes:
        seen.update(r.active_components)
    return tuple(k for k in order if k in seen)


def evaluate_candidate(
    c: Candidate,
    standard_size: float = 1.0,
    bars: Optional[List[PriceBar]] = None,
    shock_idx: Optional[int] = None,
    stab_features: Optional[StabFeatures] = None,
) -> EvaluationResult:
    floor = evaluate_floor(c)
    routes = validated_routes(c)
    n = len(routes)
    # Admis seulement si le socle passe ET au moins une route est satisfaite.
    admitted = floor.passed and n >= 1

    active = _union_active_components(routes)
    conviction = conv.compute_conviction(c, active, max(n, 1))
    sizing = conv.size_position(
        standard_size=standard_size,
        s4_factor=floor.size_factor,
        n_routes=max(n, 1),
        validated=[r.route for r in routes],
        regime_factor=c.regime_factor,
    )

    # --- Couche PATH (module le timing/la taille, ne crée pas d'admission) --- #
    archetype = stab = plan = None
    if bars is not None and shock_idx is not None:
        archetype = classify_archetype(bars, shock_idx)
    if stab_features is not None:
        stab = stabilization_score(stab_features)
    if archetype is not None:
        plan = entry_schedule(
            route_validated=(n >= 1), floor_ok=floor.passed,
            archetype=archetype, stab=stab if stab is not None else 0,
        )
    path = PathResult(archetype=archetype, stab=stab, entry_plan=plan)

    return EvaluationResult(
        candidate=c, floor=floor, routes=routes, n_routes=n,
        admitted=admitted, active_components=active,
        conviction=conviction, sizing=sizing, path=path,
    )
