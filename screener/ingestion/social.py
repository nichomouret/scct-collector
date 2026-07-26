#!/usr/bin/env python3
"""
Tendance sociale — Adanos (couche 0, contexte de qualification).
===============================================================
STATUT ARCHITECTURAL — à respecter strictement. La SPEC v1.1 a RETIRÉ le volet
social/squeeze du périmètre : ce screener détecte des dislocations
fondamentales, pas des meme-stocks. Le signal social n'entre donc JAMAIS dans le
scoring — ni `DIS` (§4.4), ni la conviction (§7.2), ni les routes, ni le socle.

Il joue exactement le rôle des marchés prédictifs (§5.2, « ajuste le régime, pas
le titre ») : un CONTEXTE de qualification. Deux usages :
  1. descriptif sur la fiche / la short-list (buzz, sentiment, tendance) ;
  2. indice de cause + drapeau de risque : un PIC de buzz pendant la baisse
     oriente vers une cause RUMOR / retail — un signal de PRUDENCE, pas
     d'admission. Il est fourni au classifieur LLM comme contexte (§5.3).

Conventions alignées sur `adanos_x.py` du dépôt (env `ADANOS_API_KEY`,
`ADANOS_X_BASE`, en-tête `X-API-Key`, endpoint `/v1/stock/{ticker}`). Sans clé,
`fetch_social` renvoie None (dégradation gracieuse). Parsing isolé en
`signal_from_payload` (pur, testable hors ligne).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from typing import Optional

_BASE = os.getenv("ADANOS_X_BASE", "https://api.adanos.org/x/stocks")
_UA = {"User-Agent": "screener-dislocation"}

# Seuils (points de départ, à calibrer selon le plan Adanos — cf. ortex_c4 « ADAPTER »).
BUZZ_SPIKE_Z = 3.0        # z-score de mentions au-delà duquel c'est un « pic »
BUZZ_HIGH_Z = 1.5


@dataclass
class SocialSignal:
    ticker: str
    buzz_z: Optional[float] = None       # z-score du volume de mentions
    sentiment: Optional[float] = None    # -1..1
    sentiment_label: str = ""
    mentions: Optional[int] = None
    as_of: str = ""

    @property
    def trend(self) -> str:
        """Étiquette de tendance : PIC / élevé / calme (ou vide si inconnu)."""
        if self.buzz_z is None:
            return ""
        if self.buzz_z >= BUZZ_SPIKE_Z:
            return "PIC"
        if self.buzz_z >= BUZZ_HIGH_Z:
            return "élevé"
        return "calme"

    @property
    def is_spike(self) -> bool:
        return self.buzz_z is not None and self.buzz_z >= BUZZ_SPIKE_Z

    def hint(self) -> str:
        """Phrase de contexte social passée au classifieur LLM (§5.3)."""
        if self.buzz_z is None and self.sentiment is None:
            return ""
        bits = []
        if self.trend:
            bits.append(f"buzz {self.trend.lower()}"
                        + (f" (z={self.buzz_z:.1f})" if self.buzz_z is not None else ""))
        if self.sentiment is not None:
            bits.append(f"sentiment {self.sentiment:+.2f}"
                        + (f" ({self.sentiment_label})" if self.sentiment_label else ""))
        if self.mentions is not None:
            bits.append(f"{self.mentions} mentions")
        note = " · ".join(bits)
        if self.is_spike:
            note += " — un pic de buzz pendant la baisse peut signaler une cause rumeur/retail"
        return note


def _num(v) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def signal_from_payload(ticker: str, payload: Optional[dict],
                        as_of: Optional[date] = None) -> SocialSignal:
    """
    Construit un SocialSignal depuis la réponse `/v1/stock/{ticker}` d'Adanos.
    Tolère plusieurs noms de champs (le schéma exact dépend du plan Adanos).
    Fonction pure — testable sans réseau.
    """
    as_of = as_of or date.today()
    sig = SocialSignal(ticker=ticker.upper(), as_of=as_of.isoformat())
    if not payload:
        return sig

    # buzz / z-score de mentions
    for k in ("mentions_z", "buzz_z", "z_score", "buzz"):
        v = _num(payload.get(k))
        if v is not None:
            sig.buzz_z = v
            break

    # mentions (volume)
    for k in ("mentions", "mention_count", "count"):
        v = payload.get(k)
        if isinstance(v, (int, float)):
            sig.mentions = int(v)
            break
        if isinstance(v, list):
            sig.mentions = len(v)
            break

    # sentiment : float direct, ou objet {score,label}
    s = payload.get("sentiment")
    if isinstance(s, dict):
        sig.sentiment = _num(s.get("score"))
        sig.sentiment_label = str(s.get("label") or "").strip()
    else:
        sig.sentiment = _num(s if s is not None else payload.get("sentiment_score"))
        sig.sentiment_label = str(payload.get("sentiment_label") or "").strip()
    return sig


def fetch_social(ticker: str, api_key: Optional[str] = None,
                 base: Optional[str] = None, as_of: Optional[date] = None,
                 timeout: int = 25) -> Optional[SocialSignal]:
    """Récupère la tendance sociale d'un ticker. None sans clé / SDK / au moindre échec."""
    api_key = api_key or os.getenv("ADANOS_API_KEY", "")
    base = base or _BASE
    if not api_key:
        return None
    try:
        import requests
    except ImportError:
        return None
    try:
        r = requests.get(f"{base}/v1/stock/{ticker}",
                         headers={"X-API-Key": api_key, **_UA}, timeout=timeout)
        if r.status_code != 200:
            return None
        return signal_from_payload(ticker, r.json(), as_of=as_of)
    except Exception:  # noqa: BLE001 — dégradation gracieuse
        return None
