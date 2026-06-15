# SCCT-Social · Collecte sociale — déploiement

## ⚠️ Contexte : API Reddit verrouillée (juin 2026)

Reddit a fermé l'accès self-service à la Data API : création d'app gated par une
**approbation** (formulaire ticket), usage « recherche » interdit hors programme
RFR, usage **commercial** soumis à contrat, et accès depuis une **IP hébergée**
(Railway) restreint sans OAuth valide. Réf. : [Reddit Help — Developer Platform & Accessing Reddit Data](https://support.reddithelp.com/hc/en-us/articles/14945211791892-Developer-Platform-Accessing-Reddit-Data).

**Décision : voie principale = fournisseurs tiers licenciés.** Le collecteur Reddit
direct (`reddit_collector.py`) reste en **réserve**, activable si Reddit approuve
une demande Data API non-commerciale.

---

## Contenu

```
collector/
├── social_collector.py    # ★ VOIE PRINCIPALE — ApeWisdom (+ Adanos/Quiver)
├── compute_signals.py     # C1 (volume spike Z-score) depuis la série mentions
├── reddit_collector.py    # RÉSERVE — collecte Reddit directe (si approbation)
├── ticker_extractor.py    # extraction $XXX (utilisé par reddit_collector)
├── build_ticker_universe.py
├── storage.py             # SQLite/Postgres ; tables posts + mentions
├── requirements.txt · Procfile · railway.json · .env.example · .gitignore
```

---

## Ce que cette voie capte / ne capte pas

| Composante | Via fournisseurs tiers | Note |
|---|---|---|
| **C1 — volume spike** | ✅ | Z-score sur la série de mentions (`compute_signals.py`) |
| **C2 — burstiness** | ⚠️ partiel | Vélocité de mentions oui ; **âge compte/karma NON** (les agrégateurs ne les donnent pas) |
| **C2 — synchro multi-sources** | ✅ | ApeWisdom + Adanos + Quiver = plusieurs sources |
| **C2 — similarité lexicale / comptes neufs** | ❌ | Nécessite les posts bruts → seulement via API Reddit directe (réserve) |
| **C3 — antériorité** | ✅ | Mentions vs prix/news sur la même timeline |

> Conséquence : par cette voie, **C2 est partiellement observable**. Suffisant pour
> démarrer le forward-test et calibrer C1/C3 ; le sous-signal « comptes neufs » sera
> ajouté si/quand l'accès Reddit direct est approuvé.

---

## ⏱️ Quand configurer Railway & GitHub

| Étape | Quoi | Quand passer à la suite |
|---|---|---|
| **1** | **Test LOCAL** : `RUN_ONCE=1 python social_collector.py` (ApeWisdom, gratuit, sans clé) | quand un passage logue « N snapshots insérés » |
| **2** | Vérifier la couverture micro-cap : `python api_coverage_check.py` (OCC ressort ?) | si OCC + famille-3 sont GO |
| **3** | **➜ GitHub** : pousser `collector/` | repo en ligne |
| **4** | **➜ Railway** : déployer + variables + Postgres | logs Railway montrent des cycles |
| **5** | Laisser tourner 1–2 semaines, puis `compute_signals.py` | assez d'historique pour le baseline C1 |

> **Déclencheur GitHub + Railway = fin de l'étape 2.** Tu déploies une fois que le
> test local récupère des données ET que la couverture micro-cap est confirmée.

---

## Étape 1 — Test local (immédiat, gratuit)

```bash
cd collector
pip install -r requirements.txt
RUN_ONCE=1 python3 social_collector.py
```
Attendu : `cycle: {'apewisdom': N} -> M snapshots insérés`. Un fichier `scct_posts.db` est créé.
Pour cibler tes tickers : `WATCHLIST=OCC,OPEN,KSS RUN_ONCE=1 python3 social_collector.py`

## Étape 2 — Couverture micro-cap

```bash
python3 api_coverage_check.py --csv coverage_report.csv
```
Si **OCC = ABSENT** chez tous : augmente `APEWISDOM_PAGES`, et si toujours creux,
ce ticker passera en validation forward-test (réserve Reddit) — ne paie aucun tier
tant qu'OCC + famille-3 ne sont pas GO.

## Étape 3 — GitHub

```bash
cd collector
git init && git add . && git commit -m "SCCT social collector"
git branch -M main
git remote add origin https://github.com/<toi>/scct-collector.git
git push -u origin main
```
`.gitignore` exclut `.env` et `*.db` — clés et base ne partent pas sur GitHub.

## Étape 4 — Railway

1. railway.app → **New Project → Deploy from GitHub repo**.
2. **Variables** : recopie `.env` (sans `RUN_ONCE`). `startCommand` = `python social_collector.py` (voir `railway.json`).
3. **Persistance** : ajoute un **Postgres** (*New → Database → Postgres*) → `DATABASE_URL` détecté automatiquement. Sinon Volume + `DB_PATH=/data/scct_posts.db`.
4. Vérifie les logs : un cycle toutes les ~20 min.

> Avantage du pivot : ApeWisdom n'impose pas d'OAuth, donc la restriction « IP hébergée »
> de Reddit ne s'applique pas — Railway fonctionne sans friction.

## Étape 5 — Signaux

Après quelques jours :
```bash
python3 compute_signals.py --top 25            # Z-score C1 par ticker
python3 compute_signals.py --csv signals.csv
```
Le baseline 30j de la spec demande ~30 jours ; `n_obs` indique la fiabilité du Z d'ici là.

---

## Activer la réserve Reddit (plus tard, si approuvé)

1. Demande Data API **non-commerciale** : https://support.reddithelp.com/hc/en-us/requests/new?ticket_form_id=14868593862164
   (ne pas qualifier de « recherche » ; si monétisation prévue → voie commerciale + contrat).
2. Une fois les credentials obtenus : remplis le bloc RESERVE du `.env`, génère l'univers
   (`build_ticker_universe.py`), et lance `reddit_collector.py` à la place — il alimente
   la table `posts` avec les métadonnées de compte qui complètent C2.

---

## Conformité

Collecte en lecture seule, **détection uniquement**. Ne jamais coupler à une diffusion
de signaux sans divulgation des positions (cf. `00_Project_Instructions.md`). L'usage des
données tierces reste soumis aux licences de chaque fournisseur (commercial = contrat).
