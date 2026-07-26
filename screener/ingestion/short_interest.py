#!/usr/bin/env python3
"""
Ingestion short interest / emprunt — Ortex (couche 0, §4.3, §9 Phase 2).
=======================================================================
Ortex fournit deux confirmateurs de flux à ★★★ pour l'horizon court (§4.3) que
les prix seuls ne donnent pas :
  - SAUT du taux d'emprunt > +200 bps en 3 séances  (attaque short active)
  - UTILISATION du float > 90 %                      (contrainte d'emprunt)
plus les lignes de la section POSITIONNEMENT & SORTIE de la fiche (§7.5) :
short interest %, utilisation, coût d'emprunt, days-to-cover.

Conventions alignées sur `ortex_c4_pull.py` du dépôt (mêmes noms d'env, base,
en-tête, endpoint confirmé /short_interest, champs `shortInterestPcFreeFloat`,
`shortInterestShares`, `daysToCover`). Sans `ORTEX_API_KEY`, tout dégrade en
`None` — jamais d'exception qui bloquerait la chaîne.

Le parsing est isolé en fonctions pures (`signals_from_payloads`) pour être
testé hors ligne sur des payloads synthétiques.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from typing import List, Optional, Tuple

# --- confirmateurs §4.3 : seuils ---
BORROW_JUMP_BPS_THRESHOLD = 200.0   # saut du taux d'emprunt sur 3j
FLOAT_UTIL_THRESHOLD = 0.90         # utilisation du float

_UA = {"User-Agent": "screener-dislocation"}
_CTB_PATHS = ["cost_to_borrow", "ctb", "cost-to-borrow"]


@dataclass
class ShortInterestSignals:
    ticker: str
    short_interest_pct: Optional[float] = None   # fraction du free float
    borrow_fee: Optional[float] = None           # cost-to-borrow (fraction)
    float_utilization: Optional[float] = None    # 0-1
    borrow_jump_bps_3d: Optional[float] = None   # saut CTB sur 3j (bps)
    days_to_cover: Optional[float] = None
    as_of: str = ""


# --------------------------------------------------------------------------- #
# Normalisation (mêmes conventions que ortex_c4_pull.py)                       #
# --------------------------------------------------------------------------- #
def _pct(v) -> Optional[float]:
    """Ramène un pourcentage en fraction ; >1 est supposé exprimé en % (14.2 -> 0.142)."""
    if v is None:
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v / 100.0 if v > 1 else v


def _rows(js) -> List[dict]:
    if isinstance(js, dict) and isinstance(js.get("rows"), list):
        return js["rows"]
    if isinstance(js, list):
        return js
    if isinstance(js, dict):
        return [js]
    return []


def parse_short_interest(si_json) -> Tuple[Optional[float], Optional[float]]:
    """(short_interest_pct, days_to_cover) depuis le payload /short_interest."""
    rows = _rows(si_json)
    if not rows:
        return (None, None)
    last = rows[-1]
    si = _pct(last.get("shortInterestPcFreeFloat"))
    dtc = last.get("daysToCover")
    try:
        dtc = float(dtc) if dtc is not None else None
    except (TypeError, ValueError):
        dtc = None
    return (si, dtc)


def _borrow_of(row: dict) -> Optional[float]:
    for k in ("costToBorrow", "costToBorrowNew", "ctb", "fee", "borrowFee"):
        if row.get(k) is not None:
            return _pct(row[k])
    return None


def parse_borrow(ctb_json) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """
    (borrow_fee, float_utilization, borrow_jump_bps_3d) depuis le payload CTB.
    Le saut est calculé entre la dernière ligne et celle d'il y a 3 séances
    (rows en ordre chronologique). Insuffisance d'historique -> jump None.
    """
    rows = _rows(ctb_json)
    if not rows:
        return (None, None, None)
    last = rows[-1]
    borrow = _borrow_of(last)
    util = _pct(last.get("utilization"))
    jump = None
    if len(rows) >= 4 and borrow is not None:
        prev = _borrow_of(rows[-4])
        if prev is not None:
            jump = (borrow - prev) * 10_000.0   # fraction -> bps
    return (borrow, util, jump)


def signals_from_payloads(ticker: str, si_json, ctb_json,
                          as_of: Optional[date] = None) -> ShortInterestSignals:
    """Assemble les signaux depuis les deux payloads Ortex. Fonction pure (testable)."""
    as_of = as_of or date.today()
    si, dtc = parse_short_interest(si_json)
    borrow, util, jump = parse_borrow(ctb_json)
    return ShortInterestSignals(
        ticker=ticker.upper(), short_interest_pct=si, borrow_fee=borrow,
        float_utilization=util, borrow_jump_bps_3d=jump, days_to_cover=dtc,
        as_of=as_of.isoformat(),
    )


# --------------------------------------------------------------------------- #
# Confirmateurs §4.3                                                           #
# --------------------------------------------------------------------------- #
def flow_confirmers(sig: Optional[ShortInterestSignals]) -> Tuple[int, List[str]]:
    """
    Compte les confirmateurs de flux issus d'Ortex et renvoie leurs libellés
    (pour la section CONFIRMATIONS de la fiche).
    """
    if sig is None:
        return (0, [])
    n, labels = 0, []
    if sig.borrow_jump_bps_3d is not None and sig.borrow_jump_bps_3d >= BORROW_JUMP_BPS_THRESHOLD:
        n += 1
        labels.append(f"saut du taux d'emprunt +{sig.borrow_jump_bps_3d:.0f} bps/3j")
    if sig.float_utilization is not None and sig.float_utilization >= FLOAT_UTIL_THRESHOLD:
        n += 1
        labels.append(f"utilisation du float {sig.float_utilization:.0%}")
    return (n, labels)


# --------------------------------------------------------------------------- #
# Réseau (nécessite requests + ORTEX_API_KEY ; dégrade sans clé)              #
# --------------------------------------------------------------------------- #
def fetch_ortex_signals(ticker: str, api_key: Optional[str] = None,
                        base: Optional[str] = None, as_of: Optional[date] = None,
                        timeout: int = 25, skip_ctb: bool = False) -> ShortInterestSignals:
    """
    Récupère les signaux Ortex pour un ticker. Sans clé (ou sans `requests`),
    renvoie des signaux vides — la chaîne continue, DIS retombe sur les
    confirmateurs de prix.
    """
    as_of = as_of or date.today()
    api_key = api_key or os.getenv("ORTEX_API_KEY", "")
    base = base or os.getenv("ORTEX_BASE", "https://api.ortex.com/api/v1/stock/us")
    if not api_key:
        return ShortInterestSignals(ticker=ticker.upper(), as_of=as_of.isoformat())
    try:
        import requests
    except ImportError:
        return ShortInterestSignals(ticker=ticker.upper(), as_of=as_of.isoformat())

    headers = {"Ortex-Api-Key": api_key, **_UA}
    si_json = ctb_json = None
    try:
        r = requests.get(f"{base}/{ticker}/short_interest", headers=headers, timeout=timeout)
        if r.status_code == 200:
            si_json = r.json()
    except Exception:  # noqa: BLE001 — dégradation gracieuse
        si_json = None
    if not skip_ctb:
        for path in _CTB_PATHS:
            try:
                r = requests.get(f"{base}/{ticker}/{path}", headers=headers, timeout=timeout)
                if r.status_code == 200:
                    ctb_json = r.json()
                    break
            except Exception:  # noqa: BLE001
                continue
    return signals_from_payloads(ticker, si_json, ctb_json, as_of=as_of)
