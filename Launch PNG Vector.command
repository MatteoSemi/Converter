#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
if [[ ! -x .venv/bin/python ]]; then
  echo "Run bash scripts/setup-mac.sh in this folder first. See MAC_START_HERE.md."
  read -r -p "Press Return to close. " _
  exit 1
fi
if command -v brew >/dev/null 2>&1; then
  export DYLD_FALLBACK_LIBRARY_PATH="$(brew --prefix cairo)/lib:${DYLD_FALLBACK_LIBRARY_PATH:-/usr/local/lib:/usr/lib}"
fi
if ! .venv/bin/python -m pngvector; then
  echo "Launch failed. Run .venv/bin/python -m pngvector.doctor and share the error message."
  read -r -p "Press Return to close. " _
  exit 1
fi
