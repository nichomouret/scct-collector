#!/usr/bin/env python3
"""
Ingestion de catalyseurs — module central de la couche 1 (§3.2, P1).
====================================================================
« Sans lui, la contrainte de 2 mois rend l'outil inutilisable » (§3.2). Le
registre de catalyseurs remonte en couche 1 : c'est un critère d'éligibilité
amont, pas un enrichissement de fin de chaîne.

Fournisseurs (tous gratuits, §9 Phase 1) :
  - ClinicalTrials.gov v2 (sans clé) : readouts d'essais — puissance TRÈS HAUTE,
    binaires, dates APPROXIMATIVES (elles glissent). Ce sont les cas route D
    (Abivax, Inventiva).
  - CSV manuel : résultats, réglementaire, rééquilibrages d'indice, lock-ups —
    dates CERTAINES, faciles à maintenir à la main ou depuis des calendriers.

L'architecture est extensible : ajouter openFDA / EDGAR lock-ups / calendriers
d'indices = ajouter une fonction qui renvoie des `RawCatalyst`.

POINT-IN-TIME (§10.2) : chaque `RawCatalyst` porte la date TELLE QU'ANNONCÉE et
un `as_of` de capture. Le calcul de `days_to_catalyst` se fait dans le registre
contre une date de référence injectable — jamais avec la date corrigée après coup.
"""
from __future__ import annotations

import csv
import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date
from typing import List, Optional

from ..models import CatalystPower, DateCertainty

_UA = {"User-Agent": "Mozilla/5.0"}
_CT_BASE = "https://clinicaltrials.gov/api/v2/studies"

# Statuts d'essai « à venir » (un readout futur, pas un essai clos).
_CT_ACTIVE = {"RECRUITING", "ACTIVE_NOT_RECRUITING", "NOT_YET_RECRUITING",
              "ENROLLING_BY_INVITATION"}
# Phases dont le readout meut réellement un cours.
_CT_PHASE_POWER = {
    "PHASE3": CatalystPower.VERY_HIGH,
    "PHASE2": CatalystPower.HIGH,
}


@dataclass
class RawCatalyst:
    ticker: str
    catalyst_type: str
    date_expected: str                 # ISO 'YYYY-MM-DD' (telle qu'annoncée à as_of)
    date_certainty: DateCertainty
    binary: bool
    power: CatalystPower
    source_url: str = ""
    expected_move_pct: Optional[float] = None
    as_of: str = ""                    # date de capture (traçabilité PIT)


class CatalystFetchError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# Parsing de dates                                                             #
# --------------------------------------------------------------------------- #
def parse_partial_date(s: Optional[str]) -> Optional[str]:
    """
    Normalise une date CT.gov ('YYYY-MM-DD' ou 'YYYY-MM') en ISO complète.
    Un mois seul est ancré au 15 (milieu de mois) — de toute façon APPROXIMATIVE.
    """
    if not s:
        return None
    parts = s.split("-")
    try:
        if len(parts) == 3:
            return date(int(parts[0]), int(parts[1]), int(parts[2])).isoformat()
        if len(parts) == 2:
            return date(int(parts[0]), int(parts[1]), 15).isoformat()
        if len(parts) == 1:
            return date(int(parts[0]), 6, 30).isoformat()
    except ValueError:
        return None
    return None


# --------------------------------------------------------------------------- #
# Fournisseur : ClinicalTrials.gov v2                                          #
# --------------------------------------------------------------------------- #
def clinicaltrials_catalysts(sponsor: str, ticker: str, as_of: Optional[date] = None,
                             page_size: int = 50, timeout: int = 25) -> List[RawCatalyst]:
    """
    Readouts à venir pour un sponsor. `as_of` filtre les dates de complétion
    primaire strictement futures (défaut : aujourd'hui).
    """
    as_of = as_of or date.today()
    q = urllib.parse.urlencode({
        "query.spons": sponsor,
        "filter.overallStatus": "|".join(sorted(_CT_ACTIVE)),
        "fields": "NCTId,BriefTitle,Phase,OverallStatus,PrimaryCompletionDate,DesignModule,StatusModule,IdentificationModule",
        "pageSize": str(page_size),
    })
    url = f"{_CT_BASE}?{q}"
    req = urllib.request.Request(url, headers=_UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = json.loads(r.read())
    except Exception as e:  # noqa: BLE001
        raise CatalystFetchError(f"CT.gov {sponsor}: {type(e).__name__}: {e}") from e

    out: List[RawCatalyst] = []
    for study in payload.get("studies", []):
        p = study.get("protocolSection", {})
        idm = p.get("identificationModule", {})
        design = p.get("designModule", {})
        status = p.get("statusModule", {})
        nct = idm.get("nctId", "")
        phases = design.get("phases", []) or []
        overall = status.get("overallStatus", "")
        if overall not in _CT_ACTIVE:
            continue
        power = next((_CT_PHASE_POWER[ph] for ph in phases if ph in _CT_PHASE_POWER), None)
        if power is None:                                # PHASE1 / non pertinent
            continue
        pcd = parse_partial_date(status.get("primaryCompletionDateStruct", {}).get("date"))
        if not pcd or date.fromisoformat(pcd) <= as_of:  # strictement futur
            continue
        out.append(RawCatalyst(
            ticker=ticker.upper(), catalyst_type="CLINICAL_READOUT",
            date_expected=pcd, date_certainty=DateCertainty.APPROXIMATE,
            binary=True, power=power,
            source_url=f"https://clinicaltrials.gov/study/{nct}",
            as_of=as_of.isoformat(),
        ))
    return out


# --------------------------------------------------------------------------- #
# Fournisseur : CSV manuel                                                     #
# --------------------------------------------------------------------------- #
def csv_catalysts(path: str, as_of: Optional[date] = None) -> List[RawCatalyst]:
    """
    Lit un CSV de catalyseurs datés à la main (résultats, réglementaire, indices).
    Colonnes : ticker, catalyst_type, date_expected, date_certainty, binary,
    expected_move_pct, power, source_url. `days_to_catalyst` éventuel est ignoré
    (recalculé PIT par le registre).
    """
    as_of = as_of or date.today()
    out: List[RawCatalyst] = []
    with open(path) as f:
        for row in csv.DictReader(f):
            tk = (row.get("ticker") or "").strip().upper()
            iso = parse_partial_date((row.get("date_expected") or "").strip())
            if not tk or not iso:
                continue
            try:
                certainty = DateCertainty(str(row.get("date_certainty", "CERTAIN")).upper())
            except ValueError:
                certainty = DateCertainty.CERTAIN
            try:
                power = CatalystPower(str(row.get("power", "MEDIUM")).upper())
            except ValueError:
                power = CatalystPower.MEDIUM
            em = row.get("expected_move_pct")
            out.append(RawCatalyst(
                ticker=tk, catalyst_type=(row.get("catalyst_type") or "CATALYST").strip(),
                date_expected=iso, date_certainty=certainty,
                binary=str(row.get("binary", "")).strip().lower() in ("1", "true", "yes", "oui"),
                power=power, source_url=(row.get("source_url") or "").strip(),
                expected_move_pct=float(em) if em not in (None, "", "NA") else None,
                as_of=as_of.isoformat(),
            ))
    return out
