#!/usr/bin/env python3
"""
Route B — Sur-réaction à une news (§7.1).
=========================================
Moteur du rendement : écart entre impact réel et capitalisation effacée.
Cas d'école : IBM -25 %. C'est la route qui admet un titre NON décoté —
catalyseur NON requis, valorisation NON requise.
"""
from __future__ import annotations

from ...models import Candidate, Permanence, Route
from .base import RouteResult, all_ok

# B s'appuie sur l'ampleur de la sur-réaction (DIS). Valorisation NON requise :
# un coussin éventuel joue via le facteur S4 (sizing), pas via la conviction.
ACTIVE_COMPONENTS = ("dis",)


def evaluate(c: Candidate) -> RouteResult:
    ratio = c.surreaction_ratio
    b1 = ("B1 ratio sur-réaction>=3.0",
          ratio is not None and ratio >= 3.0,
          f"ratio={ratio:.2f}" if ratio is not None else "capi/impact indéterminé")
    b2 = ("B2 choc<=5j",
          c.days_since_shock is not None and c.days_since_shock <= 5,
          f"choc il y a {c.days_since_shock}j")
    b3 = ("B3 non permanent et pas de rupture",
          c.permanence is not Permanence.PERMANENT and not c.structural_break,
          f"permanence={c.permanence.value}, rupture={c.structural_break}")
    b4 = ("B4 bilan sain",
          (c.net_debt_ebitda is not None and c.net_debt_ebitda < 3.5) or c.net_cash_positive,
          f"net_debt/EBITDA={c.net_debt_ebitda}, trésorerie nette={c.net_cash_positive}")
    b5 = ("B5 >=2 précédents récupérés >=50 %",
          c.recovery_precedents >= 2,
          f"{c.recovery_precedents} précédent(s)")
    checks = [b1, b2, b3, b4, b5]
    return RouteResult(Route.B, all_ok(checks), checks, ACTIVE_COMPONENTS)
