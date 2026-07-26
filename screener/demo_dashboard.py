#!/usr/bin/env python3
"""
Démo du tableau de bord — short-list peuplée sur les cinq routes (§7.1).
=======================================================================
Construit six candidats illustrant chacune des routes A-E (plus un titre non
admis, pour montrer la colonne « surveiller »), les évalue via le moteur de
décision, et rend le tableau de bord interactif (`output/dashboard.py`).

Les cas s'inspirent des exemples canoniques de la SPEC : IBM (B, sur-réaction),
2CRSi/Grizzly (C, réfutation), Abivax (D, binaire daté), une small-cap décotée
EU (E), et une dislocation technique de rebalancement d'indice (A).

    python -m screener.demo_dashboard            # -> demo_dashboard.html
    python -m screener.demo_dashboard --out x.html
"""
from __future__ import annotations

import argparse
import os
from typing import List

from .engine import EvaluationResult, evaluate_candidate
from .models import (
    Archetype, Candidate, Catalyst, CatalystPower, CauseClass, DateCertainty,
    Permanence, Region,
)
from .detection.stabilization import StabFeatures


# --------------------------------------------------------------------------- #
# Barres synthétiques : un choc à J-`dsc` pour piloter l'archétype PATH.       #
# --------------------------------------------------------------------------- #
def _bars_with_shock(dsc: int, price: float, kind: str = "recover"):
    """Fabrique des barres closes avec un choc net, pour classifier l'archétype."""
    from .detection.path_archetype import PriceBar

    n = 60
    shock_idx = n - 1 - dsc
    out: List[PriceBar] = []
    base = price * 1.25
    for i in range(n):
        d = f"2026-{5 + i // 30:02d}-{1 + i % 30:02d}"
        if i < shock_idx:
            px = base
            o = h = l = c = px
            v = 1_000_000
        elif i == shock_idx:
            o = base
            c = base * 0.80          # gap/capitulation -20 %
            l = base * 0.78
            h = base
            v = 5_000_000
        else:
            # trajectoire post-choc
            floor = base * 0.80
            if kind == "recover":
                c = floor * (1 + 0.01 * (i - shock_idx))
            elif kind == "grind":
                c = floor * (1 - 0.01 * (i - shock_idx))
            else:
                c = floor
            o = c
            h = c * 1.01
            l = c * 0.99
            v = 1_500_000
        out.append(PriceBar(date=d, open=o, high=h, low=l, close=c, volume=v))
    return out, shock_idx


def _stab(good: bool) -> StabFeatures:
    """Features de stabilisation — un profil « stabilisé » vs « encore vendeur »."""
    if good:
        return StabFeatures(volume_contraction_ratio=0.45, closes_high_in_range=3,
                            no_new_low_3d=True, vwap_anchored_reconquered=True,
                            first_higher_low=True, rv_iv_converging=True)
    return StabFeatures(volume_contraction_ratio=0.9, closes_high_in_range=0,
                        no_new_low_3d=False, vwap_anchored_reconquered=False,
                        first_higher_low=False, rv_iv_converging=False)


# --------------------------------------------------------------------------- #
# Six candidats illustrant les routes.                                         #
# --------------------------------------------------------------------------- #
def _route_b() -> Candidate:
    """IBM-like : sur-réaction à une news, titre NON décoté (§7.1 B)."""
    return Candidate(
        ticker="IBM", name="International Business Machines", sector="Technologie",
        place="NYSE", region=Region.US, market_cap=95_000e6, price=142.0,
        adv_20d=650e6, analyst_coverage=22, listing_age_days=12000,
        target_position_value=900e6,
        expected_resolution_days=22,
        hard_stop_distance=0.11, hard_stop_level_motivated=True,
        dis=3.4, z_res=-3.6, z_volume=4.1, days_since_shock=2,
        cause_class=CauseClass.SECTOR_CONTAGION, permanence=Permanence.TRANSITORY,
        capi_effacee=24_000e6, impact_flux_actualise=3_500e6,   # ratio ~6.9x
        net_debt_ebitda=2.2, recovery_precedents=3,
        aqs=1.6, insider_buy=True,
        short_interest_pct=0.031, borrow_fee=0.008, days_to_cover=1.4,
        analysis="Décrochage de -20 % sur une déception de guidance non structurelle "
                 "(contagion du secteur software). La capitalisation effacée (24 Md$) "
                 "dépasse de ~7× l'impact de flux actualisé estimé (3,5 Md$) : "
                 "sur-réaction caractérisée. Bilan sain, 3 précédents récupérés. "
                 "Risque principal : révision de guidance confirmée comme structurelle "
                 "aux résultats.",
    )


def _route_c() -> Candidate:
    """2CRSi / Grizzly : réfutation d'une attaque de vendeur à découvert (§7.1 C)."""
    return Candidate(
        ticker="2CRSI.PA", name="2CRSi SA", sector="Technologie",
        place="Euronext Paris", region=Region.EU, market_cap=320e6, price=5.10,
        adv_20d=2.4e6, analyst_coverage=4, listing_age_days=2400,
        target_position_value=3.0e6,
        expected_resolution_days=18,
        allow_reduced_size_fallback=True,   # taille réduite → 0.5x (queue gauche)
        dis=3.1, z_res=-3.3, z_volume=3.6, days_since_shock=3,
        cause_class=CauseClass.SHORT_REPORT, permanence=Permanence.TRANSITORY,
        rebut_score=7.5, response_date_days=12, dilution_or_urgent_refi=False,
        aqs=0.8, insider_buy=True,
        short_interest_pct=0.145, borrow_fee=0.09, float_utilization=0.93,
        borrow_jump_bps_3d=260, days_to_cover=4.8,
        social_buzz_z=3.4, social_sentiment=-0.42, social_mentions=1850,
        social_trend="PIC",
        analysis="Attaque d'un vendeur à découvert (rapport Grizzly) sur des "
                 "allégations comptables. La direction a annoncé un audit "
                 "indépendant rendu sous 12 jours — c'est le catalyseur daté. "
                 "REBUT 7,5/10 : réponse point par point crédible, achats d'initiés "
                 "post-attaque. Pic de buzz retail (z=3,4) → prudence sur la cause. "
                 "Taille plafonnée à 0,5× (queue gauche épaisse).",
    )


def _route_d() -> Candidate:
    """Abivax-like : binaire daté (readout clinique), sans décrochage préalable (§7.1 D)."""
    cat = Catalyst(
        catalyst_type="Readout Phase 3 (ABX464)", date_expected="2026-08-28",
        date_certainty=DateCertainty.SEMI_CERTAIN, days_to_catalyst=24,
        binary=True, expected_move_pct=45.0, power=CatalystPower.VERY_HIGH,
        source_url="https://clinicaltrials.gov/study/NCT00000000",
    )
    return Candidate(
        ticker="ABVX", name="Abivax SA", sector="Santé", place="Euronext Paris",
        region=Region.EU, market_cap=1_200e6, price=9.80,
        adv_20d=8e6, analyst_coverage=7, listing_age_days=2600,
        target_position_value=6.0e6,
        expected_resolution_days=26,
        option_structure_capped=True,       # véhicule optionnel → perte plafonnée
        dis=1.2, z_res=-0.8, z_volume=1.1, days_since_shock=None,
        catalyst=cat, pms=0.28, upside_thesis_pct=90.0, scenario_documented=True,
        pred_market_liquidity=120_000,
        aqs=1.9, insider_buy=False,
        analysis="Readout de Phase 3 attendu ~28/08 sur l'ABX464 (colite ulcéreuse). "
                 "Différentiel de probabilité : le marché prédictif implique ~55 % de "
                 "succès contre ~70 % dans la thèse (écart 0,28). Mouvement implicite "
                 "±45 %, upside thèse +90 % (ratio 2×). Position via calls pour "
                 "plafonner la perte au binaire. Risque : échec du critère primaire.",
    )


def _route_e() -> Candidate:
    """Small-cap EU décotée avec catalyseur daté — la route « classique » (§7.1 E)."""
    cat = Catalyst(
        catalyst_type="Journée investisseurs + cession d'actif", date_expected="2026-09-05",
        date_certainty=DateCertainty.CERTAIN, days_to_catalyst=32,
        binary=False, expected_move_pct=15.0, power=CatalystPower.HIGH,
        source_url="https://ir.example.com/cmd",
    )
    return Candidate(
        ticker="SESG.PA", name="SES-imagotag (VusionGroup)", sector="Industrie",
        place="Euronext Paris", region=Region.EU, market_cap=1_600e6, price=118.0,
        adv_20d=12e6, analyst_coverage=9, listing_age_days=4200,
        target_position_value=9.0e6,
        expected_resolution_days=34,
        fv_low=152.0, fv_mid=178.0, fv_high=205.0,   # coussin ~ +29 %
        dis=2.6, z_res=-2.7, z_volume=3.2, days_since_shock=4,
        cause_class=CauseClass.RUMOR_UNCONFIRMED, permanence=Permanence.TRANSITORY,
        catalyst=cat, val_z=1.8, value_trap=False,
        aqs=1.3, insider_buy=True,
        short_interest_pct=0.052, borrow_fee=0.015, days_to_cover=2.1,
        analysis="Décote de ~29 % sur la borne basse de FV après une rumeur non "
                 "confirmée sur un client. VAL_z 1,8 (décoté sans être un piège de "
                 "valeur), catalyseur daté au 05/09 (journée investisseurs + cession "
                 "d'actif qui matérialise la valeur). Support analystes (AQS 1,3) et "
                 "achats d'initiés. Convergence attendue sous 34 séances.",
    )


def _route_a() -> Candidate:
    """Rebalancement d'indice / liquidation forcée — dislocation purement technique (§7.1 A)."""
    return Candidate(
        ticker="RILY", name="B. Riley Financial", sector="Finance", place="Nasdaq",
        region=Region.US, market_cap=280e6, price=18.4,
        adv_20d=15e6, analyst_coverage=5, listing_age_days=3500,
        target_position_value=4.0e6,
        expected_resolution_days=12,
        hard_stop_distance=0.10, hard_stop_level_motivated=True,
        dis=3.6, z_res=-3.9, z_volume=4.6, days_since_shock=1,
        cause_class=CauseClass.INDEX_REBALANCE, permanence=Permanence.TRANSITORY,
        negative_filing_72h=False,
        aqs=None, insider_buy=False,
        short_interest_pct=0.088, borrow_fee=0.022, days_to_cover=3.2,
        analysis="Sortie d'un indice small-cap → vente mécanique concentrée sur une "
                 "séance (z-volume +4,6σ, résidu -3,9σ). Aucun dépôt réglementaire "
                 "négatif à 72 h : le déséquilibre est un flux, pas une information. "
                 "Résolution mécanique attendue à ~12 séances une fois le rebalancement "
                 "digéré. Risque : nouvelle jambe vendeuse si un fonds reste à liquider.",
    )


def _not_admitted() -> Candidate:
    """Décrochage de prix SANS raison qualifiée → échoue S3, reste en surveillance."""
    return Candidate(
        ticker="XYZ", name="Exemple non qualifié", sector="Consommation",
        place="NYSE", region=Region.US, market_cap=900e6, price=24.0,
        adv_20d=20e6, analyst_coverage=8, listing_age_days=5000,
        target_position_value=5.0e6,
        expected_resolution_days=None,        # pas d'horizon → S3 échoue
        dis=2.9, z_res=-3.0, z_volume=3.4, days_since_shock=2,
        analysis="Décrochage technique net (-3,0σ) mais aucune raison de trader "
                 "identifiée : ni catalyseur daté, ni réfutation, ni décote établie, "
                 "ni classification de news. Un décrochage de prix n'est pas un "
                 "dossier — reste en surveillance jusqu'à qualification.",
    )


def build_results() -> List[EvaluationResult]:
    specs = [
        (_route_b(), 2, "grind", True),
        (_route_c(), 3, "recover", True),
        (_route_d(), None, None, False),      # pas de choc → pas d'archétype
        (_route_e(), 4, "recover", True),
        (_route_a(), 1, "flat", False),       # J-1, vendeur peut-être encore actif
        (_not_admitted(), 2, "grind", False),
    ]
    results: List[EvaluationResult] = []
    for cand, dsc, kind, stab_good in specs:
        if dsc is None:
            res = evaluate_candidate(cand, standard_size=1.0)
        else:
            bars, shock_idx = _bars_with_shock(dsc, cand.price, kind)
            res = evaluate_candidate(cand, standard_size=1.0, bars=bars,
                                     shock_idx=shock_idx, stab_features=_stab(stab_good))
        results.append(res)
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Démo — tableau de bord short-list")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__),
                                                  "..", "demo_dashboard.html"))
    args = ap.parse_args(argv)

    from .output.dashboard import render_html
    results = build_results()
    html = render_html(results, as_of="2026-07-24", universe="démo · routes A-E",
                       standalone=True)
    out = os.path.abspath(args.out)
    with open(out, "w") as f:
        f.write(html)

    n_adm = sum(1 for r in results if r.admitted)
    print(f"{len(results)} candidats · {n_adm} admis")
    for r in results:
        routes = "".join(rt.value for rt in r.route_labels) or "-"
        print(f"  {r.candidate.ticker:<10} admis={'✓' if r.admitted else '·'} "
              f"routes={routes:<4} conv={min(r.conviction.conv,10):.1f} "
              f"taille={r.sizing.final_size:.2f}×")
    print(f"\n-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
