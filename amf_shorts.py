#!/usr/bin/env python3
"""
AMF · AGENT 3 — Positions courtes nettes (ventes à découvert)
=============================================================
Collecte les déclarations de positions courtes nettes publiées par l'AMF au
titre du règlement européen 236/2012 (short selling).

Source : fichier CSV officiel « open data » de l'AMF, mis à jour quotidiennement
et publié sur data.gouv.fr. L'URL du fichier est horodatée (change à chaque
publication) — on la résout dynamiquement via l'API data.gouv.

Colonnes du CSV (séparateur ';', UTF-8) :
    Detenteur de la position courte nette ; Legal Entity Identifier detenteur ;
    Emetteur / issuer ; Ratio ; code ISIN ; Date de debut position ;
    Date de debut de publication position ; Date de fin de publication position

⚠ « Ratio » = pourcentage du CAPITAL de l'émetteur (donnée réglementaire AMF),
   et non du flottant. On le libelle donc « % du capital ». Une ligne sans
   « Date de fin de publication position » correspond à une position toujours
   ouverte (active) ; une ligne datée en fin correspond à une clôture/révision.

Le flux quotidien = les lignes dont « Date de debut de publication position »
est égale à la date cible (nouvelles déclarations publiées ce jour-là).

Usage CLI (test) :
    python amf_shorts.py --date 2026-08-04
    python amf_shorts.py --days 3
"""
from __future__ import annotations
import argparse
import csv
import datetime as dt
import io
import json
import os
import sys
from typing import Optional

try:
    import requests
except ImportError:
    sys.exit("pip install requests")

DATASET = ("historique-des-positions-courtes-nettes-sur-actions-"
           "rendues-publiques-depuis-le-1er-novembre-2012")
DATAGOUV_API = f"https://www.data.gouv.fr/api/1/datasets/{DATASET}/"
UA = {"User-Agent": "Mozilla/5.0 (AMF-collector)"}
TIMEOUT = 60

COL_DET = "Detenteur de la position courte nette"
COL_LEI = "Legal Entity Identifier detenteur"
COL_EME = "Emetteur / issuer"
COL_RATIO = "Ratio"
COL_ISIN = "code ISIN"
COL_DEB = "Date de debut position"
COL_PUB = "Date de debut de publication position"
COL_FIN = "Date de fin de publication position"


def latest_csv_url() -> str:
    """Résout l'URL courante du CSV VAD via l'API data.gouv."""
    override = os.getenv("AMF_VAD_CSV_URL", "").strip()
    if override:
        return override
    r = requests.get(DATAGOUV_API, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    resources = r.json().get("resources", [])
    # ressource CSV « ventes-a-decouvert » (ignore les descriptions .pdf, etc.)
    csvs = [x for x in resources
            if (x.get("format") or "").lower() == "csv"
            and (x.get("url") or "").lower().endswith(".csv")]
    if not csvs:
        raise RuntimeError("Aucune ressource CSV trouvée dans le dataset data.gouv AMF/VAD")
    # préfère celle intitulée ventes-a-decouvert, sinon la plus récente
    csvs.sort(key=lambda x: (("ventes" not in (x.get("title") or "").lower()),
                             x.get("last_modified") or ""), reverse=False)
    for x in csvs:
        if "ventes" in (x.get("title") or "").lower():
            return x["url"]
    return csvs[-1]["url"]


def _download_rows(url: str) -> list[dict]:
    r = requests.get(url, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    text = r.content.decode("utf-8", errors="replace")
    return list(csv.DictReader(io.StringIO(text), delimiter=";"))


def _to_float(s: str) -> Optional[float]:
    try:
        return float((s or "").strip().replace(",", "."))
    except (ValueError, AttributeError):
        return None


def _record(row: dict, target: Optional[str]) -> dict:
    fin = (row.get(COL_FIN) or "").strip()
    pub = (row.get(COL_PUB) or "").strip()
    active = not fin
    return {
        "detenteur": (row.get(COL_DET) or "").strip(),
        "lei": (row.get(COL_LEI) or "").strip(),
        "issuer": (row.get(COL_EME) or "").strip(),
        "isin": (row.get(COL_ISIN) or "").strip(),
        "ratio": _to_float(row.get(COL_RATIO)),           # % du capital
        "date_position": (row.get(COL_DEB) or "").strip(),
        "date_publication": pub,
        "date_fin": fin or None,
        "active": active,
        "is_new": (pub == target) if target else False,
    }


def collect(date_debut: dt.date, date_fin: dt.date) -> list[dict]:
    """Nouvelles publications de positions courtes sur la fenêtre [debut, fin].

    Filtre les lignes dont la date de début de publication tombe dans la
    fenêtre. Tri par date de publication décroissante puis ratio décroissant.
    """
    url = latest_csv_url()
    rows = _download_rows(url)
    deb_s, fin_s = date_debut.isoformat(), date_fin.isoformat()
    out = []
    for row in rows:
        pub = (row.get(COL_PUB) or "").strip()
        if deb_s <= pub <= fin_s:
            out.append(_record(row, target=fin_s))
    out.sort(key=lambda r: (r["date_publication"], r["ratio"] or 0.0), reverse=True)
    return out


def active_positions(min_ratio: float = 0.0) -> list[dict]:
    """Toutes les positions courtes actuellement ouvertes (sans date de fin)."""
    url = latest_csv_url()
    rows = _download_rows(url)
    out = [_record(r, None) for r in rows if not (r.get(COL_FIN) or "").strip()]
    out = [r for r in out if (r["ratio"] or 0) >= min_ratio]
    out.sort(key=lambda r: r["ratio"] or 0.0, reverse=True)
    return out


def _cli():
    ap = argparse.ArgumentParser(description="Positions courtes nettes AMF (open data)")
    ap.add_argument("--days", type=int, default=2)
    ap.add_argument("--date", help="date unique YYYY-MM-DD")
    ap.add_argument("--active", action="store_true", help="lister les positions actives")
    args = ap.parse_args()

    if args.active:
        recs = active_positions(min_ratio=0.5)
        print(f"# {len(recs)} positions actives (>=0.5% du capital)", file=sys.stderr)
        for r in recs[:40]:
            print(f"{r['ratio']:>5}% | {r['issuer']:<28} | {r['detenteur']}")
        return

    if args.date:
        d = dt.date.fromisoformat(args.date)
        deb, fin = d, d
    else:
        fin = dt.date.today()
        deb = fin - dt.timedelta(days=args.days - 1)

    recs = collect(deb, fin)
    print(f"# {len(recs)} nouvelle(s) publication(s) sur {deb}..{fin}", file=sys.stderr)
    for r in recs:
        act = "actif" if r["active"] else f"clos {r['date_fin']}"
        print(f"{r['date_publication']} | {r['ratio']}% cap | {r['issuer']} "
              f"({r['isin']}) | {r['detenteur']} | {act}")
    if recs:
        print("\n--- JSON (premier) ---", file=sys.stderr)
        print(json.dumps(recs[0], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _cli()
