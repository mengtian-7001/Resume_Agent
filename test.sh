#!/usr/bin/env bash
# One-command unit tests for a fresh clone / CI.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT/backend"

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
PIP_DISABLE_PIP_VERSION_CHECK=1 python -m pip install -q --disable-pip-version-check -r requirements-dev.txt
python -m pytest "$@"
PYTHONPATH="$ROOT/backend" python "$ROOT/backend/scripts/run_sample_doc_eval.py"
PYTHONPATH="$ROOT/backend" python "$ROOT/backend/scripts/run_heldout_doc_eval.py"
