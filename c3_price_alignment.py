#!/usr/bin/env python3
"""
SCCT-Social · C3 — Antériorité sociale (lead-lag), CENTRÉ SUR L'ÉVÉNEMENT
========================================================================
Mesure si le SOCIAL PRÉCÈDE le PRIX *autour de l'ignition*, pas sur toute la
série (un « pic de prix » pris sur 8 mois est du bruit — d'où la v1 cassée).

Méthode :
  1. Ancre = date d'IGNITION sociale = pic du Z-score glissant des mentions
     (cohérent avec c1_backtest.py).
  2. Fenêtre d'événement bornée autour de l'ancre : [ancre - PRE, ancre + POST] jours.
  3. Dans cette fenêtre :
     - fwd_ret_Kd  = rendement du prix de l'ancre à +K jours (le mouvement A-T-IL suivi ?)
     - price_peak  = date du plus haut cours DANS la fenêtre ; lead_days = price_peak - ancre
                     (borné -> plus de « lead » de 200 jours)
     - best_lag/corr = corrélation Δmentions vs rendement, lags ±L, DANS la fenêtre
  4. C3_score [0,1] : social précède (lead_days >= 0) ET le prix a monté après l'ancre.

Stdlib uniquement.

Usage :
    export TWELVEDATA_API_KEY=xxx
    python c3_price_alignment.py --social backtest_social.csv --out c3_aligned.csv
    python c3_price_alignment.py --pre 10 --post 21 --z-window 14
"""
from __future__ import annotations
import argparse, csv, datetime as dt, math, os, statistics, sys, time
from collections import defaultdict

try:
    import requests
except ImportError:
    sys.exit("pip install requests")

TD_KEY = os.getenv("TWELVEDATA_API_KEY", "")
UA = {"User-Agent": "SCCT-Social C3"}


def read_social(path: str):
    s = defaultdict(dict)
    with open(path) as f:
        for row in csv.DictReader(f):
            try:
                s[row["ticker"].upper()][row["date"]] = float(row["mentions"] or 0)
            except ValueError:
                pass
    return s


def fetch_prices(tk: str, dfrom: str, dto: str) -> dict:
    if not TD_KEY or TD_KEY == "xxx":
        print("  TWELVEDATA_API_KEY manquant/placeholder. Clé gratuite sur twelvedata.com.",
              file=sys.stderr)
        return {}
    try:
        r = requests.get("https://api.twelvedata.com/time_series", params={
            "symbol": tk, "interval": "1day", "start_date": dfrom, "end_date": dto,
            "apikey": TD_KEY, "order": "ASC", "outputsize": 5000}, headers=UA, timeout=30)
        js = r.json()
        if isinstance(js, dict) and js.get("status") == "error":
            print(f"  [{tk}] TwelveData refus : {js.get('message')}", file=sys.stderr)
            return {}
        vals = js.get("values", []) if isinstance(js, dict) else []
        return {v["datetime"]: float(v["close"]) for v in vals}
    except Exception as e:
        print(f"  [{tk}] TwelveData err {e}", file=sys.stderr)
        return {}


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs)); dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return num / (dx * dy) if dx > 0 and dy > 0 else 0.0


def ignition_date(dates, vals, zwin):
    """Ancre = pic de mentions BRUT (robuste). Le Z glissant dérape sur séries
    longtemps élevées (baseline qui se renormalise) -> on prend le maximum brut."""
    if not vals:
        return None
    return dates[max(range(len(vals)), key=lambda i: vals[i])]


def c3_from_signal(moved, best_lag, best_corr):
    """C3 piloté par le LAG de corrélation croisée (mesure principale d'antériorité).
    best_lag > 0 = Δmentions précèdent le rendement = antériorité.
    Gated par 'moved' (sans mouvement -> Q3, timing non probant)."""
    if not moved:
        return 0.1
    if abs(best_corr) < 0.2:           # corrélation trop faible -> lead non concluant
        return 0.5
    return round(max(0.0, min(1.0, 0.5 + 0.1 * best_lag)), 3)  # +5 -> 1.0 ; -5 -> 0.0


def nearest_on_or_after(common, anchor):
    later = [d for d in common if d >= anchor]
    return later[0] if later else (common[-1] if common else None)


def analyze(tk, mention_map, price_map, pre, post, zwin, move_thr, maxlag):
    common = sorted(set(mention_map) & set(price_map))
    if len(common) < 6:
        return {"ticker": tk, "n_days": len(common), "note": "trop peu de jours communs"}
    dates = sorted(mention_map)
    vals = [mention_map[d] for d in dates]
    anchor = ignition_date(dates, vals, zwin)
    a = dt.date.fromisoformat(anchor)
    lo, hi = a - dt.timedelta(days=pre), a + dt.timedelta(days=post)
    ev = [d for d in common if lo <= dt.date.fromisoformat(d) <= hi]
    if len(ev) < 4:
        return {"ticker": tk, "n_days": len(common), "anchor": anchor,
                "note": "fenêtre événement trop peu de jours prix"}
    closes = [price_map[d] for d in ev]
    # MOUVEMENT = montée sur la fenêtre (drawup), pas « depuis l'ancre » :
    # base = plus bas cours avant le pic de prix ; sommet = plus haut de la fenêtre.
    p_peak_i = max(range(len(closes)), key=lambda i: closes[i])
    p_peak = ev[p_peak_i]
    base_close = min(closes[:p_peak_i + 1]) if p_peak_i >= 0 else closes[0]
    run_up = (closes[p_peak_i] - base_close) / base_close if base_close else 0.0
    moved = run_up >= move_thr
    lead_days = (dt.date.fromisoformat(p_peak) - a).days  # pic prix vs pic mentions
    # corrélation Δmentions vs rendement dans la fenêtre (mesure d'antériorité)
    m_ev = [mention_map[d] for d in ev]
    dment = [0.0] + [m_ev[i] - m_ev[i-1] for i in range(1, len(m_ev))]
    rets = [0.0] + [(closes[i]-closes[i-1])/closes[i-1] if closes[i-1] else 0.0
                    for i in range(1, len(closes))]
    best_lag, best_corr = 0, 0.0
    for lag in range(-maxlag, maxlag + 1):
        xs, ys = [], []
        for t in range(len(ev)):
            tt = t + lag
            if 0 <= tt < len(ev):
                xs.append(dment[t]); ys.append(rets[tt])
        c = pearson(xs, ys)
        if abs(c) > abs(best_corr):
            best_corr, best_lag = c, lag
    if not moved:
        note = "pas de mouvement (Q3 ?)"
    elif best_lag > 0:
        note = "social précède (lag+)"
    elif best_lag < 0:
        note = "social suit (lag-)"
    else:
        note = "synchrone"
    return {"ticker": tk, "n_days": len(common), "anchor": anchor,
            "run_up": round(run_up, 3), "moved": "oui" if moved else "non",
            "price_peak": p_peak, "lead_days": lead_days,
            "best_lag": best_lag, "max_corr": round(best_corr, 3),
            "C3_score": c3_from_signal(moved, best_lag, best_corr), "note": note}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--social", default="backtest_social.csv")
    ap.add_argument("--out", default="c3_aligned.csv")
    ap.add_argument("--pre", type=int, default=10, help="jours avant l'ancre")
    ap.add_argument("--post", type=int, default=21, help="jours après l'ancre")
    ap.add_argument("--z-window", type=int, default=14, help="baseline du Z d'ignition")
    ap.add_argument("--move-threshold", type=float, default=0.15, help="rendement = 'mouvement'")
    ap.add_argument("--max-lag", type=int, default=7)
    args = ap.parse_args()

    if not os.path.exists(args.social):
        sys.exit(f"{args.social} introuvable.")
    if not TD_KEY or TD_KEY == "xxx":
        sys.exit("TWELVEDATA_API_KEY manquant. export TWELVEDATA_API_KEY=<clé> puis relance.")

    social = read_social(args.social)
    print(f"{len(social)} tickers · fenêtre [-{args.pre}j, +{args.post}j] autour de l'ignition\n")
    results = []
    hdr = (f"{'Ticker':<7}{'Ignition':>12}{'run_up':>8}{'Move':>6}{'Pic prix':>12}"
           f"{'Lead(j)':>8}{'lag':>5}{'corr':>7}{'C3':>6}  Note")
    print(hdr); print("-" * (len(hdr) + 6))
    for tk in sorted(social):
        ds = sorted(social[tk])
        prices = fetch_prices(tk, ds[0], ds[-1])
        r = analyze(tk, social[tk], prices, args.pre, args.post,
                    args.z_window, args.move_threshold, args.max_lag)
        results.append(r)
        if "lead_days" in r:
            print(f"{tk:<7}{r['anchor']:>12}{r['run_up']:>8}{r['moved']:>6}{r['price_peak']:>12}"
                  f"{r['lead_days']:>8}{r['best_lag']:>5}{r['max_corr']:>7}{str(r['C3_score']):>6}  {r['note']}")
        else:
            print(f"{tk:<7}{'':<12}{r.get('note','')}")
        time.sleep(8)

    cols = ["ticker", "n_days", "anchor", "run_up", "moved", "price_peak",
            "lead_days", "best_lag", "max_corr", "C3_score", "note"]
    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader()
        for r in results:
            w.writerow({c: r.get(c, "") for c in cols})
    print(f"\n-> {args.out}")
    print("Lecture : Move=oui + Lead>=0 + C3 élevé = antériorité (Q1/Q2). "
          "Move=non = buzz sans mouvement (Q3, à filtrer). Lead<0 = social suit (Q4).")
    print("Heuristique de départ — à recalibrer via la matrice de confusion.")


if __name__ == "__main__":
    main()
