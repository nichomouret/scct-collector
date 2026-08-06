# Sous-système AMF — franchissements de seuils & positions courtes

Outil multi-agents qui collecte chaque jour les déclarations réglementaires de
l'AMF, les enrichit (analyse technique / synthèse analystes / note sociale) et
les présente dans un dashboard d'aide à la décision.

Dashboard : **`/amf`** (le radar social historique reste sur `/`).

## Les 4 agents

| Agent | Rôle | Source | Sortie |
|------|------|--------|--------|
| **1** — `amf_bdif.py` | Franchissements de seuils | API BDIF `bdif.amf-france.org` (`/informations?TypesInformation=SPDE`) + PDF | détenteur, sens (hausse/baisse), seuils, % capital/DV, ISIN, titre |
| **2** — `amf_market.py` | Enrichissement des titres franchis | TwelveData (cours) + infra sociale | analyse technique + verdict, synthèse analystes, note sociale |
| **3** — `amf_shorts.py` | Positions courtes nettes | CSV open data AMF (data.gouv, MAJ quotidienne) | détenteur, émetteur, **ratio % du capital**, ISIN, dates |
| **4** — `amf_market.py` | Enrichissement des titres shortés | idem agent 2 | idem agent 2 |

L'orchestrateur `amf_orchestrator.py` enchaîne les agents, génère les résumés
(`amf_summary.py`) et écrit `amf_latest.json`, lu par `amf_dashboard.html`.

## Fonctionnement

```
amf_orchestrator.py
 ├─ agent 1  amf_bdif.collect(deb, fin)      → franchissements du jour
 ├─ agent 3  amf_shorts.collect(deb, fin)    → positions courtes du jour
 ├─ agents 2&4  amf_market.enrich(isin)      → technique / analystes / social  (1×/ISIN, mémoïsé)
 ├─ amf_summary.summarize_*()                → résumé du contenu (Claude ou repli)
 └─ écrit amf_latest.json                    → servi par app.py sur /amf_latest.json
```

**Planification** : passage quotidien dans la fenêtre **16h30–17h00 (Europe/Paris)**
+ un passage immédiat au démarrage du conteneur. Les week-ends sont sautés et la
fenêtre de collecte s'élargit automatiquement s'il n'y a rien publié le jour même
(jours fériés).

## Détails de fidélité aux données

- **Positions courtes** : l'AMF publie un **ratio en % du capital** (règlement UE
  236/2012, seuil de publication 0,5 %), et non en % du flottant. Le dashboard
  l'affiche donc comme « % du capital ».
- **Synthèse analystes** : aucune source *gratuite* fiable ne couvre le consensus
  sell-side sur Euronext → affiché « N/A ». Point d'extension `AMF_ANALYST_PROVIDER=fmp`
  (+ `FMP_API_KEY`) pour brancher Financial Modeling Prep.
- **Note sociale** : réutilise la table `mentions` du projet (ApeWisdom/Adanos),
  interrogée par symbole. La couverture des small caps FR y est faible → « N/A »
  fréquent, ce qui est fidèle.
- **Résumés** : Claude API si `ANTHROPIC_API_KEY` est défini, sinon repli
  déterministe construit depuis les champs extraits (le pipeline ne casse jamais).

Toutes les briques se **dégradent proprement** sans clé : sans `TWELVEDATA_API_KEY`
la colonne technique affiche « N/A », etc.

## Utilisation

```bash
# un passage pour aujourd'hui
python amf_orchestrator.py --once

# une date précise
python amf_orchestrator.py --date 2026-08-04

# boucle planifiée (lancée automatiquement par app.py sur Railway)
python amf_orchestrator.py

# tester un agent isolément
python amf_bdif.py --days 3
python amf_shorts.py --date 2026-08-04
python amf_shorts.py --active          # positions actuellement ouvertes
python amf_market.py                   # indicateurs (données synthétiques hors-ligne)
```

## Configuration (voir `.env.example`)

Clés utiles : `ANTHROPIC_API_KEY`, `TWELVEDATA_API_KEY`, `AMF_ENABLE`,
`AMF_RUN_HOUR`/`AMF_RUN_MINUTE`, `AMF_LOOKBACK_DAYS`, `AMF_TD_SLEEP`,
`AMF_SUMMARY_MODEL`, `AMF_ANALYST_PROVIDER`/`FMP_API_KEY`.

Sur Railway, l'orchestrateur démarre avec le service web (voir `app.py`,
`spawn_workers`). Le désactiver : `AMF_ENABLE=0`.

## Avertissement

Aide à la décision fondée sur des données publiques réglementaires et des
indicateurs automatiques. **Ce n'est pas un conseil d'investissement** ni une
recommandation d'achat/vente.
