#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
MODE="${1:-auto}"

has_live_config() {
  local config_file="$ROOT/supabase-config.js"
  [[ -f "$config_file" ]] \
    && grep -Eq '"?url"?[[:space:]]*:[[:space:]]*"https?://[^"[:space:]]+"' "$config_file" \
    && grep -Eq '"?anonKey"?[[:space:]]*:[[:space:]]*"[^"[:space:]]+"' "$config_file" \
    && grep -Eq '"?workspaceId"?[[:space:]]*:[[:space:]]*"[^"[:space:]]+"' "$config_file" \
    && ! grep -q 'https://your-project.supabase.co' "$config_file" \
    && ! grep -q 'your-public-anon-key' "$config_file"
}

case "$MODE" in
  --demo)
    exec "$ROOT/start.sh"
    ;;
  --live)
    exec "$ROOT/scripts/dev.sh"
    ;;
  auto)
    if has_live_config; then
      exec "$ROOT/scripts/dev.sh"
    fi
    echo "未检测到 Supabase 配置，自动进入零配置演示模式。"
    echo "如需真实解析，请配置 supabase-config.js 后运行 ./dev.sh --live。"
    exec "$ROOT/start.sh"
    ;;
  *)
    echo "用法：./dev.sh [--demo|--live]"
    exit 2
    ;;
esac
