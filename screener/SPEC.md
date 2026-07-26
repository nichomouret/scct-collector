# SPEC — Screener de dislocation multi-signaux
### Projet NM Group — v1.3 — Juillet 2026
*Document de spécification destiné à l'implémentation via Claude Code*

**Changements v1.2 → v1.3**
- Ajout de la **couche `PATH` (chemin de prix)**, transverse aux cinq routes : archétypes de trajectoire, score de stabilisation, entrée par tranches, stop structurel. Elle ne crée aucune admission — elle module le timing et la taille. Voir §7.3.

**Changements v1.1 → v1.2 — refonte de la logique de décision**
- Les portes ET séquentielles sont **remplacées par une architecture à routes (playbooks)**. Cinq configurations de setup coexistent, chacune avec ses propres conditions. Un titre qualifie s'il satisfait **une** route entièrement, pas toutes les conditions de toutes les routes.
- La superposition de plusieurs routes ne conditionne plus l'admission : elle devient un **multiplicateur de conviction et de taille**.
- La porte de valorisation est généralisée en **test de perte bornée** : décote, stop dur, taille réduite ou structure optionnelle sont des mécanismes équivalents. Un titre non décoté (type IBM) reste éligible s'il borne sa perte autrement.
- La classe `SHORT_REPORT` réintègre le périmètre via la **route C**, gouvernée par un score de réfutation (cas 2CRSi).

**Changements v1.0 → v1.1**
- Module Trump / signal politique **retiré** du périmètre. Le radar thématique reste un outil autonome et distinct.
- Ajout d'une **contrainte d'horizon dure : durée de détention cible < 2 mois (40 séances)**. Cette contrainte n'est pas un paramètre, c'est un choix d'architecture — elle modifie les portes, le moteur de rendement, le backtest et la feuille de route. Voir §6.

---

## 0. Résumé exécutif

**Objectif** : identifier, sur les marchés US et européens, des titres dont le cours a décroché de sa valeur fondamentale pour des raisons non fondamentales (rumeur, flux forcé, contagion sectorielle, sur-réaction), **et dont la normalisation est susceptible de se produire en moins de 40 séances** parce qu'un catalyseur daté force la résolution.

**Principe architectural n°1** : l'outil n'est pas un modèle de prédiction. C'est un **moteur de triage** qui réduit un univers de ~6 500 titres à une short-list de 3 à 10 noms par jour, chacun livré avec un dossier pré-instruit permettant une décision humaine en 60 secondes.

**Principe architectural n°2** : les signaux ont des demi-vies incompatibles. Une dislocation de flux se résorbe en jours ; un multiple de valorisation se normalise en trimestres. **Ils ne doivent jamais être moyennés dans un score unique.** L'architecture est en cascade de portes, pas en somme pondérée.

**Principe architectural n°3** : sous contrainte de 2 mois, **la valorisation ne produit pas le rendement — elle borne la perte.** Mais elle n'est qu'un des mécanismes possibles de bornage : un stop dur, une taille réduite ou une structure optionnelle jouent le même rôle. L'exigence n'est donc pas « le titre doit être décoté », c'est **« la perte doit être bornée par au moins un mécanisme identifié »**.

**Principe architectural n°4 (v1.2)** : il n'existe pas un seul type de setup gagnant à horizon court, mais **cinq configurations distinctes** (§7.1). Elles sont en **OU** entre elles, en **ET** à l'intérieur de chacune. Un titre qui présente une sur-réaction massive sans être décoté, ou un binaire daté sans dislocation préalable, est un setup valide — pas un rejet. La superposition de deux ou trois routes n'est pas une condition d'entrée : c'est le signal de conviction maximale, et elle s'exprime par **la taille de la position**, pas par l'admission.

---

## 1. Architecture générale

```
COUCHE 0 — INGESTION (continu)
  Prix · Fondamentaux · Estimations · News · Filings · Short interest ·
  Options · Calendrier catalyseurs · Marchés prédictifs
        ▼
COUCHE 1 — UNIVERS ÉLIGIBLE (batch quotidien, 06:00 CET)
  Liquidité/qualité + bandes de valorisation + CATALYSEURS
  → ~600 titres éligibles avec catalyseur daté ≤ 45 jours
        ▼
COUCHE 2 — DÉTECTION D'ANOMALIE (intraday, 5 min)
  Rendement résiduel + confirmation de flux → ~5-25 candidats/jour
        ▼
COUCHE 3 — QUALIFICATION (événementiel, LLM)
  Classification de cause · Vitesse de résolution · Analystes · Marchés prédictifs
        ▼
COUCHE 4 — DOSSIER, ALERTE & DISCIPLINE DE SORTIE
  Fiche 1 page · Score · Thèse · Invalidation · Time-stop
```

Chaque couche filtre la précédente. Un titre qui échoue en couche 1 n'est jamais évalué en couche 2 — c'est ce qui permet de faire tourner un LLM sur 20 titres/jour au lieu de 6 500.

**Nouveauté v1.1** : le calendrier de catalyseurs remonte en couche 1. Il n'est plus un enrichissement de fin de chaîne mais un **critère d'éligibilité amont**.

---

## 3. Couche 1 — Univers éligible, valorisation, catalyseurs

### 3.1 Filtres d'éligibilité

```python
ELIGIBILITY = {
    "market_cap_min": {"US": 200e6, "EU": 150e6, "IL": 80e6},
    "adv_20d_min": {"US": 2e6, "EU": 1e6, "IL": 0.5e6},
    "price_min": 3.0,
    "analyst_coverage_min": 2,
    "listing_age_min_days": 250,
    "exclude_sectors": ["SPAC", "Shell", "Closed-End Fund"],
    "exclude_flags": ["going_concern", "delisting_notice", "trading_halt_pending"],
    "catalyst_required": True,
    "catalyst_max_days": 45,          # buffer de 15 séances avant le time-stop
    "exit_liquidity_max_days_adv": 2, # position sortable en 2 jours d'ADV
}
```

### 3.2 Registre des catalyseurs

Schéma :
```json
{
  "ticker": "XXX.PA",
  "catalyst_type": "PHASE3_READOUT",
  "date_expected": "2026-09-15",
  "date_certainty": "APPROXIMATE",
  "days_to_catalyst": 51,
  "binary": true,
  "expected_move_pct": 35.0,
  "source_url": "..."
}
```

`expected_move_pct` doit être dérivé du marché des options quand elles existent (prix du straddle à l'échéance la plus proche du catalyseur).

### 3.3 Score de valorisation (VAL_z)

```
VAL_z = -1 × mean(percentile_5y_sector(m) for m in metrics_applicable)
```

Sous contrainte de 2 mois, `VAL_z` n'est plus un moteur de rendement — il devient un **amortisseur de perte**. **Garde-fou value trap** : `VAL_z > 1.5` ET `QUAL < -1` → `VALUE_TRAP`, sortie d'univers.

### 3.4 Bande de juste valeur

Fourchette (`fv_low`, `fv_mid`, `fv_high`) issue de trois méthodes indépendantes. L'écart `(fv_low - price) / price` est le **coussin** exigé en couche 4.

---

## 4. Couche 2 — Détection d'anomalie

### 4.1 Rendement résiduel
```
r_i,t = α_i + β_mkt·r_mkt,t + β_sect·r_sect,t + β_size·SMB_t + β_val·HML_t + ε_i,t
z_res(t) = ε_i,t / σ(ε_i, 60j)
```
**Déclencheur primaire** : `z_res ≤ -2.5` sur 1 séance, ou `z_res_cum(3j) ≤ -3.0`.

### 4.2 Fenêtre de fraîcheur
```
decay(d) = exp(-d / 3)
rejet si d > 5 séances
```

### 4.3 Confirmateurs de flux (chacun ±1 point)
Z-score de volume > 3 ; saut du taux d'emprunt > +200 bps/3j ; utilisation du float > 90 % ; déformation du skew 30j ; absence de dépôt réglementaire 48h ; achat d'initié post-baisse ; divergence crédit/actions.

### 4.4 Score de dislocation
```
DIS = |z_res| × (1 + 0.15 × Σ confirmateurs) × decay(jours_depuis_choc)
```

---

## 5. Couche 3 — Qualification

### 5.1 Module analystes (AQS)
```
AQS = 0.5·z(ARM_30j_pondéré) + 0.3·z(nb_révisions_hausse_qualité_30j) - 0.2·z(dispersion)
```
Configuration cible : révisions à la hausse par des analystes à haut hit rate **pendant que le cours baisse**, à moins de 45 jours d'un catalyseur.

### 5.2 Module marchés prédictifs (PMS)
```
p_implied = (price - V_échec) / (V_succès - V_échec)
PMS = p_market - p_implied
```
Alerte si `|PMS| > 0.20` avec open interest > 50 k$.

### 5.3 Module classification de news (LLM)

**Gating v1.1** — deux conditions cumulatives :
1. `cause_class` ∈ {`RUMOR_UNCONFIRMED`, `SECTOR_CONTAGION`, `INDEX_REBALANCE`, `LOCKUP_EXPIRY`, `FORCED_SELLING`, `NO_IDENTIFIED_CAUSE`} **ou** `permanence = TRANSITORY`
2. **`expected_resolution_days ≤ 40`**

**Point clé de la v1.2** : la vitesse de résolution n'est pas une propriété de la classe d'événement, c'est une propriété de **la qualité et de la rapidité de la réponse**. C'est la réfutation qu'il faut scorer, pas l'attaque.

#### Score de réfutation (`REBUT`, 0-10)

| Critère | Points |
|---|---|
| Réponse point par point, chiffrée, publiée en < 5 séances | 0-2 |
| Audit ou revue indépendante **mandatée**, cabinet identifiable | 0-2 |
| Confirmation par une contrepartie **nommée** | 0-2 |
| Achats d'initiés post-attaque (dirigeants) | 0-2 |
| Antécédent de crédibilité de l'attaquant | 0-1 |
| Absence de dilution ou de refinancement d'urgence dans les 30 jours | 0-1 |

**Seuil route C : `REBUT ≥ 6`.** La date de rendu de l'audit / de la réponse détaillée **devient le catalyseur daté** (`days_to_catalyst`). Bornage de perte obligatoirement par **taille réduite** (0.5×).

**Test de sur-réaction** : `cash_flow_impact_pct` actualisé vs capitalisation effacée.

---

## 6. La contrainte des 2 mois — ce qu'elle change

1. Vous ne faites plus de la value, vous faites de l'**event-driven**.
2. Le gisement se réduit d'environ deux tiers (~60-90 opportunités/an).
3. La concurrence change : votre avantage est la **couverture** (small/mid EU, TASE), pas la vitesse.
4. La **discipline de sortie** devient une composante du système : time-stop 40 séances **dans le moteur, pas dans votre tête**.
5. Les **options** deviennent le véhicule naturel sur la poche US.
6. Vos métriques baissent, votre ratio gain/perte monte : **gain moyen / perte moyenne > 1.8**.
7. Recadrage : l'avantage est de **comprimer le temps de décision** (3 h → 60 s), pas de mieux prédire.

---

## 7. Couche 4 — Portes, scoring, sortie

### 7.1 Socle commun + routes

#### Socle commun — non négociable
```
S1  Éligibilité univers (liquidité, capi, pas de going concern / delisting)
S2  Liquidité de sortie : position cible ≤ 2 jours d'ADV
S3  expected_resolution_days ≤ 40
S4  PERTE BORNÉE par au moins un mécanisme identifié :
      (a) coussin de valorisation (fv_low - price)/price > 15 % → taille 1.0×
      (b) stop dur défini, distance ≤ 12 % et niveau technique motivé → taille 0.7×
      (c) structure optionnelle à perte plafonnée → taille 1.0×
      (d) taille réduite seule → taille 0.5×
    Le mécanisme retenu est INSCRIT dans la fiche. Aucun dossier sans mécanisme.
S5  Pas de risque existentiel dans la fenêtre :
    covenant breach, mur de dette < 90j, going concern, suspension de cotation
```

#### Les cinq routes (OU entre elles, ET à l'intérieur)

```
ROUTE A — DISLOCATION TECHNIQUE
  A1  DIS ≥ 3.0  ET  jours_depuis_choc ≤ 3
  A2  cause_class ∈ {INDEX_REBALANCE, FORCED_SELLING, LOCKUP_EXPIRY, SECTOR_CONTAGION}
  A3  aucun dépôt réglementaire négatif dans les 72h
  A4  z-volume ≥ 3
  → catalyseur NON requis · valorisation NON requise

ROUTE B — SUR-RÉACTION À UNE NEWS
  B1  ratio_surreaction = capi_effacée / |impact_flux_actualisé| ≥ 3.0
  B2  jours_depuis_choc ≤ 5
  B3  permanence ≠ PERMANENT  ET  pas de rupture structurelle du modèle
  B4  bilan sain : net debt/EBITDA < 3.5  OU  trésorerie nette
  B5  historique : ≥ 2 précédents de récupération ≥ 50 % sur 5 ans
  → catalyseur NON requis · valorisation NON requise  ← admet IBM

ROUTE C — RÉFUTATION
  C1  REBUT ≥ 6
  C2  date de réponse détaillée / rendu d'audit connue et ≤ 45 jours → catalyseur
  C3  pas de dilution ni de refinancement d'urgence annoncé
  C4  taille plafonnée à 0.5× — non négociable
  → dislocation requise implicitement (l'attaque l'a créée)

ROUTE D — BINAIRE DATÉ
  D1  catalyseur binaire, date connue, days_to_catalyst ∈ [5, 45]
  D2  |PMS| ≥ 0.20  OU  |p_estimée - p_implied| ≥ 0.20 en scénarisation manuelle
  D3  upside_thèse > 1.5 × expected_move_pct implicite du straddle
  D4  liquidité du marché prédictif > 50 k$  OU  scénarisation documentée
  → dislocation NON requise ← admet Abivax · véhicule optionnel privilégié

ROUTE E — DÉCOTE + CATALYSEUR
  E1  VAL_z > 1.0  ET  non VALUE_TRAP
  E2  catalyseur daté, days_to_catalyst ∈ [5, 45]
  E3  DIS ≥ 2.0
  E4  confirmation indépendante : AQS > 1.0  OU  achat d'initié
  → la route « classique », la plus exigeante, la plus fiable
```

#### Superposition
`n = len(routes_validées)`. `n ≥ 1` suffit pour entrer. `n ≥ 2` = signal de conviction maximale, exprimé dans la taille.

### 7.2 Conviction, taille, priorité

```
CONV_base = 0.30·norm(DIS) + 0.25·norm(puissance_catalyseur × certitude_date)
          + 0.20·norm(AQS) + 0.15·norm(coussin) + 0.10·norm(VAL_z)

multiplicateur_routes = {1: 1.00, 2: 1.35, 3: 1.60}[min(n, 3)]
CONV = CONV_base × multiplicateur_routes

taille = taille_standard × facteur_S4 × multiplicateur_routes
       × plafond_route (0.5 forcé sur route C) × facteur_régime
```

**Les termes de `CONV_base` dont la route ne se sert pas sont neutralisés à 0**, pas comptés comme nuls, et exclus de la renormalisation. Chaque route normalise son score sur ses seules composantes actives, puis les scores sont ramenés sur une échelle commune par percentile historique **intra-route**. Point d'implémentation facile à rater et qui fausse tout le classement s'il est manqué.

### 7.3 Couche de chemin de prix (`PATH`) — transverse, v1.3

**Statut architectural** : `PATH` n'est **pas une route**. Elle ne crée aucune admission, ne génère aucun signal seule. Elle **module l'entrée et la taille** de dossiers déjà qualifiés.

#### 7.3.1 Archétypes de chemin

| Archétype | Signature | Action |
|---|---|---|
| `GAP_AND_FLAT` | Gap unique, puis 3+ séances de range étroit, volume décroissant | ★★★ entrée rapide |
| `CAPITULATION_V` | Pic de volume ≥ 5σ sur le point bas, mèche basse, clôture en haut de range | ★★★ entrée rapide |
| `GRINDING_DECLINE` | 5+ séances de baisse, volume stable/croissant, clôtures en bas de range | ✗ ne pas entrer |
| `STAIRCASE_DOWN` | Rebonds avortés, chaque plus haut inférieur au précédent | ✗ ne pas entrer |
| `FAILED_BOUNCE` | Rebond > 8 % puis retour sous le point bas | ✗ sortie si en position |

#### 7.3.2 Score de stabilisation (`STAB`, 0-10)

| Feature | Points |
|---|---|
| Contraction de volume : volume 3 dernières / volume du choc < 0.5 | 0-2 |
| Position de clôture dans le range > 0.6 sur 2 des 3 dernières séances | 0-2 |
| Aucun nouveau plus bas depuis N=3 séances | 0-2 |
| Reconquête du VWAP ancré au jour du choc | 0-2 |
| Premier plus bas ascendant formé | 0-1 |
| Convergence vol réalisée / vol implicite | 0-1 |

`STAB ≥ 6` = stabilisation confirmée.

#### 7.3.3 Entrée par tranches (pas gate binaire)
```python
ENTRY_SCHEDULE = {
    "tranche_1": {"trigger": "route_validée AND socle_ok", "size": 0.5,
                  "condition": "archétype ∉ {GRINDING_DECLINE, STAIRCASE_DOWN, FAILED_BOUNCE}"},
    "tranche_2": {"trigger": "STAB >= 6", "size": 0.5,
                  "deadline": "8 séances, sinon abandon de la tranche 2"},
}
```
Stop structurel (nouveau plus bas sous le point de capitulation) remplace le stop fixe -12 % de S4(b).

#### 7.3.4 Discipline anti-sur-ajustement
1. **Budget fermé : 6 features maximum.** Toute nouvelle en remplace une (preuve sur 40 trades).
2. **Aucun indicateur classique** (RSI, MACD, Bollinger…).
3. **Test du coût d'attente obligatoire.** Si le tranching ne bat pas l'entrée immédiate d'au moins 0.3 de Sharpe, désactiver `PATH` sauf le filtre des trois archétypes bloquants.
4. **Garde anti-look-ahead** : features calculées sur barres **closes uniquement** ; test dédié `backtest/leakage_guard.py`.
5. **Reporting séparé** : apport de `PATH` mesuré en delta par route.

### 7.4 Règles de sortie codées en dur
```python
EXIT_RULES = {
    "time_stop_sessions": 40,          # sortie inconditionnelle
    "catalyst_resolved": "exit_within_2_sessions",
    "stop_thesis_invalidation": True,
    "trailing_stop_after_target": 0.5, # verrouille 50 % du gain
    "max_adverse_excursion": -0.15,    # stop dur
}
```
Le `time_stop` est non négociable et s'applique **à l'identique dans le backtest et en production**.

### 7.5 Fiche de sortie (1 page)
Format imposé : ROUTE(S) · BORNAGE PERTE · CHEMIN DE PRIX · CONVICTION · TAILLE CIBLE · HORIZON · COURS/FV · COUSSIN · CATALYSEUR (section n°1) · CE QUI S'EST PASSÉ · POURQUOI C'EST UNE DISLOCATION · CONFIRMATIONS INDÉPENDANTES · CE QUI INVALIDE LA THÈSE (obligatoire) · POSITIONNEMENT & SORTIE.

---

## 10. Protocole de backtest — non négociable

1. **Point-in-time strict** (fondamentaux + consensus connus à t).
2. **Point-in-time sur les catalyseurs** (date telle qu'annoncée à t).
3. **Anti-fuite LLM** (features déterministes sur la période de test).
4. **Univers survivant** (inclure délistés/faillites).
5. **Coûts réels** (spread effectif, impact √ADV, commissions, emprunt, taxes ; rotation ~6×/an).
6. **Embargo** (signal clôture t → exécution ouverture t+1).
7. **Time-stop appliqué** (40 séances, sans exception).
8. **Walk-forward** (calibration 3 ans, test 1 an, roulement).
9. **Métriques** : hit rate > 50 %, gain/perte > 1.8, Sharpe net > 1.0, DD max < 20 %, ≥ 150 trades.
10. **Test placebo** (mêmes chaînes sur signaux aléatoires).
11. **Décomposition par route** : ≥ 40 trades/route pour la valider.
12. **Chaque route franchit son propre seuil, seule.**
13. **Test de recouvrement** : routes corrélées > 0.7 → fusionner.

---

## 11. Feuille de route (extrait)

| Phase | Contenu | Livrable |
|---|---|---|
| P0 | Ingestion prix/fondamentaux US+EU, schéma PIT, univers | Base peuplée |
| P1 | **Registre des catalyseurs** | Calendrier daté |
| P2 | Modèle factoriel, résidus, détection | Flux de candidats |
| P3 | Valorisation + quality gate + value trap | Univers scoré |
| P4 | Classification news LLM + vitesse de résolution + fiche | Premier livrable quotidien |
| **P5** | **Socle S1-S5, cinq routes, time-stop, sizing** | **Discipline codée** ← implémenté ici |
| P6 | Backtest complet + protocole §10 | Go / no-go chiffré |
| P7-P9 | AQS + options · marchés prédictifs · TASE/TSX | Extensions |

---

## 12. Risques à assumer

- **Sur-ajustement** (5 routes × seuils ≈ 50 paramètres) : placebo, walk-forward, **40 trades min/route**.
- **Prolifération des routes** : cinq maximum, toute nouvelle en remplace une.
- **Corrélation en stress** : filtre de régime durcissant les portes.
- **Rotation/fiscalité** (~6×/an), **capacité** (small caps EU), **cadre réglementaire** (MAR, journal d'audit horodaté), **dépendance fournisseur** (dégradation gracieuse).
- **Le gisement peut être trop mince** : décider après P6, sur chiffres.

---

*Ce document est la référence de `screener/`. La partie déterministe de la couche 4
(socle, routes, conviction/sizing, PATH, sorties) est implémentée et testée ; voir
`CLAUDE.md` pour la carte des modules et ce qui reste en amont.*
