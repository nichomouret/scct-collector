#!/usr/bin/env python3
"""
AMF · ORCHESTRATEUR multi-agents
================================
Enchaîne les 4 agents et produit amf_latest.json (lu par le dashboard) :

  Agent 1 (amf_bdif)   -> franchissements de seuils du jour
  Agent 3 (amf_shorts) -> positions courtes nettes du jour
  Agents 2 & 4 (amf_market) -> enrichissement par titre (technique / analystes /
                               social), mémoïsé par ISIN
  amf_summary          -> résumé du contenu de chaque document (Claude ou repli)

Planification : passe quotidienne dans la fenêtre 16h30–17h00 (Europe/Paris).
Le conteneur tournant en continu (Railway), l'orchestrateur dort jusqu'au prochain
créneau puis relance. Un passage immédiat au démarrage peuple le dashboard.

Variables d'environnement :
  AMF_RUN_ONCE=1        un seul passage puis sortie (test / cron externe)
  AMF_RUN_HOUR=16       heure de déclenchement (défaut 16, Europe/Paris)
  AMF_RUN_MINUTE=45     minute (défaut 45 ; fenêtre AMF 16h30–17h00)
  AMF_LOOKBACK_DAYS=1   profondeur de la fenêtre (défaut 1 = aujourd'hui)
  AMF_MAX_DOCS=60       plafond de PDF franchissements parsés
  AMF_TD_SLEEP=8        pause (s) entre titres pour respecter le quota TwelveData
  AMF_OUT=amf_latest.json   chemin de sortie
  (+ celles des agents : TWELVEDATA_API_KEY, ANTHROPIC_API_KEY, AMF_SUMMARIES…)

Usage :
  python amf_orchestrator.py --once            # un passage
  python amf_orchestrator.py --date 2026-08-04 # date précise, un passage
  python amf_orchestrator.py                   # boucle planifiée
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import os
import sys
import time

try:
    from zoneinfo import ZoneInfo
    PARIS = ZoneInfo("Europe/Paris")
except Exception:  # noqa: BLE001 — repli si tzdata absent
    PARIS = None

import amf_bdif
import amf_shorts
import amf_market
import amf_summary

HERE = os.path.dirname(os.path.abspath(__file__))


def _log(msg: str):
    print(f"[amf] {msg}", flush=True)


def _paris_now() -> dt.datetime:
    if PARIS:
        return dt.datetime.now(PARIS)
    return dt.datetime.utcnow() + dt.timedelta(hours=2)  # approx été


def _widen_if_empty(collect_fn, target: dt.date, lookback: int, max_widen: int = 5):
    """Essaie la fenêtre [target-lookback+1, target] ; si vide, élargit jusqu'à
    trouver la publication la plus récente (utile week-end / jours fériés)."""
    deb = target - dt.timedelta(days=lookback - 1)
    recs = collect_fn(deb, target)
    widen = lookback
    while not recs and widen < max_widen + lookback:
        widen += 2
        deb = target - dt.timedelta(days=widen - 1)
        recs = collect_fn(deb, target)
    return recs, deb


def run_once(target: dt.date | None = None) -> dict:
    target = target or _paris_now().date()
    lookback = int(os.getenv("AMF_LOOKBACK_DAYS", "1"))
    max_docs = int(os.getenv("AMF_MAX_DOCS", "60"))
    td_sleep = float(os.getenv("AMF_TD_SLEEP", "8"))
    out_path = os.path.join(HERE, os.getenv("AMF_OUT", "amf_latest.json"))
    td_on = bool(os.getenv("TWELVEDATA_API_KEY", "").strip())

    _log(f"passage pour {target} (lookback={lookback}, TD={'on' if td_on else 'off'}, "
         f"Claude={'on' if amf_summary._enabled() else 'off'})")

    # --- Agent 1 : franchissements ---
    try:
        franch, deb_f = _widen_if_empty(
            lambda a, b: amf_bdif.collect(a, b, max_docs=max_docs), target, lookback)
    except Exception as e:  # noqa: BLE001
        _log(f"agent1 (franchissements) erreur : {e}")
        franch, deb_f = [], target

    # --- Agent 3 : positions courtes ---
    try:
        shorts, deb_s = _widen_if_empty(amf_shorts.collect, target, lookback)
    except Exception as e:  # noqa: BLE001
        _log(f"agent3 (positions courtes) erreur : {e}")
        shorts, deb_s = [], target

    _log(f"collecte : {len(franch)} franchissement(s), {len(shorts)} position(s) courte(s)")

    # --- Agents 2 & 4 : enrichissement par titre (une fois par ISIN) ---
    def _enrich_all(records, isin_key="isin", name_key="issuer"):
        for i, r in enumerate(records):
            isin = r.get(isin_key)
            name = r.get(name_key)
            already = (isin or name or "").upper() in amf_market._ENRICH_CACHE
            r["enrichment"] = amf_market.enrich(isin, name)
            if td_on and not already and i < len(records) - 1:
                time.sleep(td_sleep)  # quota TwelveData

    _enrich_all(franch)
    _enrich_all(shorts)

    # --- résumés ---
    for r in franch:
        r["summary"] = amf_summary.summarize_franchissement(r)
        r.pop("raw_text", None)  # allège le JSON servi
    for r in shorts:
        r["summary"] = amf_summary.summarize_short(r)

    payload = {
        "generated_utc": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "target_date": target.isoformat(),
        "window_franchissements": [deb_f.isoformat(), target.isoformat()],
        "window_positions": [deb_s.isoformat(), target.isoformat()],
        "counts": {"franchissements": len(franch), "positions_courtes": len(shorts)},
        "flags": {"twelvedata": td_on, "claude": amf_summary._enabled()},
        "franchissements": franch,
        "positions_courtes": shorts,
    }
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    os.replace(tmp, out_path)
    _log(f"écrit {out_path} ({len(franch)}+{len(shorts)} enregistrements)")
    return payload


def _seconds_until_next_run() -> float:
    hour = int(os.getenv("AMF_RUN_HOUR", "16"))
    minute = int(os.getenv("AMF_RUN_MINUTE", "45"))
    now = _paris_now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += dt.timedelta(days=1)
    # saute au lundi si le prochain créneau tombe le week-end
    while target.weekday() >= 5:  # 5=sam, 6=dim
        target += dt.timedelta(days=1)
    return (target - now).total_seconds()


def loop():
    # passage immédiat au démarrage pour peupler le dashboard
    try:
        run_once()
    except Exception as e:  # noqa: BLE001
        _log(f"passage initial en échec : {e}")
    while True:
        wait = _seconds_until_next_run()
        nxt = _paris_now() + dt.timedelta(seconds=wait)
        _log(f"prochain passage : {nxt:%Y-%m-%d %H:%M} (Paris), dans {wait/3600:.1f} h")
        time.sleep(max(60, wait))
        try:
            run_once()
        except Exception as e:  # noqa: BLE001
            _log(f"passage planifié en échec : {e}")


def _cli():
    ap = argparse.ArgumentParser(description="Orchestrateur multi-agents AMF")
    ap.add_argument("--once", action="store_true", help="un seul passage puis sortie")
    ap.add_argument("--date", help="date cible YYYY-MM-DD (implique --once)")
    args = ap.parse_args()

    if args.date:
        run_once(dt.date.fromisoformat(args.date))
    elif args.once or os.getenv("AMF_RUN_ONCE") in ("1", "true"):
        run_once()
    else:
        loop()


if __name__ == "__main__":
    _cli()
