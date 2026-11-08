#!/usr/bin/env bash
# Control script for the Northstar services inside a judas-incident-eval sandbox.
#   judasctl.sh setup | start | stop | status
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p logs run

health() { curl -sf "http://localhost:$1/health" >/dev/null 2>&1; }

case "${1:-status}" in
  setup)
    # the workspace image ships uv; its python lacks ensurepip, so build the venv with uv
    if [[ ! -x venv/bin/python ]]; then uv venv -q venv; fi
    venv/bin/python -c 'import pg8000' 2>/dev/null || uv pip install -q --python venv/bin/python 'pg8000>=1.31'
    echo "setup ok: $(venv/bin/python --version), pg8000 $(venv/bin/python -c 'import pg8000; print(pg8000.__version__)')"
    ;;
  start)
    "$0" stop >/dev/null 2>&1 || true
    for role in incident checkout; do
      nohup venv/bin/python northstar.py "$role" >>"logs/$role.log" 2>&1 &
      echo $! >"run/$role.pid"
    done
    for i in $(seq 1 40); do
      if health 8080 && health 8081; then echo "services healthy (incident :8080, checkout :8081)"; exit 0; fi
      sleep 0.5
    done
    echo "services did not become healthy"; tail -n 5 logs/incident.log logs/checkout.log; exit 1
    ;;
  stop)
    for role in incident checkout; do
      [[ -f "run/$role.pid" ]] && kill "$(cat "run/$role.pid")" 2>/dev/null || true
      rm -f "run/$role.pid"
    done
    echo "services stopped"
    ;;
  status)
    for p in 8080 8081; do if health $p; then echo ":$p healthy"; else echo ":$p down"; fi; done
    ;;
esac
