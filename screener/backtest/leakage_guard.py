#!/usr/bin/env python3
"""
Garde anti-fuite — §10.3 / §7.3.4 règle 4.
==========================================
« Une feature de chemin calculée sur la barre en cours est une fuite. » Le
risque réel ne vit pas dans les fonctions de feature (elles ne reçoivent qu'une
tranche `bars[:t+1]` et ne peuvent pas voir au-delà) mais dans le MOTEUR, s'il
passait par mégarde des barres postérieures à t.

L'invariant testé : les signaux d'entrée calculés jusqu'à une date t doivent
être IDENTIQUES qu'on ajoute ou non des barres futures. Si un signal passé
change quand on colle des barres après lui, il y a fuite.
"""
from __future__ import annotations

from typing import Callable, List

from ..detection.path_archetype import PriceBar


def _wild_future(bars: List[PriceBar], pad: int) -> List[PriceBar]:
    """Barres futures « sauvages » (forte hausse) pour tester l'invariance."""
    last = bars[-1]
    out = []
    px = last.close
    for i in range(pad):
        px *= 1.10
        out.append(PriceBar(f"{last.date}+{i+1}", px, px * 1.05, px * 0.98, px, last.volume * 3))
    return out


def signals_unaffected_by_future(entry_fn: Callable, bars: List[PriceBar],
                                 market_bars: List[PriceBar], pad: int = 6) -> bool:
    """
    `entry_fn(bars, market_bars)` renvoie une liste de signaux (objets à attribut
    `signal_idx`). Vrai si coller `pad` barres futures ne modifie AUCUN des
    signaux d'origine (seuls de nouveaux signaux peuvent apparaître à la fin).
    """
    base = entry_fn(bars, market_bars)
    extended = entry_fn(bars + _wild_future(bars, pad),
                        market_bars + _wild_future(market_bars, pad))
    if len(extended) < len(base):
        return False
    for a, b in zip(base, extended):
        if (a.signal_idx, a.shock_idx) != (b.signal_idx, b.shock_idx):
            return False
    return True
