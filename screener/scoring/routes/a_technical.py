#!/usr/bin/env python3
"""
Route A — Dislocation technique (§7.1).
=======================================
Moteur du rendement : déséquilibre de flux mécanique (rebalancement d'indice,
liquidation de fonds). Catalyseur NON requis (la résolution est mécanique),
valorisation NON requise.
"""
from __future__ import annotations

from ...models import Candidate, CauseClass, Route
from .base import RouteResult, all_ok

ACTIVE_COMPONENTS = ("dis",)

_TECHNICAL_CAUSES = frozenset({
    CauseClass.INDEX_REBALANCE,
    CauseClass.FORCED_SELLING,
    CauseClass.LOCKUP_EXPIRY,
    CauseClass.SECTOR_CONTAGION,
})


def evaluate(c: Candidate) -> RouteResult:
    a1 = ("A1 DIS>=3.0 et choc<=3j",
          c.dis >= 3.0 and c.days_since_shock is not None and c.days_since_shock <= 3,
          f"DIS={c.dis:.2f}, choc il y a {c.days_since_shock}j")
    a2 = ("A2 cause technique",
          c.cause_class in _TECHNICAL_CAUSES,
          f"cause={c.cause_class.value if c.cause_class else None}")
    a3 = ("A3 aucun dépôt négatif 72h", not c.negative_filing_72h,
          "dépôt négatif présent" if c.negative_filing_72h else "aucun")
    a4 = ("A4 z-volume>=3", c.z_volume >= 3.0, f"z-vol={c.z_volume:.2f}")
    checks = [a1, a2, a3, a4]
    return RouteResult(Route.A, all_ok(checks), checks, ACTIVE_COMPONENTS)
