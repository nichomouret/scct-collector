#!/usr/bin/env python3
"""
SCCT-Social · Test de couverture Adanos (AVANT d'abonner)
=========================================================
Quiver ayant retiré sa donnée Reddit, Adanos est la seule voie Reddit historique.
Ce script tranche, avec la CLÉ GRATUITE d'abord, sans rien payer :

  1) /v1/search?q=OCC      -> OCC est-il CONNU d'Adanos (base 35k tickers) ?
                              Non bridé par la profondeur historique -> testable gratis.
  2) /v1/stock/{ticker}    -> mentions sur une fenêtre. Sur clé gratuite : 30 j max
                              (422 si on demande plus loin -> il faut Pro 365 j).

Auth : header X-API-Key (clé sk_live_... obtenue sur adanos.org).
Base : https://api.adanos.org/reddit/stocks

Usage :
    export ADANOS_API_KEY=sk_live_xxx
    python backtest_coverage.py                          # search + stock (30 derniers j)
    python backtest_coverage.py --from 2025-08-01 --to 2026-01-31   # fenêtre backtest (Pro)
    python backtest_coverage.py --tickers OCC OPEN KSS DNUT GME AMC --csv coverage.csv
"""
from __future__ import annotations
import argparse, csv, datetime as dt, os, sys, time

try:
    import requests
except ImportError:
    sys.exit("pip install requests")

ADANOS_KEY = os.getenv("ADANOS_API_KEY", "")
ADANOS_BASE = os.getenv("ADANOS_BASE", "https://api.adanos.org/reddit/stocks")
UA = {"User-Agent": "SCCT-Social backtest coverage"}
DEFAULT_TICKERS = ["OCC", "OPEN", "KSS", "DNUT", "AMC", "GME"]


def _headers():
    return {"X-API-Key": ADANOS_KEY, **UA}


def search_known(tk: str) -> dict:
    """/v1/search : OCC est-il dans la base Adanos ? (gratuit, non bridé historique)"""
    res = {"ticker": tk, "known": "?", "name": "-", "exchange": "-",
           "recent_mentions": "-", "note": ""}
    try:
        r = requests.get(f"{ADANOS_BASE}/v1/search",
                         params={"q": tk, "limit": 50}, headers=_headers(), timeout=25)
        if r.status_code == 401:
            res["note"] = "clé invalide (401)"; return res
        if r.status_code == 429:
            res["note"] = "quota atteint (429)"; return res
        if r.status_code != 200:
            res["note"] = f"HTTP {r.status_code}"; return res
        js = r.json()
        hit = next((x for x in js.get("results", [])
                    if str(x.get("ticker", "")).upper() == tk.upper()), None)
        if not hit:
            res["known"] = "NON"; res["note"] = "absent de la base Adanos"; return res
        res["known"] = "oui"
        res["name"] = (hit.get("name") or "-")[:24]
        res["exchange"] = hit.get("exchange") or "-"
        summ = hit.get("summary") or {}
        res["recent_mentions"] = summ.get("mentions", "-")
    except Exception as e:
        res["note"] = f"err {e}"
    return res


def stock_window(tk: str, dfrom: str | None, dto: str | None) -> dict:
    """/v1/stock/{ticker} : mentions sur la fenêtre + profondeur effective atteinte."""
    res = {"ticker": tk, "found": "?", "mentions": "-", "earliest": "-", "note": ""}
    params = {}
    if dfrom and dto:
        params = {"from": dfrom, "to": dto}
    try:
        r = requests.get(f"{ADANOS_BASE}/v1/stock/{tk}",
                         params=params, headers=_headers(), timeout=25)
        if r.status_code == 404:
            res["found"] = "non"; res["note"] = "0 mention sur la période"; return res
        if r.status_code == 422:
            res["note"] = "422 fenêtre hors plan (gratuit=30j) -> Pro requis pour mi-2025"
            return res
        if r.status_code == 401:
            res["note"] = "clé invalide (401)"; return res
        if r.status_code == 429:
            res["note"] = "quota atteint (429)"; return res
        if r.status_code != 200:
            res["note"] = f"HTTP {r.status_code}"; return res
        js = r.json()
        res["found"] = "oui" if js.get("found") else "non"
        res["mentions"] = js.get("mentions", "-")
        dates = [d.get("date") for d in (js.get("daily_trend") or []) if d.get("date")]
        res["earliest"] = min(dates) if dates else "-"
        rem = r.headers.get("X-RateLimit-Remaining")
        if rem is not None:
            res["note"] = f"quota restant {rem}"
    except Exception as e:
        res["note"] = f"err {e}"
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="dfrom", default=None)
    ap.add_argument("--to", dest="dto", default=None)
    ap.add_argument("--tickers", nargs="+", default=DEFAULT_TICKERS)
    ap.add_argument("--csv", default="")
    args = ap.parse_args()

    if not ADANOS_KEY:
        sys.exit("ADANOS_API_KEY absent. export ADANOS_API_KEY=sk_live_... puis relance.")

    win = f"{args.dfrom} -> {args.dto}" if args.dfrom else "30 derniers jours (défaut clé gratuite)"
    print(f"Adanos — base de tickers + couverture. Fenêtre stock : {win}\n")

    rows = []
    hdr = (f"{'Ticker':<7}{'Connu':<6}{'Société':<26}{'Bourse':<8}"
           f"{'Mentions(win)':<14}{'+ancienne':<12}{'Note'}")
    print(hdr); print("-" * (len(hdr) + 16))
    for tk in args.tickers:
        k = search_known(tk)
        s = stock_window(tk, args.dfrom, args.dto)
        print(f"{tk:<7}{str(k['known']):<6}{str(k['name']):<26}{str(k['exchange']):<8}"
              f"{str(s['mentions']):<14}{str(s['earliest']):<12}{s['note'] or k['note']}")
        rows.append({"ticker": tk, "known": k["known"], "name": k["name"],
                     "exchange": k["exchange"], "recent_mentions": k["recent_mentions"],
                     "found_window": s["found"], "mentions_window": s["mentions"],
                     "earliest": s["earliest"], "note": s["note"] or k["note"]})
        time.sleep(0.3)

    print("\nLecture :")
    print(" • Connu=oui  -> OCC est dans la base Adanos (bon signe, indépendant du plan).")
    print(" • Mentions(win) vide + note 422 -> il FAUT Pro (365j) pour valider mi-2025.")
    print(" • Connu=NON partout -> Adanos ne couvre pas -> forward-test propriétaire.")
    print(" Décision : si OCC connu + couvert -> 1 mois Adanos Pro pour extraire le backtest, puis résilier.")

    if args.csv:
        with open(args.csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader(); w.writerows(rows)
        print(f"\nCSV -> {args.csv}")


if __name__ == "__main__":
    main()
