"""
SCCT-Social · Helpers API X/Twitter (Adanos) — réutilise la clé ADANOS_API_KEY.
Base : https://api.adanos.org/x/stocks   (auth header X-API-Key)
Endpoints utilisés :
  /v1/stock/{ticker}              -> buzz, sentiment, top_authors, top_tweets
  /v1/stock/{ticker}/mentions     -> tweets bruts (Pro) : author, created_utc, likes, sentiment…
  /v1/trending                    -> tickers tendance sur X
Doc : https://api.adanos.org/docs  (section X)
"""
from __future__ import annotations
import datetime as dt
import os
import sys
import time

try:
    import requests
except ImportError:
    sys.exit("pip install requests")

X_KEY = os.getenv("ADANOS_API_KEY", "")
X_BASE = os.getenv("ADANOS_X_BASE", "https://api.adanos.org/x/stocks")
UA = {"User-Agent": "SCCT-Social X"}


def _headers():
    return {"X-API-Key": X_KEY, **UA}


def x_stock(tk: str, dfrom: str | None = None, dto: str | None = None):
    """Données X pour un ticker : buzz, sentiment, top_authors, top_tweets. None si erreur."""
    if not X_KEY:
        return None
    params = {}
    if dfrom and dto:
        params = {"from": dfrom, "to": dto}
    try:
        r = requests.get(f"{X_BASE}/v1/stock/{tk}", params=params, headers=_headers(), timeout=25)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def x_raw_mentions_day(tk: str, day: str):
    """Tweets bruts d'un ticker sur un jour (Pro). Renvoie (rows, count) ; rows=None si 403/erreur fatale."""
    if not X_KEY:
        return [], 0
    try:
        r = requests.get(f"{X_BASE}/v1/stock/{tk}/mentions",
                         params={"from": day, "to": day, "limit": 100},
                         headers=_headers(), timeout=30)
        if r.status_code == 403:
            print("  X raw = Pro requis (403)", file=sys.stderr)
            return None, None
        if r.status_code == 401:
            print("  X clé invalide (401)", file=sys.stderr)
            return None, None
        if r.status_code == 429:
            time.sleep(2)
            return [], 0
        if r.status_code != 200:
            return [], 0
        js = r.json()
        return js.get("results", []), js.get("count", 0)
    except Exception as e:
        print(f"  X raw {tk} {day} err {e}", file=sys.stderr)
        return [], 0


def load_confirmed(path: str = "confirmed_accounts.txt") -> set[str]:
    """Charge les handles X confirmés (un par ligne, sans @, # = commentaire)."""
    out = set()
    try:
        with open(path) as f:
            for ln in f:
                ln = ln.strip()
                if ln and not ln.startswith("#"):
                    out.add(ln.lstrip("@").lower())
    except FileNotFoundError:
        pass
    return out


def confirmed_in_stock(xs: dict, confirmed: set[str]) -> list[str]:
    """Comptes confirmés présents dans top_authors / top_tweets d'une réponse x_stock
    (gratuit, pas d'appel supplémentaire). Renvoie la liste des handles trouvés."""
    if not xs or not confirmed:
        return []
    found = {}  # clé = handle minuscule (dédup casse), valeur = casse d'origine
    def _scan(items, key):
        for it in (items or []):
            h = it.get(key) if isinstance(it, dict) else it
            if h:
                lc = str(h).lstrip("@").lower()
                if lc in confirmed:
                    found.setdefault(lc, str(h).lstrip("@"))
    _scan(xs.get("top_authors"), "author")
    _scan(xs.get("top_tweets"), "author")
    return sorted(found.values())


def x_trending(dfrom: str | None = None, dto: str | None = None, limit: int = 100):
    if not X_KEY:
        return []
    # NB : 'type=stock' ET 'from/to' ensemble cassent la requête (réponse vide).
    # Avec fenêtre de dates -> buzz agrégé sur la période ; sinon -> trending live.
    params = {"limit": limit}
    if dfrom and dto:
        params.update({"from": dfrom, "to": dto})
    else:
        params["type"] = "stock"
    try:
        r = requests.get(f"{X_BASE}/v1/trending", params=params, headers=_headers(), timeout=25)
        if r.status_code != 200:
            print(f"  x_trending HTTP {r.status_code}", file=sys.stderr)
            return []
        js = r.json()
        return js if isinstance(js, list) else js.get("results", [])
    except Exception as e:
        print(f"  x_trending err {e}", file=sys.stderr)
        return []
