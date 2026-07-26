#!/usr/bin/env python3
"""
Screener de dislocation multi-signaux — modèles de données partagés
====================================================================
Dataclasses et énumérations lues par tout le moteur de décision (couche 4 de
la SPEC v1.3). Aucune dépendance externe : le moteur de décision est
déterministe et testable sans le moindre flux payant.

Ce module ne calcule rien ; il définit le vocabulaire. Le calcul vit dans
`scoring/`, `detection/` et `execution/`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# --------------------------------------------------------------------------- #
# Énumérations                                                                 #
# --------------------------------------------------------------------------- #
class Region(str, Enum):
    US = "US"
    EU = "EU"
    IL = "IL"


class DateCertainty(str, Enum):
    CERTAIN = "CERTAIN"
    SEMI_CERTAIN = "SEMI_CERTAIN"
    APPROXIMATE = "APPROXIMATE"


class CatalystPower(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    VERY_HIGH = "VERY_HIGH"


class CauseClass(str, Enum):
    FUNDAMENTAL_NEGATIVE = "FUNDAMENTAL_NEGATIVE"
    GUIDANCE_CUT = "GUIDANCE_CUT"
    REGULATORY = "REGULATORY"
    DILUTION = "DILUTION"
    SHORT_REPORT = "SHORT_REPORT"
    RUMOR_UNCONFIRMED = "RUMOR_UNCONFIRMED"
    SECTOR_CONTAGION = "SECTOR_CONTAGION"
    INDEX_REBALANCE = "INDEX_REBALANCE"
    LOCKUP_EXPIRY = "LOCKUP_EXPIRY"
    FORCED_SELLING = "FORCED_SELLING"
    NO_IDENTIFIED_CAUSE = "NO_IDENTIFIED_CAUSE"


class Permanence(str, Enum):
    PERMANENT = "PERMANENT"
    TRANSITORY = "TRANSITORY"
    UNKNOWN = "UNKNOWN"


class Archetype(str, Enum):
    """Archétypes de chemin de prix (§7.3.1). Les trois derniers sont bloquants."""
    GAP_AND_FLAT = "GAP_AND_FLAT"
    CAPITULATION_V = "CAPITULATION_V"
    GRINDING_DECLINE = "GRINDING_DECLINE"
    STAIRCASE_DOWN = "STAIRCASE_DOWN"
    FAILED_BOUNCE = "FAILED_BOUNCE"


#: Archétypes qui interdisent l'entrée en tranche 1 (§7.3.3) — vendeur actif.
BLOCKING_ARCHETYPES = frozenset(
    {Archetype.GRINDING_DECLINE, Archetype.STAIRCASE_DOWN, Archetype.FAILED_BOUNCE}
)


class Route(str, Enum):
    A = "A"  # Dislocation technique
    B = "B"  # Sur-réaction à une news
    C = "C"  # Réfutation
    D = "D"  # Binaire daté
    E = "E"  # Décote + catalyseur


class LossBoundMechanism(str, Enum):
    """Mécanismes de bornage de perte du socle S4, avec leur facteur de taille."""
    VALUATION_CUSHION = "VALUATION_CUSHION"    # (a) coussin > 15 %      -> 1.0x
    HARD_STOP = "HARD_STOP"                    # (b) stop dur <= 12 %    -> 0.7x
    OPTION_STRUCTURE = "OPTION_STRUCTURE"      # (c) perte plafonnée     -> 1.0x
    REDUCED_SIZE = "REDUCED_SIZE"              # (d) taille réduite seule -> 0.5x
    NONE = "NONE"                              # aucun mécanisme -> S4 échoue


#: Facteur de taille associé à chaque mécanisme S4 (§7.1, §7.2).
S4_SIZE_FACTOR = {
    LossBoundMechanism.VALUATION_CUSHION: 1.0,
    LossBoundMechanism.HARD_STOP: 0.7,
    LossBoundMechanism.OPTION_STRUCTURE: 1.0,
    LossBoundMechanism.REDUCED_SIZE: 0.5,
    LossBoundMechanism.NONE: 0.0,
}


# --------------------------------------------------------------------------- #
# Structures                                                                   #
# --------------------------------------------------------------------------- #
@dataclass
class Catalyst:
    """Registre des catalyseurs (§3.2). `days_to_catalyst` est le pivot horizon."""
    catalyst_type: str
    date_expected: str                      # ISO 'YYYY-MM-DD'
    date_certainty: DateCertainty = DateCertainty.APPROXIMATE
    days_to_catalyst: Optional[int] = None
    binary: bool = False
    expected_move_pct: Optional[float] = None   # dérivé du straddle si options liquides
    power: CatalystPower = CatalystPower.MEDIUM
    source_url: str = ""


@dataclass
class Candidate:
    """
    Un candidat prêt pour la couche 4 (portes + scoring + sortie).

    Les champs sont regroupés par usage. Tous ont des valeurs par défaut afin
    qu'un test puisse ne renseigner que ce dont une route donnée a besoin — une
    route neutralise les composantes qu'elle n'utilise pas (§7.2).
    """
    # --- Identité / marché ---
    ticker: str = ""
    name: str = ""
    sector: str = ""
    place: str = ""
    region: Region = Region.US
    market_cap: float = 0.0
    price: float = 0.0

    # --- Socle S1 : éligibilité univers (§3.1) ---
    adv_20d: float = 0.0                 # volume moyen 20j en devise
    analyst_coverage: int = 0
    listing_age_days: int = 10_000
    sector_flags: tuple = ()             # ex. ("SPAC",) -> exclu
    exclude_flags: tuple = ()            # ex. ("going_concern",)

    # --- Socle S2 : liquidité de sortie ---
    target_position_value: float = 0.0   # taille cible de position en devise

    # --- Socle S3 : horizon ---
    expected_resolution_days: Optional[int] = None

    # --- Socle S4 : bornage de perte ---
    fv_low: Optional[float] = None
    fv_mid: Optional[float] = None
    fv_high: Optional[float] = None
    hard_stop_distance: Optional[float] = None   # fraction, ex. 0.10 = -10 %
    hard_stop_level_motivated: bool = False
    option_structure_capped: bool = False
    allow_reduced_size_fallback: bool = True     # (d) toujours disponible sauf refus

    # --- Socle S5 : risque existentiel dans la fenêtre ---
    covenant_breach: bool = False
    debt_wall_days: Optional[int] = None         # jours avant mur de dette
    going_concern: bool = False
    trading_suspension: bool = False

    # --- Détection (§4) ---
    dis: float = 0.0                     # score de dislocation DIS (§4.4)
    z_res: float = 0.0                   # rendement résiduel signé (§4.1)
    z_volume: float = 0.0
    days_since_shock: Optional[int] = None
    negative_filing_72h: bool = False

    # --- Short interest / emprunt (Ortex, §4.3) ---
    short_interest_pct: Optional[float] = None   # % du free float (fraction)
    borrow_fee: Optional[float] = None           # cost-to-borrow (fraction)
    float_utilization: Optional[float] = None    # 0-1
    borrow_jump_bps_3d: Optional[float] = None   # saut du taux d'emprunt sur 3j (bps)
    days_to_cover: Optional[float] = None

    # --- Qualification news (§5.3) ---
    cause_class: Optional[CauseClass] = None
    permanence: Permanence = Permanence.UNKNOWN

    # --- Route B : sur-réaction ---
    capi_effacee: Optional[float] = None            # capitalisation effacée
    impact_flux_actualise: Optional[float] = None   # impact de flux actualisé (valeur absolue)
    net_debt_ebitda: Optional[float] = None
    net_cash_positive: bool = False
    structural_break: bool = False                  # rupture du modèle -> disqualifie B
    recovery_precedents: int = 0                    # nb de chocs comparables récupérés >=50 %

    # --- Route C : réfutation ---
    rebut_score: Optional[float] = None             # REBUT 0-10 (§5.3)
    response_date_days: Optional[int] = None        # jours avant réponse/audit -> catalyseur
    dilution_or_urgent_refi: bool = False

    # --- Route D : binaire daté ---
    catalyst: Optional[Catalyst] = None
    pms: Optional[float] = None                     # p_market - p_implied (§5.2)
    manual_prob_gap: Optional[float] = None         # |p_estimée - p_implied| scénarisé
    upside_thesis_pct: Optional[float] = None
    pred_market_liquidity: Optional[float] = None   # open interest en $
    scenario_documented: bool = False

    # --- Route E / conviction : valorisation & analystes ---
    val_z: Optional[float] = None                   # VAL_z (§3.3)
    value_trap: bool = False
    aqs: Optional[float] = None                     # score analystes (§5.1)
    insider_buy: bool = False

    # --- Régime (§12) ---
    regime_factor: float = 1.0                      # <1 en stress

    # ---- Propriétés dérivées ---------------------------------------------- #
    @property
    def coussin(self) -> Optional[float]:
        """Coussin de valorisation (fv_low - price) / price (§3.4)."""
        if self.fv_low is None or not self.price:
            return None
        return (self.fv_low - self.price) / self.price

    @property
    def days_of_adv(self) -> Optional[float]:
        """Nombre de jours d'ADV que représente la position cible (S2)."""
        if not self.adv_20d:
            return None
        return self.target_position_value / self.adv_20d

    @property
    def surreaction_ratio(self) -> Optional[float]:
        """capi_effacée / |impact_flux_actualisé| (route B / fiche)."""
        if self.capi_effacee is None or not self.impact_flux_actualise:
            return None
        return self.capi_effacee / abs(self.impact_flux_actualise)
