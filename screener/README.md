# screener/ — moteur de décision (dislocation multi-signaux, SPEC v1.3)

Sous-système distinct du collecteur SCCT-Social (racine du dépôt). Implémente la
partie **déterministe et testable sans flux payant** de la couche 4 : socle S1-S5,
cinq routes (playbooks), score de réfutation, conviction/sizing par multiplicateur,
couche PATH (chemin de prix) et règles de sortie codées en dur.

Spécification complète : [`SPEC.md`](SPEC.md). Carte des modules et invariants
d'architecture : [`CLAUDE.md`](CLAUDE.md).

## Démarrage

```bash
python -m unittest discover -s screener/tests   # 40 tests, stdlib uniquement
python -m screener.demo                          # imprime une fiche 1 page
```

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
