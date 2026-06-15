#!/usr/bin/env python3
"""
SCCT-Social · Service web (Railway) — dashboard + workers dans un seul conteneur
================================================================================
Un seul service Railway :
  - sert le dashboard (/) et le JSON des signaux (/scan_latest.json)
  - lance EN ARRIÈRE-PLAN le collecteur continu (baseline) + la boucle de scan
Stockage : Postgres si DATABASE_URL est défini (Railway), sinon SQLite local.

Variables d'environnement (à mettre dans Railway) :
  ADANOS_API_KEY, ORTEX_API_KEY, TWELVEDATA_API_KEY, SEC_USER_AGENT
  DATABASE_URL (fourni auto par le Postgres Railway)
  PORT (fourni auto par Railway)
  SCAN_EVERY (défaut 1800), ORTEX_SKIP_CTB=1, COLLECTOR_NO_ADANOS=1 (recommandé)

Lancement : python app.py   (commande de démarrage Railway)
"""
import os, sys, subprocess
from flask import Flask, send_from_directory, jsonify

HERE = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__)


@app.route("/")
def home():
    return send_from_directory(HERE, "dashboard.html")


@app.route("/scan_latest.json")
def scan_json():
    p = os.path.join(HERE, "scan_latest.json")
    if os.path.exists(p):
        return send_from_directory(HERE, "scan_latest.json")
    return jsonify({"generated_utc": "", "min_score": 40, "n_universe": 0,
                    "signals": [], "all": []})


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


def spawn_workers():
    env = os.environ.copy()
    # 1) collecteur continu -> baseline de mentions dans Postgres
    subprocess.Popen([sys.executable, "social_collector.py"], cwd=HERE, env=env)
    # 2) boucle de scan -> rafraîchit scan_latest.json
    every = os.environ.get("SCAN_EVERY", "1800")
    subprocess.Popen([
        "bash", "-c",
        f"while true; do {sys.executable} scct_scan.py --watchlist microcaps.txt "
        f"--discovery 50 --out scan_latest.json; sleep {every}; done"
    ], cwd=HERE, env=env)
    print("workers lancés (collecteur + boucle de scan)", flush=True)


if __name__ == "__main__":
    # éviter le double-spawn du reloader Flask
    if os.environ.get("RUN_WORKERS", "1") == "1":
        spawn_workers()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), use_reloader=False)
