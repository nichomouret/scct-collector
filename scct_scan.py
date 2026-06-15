#!/usr/bin/env python3
"""
SCCT-Social · SCANNER LIVE (watchlist + découverte) — produit les signaux du jour
=================================================================================
Combine les deux univers décidés :
  - DÉCOUVERTE : top mentions ApeWisdom (gratuit) — ce qui buzze maintenant.
  - WATCHLIST  : tes micro-caps screenés (microcaps.txt), interrogés par symbole
                 sur Adanos — pour ne pas rater l'ignition précoce sur tes cibles.

Pour l'union des candidats, calcule un score SCCT LIVE :
  C1  spike de mentions (accélération vs 24h)            — ApeWisdom / Adanos
  C2  coordination (burstiness, comptes, subs, lexical)  — Adanos RAW (Pro) ; sinon omis
  C4  pression de float (float serré + SI)               — Ortex
  C5  catalyseur récent (8-K matériel)                   — SEC EDGAR (quadrant)
Score = mêmes poids que le backtest (C2 0.50, C4 0.25, C1 0.15, C3 0.10→absent live, C5→quadrant).
Renormalisé sur les composantes disponibles.

Sortie : tableau classé + scan_latest.json (lu par le dashboard) + table `signals` (DB).

⚠ C2 live nécessite Adanos Pro (endpoint raw). Sans Pro, le scan tourne sur C1+C4
(+C5 quadrant) — moins discriminant. Détection only ; décisions de trade = les tiennes.

Config (env) : ADANOS_API_KEY, ORTEX_API_KEY, SEC_USER_AGENT
Usage :
    python scct_scan.py --watchlist microcaps.txt --discovery 50 --out scan_latest.json
    python scct_scan.py --no-c2          # ignore C2 (si pas de Pro)
"""
from __future__ import annotations
import argparse, datetime as dt, json, os, sys, time

try:
    import requests
except ImportError:
    sys.exit("pip install requests")

# Réutilise les fonctions déjà écrites et testées
import c2_coordination as C2
import ortex_c4_pull as C4
import c5_catalyst as C5
from ticker_extractor import STOPLIST

ADANOS_KEY = os.getenv("ADANOS_API_KEY", "")
ADANOS_BASE = os.getenv("ADANOS_BASE", "https://api.adanos.org/reddit/stocks")
UA = {"User-Agent": "SCCT-Social scan"}

W = {"C1": 0.15, "C2": 0.50, "C4": 0.25}  # C3 absent en live, C5 -> quadrant
Z_SAT = 5.0

# ETF / indices courants à exclure (pas des cibles de squeeze micro-cap)
ETF_BLOCK = {"SPY", "QQQ", "VOO", "IVV", "VTI", "SGOV", "USO", "USO", "SOXL", "SOXS",
             "TQQQ", "SQQQ", "UVXY", "VXX", "GLD", "SLV", "TLT", "HYG", "ARKK",
             "SPXL", "SPXS", "DIA", "IWM", "XLF", "XLE", "XLK", "BITO", "UCO", "BOIL"}
# Faux tickers / mots courants vus dans le bruit ApeWisdom (en plus de STOPLIST)
JUNK = STOPLIST | {"API", "JUST", "WTI", "WH", "UP", "IT", "DC", "EU", "UAE", "YOU",
                   "AM", "ES", "OS", "NOW", "ANY", "GO", "AI", "OR", "BE", "ARE",
                   "DJT", "WTI", "UAE", "EU"}


def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def apewisdom_discovery(pages):
    out = {}
    for p in range(1, pages + 1):
        try:
            r = requests.get(f"https://apewisdom.io/api/v1.0/filter/all-stocks/page/{p}",
                             headers=UA, timeout=20)
            data = r.json().get("results", [])
        except Exception:
            break
        if not data:
            break
        for it in data:
            tk = str(it.get("ticker", "")).upper()
            if tk:
                out[tk] = {"mentions": it.get("mentions"), "prev": it.get("mentions_24h_ago")}
        time.sleep(0.3)
    return out


def adanos_stock(tk):
    if not ADANOS_KEY:
        return None
    try:
        r = requests.get(f"{ADANOS_BASE}/v1/stock/{tk}", headers={"X-API-Key": ADANOS_KEY, **UA}, timeout=25)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def adanos_raw_recent(tk, days=3):
    """Mentions brutes des derniers jours (Adanos Pro) -> pour C2."""
    if not ADANOS_KEY:
        return []
    rows = []
    today = dt.date.today()
    for i in range(days):
        d = (today - dt.timedelta(days=i)).isoformat()
        try:
            r = requests.get(f"{ADANOS_BASE}/v1/stock/{tk}/mentions",
                             params={"from": d, "to": d, "limit": 100},
                             headers={"X-API-Key": ADANOS_KEY, **UA}, timeout=25)
            if r.status_code == 403:
                return None  # pas Pro
            if r.status_code == 200:
                rows += r.json().get("results", [])
        except Exception:
            pass
        time.sleep(0.1)
    return rows


def c1_spike(mentions, prev, min_mentions):
    # plancher de volume ABSOLU : pas d'anomalie sur 2 mentions vs 0
    if not mentions or mentions < min_mentions:
        return 0.0
    prev = prev or 0
    ratio = mentions / max(prev, 1)
    return round(clamp((ratio - 1) / 2.0), 3)  # ratio 1->0, 3->1


def c2_live(tk, want_c2):
    if not want_c2:
        return None
    raw = adanos_raw_recent(tk)
    if raw is None:
        return None  # pas Pro
    rows = [{"created_utc": m.get("created_utc"), "author": m.get("author"),
             "subreddit": m.get("subreddit"),
             "text_snippet": m.get("text_snippet", "")} for m in raw]
    if len(rows) < 4:
        return None
    ts = [t for t in (C2.parse_ts(r["created_utc"]) for r in rows) if t]
    burst90, _, _, _ = C2.burstiness(ts, 90)
    top5, _, _ = C2.author_concentration(rows)
    n_subs, _ = C2.subreddit_sync(rows)
    lex, _ = C2.lexical_similarity(rows)
    return round((burst90 + top5 + min(1.0, n_subs / 6) + lex) / 4, 3)


def c4_live(tk):
    ox = C4.fetch_ortex(tk)
    fl = ox["float_shares"]
    sf, ss, sb, c4 = C4.compute_c4(fl, ox["si"], ox["borrow"])
    return c4, (round(fl / 1e6, 1) if fl else None), ox["si"], ox.get("si_usd")


def c5_live(tk):
    C5.load_cik()
    today = dt.date.today()
    r = C5.analyze(tk, today, 21, 2, today - dt.timedelta(days=30), today)
    return r.get("C5", 0)


def score(c1, c2, c4):
    comps = {"C1": (c1, W["C1"]), "C2": (c2, W["C2"]), "C4": (c4, W["C4"])}
    used = {k: (v, w) for k, (v, w) in comps.items()
            if v is not None or k == "C1"}  # C1 social-absence=0 garanti
    used = {k: ((v if v is not None else 0.0), w) for k, (v, w) in used.items()}
    wsum = sum(w for _, w in used.values())
    return round(sum(v * w for v, w in used.values()) / wsum * 100, 1) if wsum else 0.0


def claude_analysis(signals, n_universe):
    """Résumé en langage clair du scan via l'API Anthropic (clé ANTHROPIC_API_KEY).
    Renvoie un texte FR de 3-4 phrases, ou un repli déterministe si pas de clé/erreur."""
    key = os.getenv("ANTHROPIC_API_KEY", "")
    top = signals[:8]
    if not key:
        if not signals:
            return "Aucun signal au-dessus du seuil — période calme, pas de squeeze social détecté."
        names = ", ".join(f"{s['ticker']} ({s['SCCT']})" for s in top)
        return f"{len(signals)} candidats : {names}. Vérifie float, short interest et catalyseur avant tout trade."
    try:
        import json as _json
        payload = {
            "model": os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"),
            "max_tokens": 600,
            "messages": [{"role": "user", "content":
                "Tu es analyste pour SCCT, un détecteur de squeezes micro-cap US via coordination "
                "sociale Reddit. Voici le scan courant (score 0-100 ; C1=spike volume, C2=coordination, "
                "C4=pression de float, C5=catalyseur ; quadrants Q1 pump pur / Q2 convergence idéale / "
                "Q3 bruit / Q4 rerating fondamental). Rédige une analyse FR de 3-4 phrases : ce qui "
                "ressort, les pièges, et s'il y a vraiment quelque chose à surveiller. Sois sobre et "
                "factuel, rappelle que c'est de la détection, pas un conseil. Données : "
                + _json.dumps({"n_universe": n_universe, "signals": top}, ensure_ascii=False)}],
        }
        r = requests.post("https://api.anthropic.com/v1/messages",
                          headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                                   "content-type": "application/json"},
                          json=payload, timeout=30)
        if r.status_code != 200:
            return f"(analyse Claude indisponible : HTTP {r.status_code})"
        return r.json()["content"][0]["text"].strip()
    except Exception as e:
        return f"(analyse Claude indisponible : {e})"


def quadrant(c1, c5):
    # axe 2 = catalyseur (C5) ; antériorité C3 absente en live -> proxy par spike fort
    anchor = c5 and c5 >= 0.5
    return ("Q2 convergence" if anchor else "Q1 pump pur") if c1 and c1 >= 0.5 else \
           ("Q4 rerating" if anchor else "Q3 bruit")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--watchlist", default="microcaps.txt")
    ap.add_argument("--discovery", type=int, default=50, help="top N ApeWisdom (0 = off)")
    ap.add_argument("--discovery-pages", type=int, default=5)
    ap.add_argument("--no-c2", action="store_true", help="ignorer C2 (si pas de plan Adanos Pro)")
    ap.add_argument("--min-score", type=float, default=40.0)
    ap.add_argument("--min-mentions", type=int, default=20, help="plancher volume pour C1 (anti-bruit)")
    ap.add_argument("--max-float-m", type=float, default=150.0, help="plafond free float (M) — cibles micro/small-cap")
    ap.add_argument("--max-si-usd", type=float, default=1.5e9, help="plafond short interest en $ (au-dessus = mega-cap)")
    ap.add_argument("--out", default="scan_latest.json")
    args = ap.parse_args()

    want_c2 = not args.no_c2
    # univers : watchlist + découverte
    universe = {}
    if os.path.exists(args.watchlist):
        with open(args.watchlist) as f:
            for ln in f:
                t = ln.strip().upper()
                if t and not t.startswith("#"):
                    universe[t] = {"src": "watchlist"}
    disc = apewisdom_discovery(args.discovery_pages) if args.discovery else {}
    for i, (tk, d) in enumerate(sorted(disc.items(), key=lambda x: -(x[1]["mentions"] or 0))):
        if i >= args.discovery:
            break
        universe.setdefault(tk, {})["src"] = universe.get(tk, {}).get("src", "découverte")
        universe[tk].update(d)

    print(f"Univers : {len(universe)} candidats (watchlist + top {args.discovery} découverte) · "
          f"C2={'oui' if want_c2 else 'non'}\n")
    hdr = f"{'Ticker':<7}{'Src':<11}{'C1':>5}{'C2':>6}{'C4':>6}{'C5':>4}{'SCCT':>7}  Quadrant"
    print(hdr); print("-" * (len(hdr) + 6))
    results = []
    n_junk = n_bigfloat = 0
    for tk, d in universe.items():
        # filtre 1 : tickers-poubelle / mots courants / ETF
        if tk in JUNK or tk in ETF_BLOCK:
            n_junk += 1
            continue
        mentions, prev = d.get("mentions"), d.get("prev")
        if mentions is None:  # watchlist sans data découverte -> interroge Adanos
            js = adanos_stock(tk)
            if js:
                dt_rows = js.get("daily_trend") or []
                if dt_rows:
                    # jour le plus récent vs moyenne de la semaine précédente (pas total 7j !)
                    mentions = dt_rows[0].get("mentions")
                    base = [r.get("mentions", 0) for r in dt_rows[1:8]]
                    prev = (sum(base) / len(base)) if base else 0
                else:
                    mentions = js.get("mentions")
        c1 = c1_spike(mentions, prev, args.min_mentions)
        c4, float_m, si, si_usd = c4_live(tk)
        # filtre 2 : float inconnu (throttlé/délisté) -> on ne peut pas évaluer -> exclu
        if float_m is None:
            n_bigfloat += 1
            continue
        # filtre 3 : univers micro/small-cap (float > plafond -> exclu)
        if float_m > args.max_float_m:
            n_bigfloat += 1
            continue
        # filtre 4 : grosse cap déguisée (short interest en $ énorme = mega-cap, ex. AMZN)
        if si_usd is not None and si_usd > args.max_si_usd:
            n_bigfloat += 1
            continue
        c2 = c2_live(tk, want_c2)
        c5 = c5_live(tk)
        sc = score(c1, c2, c4)
        q = quadrant(c1, c5)
        results.append({"ticker": tk, "src": d.get("src", "?"), "mentions": mentions,
                        "C1": c1, "C2": c2, "C4": c4, "C5": c5, "float_m": float_m,
                        "short_int": si, "SCCT": sc, "quadrant": q})
        time.sleep(0.2)
    print(f"(filtrés : {n_junk} poubelle/ETF, {n_bigfloat} float > {args.max_float_m}M)\n")

    results.sort(key=lambda r: r["SCCT"], reverse=True)
    for r in results:
        if r["SCCT"] < args.min_score:
            continue
        def f(x): return "-" if x is None else (f"{x:.2f}" if isinstance(x, float) else str(x))
        print(f"{r['ticker']:<7}{r['src']:<11}{f(r['C1']):>5}{f(r['C2']):>6}{f(r['C4']):>6}"
              f"{f(r['C5']):>4}{r['SCCT']:>7}  {r['quadrant']}")

    sigs = [r for r in results if r["SCCT"] >= args.min_score]
    snapshot = {"generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                "min_score": args.min_score, "n_universe": len(universe),
                "analysis": claude_analysis(sigs, len(universe)),
                "signals": sigs, "all": results}
    with open(args.out, "w") as f:
        json.dump(snapshot, f, indent=2)
    n_sig = len(snapshot["signals"])
    print(f"\n{n_sig} signaux ≥ {args.min_score} -> {args.out}")
    print("Détection only. Vérifie chaque candidat manuellement avant tout trade.")


if __name__ == "__main__":
    main()
