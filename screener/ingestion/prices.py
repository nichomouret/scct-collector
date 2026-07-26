#!/usr/bin/env python3
"""
Ingestion de prix — couche 0 (tranche verticale).
=================================================
Source par défaut : API chart de Yahoo Finance (JSON, sans clé). C'est une
source de démarrage gratuite pour la tranche verticale ; la SPEC prévoit FMP /
EODHD en Phase 2 (§9). Un cache disque quotidien évite de refrapper l'API, et
un chargeur CSV local permet de tourner hors ligne (tests, backtest).

Toutes les barres renvoyées sont des `PriceBar` (OHLCV close), réutilisant la
dataclass de `detection.path_archetype`.
"""
from __future__ import annotations

import csv
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import List, Optional

from ..detection.path_archetype import PriceBar

_YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range={rng}&interval=1d"
_UA = {"User-Agent": "Mozilla/5.0"}
_COLS = ["date", "open", "high", "low", "close", "volume"]


class PriceFetchError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# Réseau                                                                       #
# --------------------------------------------------------------------------- #
def fetch_yahoo(symbol: str, rng: str = "2y", timeout: int = 30) -> List[PriceBar]:
    """Barres quotidiennes depuis Yahoo. `symbol` = symbole Yahoo (ex. 'MC.PA', '^GSPC')."""
    url = _YAHOO.format(sym=urllib.parse.quote(symbol), rng=rng)
    req = urllib.request.Request(url, headers=_UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = json.loads(r.read())
    except Exception as e:  # noqa: BLE001 — on remonte une erreur typée
        raise PriceFetchError(f"{symbol}: {type(e).__name__}: {e}") from e

    chart = (payload or {}).get("chart") or {}
    if chart.get("error"):
        raise PriceFetchError(f"{symbol}: {chart['error']}")
    results = chart.get("result") or []
    if not results:
        raise PriceFetchError(f"{symbol}: réponse vide")
    res = results[0]
    ts = res.get("timestamp") or []
    q = ((res.get("indicators") or {}).get("quote") or [{}])[0]
    bars: List[PriceBar] = []
    for i, t in enumerate(ts):
        o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
        v = q["volume"][i]
        if None in (o, h, l, c) or v is None:
            continue
        date = datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d")
        bars.append(PriceBar(date, float(o), float(h), float(l), float(c), float(v)))
    if not bars:
        raise PriceFetchError(f"{symbol}: aucune barre exploitable")
    return bars


# --------------------------------------------------------------------------- #
# CSV local                                                                    #
# --------------------------------------------------------------------------- #
def write_csv(bars: List[PriceBar], path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(_COLS)
        for b in bars:
            w.writerow([b.date, b.open, b.high, b.low, b.close, b.volume])


def read_csv(path: str) -> List[PriceBar]:
    bars: List[PriceBar] = []
    with open(path) as f:
        for r in csv.DictReader(f):
            bars.append(PriceBar(r["date"], float(r["open"]), float(r["high"]),
                                 float(r["low"]), float(r["close"]), float(r["volume"])))
    if not bars:
        raise PriceFetchError(f"{path}: CSV vide")
    return bars


def _is_fresh_today(path: str) -> bool:
    if not os.path.exists(path):
        return False
    mtime = datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc).date()
    return mtime == datetime.now(tz=timezone.utc).date()


# --------------------------------------------------------------------------- #
# API de haut niveau                                                           #
# --------------------------------------------------------------------------- #
def load_bars(symbol: str, cache_dir: Optional[str] = None, rng: str = "2y",
              refresh: bool = False, offline: bool = False) -> List[PriceBar]:
    """
    Charge les barres d'un symbole, avec cache quotidien.

    - offline=True : lit uniquement le cache CSV (aucun réseau) ; lève si absent.
    - sinon : sert le cache s'il date d'aujourd'hui, sinon frappe Yahoo et met à jour.

    Le cache est keyé par (symbole, `rng`) : deux fenêtres d'historique
    différentes ne se recouvrent pas (sinon `--range 10y` renverrait le cache
    2y/5y déjà présent).
    """
    safe = symbol.replace("/", "_")
    cache_path = os.path.join(cache_dir, f"{safe}_{rng}.csv") if cache_dir else None

    if offline:
        if cache_path and os.path.exists(cache_path):
            return read_csv(cache_path)
        raise PriceFetchError(f"{symbol}: mode hors ligne et cache absent ({cache_path})")

    if cache_path and not refresh and _is_fresh_today(cache_path):
        return read_csv(cache_path)

    bars = fetch_yahoo(symbol, rng=rng)
    if cache_path:
        write_csv(bars, cache_path)
    return bars
