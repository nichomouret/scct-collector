#!/usr/bin/env python3
"""
AMF · Résumés de documents (Claude API + repli déterministe)
============================================================
Génère un résumé en français du contenu d'une déclaration AMF (franchissement de
seuils ou position courte). Deux voies :

  1. Claude API (Anthropic Messages API) si ANTHROPIC_API_KEY est défini.
     Modèle configurable via AMF_SUMMARY_MODEL (défaut : Haiku, économique).
  2. Repli déterministe (gabarit à partir des champs extraits) si la clé est
     absente, si AMF_SUMMARIES=0, ou en cas d'erreur API. Le pipeline ne casse
     donc jamais faute de clé/quota.

On appelle l'API via `requests` (pas de SDK) pour rester cohérent avec le reste
du dépôt et éviter une dépendance lourde.
"""
from __future__ import annotations
import os
import sys
from typing import Optional

try:
    import requests
except ImportError:
    sys.exit("pip install requests")

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
TIMEOUT = 40


def _enabled() -> bool:
    return (os.getenv("AMF_SUMMARIES", "1") not in ("0", "false", "")
            and bool(os.getenv("ANTHROPIC_API_KEY", "").strip()))


def _call_claude(prompt: str, max_tokens: int = 180) -> Optional[str]:
    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not key:
        return None
    model = os.getenv("AMF_SUMMARY_MODEL", DEFAULT_MODEL)
    headers = {
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = {
        "model": model,
        "max_tokens": max_tokens,
        "system": ("Tu es analyste marché. Résume en français, factuel et concis "
                   "(1 à 2 phrases), une déclaration réglementaire AMF. Pas de "
                   "conseil d'investissement, pas de spéculation, uniquement les "
                   "faits déclarés."),
        "messages": [{"role": "user", "content": prompt}],
    }
    r = requests.post(ANTHROPIC_URL, headers=headers, json=body, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    parts = data.get("content") or []
    txt = " ".join(p.get("text", "") for p in parts if p.get("type") == "text")
    return txt.strip() or None


# --------------------------------------------------------------------------- #
# Franchissements de seuils
# --------------------------------------------------------------------------- #
def _fallback_franchissement(rec: dict) -> str:
    decl = rec.get("declarant") or "Un déclarant"
    issuer = rec.get("issuer") or "l'émetteur"
    sens = rec.get("sens")
    seuils = ", ".join(rec.get("seuils") or [])
    cap = rec.get("pct_capital")
    dv = rec.get("pct_droits_vote")
    when = rec.get("date_franchissement")
    bits = [f"{decl} a déclaré un franchissement"]
    if sens:
        bits.append(f"à la {sens}")
    if when:
        bits.append(f"le {when}")
    phrase = " ".join(bits) + f" de seuil(s) sur {issuer}"
    if seuils:
        phrase += f" (seuils : {seuils})"
    if cap is not None:
        phrase += f". Détention post-opération : {cap}% du capital"
        if dv is not None:
            phrase += f" et {dv}% des droits de vote"
    return phrase.strip().rstrip(".") + "."


def summarize_franchissement(rec: dict) -> str:
    if not _enabled():
        return _fallback_franchissement(rec)
    try:
        raw = (rec.get("raw_text") or "")[:3500]
        prompt = (
            "Résume cette déclaration de franchissement de seuils AMF. "
            "Indique : qui déclare (détenteur), sur quel titre, le sens (hausse/baisse), "
            "les seuils franchis, et le % de capital/droits de vote détenu après.\n\n"
            f"Champs extraits : déclarant={rec.get('declarant')}, "
            f"émetteur={rec.get('issuer')}, sens={rec.get('sens')}, "
            f"seuils={rec.get('seuils')}, capital={rec.get('pct_capital')}%, "
            f"droits_vote={rec.get('pct_droits_vote')}%.\n\n"
            f"Extrait du document :\n{raw}"
        )
        return _call_claude(prompt) or _fallback_franchissement(rec)
    except Exception:  # noqa: BLE001
        return _fallback_franchissement(rec)


# --------------------------------------------------------------------------- #
# Positions courtes
# --------------------------------------------------------------------------- #
def _fallback_short(rec: dict) -> str:
    det = rec.get("detenteur") or "Un détenteur"
    issuer = rec.get("issuer") or "l'émetteur"
    ratio = rec.get("ratio")
    pub = rec.get("date_publication")
    active = rec.get("active")
    phrase = f"{det} déclare une position courte nette"
    if ratio is not None:
        phrase += f" de {ratio}% du capital"
    phrase += f" sur {issuer}"
    if pub:
        phrase += f", publiée le {pub}"
    phrase += ", position active" if active else " (position clôturée/révisée)"
    return phrase.strip().rstrip(".") + "."


def summarize_short(rec: dict) -> str:
    if not _enabled():
        return _fallback_short(rec)
    try:
        prompt = (
            "Résume en une phrase cette déclaration de position courte nette (AMF, "
            "règlement short-selling 236/2012).\n\n"
            f"Détenteur={rec.get('detenteur')}, émetteur={rec.get('issuer')}, "
            f"ratio={rec.get('ratio')}% du capital, ISIN={rec.get('isin')}, "
            f"date de publication={rec.get('date_publication')}, "
            f"position active={rec.get('active')}."
        )
        return _call_claude(prompt, max_tokens=120) or _fallback_short(rec)
    except Exception:  # noqa: BLE001
        return _fallback_short(rec)


if __name__ == "__main__":
    demo_f = {"declarant": "BlackRock Inc.", "issuer": "VALEO", "sens": "hausse",
              "seuils": ["5%"], "pct_capital": 5.02, "pct_droits_vote": 4.45,
              "date_franchissement": "29 juillet 2026", "raw_text": ""}
    demo_s = {"detenteur": "CITADEL ADVISORS LLC", "issuer": "RENAULT",
              "ratio": 1.18, "isin": "FR0000131906",
              "date_publication": "2026-08-04", "active": True}
    print("Claude actif :", _enabled())
    print("FRANCH :", summarize_franchissement(demo_f))
    print("SHORT  :", summarize_short(demo_s))
