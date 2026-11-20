#!/bin/bash
# Levanta la web estática (uis/, puerto 8080) y la user-api (FastAPI, puerto 8000).
# Uso: bash .claude/skills/run-web/start.sh        -> arranca
#      bash .claude/skills/run-web/start.sh stop   -> para
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
LOG_DIR="${LOG_DIR:-${TMPDIR:-/tmp}/trackflow-run}"
mkdir -p "$LOG_DIR"

if [ "${1:-}" = "stop" ]; then
  pkill -f "uvicorn app.main:app" || true
  pkill -f "http.server 8080" || true
  echo "Parado."
  exit 0
fi

# Variables mínimas que la API exige (no hay .env en el repo).
# Si existe services/user-api/.env, se cargan sus valores primero.
if [ -f "$ROOT/services/user-api/.env" ]; then
  set -a; . "$ROOT/services/user-api/.env"; set +a
fi
export JWT_SECRET_KEY="${JWT_SECRET_KEY:-$(python3 -c 'import secrets;print(secrets.token_hex(32))')}"
export ACCESS_TOKEN_EXPIRE_MINUTES="${ACCESS_TOKEN_EXPIRE_MINUTES:-60}"
DEFAULT_CORS="http://localhost:8080,http://127.0.0.1:8080"
if [ -n "${CODESPACE_NAME:-}" ]; then
  DEFAULT_CORS="$DEFAULT_CORS,https://${CODESPACE_NAME}-8080.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-app.github.dev}"
fi
export CORS_ALLOW_ORIGINS="${CORS_ALLOW_ORIGINS:-$DEFAULT_CORS}"
export RESET_PASSWORD_URL="${RESET_PASSWORD_URL:-http://localhost:8080/reset-password.html}"

cd "$ROOT/services/user-api"
nohup "$ROOT/.venv/bin/uvicorn" app.main:app --host 0.0.0.0 --port 8000 > "$LOG_DIR/api.log" 2>&1 &

cd "$ROOT/uis"
nohup python3 -m http.server 8080 > "$LOG_DIR/web.log" 2>&1 &

# Esperar a que respondan
for i in $(seq 1 20); do
  if curl -sf -o /dev/null http://127.0.0.1:8000/docs && curl -sf -o /dev/null http://127.0.0.1:8080/; then
    echo "OK  web: http://localhost:8080   api: http://localhost:8000/docs"
    if [ -n "${CODESPACE_NAME:-}" ]; then
      echo "Codespaces (abrir en el navegador): https://${CODESPACE_NAME}-8080.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-app.github.dev}"
    fi
    echo "Logs: $LOG_DIR/api.log  $LOG_DIR/web.log"
    exit 0
  fi
  sleep 1
done
echo "No arrancó a tiempo. Revisa $LOG_DIR/api.log y $LOG_DIR/web.log" >&2
exit 1
