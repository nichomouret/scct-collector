#!/usr/bin/env python3
"""
Rendement résiduel & score de dislocation — couche 2 (§4), version tranche verticale.
=====================================================================================
SIMPLIFICATION ASSUMÉE : la SPEC §4.1 neutralise quatre facteurs
(marché, secteur, taille, valeur). Faute de séries factorielles gratuites, cette
version ne neutralise que le **marché** (bêta unique sur un indice de référence).
C'est suffisant pour distinguer le résidu du bêta — « une baisse de -8 % un jour
où le secteur perd 7 % n'est pas une anomalie » — mais le modèle 4 facteurs reste
la cible. La signature de sortie ne changera pas quand on l'enrichira.

Sorties : z_res (signé), DIS (§4.4), z_volume, jours depuis le choc, et l'index
de la barre du choc (pour la classification d'archétype).
"""
from __future__ import annotations

from dataclasses import dataclass
from math import exp
from statistics import pstdev, mean
from typing import Dict, List, Optional

from .path_archetype import PriceBar

# §4.1 déclencheur primaire / §4.2 fenêtre de fraîcheur.
Z_TRIGGER_1D = -2.5
Z_TRIGGER_CUM3 = -3.0
FRESH_MAX_SESSIONS = 5     # rejet si le choc date de plus de 5 séances (§4.2)
LOOKBACK_SHOCK = 15        # on cherche le choc le plus récent dans cette fenêtre
BETA_WINDOW = 250
VOL_WINDOW = 60


@dataclass
class DetectionResult:
    z_res_latest: float          # résidu z de la dernière séance (signé)
    z_res_at_shock: float        # résidu z à la séance du choc (signé, négatif)
    dis: float                   # score de dislocation DIS (§4.4)
    z_volume: float              # z-score de volume de la dernière séance
    days_since_shock: Optional[int]
    shock_idx: Optional[int]     # index de la barre du choc dans `bars`
    fresh: bool                  # choc <= 5 séances


def _returns_by_date(bars: List[PriceBar]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for i in range(1, len(bars)):
        prev = bars[i - 1].close
        if prev:
            out[bars[i].date] = bars[i].close / prev - 1.0
    return out


def returns_by_date(bars: List[PriceBar]) -> Dict[str, float]:
    """Rendements simples par date — public, pour pré-calcul (backtest §10)."""
    return _returns_by_date(bars)


def _ols(xs: List[float], ys: List[float]):
    """Régression OLS simple y = alpha + beta·x. Renvoie (alpha, beta)."""
    n = len(xs)
    mx, my = mean(xs), mean(ys)
    var = sum((x - mx) ** 2 for x in xs)
    if var == 0:
        return (my, 0.0)
    cov = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    beta = cov / var
    return (my - beta * mx, beta)


def dislocation_score(z_at_shock: float, days_since: int, n_confirmers: int) -> float:
    """
    DIS = |z_res| × (1 + 0.15 × Σ confirmateurs) × decay(jours_depuis_choc) (§4.4).

    Extrait pour que la couche d'assemblage recompute DIS avec la totalité des
    confirmateurs (§4.3), pas seulement ceux dérivables des prix : le z-volume
    vient des prix, mais le saut du taux d'emprunt et l'utilisation du float
    viennent d'Ortex, et l'achat d'initié d'un dépôt réglementaire.
    """
    return abs(z_at_shock) * (1 + 0.15 * n_confirmers) * exp(-days_since / 3.0)


def _z_volume(bars: List[PriceBar]) -> float:
    if len(bars) < 21:
        return 0.0
    prior = [b.volume for b in bars[-21:-1]]
    m, s = mean(prior), pstdev(prior)
    if s == 0:
        return 0.0
    return (bars[-1].volume - m) / s


def compute_detection(bars: List[PriceBar], market_bars: List[PriceBar],
                      market_returns: Optional[Dict[str, float]] = None) -> DetectionResult:
    """
    Calcule le résidu marché-neutre et le score de dislocation.

    Point-in-time léger : (alpha, beta) sont estimés sur les 250 derniers points
    ALIGNÉS disponibles, et les résidus/σ sur cette même fenêtre — pas de donnée
    postérieure à la dernière barre.

    `market_returns` (optionnel) : rendements marché pré-calculés, pour éviter de
    les recalculer à chaque appel dans la boucle jour par jour du backtest (§10).
    L'alignement reste borné par les dates du titre → aucune fuite.
    """
    stock_r = _returns_by_date(bars)
    mkt_r = market_returns if market_returns is not None else _returns_by_date(market_bars)
    common = [d for d in sorted(stock_r) if d in mkt_r]
    if len(common) < 80:
        # historique insuffisant : pas de détection exploitable.
        return DetectionResult(0.0, 0.0, 0.0, _z_volume(bars), None, None, False)

    window = common[-BETA_WINDOW:]
    xs = [mkt_r[d] for d in window]
    ys = [stock_r[d] for d in window]
    alpha, beta = _ols(xs, ys)
    resid = {d: stock_r[d] - (alpha + beta * mkt_r[d]) for d in window}

    tail = window[-VOL_WINDOW:]
    sigma = pstdev([resid[d] for d in tail]) or 1e-9
    zres = {d: resid[d] / sigma for d in window}

    dates = window
    latest = dates[-1]
    z_latest = zres[latest]

    # Choc = jour le PLUS négatif dans la fenêtre de recherche, validé par le
    # déclencheur (§4.1). Ancrer sur le min évite que le trigger cumulé-3 jours
    # ne se déclenche sur les séances POSTÉRIEURES au choc (dont la fenêtre
    # glissante contient encore le choc), ce qui décalerait `days_since_shock`.
    recent = dates[-LOOKBACK_SHOCK:] if len(dates) > LOOKBACK_SHOCK else dates
    cand = min(recent, key=lambda d: zres[d])
    idx = dates.index(cand)
    cum3 = zres[cand] + (zres[dates[idx - 1]] if idx >= 1 else 0.0) \
                      + (zres[dates[idx - 2]] if idx >= 2 else 0.0)
    triggered = zres[cand] <= Z_TRIGGER_1D or cum3 <= Z_TRIGGER_CUM3
    shock_date = cand if triggered else None
    days_since = (len(dates) - 1) - idx

    z_vol = _z_volume(bars)
    if shock_date is None:
        return DetectionResult(z_latest, 0.0, 0.0, z_vol, None, None, False)

    z_at_shock = zres[shock_date]
    fresh = days_since <= FRESH_MAX_SESSIONS
    # Confirmateurs de flux disponibles depuis les prix seuls : z-volume (§4.3).
    # La couche d'assemblage recompute DIS avec les confirmateurs Ortex/initiés.
    confirmers = 1 if z_vol >= 3.0 else 0
    dis = dislocation_score(z_at_shock, days_since, confirmers)

    # Index de la barre du choc dans `bars`.
    shock_idx = next((i for i, b in enumerate(bars) if b.date == shock_date), None)

    return DetectionResult(
        z_res_latest=z_latest, z_res_at_shock=z_at_shock, dis=dis,
        z_volume=z_vol, days_since_shock=days_since, shock_idx=shock_idx, fresh=fresh,
    )
