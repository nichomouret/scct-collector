#!/usr/bin/env python3
"""
Route E — Décote + catalyseur (§7.1).
=====================================
Moteur du rendement : convergence accélérée par un événement daté. La route
« classique », la plus exigeante et la plus fiable. Cas d'usage : small/mid EU
et TASE. C'est la seule route qui active toutes les composantes de CONV_base.
"""
from __future__ import annotations

from ...models import Candidate, Route
from .base import RouteResult, all_ok

ACTIVE_COMPONENTS = ("dis", "catalyst", "aqs", "coussin", "val")


def evaluate(c: Candidate) -> RouteResult:
    cat = c.catalyst
    dtc = cat.days_to_catalyst if cat else None
    e1 = ("E1 VAL_z>1.0 et non VALUE_TRAP",
          c.val_z is not None and c.val_z > 1.0 and not c.value_trap,
          f"VAL_z={c.val_z}, value_trap={c.value_trap}")
    e2 = ("E2 catalyseur daté 5-45j",
          dtc is not None and 5 <= dtc <= 45,
          f"dtc={dtc}")
    e3 = ("E3 DIS>=2.0", c.dis >= 2.0, f"DIS={c.dis:.2f}")
    e4 = ("E4 AQS>1.0 ou achat d'initié",
          (c.aqs is not None and c.aqs > 1.0) or c.insider_buy,
          f"AQS={c.aqs}, initié={c.insider_buy}")
    checks = [e1, e2, e3, e4]
    return RouteResult(Route.E, all_ok(checks), checks, ACTIVE_COMPONENTS)
