#!/usr/bin/env python3
"""
SCCT-Social · C2 — Signature de coordination (composante la plus discriminante, 0.35)
=====================================================================================
Calcule les quatre sous-signaux de C2 à partir des mentions BRUTES horodatées
(raw_mentions.csv, sortie d'adanos_raw_pull.py). Aucune donnée de prix requise.

Sous-signaux (spec §5) :
  1. Burstiness        — concentration temporelle : part des mentions du jour-pic
                         dans sa fenêtre 90 min la plus dense + indice d'inter-arrivée.
  2. Concentration des comptes — part des mentions issues des top auteurs
                         (peu d'auteurs très actifs = empreinte non organique).
  3. Synchro cross-sub — nb de subreddits simultanés + dispersion (émergence multi-sources).
  4. Similarité lexicale — formulations quasi-identiques (Jaccard n-grams sur snippets).
                         Proxy stdlib de la cosine-sur-embeddings de la spec.

C2 = moyenne pondérée des 4 sous-scores normalisés [0,1] (poids égaux par défaut,
à recalibrer via la matrice de confusion).

⚠ Jours tronqués : adanos_raw_pull plafonne à 100 lignes/jour (jours à fort volume
incomplets, biais fin de journée). La burstiness est calculée sur le JOUR-PIC ;
si ce jour est tronqué, l'indice est sous-estimé -> signalé (cap=oui).

Stdlib uniquement.

Usage :
    python c2_coordination.py --raw raw_mentions.csv --out c2_scores.csv
    python c2_coordination.py --burst-window 90   # minutes
"""
from __future__ import annotations
import argparse, csv, datetime as dt, math, os, re, sys
from collections import defaultdict, Counter

WORD = re.compile(r"[a-z0-9$]{2,}")
STOP = {"the", "and", "for", "you", "this", "that", "with", "are", "but", "not",
        "all", "out", "now", "its", "have", "will", "just", "they", "from"}


def parse_ts(s):
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


def read_raw(path):
    rows = defaultdict(list)
    with open(path) as f:
        for r in csv.DictReader(f):
            rows[r["ticker"].upper()].append(r)
    return rows


def load_anchors(path):
    """ticker -> date d'ignition (pic de mentions), depuis c1_backtest.csv."""
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


def window_rows(rows, anchor, pre, post):
    """Filtre les lignes dont created_utc tombe dans [ancre-pre, ancre+post] jours."""
    if anchor is None:
        return rows
    lo, hi = anchor - dt.timedelta(days=pre), anchor + dt.timedelta(days=post)
    out = []
    for r in rows:
        t = parse_ts(r.get("created_utc"))
        if t and lo <= t.date() <= hi:
            out.append(r)
    return out


def burstiness(timestamps, window_min):
    """(part max en fenêtre `window_min` sur le jour-pic, indice B, jour-pic, cap_estimé)."""
    if len(timestamps) < 3:
        return 0.0, 0.0, None, False
    by_day = defaultdict(list)
    for t in timestamps:
        by_day[t.date()].append(t)
    peak_day = max(by_day, key=lambda d: len(by_day[d]))
    day_ts = sorted(by_day[peak_day])
    n = len(day_ts)
    # fenêtre glissante de window_min : part max des mentions du jour
    w = dt.timedelta(minutes=window_min)
    best = 0
    j = 0
    for i in range(n):
        while day_ts[i] - day_ts[j] > w:
            j += 1
        best = max(best, i - j + 1)
    frac = best / n
    # indice de burstiness B (Goh-Barabási) sur inter-arrivées globales
    allts = sorted(timestamps)
    gaps = [(allts[i] - allts[i-1]).total_seconds() for i in range(1, len(allts))]
    if len(gaps) >= 2:
        mu = sum(gaps) / len(gaps)
        sd = math.sqrt(sum((g - mu) ** 2 for g in gaps) / len(gaps))
        B = (sd - mu) / (sd + mu) if (sd + mu) > 0 else 0.0
    else:
        B = 0.0
    cap = (n >= 100)  # jour-pic probablement tronqué
    return round(frac, 3), round(B, 3), peak_day.isoformat(), cap


def author_concentration(rows):
    authors = [r.get("author") or "?" for r in rows]
    known = [a for a in authors if a not in ("?", "", None)]
    if not known:
        return 0.0, 0, 0
    c = Counter(known)
    total = len(known)
    top5 = sum(n for _, n in c.most_common(5)) / total
    uniq_ratio = len(c) / total           # faible = repeat posters = coordination
    return round(top5, 3), len(c), round(uniq_ratio, 3)


def subreddit_sync(rows):
    subs = [r.get("subreddit") or "?" for r in rows]
    c = Counter(s for s in subs if s not in ("?", "", None))
    if not c:
        return 0, 0.0
    n_subs = len(c)
    top_share = c.most_common(1)[0][1] / sum(c.values())
    return n_subs, round(top_share, 3)


def lexical_similarity(rows, max_snippets=400):
    snips = [r.get("text_snippet") or r.get("text") or "" for r in rows]
    snips = [s for s in snips if s][:max_snippets]
    sets = []
    for s in snips:
        toks = [w for w in WORD.findall(s.lower()) if w not in STOP]
        if len(toks) >= 3:
            grams = set(zip(toks, toks[1:]))  # bigrammes
            if grams:
                sets.append(grams)
    if len(sets) < 2:
        return 0.0, 0.0
    import itertools
    pairs = list(itertools.combinations(range(len(sets)), 2))
    if len(pairs) > 6000:
        step = len(pairs) // 6000 + 1
        pairs = pairs[::step]
    sims = []
    for i, j in pairs:
        u = sets[i] | sets[j]
        if u:
            sims.append(len(sets[i] & sets[j]) / len(u))
    if not sims:
        return 0.0, 0.0
    mean_j = sum(sims) / len(sims)
    near_dup = sum(1 for s in sims if s >= 0.5) / len(sims)
    return round(mean_j, 3), round(near_dup, 3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="raw_mentions.csv")
    ap.add_argument("--out", default="c2_scores.csv")
    ap.add_argument("--burst-window", type=int, default=90, help="minutes")
    ap.add_argument("--w-burst", type=float, default=0.25)
    ap.add_argument("--w-author", type=float, default=0.25)
    ap.add_argument("--w-sub", type=float, default=0.25)
    ap.add_argument("--w-lex", type=float, default=0.25)
    ap.add_argument("--sub-cap", type=int, default=6, help="nb subs pour normaliser la synchro")
    ap.add_argument("--c1", default="c1_backtest.csv", help="ancres d'ignition (peak_mentions_date)")
    ap.add_argument("--pre", type=int, default=7, help="jours avant l'ignition")
    ap.add_argument("--post", type=int, default=7, help="jours après l'ignition")
    ap.add_argument("--whole-series", action="store_true",
                    help="ignorer le fenêtrage et calculer sur toute la série (ancien comportement)")
    args = ap.parse_args()

    if not os.path.exists(args.raw):
        sys.exit(f"{args.raw} introuvable. Lance d'abord adanos_raw_pull.py.")
    data = read_raw(args.raw)
    anchors = {} if args.whole_series else load_anchors(args.c1)
    if anchors:
        print(f"C2 fenêtré sur l'ignition ± [{args.pre}j, {args.post}j] "
              f"({len(anchors)} ancres) — la coordination se mesure sur l'événement.\n")
    else:
        print("C2 sur la série complète (pas d'ancres) — risque de dilution.\n")
    rows_out = []
    hdr = (f"{'Ticker':<7}{'n':>6}{'Burst90':>9}{'B-idx':>7}{'TopAuth5':>9}{'Uniq':>6}"
           f"{'Subs':>5}{'Lex':>6}{'NearDup':>8}{'C2':>6}  Jour-pic")
    print(hdr); print("-" * (len(hdr) + 6))
    for tk in sorted(data):
        rows = window_rows(data[tk], anchors.get(tk), args.pre, args.post)
        if not rows:  # rien dans la fenêtre -> repli série complète, signalé
            rows = data[tk]
        ts = [t for t in (parse_ts(r.get("created_utc")) for r in rows) if t]
        burst90, Bidx, peak_day, cap = burstiness(ts, args.burst_window)
        top5, n_auth, uniq = author_concentration(rows)
        n_subs, top_sub = subreddit_sync(rows)
        lex, near = lexical_similarity(rows)
        # normalisations -> [0,1]
        s_burst = burst90                                   # déjà 0-1
        s_author = top5                                     # part top5 auteurs
        s_sub = min(1.0, n_subs / args.sub_cap)             # + de subs = + de synchro
        s_lex = lex                                          # Jaccard moyen
        wsum = args.w_burst + args.w_author + args.w_sub + args.w_lex
        c2 = (s_burst*args.w_burst + s_author*args.w_author +
              s_sub*args.w_sub + s_lex*args.w_lex) / wsum
        c2 = round(c2, 3)
        capflag = "*" if cap else " "
        print(f"{tk:<7}{len(rows):>6}{burst90:>8}{capflag}{Bidx:>7}{top5:>9}{uniq:>6}"
              f"{n_subs:>5}{lex:>6}{near:>8}{c2:>6}  {peak_day}")
        rows_out.append({"ticker": tk, "n_rows": len(rows), "burst_90min": burst90,
                         "burst_index": Bidx, "peak_day": peak_day, "peak_day_capped": cap,
                         "top5_author_share": top5, "n_authors": n_auth, "uniq_ratio": uniq,
                         "n_subreddits": n_subs, "top_sub_share": top_sub,
                         "lex_mean_jaccard": lex, "lex_near_dup": near, "C2_score": c2})

    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows_out[0].keys()))
        w.writeheader(); w.writerows(rows_out)
    print(f"\n-> {args.out}")
    print("Burst90 = part des mentions du jour-pic dans sa fenêtre 90 min la + dense (concentration).")
    print("* = jour-pic tronqué (>=100) -> burstiness sous-estimée (cf. GME).")
    print("C2 élevé = coordination marquée. À recalibrer + valider contre familles 3/4 (buzz non coordonné).")


if __name__ == "__main__":
    main()
