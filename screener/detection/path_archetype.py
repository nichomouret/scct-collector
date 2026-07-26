#!/usr/bin/env python3
"""
Archétypes de chemin de prix — §7.3.1.
======================================
Le *profil* de la baisse renseigne sur l'état du flux vendeur, indépendamment
de la cause annoncée. La distinction qui porte l'essentiel de la valeur :

    GAP_AND_FLAT / CAPITULATION_V  (vendeur sorti — entrée rapide)
    GRINDING_DECLINE / STAIRCASE_DOWN / FAILED_BOUNCE  (vendeur actif — bloquant)

DISCIPLINE ANTI-FUITE (§7.3.4, règle 4) : toutes les features sont calculées
sur barres CLOSES uniquement. Le classifieur ne reçoit jamais la barre en cours ;
`bars[:as_of]` est fermé, `bars[as_of:]` n'existe pas encore pour lui. La barre
du choc et toutes les barres postérieures passées au classifieur sont des
barres closes.

Les seuils ci-dessous sont des points de départ à calibrer par le backtest
(§7.3.4). Aucun indicateur classique (RSI, MACD, Bollinger) — règle 2.
"""
from __future__ import annotations

from dataclasses import dataclass
from statistics import mean, pstdev
from typing import List, Optional

from ..models import Archetype


@dataclass
class PriceBar:
    """Barre OHLCV close. `date` sert uniquement à l'audit / au tri."""
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def range_position(self) -> float:
        """Position de la clôture dans le range du jour ∈ [0,1] (1 = clôture au plus haut)."""
        span = self.high - self.low
        if span <= 0:
            return 0.5
        return (self.close - self.low) / span


def _volume_sigma(shock_vol: float, baseline: List[float]) -> Optional[float]:
    """Z-score du volume du choc contre la ligne de base pré-choc."""
    vols = [b for b in baseline if b > 0]
    if len(vols) < 5:
        return None
    m, s = mean(vols), pstdev(vols)
    if s <= 0:
        return None
    return (shock_vol - m) / s


def classify_archetype(bars: List[PriceBar], shock_idx: int) -> Archetype:
    """
    Classe le chemin de prix post-choc. `shock_idx` = index de la barre du choc.
    Seules les barres jusqu'à la dernière close sont fournies (garde anti-fuite).

    Ordre : les archétypes bloquants (flux vendeur actif) sont testés d'abord ;
    en cas d'ambiguïté, on préfère bloquer que sur-entrer.
    """
    if shock_idx < 0 or shock_idx >= len(bars):
        raise ValueError("shock_idx hors bornes")
    shock = bars[shock_idx]
    post = bars[shock_idx + 1:]
    baseline = [b.volume for b in bars[:shock_idx]]

    # --- FAILED_BOUNCE : rebond >8 % puis retour sous le point bas ---------- #
    if post:
        running_min = shock.low
        bounced = False
        for b in post:
            if b.high >= running_min * 1.08:
                bounced = True
            if bounced and b.close < running_min:
                return Archetype.FAILED_BOUNCE
            running_min = min(running_min, b.low)

    # --- STAIRCASE_DOWN : rebonds avortés, plus hauts descendants ----------- #
    if len(post) >= 4:
        swing_highs = []
        for i in range(1, len(post)):
            if post[i].close > post[i - 1].close:   # tentative de rebond
                swing_highs.append(post[i].high)
        lower_highs = sum(1 for i in range(1, len(swing_highs))
                          if swing_highs[i] < swing_highs[i - 1])
        overall_down = post[-1].close < shock.close
        if len(swing_highs) >= 2 and lower_highs >= 1 and overall_down:
            # distingué du grinding par la présence de rebonds intermédiaires
            if any(post[i].close > post[i - 1].close for i in range(1, len(post))):
                return Archetype.STAIRCASE_DOWN

    # --- GRINDING_DECLINE : baisse continue, clôtures basses, volume soutenu - #
    if len(post) >= 5:
        window = post[:5]
        closes_low = sum(1 for b in window if b.range_position < 0.4)
        still_new_lows = window[-1].low <= min(b.low for b in window[:-1])
        vol_sustained = mean(b.volume for b in window[-2:]) >= 0.8 * mean(b.volume for b in window[:2])
        if closes_low >= 3 and still_new_lows and vol_sustained:
            return Archetype.GRINDING_DECLINE

    # --- CAPITULATION_V : pic de volume >=5σ, mèche basse, clôture haute ----- #
    vsigma = _volume_sigma(shock.volume, baseline)
    if (vsigma is not None and vsigma >= 5.0
            and shock.range_position >= 0.6
            and (not post or all(b.low >= shock.low for b in post[:3]))):
        return Archetype.CAPITULATION_V

    # --- GAP_AND_FLAT : gap unique puis 3+ séances de range étroit ---------- #
    if len(post) >= 3:
        first3 = post[:3]
        shock_span = max(shock.high - shock.low, 1e-9)
        narrow = all((b.high - b.low) <= 0.6 * shock_span for b in first3)
        no_new_low = all(b.low >= shock.low for b in first3)
        vol_decaying = first3[-1].volume <= first3[0].volume
        if narrow and no_new_low and vol_decaying:
            return Archetype.GAP_AND_FLAT

    # --- Défaut : chemin non stabilisé -> traité comme bloquant (prudence) --- #
    return Archetype.GRINDING_DECLINE
