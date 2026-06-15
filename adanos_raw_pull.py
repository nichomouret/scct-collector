#!/usr/bin/env python3
"""
SCCT-Social · Extraction des mentions BRUTES HORODATÉES (Adanos Pro)
===================================================================
Le backtest journalier a montré que C3 (antériorité) est SYNCHRONE au pas
quotidien -> il faut de l'INTRADAY. L'endpoint Pro /v1/stock/{ticker}/mentions
renvoie chaque mention avec son created_utc -> on peut reconstruire la
distribution horaire et refaire le lead-lag à l'heure.

À lancer pendant que Pro est actif (jusqu'au 12/07). Endpoint Pro-only, 365j.

Limite connue : `limit` max 100 lignes/appel et pas d'offset -> on requête
JOUR PAR JOUR. Sur un jour à >100 mentions, on n'a que les 100 plus récentes
(biais fin de journée) ; le script SIGNALE ces jours « tronqués ». Les jours de
RAMPE (avant le pic, volume < 100) sont justement les plus utiles pour
l'antériorité, et y sont complets.

Endpoint : GET /v1/stock/{ticker}/mentions?from&to&limit&include_inherited
Auth : X-API-Key.

Usage :
    export ADANOS_API_KEY=sk_live_xxx
    python adanos_raw_pull.py --tickers OPEN KSS DNUT GME --from 2025-07-01 --to 2025-10-31
    python adanos_raw_pull.py --out raw_mentions.csv
"""
from __future__ import annotations
import argparse, csv, datetime as dt, os, sys, time

try:
    import requests
except ImportError:
    sys.exit("pip install requests")

ADANOS_KEY = os.getenv("ADANOS_API_KEY", "")
ADANOS_BASE = os.getenv("ADANOS_BASE", "https://api.adanos.org/reddit/stocks")
UA = {"User-Agent": "SCCT-Social raw pull"}
DEFAULT_TICKERS = ["OPEN", "KSS", "DNUT", "GME"]


def _headers():
    return {"X-API-Key": ADANOS_KEY, **UA}


def pull_day(tk: str, day: str):
    """Renvoie (rows, count_total). count_total > len(rows) => jour tronqué (>100)."""
    try:
        r = requests.get(f"{ADANOS_BASE}/v1/stock/{tk}/mentions",
                         params={"from": day, "to": day, "limit": 100,
                                 "include_inherited": "false"},
                         headers=_headers(), timeout=30)
        if r.status_code == 403:
            print("  403 — endpoint raw réservé au plan Professional.", file=sys.stderr)
            return None, None
        if r.status_code == 404:
            return [], 0
        if r.status_code == 401:
            print("  401 — clé invalide.", file=sys.stderr); return None, None
        if r.status_code == 429:
            time.sleep(3); return [], 0
        if r.status_code != 200:
            print(f"  [{tk}] {day} HTTP {r.status_code}", file=sys.stderr); return [], 0
        js = r.json()
        return js.get("results", []), js.get("count", 0)
    except Exception as e:
        print(f"  [{tk}] {day} err {e}", file=sys.stderr)
        return [], 0


def daterange(a: dt.date, b: dt.date):
    cur = a
    while cur <= b:
        yield cur
        cur += dt.timedelta(days=1)


def main():
    ap = argparse.ArgumentParser()
    today = dt.date.today()
    ap.add_argument("--tickers", nargs="+", default=DEFAULT_TICKERS)
    ap.add_argument("--from", dest="dfrom", default=(today - dt.timedelta(days=360)).isoformat())
    ap.add_argument("--to", dest="dto", default=today.isoformat())
    ap.add_argument("--out", default="raw_mentions.csv")
    ap.add_argument("--c1", default="", help="c1_backtest.csv -> pull SEULEMENT la fenêtre d'événement par ticker (rapide)")
    ap.add_argument("--pre", type=int, default=10, help="jours avant l'ignition (mode fenêtre)")
    ap.add_argument("--post", type=int, default=10, help="jours après l'ignition (mode fenêtre)")
    args = ap.parse_args()

    if not ADANOS_KEY:
        sys.exit("ADANOS_API_KEY absent. export ADANOS_API_KEY=sk_live_... puis relance.")

    dfrom, dto = dt.date.fromisoformat(args.dfrom), dt.date.fromisoformat(args.dto)
    # Mode fenêtre : ancres = pic de mentions par ticker (c1_backtest.csv)
    anchors = {}
    if args.c1 and os.path.exists(args.c1):
        with open(args.c1) as f:
            for r in csv.DictReader(f):
                d = r.get("peak_mentions_date") or r.get("peak_z_date")
                try:
                    anchors[r["ticker"].upper()] = dt.date.fromisoformat(d)
                except (ValueError, TypeError, KeyError):
                    pass
    if anchors:
        print(f"Mode FENÊTRE d'événement ±[{args.pre}j,{args.post}j] ({len(anchors)} ancres) "
              f"— pull ciblé, rapide.")
    else:
        print(f"Mentions brutes {dfrom} -> {dto} (plage complète, lent).")
    print("(jour par jour ; les jours tronqués à 100 sont signalés)\n")

    all_rows = []
    cols = ["ticker", "created_utc", "subreddit", "author", "upvotes",
            "sentiment_score", "sentiment_label", "text_snippet",
            "post_id", "comment_id", "is_inherited"]
    for tk in args.tickers:
        got, capped_days, total_count = 0, 0, 0
        a = anchors.get(tk.upper())
        if a:
            days = list(daterange(a - dt.timedelta(days=args.pre), a + dt.timedelta(days=args.post)))
        else:
            days = list(daterange(dfrom, dto))
        for day in days:
            rows, count = pull_day(tk, day.isoformat())
            if rows is None:        # erreur fatale (403/401)
                return
            total_count += count or 0
            if count and count > len(rows):
                capped_days += 1
            for m in rows:
                all_rows.append({
                    "ticker": tk.upper(),
                    "created_utc": m.get("created_utc"),
                    "subreddit": m.get("subreddit"),
                    "author": m.get("author"),
                    "upvotes": m.get("upvotes"),
                    "sentiment_score": m.get("sentiment_score"),
                    "sentiment_label": m.get("sentiment_label"),
                    "text_snippet": (m.get("text_snippet") or "").replace("\n", " ")[:300],
                    "post_id": m.get("post_id"),
                    "comment_id": m.get("comment_id"),
                    "is_inherited": m.get("is_inherited"),
                })
                got += 1
            time.sleep(0.1)         # Pro = 1000/min, large marge
        flag = f" · {capped_days} jours tronqués (>100)" if capped_days else ""
        print(f"{tk:<6} {got:>6} lignes brutes récupérées · {total_count} mentions totales déclarées{flag}")

    if not all_rows:
        print("\nAucune ligne. Vérifie le plan (raw = Pro) et la fenêtre.")
        return
    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(all_rows)
    print(f"\n{len(all_rows)} lignes -> {args.out}")
    print("Prochaine étape : agréger par heure (created_utc) et refaire le lead-lag "
          "C3 vs prix intraday TwelveData. Les jours non tronqués donnent la vraie "
          "distribution horaire de l'ignition.")


if __name__ == "__main__":
    main()
