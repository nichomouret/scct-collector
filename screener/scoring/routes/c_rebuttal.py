#!/usr/bin/env python3
"""
Route C — Réfutation (§7.1).
============================
Moteur du rendement : levée d'un doute par une réponse crédible.
Cas d'école : 2CRSi / Grizzly. La date de réponse détaillée / de rendu d'audit
EST le catalyseur daté. Queue gauche épaisse : taille plafonnée à 0.5x, non
négociable (appliqué dans le sizing, §7.2).

La dislocation est requise implicitement — l'attaque l'a créée.
"""
from __future__ import annotations

from ...models import Candidate, Route
from ..rebuttal import REBUT_THRESHOLD
from .base import RouteResult, all_ok

ACTIVE_COMPONENTS = ("dis",)


def evaluate(c: Candidate) -> RouteResult:
    c1 = ("C1 REBUT>=6",
          c.rebut_score is not None and c.rebut_score >= REBUT_THRESHOLD,
          f"REBUT={c.rebut_score}")
    c2 = ("C2 date de réponse/audit connue et <=45j",
          c.response_date_days is not None and 0 <= c.response_date_days <= 45,
          f"réponse dans {c.response_date_days}j" if c.response_date_days is not None else "date inconnue")
    c3 = ("C3 pas de dilution/refi d'urgence", not c.dilution_or_urgent_refi,
          "dilution/refi annoncé" if c.dilution_or_urgent_refi else "aucun")
    checks = [c1, c2, c3]
    return RouteResult(Route.C, all_ok(checks), checks, ACTIVE_COMPONENTS)
