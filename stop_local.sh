#!/bin/bash
# Arrête les processus lancés par run_local.sh
cd "$(dirname "$0")"
for p in collector scan dashboard; do
  if [ -f "logs/$p.pid" ]; then
    pid=$(cat "logs/$p.pid")
    if kill "$pid" 2>/dev/null; then echo "arrêté $p (PID $pid)"; else echo "$p déjà arrêté"; fi
    rm -f "logs/$p.pid"
  fi
done
echo "Terminé."
