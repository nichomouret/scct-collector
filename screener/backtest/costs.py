#!/usr/bin/env python3
"""
Coûts de transaction — §10.5 (« le premier tueur de performance »).
===================================================================
Spread effectif (pas le mid), impact de marché (√ de la participation à l'ADV),
commissions, taxes sur transaction (FR 0.3 %). Sur une stratégie à 2 mois, la
rotation ~6×/an fait peser les coûts 3 à 4× plus qu'à horizon annuel — d'où
l'exigence de les modéliser honnêtement.

Le coût est exprimé en fraction du notionnel et retranché du rendement brut
(entrée + sortie). Approximation standard à ce niveau de détail.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import sqrt


@dataclass
class Costs:
    half_spread_bps: float = 5.0      # demi-spread effectif, par côté (bps)
    commission_bps: float = 1.0       # commission, par côté (bps)
    impact_coef_bps: float = 10.0     # impact = coef × √(participation), en bps
    participation: float = 0.10       # part de l'ADV consommée (position/ADV)
    tax_bps: float = 0.0              # taxe sur transaction à l'achat (FR : 30 bps)

    def _impact_bps(self) -> float:
        return self.impact_coef_bps * sqrt(max(self.participation, 0.0))

    def per_side_bps(self) -> float:
        return self.half_spread_bps + self.commission_bps + self._impact_bps()

    def roundtrip_fraction(self) -> float:
        """Coût aller-retour en fraction du notionnel (2 côtés + taxe à l'achat)."""
        return (2.0 * self.per_side_bps() + self.tax_bps) / 1e4


# §10.5 : préréglage France (taxe 0,3 %). Ajuster par marché.
COSTS_FR = Costs(tax_bps=30.0)
COSTS_US = Costs(tax_bps=0.0)
