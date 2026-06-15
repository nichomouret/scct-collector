#!/usr/bin/env python3
"""
SCCT-Social · C3 INTRADAY — lead-lag horaire (résout la synchronicité journalière)
==================================================================================
Le backtest journalier a montré que mentions et prix sont synchrones (lag 0). À
l'heure, on peut voir si les mentions PRÉCÈDENT le prix de quelques heures.

Entrées :
  - raw_mentions.csv (created_utc) -> mentions agrégées par HEURE.
  - TwelveData time_series interval=1h sur la même fenêtre.

Par ticker : corrélation croisée Δmentions(h) vs rendement(h+lag), lag en HEURES.
  lag > 0 = mentions précèdent le prix (antériorité intraday) -> C3_intraday élevé.

⚠ Profondeur intraday TwelveData : le tier GRATUIT ne remonte en général que
quelques jours/semaines en horaire. Sur la fenêtre 2025, l'API renverra
probablement peu/pas de barres -> le script le DÉTECTE et le signale. Dans ce cas
l'intraday est surtout exploitable en FORWARD-TEST (collecte temps réel), pas en
rétrospectif. Repli proposé : intraday mentions vs prix QUOTIDIEN (le burst
intraday précède-t-il le saut journalier du lendemain ?).

Stdlib uniquement.

Usage :
    export TWELVEDATA_API_KEY=xxx
    python c3_intraday.py --raw raw_mentions.csv --out c3_intraday.csv
    python c3_intraday.py --max-lag-hours 12
"""
from __future__ import annotations
import argparse, csv, datetime as dt, math, os, sys, time
from collections import defaultdict

try:
    import requests
except ImportError:
    sys.exit("pip install requests")

TD_KEY = os.getenv("TWELVEDATA_API_KEY", "")
UA = {"User-Agent": "SCCT-Social C3 intraday"}


def parse_ts(s):
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


def read_hourly_mentions(path):
    """ticker -> {heure_iso 'YYYY-MM-DD HH:00': count}"""
    series = defaultdict(lambda: defaultdict(int))
    rng = defaultdict(lambda: [None, None])
    with open(path) as f:
        for r in csv.DictReader(f):
            t = parse_ts(r.get("created_utc"))
            if not t:
                continue
            tk = r["ticker"].upper()
            h = t.astimezone(dt.timezone.utc).replace(minute=0, second=0, microsecond=0)
            key = h.strftime("%Y-%m-%d %H:00")
            series[tk][key] += 1
            lo, hi = rng[tk]
            rng[tk] = [min(lo, h) if lo else h, max(hi, h) if hi else h]
    return series, rng


def fetch_intraday(tk, start, end):
    """TwelveData 1h -> {heure 'YYYY-MM-DD HH:00': close}. Vide si indispo."""
    if not TD_KEY or TD_KEY == "xxx":
        print("  TWELVEDATA_API_KEY manquant/placeholder.", file=sys.stderr)
        return {}, "clé absente"
    try:
        r = requests.get("https://api.twelvedata.com/time_series", params={
            "symbol": tk, "interval": "1h", "start_date": start, "end_date": end,
            "apikey": TD_KEY, "order": "ASC", "outputsize": 5000}, headers=UA, timeout=30)
        js = r.json()
        if isinstance(js, dict) and js.get("status") == "error":
            return {}, js.get("message", "error")
        vals = js.get("values", []) if isinstance(js, dict) else []
        out = {}
        for v in vals:
            # datetime TwelveData '2025-07-15 14:30:00' -> bucket heure
            t = parse_ts(v["datetime"].replace(" ", "T")) or None
            if t:
                key = t.replace(minute=0, second=0).strftime("%Y-%m-%d %H:00")
                out[key] = float(v["close"])
        return out, ("ok" if out else "0 barre")
    except Exception as e:
        return {}, f"err {e}"


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs)); dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return num / (dx * dy) if dx > 0 and dy > 0 else 0.0


def analyze(mh, ph, maxlag):
    common = sorted(set(mh) & set(ph))
    if len(common) < 6:
        return None, len(common)
    m = [mh[h] for h in common]
    c = [ph[h] for h in common]
    dm = [0.0] + [m[i] - m[i-1] for i in range(1, len(m))]
    rt = [0.0] + [(c[i]-c[i-1])/c[i-1] if c[i-1] else 0.0 for i in range(1, len(c))]
    best_lag, best_corr = 0, 0.0
    for lag in range(-maxlag, maxlag + 1):
        xs, ys = [], []
        for t in range(len(common)):
            tt = t + lag
            if 0 <= tt < len(common):
                xs.append(dm[t]); ys.append(rt[tt])
        cc = pearson(xs, ys)
        if abs(cc) > abs(best_corr):
            best_corr, best_lag = cc, lag
    return (best_lag, round(best_corr, 3)), len(common)


def c3_intraday_score(best_lag, corr):
    if abs(corr) < 0.2:
        return 0.5
    return round(max(0.0, min(1.0, 0.5 + 0.08 * best_lag)), 3)  # +6h -> ~1 ; -6h -> ~0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="raw_mentions.csv")
    ap.add_argument("--out", default="c3_intraday.csv")
    ap.add_argument("--max-lag-hours", type=int, default=8)
    args = ap.parse_args()

    if not os.path.exists(args.raw):
        sys.exit(f"{args.raw} introuvable. Lance adanos_raw_pull.py d'abord.")
    if not TD_KEY or TD_KEY == "xxx":
        sys.exit("TWELVEDATA_API_KEY manquant. export TWELVEDATA_API_KEY=<clé>.")

    mentions, rng = read_hourly_mentions(args.raw)
    print(f"{len(mentions)} tickers · mentions agrégées à l'heure\n")
    hdr = f"{'Ticker':<7}{'h.communes':>11}{'lag(h)':>8}{'corr':>7}{'C3_intra':>10}  Note"
    print(hdr); print("-" * (len(hdr) + 6))
    rows = []
    no_price = 0
    for tk in sorted(mentions):
        lo, hi = rng[tk]
        ph, status = fetch_intraday(tk, lo.strftime("%Y-%m-%d"), (hi + dt.timedelta(days=1)).strftime("%Y-%m-%d"))
        if not ph:
            no_price += 1
            print(f"{tk:<7}{'-':>11}{'-':>8}{'-':>7}{'-':>10}  prix intraday indispo ({status})")
            rows.append({"ticker": tk, "hours_common": 0, "lag_hours": "", "corr": "",
                         "C3_intraday": "", "note": f"prix intraday indispo ({status})"})
            time.sleep(8); continue
        res, n = analyze(mentions[tk], ph, args.max_lag_hours)
        if res is None:
            print(f"{tk:<7}{n:>11}{'-':>8}{'-':>7}{'-':>10}  trop peu d'heures communes")
            rows.append({"ticker": tk, "hours_common": n, "lag_hours": "", "corr": "",
                         "C3_intraday": "", "note": "trop peu d'heures communes"})
        else:
            lag, corr = res
            sc = c3_intraday_score(lag, corr)
            note = "mentions précèdent" if lag > 0 else "prix précède" if lag < 0 else "synchrone"
            print(f"{tk:<7}{n:>11}{lag:>8}{corr:>7}{sc:>10}  {note}")
            rows.append({"ticker": tk, "hours_common": n, "lag_hours": lag, "corr": corr,
                         "C3_intraday": sc, "note": note})
        time.sleep(8)

    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["ticker", "hours_common", "lag_hours",
                                          "corr", "C3_intraday", "note"])
        w.writeheader(); w.writerows(rows)
    print(f"\n-> {args.out}")
    if no_price:
        print(f"⚠ {no_price} tickers sans prix intraday : le tier TwelveData gratuit ne remonte "
              "pas l'historique horaire de 2025. L'intraday est alors un outil de FORWARD-TEST "
              "(collecte temps réel), ou nécessite un historique intraday payant.")
    print("lag(h) > 0 = mentions précèdent le prix À L'HEURE -> vraie antériorité (C3 intraday).")


if __name__ == "__main__":
    main()
