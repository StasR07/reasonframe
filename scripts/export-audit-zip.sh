#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
output="${1:-/tmp/finance-terminal-audit.zip}"

if [[ -e "$output" ]]; then
  echo "Refusing to overwrite existing archive: $output" >&2
  exit 2
fi

cd "$project_root"
zip -q -r "$output" . \
  -x '.git/*' '.venv/*' '.venv.*/*' 'venv/*' \
  -x 'frontend/node_modules/*' 'frontend/dist/*' 'node_modules/*' 'dist/*' 'build/*' \
  -x 'coverage/*' 'htmlcov/*' '.pytest_cache/*' '*/__pycache__/*' '*.pyc' \
  -x '.finance-terminal/*' 'codex-home/*' '*/codex-home/*' \
  -x '*.db' '*.db-*' '*.sqlite' '*.sqlite3' \
  -x '.env' '.env.*' '*/.env' '*/.env.*' \
  -x 'auth.json' '*/auth.json' '*token*' '*credential*' '*.log' \
  -x '__MACOSX/*' '.DS_Store' '*/.DS_Store' '*.zip'
zip -q "$output" .env.example
if [[ -f frontend/.env.example ]]; then zip -q "$output" frontend/.env.example; fi

echo "$output"
