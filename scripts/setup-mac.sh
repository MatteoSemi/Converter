#!/bin/bash
# Source installation for macOS; requires Homebrew. No image uploads.
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This installer is for macOS. See README.md for Linux instructions." >&2
  exit 1
fi
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
if ! command -v brew >/dev/null 2>&1; then
  echo "Install Homebrew from https://brew.sh, then run this script again." >&2
  exit 1
fi
brew install python@3.12 ghostscript potrace cairo
MAC_PYTHON="$(brew --prefix python@3.12)/bin/python3.12"
export DYLD_FALLBACK_LIBRARY_PATH="$(brew --prefix cairo)/lib:${DYLD_FALLBACK_LIBRARY_PATH:-/usr/local/lib:/usr/lib}"
if [[ -d .venv ]]; then
  if ! .venv/bin/python -c 'import sys; assert sys.version_info[:2] == (3, 12)' 2>/dev/null; then
    echo "Existing .venv is incompatible. Rename it yourself before retrying; it was not overwritten." >&2
    exit 1
  fi
else
  "$MAC_PYTHON" -m venv .venv
fi
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pngvector.doctor
echo "Setup complete. Run: bash 'Launch PNG Vector.command'"
