#!/usr/bin/env python3
"""
Conviction, taille, priorité — §7.2.
====================================
Le score CLASSE et DIMENSIONNE. Il ne décide pas : le socle et les routes
décident (§7.1).

Points d'implémentation critiques, dans l'ordre où ils faussent tout si ratés :

1. Les composantes de CONV_base dont la route ne se sert pas sont neutralisées
   à 0 ET exclues du dénominateur de renormalisation. Sinon une route B (sans
   valorisation ni catalyseur) serait structurellement pénalisée face à une
   route E. Chaque route normalise sur ses SEULES composantes actives.
2. Le multiplicateur de routes s'applique à CONV_base ET à la taille.
3. La route C plafonne la taille à 0.5x — plafond ABSOLU (cap), pas facteur.

Normalisation : `norm_*` mappe chaque signal brut vers [0,1] par des bornes
documentées. Ce sont des points de départ ; la spec (§7.2) prévoit à terme un
percentile historique INTRA-ROUTE. Le remplacement de ces bornes par ce
percentile est le seul changement à faire ici une fois les données réunies —
la logique de renormalisation par composantes actives, elle, ne change pas.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from ..models import Candidate, CatalystPower, DateCertainty, Route

# Poids de CONV_base (§7.2).
WEIGHTS = {
    "dis": 0.30,
    "catalyst": 0.25,
    "aqs": 0.20,
    "coussin": 0.15,
    "val": 0.10,
}

# Multiplicateur de routes (§7.2), plafonné à 3.
ROUTE_MULTIPLIER = {1: 1.00, 2: 1.35, 3: 1.60}

# Plafond de taille absolu forcé par certaines routes (§7.1 C4).
ROUTE_SIZE_CAP = {Route.C: 0.5}

_POWER = {CatalystPower.LOW: 0.25, CatalystPower.MEDIUM: 0.50,
          CatalystPower.HIGH: 0.75, CatalystPower.VERY_HIGH: 1.00}
_CERTAINTY = {DateCertainty.APPROXIMATE: 0.50, DateCertainty.SEMI_CERTAIN: 0.75,
              DateCertainty.CERTAIN: 1.00}


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def norm_dis(dis: float) -> float:
    return _clamp01(dis / 5.0)


def norm_catalyst(c: Candidate) -> float:
    if c.catalyst is None:
        return 0.0
    return _clamp01(_POWER[c.catalyst.power] * _CERTAINTY[c.catalyst.date_certainty])


def norm_aqs(aqs) -> float:
    if aqs is None:
        return 0.0
    return _clamp01((aqs + 3.0) / 6.0)   # AQS z-score ~ [-3, 3]


def norm_coussin(coussin) -> float:
    if coussin is None:
        return 0.0
    return _clamp01(coussin / 0.5)       # 50 % de coussin -> saturation


def norm_val(val_z) -> float:
    if val_z is None:
        return 0.0
    return _clamp01((val_z + 2.0) / 6.0)  # VAL_z ~ [-2, 4], plus haut = plus décoté


def component_scores(c: Candidate) -> Dict[str, float]:
    """Les cinq composantes normalisées de CONV_base pour ce candidat."""
    return {
        "dis": norm_dis(c.dis),
        "catalyst": norm_catalyst(c),
        "aqs": norm_aqs(c.aqs),
        "coussin": norm_coussin(c.coussin),
        "val": norm_val(c.val_z),
    }


def _available(c: Candidate) -> Dict[str, bool]:
    """
    Une composante est disponible si sa donnée sous-jacente existe pour ce
    candidat. `dis` est toujours disponible (défaut 0.0) ; les autres non.
    Une composante déclarée active mais ABSENTE est exclue du dénominateur —
    sinon une route serait pénalisée pour une donnée qu'elle n'exige pas.
    """
    return {
        "dis": True,
        "catalyst": c.catalyst is not None,
        "aqs": c.aqs is not None,
        "coussin": c.coussin is not None,
        "val": c.val_z is not None,
    }


def conv_base(c: Candidate, active_components: Tuple[str, ...]) -> float:
    """
    CONV_base renormalisé sur les seules composantes actives ET disponibles.

        CONV_base = Σ w_k·norm_k(actives∩dispo) / Σ w_k(actives∩dispo)

    Les composantes non utilisées par la route, ou absentes pour ce candidat,
    sont neutralisées à 0 et exclues du dénominateur (§7.2).
    """
    scores = component_scores(c)
    avail = _available(c)
    active = [k for k in active_components if k in WEIGHTS and avail[k]]
    denom = sum(WEIGHTS[k] for k in active)
    if denom == 0:
        return 0.0
    return sum(WEIGHTS[k] * scores[k] for k in active) / denom


def route_multiplier(n_routes: int) -> float:
    return ROUTE_MULTIPLIER[min(max(n_routes, 1), 3)]


@dataclass
class SizingResult:
    standard_size: float
    s4_factor: float
    route_multiplier: float
    route_cap: float          # plafond absolu (unités de taille standard), inf si aucun
    regime_factor: float
    final_size: float
    capped: bool              # True si le plafond de route a mordu


def size_position(standard_size: float, s4_factor: float, n_routes: int,
                  validated: List[Route], regime_factor: float) -> SizingResult:
    """
    taille = taille_standard × facteur_S4 × mult_routes × facteur_régime,
    puis plafonnée par le cap absolu de route (0.5x sur route C, §7.1).
    """
    mult = route_multiplier(n_routes)
    raw = standard_size * s4_factor * mult * regime_factor
    cap = min((ROUTE_SIZE_CAP[r] for r in validated if r in ROUTE_SIZE_CAP),
              default=float("inf"))
    cap_value = standard_size * cap if cap != float("inf") else float("inf")
    final = min(raw, cap_value)
    return SizingResult(
        standard_size=standard_size, s4_factor=s4_factor, route_multiplier=mult,
        route_cap=cap_value, regime_factor=regime_factor,
        final_size=final, capped=final < raw,
    )


@dataclass
class ConvictionResult:
    conv_base: float           # sur l'échelle [0,1] des composantes actives
    conv_base_10: float        # ramené sur 0-10 pour la fiche
    multiplier: float
    conv: float                # CONV_base × multiplicateur, sur 0-10
    active_components: Tuple[str, ...]


def compute_conviction(c: Candidate, active_components: Tuple[str, ...],
                       n_routes: int) -> ConvictionResult:
    base = conv_base(c, active_components)
    mult = route_multiplier(n_routes)
    return ConvictionResult(
        conv_base=base,
        conv_base_10=base * 10.0,
        multiplier=mult,
        conv=base * 10.0 * mult,
        active_components=active_components,
    )
