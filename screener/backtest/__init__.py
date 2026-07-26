#!/usr/bin/env python3
"""
Backtest — protocole §10 (P6, le go/no-go chiffré).
===================================================
« Un screener non backtesté est un générateur d'opinions coûteuses » (§11).

PÉRIMÈTRE ASSUMÉ (v1). La garde anti-fuite LLM (§10.3) interdit d'utiliser le
classifieur de news dans un backtest — le modèle connaît le futur. Les routes
B/C/E, qui dépendent de `cause_class`, ne sont donc PAS backtestables tant qu'on
n'a pas d'inputs qualitatifs historiques déterministes ; la route D exige des
dates de catalyseurs archivées en point-in-time.

Ce que ce harnais backteste aujourd'hui : le SIGNAL DÉTERMINISTE — dislocation
résiduelle (§4) filtrée par la discipline PATH (§7.3) et sortie par les règles
codées en dur (§7.4). C'est un proxy de la route A (dislocation technique) et
la validation de la couche 2 + PATH. TOUTE la machinerie §10 (coûts réels,
embargo t+1, time-stop, walk-forward, placebo, décomposition, garde anti-fuite)
est construite ici et réutilisable telle quelle quand B/C/D/E auront leurs
inputs historiques.

Modules : costs · metrics · engine · leakage_guard · placebo.
Point d'entrée : `screener.run_backtest`.
"""
