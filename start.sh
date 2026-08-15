#!/usr/bin/env bash
# Zero-config local demo for a fresh clone.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
PORT="${PORT:-4174}"
HOST="${HOST:-127.0.0.1}"
URL="http://${HOST}:${PORT}/index.html"

if ! command -v python3 >/dev/null 2>&1; then
  echo "未找到 Python 3。请先安装 Python 3，然后重新运行 ./start.sh。"
  exit 1
fi

echo
echo "=========================================="
echo "  简历中台 · 零配置演示模式"
echo "  打开：${URL}"
echo "  无需 Supabase、模型 Key 或依赖安装"
echo "  页面中点击「查看示例结果」体验完整交付"
echo "  按 Ctrl+C 停止服务"
echo "=========================================="
echo

if [[ "${NO_OPEN:-0}" != "1" ]]; then
  if command -v open >/dev/null 2>&1; then
    (sleep 0.5; open "$URL") >/dev/null 2>&1 &
  elif command -v xdg-open >/dev/null 2>&1; then
    (sleep 0.5; xdg-open "$URL") >/dev/null 2>&1 &
  fi
fi

cd "$ROOT"
exec python3 -m http.server "$PORT" --bind "$HOST"
