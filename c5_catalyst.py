#!/usr/bin/env python3
"""
SCCT-Social · C5 — Catalyseur daté (SEC EDGAR, gratuit)
=======================================================
C5 place l'AXE 2 (ancrage fondamental) : existe-t-il un catalyseur réel et daté
(8-K matériel, 6-K pour émetteurs étrangers, earnings) autour du mouvement ?
  - catalyseur présent + social antécédent -> Q2 (convergence, signal idéal)
  - pas de catalyseur + social fort         -> Q1 (pump pur)
  - catalyseur présent + social suit        -> Q4 (rerating fondamental, hors SCCT)
Explique aussi les « faux négatifs » du backtest : un mover SANS social mais AVEC
catalyseur est un mouvement fondamental que SCCT doit ignorer.

Source : SEC EDGAR (gratuit, pas de clé ; User-Agent identifiant requis).
  ticker -> CIK   : https://www.sec.gov/files/company_tickers.json
  filings         : https://data.sec.gov/submissions/CIK{cik}.json

Ancre d'événement : date du pic de mentions (c1_backtest.csv, col peak_mentions_date)
sinon milieu de fenêtre. Catalyseur recherché dans [ancre - PRE, ancre + POST] jours.

Sortie : c5_catalyst.csv (ticker, C5, catalyst_type, catalyst_date, n_8k, earnings).

Config : export SEC_USER_AGENT="Nom prenom email" (recommandé par la SEC).

Usage :
    python c5_catalyst.py --families families.csv --c1 c1_backtest.csv
    python c5_catalyst.py --tickers OPEN OCC GME --from 2025-07-01 --to 2026-02-28
"""
from __future__ import annotations
import argparse, csv, datetime as dt, os, sys, time

try:
    import requests
except ImportError:
    sys.exit("pip install requests")

SEC_UA = os.getenv("SEC_USER_AGENT", "SCCT-Social research contact@example.com")
H = {"User-Agent": SEC_UA, "Accept-Encoding": "gzip, deflate"}
CATALYST_FORMS = ("8-K", "6-K")           # événements matériels
EARNINGS_FORMS = ("10-Q", "10-K", "20-F")  # earnings/annuels
# Items 8-K MATÉRIELS (catalyseur réel) ; on exclut les dépôts purement
# administratifs (9.01 exhibits seuls). Réf. codes SEC 8-K.
MATERIAL_ITEMS = {"1.01", "1.02", "1.03", "2.01", "2.02", "2.03", "3.01",
                  "3.02", "5.01", "5.02", "7.01", "8.01"}

_CIK = {}


def load_cik():
    if _CIK:
        return
    try:
        r = requests.get("https://www.sec.gov/files/company_tickers.json", headers=H, timeout=30)
        for v in r.json().values():
            _CIK[v["ticker"].upper()] = str(v["cik_str"]).zfill(10)
    except Exception as e:
        print(f"  CIK map err: {e}", file=sys.stderr)


def filings(cik):
    try:
        r = requests.get(f"https://data.sec.gov/submissions/CIK{cik}.json", headers=H, timeout=30)
        rec = r.json().get("filings", {}).get("recent", {})
        forms = rec.get("form", []); dates = rec.get("filingDate", [])
        items = rec.get("items", [""] * len(forms))  # codes d'item pour les 8-K
        return list(zip(forms, dates, items))
    except Exception as e:
        print(f"  filings err {cik}: {e}", file=sys.stderr)
        return []


def is_material_8k(items_str):
    """8-K matériel si au moins un item hors administratif (ex. 9.01 seul = non)."""
    codes = {c.strip() for c in (items_str or "").split(",") if c.strip()}
    return bool(codes & MATERIAL_ITEMS)


def analyze(tk, anchor, pre, post, win_lo, win_hi):
    res = {"ticker": tk, "C5": 0, "catalyst_type": "", "catalyst_date": "",
           "n_8k": 0, "earnings_near": "", "note": ""}
    cik = _CIK.get(tk.upper())
    if not cik:
        res["note"] = "CIK introuvable (étranger/délisté ?)"; return res
    fl = filings(cik)
    if not fl:
        res["note"] = "pas de filings"; return res
    if anchor:
        lo, hi = anchor - dt.timedelta(days=pre), anchor + dt.timedelta(days=post)
    else:
        lo, hi = win_lo, win_hi
    cat_hit, earn_hit = None, None
    for form, d, items in fl:
        try:
            fd = dt.date.fromisoformat(d)
        except ValueError:
            continue
        if not (lo <= fd <= hi):
            continue
        if form.startswith(CATALYST_FORMS):
            # 8-K : ne compter que les matériels ; 6-K (étranger) : on garde
            if form.startswith("8-K") and not is_material_8k(items):
                continue
            res["n_8k"] += 1
            if cat_hit is None or fd < cat_hit[1]:
                cat_hit = (form, fd)
        if form.startswith(EARNINGS_FORMS) and earn_hit is None:
            earn_hit = (form, fd)
    if cat_hit:
        res["C5"] = 1
        res["catalyst_type"] = cat_hit[0]
        res["catalyst_date"] = cat_hit[1].isoformat()
    if earn_hit:
        res["earnings_near"] = f"{earn_hit[0]} {earn_hit[1]}"
        if not cat_hit:
            res["C5"] = 1  # earnings = catalyseur daté aussi
            res["catalyst_type"] = earn_hit[0]
            res["catalyst_date"] = earn_hit[1].isoformat()
    res["note"] = "catalyseur" if res["C5"] else "aucun catalyseur daté dans la fenêtre"
    return res


def load_anchors(path):
    anchors = {}
    if path and os.path.exists(path):
        with open(path) as f:
            for r in csv.DictReader(f):
                d = r.get("peak_mentions_date") or r.get("peak_z_date")
                try:
                    anchors[r["ticker"].upper()] = dt.date.fromisoformat(d)
                except (ValueError, TypeError, KeyError):
                    pass
    return anchors


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--families", default="families.csv")
    ap.add_argument("--tickers", nargs="*", default=[])
    ap.add_argument("--c1", default="c1_backtest.csv", help="pour l'ancre (peak_mentions_date)")
    ap.add_argument("--from", dest="dfrom", default="2025-07-01")
    ap.add_argument("--to", dest="dto", default="2026-02-28")
    ap.add_argument("--pre", type=int, default=21, help="jours avant l'ancre")
    ap.add_argument("--post", type=int, default=7, help="jours après l'ancre")
    ap.add_argument("--out", default="c5_catalyst.csv")
    args = ap.parse_args()

    tickers = [t.upper() for t in args.tickers]
    if not tickers and os.path.exists(args.families):
        with open(args.families) as f:
            tickers = [r["ticker"].upper() for r in csv.DictReader(f) if r.get("ticker")]
    if not tickers:
        sys.exit("Aucun ticker. --families families.csv ou --tickers T1 T2 …")

    load_cik()
    anchors = load_anchors(args.c1)
    win_lo, win_hi = dt.date.fromisoformat(args.dfrom), dt.date.fromisoformat(args.dto)
    print(f"C5 — catalyseur daté (SEC) · ancre = pic mentions ± [{args.pre}j,{args.post}j]\n")
    hdr = f"{'Ticker':<8}{'C5':>4}{'Type':>8}{'Date':>13}{'n8K':>5}  Earnings/Note"
    print(hdr); print("-" * (len(hdr) + 8))
    rows = []
    for tk in tickers:
        r = analyze(tk, anchors.get(tk), args.pre, args.post, win_lo, win_hi)
        extra = r["earnings_near"] or r["note"]
        print(f"{tk:<8}{r['C5']:>4}{r['catalyst_type']:>8}{r['catalyst_date']:>13}{r['n_8k']:>5}  {extra}")
        rows.append(r)
        time.sleep(0.15)  # SEC courtoisie (<10 req/s)

    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["ticker", "C5", "catalyst_type",
                                          "catalyst_date", "n_8k", "earnings_near", "note"])
        w.writeheader(); w.writerows(rows)
    n_cat = sum(r["C5"] for r in rows)
    print(f"\n-> {args.out} · {n_cat}/{len(rows)} avec catalyseur daté")
    print("C5=1 + social antécédent => Q2 (convergence). C5=0 + social fort => Q1 (pump pur).")
    print("C5=1 sans social (faux négatif du backtest) => Q4 rerating, que SCCT ignore à raison.")


if __name__ == "__main__":
    main()
