#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$ROOT/backend/.env"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "缺少 backend/.env，请先执行："
  echo "  cd backend && cp .env.example .env"
  echo "并填写 SUPABASE_SERVICE_ROLE_KEY 与 INTERNAL_API_TOKEN"
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

PORT="${WORKER_PORT:-8000}"
TOKEN="${INTERNAL_API_TOKEN:?请在 backend/.env 中设置 INTERNAL_API_TOKEN}"

echo "Worker 循环处理中（Ctrl+C 停止）→ http://127.0.0.1:${PORT}/internal/tasks/run-once"
while true; do
  RESULT=$(curl -s -X POST "http://127.0.0.1:${PORT}/internal/tasks/run-once" \
    -H "X-Internal-Token: ${TOKEN}" \
    -H "Content-Type: application/json" || echo '{"processed":false,"reason":"worker_unreachable"}')
  echo "$(date '+%H:%M:%S') ${RESULT}"
  if echo "$RESULT" | grep -q '"processed": true'; then
    sleep 0.3
  else
    sleep 2
  fi
done
