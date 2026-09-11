#!/usr/bin/env bash
set -euo pipefail
repo_dir="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo_dir"
export UV_CACHE_DIR="/tmp/finance-terminal-uv-cache"
uv run --with pyinstaller pyinstaller --clean --noconfirm --onefile \
  --name reasonframe-backend \
  --distpath /tmp/finance-terminal-sidecar-dist \
  --workpath /tmp/finance-terminal-sidecar-build \
  --specpath /tmp/finance-terminal-sidecar-spec \
  --collect-all edgar \
  --collect-all finance_terminal \
  --collect-all openai_codex \
  --collect-all codex_cli_bin \
  --hidden-import finance_terminal.api \
  src/finance_terminal/desktop_backend.py
mkdir -p frontend/src-tauri/binaries
cp /tmp/finance-terminal-sidecar-dist/reasonframe-backend frontend/src-tauri/binaries/reasonframe-backend
chmod 755 frontend/src-tauri/binaries/reasonframe-backend
