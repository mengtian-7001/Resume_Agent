#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$ROOT/backend/.env"
EXAMPLE="$ROOT/backend/.env.example"
FRONTEND_CONFIG="$ROOT/supabase-config.js"

if [[ ! -f "$FRONTEND_CONFIG" ]]; then
  echo "缺少 supabase-config.js，请先复制 supabase-config.example.js 并填写 Supabase 项目配置。"
  exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
  cp "$EXAMPLE" "$ENV_FILE"
  echo "已创建 backend/.env"
fi

read_config_value() {
  local key="$1"
  python3 - "$FRONTEND_CONFIG" "$key" <<'PY'
import re, sys
path, key = sys.argv[1], sys.argv[2]
text = open(path, encoding="utf-8").read()
match = re.search(rf'{key}\s*:\s*"([^"]+)"', text)
print(match.group(1) if match else "")
PY
}

SUPABASE_URL="$(read_config_value url)"
WORKSPACE_FROM_CONFIG="$(read_config_value workspaceId)"

if [[ -n "$SUPABASE_URL" ]] && grep -q 'https://your-project.supabase.co' "$ENV_FILE"; then
  if [[ "$(uname)" == "Darwin" ]]; then
    sed -i '' "s|SUPABASE_URL=.*|SUPABASE_URL=${SUPABASE_URL}|" "$ENV_FILE"
  else
    sed -i "s|SUPABASE_URL=.*|SUPABASE_URL=${SUPABASE_URL}|" "$ENV_FILE"
  fi
fi

if grep -q 'replace-with-a-long-random-token' "$ENV_FILE"; then
  TOKEN="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(24))
PY
)"
  if [[ "$(uname)" == "Darwin" ]]; then
    sed -i '' "s|INTERNAL_API_TOKEN=.*|INTERNAL_API_TOKEN=${TOKEN}|" "$ENV_FILE"
  else
    sed -i "s|INTERNAL_API_TOKEN=.*|INTERNAL_API_TOKEN=${TOKEN}|" "$ENV_FILE"
  fi
  echo "已生成 INTERNAL_API_TOKEN"
fi

if grep -q 'replace-with-service-role-key' "$ENV_FILE"; then
  if [[ -n "${SUPABASE_SERVICE_ROLE_KEY:-}" ]]; then
    KEY="$SUPABASE_SERVICE_ROLE_KEY"
  else
    echo
    echo "还需要 Supabase 的 service_role key（仅本地 Worker 使用，不会写入前端）。"
    echo "获取位置：Supabase Dashboard → Project Settings → API → service_role"
    echo "也可先 export SUPABASE_SERVICE_ROLE_KEY=... 再运行本脚本。"
    echo
    read -rsp "请粘贴 service_role key: " KEY
    echo
  fi
  if [[ -z "$KEY" ]]; then
    echo "未提供 service_role key，Worker 无法连接数据库。"
    exit 1
  fi
  python3 - "$ENV_FILE" "$KEY" <<'PY'
import sys
path, key = sys.argv[1], sys.argv[2]
text = open(path, encoding="utf-8").read()
text = text.replace("replace-with-service-role-key", key.replace("\\", "\\\\"))
open(path, "w", encoding="utf-8").write(text)
PY
  echo "已写入 SUPABASE_SERVICE_ROLE_KEY"
fi

if [[ ! -d "$ROOT/backend/.venv" ]]; then
  echo "创建 Python 虚拟环境…"
  python3 -m venv "$ROOT/backend/.venv"
fi

# shellcheck disable=SC1091
source "$ROOT/backend/.venv/bin/activate"
pip install -q -r "$ROOT/backend/requirements.txt"

if [[ ! -f "$ROOT/samples/manifest.json" ]]; then
  python3 "$ROOT/scripts/generate_sample_documents.py"
fi

if [[ -n "$WORKSPACE_FROM_CONFIG" ]]; then
  echo "工作区 ID：$WORKSPACE_FROM_CONFIG"
fi
echo "本地环境就绪：backend/.env"
