#!/usr/bin/env python3
"""
AMF · AGENTS 2 & 4 — Enrichissement par titre
=============================================
Pour chaque titre concerné par un franchissement de seuils (agent 2) ou une
position courte (agent 4), produit :

  - ANALYSE TECHNIQUE : à partir des cours quotidiens TwelveData (Euronext),
    indicateurs calculés en pur Python (aucune dépendance lourde) :
    SMA20/50/200, EMA12/26, MACD, RSI14, variations 1m/3m, tendance, verdict.
  - SYNTHÈSE ANALYSTES : best-effort gratuit. Aucune source gratuite fiable de
    consensus sell-side sur les valeurs Euronext → renvoie « non disponible »
    de façon structurée, avec point d'extension (AMF_ANALYST_PROVIDER) pour
    brancher une clé (ex. Financial Modeling Prep) plus tard.
  - NOTE SOCIALE : réutilise l'infra sociale existante du projet (table
    `mentions` alimentée par ApeWisdom/Adanos), interrogée par symbole. La
    couverture des small caps FR y est faible → « non disponible » fréquent,
    ce qui est fidèle à la réalité.

Toutes les briques se dégradent proprement sans clé (technique N/A sans
TWELVEDATA_API_KEY, etc.). Un cache mémoire évite de réinterroger deux fois le
même ISIN dans un même passage.
"""
from __future__ import annotations
import datetime as dt
import os
import sys
import time
from typing import Optional

try:
    import requests
except ImportError:
    sys.exit("pip install requests")

try:
    from storage import Store
except Exception:  # noqa: BLE001
    Store = None

TD_KEY = os.getenv("TWELVEDATA_API_KEY", "")
TD_BASE = "https://api.twelvedata.com"
UA = {"User-Agent": "AMF-collector market"}
TIMEOUT = 20

_SYMBOL_CACHE: dict[str, Optional[dict]] = {}
_ENRICH_CACHE: dict[str, dict] = {}


# --------------------------------------------------------------------------- #
# Indicateurs (pur Python)
# --------------------------------------------------------------------------- #
def _sma(vals: list[float], n: int) -> Optional[float]:
    if len(vals) < n:
        return None
    return sum(vals[-n:]) / n


def _ema_series(vals: list[float], n: int) -> list[float]:
    if not vals:
        return []
    k = 2 / (n + 1)
    out = [vals[0]]
    for v in vals[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def _rsi(vals: list[float], n: int = 14) -> Optional[float]:
    if len(vals) < n + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(vals)):
        d = vals[i] - vals[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_g = sum(gains[:n]) / n
    avg_l = sum(losses[:n]) / n
    for i in range(n, len(gains)):
        avg_g = (avg_g * (n - 1) + gains[i]) / n
        avg_l = (avg_l * (n - 1) + losses[i]) / n
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return round(100 - 100 / (1 + rs), 1)


def _macd(vals: list[float]) -> Optional[dict]:
    if len(vals) < 35:
        return None
    ema12 = _ema_series(vals, 12)
    ema26 = _ema_series(vals, 26)
    macd_line = [a - b for a, b in zip(ema12[-len(ema26):], ema26)]
    signal = _ema_series(macd_line, 9)
    hist = macd_line[-1] - signal[-1]
    return {"macd": round(macd_line[-1], 4), "signal": round(signal[-1], 4),
            "hist": round(hist, 4)}


def _pct_change(vals: list[float], back: int) -> Optional[float]:
    if len(vals) <= back:
        return None
    old = vals[-back - 1]
    if not old:
        return None
    return round((vals[-1] / old - 1) * 100, 1)


# --------------------------------------------------------------------------- #
# TwelveData : résolution symbole + cours
# --------------------------------------------------------------------------- #
def resolve_symbol(isin: Optional[str], name: Optional[str] = None) -> Optional[dict]:
    """ISIN -> {symbol, mic_code, exchange, currency, name} via TwelveData."""
    if not TD_KEY or not isin:
        return None
    if isin in _SYMBOL_CACHE:
        return _SYMBOL_CACHE[isin]
    res = None
    try:
        r = requests.get(f"{TD_BASE}/symbol_search",
                         params={"symbol": isin, "apikey": TD_KEY, "outputsize": 20},
                         headers=UA, timeout=TIMEOUT)
        data = r.json().get("data", []) if r.ok else []
        # préfère une place Euronext, sinon la première correspondance
        def _rank(d):
            ex = (d.get("exchange") or "").lower()
            mic = (d.get("mic_code") or "").upper()
            euronext = ("euronext" in ex) or mic.startswith(("XPAR", "XAMS",
                                                             "XBRU", "XLIS", "ALXP"))
            return (not euronext,)
        cand = sorted(data, key=_rank) if data else []
        if cand:
            d = cand[0]
            res = {"symbol": d.get("symbol"), "mic_code": d.get("mic_code"),
                   "exchange": d.get("exchange"), "currency": d.get("currency"),
                   "name": d.get("instrument_name") or name}
    except Exception:  # noqa: BLE001
        res = None
    _SYMBOL_CACHE[isin] = res
    return res


def _time_series(symbol: str, mic: Optional[str], outputsize: int = 220) -> list[float]:
    params = {"symbol": symbol, "interval": "1day", "outputsize": outputsize,
              "apikey": TD_KEY, "order": "ASC"}
    if mic:
        params["mic_code"] = mic
    r = requests.get(f"{TD_BASE}/time_series", params=params, headers=UA, timeout=TIMEOUT)
    if not r.ok:
        return []
    values = r.json().get("values") or []
    closes = []
    for v in values:
        try:
            closes.append(float(v["close"]))
        except (KeyError, ValueError, TypeError):
            pass
    return closes


def technical(isin: Optional[str], name: Optional[str] = None) -> dict:
    """Analyse technique du titre. Renvoie {available, verdict, note, ...}."""
    out = {"available": False, "note": "cours indisponible (clé TwelveData absente "
           "ou titre non résolu)"}
    sym = resolve_symbol(isin, name)
    if not sym or not sym.get("symbol"):
        return out
    closes = _time_series(sym["symbol"], sym.get("mic_code"))
    if len(closes) < 40:
        out["note"] = "historique de cours insuffisant"
        out["symbol"] = sym.get("symbol")
        return out

    last = closes[-1]
    sma20, sma50, sma200 = _sma(closes, 20), _sma(closes, 50), _sma(closes, 200)
    rsi = _rsi(closes)
    macd = _macd(closes)
    chg1m, chg3m = _pct_change(closes, 21), _pct_change(closes, 63)

    # score de tendance simple (-3..+3)
    score = 0
    if sma50 and last > sma50:
        score += 1
    if sma200 and last > sma200:
        score += 1
    if sma50 and sma200 and sma50 > sma200:
        score += 1
    if sma50 and last < sma50:
        score -= 1
    if sma200 and last < sma200:
        score -= 1
    if macd and macd["hist"] > 0:
        score += 1
    elif macd and macd["hist"] < 0:
        score -= 1

    if score >= 2:
        verdict = "Haussière"
    elif score <= -2:
        verdict = "Baissière"
    else:
        verdict = "Neutre"

    notes = []
    if rsi is not None:
        if rsi >= 70:
            notes.append(f"RSI {rsi} (suracheté)")
        elif rsi <= 30:
            notes.append(f"RSI {rsi} (survendu)")
        else:
            notes.append(f"RSI {rsi}")
    if sma200 and last:
        notes.append(f"{'au-dessus' if last > sma200 else 'sous'} MM200")
    if chg1m is not None:
        notes.append(f"1M {chg1m:+}%")

    out.update({
        "available": True,
        "symbol": sym.get("symbol"),
        "exchange": sym.get("exchange"),
        "currency": sym.get("currency"),
        "last": round(last, 4),
        "sma20": round(sma20, 4) if sma20 else None,
        "sma50": round(sma50, 4) if sma50 else None,
        "sma200": round(sma200, 4) if sma200 else None,
        "rsi": rsi,
        "macd": macd,
        "chg_1m": chg1m,
        "chg_3m": chg3m,
        "trend_score": score,
        "verdict": verdict,
        "note": " · ".join(notes),
    })
    return out


# --------------------------------------------------------------------------- #
# Synthèse analystes (best-effort gratuit)
# --------------------------------------------------------------------------- #
def analyst(isin: Optional[str], symbol: Optional[str] = None,
            name: Optional[str] = None) -> dict:
    """Best-effort gratuit. Sans provider configuré -> non disponible (structuré).

    Point d'extension : si AMF_ANALYST_PROVIDER + clé sont fournis, brancher ici
    l'appel (ex. Financial Modeling Prep price-target-consensus / grades).
    """
    provider = os.getenv("AMF_ANALYST_PROVIDER", "").strip().lower()
    if provider == "fmp":
        key = os.getenv("FMP_API_KEY", "").strip()
        sym = symbol or (resolve_symbol(isin, name) or {}).get("symbol")
        if key and sym:
            try:
                r = requests.get(
                    f"https://financialmodelingprep.com/api/v4/price-target-consensus",
                    params={"symbol": sym, "apikey": key}, headers=UA, timeout=TIMEOUT)
                if r.ok and r.json():
                    d = r.json()[0]
                    return {"available": True, "provider": "fmp",
                            "target_median": d.get("targetMedian"),
                            "target_high": d.get("targetHigh"),
                            "target_low": d.get("targetLow"),
                            "note": "consensus FMP"}
            except Exception:  # noqa: BLE001
                pass
    return {"available": False, "note": "consensus analystes non disponible (gratuit)"}


# --------------------------------------------------------------------------- #
# Note sociale (réutilise l'infra existante : table `mentions`)
# --------------------------------------------------------------------------- #
def _social_from_store(store, ticker: str) -> Optional[dict]:
    since = time.time() - 48 * 3600
    cur = store.conn.cursor()
    ph = store.ph
    cur.execute(
        f"SELECT COALESCE(SUM(mentions),0), AVG(sentiment), MAX(fetched_utc) "
        f"FROM mentions WHERE ticker = {ph} AND fetched_utc >= {ph}",
        (ticker.upper(), since))
    row = cur.fetchone()
    if not row or not row[2]:
        return None
    total, sent, last = int(row[0] or 0), row[1], row[2]
    if total <= 0:
        return None
    return {"available": True, "ticker": ticker.upper(), "mentions_48h": total,
            "sentiment": round(sent, 3) if sent is not None else None,
            "last_seen_utc": last,
            "note": f"{total} mentions/48h"
                    + (f", sent. {round(sent, 2)}" if sent is not None else "")}


def social(isin: Optional[str], symbol: Optional[str] = None,
           name: Optional[str] = None) -> dict:
    """Note sociale best-effort via l'infra existante. N/A si non couvert."""
    na = {"available": False, "note": "aucune donnée sociale (faible couverture FR)"}
    if Store is None:
        return na
    # candidats de ticker : symbole TD résolu (souvent équivalent US/ADR),
    # sinon rien (les raisons sociales FR ne matchent pas la table mentions US).
    candidates = []
    if symbol:
        candidates.append(symbol.split(":")[0])
    resolved = resolve_symbol(isin, name)
    if resolved and resolved.get("symbol"):
        candidates.append(resolved["symbol"].split(":")[0])
    candidates = [c for c in candidates if c]
    if not candidates:
        return na
    seen = set()
    try:
        store = Store()
    except Exception:  # noqa: BLE001
        return na
    try:
        for tk in candidates:
            tk = (tk or "").strip().upper()
            if not tk or tk in seen:
                continue
            seen.add(tk)
            res = _social_from_store(store, tk)
            if res:
                return res
    finally:
        store.close()
    return na


# --------------------------------------------------------------------------- #
# Enrichissement complet (mémoïsé par ISIN)
# --------------------------------------------------------------------------- #
def enrich(isin: Optional[str], name: Optional[str] = None) -> dict:
    """Agrège technique + analystes + social pour un titre (cache par ISIN)."""
    key = (isin or name or "").upper()
    if key in _ENRICH_CACHE:
        return _ENRICH_CACHE[key]
    tech = technical(isin, name)
    sym = tech.get("symbol")
    out = {
        "isin": isin,
        "name": name,
        "technical": tech,
        "analyst": analyst(isin, sym, name),
        "social": social(isin, sym, name),
    }
    _ENRICH_CACHE[key] = out
    return out


if __name__ == "__main__":
    # test hors-ligne des indicateurs (données synthétiques)
    import math
    series = [100 + 10 * math.sin(i / 9) + i * 0.15 for i in range(220)]
    print("SMA20 :", round(_sma(series, 20), 3))
    print("SMA50 :", round(_sma(series, 50), 3))
    print("RSI14 :", _rsi(series))
    print("MACD  :", _macd(series))
    print("chg1m :", _pct_change(series, 21), "%")
    print("--- enrich (dégradé si pas de clé TD) ---")
    print("TD_KEY présent :", bool(TD_KEY))
    e = enrich("FR0000131906", "RENAULT")
    print("technical.available :", e["technical"]["available"], "|", e["technical"]["note"])
    print("analyst :", e["analyst"]["note"])
    print("social  :", e["social"]["note"])
