#!/usr/bin/env python3
"""
SCCT-Social · Extraction historique Adanos pour le BACKTEST (P2/P4)
===================================================================
À lancer dès qu'Adanos Pro est actif (365 j). Tire la série quotidienne de
mentions / sentiment / buzz pour l'échantillon des 4 familles sur la fenêtre du
backtest, et écrit un CSV « tidy » (une ligne = un ticker × un jour) prêt à :
  - calculer C1 (volume spike), C2 (vélocité/burstiness), C3 (lead-lag vs prix),
  - s'aligner avec les prix TwelveData et le newsflow SEC sur la même timeline.

⚠ Fenêtre glissante : Pro remonte à 365 j AVANT aujourd'hui. Extraire tôt — les
données de mi-2025 sortent de portée un peu plus chaque jour.

Endpoint : GET https://api.adanos.org/reddit/stocks/v1/stock/{ticker}?from&to
           -> daily_trend[] = {date, mentions, sentiment_score, buzz_score}
Auth : X-API-Key.

Usage :
    export ADANOS_API_KEY=sk_live_xxx
    python adanos_backtest_pull.py --from 2025-07-01 --to 2026-02-28
    python adanos_backtest_pull.py --tickers OCC OPEN KSS DNUT --out backtest_social.csv

Stocke aussi dans la table `mentions` (source='adanos_hist') si --db est passé,
pour réutiliser compute_signals.py sur l'historique.
"""
from __future__ import annotations
import argparse, csv, datetime as dt, os, sys, time

try:
    import requests
except ImportError:
    sys.exit("pip install requests")

ADANOS_KEY = os.getenv("ADANOS_API_KEY", "")
ADANOS_BASE = os.getenv("ADANOS_BASE", "https://api.adanos.org/reddit/stocks")
UA = {"User-Agent": "SCCT-Social backtest pull"}

# Échantillon par défaut : familles 1 (vrais positifs) + 2 (hybride OCC).
# Ajoute tes noms famille 3/4 dérivés des données.
DEFAULT_TICKERS = ["OCC", "OPEN", "KSS", "DNUT", "AMC", "GME"]

# Adanos /v1/stock accepte une fenêtre ; on découpe en tranches pour rester
# sous d'éventuels plafonds de période et limiter chaque réponse.
CHUNK_DAYS = 60


def _headers():
    return {"X-API-Key": ADANOS_KEY, **UA}


def _date(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def chunks(dfrom: dt.date, dto: dt.date, days: int):
    cur = dfrom
    while cur <= dto:
        end = min(cur + dt.timedelta(days=days - 1), dto)
        yield cur, end
        cur = end + dt.timedelta(days=1)


def pull_ticker(tk: str, dfrom: dt.date, dto: dt.date) -> list[dict]:
    rows: dict[str, dict] = {}  # date -> row (dédoublonnage entre tranches)
    for a, b in chunks(dfrom, dto, CHUNK_DAYS):
        try:
            r = requests.get(f"{ADANOS_BASE}/v1/stock/{tk}",
                             params={"from": a.isoformat(), "to": b.isoformat()},
                             headers=_headers(), timeout=30)
        except Exception as e:
            print(f"  [{tk}] {a}->{b} err {e}", file=sys.stderr); continue
        if r.status_code == 404:
            continue  # pas de mention sur cette tranche
        if r.status_code == 422:
            print(f"  [{tk}] {a} hors plan (besoin Pro 365j)", file=sys.stderr); continue
        if r.status_code == 429:
            print(f"  [{tk}] quota atteint, pause 5s", file=sys.stderr); time.sleep(5); continue
        if r.status_code != 200:
            print(f"  [{tk}] HTTP {r.status_code}", file=sys.stderr); continue
        js = r.json()
        for d in (js.get("daily_trend") or []):
            date = d.get("date")
            if not date:
                continue
            rows[date] = {
                "ticker": tk.upper(), "date": date,
                "mentions": d.get("mentions"),
                "sentiment_score": d.get("sentiment_score"),
                "buzz_score": d.get("buzz_score"),
            }
        time.sleep(0.3)  # courtoisie rate limit
    return [rows[k] for k in sorted(rows)]


def main():
    ap = argparse.ArgumentParser()
    today = dt.date.today()
    ap.add_argument("--from", dest="dfrom", default=(today - dt.timedelta(days=360)).isoformat())
    ap.add_argument("--to", dest="dto", default=today.isoformat())
    ap.add_argument("--tickers", nargs="+", default=DEFAULT_TICKERS)
    ap.add_argument("--out", default="backtest_social.csv")
    ap.add_argument("--db", action="store_true", help="écrit aussi dans la table mentions (source=adanos_hist)")
    args = ap.parse_args()

    if not ADANOS_KEY:
        sys.exit("ADANOS_API_KEY absent. export ADANOS_API_KEY=sk_live_... puis relance.")

    dfrom, dto = _date(args.dfrom), _date(args.dto)
    if (today - dfrom).days > 366:
        print(f"⚠ {args.dfrom} est au-delà de 365 j -> Adanos Pro ne le couvre pas.", file=sys.stderr)
    print(f"Extraction Adanos {dfrom} -> {dto} pour : {', '.join(args.tickers)}\n")

    all_rows = []
    for tk in args.tickers:
        rows = pull_ticker(tk, dfrom, dto)
        days = len(rows)
        total = sum((r["mentions"] or 0) for r in rows)
        peak = max((r["mentions"] or 0) for r in rows) if rows else 0
        earliest = rows[0]["date"] if rows else "-"
        print(f"{tk:<6} {days:>4} jours · {total:>6} mentions cumulées · pic/j {peak:>5} · depuis {earliest}")
        all_rows.extend(rows)

    if not all_rows:
        print("\nAucune donnée. Vérifie le plan (Pro requis pour mi-2025) et la fenêtre.")
        return

    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["ticker", "date", "mentions",
                                          "sentiment_score", "buzz_score"])
        w.writeheader(); w.writerows(all_rows)
    print(f"\n{len(all_rows)} lignes -> {args.out}")

    if args.db:
        try:
            from storage import Store
            store = Store()
            recs = []
            for r in all_rows:
                ts = dt.datetime.fromisoformat(r["date"]).replace(
                    tzinfo=dt.timezone.utc).timestamp()
                recs.append({"source": "adanos_hist", "ticker": r["ticker"],
                             "fetched_utc": ts, "rank": None, "mentions": r["mentions"],
                             "mentions_prev": None, "upvotes": None,
                             "sentiment": r["sentiment_score"], "name": None,
                             "extra": {"buzz_score": r["buzz_score"]}})
            n = store.upsert_mentions(recs)
            print(f"DB: {n} lignes insérées (source=adanos_hist). "
                  f"compute_signals.py --source adanos_hist pour le Z-score C1.")
        except Exception as e:
            print(f"DB: écriture ignorée ({e})")

    print("\nProchaine étape : aligner ce CSV avec les prix TwelveData (C3 lead-lag) "
          "et le newsflow SEC (C5) sur la même timeline.")


if __name__ == "__main__":
    main()
