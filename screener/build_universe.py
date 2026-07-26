#!/usr/bin/env python3
"""
Générateur d'univers US — pour le backtest (§10) et le screener.
================================================================
Source : SEC `company_tickers.json` (gratuit, sans clé) — ~10 000 émetteurs US,
ordonnés par taille décroissante. On SAUTE le haut du classement (méga/large
caps) pour viser la bande small/mid où vivent les inefficiences (§6.3), puis on
échantillonne.

⚠️ BIAIS DE SURVIVANCE (§10.4) : cette liste ne contient que les émetteurs
ENCORE cotés. Un backtest dessus surestime la performance (délistés/faillites
absents). Résultat à lire comme optimiste ; le corriger demande une source
survivorship-inclusive (payante). C'est la limite honnête de l'univers gratuit.

Écrit un CSV au format attendu par `run` / `run_backtest`.

Usage :
    python -m screener.build_universe --sample 40 --skip-top 300 --seed 7
    python -m screener.build_universe --out screener/data/universe_us.csv --sample 200
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import sys
import urllib.request

_SEC_URL = "https://www.sec.gov/files/company_tickers.json"
_UA = {"User-Agent": "screener research nm@nmgroup.be"}
_VALID = re.compile(r"^[A-Z][A-Z0-9.\-]{0,6}$")   # symboles Yahoo-compatibles
# Exclusions §3.1 (SPAC / Shell / warrants / units / preferreds) — heuristique nom.
_EXCLUDE_NAME = re.compile(
    r"\b(ACQUISITION|WARRANT|UNITS?|RIGHTS?|DEPOSITARY|PREFERRED|SPAC|TRUST|"
    r"BLANK CHECK|HOLDINGS? CORP)\b", re.I)
# Suffixes de symboles à 5 lettres typiques des warrants/units/preferreds.
_SUSPECT_SUFFIX = ("W", "U", "R", "P", "L", "Z")

_COLS = ["ticker", "symbol", "name", "sector", "region", "market_cap",
         "analyst_coverage", "target_position_value", "market_index", "sponsor"]


def fetch_sec_tickers(timeout: int = 30) -> list:
    """Liste ordonnée (par taille décroissante) de (ticker, title) depuis la SEC."""
    req = urllib.request.Request(_SEC_URL, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read())
    # dict indexé numériquement dans l'ordre de taille -> on préserve l'ordre
    out = []
    for k in sorted(data.keys(), key=lambda x: int(x)):
        it = data[k]
        sym = (it.get("ticker") or "").strip().upper()
        name = (it.get("title") or "").strip()
        if not _VALID.match(sym):
            continue
        if _EXCLUDE_NAME.search(name):
            continue
        # symbole à 5 lettres finissant par un suffixe suspect (warrant/unit/pref)
        if len(sym) == 5 and sym.isalpha() and sym.endswith(_SUSPECT_SUFFIX):
            continue
        out.append((sym, name))
    return out


def build(out_path: str, sample: int, skip_top: int, seed: int,
          target_position_value: float) -> int:
    try:
        allsyms = fetch_sec_tickers()
    except Exception as e:  # noqa: BLE001
        sys.exit(f"Échec récupération SEC : {type(e).__name__}: {e}")

    band = allsyms[skip_top:]                      # on saute les méga/large caps
    if not band:
        sys.exit(f"--skip-top {skip_top} laisse un univers vide ({len(allsyms)} dispo).")
    rng = random.Random(seed)
    picked = rng.sample(band, min(sample, len(band)))
    picked.sort(key=lambda x: x[0])

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_COLS)
        w.writeheader()
        for sym, name in picked:
            w.writerow({
                "ticker": sym, "symbol": sym, "name": name, "sector": "",
                "region": "US", "market_cap": "", "analyst_coverage": "",
                "target_position_value": target_position_value,
                "market_index": "^GSPC", "sponsor": "",
            })
    print(f"Univers US : {len(picked)} titres (bande small/mid, top {skip_top} sauté "
          f"sur {len(allsyms)}) -> {out_path}")
    print("⚠ biais de survivance (§10.4) : émetteurs encore cotés uniquement — "
          "résultat de backtest à lire comme optimiste.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Générateur d'univers US (SEC) pour le backtest")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__),
                                                  "data", "universe_us.csv"))
    ap.add_argument("--sample", type=int, default=40, help="nombre de titres à tirer")
    ap.add_argument("--skip-top", type=int, default=300,
                    help="nombre de méga/large caps à sauter (viser small/mid)")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--target-position-value", type=float, default=1_000_000)
    args = ap.parse_args(argv)
    return build(args.out, args.sample, args.skip_top, args.seed,
                 args.target_position_value)


if __name__ == "__main__":
    sys.exit(main())
