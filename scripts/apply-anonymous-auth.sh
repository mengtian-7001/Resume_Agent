#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MIGRATION="$ROOT/supabase/migrations/20260813204500_anonymous_workspace_bootstrap.sql"

echo "匿名登录迁移 SQL：$MIGRATION"
echo
echo "任选一种方式执行（无需浏览器自动化）："
echo
echo "方式 1 — Supabase Dashboard（推荐，约 30 秒）"
echo "  1. 打开 https://supabase.com/dashboard/project/floumalipbshdgtuyxym/sql/new"
echo "  2. 粘贴下面文件的全部内容"
echo "  3. 点击 Run，并在 destructive 提示里确认"
echo
echo "方式 2 — psql（若已有数据库连接串）"
echo "  psql \"\$DATABASE_URL\" -f \"$MIGRATION\""
echo
echo "方式 3 — Supabase CLI"
echo "  brew install supabase/tap/supabase"
echo "  supabase login"
echo "  supabase link --project-ref floumalipbshdgtuyxym"
echo "  supabase db push"
echo
echo "执行完成后，刷新 http://localhost:4174/index.html 即可。"
echo "页面会自动匿名登录，无需邮箱。"
echo
echo "--- SQL 内容预览（前 20 行）---"
sed -n '1,20p' "$MIGRATION"
