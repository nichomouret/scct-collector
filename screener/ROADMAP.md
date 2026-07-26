# État du projet — screener de dislocation multi-signaux

Carte des phases de la SPEC (§11) vers ce qui est implémenté dans `screener/`.
Voir `SPEC.md` (référence), `CLAUDE.md` (carte des modules) et `README.md` (usage).

## Phases

| Phase | Contenu (SPEC §11) | État |
|---|---|---|
| **P0** | Ingestion prix US+EU, schéma, univers | ⚙️ **Partiel** — prix (Yahoo) + univers US (SEC). Fondamentaux/PIT DB non faits. |
| **P1** | **Registre des catalyseurs** | ✅ **Fait** — ClinicalTrials.gov + CSV manuel, point-in-time (`--as-of`). |
| **P2** | Modèle factoriel, résidus, détection | ⚙️ **Partiel** — résidu **marché-neutre** (bêta unique), DIS, z-volume. Le 4-facteurs (§4.1) reste la cible. |
| **P3** | Valorisation + quality gate + value trap | ❌ **Non fait** — `VAL_z`, bandes FV, `QUAL` consommés par le moteur mais non calculés (viennent de l'overlay). |
| **P4** | Classification news LLM + fiche | ✅ **Fait** — NewsAPI + Claude (sortie structurée) → cause/permanence/horizon + **analyse par titre** ; fiche 1 page. |
| **P5** | **Socle S1-S5, 5 routes, time-stop, sizing** | ✅ **Fait** — cœur du moteur de décision, + couche PATH (v1.3). |
| **P6** | **Backtest** (protocole §10) | ✅ **Fait (périmètre v1)** — signal price-derived (proxy route A) ; toute la machinerie §10. B/C/E/D en attente d'inputs historiques. |
| **P7** | Module analystes (AQS) + options | ⚙️ **Partiel** — Ortex (short interest/emprunt, §4.3) fait ; AQS et surface d'options non faits. |
| **P8** | Marchés prédictifs + filtre de régime | ❌ **Non fait** — `PMS` (§5.2) et filtre de régime (§12) consommés mais non alimentés. |
| **P9** | Extension TASE + TSX | ❌ **Non fait** — architecture région-aware prête (`Region`, seuils IL), sources non branchées. |

Hors périmètre SPEC v1.1 mais branché en **contexte** (hors scoring) : tendance
sociale Adanos.

## Chaîne runnable aujourd'hui

```
build_universe → build_catalysts → build_short_interest → build_news → build_social
        └────────────────────────── run (short-list + fiches) ──────────────────────┘
run_backtest   (go/no-go §10)
```

Toutes les couches d'ingestion **dégradent gracieusement** sans clé : le moteur
(socle + routes + conviction + PATH + sorties) est stdlib pur, déterministe,
et tourne sur le seul overlay manuel si aucune API n'est configurée.

## Limites connues (à assumer / lever)

1. **Biais de survivance** (§10.4) — univers SEC = émetteurs encore cotés → backtest optimiste. Corriger = source payante avec délistés.
2. **Périmètre backtest** — seule la route A (technique, price-derived) est backtestée. B/C/E exigent des inputs qualitatifs historiques déterministes (garde anti-fuite LLM §10.3) ; D des catalyseurs archivés PIT.
3. **Résidu 1-facteur** — simplification de §4.1 (marché seul, pas secteur/taille/valeur).
4. **P3/P8 absents** — `VAL_z`, bandes FV, `PMS`, filtre de régime viennent de l'overlay, pas encore calculés.
5. **Significativité** — atteindre les 150 trades / 40-par-route (§10.9-11) exige un univers de plusieurs centaines de noms et un historique long.

## Prochaines étapes à plus fort levier

- **Élargir l'univers** (200-500 small/mid) + historique long → premier go/no-go sérieux sur la route A.
- **P3 valorisation** — calculer `VAL_z` / bandes FV depuis les fondamentaux (débloque la route E et le coussin S4a en réel).
- **Catalyseurs PIT archivés** — snapshots datés → backtester la route D.
- **AQS analystes** (P7) et **surface d'options** (P7, `expected_move_pct` réel, route D).
