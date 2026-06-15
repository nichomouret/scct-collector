#!/usr/bin/env python3
"""
SCCT-Social · Calcul des signaux C1 (volume spike) à partir de la série `mentions`
==================================================================================
Lit les snapshots accumulés par social_collector.py et calcule, par ticker :
  - last_mentions      : dernier volume de mentions
  - baseline_mean/std  : moyenne/écart-type sur la fenêtre de référence
  - z_score            : (last - mean) / std      -> C1 brut (Z > 3 = anomalie)
  - velocity_1h        : variation de mentions sur ~1h (burstiness brut, sous-signal C2)
  - n_obs              : nombre de snapshots (qualité du baseline)

⚠ Le baseline 30j de la spec demande ~30 jours de collecte. En attendant, la
fenêtre est paramétrable (--baseline-hours) et n_obs indique si le Z est fiable.

Usage :
    python compute_signals.py                       # console, tri par z_score
    python compute_signals.py --source apewisdom --baseline-hours 720
    python compute_signals.py --csv signals.csv
"""
from __future__ import annotations
import argparse, csv, os, statistics, sys
from collections import defaultdict

try:
    from storage import Store
except Exception as e:
    sys.exit(f"storage indisponible: {e}")


def load_series(store: Store, source: str):
    cur = store.conn.cursor()
    ph = store.ph
    cur.execute(
        f"SELECT ticker, fetched_utc, mentions FROM mentions "
        f"WHERE source = {ph} AND mentions IS NOT NULL ORDER BY ticker, fetched_utc",
        (source,))
    series = defaultdict(list)
    for tk, ts, m in cur.fetchall():
        series[tk].append((float(ts), float(m)))
    return series


def compute(series, baseline_hours: float):
    rows = []
    for tk, pts in series.items():
        if len(pts) < 2:
            continue
        pts.sort()
        now_ts, last = pts[-1]
        window = [m for ts, m in pts if ts >= now_ts - baseline_hours * 3600]
        base = window[:-1] if len(window) > 1 else [m for _, m in pts[:-1]]
        if len(base) < 2:
            mean = base[0] if base else last
            std = 0.0
        else:
            mean = statistics.fmean(base)
            std = statistics.pstdev(base)
        z = (last - mean) / std if std > 0 else (0.0 if last <= mean else float("inf"))
        # vélocité ~1h : dernier point vs point le plus proche de t-1h
        prev = None
        for ts, m in pts[:-1]:
            if ts <= now_ts - 3600:
                prev = m
        velocity = (last - prev) if prev is not None else None
        rows.append({
            "ticker": tk, "last_mentions": int(last),
            "baseline_mean": round(mean, 1), "baseline_std": round(std, 1),
            "z_score": (round(z, 2) if z != float("inf") else "inf"),
            "velocity_1h": (int(velocity) if velocity is not None else None),
            "n_obs": len(pts),
        })
    # tri : Z décroissant (inf en tête)
    rows.sort(key=lambda r: (r["z_score"] == "inf", r["z_score"] if r["z_score"] != "inf" else 0),
              reverse=True)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="apewisdom")
    ap.add_argument("--baseline-hours", type=float, default=720.0, help="défaut 30j")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--csv", default="")
    args = ap.parse_args()

    store = Store()
    series = load_series(store, args.source)
    if not series:
        print(f"Aucune donnée pour source='{args.source}'. Lance d'abord social_collector.py.")
        return
    rows = compute(series, args.baseline_hours)

    hdr = f"{'Ticker':<8}{'Mentions':>9}{'Z(C1)':>8}{'Vél.1h':>8}{'Baseline':>10}{'n':>5}"
    print(hdr); print("-" * len(hdr))
    for r in rows[:args.top]:
        z = r["z_score"]; flag = "  <-- anomalie" if (z == "inf" or (isinstance(z, float) and z > 3)) else ""
        vel = r["velocity_1h"] if r["velocity_1h"] is not None else "-"
        print(f"{r['ticker']:<8}{r['last_mentions']:>9}{str(z):>8}{str(vel):>8}"
              f"{r['baseline_mean']:>10}{r['n_obs']:>5}{flag}")
    print("\nZ(C1) > 3 = condition d'entrée (anomalie de volume). n faible => baseline peu fiable, "
          "laisser la collecte accumuler.")

    if args.csv:
        with open(args.csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader(); w.writerows(rows)
        print(f"CSV -> {args.csv}")


if __name__ == "__main__":
    main()
