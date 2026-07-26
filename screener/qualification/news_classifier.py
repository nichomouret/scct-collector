#!/usr/bin/env python3
"""
Classification de news (LLM) — couche 3, §5.3 (P4, « premier livrable quotidien »).
==================================================================================
Pour chaque candidat, récupère news/filings des 72h et les classe en une sortie
structurée : `cause_class`, `permanence`, `expected_resolution_days`, etc. Ce
sont exactement les champs qui gouvernent le socle S3 (horizon) et les routes
A/B/E — et qui, sans cette couche, doivent être saisis à la main dans l'overlay.

Modèle : SPEC §8 spécifie explicitement « Sonnet pour le volume, Opus pour la
synthèse de fiche ». La classification est du volume → défaut `claude-sonnet-5`
(surchargé par `SCREENER_LLM_MODEL`). Sortie forcée en JSON via
`output_config.format` (schéma strict).

⚠️ GARDE ANTI-FUITE LLM (§10.3) : une feature issue d'un LLM sur données
historiques est suspecte — le modèle SAIT ce qui s'est passé. Cette couche est
réservée au live / forward. Sur la période de test d'un backtest, n'utiliser
que des features déterministes, ou un modèle à cutoff antérieur à t. NE PAS
brancher ce classifieur dans `backtest/`.

Sans clé `ANTHROPIC_API_KEY` ni SDK `anthropic`, `classify_news` renvoie None et
la chaîne retombe sur l'overlay manuel.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import List, Optional

from ..models import CauseClass, Permanence
from ..ingestion.news import NewsItem

# Classes de cause éligibles au gating (§5.3) : dislocation non fondamentalement
# permanente. `permanence == TRANSITORY` élargit l'admission au-delà de cette liste.
GATING_CAUSES = frozenset({
    CauseClass.RUMOR_UNCONFIRMED, CauseClass.SECTOR_CONTAGION,
    CauseClass.INDEX_REBALANCE, CauseClass.LOCKUP_EXPIRY,
    CauseClass.FORCED_SELLING, CauseClass.NO_IDENTIFIED_CAUSE,
})
RESOLUTION_MAX_DAYS = 40   # §5.3 condition 2

_SOURCE_RELIABILITY = ["PRIMARY_FILING", "TIER1_MEDIA", "TIER2_MEDIA", "SOCIAL", "ANONYMOUS"]
_DEFAULT_MODEL = os.getenv("SCREENER_LLM_MODEL", "claude-sonnet-5")

# Schéma de sortie structurée (§5.3), strict.
_SCHEMA = {
    "type": "object",
    "properties": {
        "cause_class": {"type": "string", "enum": [c.value for c in CauseClass]},
        "permanence": {"type": "string", "enum": [p.value for p in Permanence]},
        "expected_resolution_days": {"type": "integer"},
        "cash_flow_impact_pct": {"type": "number"},
        "source_reliability": {"type": "string", "enum": _SOURCE_RELIABILITY},
        "confidence": {"type": "number"},
        "evidence_urls": {"type": "array", "items": {"type": "string"}},
        "analysis": {"type": "string"},
    },
    "required": ["cause_class", "permanence", "expected_resolution_days",
                 "cash_flow_impact_pct", "source_reliability", "confidence",
                 "evidence_urls", "analysis"],
    "additionalProperties": False,
}

_SYSTEM = (
    "Tu es un analyste actions spécialisé en situations spéciales et event-driven. "
    "On te donne les news/communiqués récents (72h) sur un titre qui vient de "
    "décrocher. (1) Classe la CAUSE du décrochage et estime si la dislocation est "
    "transitoire et à quel horizon (en séances) elle se résout. (2) Rédige une "
    "ANALYSE concise (2-3 phrases, français) : ce qui s'est passé, pourquoi c'est "
    "— ou n'est pas — une dislocation exploitable à horizon court, et le principal "
    "risque d'invalidation. Sois calibré et factuel : réponds UNKNOWN / "
    "NO_IDENTIFIED_CAUSE plutôt que d'inventer une cause, et dis-le si les news ne "
    "montrent pas de dislocation. N'utilise que l'information fournie ; ne suppose "
    "aucune connaissance postérieure."
)


@dataclass
class NewsClassification:
    cause_class: Optional[CauseClass]
    permanence: Permanence
    expected_resolution_days: Optional[int]
    cash_flow_impact_pct: Optional[float]
    source_reliability: str
    confidence: float
    evidence_urls: List[str] = field(default_factory=list)
    analysis: str = ""          # analyse rédigée par Claude (2-3 phrases)

    def as_overlay_row(self, ticker: str) -> dict:
        """Ligne au format overlay consommé par `screener.run`."""
        return {
            "ticker": ticker.upper(),
            "cause_class": self.cause_class.value if self.cause_class else "",
            "permanence": self.permanence.value,
            "expected_resolution_days": ("" if self.expected_resolution_days is None
                                         else self.expected_resolution_days),
            "cash_flow_impact_pct": ("" if self.cash_flow_impact_pct is None
                                     else self.cash_flow_impact_pct),
            "source_reliability": self.source_reliability,
            "confidence": self.confidence,
            "evidence_url": self.evidence_urls[0] if self.evidence_urls else "",
            "analysis": self.analysis,
        }


def _enum(cls, v, default=None):
    try:
        return cls(str(v).strip().upper())
    except (ValueError, AttributeError):
        return default


def parse_classification(data: dict) -> NewsClassification:
    """Fonction pure : convertit le JSON du modèle en NewsClassification (testable)."""
    def num(key):
        v = data.get(key)
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    res = data.get("expected_resolution_days")
    try:
        res = int(res) if res is not None else None
    except (TypeError, ValueError):
        res = None

    return NewsClassification(
        cause_class=_enum(CauseClass, data.get("cause_class")),
        permanence=_enum(Permanence, data.get("permanence"), Permanence.UNKNOWN),
        expected_resolution_days=res,
        cash_flow_impact_pct=num("cash_flow_impact_pct"),
        source_reliability=str(data.get("source_reliability") or "TIER2_MEDIA"),
        confidence=num("confidence") or 0.0,
        evidence_urls=list(data.get("evidence_urls") or []),
        analysis=str(data.get("analysis") or "").strip(),
    )


def passes_gating(c: NewsClassification) -> bool:
    """
    Gating v1.1 (§5.3), deux conditions cumulatives :
      1. cause_class ∈ GATING_CAUSES  OU  permanence == TRANSITORY
      2. expected_resolution_days ≤ 40
    """
    cond1 = (c.cause_class in GATING_CAUSES) or (c.permanence is Permanence.TRANSITORY)
    cond2 = (c.expected_resolution_days is not None
             and c.expected_resolution_days <= RESOLUTION_MAX_DAYS)
    return cond1 and cond2


def _build_user_prompt(ticker: str, name: str, items: List[NewsItem]) -> str:
    lines = [f"Titre : {name} ({ticker})", "", "News des 72 dernières heures :"]
    if not items:
        lines.append("  (aucune news trouvée)")
    for it in items[:20]:
        lines.append(f"- {it.as_evidence()}")
        if it.description:
            lines.append(f"    {it.description}")
    lines.append("")
    lines.append("Classe la cause et estime la permanence et l'horizon de résolution.")
    return "\n".join(lines)


def classify_news(ticker: str, name: str, items: List[NewsItem],
                  model: Optional[str] = None, api_key: Optional[str] = None,
                  max_tokens: int = 1024) -> Optional[NewsClassification]:
    """
    Appelle Claude en sortie structurée. Renvoie None si le SDK `anthropic` ou la
    clé sont absents (dégradation gracieuse → overlay manuel).
    """
    api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return None
    try:
        import anthropic
    except ImportError:
        return None

    client = anthropic.Anthropic(api_key=api_key)
    try:
        resp = client.messages.create(
            model=model or _DEFAULT_MODEL,
            max_tokens=max_tokens,
            system=_SYSTEM,
            output_config={"effort": "low",
                           "format": {"type": "json_schema", "schema": _SCHEMA}},
            messages=[{"role": "user",
                       "content": _build_user_prompt(ticker, name, items)}],
        )
    except Exception:  # noqa: BLE001 — dégradation gracieuse (réseau, quota, refus)
        return None

    if getattr(resp, "stop_reason", None) == "refusal":
        return None
    text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), None)
    if not text:
        return None
    try:
        return parse_classification(json.loads(text))
    except (json.JSONDecodeError, TypeError):
        return None
