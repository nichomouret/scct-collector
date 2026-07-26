#!/usr/bin/env python3
"""
Ingestion de fondamentaux d'éligibilité — TwelveData (couche 0, §3.1 / socle S1).
================================================================================
Le socle S1 (§3.1) exige une capitalisation minimale par région. Sur un univers
généré depuis SEC (`build_universe`), la colonne `market_cap` est vide → S1
échoue et `run` n'admet rien. Ce module comble ce trou : il récupère la
capitalisation (et le cours, le nombre d'actions, la bande 52 semaines) chez
TwelveData pour peupler l'univers.

Conventions communes au paquet : clé via `TWELVEDATA_API_KEY`, base surchargée
par `TWELVEDATA_BASE`, dégradation gracieuse (sans clé ni `requests`, tout
retombe à `None` — la chaîne continue). Le parsing est isolé en fonction pure
(`fundamentals_from_payloads`) pour être testé hors ligne.

⚠ Limite assumée : TwelveData (offre de base) n'expose PAS le nombre d'analystes.
`analyst_coverage` (S1) reste donc à fournir via l'univers ; ce module ne le
fabrique pas.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

_UA = {"User-Agent": "screener-dislocation"}
_DEFAULT_BASE = "https://api.twelvedata.com"


@dataclass
class Fundamentals:
    ticker: str
    market_cap: Optional[float] = None
    price: Optional[float] = None
    shares_outstanding: Optional[float] = None
    fifty_two_week_low: Optional[float] = None
    fifty_two_week_high: Optional[float] = None
    name: str = ""
    as_of: str = ""


def _num(v) -> Optional[float]:
    """Parse tolérant : chaînes TwelveData, None, '', 'NA' → float ou None."""
    if v in (None, "", "NA", "null"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _dig(d, *path):
    """Descend une suite de clés dans des dicts imbriqués, tolérant aux absences."""
    cur = d
    for k in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def fundamentals_from_payloads(ticker: str, stats_json=None, quote_json=None,
                               as_of: str = "") -> Fundamentals:
    """
    Assemble les fondamentaux depuis les payloads `/statistics` et `/quote`.
    Fonction pure : testable hors ligne sur des dicts synthétiques.
    """
    mcap = _num(_dig(stats_json, "statistics", "valuations_metrics",
                     "market_capitalization"))
    if mcap is None:  # certains schémas remontent market_capitalization à plat
        mcap = _num(_dig(stats_json, "market_capitalization"))
    shares = _num(_dig(stats_json, "statistics", "stock_statistics",
                       "shares_outstanding"))
    if shares is None:
        shares = _num(_dig(stats_json, "shares_outstanding"))

    price = _num(_dig(quote_json, "close")) or _num(_dig(quote_json, "price"))
    low = _num(_dig(quote_json, "fifty_two_week", "low"))
    high = _num(_dig(quote_json, "fifty_two_week", "high"))
    name = (_dig(quote_json, "name") or "") if isinstance(quote_json, dict) else ""

    # Repli : capitalisation = cours × actions si l'un des deux manque.
    if mcap is None and price is not None and shares is not None:
        mcap = price * shares

    return Fundamentals(
        ticker=ticker.upper(), market_cap=mcap, price=price,
        shares_outstanding=shares, fifty_two_week_low=low,
        fifty_two_week_high=high, name=name or "", as_of=as_of,
    )


def fetch_fundamentals(ticker: str, api_key: Optional[str] = None,
                       base: Optional[str] = None, timeout: int = 25) -> Fundamentals:
    """
    Récupère les fondamentaux d'un ticker chez TwelveData. Sans clé (ou sans
    `requests`), renvoie des fondamentaux vides — dégradation gracieuse.
    """
    api_key = api_key or os.getenv("TWELVEDATA_API_KEY", "")
    base = base or os.getenv("TWELVEDATA_BASE", _DEFAULT_BASE)
    if not api_key:
        return Fundamentals(ticker=ticker.upper())
    try:
        import requests
    except ImportError:
        return Fundamentals(ticker=ticker.upper())

    stats_json = quote_json = None
    params = {"symbol": ticker, "apikey": api_key}
    try:
        r = requests.get(f"{base}/statistics", params=params, headers=_UA, timeout=timeout)
        if r.status_code == 200:
            j = r.json()
            if isinstance(j, dict) and j.get("status") != "error":
                stats_json = j
    except Exception:  # noqa: BLE001 — dégradation gracieuse
        stats_json = None
    try:
        r = requests.get(f"{base}/quote", params=params, headers=_UA, timeout=timeout)
        if r.status_code == 200:
            j = r.json()
            if isinstance(j, dict) and j.get("status") != "error":
                quote_json = j
    except Exception:  # noqa: BLE001
        quote_json = None
    return fundamentals_from_payloads(ticker, stats_json, quote_json)
