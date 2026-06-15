"""
SCCT-Social · Moteur d'extraction de tickers (composant C2/C1).
Reddit n'ayant pas de cashtags structurés, l'extraction est un sous-projet :
détection -> désambiguïsation -> normalisation -> filtrage du bruit.

Conçu pour être testable hors-ligne (aucune dépendance réseau).
"""
from __future__ import annotations
import os, re
from functools import lru_cache

# --- Stoplist : mots anglais qui sont aussi des tickers valides -> bruit ---
# (liste de départ, à enrichir au fil du forward-test)
STOPLIST = {
    "A", "I", "AN", "AS", "AT", "BE", "BY", "DO", "GO", "IF", "IN", "IS", "IT",
    "ME", "MY", "NO", "OF", "ON", "OR", "SO", "TO", "UP", "US", "WE",
    "ALL", "AND", "ANY", "ARE", "BIG", "BUY", "CAN", "CEO", "DAY", "DD", "EPS",
    "FAQ", "FOR", "FUD", "GET", "HOT", "IPO", "ITM", "LOW", "NEW", "NOW", "OTM",
    "OUT", "PSA", "RED", "RUN", "SEC", "SEE", "THE", "WSB", "YOU", "YOLO",
    "EDIT", "ELON", "GAIN", "HOLD", "LOSS", "MOON", "OPEN", "PUMP", "PUTS",
    "REAL", "RICH", "SHIP", "SOLD", "TLDR", "WAGE", "WIN", "AI", "EV", "PE",
    "ATH", "HODL", "FOMO", "GUH", "CALL", "CALLS",
}
# NB : 'OPEN' est ambigu (mot + ticker Opendoor). On le laisse en stoplist par
# défaut mais on le force comme ticker valide s'il porte le préfixe $ ($OPEN).

# --- Cashtag explicite ($XXX) : signal le plus fiable ---
RE_CASHTAG = re.compile(r"\$([A-Za-z]{1,5})\b")
# --- Token majuscule isolé (XXX) : candidat à valider contre l'univers ---
RE_BARE = re.compile(r"\b([A-Z]{2,5})\b")

# Aliases nom-complet -> ticker (désambiguïsation par contexte). À enrichir.
NAME_ALIASES = {
    "optical cable": "OCC",
    "optical cable corp": "OCC",
    "opendoor": "OPEN",
    "krispy kreme": "DNUT",
    "kohl": "KSS",
    "kohls": "KSS",
    "gamestop": "GME",
}

# Désambiguïsation par contexte sectoriel (token -> indices attendus).
# Si le token apparaît sans aucun indice, on baisse la confiance.
CONTEXT_HINTS = {
    "OCC": ["optical", "cable", "fiber", "fibre", "connectivity"],
}


@lru_cache(maxsize=1)
def load_universe() -> set[str]:
    """Univers de tickers US. Fichier texte 1 ticker/ligne via TICKER_UNIVERSE_FILE,
    sinon graine minimale. Remplacer par la liste NASDAQ/NYSE/AMEX complète en prod."""
    path = os.getenv("TICKER_UNIVERSE_FILE", "")
    if path and os.path.exists(path):
        with open(path) as f:
            return {ln.strip().upper() for ln in f if ln.strip() and not ln.startswith("#")}
    # graine : cas de référence de la spec
    return {"OCC", "OPEN", "KSS", "DNUT", "AMC", "GME"}


def _valid_token(tk: str, universe: set[str]) -> bool:
    return tk in universe and tk not in STOPLIST


def extract(text: str, *, universe: set[str] | None = None,
            min_confidence: float = 0.0) -> dict[str, float]:
    """
    Renvoie {TICKER: confiance[0..1]} pour un texte (titre + corps).
    - cashtag $XXX                -> 0.9 (force le ticker même si dans la stoplist)
    - alias nom-complet           -> 0.8
    - token majuscule dans univers-> 0.5, +0.3 si indice de contexte sectoriel
    - token dans la stoplist sans $-> ignoré
    """
    if not text:
        return {}
    universe = universe if universe is not None else load_universe()
    low = text.lower()
    scores: dict[str, float] = {}

    def bump(tk: str, val: float):
        scores[tk] = max(scores.get(tk, 0.0), val)

    # 1) cashtags explicites (priment, contournent la stoplist)
    for m in RE_CASHTAG.finditer(text):
        tk = m.group(1).upper()
        if tk in universe:
            bump(tk, 0.9)

    # 2) alias de noms complets
    for name, tk in NAME_ALIASES.items():
        if name in low and tk in universe:
            bump(tk, 0.8)

    # 3) tokens majuscules nus, validés contre l'univers et hors stoplist
    for m in RE_BARE.finditer(text):
        tk = m.group(1).upper()
        if not _valid_token(tk, universe):
            continue
        conf = 0.5
        hints = CONTEXT_HINTS.get(tk)
        if hints and any(h in low for h in hints):
            conf += 0.3  # désambiguïsation positive
        bump(tk, conf)

    return {tk: c for tk, c in scores.items() if c >= min_confidence}


if __name__ == "__main__":
    # Smoke-test hors-ligne
    samples = [
        "$OCC optical cable corp squeeze incoming, low float",
        "I am ALL IN on OPEN, this will MOON to the SEC filing",
        "OCC is just options clearing, not a ticker here",  # pas d'indice optical -> faible conf
        "GME and AMC back again, KSS too",
        "buy the dip on DNUT krispy kreme",
    ]
    for s in samples:
        print(f"\n> {s}\n  {extract(s)}")
