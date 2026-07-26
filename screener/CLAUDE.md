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

## Registre de catalyseurs (P1 — module central §3.2)

Alimente le socle S3 et les routes C/D/E. Fournisseurs gratuits, extensibles :

| Module | SPEC | Rôle |
|---|---|---|
| `ingestion/catalysts.py` | §3.2, §9 | Fournisseurs : **ClinicalTrials.gov v2** (readouts, sans clé) + CSV manuel → `RawCatalyst` |
| `universe/catalyst_registry.py` | §3.1-3.2 | Agrège, dédup, `days_to_catalyst` **point-in-time** (§10.2), fenêtre ≤ 45j, → `Catalyst` |
| `build_catalysts.py` | P1 | CLI : univers → `catalysts.built.csv` (drop-in pour `run.py --catalysts`) |

`python -m screener.build_catalysts [--as-of YYYY-MM-DD] [--all-clinical]`.
ClinicalTrials.gov n'est interrogé que pour les titres santé/biotech (secteur ou
colonne `sponsor`), sinon un nom non-santé ramènerait des études sans rapport.
`--as-of` rend le registre rejouable en point-in-time (indispensable au backtest P6).
`catalysts.built.csv` est gitignoré (artefact daté, régénéré).

## Short interest / emprunt (Ortex, §4.3, Phase 2)

Deux confirmateurs de flux à ★★★ que les prix seuls ne donnent pas — **saut du
taux d'emprunt > +200 bps/3j** et **utilisation du float > 90 %** — plus les
lignes short interest / coût d'emprunt / DTC de la fiche (§7.5).

| Module | SPEC | Rôle |
|---|---|---|
| `ingestion/short_interest.py` | §4.3 | Client Ortex (env `ORTEX_API_KEY`/`ORTEX_BASE`, en-tête `Ortex-Api-Key`, endpoint `/short_interest`) ; parsing pur + `flow_confirmers()` |
| `build_short_interest.py` | Phase 2 | CLI univers → `short_interest.built.csv` (drop-in pour `run.py --short-interest`) |

Conventions alignées sur `ortex_c4_pull.py` (racine). Ces confirmateurs sont
recomptés dans le builder et **rehaussent `DIS`** via `dislocation_score()`
(§4.4) : `DIS = |z_res|·(1+0.15·Σconfirmateurs)·decay`. Sans clé, tout dégrade
en `None` et `DIS` retombe sur le seul z-volume. Snapshot gitignoré.

## Classification de news LLM (§5.3, P4 — keystone)

Le module le plus important : il **automatise** `cause_class` / `permanence` /
`expected_resolution_days` — les champs qui gouvernent le socle S3 et les routes
A/B/E, et qui devaient sinon être saisis à la main dans l'overlay. C'est
« l'outil pré-instruit, l'humain tranche » (§6.7) rendu concret.

| Module | SPEC | Rôle |
|---|---|---|
| `ingestion/news.py` | §5.3 | NewsAPI (`NEWS_API_KEY`) → news 72h ; dégrade en `[]` sans clé |
| `qualification/news_classifier.py` | §5.3 | Claude en **sortie structurée** (`output_config.format`) → `NewsClassification` + gating + **analyse rédigée par titre** |
| `build_news.py` | P4 | CLI univers → `news.built.csv` (format overlay, drop-in `run.py --news`) |

- **Modèle** : SPEC §8 (« Sonnet pour le volume ») → défaut `claude-sonnet-5`,
  surchargé par `SCREENER_LLM_MODEL`. SDK `anthropic` (optionnel, `requirements.txt`).
- **Précédence** : dans `run.py`, l'overlay manuel **écrase** le news LLM
  (`{**news, **overlay}`) — l'humain a le dernier mot.
- **Analyse par titre** : le même appel produit un champ `analysis` (2-3 phrases)
  rendu dans la fiche (`ANALYSE (Claude)`) et dans `run` (`ANALYSE CLAUDE PAR TITRE`,
  pour chaque titre). Circule via l'overlay → `Candidate.analysis` → `dossier`.
- **Gating (§5.3)** : `cause ∈ {transitoires}` OU `permanence=TRANSITORY`, ET
  `expected_resolution_days ≤ 40`.
- ⚠️ **Garde anti-fuite LLM (§10.3)** : live/forward uniquement. NE PAS brancher
  dans `backtest/` — le modèle connaît le futur. Documenté dans le module.
- Sans `ANTHROPIC_API_KEY`, tout dégrade → overlay manuel. `news.built.csv` gitignoré.

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
