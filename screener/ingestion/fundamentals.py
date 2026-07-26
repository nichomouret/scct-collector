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

import json
import os
import urllib.request
from dataclasses import dataclass
from typing import Dict, Optional

_UA = {"User-Agent": "screener-dislocation nm@nmgroup.be"}
_DEFAULT_BASE = "https://api.twelvedata.com"

# SEC — sans clé (source par défaut de la capitalisation). L'offre gratuite de
# TwelveData ne donne PAS /statistics ; SEC fournit le nombre d'actions, qu'on
# multiplie par le cours (Yahoo, déjà chargé) pour obtenir la capitalisation.
_SEC_TICKERS = "https://www.sec.gov/files/company_tickers.json"
_SEC_CONCEPT = ("https://data.sec.gov/api/xbrl/companyconcept/"
                "CIK{cik:010d}/dei/EntityCommonStockSharesOutstanding.json")


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


# --------------------------------------------------------------------------- #
# Source SEC (sans clé) : nombre d'actions → capitalisation = actions × cours   #
# --------------------------------------------------------------------------- #
def parse_sec_shares(concept_json) -> Optional[float]:
    """Nombre d'actions le plus récent depuis un payload companyconcept (dei).

    Fonction pure (testable). Retient la valeur à la date de fin la plus
    récente, toutes unités « shares » confondues."""
    units = _dig(concept_json, "units")
    if not isinstance(units, dict):
        return None
    rows = []
    for arr in units.values():
        if isinstance(arr, list):
            rows.extend(r for r in arr if isinstance(r, dict) and "val" in r)
    if not rows:
        return None
    best = max(rows, key=lambda r: r.get("end", ""))
    return _num(best.get("val"))


def load_cik_map(timeout: int = 25) -> Dict[str, int]:
    """Table ticker → CIK depuis SEC (sans clé). {} si indisponible."""
    try:
        req = urllib.request.Request(_SEC_TICKERS, headers=_UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
    except Exception:  # noqa: BLE001 — dégradation gracieuse
        return {}
    out: Dict[str, int] = {}
    for v in data.values() if isinstance(data, dict) else []:
        t = (v.get("ticker") or "").upper()
        if t and v.get("cik_str") is not None:
            out[t] = int(v["cik_str"])
    return out


def fetch_sec_shares(cik: int, timeout: int = 25) -> Optional[float]:
    """Nombre d'actions en circulation (SEC XBRL, sans clé). None si absent."""
    try:
        req = urllib.request.Request(_SEC_CONCEPT.format(cik=cik), headers=_UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return parse_sec_shares(json.load(r))
    except Exception:  # noqa: BLE001
        return None


def sec_fundamentals(ticker: str, price: Optional[float],
                     cik_map: Dict[str, int], name: str = "",
                     as_of: str = "", timeout: int = 25) -> Fundamentals:
    """Fondamentaux via SEC : capitalisation = actions (SEC) × cours (fourni).

    `price` vient de la couche prix (Yahoo) déjà chargée par l'appelant. Hors US
    (ticker absent de la table SEC) → capitalisation None (documenté)."""
    cik = cik_map.get(ticker.upper())
    shares = fetch_sec_shares(cik, timeout=timeout) if cik is not None else None
    mcap = (price * shares) if (price is not None and shares is not None) else None
    return Fundamentals(ticker=ticker.upper(), market_cap=mcap, price=price,
                        shares_outstanding=shares, name=name, as_of=as_of)


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
