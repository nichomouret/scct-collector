# Screener de dislocation multi-signaux — contexte projet (Claude Code)

Sous-système **distinct** du collecteur SCCT-Social qui vit à la racine du dépôt.
Objectif (SPEC v1.3, `SPEC.md`) : réduire un univers de ~6 500 titres US/EU à une
short-list de 3-10 noms/jour, chacun livré avec une fiche pré-instruite permettant
une décision humaine en 60 secondes, sous **contrainte d'horizon dure < 40 séances**.

## Ce qui est implémenté dans `screener/` (couche 4 — le moteur de décision)

Partie **déterministe et testable sans aucun flux payant** — roadmap P5 + couche PATH (v1.3) :

| Module | SPEC | Rôle |
|---|---|---|
| `models.py` | §3.2, §7 | Dataclasses `Candidate` / `Catalyst` + énumérations partagées |
| `scoring/floor.py` | §7.1 | Socle **S1-S5** + sélection du mécanisme de bornage S4 |
| `scoring/rebuttal.py` | §5.3 | Score de réfutation **REBUT** (0-10) |
| `scoring/routes/{a..e}.py` | §7.1 | Les **cinq routes** (OU entre elles, ET à l'intérieur) |
| `scoring/conviction.py` | §7.2 | **CONV_base** (renorm. par composantes actives), multiplicateur, **sizing** |
| `detection/path_archetype.py` | §7.3.1 | Classification d'**archétypes** de chemin (barres closes) |
| `detection/stabilization.py` | §7.3.2-3 | Score **STAB** + entrée par tranches |
| `execution/exit_rules.py` | §7.4, §7.3.3 | **Time-stop 40 séances**, MAE, stop structurel |
| `output/dossier.py` | §7.5 | Rendu de la **fiche 1 page** |
| `engine.py` | §7 | Orchestrateur : socle → routes → conviction → taille → PATH |

## Tranche verticale runnable (`screener.run`)

Bout à bout, données réelles gratuites — de la watchlist à la short-list :

| Module | SPEC | Rôle |
|---|---|---|
| `ingestion/prices.py` | Couche 0 | Barres OHLCV (Yahoo sans clé) + cache quotidien + chargeur CSV offline |
| `detection/residuals.py` | §4.1-4.4 | Résidu **marché-neutre** (bêta unique, simplification assumée du 4-facteurs), `z_res`, `DIS`, `z_volume`, jours depuis le choc |
| `universe/candidate_builder.py` | Couche 1 | Fusionne détection (prix) + catalyseur + **overlay** qualitatif → `Candidate` |
| `run.py` | — | CLI : univers → prix → détection → build → moteur → short-list + fiches |

`python -m screener.run [--offline] [--universe u.csv --catalysts c.csv --overlay o.csv]`.
Cache dans `screener/.cache/` (gitignoré). Sans overlay/catalyseur, un décrochage
échoue le socle S3 — voulu : un décrochage de prix n'est pas un dossier.

**Principes d'architecture à ne jamais casser** (ils viennent de la SPEC) :
1. Pas de score unique moyennant des signaux à demi-vies incompatibles — **cascade de portes**.
2. `PATH` **ne crée aucune admission** : un titre non admis par socle+route n'entre jamais.
3. Renormalisation de `CONV_base` sur les **seules composantes actives** de la/les route(s) —
   un point d'implémentation facile à rater qui fausse tout le classement (§7.2).
4. Le **time-stop 40 séances** doit être identique en backtest et en production (§7.4, §10.7).
5. Discipline anti-sur-ajustement de `PATH` : **6 features max, budget fermé**, aucun
   indicateur classique, garde anti-look-ahead (§7.3.4).

## Ce qui reste hors de ce paquet (couches amont)

Ingestion (prix, fondamentaux, estimations, news, filings, short interest, options,
marchés prédictifs, registre de catalyseurs), modèle factoriel/résidus (§4), modules
analystes/marchés prédictifs/classification LLM (§5) et le backtest (§10) alimentent un
`Candidate`, que ce moteur consomme. Ce sont les phases P0-P4, P6-P9 de la roadmap (§11).

## Conventions

- Python 3.12, **stdlib uniquement** dans ce paquet (déterminisme, testabilité, zéro friction).
- Docstrings en français référençant les sections de `SPEC.md`.
- Tests : `python -m unittest discover -s screener/tests` (aucune dépendance).
- Démo : `python -m screener.demo` (imprime une fiche exemple).

Point d'entrée : `screener.evaluate_candidate(candidate, standard_size=1.0, ...)`.
