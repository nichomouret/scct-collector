#!/usr/bin/env python3
"""
Route D — Binaire daté (§7.1).
==============================
Moteur du rendement : différentiel de probabilité. Cas d'école : Abivax,
Inventiva. Dislocation NON requise (c'est la route qui admet un titre sans
décrochage préalable), valorisation NON requise. Véhicule optionnel privilégié
si la chaîne d'options est liquide.
"""
from __future__ import annotations

from ...models import Candidate, Route
from .base import RouteResult, all_ok

# D est piloté par le catalyseur (différentiel de probabilité). L'appui
# analystes (aqs), s'il existe, remonte via l'union des routes et le sizing.
ACTIVE_COMPONENTS = ("catalyst",)


def evaluate(c: Candidate) -> RouteResult:
    cat = c.catalyst
    dtc = cat.days_to_catalyst if cat else None
    d1 = ("D1 catalyseur binaire daté, 5-45j",
          bool(cat) and cat.binary and dtc is not None and 5 <= dtc <= 45,
          f"binaire={cat.binary if cat else None}, dtc={dtc}")

    pms_ok = c.pms is not None and abs(c.pms) >= 0.20
    manual_ok = c.manual_prob_gap is not None and abs(c.manual_prob_gap) >= 0.20
    d2 = ("D2 |PMS|>=0.20 ou écart scénarisé>=0.20", pms_ok or manual_ok,
          f"PMS={c.pms}, écart manuel={c.manual_prob_gap}")

    em = cat.expected_move_pct if cat else None
    d3 = ("D3 upside thèse > 1.5x mouvement implicite",
          c.upside_thesis_pct is not None and em is not None and em > 0
          and c.upside_thesis_pct > 1.5 * em,
          f"upside={c.upside_thesis_pct}, move implicite={em}")

    d4 = ("D4 marché prédictif>50k$ ou scénarisation documentée",
          (c.pred_market_liquidity is not None and c.pred_market_liquidity > 50_000)
          or c.scenario_documented,
          f"OI={c.pred_market_liquidity}, documenté={c.scenario_documented}")
    checks = [d1, d2, d3, d4]
    return RouteResult(Route.D, all_ok(checks), checks, ACTIVE_COMPONENTS)
