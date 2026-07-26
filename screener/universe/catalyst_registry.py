#!/usr/bin/env python3
"""
Registre des catalyseurs — couche 1 (§3.2, §7.1 socle S3).
==========================================================
Agrège les `RawCatalyst` de tous les fournisseurs, calcule `days_to_catalyst`
en POINT-IN-TIME (§10.2 — contre une date de référence injectable, jamais la
date corrigée après coup), filtre la fenêtre d'éligibilité (≤ 45 jours, §3.1)
et sert des `models.Catalyst` prêts pour le moteur.

Sélection « meilleur catalyseur » par titre : le plus PROCHE dans la fenêtre
(c'est lui qui pilote l'horizon), départage par puissance décroissante.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional

from ..models import Catalyst
from ..ingestion.catalysts import RawCatalyst

CATALYST_MAX_DAYS = 45   # §3.1 catalyst_max_days (buffer de 15 séances avant le time-stop)

_POWER_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "VERY_HIGH": 3}


@dataclass
class CatalystRegistry:
    raws: List[RawCatalyst]

    def add(self, raw: RawCatalyst) -> None:
        self.raws.append(raw)

    def extend(self, raws: List[RawCatalyst]) -> None:
        self.raws.extend(raws)

    def all_for(self, ticker: str) -> List[RawCatalyst]:
        return [r for r in self.raws if r.ticker.upper() == ticker.upper()]

    def catalyst_for(self, ticker: str, as_of: Optional[date] = None,
                     max_days: int = CATALYST_MAX_DAYS) -> Optional[Catalyst]:
        """
        Meilleur catalyseur daté pour ce titre dans [0, max_days] à `as_of`.
        Renvoie un `models.Catalyst` (avec `days_to_catalyst` calculé) ou None.
        """
        as_of = as_of or date.today()
        eligible = []
        for r in self.all_for(ticker):
            try:
                dtc = (date.fromisoformat(r.date_expected) - as_of).days
            except ValueError:
                continue
            if 0 <= dtc <= max_days:
                eligible.append((dtc, r))
        if not eligible:
            return None
        # plus proche d'abord, puis puissance décroissante
        dtc, r = min(eligible, key=lambda t: (t[0], -_POWER_RANK.get(r_power(t[1]), 0)))
        return Catalyst(
            catalyst_type=r.catalyst_type, date_expected=r.date_expected,
            date_certainty=r.date_certainty, days_to_catalyst=dtc,
            binary=r.binary, expected_move_pct=r.expected_move_pct,
            power=r.power, source_url=r.source_url,
        )

    def to_rows(self, as_of: Optional[date] = None,
                max_days: int = CATALYST_MAX_DAYS) -> List[dict]:
        """
        Lignes normalisées (une par titre, le meilleur catalyseur) au format
        consommé par `screener.run` (`--catalysts`).
        """
        as_of = as_of or date.today()
        tickers = sorted({r.ticker.upper() for r in self.raws})
        rows = []
        for tk in tickers:
            cat = self.catalyst_for(tk, as_of=as_of, max_days=max_days)
            if cat is None:
                continue
            rows.append({
                "ticker": tk, "catalyst_type": cat.catalyst_type,
                "date_expected": cat.date_expected,
                "date_certainty": cat.date_certainty.value,
                "days_to_catalyst": cat.days_to_catalyst,
                "binary": "true" if cat.binary else "false",
                "expected_move_pct": "" if cat.expected_move_pct is None else cat.expected_move_pct,
                "power": cat.power.value, "source_url": cat.source_url,
            })
        return rows


def r_power(raw: RawCatalyst) -> str:
    return raw.power.value


def new_registry() -> CatalystRegistry:
    return CatalystRegistry(raws=[])
