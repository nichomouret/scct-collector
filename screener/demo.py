#!/usr/bin/env python3
"""
Démo du moteur de décision : construit un candidat superposant les routes B et
D (le meilleur dossier que le système puisse produire, §7.1) et imprime la
fiche 1 page (§7.5).

    python -m screener.demo
"""
from __future__ import annotations

from .models import (
    Candidate, Catalyst, CatalystPower, CauseClass, DateCertainty, Permanence, Region,
)
from .engine import evaluate_candidate
from .output import dossier


def build_example() -> Candidate:
    cat = Catalyst(
        catalyst_type="Résultats S1", date_expected="2026-09-15",
        date_certainty=DateCertainty.CERTAIN, days_to_catalyst=28,
        binary=True, expected_move_pct=18.0, power=CatalystPower.HIGH,
        source_url="https://ir.example.com/agenda",
    )
    return Candidate(
        ticker="XXX.PA", name="Exemple SA", sector="Industrie", place="Euronext Paris",
        region=Region.EU, market_cap=1_800e6, price=12.40,
        adv_20d=6_000_000, analyst_coverage=6, listing_age_days=3000,
        target_position_value=8_400_000,          # ~1.4 j d'ADV
        expected_resolution_days=25,
        fv_low=15.20, fv_mid=17.50, fv_high=19.80,  # coussin +22.6 %
        # Détection
        dis=3.0, z_res=-3.1, z_volume=4.2, days_since_shock=2,
        cause_class=CauseClass.SECTOR_CONTAGION, permanence=Permanence.TRANSITORY,
        negative_filing_72h=False,
        # Route B — sur-réaction
        capi_effacee=400e6, impact_flux_actualise=60e6,  # ratio 6.7x
        net_debt_ebitda=1.8, recovery_precedents=2,
        # Route D — binaire daté
        catalyst=cat, pms=0.24, upside_thesis_pct=32.0, scenario_documented=True,
        # Confirmations
        aqs=1.4, insider_buy=True,
    )


def main() -> None:
    res = evaluate_candidate(build_example(), standard_size=1.0)
    print(dossier.render(res))
    print()
    print(f"[admis={res.admitted} · routes={[r.value for r in res.route_labels]} · "
          f"conviction={res.conviction.conv:.1f}/10 · taille={res.sizing.final_size:.2f}×]")


if __name__ == "__main__":
    main()
