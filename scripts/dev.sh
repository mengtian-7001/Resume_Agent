#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$ROOT/backend/.env"
WORKER_OVERLAY="$ROOT/supabase-config.worker.js"
FRONTEND_PORT="${FRONTEND_PORT:-4174}"
WORKER_PORT="${WORKER_PORT:-8000}"
PIDS=()

cleanup() {
  local pid
  for pid in "${PIDS[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
  echo
  echo "已停止本地服务。"
}
trap cleanup EXIT INT TERM

"$ROOT/scripts/setup-dev-env.sh"

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

# Never put INTERNAL_API_TOKEN in browser-readable JS. Localhost Demo uses /dev/jobs/process
# (loopback-only). Production callers use session JWT → /api/jobs/process.
cat > "$WORKER_OVERLAY" <<EOF
// 由 scripts/dev.sh 自动生成，请勿提交到 Git。
Object.assign(window.SUPABASE_CONFIG ||= {}, {
  workerUrl: "http://127.0.0.1:${WORKER_PORT}",
});
EOF

# shellcheck disable=SC1091
source "$ROOT/backend/.venv/bin/activate"

while lsof -iTCP:"$FRONTEND_PORT" -sTCP:LISTEN -P >/dev/null 2>&1; do
  FRONTEND_PORT=$((FRONTEND_PORT + 1))
done

echo "启动 Worker → http://127.0.0.1:${WORKER_PORT}"
(
  cd "$ROOT/backend"
  exec uvicorn app.main:app --host 127.0.0.1 --port "$WORKER_PORT"
) &
PIDS+=($!)

echo "等待 Worker 就绪…"
for _ in $(seq 1 30); do
  if curl -sf "http://127.0.0.1:${WORKER_PORT}/health" >/dev/null; then
    break
  fi
  sleep 0.2
done

echo "启动前端 → http://127.0.0.1:${FRONTEND_PORT}"
(
  cd "$ROOT"
  exec python3 -m http.server "$FRONTEND_PORT" --bind 127.0.0.1
) &
PIDS+=($!)

URL="http://127.0.0.1:${FRONTEND_PORT}/index.html"
echo
echo "=========================================="
echo "  简历中台本地环境已启动"
echo "  打开：${URL}"
echo "  提交后点击「一键解析」，页面会自动完成全部流程"
echo "  按 Ctrl+C 停止全部服务"
echo "=========================================="
echo

if command -v open >/dev/null 2>&1; then
  open "$URL"
fi

wait
