#!/usr/bin/env bash
# =============================================================================
# Scan quotidien — régénère scan.html (short-list du jour) et shortlist.html.
# =============================================================================
# Conçu pour être lancé à la main OU par cron. Robuste : une étape de
# qualification qui échoue (réseau, quota) n'interrompt pas le scan.
#
#   À la main :   ./screener/run_daily.sh
#   Cron (voir README / .env.example) : 0 7 * * 1-5 .../screener/run_daily.sh
#
# Clés API : place-les dans screener/.env.local (non versionné), une par ligne :
#   ORTEX_API_KEY=...
#   NEWS_API_KEY=...
#   ANTHROPIC_API_KEY=...
# Sans ce fichier, le scan tourne quand même (prix seuls, capi via SEC).
# =============================================================================
set -uo pipefail

# --- Racine du dépôt (ce script est dans screener/) ---
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
cd "$ROOT"

# --- venv si présent ---
# shellcheck disable=SC1091
[ -f .venv/bin/activate ] && source .venv/bin/activate

# --- clés locales optionnelles ---
if [ -f screener/.env.local ]; then
  set -a; # shellcheck disable=SC1091
  source screener/.env.local; set +a
fi

DATA="screener/data"
SAMPLE="${SCAN_SAMPLE:-800}"        # nombre de titres scannés (SCAN_SAMPLE pour surcharger)
DIS_MIN="${SCAN_DIS_MIN:-2.5}"

echo "=== Scan quotidien $(date '+%Y-%m-%d %H:%M') — univers ${SAMPLE}, DIS>=${DIS_MIN} ==="

# 1. univers propre (NYSE/Nasdaq, ordinaires) + capi via SEC ensuite
python -m screener.build_universe --sample "$SAMPLE" --skip-top 300 \
  --default-coverage 3 --out "$DATA/big.csv" || { echo "build_universe a échoué"; exit 1; }

# 2. scan : short-list + pont univers + gabarit overlay
python -m screener.scan --universe "$DATA/big.csv" \
  --dis-min "$DIS_MIN" --fresh-max-days 5 \
  --html "$DATA/scan.html" \
  --emit-universe "$DATA/hits.csv" \
  --emit-overlay "$DATA/overlay.csv" || { echo "scan a échoué"; exit 1; }

# 3. qualification légère sur les seules touches (n'interrompt pas si ça casse)
python -m screener.build_fundamentals   --universe "$DATA/hits.csv" || true
python -m screener.build_short_interest --universe "$DATA/hits.csv" || true
python -m screener.build_news           --universe "$DATA/hits.csv" || true

# 4. dossiers + dashboard (admis seulement si une route valide)
python -m screener.run --universe "$DATA/hits.csv" \
  --fundamentals   "$DATA/fundamentals.built.csv" \
  --short-interest "$DATA/short_interest.built.csv" \
  --news           "$DATA/news.built.csv" \
  --dashboard      "$DATA/shortlist.html" || true

echo "=== Terminé. Ouvrir :"
echo "    file://$ROOT/$DATA/scan.html        (short-list technique + stops/objectifs)"
echo "    file://$ROOT/$DATA/shortlist.html   (dossiers admis, si overlay rempli)"
