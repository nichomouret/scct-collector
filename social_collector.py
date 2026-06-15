#!/usr/bin/env python3
"""
SCCT-Social · Collecteur social via fournisseurs tiers (VOIE PRINCIPALE)
========================================================================
Suite au verrouillage de l'API Reddit (approbation requise, IP hébergée
restreinte), la collecte passe par des agrégateurs licenciés :

  - ApeWisdom : GRATUIT, sans clé. Mentions + upvotes + rang par ticker.
                Socle immédiat. ~2 rafraîchissements/heure côté source.
  - Adanos    : si ADANOS_API_KEY. Score sentiment finance-tuned (ADAPTER).
  - Quiver    : si QUIVER_API_KEY. Mentions WSB profondeur historique (ADAPTER).

On stocke une SÉRIE TEMPORELLE de snapshots dans la table `mentions` ; le
volume-spike C1 (Z-score) se calcule ensuite sur l'historique accumulé
(voir compute_signals.py).

⚠ Limite vs collecte Reddit directe : les agrégateurs ne donnent PAS l'âge de
compte / le karma -> le sous-signal C2 'nouveauté des comptes' n'est pas
observable ici. Burstiness (vélocité de mentions) et synchro multi-sources le
restent. C2 est donc partiellement observable par cette voie.

Config (env) :
    ADANOS_API_KEY, QUIVER_API_KEY     (optionnels)
    APEWISDOM_FILTER   défaut "all-stocks"  (ex. wallstreetbets, all-stocks…)
    APEWISDOM_PAGES    défaut 4   (≈ top 400 tickers ; ↑ pour couvrir + de micro-caps)
    WATCHLIST          optionnel "OCC,OPEN,KSS" -> ne stocke que ces tickers
    POLL_SECONDS       défaut 1200 (20 min ; inutile d'aller sous le refresh source)
    DB_PATH / DATABASE_URL
    RUN_ONCE           "1" pour un seul passage (test)

Usage :
    pip install -r requirements.txt
    RUN_ONCE=1 python social_collector.py          # test (ApeWisdom gratuit)
    python social_collector.py                     # boucle continue
"""
from __future__ import annotations
import os, sys, time, signal, logging
from datetime import datetime, timezone

try:
    import requests
except ImportError:
    sys.exit("pip install requests")

from storage import Store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("scct")

APEWISDOM_FILTER = os.getenv("APEWISDOM_FILTER", "all-stocks")
APEWISDOM_PAGES = int(os.getenv("APEWISDOM_PAGES", "4"))
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "1200"))
RUN_ONCE = os.getenv("RUN_ONCE", "") in ("1", "true", "yes")
ADANOS_KEY = os.getenv("ADANOS_API_KEY", "")
QUIVER_KEY = os.getenv("QUIVER_API_KEY", "")
WATCHLIST = {t.strip().upper() for t in os.getenv("WATCHLIST", "").split(",") if t.strip()}
UA = {"User-Agent": "SCCT-Social collector"}

_running = True
def _stop(*_):
    global _running; _running = False
    log.info("arrêt demandé, fin du cycle en cours…")
signal.signal(signal.SIGINT, _stop)
signal.signal(signal.SIGTERM, _stop)


def _keep(ticker: str) -> bool:
    return (not WATCHLIST) or (ticker.upper() in WATCHLIST)


# ----------------------------------------------------------------------
# ApeWisdom (gratuit). Doc : https://apewisdom.io/api/
# ----------------------------------------------------------------------
def fetch_apewisdom(now: float) -> list[dict]:
    rows = []
    for page in range(1, APEWISDOM_PAGES + 1):
        url = f"https://apewisdom.io/api/v1.0/filter/{APEWISDOM_FILTER}/page/{page}"
        try:
            r = requests.get(url, headers=UA, timeout=20)
            data = r.json().get("results", [])
        except Exception as e:
            log.warning("ApeWisdom p%d erreur: %s", page, e); break
        if not data:
            break
        for it in data:
            tk = str(it.get("ticker", "")).upper()
            if not tk or not _keep(tk):
                continue
            rows.append({
                "source": "apewisdom", "ticker": tk, "fetched_utc": now,
                "rank": _int(it.get("rank")), "mentions": _int(it.get("mentions")),
                "mentions_prev": _int(it.get("mentions_24h_ago")),
                "upvotes": _int(it.get("upvotes")), "sentiment": None,
                "name": it.get("name"), "extra": None,
            })
        time.sleep(0.3)
    return rows


# ----------------------------------------------------------------------
# Adanos (clé) — score sentiment finance-tuned.
# Doc : https://api.adanos.org/docs  — base https://api.adanos.org/reddit/stocks
#   GET /v1/stock/{ticker}  -> found, mentions, sentiment_score, buzz_score,
#                              bullish_pct, total_upvotes, subreddit_count…
#   Auth : header X-API-Key. 404 = aucune mention sur la période.
# ----------------------------------------------------------------------
ADANOS_BASE = os.getenv("ADANOS_BASE", "https://api.adanos.org/reddit/stocks")

def fetch_adanos(now: float, tickers: list[str]) -> list[dict]:
    if not ADANOS_KEY:
        return []
    out = []
    headers = {"X-API-Key": ADANOS_KEY, **UA}
    for tk in tickers:
        try:
            r = requests.get(f"{ADANOS_BASE}/v1/stock/{tk}", headers=headers, timeout=20)
            if r.status_code == 404:
                continue  # pas de mention sur la période -> on n'écrit rien
            if r.status_code != 200:
                log.debug("Adanos %s HTTP %s", tk, r.status_code); continue
            js = r.json()
            out.append({
                "source": "adanos", "ticker": tk.upper(), "fetched_utc": now,
                "rank": None, "mentions": _int(js.get("mentions")),
                "mentions_prev": None, "upvotes": _int(js.get("total_upvotes")),
                "sentiment": _float(js.get("sentiment_score")),
                "name": js.get("company_name"),
                "extra": {"buzz_score": js.get("buzz_score"),
                          "bullish_pct": js.get("bullish_pct"),
                          "subreddit_count": js.get("subreddit_count")},
            })
        except Exception as e:
            log.debug("Adanos %s err: %s", tk, e)
        time.sleep(0.2)
    return out


# ----------------------------------------------------------------------
# Quiver (clé) — mentions WSB. Auth Quiver = header "Authorization: Token <key>".
# Endpoint live : /beta/live/wallstreetbets/{ticker}. Champs Quiver en CamelCase.
# (Pour l'historique profond -> voir backtest_coverage.py / package quiverquant.)
# ----------------------------------------------------------------------
def fetch_quiver(now: float, tickers: list[str]) -> list[dict]:
    if not QUIVER_KEY:
        return []
    out = []
    headers = {"Authorization": f"Token {QUIVER_KEY}", "Accept": "application/json", **UA}
    for tk in tickers:
        try:
            r = requests.get(
                f"https://api.quiverquant.com/beta/live/wallstreetbets/{tk}",
                headers=headers, timeout=20)
            if r.status_code != 200:
                log.debug("Quiver %s HTTP %s", tk, r.status_code); continue
            js = r.json()
            latest = js[0] if isinstance(js, list) and js else js
            if not isinstance(latest, dict):
                continue
            out.append({
                "source": "quiver", "ticker": tk.upper(), "fetched_utc": now,
                "rank": _int(latest.get("Rank")), "mentions": _int(latest.get("Mentions")),
                "mentions_prev": None, "upvotes": _int(latest.get("Upvotes")),
                "sentiment": _float(latest.get("Sentiment")),
                "name": None, "extra": None,
            })
        except Exception as e:
            log.debug("Quiver %s err: %s", tk, e)
        time.sleep(0.2)
    return out


def _int(v):
    try: return int(v)
    except (TypeError, ValueError): return None
def _float(v):
    try: return float(v)
    except (TypeError, ValueError): return None


def harvest(store: Store) -> int:
    now = datetime.now(timezone.utc).timestamp()
    rows = fetch_apewisdom(now)
    # tickers vus chez ApeWisdom (ou watchlist) -> enrichissement payant ciblé.
    # COLLECTOR_NO_ADANOS=1 : baseline ApeWisdom seule (gratuit) -> économise le quota
    # Adanos pour le scan (recommandé en 24/7, indispensable après passage Hobby).
    seen = sorted({r["ticker"] for r in rows} | WATCHLIST)
    if os.getenv("COLLECTOR_NO_ADANOS", "") not in ("1", "true", "yes"):
        rows += fetch_adanos(now, seen)
    rows += fetch_quiver(now, seen)
    inserted = store.upsert_mentions(rows)
    by_src = {}
    for r in rows:
        by_src[r["source"]] = by_src.get(r["source"], 0) + 1
    log.info("cycle: %s -> %d snapshots insérés (total mentions %d)",
             by_src, inserted, store.count("mentions"))
    return inserted


def main():
    log.info("filter=%s pages=%s poll=%ss adanos=%s quiver=%s watchlist=%s",
             APEWISDOM_FILTER, APEWISDOM_PAGES, POLL_SECONDS,
             bool(ADANOS_KEY), bool(QUIVER_KEY), sorted(WATCHLIST) or "ALL")
    store = Store()
    log.info("stockage=%s", "Postgres" if store.is_pg else f"SQLite ({os.getenv('DB_PATH','scct_posts.db')})")
    try:
        while _running:
            t0 = time.time()
            try:
                harvest(store)
            except Exception as e:
                log.error("cycle en échec: %s", e)
            if RUN_ONCE:
                log.info("RUN_ONCE -> sortie."); break
            dt = POLL_SECONDS - (time.time() - t0)
            while dt > 0 and _running:
                time.sleep(min(1.0, dt)); dt -= 1.0
    finally:
        store.close()
        log.info("collecteur arrêté proprement.")


if __name__ == "__main__":
    main()
