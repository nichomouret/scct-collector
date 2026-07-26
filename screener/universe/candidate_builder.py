#!/usr/bin/env python3
"""
Assemblage d'un `Candidate` — couche 1 (tranche verticale).
===========================================================
Fusionne trois entrées :

  1. UNIVERS (statique, CSV)   : identité, capi, couverture analystes, région,
                                 taille de position cible.
  2. DÉTECTION (prix, calculée): price, ADV, résidu/DIS, z-volume, choc, archétype.
  3. OVERLAY (qualitatif, CSV) : les champs qui exigent, dans le système complet,
                                 la classification LLM (§5.3), les analystes (§5.1),
                                 les options / marchés prédictifs (§5.2) ou une
                                 saisie humaine (scénarios, réfutation).

C'est fidèle au produit : l'outil PRÉ-INSTRUIT à partir des prix, l'humain (ou,
plus tard, les couches P4-P8) remplit les champs qualitatifs. Sans overlay ni
catalyseur, un candidat n'a pas de `cause_class` ni d'horizon → il échoue le
socle S3, ce qui est le comportement correct : un simple décrochage de prix
n'est pas un dossier.
"""
from __future__ import annotations

from dataclasses import dataclass
from statistics import mean
from typing import Dict, List, Optional

from ..models import (
    Candidate, Catalyst, CatalystPower, CauseClass, DateCertainty, Permanence, Region,
)
from ..detection.path_archetype import PriceBar, classify_archetype
from ..detection.residuals import DetectionResult, compute_detection, dislocation_score
from ..detection.stabilization import StabFeatures
from ..ingestion.short_interest import (
    ShortInterestSignals, flow_confirmers, signals_from_payloads,
)
from ..models import Archetype


# --------------------------------------------------------------------------- #
# Parsing tolérant                                                             #
# --------------------------------------------------------------------------- #
def _f(v) -> Optional[float]:
    try:
        return float(v) if v not in (None, "", "NA", "nan") else None
    except (TypeError, ValueError):
        return None


def _i(v) -> Optional[int]:
    f = _f(v)
    return int(f) if f is not None else None


def _b(v) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "oui", "y", "vrai")


def _enum(cls, v, default=None):
    if v in (None, ""):
        return default
    try:
        return cls(str(v).strip().upper())
    except ValueError:
        return default


@dataclass
class BuiltCandidate:
    candidate: Candidate
    bars: List[PriceBar]
    shock_idx: Optional[int]
    stab_features: Optional[StabFeatures]
    detection: DetectionResult
    archetype: Optional[Archetype]


def stab_features_from_bars(bars: List[PriceBar], shock_idx: int) -> StabFeatures:
    """
    Sous-ensemble de STAB (§7.3.2) calculable sur les prix seuls (barres closes).
    La convergence RV/IV exige la vol implicite (options) : absente ici → 0 point.
    """
    window = bars[shock_idx:]
    if len(window) < 2:
        return StabFeatures()
    shock = bars[shock_idx]
    last3 = bars[-3:]

    vol_ratio = (mean(b.volume for b in last3) / shock.volume) if shock.volume else None
    closes_high = sum(1 for b in last3 if b.range_position > 0.6)

    if len(window) > 3:
        min_low_before = min(b.low for b in window[:-3])
    else:
        min_low_before = shock.low
    no_new_low = all(b.low >= min_low_before for b in last3)

    num = sum((b.high + b.low + b.close) / 3.0 * b.volume for b in window)
    den = sum(b.volume for b in window)
    anchored_vwap = num / den if den else None
    vwap_ok = anchored_vwap is not None and bars[-1].close >= anchored_vwap

    lows = [b.low for b in window]
    min_idx = lows.index(min(lows))
    higher_low = any(lows[k] > lows[min_idx] for k in range(min_idx + 1, len(lows)))

    return StabFeatures(
        volume_contraction_ratio=vol_ratio,
        closes_high_in_range=closes_high,
        no_new_low_3d=no_new_low,
        vwap_anchored_reconquered=vwap_ok,
        first_higher_low=higher_low,
        rv_iv_converging=False,
    )


def _catalyst_from_row(row: Optional[dict]) -> Optional[Catalyst]:
    if not row:
        return None
    return Catalyst(
        catalyst_type=row.get("catalyst_type", "CATALYST"),
        date_expected=row.get("date_expected", ""),
        date_certainty=_enum(DateCertainty, row.get("date_certainty"),
                             DateCertainty.APPROXIMATE),
        days_to_catalyst=_i(row.get("days_to_catalyst")),
        binary=_b(row.get("binary")),
        expected_move_pct=_f(row.get("expected_move_pct")),
        power=_enum(CatalystPower, row.get("power"), CatalystPower.MEDIUM),
        source_url=row.get("source_url", ""),
    )


def _signals_from_row(ticker: str, row: Optional[dict]) -> Optional[ShortInterestSignals]:
    """Reconstruit des signaux Ortex depuis une ligne CSV (build_short_interest)."""
    if not row:
        return None
    return ShortInterestSignals(
        ticker=ticker.upper(),
        short_interest_pct=_f(row.get("short_interest_pct")),
        borrow_fee=_f(row.get("borrow_fee")),
        float_utilization=_f(row.get("float_utilization")),
        borrow_jump_bps_3d=_f(row.get("borrow_jump_bps_3d")),
        days_to_cover=_f(row.get("days_to_cover")),
        as_of=row.get("as_of", ""),
    )


def build_candidate(uni: dict, bars: List[PriceBar], market_bars: List[PriceBar],
                    catalyst_row: Optional[dict] = None,
                    overlay: Optional[dict] = None,
                    short_interest: Optional[dict] = None) -> BuiltCandidate:
    ov = overlay or {}
    det = compute_detection(bars, market_bars)
    catalyst = _catalyst_from_row(catalyst_row)
    sig = _signals_from_row(uni.get("ticker", ""), short_interest)

    price = bars[-1].close
    adv_20d = mean(b.close * b.volume for b in bars[-20:]) if len(bars) >= 20 else \
        mean(b.close * b.volume for b in bars)

    # Horizon : overlay prioritaire ; sinon dérivé du catalyseur (il tranche).
    resolution = _i(ov.get("expected_resolution_days"))
    if resolution is None and catalyst is not None:
        resolution = catalyst.days_to_catalyst

    # Archétype / STAB seulement si un choc daté a été localisé.
    archetype = stab_feats = None
    dis = det.dis
    if det.shock_idx is not None:
        archetype = classify_archetype(bars, det.shock_idx)
        stab_feats = stab_features_from_bars(bars, det.shock_idx)
        # Recompute DIS avec la TOTALITÉ des confirmateurs (§4.3) : z-volume
        # (prix) + saut d'emprunt / utilisation du float (Ortex) + achat d'initié.
        price_conf = 1 if det.z_volume >= 3.0 else 0
        ortex_conf, _ = flow_confirmers(sig)
        insider_conf = 1 if _b(ov.get("insider_buy")) else 0
        dis = dislocation_score(det.z_res_at_shock, det.days_since_shock or 0,
                                price_conf + ortex_conf + insider_conf)

    c = Candidate(
        ticker=uni.get("ticker", ""), name=uni.get("name", ""),
        sector=uni.get("sector", ""), place=uni.get("place", ""),
        region=_enum(Region, uni.get("region"), Region.US),
        market_cap=_f(uni.get("market_cap")) or 0.0,
        price=price, adv_20d=adv_20d,
        analyst_coverage=_i(uni.get("analyst_coverage")) or 0,
        listing_age_days=len(bars),
        target_position_value=_f(uni.get("target_position_value")) or 0.0,
        expected_resolution_days=resolution,
        # Détection (prix + confirmateurs)
        dis=dis, z_res=det.z_res_at_shock, z_volume=det.z_volume,
        days_since_shock=det.days_since_shock,
        negative_filing_72h=_b(ov.get("negative_filing_72h")),
        # Short interest / emprunt (Ortex, §4.3)
        short_interest_pct=(sig.short_interest_pct if sig else None),
        borrow_fee=(sig.borrow_fee if sig else None),
        float_utilization=(sig.float_utilization if sig else None),
        borrow_jump_bps_3d=(sig.borrow_jump_bps_3d if sig else None),
        days_to_cover=(sig.days_to_cover if sig else None),
        # Qualification (overlay)
        cause_class=_enum(CauseClass, ov.get("cause_class")),
        permanence=_enum(Permanence, ov.get("permanence"), Permanence.UNKNOWN),
        analysis=(ov.get("analysis") or "").strip(),
        # Tendance sociale (Adanos) — contexte, hors scoring
        social_buzz_z=_f(ov.get("social_buzz_z")),
        social_sentiment=_f(ov.get("social_sentiment")),
        social_mentions=_i(ov.get("social_mentions")),
        social_trend=(ov.get("social_trend") or "").strip(),
        # Socle S4 (overlay)
        fv_low=_f(ov.get("fv_low")), fv_mid=_f(ov.get("fv_mid")), fv_high=_f(ov.get("fv_high")),
        hard_stop_distance=_f(ov.get("hard_stop_distance")),
        hard_stop_level_motivated=_b(ov.get("hard_stop_level_motivated")),
        option_structure_capped=_b(ov.get("option_structure_capped")),
        # Socle S5 (overlay)
        covenant_breach=_b(ov.get("covenant_breach")),
        debt_wall_days=_i(ov.get("debt_wall_days")),
        going_concern=_b(ov.get("going_concern")),
        trading_suspension=_b(ov.get("trading_suspension")),
        # Route B
        capi_effacee=_f(ov.get("capi_effacee")),
        impact_flux_actualise=_f(ov.get("impact_flux_actualise")),
        net_debt_ebitda=_f(ov.get("net_debt_ebitda")),
        net_cash_positive=_b(ov.get("net_cash_positive")),
        structural_break=_b(ov.get("structural_break")),
        recovery_precedents=_i(ov.get("recovery_precedents")) or 0,
        # Route C
        rebut_score=_f(ov.get("rebut_score")),
        response_date_days=_i(ov.get("response_date_days")),
        dilution_or_urgent_refi=_b(ov.get("dilution_or_urgent_refi")),
        # Route D
        catalyst=catalyst,
        pms=_f(ov.get("pms")), manual_prob_gap=_f(ov.get("manual_prob_gap")),
        upside_thesis_pct=_f(ov.get("upside_thesis_pct")),
        pred_market_liquidity=_f(ov.get("pred_market_liquidity")),
        scenario_documented=_b(ov.get("scenario_documented")),
        # Route E / conviction
        val_z=_f(ov.get("val_z")), value_trap=_b(ov.get("value_trap")),
        aqs=_f(ov.get("aqs")), insider_buy=_b(ov.get("insider_buy")),
        # Régime
        regime_factor=_f(ov.get("regime_factor")) or 1.0,
    )
    return BuiltCandidate(c, bars, det.shock_idx, stab_feats, det, archetype)
