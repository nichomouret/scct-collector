#!/usr/bin/env python3
"""
Scanner du jour — dislocations techniques fraîches (couche 2, §4).
=================================================================
Répond à « quels titres trader maintenant ? ». Pour chaque titre de l'univers,
calcule la détection sur la DERNIÈRE barre : s'il y a une dislocation résiduelle
fraîche (§4) non bloquée par la discipline PATH (§7.3.3), le titre entre dans la
short-list du jour, avec l'archétype, le stop structurel et la reco d'entrée.

Prix seuls → aucune clé API requise. C'est le FILTRE d'entrée (couche 2), pas le
dossier complet : le backtest montre que la dislocation technique seule ne bat pas
les seuils (§10.9). À confirmer par une raison de trader (catalyseur, réfutation,
décote, news) — les routes qualitatives — avant d'agir. C'est exactement le rôle
de `run` (couche 4) quand les couches de données sont branchées.

    python -m screener.scan --universe screener/data/universe_us.csv --html sig.html
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .ingestion.prices import PriceFetchError, load_bars
from .detection.path_archetype import PriceBar, classify_archetype
from .detection.residuals import compute_detection
from .detection.stabilization import entry_schedule, stabilization_score
from .universe.candidate_builder import stab_features_from_bars
from .models import BLOCKING_ARCHETYPES

_HERE = os.path.dirname(__file__)
_DATA = os.path.join(_HERE, "data")
_DEFAULT_INDEX = {"US": "^GSPC", "EU": "^STOXX50E", "IL": "^TA125.TA"}


@dataclass
class ScanConfig:
    dis_min: float = 1.5
    fresh_max_days: int = 5       # fenêtre de fraîcheur (§4.2)
    rng: str = "2y"


def scan_ticker(ticker: str, name: str, bars: List[PriceBar],
                market_bars: List[PriceBar], cfg: ScanConfig) -> Optional[dict]:
    det = compute_detection(bars, market_bars)
    if det.shock_idx is None or not det.fresh:
        return None
    if det.days_since_shock is None or det.days_since_shock > cfg.fresh_max_days:
        return None
    if det.dis < cfg.dis_min:
        return None
    arch = classify_archetype(bars, det.shock_idx)
    blocked = arch in BLOCKING_ARCHETYPES
    stab = stabilization_score(stab_features_from_bars(bars, det.shock_idx))
    plan = entry_schedule(True, True, arch, stab)
    price = bars[-1].close
    shock_low = bars[det.shock_idx].low
    stop_pct = (price - shock_low) / price if price else None
    # Objectif TECHNIQUE : risque défini par le stop structurel (§7.3.3), cible à
    # 2R / 3R. Pas de valeur fondamentale ici (le scanner n'a que les prix) — ces
    # cibles sont un cadre risque/rendement, pas une thèse de valorisation.
    risk = price - shock_low if (price and shock_low < price) else None
    target_2r = price + 2 * risk if risk and risk > 0 else None
    target_3r = price + 3 * risk if risk and risk > 0 else None
    return {
        "ticker": ticker, "name": name, "price": price,
        "days_since_shock": det.days_since_shock,
        "z_res": det.z_res_at_shock, "dis": det.dis, "z_volume": det.z_volume,
        "archetype": arch.value, "blocked": blocked, "stab": stab,
        "tranche1_ok": plan.tranche1_ok, "tranche2_armed": plan.tranche2_armed,
        "blocked_reason": plan.blocked_reason,
        "stop_level": shock_low, "stop_pct": stop_pct,
        "target_2r": target_2r, "target_3r": target_3r,
    }


def scan_universe(data: Dict[str, Tuple[str, List[PriceBar], List[PriceBar]]],
                  cfg: ScanConfig) -> List[dict]:
    out = []
    for ticker, (name, bars, mkt) in data.items():
        c = scan_ticker(ticker, name, bars, mkt, cfg)
        if c:
            out.append(c)
    # Tri « tradeable d'abord » : les setups entrables tout de suite (archétype
    # non bloquant → tranche 1 possible) remontent, puis par ampleur (DIS).
    out.sort(key=lambda c: (c["tranche1_ok"], c["dis"]), reverse=True)
    return out


def _load_universe(path: str) -> List[dict]:
    with open(path) as f:
        return [r for r in csv.DictReader(f) if (r.get("ticker") or "").strip()]


# Colonnes d'univers attendues par build_universe / build_* / run.
_UNI_COLS = ["ticker", "symbol", "name", "sector", "place", "region", "market_cap",
             "analyst_coverage", "target_position_value", "market_index", "sponsor"]


def _emit_universe(path: str, universe: List[dict], candidates: List[dict],
                   default_coverage: str) -> int:
    """Écrit les titres décrochés au format univers (pont scanner → qualification).

    Conserve les champs des lignes d'origine (région, indice, coverage…) et suit
    l'ordre du scanner (DIS décroissant). `market_cap` est laissé vide : c'est
    build_fundamentals (source SEC) qui le remplit ensuite."""
    by_tk = {(u.get("ticker") or "").strip().upper(): u for u in universe}
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    n = 0
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_UNI_COLS)
        w.writeheader()
        for c in candidates:
            u = by_tk.get(c["ticker"], {})
            cov = (u.get("analyst_coverage") or "").strip() or default_coverage
            w.writerow({
                "ticker": c["ticker"],
                "symbol": (u.get("symbol") or c["ticker"]).strip(),
                "name": u.get("name") or c.get("name") or c["ticker"],
                "sector": u.get("sector") or "",
                "place": u.get("place") or "",
                "region": (u.get("region") or "US").strip(),
                "market_cap": "",                       # rempli par build_fundamentals
                "analyst_coverage": cov,
                "target_position_value": u.get("target_position_value") or "",
                "market_index": u.get("market_index") or "",
                "sponsor": u.get("sponsor") or "",
            })
            n += 1
    return n


def _market_symbol(uni: dict) -> str:
    if uni.get("market_index"):
        return uni["market_index"].strip()
    return _DEFAULT_INDEX.get((uni.get("region") or "US").strip().upper(), "^GSPC")


# Colonnes d'overlay qualitatif que l'humain remplit pour qualifier un titre
# (§6.7 « l'outil pré-instruit, l'humain tranche »). Consommées par run --overlay.
_OVERLAY_COLS = [
    "ticker", "cause_class", "permanence", "expected_resolution_days",
    "fv_low", "fv_mid", "fv_high", "val_z", "aqs", "insider_buy",
    "capi_effacee", "impact_flux_actualise", "net_debt_ebitda", "recovery_precedents",
    "rebut_score", "response_date_days", "upside_thesis_pct", "scenario_documented",
    "hard_stop_distance", "hard_stop_level_motivated", "analysis",
]


def _emit_overlay(path: str, candidates: List[dict]) -> int:
    """Écrit un gabarit d'overlay (une ligne par touche) à remplir à la main.

    Pré-remplit `ticker` et pose `hard_stop_distance` = distance au stop
    structurel (le scanner la connaît) ; les autres champs qualitatifs sont
    laissés vides pour saisie humaine avant `run --overlay`."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_OVERLAY_COLS)
        w.writeheader()
        for c in candidates:
            row = {k: "" for k in _OVERLAY_COLS}
            row["ticker"] = c["ticker"]
            if c.get("stop_pct"):                       # stop structurel connu → S4(b)
                row["hard_stop_distance"] = round(c["stop_pct"], 4)
                row["hard_stop_level_motivated"] = "true"
            w.writerow(row)
    return len(candidates)


def run(universe_path: str, offline: bool, cache_dir: str, cfg: ScanConfig,
        html_path: Optional[str], json_path: Optional[str],
        emit_universe: Optional[str] = None, default_coverage: str = "3",
        emit_overlay: Optional[str] = None) -> int:
    universe = _load_universe(universe_path)
    market_cache: Dict[str, list] = {}
    data: Dict[str, Tuple[str, list, list]] = {}
    errors: List[str] = []
    for uni in universe:
        tk = uni["ticker"].strip().upper()
        symbol = (uni.get("symbol") or tk).strip()
        mkt_sym = _market_symbol(uni)
        try:
            bars = load_bars(symbol, cache_dir=cache_dir, rng=cfg.rng, offline=offline)
            if mkt_sym not in market_cache:
                market_cache[mkt_sym] = load_bars(mkt_sym, cache_dir=cache_dir,
                                                  rng=cfg.rng, offline=offline)
            data[tk] = (uni.get("name") or tk, bars, market_cache[mkt_sym])
        except PriceFetchError as e:
            errors.append(f"{tk}: {e}")

    candidates = scan_universe(data, cfg)
    asof = data[next(iter(data))][1][-1].date if data else ""
    result = {"as_of": asof, "n_scanned": len(data), "candidates": candidates,
              "config": {"dis_min": cfg.dis_min, "fresh_max_days": cfg.fresh_max_days}}

    print(f"\nSIGNAUX DU JOUR — {len(candidates)} dislocation(s) fraîche(s) / "
          f"{len(data)} titres scannés (au {asof})")
    print("-" * 78)
    if candidates:
        print(f"{'Ticker':<9}{'Choc':>6}{'DIS':>6}{'STAB':>6}  {'Archétype':<16}{'Entrée':<22}")
        for c in candidates:
            entry = "tranche 1 (50%)" if c["tranche1_ok"] else "attendre (bloqué)"
            print(f"{c['ticker']:<9}{'J-'+str(c['days_since_shock']):>6}{c['dis']:>6.1f}"
                  f"{c['stab']:>5}/9  {c['archetype']:<16}{entry:<22}")
    else:
        print("(aucune dislocation fraîche aujourd'hui)")

    if json_path:
        with open(json_path, "w") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"\n-> {json_path}")
    if html_path:
        from .output.scan_report import render_html
        with open(html_path, "w") as f:
            f.write(render_html(result, standalone=True))
        print(f"-> {html_path}  (ouvrir dans un navigateur)")
    if emit_universe:
        n = _emit_universe(emit_universe, universe, candidates, default_coverage)
        print(f"-> {emit_universe}  ({n} titre(s) — prêt pour build_fundamentals / "
              f"build_news / run)")
    if emit_overlay:
        n = _emit_overlay(emit_overlay, candidates)
        print(f"-> {emit_overlay}  ({n} ligne(s) — remplis la cause à la main puis "
              f"run --overlay)")
    for e in errors:
        print(f"  ! {e}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Scanner du jour — dislocations fraîches")
    ap.add_argument("--universe", default=os.path.join(_DATA, "universe.sample.csv"))
    ap.add_argument("--cache-dir", default=os.path.join(_HERE, ".cache"))
    ap.add_argument("--range", default="2y")
    ap.add_argument("--dis-min", type=float, default=1.5)
    ap.add_argument("--fresh-max-days", type=int, default=5)
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--html", default=None)
    ap.add_argument("--json", default=None)
    ap.add_argument("--emit-universe", default=None, metavar="PATH",
                    help="écrit les titres décrochés au format univers (pont vers "
                         "build_fundamentals / build_news / run)")
    ap.add_argument("--default-coverage", default="3",
                    help="analyst_coverage par défaut pour les lignes émises (S1)")
    ap.add_argument("--emit-overlay", default=None, metavar="PATH",
                    help="écrit un gabarit d'overlay à remplir à la main (cause, "
                         "valorisation…) pour qualifier un titre → run --overlay")
    args = ap.parse_args(argv)
    if not os.path.exists(args.universe):
        sys.exit(f"univers introuvable : {args.universe}")
    cfg = ScanConfig(dis_min=args.dis_min, fresh_max_days=args.fresh_max_days, rng=args.range)
    return run(args.universe, args.offline, args.cache_dir, cfg, args.html, args.json,
               args.emit_universe, args.default_coverage, args.emit_overlay)


if __name__ == "__main__":
    sys.exit(main())
