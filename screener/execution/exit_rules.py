#!/usr/bin/env python3
"""
Règles de sortie codées en dur — §7.4 (+ stop structurel §7.3.3).
=================================================================
« Le time-stop à 40 séances doit être dans le moteur, pas dans votre tête »
(§6.4). Il est non négociable et doit s'appliquer À L'IDENTIQUE dans le backtest
et en production (§7.4, §10.7) : un backtest sans time-stop sur une stratégie à
time-stop est un backtest d'une autre stratégie.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

EXIT_RULES = {
    "time_stop_sessions": 40,           # sortie inconditionnelle
    "catalyst_resolved_exit_sessions": 2,
    "trailing_stop_after_target": 0.5,  # verrouille 50 % du gain une fois la cible atteinte
    "max_adverse_excursion": -0.15,     # stop dur
}


class ExitReason(str, Enum):
    HOLD = "HOLD"
    TIME_STOP = "TIME_STOP"
    CATALYST_RESOLVED = "CATALYST_RESOLVED"
    THESIS_INVALIDATED = "THESIS_INVALIDATED"
    MAX_ADVERSE_EXCURSION = "MAX_ADVERSE_EXCURSION"
    STRUCTURAL_STOP = "STRUCTURAL_STOP"
    TRAILING_STOP = "TRAILING_STOP"


@dataclass
class PositionState:
    sessions_held: int
    pnl_pct: float                       # rendement courant de la position
    catalyst_resolved: bool = False
    sessions_since_catalyst: Optional[int] = None
    thesis_invalidated: bool = False     # cf. section invalidation de la fiche
    new_low_below_capitulation: bool = False  # stop structurel (§7.3.3)
    target_reached: bool = False
    peak_pnl_pct: float = 0.0            # plus haut rendement atteint (pour le trailing)


@dataclass
class ExitDecision:
    should_exit: bool
    reason: ExitReason
    detail: str


def evaluate_exit(pos: PositionState) -> ExitDecision:
    """
    Applique les règles dans l'ordre de priorité. Le time-stop et la MAE sont
    inconditionnels ; le stop structurel remplace avantageusement le stop fixe
    à -12 % du mécanisme S4(b) et sera généralement plus serré (§7.3.3).
    """
    if pos.sessions_held >= EXIT_RULES["time_stop_sessions"]:
        return ExitDecision(True, ExitReason.TIME_STOP,
                            f"{pos.sessions_held} séances >= {EXIT_RULES['time_stop_sessions']}")

    if pos.pnl_pct <= EXIT_RULES["max_adverse_excursion"]:
        return ExitDecision(True, ExitReason.MAX_ADVERSE_EXCURSION,
                            f"MAE {pos.pnl_pct:+.1%} <= {EXIT_RULES['max_adverse_excursion']:+.0%}")

    if pos.thesis_invalidated:
        return ExitDecision(True, ExitReason.THESIS_INVALIDATED, "point d'invalidation atteint")

    if pos.new_low_below_capitulation:
        return ExitDecision(True, ExitReason.STRUCTURAL_STOP,
                            "nouveau plus bas sous le point de capitulation")

    if (pos.catalyst_resolved and pos.sessions_since_catalyst is not None
            and pos.sessions_since_catalyst >= EXIT_RULES["catalyst_resolved_exit_sessions"]):
        return ExitDecision(True, ExitReason.CATALYST_RESOLVED,
                            f"catalyseur résolu depuis {pos.sessions_since_catalyst} séances")

    # Trailing stop après cible : verrouille 50 % du gain de pic.
    if pos.target_reached and pos.peak_pnl_pct > 0:
        floor = pos.peak_pnl_pct * EXIT_RULES["trailing_stop_after_target"]
        if pos.pnl_pct <= floor:
            return ExitDecision(True, ExitReason.TRAILING_STOP,
                                f"repli sous {floor:+.1%} (50 % du pic {pos.peak_pnl_pct:+.1%})")

    return ExitDecision(False, ExitReason.HOLD, f"détention {pos.sessions_held} séances")
