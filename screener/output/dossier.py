#!/usr/bin/env python3
"""
Fiche de sortie 1 page — §7.5 (format imposé).
==============================================
Objectif produit (§6.7) : comprimer le temps de décision de 3 heures à
60 secondes. La fiche rend une décision humaine possible d'un coup d'œil :
route(s), bornage de perte, chemin de prix, conviction, taille, catalyseur,
et surtout la section « ce qui invalide la thèse » — obligatoire.
"""
from __future__ import annotations

import textwrap
from typing import List

from ..engine import EvaluationResult
from ..ingestion.short_interest import BORROW_JUMP_BPS_THRESHOLD, FLOAT_UTIL_THRESHOLD
from ..models import LossBoundMechanism, Route

_ROUTE_NAME = {
    Route.A: "dislocation technique",
    Route.B: "sur-réaction",
    Route.C: "réfutation",
    Route.D: "binaire daté",
    Route.E: "décote + catalyseur",
}

_MECH_LABEL = {
    LossBoundMechanism.VALUATION_CUSHION: "(a) coussin de valorisation",
    LossBoundMechanism.HARD_STOP: "(b) stop dur",
    LossBoundMechanism.OPTION_STRUCTURE: "(c) structure optionnelle",
    LossBoundMechanism.REDUCED_SIZE: "(d) taille réduite seule",
    LossBoundMechanism.NONE: "AUCUN",
}


def _routes_line(res: EvaluationResult) -> str:
    if not res.routes:
        return "aucune"
    parts = [f"{r.route.value} ({_ROUTE_NAME[r.route]})" for r in res.routes]
    return " + ".join(parts) + f"   →  n={res.n_routes}, mult. {res.conviction.multiplier:.2f}×"


def render(res: EvaluationResult) -> str:
    c = res.candidate
    L: List[str] = []
    cap = c.market_cap / 1e6
    L.append(f"{c.ticker} · {c.name} · {c.sector} · {cap:.0f} M · {c.place}")
    L.append("─" * 51)
    L.append(f"ROUTE(S)        {_routes_line(res)}")

    factor = res.floor.size_factor
    mech = _MECH_LABEL[res.floor.mechanism]
    coussin = c.coussin
    coussin_str = f" {coussin:+.1%}" if (coussin is not None
                  and res.floor.mechanism is LossBoundMechanism.VALUATION_CUSHION) else ""
    L.append(f"BORNAGE PERTE   {mech}{coussin_str}   →  facteur {factor:.1f}×")

    p = res.path
    if p.archetype is not None:
        stab_str = f" · STAB {p.stab}/10" if p.stab is not None else ""
        L.append(f"CHEMIN DE PRIX  {p.archetype.value}{stab_str}")
        if p.entry_plan is not None:
            plan = p.entry_plan
            if plan.tranche1_ok:
                t2 = " tranche 2 armée" if plan.tranche2_armed else " tranche 2 en attente (STAB<6)"
                L.append(f"                → tranche 1 (50%) immédiate,{t2}")
            else:
                L.append(f"                → entrée bloquée : {plan.blocked_reason}")

    # CONV = base × multiplicateur peut dépasser 10 (superposition de routes) ;
    # la fiche l'affiche saturé à 10, le score brut restant pour le classement.
    L.append(f"CONVICTION      {min(res.conviction.conv, 10.0):.1f} / 10")
    sz = res.sizing
    cap_note = "  (plafonnée route C)" if sz.capped else ""
    L.append(f"TAILLE CIBLE    {sz.final_size:.2f} × taille standard{cap_note}")
    if c.expected_resolution_days is not None:
        L.append(f"HORIZON         {c.expected_resolution_days} séances (time-stop 40)")
    if c.price:
        fv = ""
        if c.fv_low is not None and c.fv_high is not None:
            fv = f"        Bande FV : {c.fv_low:.2f} – {c.fv_high:.2f}"
        L.append(f"COURS           {c.price:.2f}{fv}")
    if coussin is not None:
        L.append(f"COUSSIN         {coussin:+.1%}")

    # --- Catalyseur (section n°1) ---
    if c.catalyst is not None:
        cat = c.catalyst
        L.append("")
        L.append("CATALYSEUR                                    ← section n°1")
        L.append(f"  {cat.catalyst_type} · {cat.date_expected} · {cat.date_certainty.value}")
        if cat.expected_move_pct is not None:
            L.append(f"  Mouvement implicite (straddle) : ±{cat.expected_move_pct:.0f} %")
        if c.upside_thesis_pct is not None and cat.expected_move_pct:
            ratio = c.upside_thesis_pct / cat.expected_move_pct
            flag = "✓" if ratio > 1.5 else "✗"
            L.append(f"  Upside thèse : +{c.upside_thesis_pct:.0f} %   →  ratio {ratio:.1f}×  {flag}")

    # --- Analyse Claude (§5.3, §8) ---
    if c.analysis:
        L.append("")
        L.append("ANALYSE (Claude)")
        for line in textwrap.wrap(c.analysis, width=61):
            L.append(f"  {line}")

    # --- Pourquoi c'est une dislocation ---
    L.append("")
    L.append("POURQUOI C'EST UNE DISLOCATION")
    L.append(f"  Résidu factoriel : {c.z_res:+.1f}σ · Volume : {c.z_volume:+.1f}σ · DIS {c.dis:.2f}")
    if c.surreaction_ratio is not None:
        L.append(f"  Impact flux : {c.impact_flux_actualise:.0f} · Capi effacée : "
                 f"{c.capi_effacee:.0f} · Ratio : {c.surreaction_ratio:.1f}×")
    if c.cause_class is not None:
        res_str = f" · Résolution attendue : ~{c.expected_resolution_days} séances" \
                  if c.expected_resolution_days is not None else ""
        L.append(f"  Classe : {c.cause_class.value}{res_str}")

    # --- Confirmations indépendantes ---
    L.append("")
    L.append("CONFIRMATIONS INDÉPENDANTES")
    L.append(f"  {'✓' if c.aqs and c.aqs > 1.0 else '✗'} AQS = {c.aqs}")
    L.append(f"  {'✓' if c.insider_buy else '✗'} achat d'initié")
    L.append(f"  {'✓' if (c.pms is not None and abs(c.pms) >= 0.20) else '✗'} "
             f"différentiel marché prédictif (PMS = {c.pms})")
    if c.borrow_jump_bps_3d is not None:
        ok = c.borrow_jump_bps_3d >= BORROW_JUMP_BPS_THRESHOLD
        L.append(f"  {'✓' if ok else '✗'} saut du taux d'emprunt ({c.borrow_jump_bps_3d:+.0f} bps/3j)")
    if c.float_utilization is not None:
        ok = c.float_utilization >= FLOAT_UTIL_THRESHOLD
        L.append(f"  {'✓' if ok else '✗'} utilisation du float ({c.float_utilization:.0%})")

    # --- Ce qui invalide la thèse (obligatoire) ---
    L.append("")
    L.append("CE QUI INVALIDE LA THÈSE                      ← obligatoire")
    for inv in _invalidations(res):
        L.append(f"  • {inv}")

    # --- Positionnement & sortie ---
    L.append("")
    L.append("POSITIONNEMENT & SORTIE")
    si_bits = []
    if c.short_interest_pct is not None:
        si_bits.append(f"Short interest {c.short_interest_pct:.1%}")
    if c.float_utilization is not None:
        si_bits.append(f"Utilisation float {c.float_utilization:.0%}")
    if c.borrow_fee is not None:
        si_bits.append(f"Coût emprunt {c.borrow_fee:.1%}")
    if c.days_to_cover is not None:
        si_bits.append(f"DTC {c.days_to_cover:.1f}")
    if si_bits:
        L.append("  " + " · ".join(si_bits))
    if c.days_of_adv is not None:
        L.append(f"  Position cible : {c.target_position_value:.0f} = {c.days_of_adv:.1f} j d'ADV  "
                 f"{'✓' if c.days_of_adv <= 2 else '✗'}")
    L.append(f"  Time-stop : 40 séances (inconditionnel) · MAE : -15 %")
    return "\n".join(L)


def _invalidations(res: EvaluationResult) -> List[str]:
    """Points d'invalidation dérivés de la configuration — la fiche les impose."""
    c = res.candidate
    out: List[str] = []
    if c.catalyst is not None:
        out.append(f"Report du catalyseur au-delà de {c.catalyst.date_expected} → sortie immédiate")
    if Route.C in res.route_labels:
        out.append("Confirmation de l'attaque par une source primaire → sortie")
    if Route.A in res.route_labels or Route.B in res.route_labels:
        out.append("Nouveau plus bas sous le point de capitulation → stop structurel")
    out.append("Dépassement de -15 % (MAE) → stop dur inconditionnel")
    out.append("40 séances atteintes → time-stop, sans exception")
    return out
