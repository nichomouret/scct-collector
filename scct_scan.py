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
import adanos_x as X
from ticker_extractor import STOPLIST
try:
    from storage import Store
except Exception:
    Store = None

ADANOS_KEY = os.getenv("ADANOS_API_KEY", "")
ADANOS_BASE = os.getenv("ADANOS_BASE", "https://api.adanos.org/reddit/stocks")
TD_KEY = os.getenv("TWELVEDATA_API_KEY", "")
UA = {"User-Agent": "SCCT-Social scan"}
IGN_MOVE_CAP = float(os.getenv("IGN_MOVE_CAP", "0.15"))   # au-delà, l'ignition s'amortit
CHASE_MOVE = float(os.getenv("CHASE_MOVE", "0.20"))       # hausse 5j -> flag « déjà parti »

_price_cache = {}


def price_snapshot(tk, max_age_days=10):
    """1 appel TwelveData (mis en cache) -> dict :
       tradeable (coté récemment ?), last (dernier close), chg_3d / chg_5d (variation %).
    Sert au filtre cotation ET à la détection précoce (le move est-il déjà parti ?).
    Fail-open : pas de clé / erreur réseau -> tradeable=True (on ne bloque pas)."""
    if tk in _price_cache:
        return _price_cache[tk]
    snap = {"tradeable": True, "last": None, "chg_3d": None, "chg_5d": None}
    if not TD_KEY:
        _price_cache[tk] = snap
        return snap
    try:
        r = requests.get("https://api.twelvedata.com/time_series",
                         params={"symbol": tk, "interval": "1day", "outputsize": 8,
                                 "apikey": TD_KEY}, headers=UA, timeout=20)
        js = r.json()
        vals = js.get("values") if isinstance(js, dict) else None
        if not vals:                       # symbole introuvable / délisté
            snap["tradeable"] = False
        else:
            # TwelveData : ordre décroissant par défaut (vals[0] = plus récent)
            closes = [float(v["close"]) for v in vals if v.get("close")]
            last = str(vals[0].get("datetime", ""))[:10]
            try:
                snap["tradeable"] = (dt.date.today() - dt.date.fromisoformat(last)).days <= max_age_days
            except ValueError:
                pass
            if closes:
                snap["last"] = closes[0]
                if len(closes) > 3 and closes[3] > 0:
                    snap["chg_3d"] = (closes[0] - closes[3]) / closes[3]
                if len(closes) > 5 and closes[5] > 0:
                    snap["chg_5d"] = (closes[0] - closes[5]) / closes[5]
    except Exception:
        pass                               # réseau : ne pas bloquer
    _price_cache[tk] = snap
    return snap


def is_tradeable(tk, max_age_days=10):
    return price_snapshot(tk, max_age_days)["tradeable"]


def ignition_score(mention_ratio, trend_history, chg_5d, move_cap=0.15):
    """Signal d'IGNITION PRÉCOCE : buzz en accélération AVANT que le prix ait
    beaucoup bougé. Élevé = coordination naissante (en amont), pas un move déjà
    consommé. Combine l'accélération du buzz X (dérivée de trend_history) et celle
    des mentions Reddit, amortie si le titre a déjà couru (anti-chasing)."""
    accel = 0.0
    th = [float(x) for x in (trend_history or []) if isinstance(x, (int, float))]
    if len(th) >= 4:
        prior = sum(th[-4:-1]) / 3
        accel = clamp((th[-1] - prior) / max(prior, 1.0))
    mr = clamp((mention_ratio - 1) / 2.0) if mention_ratio else 0.0
    raw = 0.5 * accel + 0.5 * mr
    if chg_5d is not None and chg_5d > move_cap:   # move déjà entamé -> on amortit
        raw *= clamp(1 - (chg_5d - move_cap) / move_cap)   # > 2x le cap -> ~0
    return round(clamp(raw), 3)

W = {"C1": 0.15, "C2": 0.50, "C4": 0.25}  # C3 absent en live, C5 -> quadrant
Z_SAT = 5.0

# ETF / indices courants à exclure (pas des cibles de squeeze micro-cap)
ETF_BLOCK = {"SPY", "QQQ", "VOO", "IVV", "VTI", "SGOV", "USO", "USO", "SOXL", "SOXS",
             "TQQQ", "SQQQ", "UVXY", "VXX", "GLD", "SLV", "TLT", "HYG", "ARKK",
             "SPXL", "SPXS", "DIA", "IWM", "XLF", "XLE", "XLK", "BITO", "UCO", "BOIL"}
# Faux tickers / mots courants vus dans le bruit ApeWisdom (en plus de STOPLIST)
JUNK = STOPLIST | {"API", "JUST", "WTI", "WH", "UP", "IT", "DC", "EU", "UAE", "YOU",
                   "AM", "ES", "OS", "NOW", "ANY", "GO", "AI", "OR", "BE", "ARE",
                   "DJT", "WTI", "UAE", "EU", "VT", "VXUS", "BND", "BNDX", "VYM",
                   "SCHD", "VEA", "VWO", "AGG", "JEPI", "JEPQ", "SCHG"}
# Filtre par NOM de société : exclut tout ETF/fonds quel que soit le ticker
ETF_NAME_HINTS = ("etf", "etn", " fund", "index fund", "vanguard", "ishares",
                  "spdr", "proshares", "direxion", "invesco", " trust etf")


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
                out[tk] = {"mentions": it.get("mentions"), "prev": it.get("mentions_24h_ago"),
                           "name": it.get("name")}
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


def c4_live(tk, store=None):
    # cache float/SI (Postgres) : Ortex n'est appelé que si absent ou périmé (>7j)
    cached = store.meta_get(tk) if store else None
    if cached and cached.get("float_m") is not None:
        fl = cached["float_m"] * 1e6; si = cached.get("si"); si_usd = cached.get("si_usd")
    else:
        ox = C4.fetch_ortex(tk)
        fl, si, si_usd = ox["float_shares"], ox["si"], ox.get("si_usd")
        if store and fl is not None:
            store.meta_put(tk, fl / 1e6, si, si_usd, None)
    sf, ss, sb, c4 = C4.compute_c4(fl, si, None)
    return c4, (round(fl / 1e6, 1) if fl else None), si, si_usd


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
                "sociale Reddit (score 0-100 ; C1=spike volume, C2=coordination, C4=pression de float, "
                "C5=catalyseur ; quadrants Q1 pump pur / Q2 convergence idéale / Q3 bruit / Q4 rerating). "
                "Rédige en FRANÇAIS, format STRICT, SANS markdown (pas de #, pas de **, pas de gras) :\n"
                "Ligne 1 : 'VERDICT: ' suivi d'UNE phrase (y a-t-il quelque chose à regarder, oui/non et pourquoi).\n"
                "Puis 2 à 4 lignes, chacune commençant par '- ', courtes (1 phrase max) : le ou les "
                "candidats notables, les pièges, ce qu'il faut surveiller. Sobre, factuel. "
                "Termine par une ligne '- Rappel: détection algorithmique, pas un conseil.' Données : "
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
    _i = lambda k, d: int(os.getenv(k, str(d)))
    _f = lambda k, d: float(os.getenv(k, str(d)))
    ap.add_argument("--watchlist", default="microcaps.txt")
    ap.add_argument("--discovery", type=int, default=_i("DISCOVERY", 80), help="top N ApeWisdom (0 = off)")
    ap.add_argument("--discovery-pages", type=int, default=_i("DISCOVERY_PAGES", 10))
    ap.add_argument("--no-c2", action="store_true", help="ignorer C2 (si pas de plan Adanos Pro)")
    ap.add_argument("--min-score", type=float, default=_f("MIN_SCORE", 40.0))
    ap.add_argument("--min-mentions", type=int, default=_i("MIN_MENTIONS", 15), help="plancher volume pour C1 (anti-bruit)")
    ap.add_argument("--max-float-m", type=float, default=_f("MAX_FLOAT_M", 150.0), help="plafond free float (M)")
    ap.add_argument("--max-si-usd", type=float, default=_f("MAX_SI_USD", 1.5e9), help="plafond short interest $ (mega-cap)")
    ap.add_argument("--max-score-per-cycle", type=int, default=_i("MAX_SCORE_PER_CYCLE", 250),
                    help="nb max de candidats scorés par cycle (borne le coût Ortex)")
    ap.add_argument("--out", default="scan_latest.json")
    args = ap.parse_args()

    store = None
    if Store is not None:
        try:
            store = Store()  # cache float/SI (Postgres si DATABASE_URL, sinon SQLite)
        except Exception as e:
            print(f"(cache indisponible: {e})", file=sys.stderr)

    want_c2 = not args.no_c2
    CONFIRMED = X.load_confirmed(os.getenv("CONFIRMED_FILE", "confirmed_accounts.txt"))
    if CONFIRMED:
        print(f"Comptes X confirmés chargés : {len(CONFIRMED)}\n", file=sys.stderr)
    # --- Univers (logique « buzz-gated ») ---
    # 1) ApeWisdom en profondeur = source de buzz GRATUITE (top ~1000 selon pages).
    disc = apewisdom_discovery(args.discovery_pages)
    # 2) Watchlist : on ne RETIENT que les noms qui buzzent (présents dans ApeWisdom
    #    avec >= min_mentions) -> une watchlist de centaines de noms ne coûte presque rien
    #    (les noms sans buzz ne déclenchent aucun appel payant).
    wl = set()
    if os.path.exists(args.watchlist):
        with open(args.watchlist) as f:
            wl = {ln.strip().upper() for ln in f if ln.strip() and not ln.startswith("#")}
    universe = {}
    for t in wl:
        d = disc.get(t)
        if d and (d.get("mentions") or 0) >= args.min_mentions:
            universe[t] = {"src": "watchlist", **d}
    # 3) Découverte : top N ApeWisdom par mentions (hors watchlist déjà prise)
    added = 0
    for tk, d in sorted(disc.items(), key=lambda x: -(x[1].get("mentions") or 0)):
        if added >= args.discovery:
            break
        if tk in universe:
            continue
        universe[tk] = {"src": "découverte", **d}
        added += 1

    print(f"Univers : {len(universe)} candidats qui buzzent (watchlist active : "
          f"{sum(1 for v in universe.values() if v['src']=='watchlist')} sur {len(wl)} surveillés · "
          f"découverte : {added}) · C2={'oui' if want_c2 else 'non'}\n")
    # plafond par cycle : on score les plus buzzants d'abord (borne le coût Ortex)
    cand = sorted(universe.items(), key=lambda x: -(x[1].get("mentions") or 0))[:args.max_score_per_cycle]
    hdr = f"{'Ticker':<7}{'Src':<11}{'C1':>5}{'C2':>6}{'C4':>6}{'C5':>4}{'SCCT':>7}  Quadrant"
    print(hdr); print("-" * (len(hdr) + 6))
    results = []
    n_junk = n_bigfloat = n_delisted = 0
    for tk, d in cand:
        # filtre 1 : tickers-poubelle / mots courants / ETF
        if tk in JUNK or tk in ETF_BLOCK:
            n_junk += 1
            continue
        mentions, prev, name = d.get("mentions"), d.get("prev"), d.get("name")
        # filtre ETF/fonds par nom de société (robuste, quel que soit le ticker)
        if name and any(h in name.lower() for h in ETF_NAME_HINTS):
            n_junk += 1
            continue
        c1 = c1_spike(mentions, prev, args.min_mentions)
        c4, float_m, si, si_usd = c4_live(tk, store)
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
        # filtre 5 : titre ENCORE coté ? (anti données mortes Ortex, ex. PS délisté)
        if not is_tradeable(tk):
            n_delisted += 1
            continue
        c2 = c2_live(tk, want_c2)
        c5 = c5_live(tk)
        sc = score(c1, c2, c4)
        q = quadrant(c1, c5)
        # présence sociale = spike de mentions (C1) OU coordination (C2). SCCT détecte
        # des squeezes SOCIAUX : sans présence sociale, un titre n'est PAS un signal
        # (quel que soit son float/catalyseur) — il peut au mieux aller en « veille ».
        has_social = bool(c1) or bool(c2 and c2 > 0)
        # --- signal X/Twitter (cross-validation + comptes confirmés) ---
        x_buzz = x_sent = None
        cross = False
        x_confirmed = []
        xs = None
        if os.getenv("X_ENABLED", "1") in ("1", "true", "yes"):
            xs = X.x_stock(tk)
            if xs:
                x_buzz = xs.get("buzz_score")
                x_sent = xs.get("sentiment_score")
                # cross-plateforme : buzz Reddit (mentions) ET buzz X significatif
                cross = bool((mentions or 0) >= args.min_mentions and (x_buzz or 0) >= 40)
                # comptes à edge confirmé actifs sur le titre = élément de confirmation
                x_confirmed = X.confirmed_in_stock(xs, CONFIRMED)
        # --- détection précoce : ignition (buzz qui accélère AVANT le gros move) ---
        snap = price_snapshot(tk)          # déjà appelé par le filtre cotation (cache)
        chg_5d = snap.get("chg_5d")
        mention_ratio = (mentions or 0) / max(prev or 0, 1)
        ignition = ignition_score(mention_ratio, xs.get("trend_history") if xs else None,
                                  chg_5d, IGN_MOVE_CAP)
        already_moved = chg_5d is not None and chg_5d >= CHASE_MOVE
        results.append({"ticker": tk, "name": name, "src": d.get("src", "?"), "mentions": mentions,
                        "C1": c1, "C2": c2, "C4": c4, "C5": c5, "float_m": float_m,
                        "short_int": si, "x_buzz": x_buzz, "x_sent": x_sent, "cross": cross,
                        "x_confirmed": x_confirmed, "ignition": ignition,
                        "chg_5d": round(chg_5d, 3) if chg_5d is not None else None,
                        "already_moved": already_moved,
                        "SCCT": sc, "quadrant": q, "has_social": has_social})
        time.sleep(0.2)
    print(f"(filtrés : {n_junk} poubelle/ETF, {n_bigfloat} float > {args.max_float_m}M, "
          f"{n_delisted} délistés/non cotés)\n")

    results.sort(key=lambda r: r["SCCT"], reverse=True)
    # SIGNAUX = présence sociale (C1 ou C2) ET score >= seuil
    sigs = [r for r in results if r["SCCT"] >= args.min_score and r["has_social"]]
    # VEILLE = titres réellement discutés (mentions >= seuil) mais pas (encore) un
    # signal : soit score sous le seuil, soit pas (encore) de présence sociale.
    sig_ids = {id(r) for r in sigs}
    watch = [r for r in results
             if id(r) not in sig_ids and (r.get("mentions") or 0) >= args.min_mentions]
    # priorité à l'IGNITION (buzz qui accélère tôt) puis au score
    watch.sort(key=lambda r: (r.get("ignition") or 0, r["SCCT"]), reverse=True)
    watch = watch[:25]

    def f(x): return "-" if x is None else (f"{x:.2f}" if isinstance(x, float) else str(x))
    for r in sigs:
        print(f"{r['ticker']:<7}{r['src']:<11}{f(r['C1']):>5}{f(r['C2']):>6}{f(r['C4']):>6}"
              f"{f(r['C5']):>4}{r['SCCT']:>7}  {r['quadrant']}")

    snapshot = {"generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                "min_score": args.min_score, "n_universe": len(universe),
                "analysis": claude_analysis(sigs, len(universe)),
                "signals": sigs, "watch": watch, "all": results}
    with open(args.out, "w") as f:
        json.dump(snapshot, f, indent=2)
    n_sig = len(snapshot["signals"])
    print(f"\n{n_sig} signaux ≥ {args.min_score} · {len(watch)} en veille -> {args.out}")
    print("Détection only. Vérifie chaque candidat manuellement avant tout trade.")


if __name__ == "__main__":
    main()
