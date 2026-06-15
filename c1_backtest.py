#!/usr/bin/env python3
"""
SCCT-Social · C1 en mode BACKTEST — pic de Z-score glissant
===========================================================
Différence clé avec compute_signals.py :
  - compute_signals.py = TEMPS RÉEL : Z sur le DERNIER point (spike-t-il MAINTENANT ?).
  - c1_backtest.py     = BACKTEST   : Z GLISSANT sur toute la série, on garde le PIC
                                       (le ticker a-t-il déclenché l'anomalie, et QUAND ?).

Pour chaque ticker, sur backtest_social.csv :
  - série quotidienne de mentions
  - baseline glissante (W jours antérieurs), Z[t] = (mentions[t] - mean) / std
  - peak_z + date du pic + 'fired' (peak_z > seuil) + pic brut de mentions
Le 'fired_date' se compare ensuite au pic de PRIX (c3_aligned.csv) : si le pic de
mentions précède le pic de prix -> antériorité (C3), c'est le signal Q1/Q2.

Stdlib uniquement.

Usage :
    python c1_backtest.py --social backtest_social.csv
    python c1_backtest.py --window 30 --z-threshold 3 --csv c1_backtest.csv
"""
from __future__ import annotations
import argparse, csv, statistics, sys, os
from collections import defaultdict


def read_social(path: str):
    series = defaultdict(dict)
    with open(path) as f:
        for row in csv.DictReader(f):
            tk = row["ticker"].upper()
            try:
                series[tk][row["date"]] = float(row["mentions"] or 0)
            except ValueError:
                pass
    return series


def rolling_peak_z(dates_sorted, vals, window: int):
    """Z glissant : baseline = W points antérieurs. Renvoie (peak_z, peak_date, n_eval)."""
    peak_z, peak_date, n_eval = None, None, 0
    for t in range(len(vals)):
        base = vals[max(0, t - window):t]
        if len(base) < max(3, window // 3):   # baseline minimale fiable
            continue
        mean = statistics.fmean(base)
        std = statistics.pstdev(base)
        if std <= 0:
            z = float("inf") if vals[t] > mean else 0.0
        else:
            z = (vals[t] - mean) / std
        n_eval += 1
        zc = 1e9 if z == float("inf") else z
        if peak_z is None or zc > peak_z:
            peak_z, peak_date = zc, dates_sorted[t]
    return peak_z, peak_date, n_eval


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--social", default="backtest_social.csv")
    ap.add_argument("--window", type=int, default=30, help="baseline glissante (jours)")
    ap.add_argument("--z-threshold", type=float, default=3.0)
    ap.add_argument("--csv", default="")
    args = ap.parse_args()

    if not os.path.exists(args.social):
        sys.exit(f"{args.social} introuvable. Lance d'abord adanos_backtest_pull.py.")

    social = read_social(args.social)
    rows = []
    hdr = f"{'Ticker':<7}{'Pic Z(C1)':>11}{'Date pic Z':>13}{'Pic mentions':>14}{'Date pic':>13}{'Fired':>7}{'n':>5}"
    print(hdr); print("-" * len(hdr))
    for tk in sorted(social):
        dates = sorted(social[tk])
        vals = [social[tk][d] for d in dates]
        if len(vals) < 4:
            print(f"{tk:<7}{'(série trop courte)':>40}");
            rows.append({"ticker": tk, "note": "série trop courte", "n_days": len(vals)})
            continue
        pz, pdate, n = rolling_peak_z(dates, vals, args.window)
        mpeak_i = max(range(len(vals)), key=lambda i: vals[i])
        mpeak_date, mpeak_val = dates[mpeak_i], vals[mpeak_i]
        fired = (pz is not None and pz >= args.z_threshold)
        pz_disp = "inf" if (pz is not None and pz >= 1e8) else (round(pz, 2) if pz is not None else "-")
        print(f"{tk:<7}{str(pz_disp):>11}{str(pdate or '-'):>13}{int(mpeak_val):>14}"
              f"{mpeak_date:>13}{'OUI' if fired else 'non':>7}{n:>5}")
        rows.append({"ticker": tk, "peak_z": pz_disp, "peak_z_date": pdate,
                     "peak_mentions": int(mpeak_val), "peak_mentions_date": mpeak_date,
                     "fired": "OUI" if fired else "non", "n_eval": n})

    print(f"\nSeuil Z = {args.z_threshold} (Z>seuil = anomalie de volume, condition d'entrée C1).")
    print("Étape suivante : comparer 'Date pic Z' / 'Date pic' au pic de PRIX (c3_aligned.csv).")
    print("Pic social AVANT pic prix = antériorité = signal Q1/Q2. Après = Q3/Q4 (à filtrer).")

    if args.csv and rows:
        cols = ["ticker", "peak_z", "peak_z_date", "peak_mentions",
                "peak_mentions_date", "fired", "n_eval", "note", "n_days"]
        with open(args.csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for r in rows:
                w.writerow({c: r.get(c, "") for c in cols})
        print(f"-> {args.csv}")


if __name__ == "__main__":
    main()
