#!/usr/bin/env python3
"""
SCCT-Social · Collecteur Reddit (P5 — forward-test propriétaire)
================================================================
Socle de la composante C2 : collecte horodatée des posts + MÉTADONNÉES DE COMPTE
(âge du compte, karma) qu'aucun agrégateur tiers ne fournit. Tourne en boucle,
poll /new et /rising sur les subs cibles, extrait les tickers, dédoublonne, stocke.

Auth : OAuth2 read-only via app Reddit de type 'script' (client_id + client_secret).
       Aucune action d'écriture — DÉTECTION uniquement (cf. ligne rouge conformité).

Config (variables d'environnement) :
    REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET   (obligatoires)
    REDDIT_USER_AGENT     défaut "scct-social/0.1 by u/yourname"
    SUBREDDITS            défaut "wallstreetbets,pennystocks,smallstreetbets,shortsqueeze"
    POLL_SECONDS          défaut 90
    POST_LIMIT            défaut 60   (par listing et par sub)
    FETCH_AUTHOR          défaut "watched"  (watched|always|never) -> coût API
    MIN_CONFIDENCE        défaut 0.5
    DB_PATH / DATABASE_URL  (cf. storage.py)
    RUN_ONCE             "1" pour un seul passage (test) puis sortie

Usage local :
    pip install -r requirements.txt
    export REDDIT_CLIENT_ID=... REDDIT_CLIENT_SECRET=...
    RUN_ONCE=1 python reddit_collector.py     # test : un passage
    python reddit_collector.py                # boucle continue
"""
from __future__ import annotations
import os, sys, time, signal, logging
from datetime import datetime, timezone

try:
    import praw
except ImportError:
    sys.exit("pip install praw")

from ticker_extractor import extract, load_universe
from storage import Store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("scct")

SUBS = [s.strip() for s in os.getenv(
    "SUBREDDITS", "wallstreetbets,pennystocks,smallstreetbets,shortsqueeze").split(",") if s.strip()]
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "90"))
POST_LIMIT = int(os.getenv("POST_LIMIT", "60"))
FETCH_AUTHOR = os.getenv("FETCH_AUTHOR", "watched").lower()
MIN_CONF = float(os.getenv("MIN_CONFIDENCE", "0.5"))
RUN_ONCE = os.getenv("RUN_ONCE", "") in ("1", "true", "yes")

_running = True
def _stop(*_):
    global _running
    _running = False
    log.info("arrêt demandé, fin du cycle en cours…")
signal.signal(signal.SIGINT, _stop)
signal.signal(signal.SIGTERM, _stop)


def make_reddit() -> "praw.Reddit":
    cid = os.getenv("REDDIT_CLIENT_ID"); sec = os.getenv("REDDIT_CLIENT_SECRET")
    if not cid or not sec:
        sys.exit("REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET manquants.")
    reddit = praw.Reddit(
        client_id=cid, client_secret=sec,
        user_agent=os.getenv("REDDIT_USER_AGENT", "scct-social/0.1 by u/yourname"),
    )
    reddit.read_only = True
    return reddit


def author_meta(post, want: bool) -> dict:
    """Récupère âge de compte + karma (alimente C2 : 'nouveauté des comptes').
    Coûteux (1 requête/auteur) -> ne le faire que si 'want'."""
    out = {"author": None, "author_created_utc": None,
           "author_comment_karma": None, "author_link_karma": None}
    a = post.author
    if a is None:
        return out
    out["author"] = str(a)
    if not want:
        return out
    try:
        out["author_created_utc"] = float(getattr(a, "created_utc", None) or 0) or None
        out["author_comment_karma"] = getattr(a, "comment_karma", None)
        out["author_link_karma"] = getattr(a, "link_karma", None)
    except Exception as e:
        log.debug("author meta err %s: %s", a, e)
    return out


def harvest(reddit, store: Store, universe) -> int:
    now = datetime.now(timezone.utc).timestamp()
    batch = []
    for sub in SUBS:
        sr = reddit.subreddit(sub)
        for listing in ("new", "rising"):  # 'rising' = accélération précoce, précieux
            try:
                stream = getattr(sr, listing)(limit=POST_LIMIT)
            except Exception as e:
                log.warning("%s/%s erreur: %s", sub, listing, e)
                continue
            for p in stream:
                text = f"{p.title or ''}\n{getattr(p, 'selftext', '') or ''}"
                tickers = extract(text, universe=universe, min_confidence=MIN_CONF)
                want_author = (FETCH_AUTHOR == "always" or
                               (FETCH_AUTHOR == "watched" and bool(tickers)))
                am = author_meta(p, want_author)
                batch.append({
                    "post_id": p.id, "subreddit": sub, "listing": listing,
                    "created_utc": float(p.created_utc), "fetched_utc": now,
                    "score": int(p.score), "upvote_ratio": getattr(p, "upvote_ratio", None),
                    "num_comments": int(p.num_comments),
                    "flair": getattr(p, "link_flair_text", None),
                    "title": (p.title or "")[:500], "tickers": tickers, **am,
                })
    inserted = store.upsert(batch)
    log.info("cycle: %d posts vus, %d nouveaux (total %d)", len(batch), inserted, store.count())
    return inserted


def main():
    log.info("subs=%s poll=%ss limit=%s author=%s", SUBS, POLL_SECONDS, POST_LIMIT, FETCH_AUTHOR)
    reddit = make_reddit()
    store = Store()
    log.info("stockage=%s", "Postgres" if store.is_pg else f"SQLite ({os.getenv('DB_PATH','scct_posts.db')})")
    universe = load_universe()
    log.info("univers tickers chargé: %d symboles", len(universe))
    try:
        while _running:
            t0 = time.time()
            try:
                harvest(reddit, store, universe)
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
