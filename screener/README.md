# screener/ — moteur de décision (dislocation multi-signaux, SPEC v1.3)

Sous-système distinct du collecteur SCCT-Social (racine du dépôt). Implémente la
partie **déterministe et testable sans flux payant** de la couche 4 : socle S1-S5,
cinq routes (playbooks), score de réfutation, conviction/sizing par multiplicateur,
couche PATH (chemin de prix) et règles de sortie codées en dur.

Spécification complète : [`SPEC.md`](SPEC.md). Carte des modules et invariants
d'architecture : [`CLAUDE.md`](CLAUDE.md).

## Démarrage

```bash
python -m unittest discover -s screener/tests   # 45 tests, stdlib uniquement
python -m screener.demo                          # imprime une fiche 1 page (données synthétiques)
python -m screener.build_catalysts               # registre de catalyseurs (CT.gov + CSV) -> catalysts.built.csv
ORTEX_API_KEY=xxx python -m screener.build_short_interest   # short interest / emprunt (Ortex) -> short_interest.built.csv
NEWS_API_KEY=xxx ANTHROPIC_API_KEY=yyy python -m screener.build_news   # classification news LLM (§5.3) -> news.built.csv
python -m screener.run                            # tranche verticale : watchlist -> short-list (live)
python -m screener.run --offline                 # idem, cache uniquement, aucun réseau
```

Chaîne complète : `build_catalysts` (P1) et `build_short_interest` (Ortex, §4.3)
produisent des CSV que `run` consomme via `--catalysts` / `--short-interest`.
Ortex fournit deux confirmateurs de flux (saut du taux d'emprunt, utilisation du
float) qui rehaussent `DIS` et remplissent la section POSITIONNEMENT de la fiche.
Sans `ORTEX_API_KEY`, la chaîne tourne quand même (DIS = z-volume seul).

`build_news` (§5.3, P4) classe les news 72h via Claude en `cause_class` /
`permanence` / `expected_resolution_days` — les champs qui gouvernent le socle S3
et les routes A/B/E, sinon saisis à la main. L'overlay manuel écrase toujours le
news LLM. Sans clés, la chaîne retombe sur l'overlay manuel.
⚠️ Live/forward uniquement — pas pour le backtest (garde anti-fuite LLM, §10.3).

## Tranche verticale (`screener.run`)

Enchaîne bout à bout : univers CSV → prix (Yahoo, sans clé, cache quotidien) →
détection résiduelle marché-neutre (§4) → assemblage de `Candidate` → moteur de
décision → short-list classée + fiches des dossiers admis.

- **`ingestion/prices.py`** — barres OHLCV depuis Yahoo, cache disque, chargeur CSV offline.
- **`detection/residuals.py`** — résidu marché-neutre (bêta unique — simplification
  assumée du modèle 4 facteurs §4.1), `z_res`, `DIS`, `z_volume`, jours depuis le choc.
- **`universe/candidate_builder.py`** — fusionne détection (prix) + overlay qualitatif
  (les champs qui, dans le système complet, viennent des couches LLM/analystes/options).
- Entrées : `data/universe.sample.csv`, `data/catalysts.sample.csv`, `data/overlay.sample.csv`.

Sans overlay ni catalyseur, un simple décrochage de prix échoue le socle S3
(pas d'horizon de résolution) — c'est voulu : un décrochage n'est pas un dossier.

## Usage

```python
from screener import Candidate, Catalyst, evaluate_candidate
from screener.output import dossier

candidate = Candidate(ticker="XXX.PA", ...)      # alimenté par les couches d'ingestion
res = evaluate_candidate(candidate, standard_size=1.0)

res.admitted            # socle passé ET >= 1 route validée
res.route_labels        # ex. [Route.B, Route.D]
res.conviction.conv     # score de classement (CONV_base × multiplicateur)
res.sizing.final_size   # taille finale (facteur S4 × mult × cap route C × régime)
print(dossier.render(res))
```

## Ce qui n'est pas ici

Les couches amont (ingestion, modèle factoriel/résidus, modules analystes / marchés
prédictifs / classification LLM, backtest §10) produisent les valeurs qui remplissent
un `Candidate`. Ce paquet consomme ce `Candidate` et rend la décision + la fiche.
