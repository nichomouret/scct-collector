#!/bin/bash
# SCCT-Social · Lanceur local "always-on"
# Démarre en arrière-plan : (1) le collecteur continu (baseline de mentions),
# (2) une boucle de scan qui rafraîchit scan_latest.json, (3) le serveur du dashboard.
# Les clés sont lues depuis .env (même dossier). Arrêt : ./stop_local.sh
set -e
cd "$(dirname "$0")"

# --- charge les clés depuis .env si présent ---
if [ -f .env ]; then set -a; source .env; set +a; fi
: "${ADANOS_API_KEY:?Manque ADANOS_API_KEY (mets-le dans .env)}"

SCAN_EVERY="${SCAN_EVERY:-1800}"   # secondes entre deux scans (30 min)
mkdir -p logs

echo "Démarrage SCCT local…"

# 1) collecteur continu -> construit la baseline dans scct_posts.db
nohup python3 social_collector.py > logs/collector.log 2>&1 &
echo $! > logs/collector.pid
echo "  collecteur PID $(cat logs/collector.pid) -> logs/collector.log"

# 2) boucle de scan -> rafraîchit scan_latest.json pour le dashboard
nohup bash -c "while true; do \
  ORTEX_SKIP_CTB=1 python3 scct_scan.py --watchlist microcaps.txt --discovery 50 \
    --out scan_latest.json >> logs/scan.log 2>&1; \
  sleep $SCAN_EVERY; done" > /dev/null 2>&1 &
echo $! > logs/scan.pid
echo "  boucle scan PID $(cat logs/scan.pid) (toutes les ${SCAN_EVERY}s) -> logs/scan.log"

# 3) serveur du dashboard
nohup python3 -m http.server 8000 > logs/dashboard.log 2>&1 &
echo $! > logs/dashboard.pid
echo "  dashboard PID $(cat logs/dashboard.pid) -> http://localhost:8000/dashboard.html"

echo "Tout tourne en arrière-plan. Arrêt : ./stop_local.sh"
echo "Suivi : tail -f logs/collector.log  |  tail -f logs/scan.log"
