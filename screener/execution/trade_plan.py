#!/usr/bin/env python3
"""
Plan de trade — niveaux d'entrée, de stop et de prise de profit (§7.3.3, §7.4).
==============================================================================
Traduit un `Candidate` en niveaux de prix concrets, DÉRIVÉS des mêmes règles que
`execution/exit_rules.py` (aucune constante nouvelle) :

- Stop : le plus SERRÉ (déclenché en premier) entre le mécanisme de bornage S4
  (stop dur motivé, §7.1 b) et la MAE inconditionnelle de −15 % (§7.4). Un stop
  structurel « sous le plus-bas de capitulation » (§7.3.3) reste plus fin mais
  exige les barres ; on l'ajoute si le plus-bas du choc est fourni.
- Cibles : la bande de juste valeur (fv_low → fv_mid) quand elle existe, sinon
  l'upside de la thèse, sinon le mouvement implicite du catalyseur (straddle).
  Deux paliers : sortie partielle prudente, puis cible de thèse.
- Time-stop : 40 séances, inconditionnel (importé, pas redéfini).

Sans cours ni cible chiffrée, le champ correspondant reste `None` — on n'invente
pas de niveau. Ce module ne décide pas d'acheter ; il chiffre le cadre de risque
d'un dossier déjà admis par le socle et les routes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..models import Candidate
from .exit_rules import EXIT_RULES

_MAE = EXIT_RULES["max_adverse_excursion"]          # −0.15
_TIME_STOP = EXIT_RULES["time_stop_sessions"]       # 40


@dataclass
class TradeLevels:
    entry: float
    stop: float
    stop_pct: float                 # risque (négatif), (stop-entry)/entry
    stop_basis: str
    structural_stop: Optional[float] = None   # sous le plus-bas du choc (§7.3.3)
    target1: Optional[float] = None
    target1_pct: Optional[float] = None
    target2: Optional[float] = None
    target2_pct: Optional[float] = None
    target_basis: str = ""
    rr: Optional[float] = None       # ratio gain(cible thèse)/risque
    time_stop_sessions: int = _TIME_STOP


def plan_levels(c: Candidate, shock_low: Optional[float] = None) -> Optional[TradeLevels]:
    """Niveaux de trade pour ce candidat, à partir du cours d'entrée courant."""
    entry = c.price
    if not entry or entry <= 0:
        return None

    # --- Stop : le plus serré (prix le plus haut) parmi les stops applicables --- #
    mae_stop = entry * (1.0 + _MAE)
    cands = [(mae_stop, f"MAE {_MAE:+.0%} (inconditionnel)")]
    if c.hard_stop_distance is not None:
        cands.append((entry * (1.0 - c.hard_stop_distance),
                      f"stop dur S4(b) −{c.hard_stop_distance:.0%}"))
    stop, stop_basis = max(cands, key=lambda t: t[0])   # max prix = plus serré
    stop = max(stop, mae_stop)                          # la MAE reste un plancher absolu
    stop_pct = (stop - entry) / entry

    structural = shock_low if (shock_low is not None and 0 < shock_low < entry) else None

    # --- Cibles de prise de profit --- #
    t1 = t2 = None
    basis = ""
    if c.fv_low is not None and c.fv_mid is not None and c.fv_mid > entry:
        # Bande de juste valeur : palier prudent (fv_low) puis cible (fv_mid).
        t1 = c.fv_low if c.fv_low > entry else None
        t2 = c.fv_mid
        basis = "bande de juste valeur (fv_low → fv_mid)"
    elif c.upside_thesis_pct:
        t2 = entry * (1.0 + c.upside_thesis_pct / 100.0)
        t1 = entry + 0.5 * (t2 - entry)
        basis = f"upside de la thèse +{c.upside_thesis_pct:.0f}%"
    elif c.catalyst is not None and c.catalyst.expected_move_pct:
        t2 = entry * (1.0 + c.catalyst.expected_move_pct / 100.0)
        t1 = entry + 0.5 * (t2 - entry)
        basis = f"mouvement implicite du catalyseur ±{c.catalyst.expected_move_pct:.0f}%"

    t1_pct = (t1 - entry) / entry if t1 else None
    t2_pct = (t2 - entry) / entry if t2 else None
    risk = entry - stop
    rr = ((t2 - entry) / risk) if (t2 and risk > 0) else None

    return TradeLevels(
        entry=entry, stop=stop, stop_pct=stop_pct, stop_basis=stop_basis,
        structural_stop=structural, target1=t1, target1_pct=t1_pct,
        target2=t2, target2_pct=t2_pct, target_basis=basis, rr=rr,
    )
