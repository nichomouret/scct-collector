#!/usr/bin/env python3
"""
SCCT-Social · C4 — Pression de float (le « carburant » du squeeze)
==================================================================
C4 combine float réduit + short interest élevé + borrow fee élevé (logique Ortex).
Aucun fournisseur social ne le donne — il faut une source short-interest.

Sources :
  - Float            -> TwelveData (/statistics)
  - Short interest % -> Ortex (estimation quotidienne ; le SI officiel FINRA est bi-mensuel)
  - Borrow fee (CTB) -> Ortex (cost-to-borrow, quotidien)
  - Days-to-cover / utilization -> Ortex (diagnostics)

Normalisation IDENTIQUE à l'onglet Assumptions du modèle Excel P1, pour que les
scores soient cohérents entre le pull et le classeur :
  score_float  = clamp((float_cap - float)/(float_cap - float_floor), 0, 1)
  score_si     = clamp(SI% / si_cap, 0, 1)
  score_borrow = clamp(borrow / bf_cap, 0, 1)
  C4 = (wf*score_float + wsi*score_si + wb*score_borrow) / (wf+wsi+wb)

Paramètres (env, défauts = Assumptions Excel) :
  FLOAT_FLOOR_M=5  FLOAT_CAP_M=300  SI_CAP=0.40  BF_CAP=1.00
  W_FLOAT=0.40  W_SI=0.40  W_BORROW=0.20

⚠ Endpoints Ortex marqués ADAPTER : le schéma exact dépend de ton plan Ortex.
Brancher les bons chemins/champs à la souscription. Sans clé Ortex, le script
remplit quand même le float (TwelveData) et laisse SI/borrow vides.

Usage :
    export TWELVEDATA_API_KEY=xxx ORTEX_API_KEY=xxx
    python ortex_c4_pull.py --tickers OCC OPEN KSS DNUT AMC GME --out c4_structure.csv
"""
from __future__ import annotations
import argparse, csv, os, sys, time

try:
    import requests
except ImportError:
    sys.exit("pip install requests")

TD_KEY = os.getenv("TWELVEDATA_API_KEY", "")
ORTEX_KEY = os.getenv("ORTEX_API_KEY", "")
UA = {"User-Agent": "SCCT-Social C4"}

FLOAT_FLOOR = float(os.getenv("FLOAT_FLOOR_M", "5")) * 1e6
FLOAT_CAP = float(os.getenv("FLOAT_CAP_M", "300")) * 1e6
SI_CAP = float(os.getenv("SI_CAP", "0.40"))
BF_CAP = float(os.getenv("BF_CAP", "1.00"))
W_FLOAT = float(os.getenv("W_FLOAT", "0.40"))
W_SI = float(os.getenv("W_SI", "0.40"))
W_BORROW = float(os.getenv("W_BORROW", "0.20"))

DEFAULT_TICKERS = ["OCC", "OPEN", "KSS", "DNUT", "AMC", "GME"]


def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def fetch_float(tk: str):
    if not TD_KEY:
        return None
    try:
        r = requests.get("https://api.twelvedata.com/statistics",
                         params={"symbol": tk, "apikey": TD_KEY}, headers=UA, timeout=25)
        js = r.json()
        stats = (js.get("statistics") or {})
        sd = stats.get("stock_statistics") or {}
        # champ float selon le plan TwelveData (ADAPTER si nommé différemment)
        for k in ("float_shares", "shares_float", "floatShares"):
            if sd.get(k):
                return float(sd[k])
    except Exception as e:
        print(f"  [{tk}] TwelveData float err {e}", file=sys.stderr)
    return None


DEBUG = os.getenv("ORTEX_DEBUG", "") in ("1", "true", "yes")
ORTEX_BASE = os.getenv("ORTEX_BASE", "https://api.ortex.com/api/v1/stock/us")
# Endpoints CTB candidats (cost-to-borrow) — le bon dépend du plan ; debug aide.
CTB_PATHS = ["cost_to_borrow", "ctb", "cost-to-borrow"]


def _pct(v):
    if v is None:
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v / 100.0 if v > 1 else v


def _last_row(js):
    rows = js.get("rows") if isinstance(js, dict) else None
    if rows:
        return rows[-1]  # le plus récent (ordre chronologique)
    return js if isinstance(js, dict) else None


def fetch_ortex(tk: str):
    """Renvoie dict {si, borrow, dtc, util, float_shares} depuis Ortex.
    SI% + shares -> on dérive le free float (shares / (SI%/100))."""
    out = {"si": None, "borrow": None, "dtc": None, "util": None,
           "float_shares": None, "si_usd": None}
    if not ORTEX_KEY:
        return out
    headers = {"Ortex-Api-Key": ORTEX_KEY, **UA}
    # --- short interest (endpoint confirmé) ---
    try:
        r = requests.get(f"{ORTEX_BASE}/{tk}/short_interest", headers=headers, timeout=25)
        if DEBUG:
            print(f"  [debug SI] {r.status_code} : {r.text[:200]}", file=sys.stderr)
        if r.status_code == 200:
            row = _last_row(r.json())
            if row:
                out["si"] = _pct(row.get("shortInterestPcFreeFloat"))
                shares = row.get("shortInterestShares")
                if out["si"] and shares:
                    out["float_shares"] = float(shares) / out["si"]  # free float dérivé
                if row.get("shortInterestUsd") is not None:
                    out["si_usd"] = float(row["shortInterestUsd"])
                if row.get("daysToCover") is not None:
                    out["dtc"] = float(row["daysToCover"])
        else:
            print(f"  [{tk}] Ortex SI HTTP {r.status_code} : {r.text[:150]}", file=sys.stderr)
    except Exception as e:
        print(f"  [{tk}] Ortex SI err {e}", file=sys.stderr)
    # --- cost-to-borrow (endpoint à confirmer ; ORTEX_SKIP_CTB=1 pour économiser les appels) ---
    if os.getenv("ORTEX_SKIP_CTB", "") in ("1", "true", "yes"):
        return out
    for path in CTB_PATHS:
        try:
            r = requests.get(f"{ORTEX_BASE}/{tk}/{path}", headers=headers, timeout=25)
            if DEBUG:
                print(f"  [debug CTB {path}] {r.status_code} : {r.text[:200]}", file=sys.stderr)
            if r.status_code == 200:
                row = _last_row(r.json())
                if row:
                    for k in ("costToBorrow", "costToBorrowNew", "ctb", "fee", "borrowFee"):
                        if row.get(k) is not None:
                            out["borrow"] = _pct(row[k]); break
                    if row.get("utilization") is not None:
                        out["util"] = _pct(row["utilization"])
                break
        except Exception as e:
            if DEBUG:
                print(f"  [{tk}] CTB {path} err {e}", file=sys.stderr)
    return out


def compute_c4(float_shares, si, bf):
    sf = ss = sb = None
    parts, weights = [], []
    if float_shares is not None:
        sf = clamp((FLOAT_CAP - float_shares) / (FLOAT_CAP - FLOAT_FLOOR))
        parts.append(sf * W_FLOAT); weights.append(W_FLOAT)
    if si is not None:
        ss = clamp(si / SI_CAP); parts.append(ss * W_SI); weights.append(W_SI)
    if bf is not None:
        sb = clamp(bf / BF_CAP); parts.append(sb * W_BORROW); weights.append(W_BORROW)
    c4 = sum(parts) / sum(weights) if weights else None
    return sf, ss, sb, (round(c4, 3) if c4 is not None else None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", nargs="+", default=DEFAULT_TICKERS)
    ap.add_argument("--out", default="c4_structure.csv")
    args = ap.parse_args()
    if not TD_KEY and not ORTEX_KEY:
        sys.exit("Ni TWELVEDATA_API_KEY ni ORTEX_API_KEY. Exporte au moins l'un des deux.")
    if not TD_KEY:
        print("⚠ TWELVEDATA_API_KEY absent -> colonne Float vide.", file=sys.stderr)
    if not ORTEX_KEY:
        print("⚠ ORTEX_API_KEY absent -> SI%/Borrow vides. "
              "Pour tester : export ORTEX_API_KEY=TEST (clé démo Ortex).", file=sys.stderr)

    rows = []
    hdr = f"{'Ticker':<7}{'Float(M)':>10}{'SI%':>7}{'Borrow%':>9}{'DTC':>6}{'sFloat':>8}{'sSI':>6}{'sBorr':>7}{'C4':>7}"
    print(hdr); print("-" * len(hdr))
    for tk in args.tickers:
        ox = fetch_ortex(tk)
        fl = ox["float_shares"] or fetch_float(tk)  # float Ortex (dérivé) prioritaire
        si, bf, dtc, util = ox["si"], ox["borrow"], ox["dtc"], ox["util"]
        sf, ss, sb, c4 = compute_c4(fl, si, bf)
        rows.append({"ticker": tk,
                     "float_m": round(fl / 1e6, 2) if fl else None,
                     "short_int_pct": round(si, 4) if si is not None else None,
                     "borrow_fee": round(bf, 4) if bf is not None else None,
                     "days_to_cover": dtc, "utilization": util,
                     "score_float": round(sf, 3) if sf is not None else None,
                     "score_si": round(ss, 3) if ss is not None else None,
                     "score_borrow": round(sb, 3) if sb is not None else None,
                     "C4": c4})
        def s(x): return "-" if x is None else x
        print(f"{tk:<7}{s(rows[-1]['float_m']):>10}{s(rows[-1]['short_int_pct']):>7}"
              f"{s(rows[-1]['borrow_fee']):>9}{s(dtc):>6}{s(rows[-1]['score_float']):>8}"
              f"{s(rows[-1]['score_si']):>6}{s(rows[-1]['score_borrow']):>7}{s(c4):>7}")
        time.sleep(8)  # TwelveData free = 8 req/min

    with open(args.out, "w", newline="") as f:
        cols = ["ticker", "float_m", "short_int_pct", "borrow_fee", "days_to_cover",
                "utilization", "score_float", "score_si", "score_borrow", "C4"]
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)
    print(f"\n-> {args.out}")
    print("C4 élevé = float serré + SI élevé + borrow cher = fort potentiel de squeeze (carburant).")
    print("Reporter ces colonnes dans l'onglet Backtest_A du modèle Excel P1 (mêmes normalisations).")


if __name__ == "__main__":
    main()
