#!/usr/bin/env python3
"""
Screener de dislocation multi-signaux — moteur de décision (couche 4, SPEC v1.3).

Ce paquet implémente la partie déterministe et testable sans flux payant :
le socle S1-S5, les cinq routes (A..E), le score de réfutation, la conviction
et le sizing par multiplicateur, la couche PATH (archétypes + stabilisation +
entrée par tranches) et les règles de sortie codées en dur.

Les couches d'ingestion / de détection amont (prix, fondamentaux, news LLM,
options, marchés prédictifs) sont hors de ce paquet : elles alimentent un
`Candidate` (voir `models.py`), que ce moteur consomme.

Point d'entrée : `screener.engine.evaluate_candidate`.
"""
from __future__ import annotations

from .engine import EvaluationResult, evaluate_candidate
from .models import Candidate, Catalyst

__all__ = ["Candidate", "Catalyst", "EvaluationResult", "evaluate_candidate"]
