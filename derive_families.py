#!/usr/bin/env python3
"""
SCCT-Social · Classement des familles de backtest sur l'UNIVERS MICRO-CAP
=========================================================================
Le trending GLOBAL d'Adanos est dominé par les mega-caps (NVDA/AAPL/…) — hors
périmètre SCCT. On classe donc sur TON univers micro-cap (screening structurel :
float bas + short interest élevé, cf. spec §4.1), en croisant buzz × mouvement :

  buzz fort + a bougé   -> famille 1/2  (vrai positif : squeeze)
  buzz fort + prix plat -> FAMILLE 3    (faux positif : buzz sans mouvement)
  a bougé  + buzz faible-> FAMILLE 4    (vrai négatif : mouvement sans social)
  plat + faible buzz    -> hors échantillon

Anti-biais : les familles 3/4 sortent des DONNÉES (pas choisies a priori).

Entrée : un univers micro-cap (--universe fichier 1 ticker/ligne, ou --tickers).
  -> à produire via un screener (Finviz : Micro-cap + Short Float élevé + Float bas,
     ou l'export de ton screening structurel). C'est la watchlist de la spec.

Pour chaque ticker : mentions Adanos (fenêtre) + montée prix TwelveData (fenêtre).

Usage :
    export ADANOS_API_KEY=sk_live_xxx TWELVEDATA_API_KEY=yyy
    python derive_families.py --universe microcaps.txt --from 2025-07-01 --to 2026-02-28
    python derive_families.py --tickers OCC ABCD EFGH --high-mentions 200 --low-mentions 40
"""
from __future__ import annotations
import argparse, csv, os, sys, time

try:
    import requests
except ImportError:
    sys.exit("pip install requests")

ADANOS_KEY = os.getenv("ADANOS_API_KEY", "")
TD_KEY = os.getenv("TWELVEDATA_API_KEY", "")
ADANOS_BASE = os.getenv("ADANOS_BASE", "https://api.adanos.org/reddit/stocks")
UA = {"User-Agent": "SCCT-Social families"}


def adanos_mentions(tk, dfrom, dto):
    try:
        r = requests.get(f"{ADANOS_BASE}/v1/stock/{tk}", params={"from": dfrom, "to": dto},
                         headers={"X-API-Key": ADANOS_KEY, **UA}, timeout=30)
        if r.status_code == 404:
            return 0
        if r.status_code != 200:
            return None
        return r.json().get("mentions", 0)
    except Exception:
        return None


def td_drawup(tk, dfrom, dto):
    if not TD_KEY or TD_KEY == "xxx":
        return None
    try:
        r = requests.get("https://api.twelvedata.com/time_series", params={
            "symbol": tk, "interval": "1day", "start_date": dfrom, "end_date": dto,
            "apikey": TD_KEY, "order": "ASC", "outputsize": 5000}, headers=UA, timeout=30)
        js = r.json()
        if not isinstance(js, dict) or js.get("status") == "error":
            return None
        closes = [float(v["close"]) for v in js.get("values", [])]
        if len(closes) < 2:
            return None
        run, lo = 0.0, closes[0]
        for c in closes:
            lo = min(lo, c); run = max(run, (c - lo) / lo if lo else 0.0)
        return round(run, 3)
    except Exception:
        return None


def classify(ment, run, hi, lo, mv):
    if ment is None or run is None:
        return "données incomplètes"
    high, low = ment >= hi, ment <= lo
    moved = run >= mv
    if high and moved:
        return "Famille 1/2 (vrai positif)"
    if high and not moved:
        return "FAMILLE 3 (faux positif)"
    if low and moved:
        return "FAMILLE 4 (vrai négatif)"
    if low and not moved:
        return "hors échantillon (plat+calme)"
    return "zone grise (buzz/mouvement intermédiaire)"


def load_universe(args):
    if args.universe and os.path.exists(args.universe):
        with open(args.universe) as f:
            return [ln.strip().upper() for ln in f
                    if ln.strip() and not ln.startswith("#")]
    return [t.upper() for t in args.tickers]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", default="", help="fichier 1 ticker/ligne (micro-caps screenées)")
    ap.add_argument("--tickers", nargs="*", default=[])
    ap.add_argument("--from", dest="dfrom", default="2025-07-01")
    ap.add_argument("--to", dest="dto", default="2026-02-28")
    ap.add_argument("--high-mentions", type=int, default=200, help="seuil 'fort buzz'")
    ap.add_argument("--low-mentions", type=int, default=40, help="seuil 'faible buzz'")
    ap.add_argument("--move-threshold", type=float, default=0.30, help="run_up = mouvement réel")
    ap.add_argument("--out", default="families.csv")
    args = ap.parse_args()

    if not ADANOS_KEY:
        sys.exit("ADANOS_API_KEY absent.")
    if not TD_KEY or TD_KEY == "xxx":
        sys.exit("TWELVEDATA_API_KEY absent/placeholder.")
    universe = load_universe(args)
    if not universe:
        sys.exit("Aucun ticker. Passe --universe microcaps.txt ou --tickers T1 T2 …\n"
                 "Astuce : Finviz screener -> Micro-cap + Float bas + Short Float élevé -> export.")

    print(f"Univers : {len(universe)} micro-caps · fenêtre {args.dfrom}->{args.dto}")
    print(f"seuils : buzz fort >={args.high_mentions}, faible <={args.low_mentions}, "
          f"mouvement >={args.move_threshold:.0%}\n")
    hdr = f"{'Ticker':<8}{'Mentions':>9}{'run_up':>8}  Classement"
    print(hdr); print("-" * (len(hdr) + 14))
    rows = []
    for tk in universe:
        ment = adanos_mentions(tk, args.dfrom, args.dto)
        run = td_drawup(tk, args.dfrom, args.dto)
        verdict = classify(ment, run, args.high_mentions, args.low_mentions, args.move_threshold)
        print(f"{tk:<8}{str(ment):>9}{str(run):>8}  {verdict}")
        rows.append({"ticker": tk, "mentions": ment, "run_up": run, "family": verdict})
        time.sleep(8)  # TwelveData free 8/min

    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["ticker", "mentions", "run_up", "family"])
        w.writeheader(); w.writerows(rows)
    counts = {}
    for r in rows:
        counts[r["family"]] = counts.get(r["family"], 0) + 1
    print(f"\n-> {args.out}")
    for k in sorted(counts):
        print(f"  {counts[k]:>3}  {k}")
    print("\nPasse ensuite l'échantillon élargi dans C1/C2/C3 et remplis la matrice de "
          "confusion : familles 1/2 doivent scorer HAUT, 3/4 BAS.")


if __name__ == "__main__":
    main()
