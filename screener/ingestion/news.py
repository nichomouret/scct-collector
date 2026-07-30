#!/usr/bin/env python3
"""
Ingestion de news — couche 0 (§5.3, P4).
========================================
Récupère news et communiqués récents pour un candidat (fenêtre 72h, §5.3),
via NewsAPI (newsapi.org, clé `NEWS_API_KEY`). Ces items alimentent le
classifieur LLM (`qualification/news_classifier.py`) qui en dérive
`cause_class`, `permanence` et `expected_resolution_days`.

Sans `NEWS_API_KEY` (ou sans `requests`), renvoie une liste vide — la chaîne
continue et retombe sur l'overlay manuel.

POINT-IN-TIME (§10.2) : `from_date` / `to_date` bornent la fenêtre exactement.
Pour un backtest, passer les dates telles que connues à t (et voir la garde
anti-fuite LLM de §10.3 dans le classifieur).
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

_NEWSAPI = "https://newsapi.org/v2/everything"
# Yahoo Finance search — flux news SANS clé (même famille que les prix, donc même
# fraîcheur/fuseau). Source par défaut : NewsAPI gratuit est trop limité/instable.
_YAHOO_SEARCH = ("https://query1.finance.yahoo.com/v1/finance/search"
                 "?q={q}&newsCount={n}&quotesCount=0")
_UA = {"User-Agent": "Mozilla/5.0 (screener-dislocation)"}

# Désignations sociales à retirer du nom pour la requête : les articles emploient
# le nom d'usage court (« Norwegian Cruise Line »), pas le nom légal complet
# (« NORWEGIAN CRUISE LINE HOLDINGS LTD. »). Chercher le nom complet en phrase
# exacte ne ramène rien — cause n°1 des `NO_IDENTIFIED_CAUSE` à tort.
_NAME_SUFFIXES = {
    "CORP", "CORPORATION", "INC", "INCORPORATED", "LTD", "LIMITED", "LLC", "PLC",
    "CO", "COMPANY", "COS", "HOLDINGS", "HOLDING", "GROUP", "GRP", "SA", "NV",
    "AG", "ADR", "CLASS", "COM", "THE", "LP", "TRUST", "ENTERPRISES", "INDUSTRIES",
    "INTERNATIONAL", "WORLDWIDE", "TECHNOLOGIES", "SYSTEMS",
}


def clean_company_name(name: str) -> str:
    """Nom d'usage : retire ponctuation et désignations sociales en fin de nom.

    « NORWEGIAN CRUISE LINE HOLDINGS LTD. » -> « NORWEGIAN CRUISE LINE ».
    Conserve au moins le premier mot (ne vide jamais un nom d'une seule société)."""
    toks = re.sub(r"[.,/]", " ", name or "").split()
    while len(toks) > 1 and toks[-1].upper().strip(".") in _NAME_SUFFIXES:
        toks.pop()
    return " ".join(toks).strip()


def company_query(name: str, ticker: str) -> str:
    """Requête NewsAPI : nom d'usage en phrase exacte, sinon repli sur le ticker."""
    clean = clean_company_name(name)
    if clean and clean.upper() != ticker.upper():
        return f'"{clean}"'
    return ticker


@dataclass
class NewsItem:
    title: str
    description: str
    source: str
    published_at: str      # ISO
    url: str

    def as_evidence(self) -> str:
        return f"[{self.published_at}] {self.source} — {self.title} ({self.url})"


def _news_from_yahoo_payload(payload, cutoff_ts: Optional[float]) -> List[NewsItem]:
    """Convertit le bloc `news` d'une réponse Yahoo search en NewsItem (fonction pure)."""
    out: List[NewsItem] = []
    for a in (payload or {}).get("news", []) or []:
        t = a.get("providerPublishTime")
        if cutoff_ts is not None and t is not None and t < cutoff_ts:
            continue
        published = (datetime.fromtimestamp(t, tz=timezone.utc).isoformat()
                     if t else "")
        out.append(NewsItem(
            title=(a.get("title") or "").strip(),
            description="",                      # le search Yahoo ne fournit pas de résumé
            source=(a.get("publisher") or "").strip(),
            published_at=published,
            url=(a.get("link") or "").strip(),
        ))
    return out


def fetch_yahoo_news(ticker: str, days: int = 7, count: int = 10,
                     timeout: int = 25) -> List[NewsItem]:
    """News récentes d'un ticker via Yahoo Finance search — SANS clé, stdlib pure.

    Filtre sur les `days` derniers jours (fraîcheur §5.3). Renvoie [] en cas
    d'échec (dégradation gracieuse) — jamais d'exception qui bloque la chaîne."""
    url = _YAHOO_SEARCH.format(q=urllib.parse.quote(ticker), n=max(1, min(count, 20)))
    req = urllib.request.Request(url, headers=_UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = json.loads(r.read())
    except Exception:  # noqa: BLE001 — dégradation gracieuse
        return []
    cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=days)).timestamp()
    return _news_from_yahoo_payload(payload, cutoff)


def fetch_news(query: str, api_key: Optional[str] = None,
               from_date: Optional[date] = None, to_date: Optional[date] = None,
               page_size: int = 20, language: str = "en",
               timeout: int = 25) -> List[NewsItem]:
    """
    News récentes correspondant à `query` (nom de société et/ou ticker).
    Fenêtre par défaut : 3 derniers jours (fraîcheur 72h, §5.3).
    """
    api_key = api_key or os.getenv("NEWS_API_KEY", "")
    if not api_key:
        return []
    try:
        import requests
    except ImportError:
        return []

    to_date = to_date or date.today()
    from_date = from_date or (to_date - timedelta(days=3))
    params = {
        "q": query, "from": from_date.isoformat(), "to": to_date.isoformat(),
        "sortBy": "publishedAt", "language": language,
        "pageSize": max(1, min(page_size, 100)), "apiKey": api_key,
    }
    try:
        r = requests.get(_NEWSAPI, params=params, headers=_UA, timeout=timeout)
        if r.status_code != 200:
            return []
        data = r.json()
    except Exception:  # noqa: BLE001 — dégradation gracieuse
        return []

    out: List[NewsItem] = []
    for a in data.get("articles", []) or []:
        out.append(NewsItem(
            title=(a.get("title") or "").strip(),
            description=(a.get("description") or "").strip(),
            source=((a.get("source") or {}).get("name") or "").strip(),
            published_at=(a.get("publishedAt") or "").strip(),
            url=(a.get("url") or "").strip(),
        ))
    return out
