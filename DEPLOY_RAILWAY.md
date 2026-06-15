# SCCT-Social — Déploiement Railway (24/7)

Un seul service web (`app.py`) qui sert le dashboard ET lance le collecteur + le scan
en arrière-plan. Stockage Postgres (persistant). Indépendant de ton Mac.

## Architecture
```
[Service web Railway]  python app.py
   ├─ Flask : sert /  (dashboard.html) + /scan_latest.json
   ├─ social_collector.py  (continu)  → Postgres (mentions)
   └─ boucle scct_scan.py  (toutes les 30 min) → scan_latest.json
[Postgres Railway]  ← DATABASE_URL (auto)
```

---

## Étape 0 — Pré-requis
- Compte GitHub + compte Railway (railway.app).
- Tes clés : Adanos, Ortex, TwelveData (déjà dans ton `.env` local).

## Étape 1 — Pousser SEULEMENT le dossier `collector/` sur GitHub
⚠️ Ne pousse jamais le dossier `Social Money` entier (il contient `KEYS API .docx`).
```bash
cd "/Users/nicholas/Desktop/Claude/Social Money/SCCT Project/collector"
git init
git add .
git status   # VÉRIFIE qu'aucun .env ni *.db n'apparaît (ils sont dans .gitignore)
git commit -m "SCCT collector + scanner + dashboard"
git branch -M main
git remote add origin https://github.com/<toi>/scct-collector.git
git push -u origin main
```
Le `.gitignore` exclut `.env`, `*.db`, `logs/`, `__pycache__/`, `scan_latest.json`.

## Étape 2 — Créer le projet Railway
1. railway.app → **New Project → Deploy from GitHub repo** → choisis `scct-collector`.
2. Railway détecte Python (Nixpacks) et lance `python app.py` (via `railway.json`).

## Étape 3 — Ajouter Postgres
1. Dans le projet : **New → Database → Add PostgreSQL**.
2. Railway crée la variable **`DATABASE_URL`** automatiquement → `storage.py` la détecte
   et bascule en Postgres (la baseline survit aux redéploiements).

## Étape 4 — Variables d'environnement
Onglet **Variables** du service web, ajoute :
```
ADANOS_API_KEY        = sk_live_...
ORTEX_API_KEY         = PdV7...
TWELVEDATA_API_KEY    = 7e03...
SEC_USER_AGENT        = Nicholas nm@nmgroup.be
ORTEX_SKIP_CTB        = 1
COLLECTOR_NO_ADANOS   = 1     # baseline ApeWisdom seule -> économise le quota Adanos
APEWISDOM_PAGES       = 10
POLL_SECONDS          = 1200
SCAN_EVERY            = 1800
```
(Ne PAS mettre `PORT` ni `DATABASE_URL` : Railway les fournit.)

## Étape 5 — Exposer le dashboard
1. Onglet **Settings → Networking → Generate Domain** (ou "Public Networking").
2. Railway te donne une URL `https://scct-collector-production.up.railway.app`.
3. Ouvre-la : c'est ton dashboard, accessible partout.
   - `/`               → dashboard
   - `/scan_latest.json` → données brutes
   - `/health`         → contrôle de vie

## Étape 6 — Vérifier
- Onglet **Deployments → Logs** : tu dois voir `workers lancés` puis, après ~5 min,
  `cycle: {...} -> N snapshots insérés` et les scans.
- Le dashboard se peuple au fil des scans (toutes les 30 min).

---

## Coûts mensuels (ordre de grandeur)
- Railway : ~5 $/mois (hobby) selon usage.
- Adanos : Pro 299 $ (raw mentions/C2) **ou** Hobby 29 $ (sans raw → C2 dégradé).
  Avec `COLLECTOR_NO_ADANOS=1`, le collecteur n'utilise pas Adanos ; seul le scan
  consomme du quota Adanos (léger).
- Ortex : Basic 39 $.
- TwelveData / SEC / ApeWisdom : gratuit.

## Rappels
- Détection only. Décisions de trade et risque = les tiens. Pas un conseil financier.
- Le backtest (F1 0.88) repose sur un petit échantillon → valeur indicative.
- Ligne rouge : ne jamais coupler la diffusion de signaux à des positions sans divulgation.
