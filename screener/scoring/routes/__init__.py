#!/usr/bin/env python3
"""Les cinq routes (playbooks) de la couche 4 — §7.1."""
from __future__ import annotations

from typing import List

from ...models import Candidate, Route
from . import a_technical, b_overreaction, c_rebuttal, d_binary, e_value
from .base import RouteResult

#: table route -> module évaluateur, dans l'ordre A..E.
ROUTE_EVALUATORS = {
    Route.A: a_technical.evaluate,
    Route.B: b_overreaction.evaluate,
    Route.C: c_rebuttal.evaluate,
    Route.D: d_binary.evaluate,
    Route.E: e_value.evaluate,
}


def evaluate_all(c: Candidate) -> List[RouteResult]:
    """Évalue les cinq routes. Ne filtre pas : renvoie chacune avec son verdict."""
    return [ROUTE_EVALUATORS[r](c) for r in Route]


def validated_routes(c: Candidate) -> List[RouteResult]:
    """Ne renvoie que les routes satisfaites intégralement (OU entre routes)."""
    return [r for r in evaluate_all(c) if r.passed]
