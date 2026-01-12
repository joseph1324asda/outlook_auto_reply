#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if ! command -v python >/dev/null 2>&1; then
  echo "Python not found. Install Python 3 and ensure it is on PATH." >&2
  exit 1
fi

if [[ "${1:-}" != "--no-install" ]]; then
  python -m pip install -r requirements.txt
fi

export LLM_MAILER_SSL="adhoc"
export PORT="7999"

python -m llm_mailer.web_app
