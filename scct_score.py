#!/usr/bin/env python3
"""
SCCT-Social · CAPSTONE — score composite + matrice de confusion (fin du P4)
===========================================================================
Assemble les composantes calculées séparément, par ticker, en un score SCCT, et
juge le modèle sur sa capacité à DISTINGUER (matrice de confusion sur l'échantillon
des 4 familles).

  SCCT = 0.15·C1 + 0.35·C2 + 0.25·C3 + 0.15·C4 + 0.10·C5   (poids spec §3.1)
  -> renormalisé sur les composantes DISPONIBLES (C4/C5 souvent absents au début).

Entrées (toutes optionnelles sauf families) :
  --families  families.csv      (ticker, run_up, family)        [obligatoire]
  --c1        c1_backtest.csv    (ticker, peak_z)               -> C1
  --c2        c2_scores.csv      (ticker, C2_score)             -> C2
  --c3        c3_aligned.csv     (ticker, C3_score)             -> C3
  --c4        c4_structure.csv   (ticker, C4)                   -> C4 (optionnel)

Sorties : scores.csv + matrice de confusion + précision/rappel/F1/taux FP.

« Mouvement réel » = run_up >= --move-threshold (familles.csv). « Signal élevé »
= SCCT >= --signal-threshold (0-100).

Stdlib uniquement.

Usage :
    python scct_score.py --signal-threshold 60 --move-threshold 0.30
"""
from __future__ import annotations
import argparse, csv, os, sys

Z_SAT = 5.0  # Z-score de C1 au-delà duquel C1 sature à 1 (Z>3 = anomalie)


def load_csv(path):
    if not path or not os.path.exists(path):
        return {}
    out = {}
    with open(path) as f:
        for r in csv.DictReader(f):
            tk = (r.get("ticker") or "").upper()
            if tk:
                out[tk] = r
    return out


def to_float(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def c1_from_z(row):
    if not row:
        return None
    z = row.get("peak_z")
    if z in ("inf", "Inf", "INF"):
        return 1.0
    zf = to_float(z)
    return None if zf is None else max(0.0, min(1.0, zf / Z_SAT))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--families", default="families.csv")
    ap.add_argument("--c1", default="c1_backtest.csv")
    ap.add_argument("--c2", default="c2_scores.csv")
    ap.add_argument("--c3", default="c3_aligned.csv")
    ap.add_argument("--c4", default="c4_structure.csv")
    ap.add_argument("--c5", default="c5_catalyst.csv")
    ap.add_argument("--signal-threshold", type=float, default=60.0)
    ap.add_argument("--move-threshold", type=float, default=0.30)
    ap.add_argument("--max-fpr", type=float, default=0.10, help="plafond taux faux positifs pour le balayage")
    ap.add_argument("--out", default="scores.csv")
    # poids RECALIBRÉS par le backtest (spec = points de départ ; cf. 01_Scoring) :
    #  C2 = cœur discriminant (↑0.50) ; C3 faible au jour comme à l'heure (↓0.10) ;
    #  C4 = carburant structurel (↑0.25, à brancher) ; C5 hors-score (quadrant, 0).
    #  C1 = seuil d'entrée (gate), peu discriminant entre titres allumés.
    ap.add_argument("--w1", type=float, default=0.15)
    ap.add_argument("--w2", type=float, default=0.50)
    ap.add_argument("--w3", type=float, default=0.10)
    ap.add_argument("--w4", type=float, default=0.25)
    ap.add_argument("--w5", type=float, default=0.00)
    args = ap.parse_args()

    fam = load_csv(args.families)
    if not fam:
        sys.exit(f"{args.families} introuvable/vide. Lance derive_families.py d'abord.")
    c1d, c2d, c3d, c4d, c5d = (load_csv(args.c1), load_csv(args.c2), load_csv(args.c3),
                               load_csv(args.c4), load_csv(args.c5))

    rows = []
    for tk, fr in fam.items():
        run = to_float(fr.get("run_up"))
        if run is None:
            continue  # données incomplètes (ex. délisté)
        c3v = to_float((c3d.get(tk) or {}).get("C3_score"))
        c5v = to_float((c5d.get(tk) or {}).get("C5"))
        comps = {
            "C1": (c1_from_z(c1d.get(tk)), args.w1),
            "C2": (to_float((c2d.get(tk) or {}).get("C2_score")), args.w2),
            "C3": (c3v, args.w3),
            "C4": (to_float((c4d.get(tk) or {}).get("C4")), args.w4),
            "C5": (c5v, args.w5),
        }
        # Axe social C1/C2/C3 : ABSENT = 0 (pas de signal social, c'est une info).
        # Axe structurel C4/C5 : absent = lacune de données -> exclu de la pondération.
        SOCIAL = {"C1", "C2", "C3"}
        used = {}
        for k, (v, w) in comps.items():
            if v is not None:
                used[k] = (v, w)
            elif k in SOCIAL:
                used[k] = (0.0, w)
        wsum = sum(w for _, w in used.values())
        if wsum == 0:
            continue
        score = sum(v * w for v, w in used.values()) / wsum * 100
        avail = used
        moved = run >= args.move_threshold
        signal = score >= args.signal_threshold
        # Quadrant : axe 1 = antériorité sociale (C3>=0.6), axe 2 = ancrage (C5>=0.5)
        ant = c3v is not None and c3v >= 0.6
        anchor = c5v is not None and c5v >= 0.5
        if c3v is None and c5v is None:
            quad = "?"
        elif ant and anchor:
            quad = "Q2 convergence"
        elif ant and not anchor:
            quad = "Q1 pump pur"
        elif (not ant) and anchor:
            quad = "Q4 rerating"
        else:
            quad = "Q3 bruit"
        rows.append({
            "ticker": tk,
            "C1": comps["C1"][0], "C2": comps["C2"][0],
            "C3": comps["C3"][0], "C4": comps["C4"][0], "C5": comps["C5"][0],
            "components_used": "+".join(avail),
            "SCCT_score": round(score, 1),
            "run_up": run, "moved": "oui" if moved else "non",
            "signal_eleve": "oui" if signal else "non",
            "quadrant": quad,
            "family_src": fr.get("family", ""),
        })

    rows.sort(key=lambda r: r["SCCT_score"], reverse=True)
    w = max(len(r["ticker"]) for r in rows) + 1
    print(f"{'Ticker':<{w}}{'C1':>5}{'C2':>6}{'C3':>6}{'C5':>5}{'SCCT':>7}{'Move':>6}{'Signal':>8}  Quadrant")
    print("-" * (w + 52))
    def f(x): return "-" if x is None else f"{x:.2f}"
    for r in rows:
        print(f"{r['ticker']:<{w}}{f(r['C1']):>5}{f(r['C2']):>6}{f(r['C3']):>6}{f(r['C5']):>5}"
              f"{r['SCCT_score']:>7}{r['moved']:>6}{r['signal_eleve']:>8}  {r['quadrant']}")

    # ---- Matrice de confusion : signal élevé × mouvement réel ----
    TP = sum(1 for r in rows if r["signal_eleve"] == "oui" and r["moved"] == "oui")
    FP = sum(1 for r in rows if r["signal_eleve"] == "oui" and r["moved"] == "non")
    FN = sum(1 for r in rows if r["signal_eleve"] == "non" and r["moved"] == "oui")
    TN = sum(1 for r in rows if r["signal_eleve"] == "non" and r["moved"] == "non")
    print(f"\nMatrice de confusion (seuil signal={args.signal_threshold:.0f}, "
          f"mouvement>={args.move_threshold:.0%}) :")
    print(f"                       Move réel    Pas de move")
    print(f"  Signal élevé            {TP:>3} (VP)      {FP:>3} (FP)")
    print(f"  Signal bas              {FN:>3} (FN)      {TN:>3} (VN)")
    prec = TP / (TP + FP) if (TP + FP) else None
    rec = TP / (TP + FN) if (TP + FN) else None
    f1 = (2 * prec * rec / (prec + rec)) if (prec and rec) else None
    fpr = FP / (FP + TN) if (FP + TN) else None
    def pct(x): return "-" if x is None else f"{x:.1%}"
    print(f"\n  Précision {pct(prec)} · Rappel {pct(rec)} · "
          f"F1 {('-' if f1 is None else f'{f1:.2f}')} · Taux faux positifs {pct(fpr)}")
    print("  Objectif : maximiser F1 sous contrainte de taux de faux positifs bas.")
    print("  (Recalibrer poids/seuils ; C4/C5 manquants -> score renormalisé sur C1-C3.)")

    # ---- Balayage de seuils : maximiser F1 SOUS CONTRAINTE taux FP <= max-fpr ----
    print(f"\nBalayage de seuils (max F1 sous contrainte taux FP <= {args.max_fpr:.0%}) :")
    print(f"  {'seuil':>6}{'VP':>4}{'FP':>4}{'FN':>4}{'VN':>4}{'Préc.':>8}{'Rappel':>8}{'F1':>6}{'TauxFP':>8}")
    best = None  # (thr, f1, fpr) parmi ceux respectant la contrainte
    for thr in range(30, 86, 5):
        tp = sum(1 for r in rows if r["SCCT_score"] >= thr and r["moved"] == "oui")
        fp = sum(1 for r in rows if r["SCCT_score"] >= thr and r["moved"] == "non")
        fn = sum(1 for r in rows if r["SCCT_score"] < thr and r["moved"] == "oui")
        tn = sum(1 for r in rows if r["SCCT_score"] < thr and r["moved"] == "non")
        p = tp / (tp + fp) if (tp + fp) else 0
        rc = tp / (tp + fn) if (tp + fn) else 0
        ff = (2 * p * rc / (p + rc)) if (p + rc) else 0
        fr = fp / (fp + tn) if (fp + tn) else 0
        flag = "  <-- meilleur (sous contrainte)" if False else ""
        if fr <= args.max_fpr and (best is None or ff > best[1]):
            best = (thr, ff, fr, rc)
        print(f"  {thr:>6}{tp:>4}{fp:>4}{fn:>4}{tn:>4}{p:>8.0%}{rc:>8.0%}{ff:>6.2f}{fr:>8.0%}")
    if best:
        print(f"  -> meilleur F1 ≈ {best[1]:.2f} au seuil {best[0]} "
              f"(rappel {best[3]:.0%}, taux FP {best[2]:.0%})")
    else:
        print(f"  -> aucun seuil ne respecte taux FP <= {args.max_fpr:.0%}")

    with open(args.out, "w", newline="") as fo:
        cols = ["ticker", "C1", "C2", "C3", "C4", "C5", "components_used", "SCCT_score",
                "run_up", "moved", "signal_eleve", "quadrant", "family_src"]
        wr = csv.DictWriter(fo, fieldnames=cols); wr.writeheader(); wr.writerows(rows)
    print(f"\n-> {args.out}")


if __name__ == "__main__":
    main()
