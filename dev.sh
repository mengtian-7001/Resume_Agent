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
    && ! grep -q 'Resume Agent public cloud runtime' "$config_file" \
    && ! grep -q 'https://your-project.supabase.co' "$config_file" \
    && ! grep -q 'your-public-anon-key' "$config_file"
}

case "$MODE" in
  --demo)
    exec "$ROOT/start.sh" --demo
    ;;
  --live|--local)
    exec "$ROOT/scripts/dev.sh"
    ;;
  auto)
    if has_live_config; then
      exec "$ROOT/scripts/dev.sh"
    fi
    echo "未检测到自有 Supabase 配置，自动接入公开匿名环境。"
    echo "将启用真实上传、解析、评分、出题和 Checker。"
    exec "$ROOT/start.sh" --connected
    ;;
  *)
    echo "用法：./dev.sh [--demo|--local]"
    exit 2
    ;;
esac
