#!/usr/bin/env python3
"""
Score de réfutation REBUT (0-10) — §5.3.
========================================
Déclenché sur tout SHORT_REPORT, RUMOR_UNCONFIRMED ou REGULATORY contesté.
Cas d'école : 2CRSi / Grizzly Research.

Point clé de la v1.2 : la vitesse de résolution n'est pas une propriété de la
classe d'événement, c'est une propriété de la qualité de la RÉPONSE. C'est la
réfutation qu'on score, pas l'attaque. Et la date de rendu de l'audit / de la
réponse détaillée DEVIENT le catalyseur daté du dossier (route C).
"""
from __future__ import annotations

from dataclasses import dataclass


def _cap(value: int, hi: int) -> int:
    """Borne un sous-score entre 0 et son plafond (garde-fou de saisie)."""
    return max(0, min(int(value), hi))


@dataclass
class RebuttalInputs:
    """Les six critères du barème (§5.3). Chacun est plafonné à sa valeur max."""
    point_by_point_response: int = 0   # 0-2 : réponse chiffrée < 5 séances
    independent_audit_mandated: int = 0  # 0-2 : audit MANDATÉ (pas annoncé), cabinet identifiable
    named_counterparty: int = 0        # 0-2 : confirmation par contrepartie nommée
    insider_buys: int = 0              # 0-2 : achats d'initiés post-attaque
    attacker_track_record: int = 0     # 0-1 : antécédent de crédibilité de l'attaquant
    no_urgent_financing: int = 0       # 0-1 : pas de dilution / refi d'urgence sous 30j

    def score(self) -> int:
        return (
            _cap(self.point_by_point_response, 2)
            + _cap(self.independent_audit_mandated, 2)
            + _cap(self.named_counterparty, 2)
            + _cap(self.insider_buys, 2)
            + _cap(self.attacker_track_record, 1)
            + _cap(self.no_urgent_financing, 1)
        )


def rebuttal_score(inputs: RebuttalInputs) -> int:
    """REBUT ∈ [0, 10]. Seuil route C : REBUT >= 6."""
    return inputs.score()


REBUT_THRESHOLD = 6
