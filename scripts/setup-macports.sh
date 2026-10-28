#!/bin/bash
# macOS 11+ route using MacPorts. Run from Terminal; sudo prompts stay local.
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ "$(uname -s)" != Darwin ]]; then
  echo 'This script requires macOS.' >&2; exit 1
fi
MAC_MAJOR="$(sw_vers -productVersion | cut -d. -f1)"
if [[ "$MAC_MAJOR" -lt 11 ]]; then
  echo 'This application requires macOS 11 or newer.' >&2; exit 1
fi
export PATH="/opt/local/bin:/opt/local/sbin:$PATH"
if [[ ! -x /opt/local/bin/port ]]; then
  echo 'Install the MacPorts package for your macOS version from https://www.macports.org/install.php first.' >&2
  exit 1
fi
if ! xcode-select -p >/dev/null 2>&1; then
  echo 'First run: xcode-select --install. Complete installation, then rerun this script.' >&2
  exit 1
fi
sudo /opt/local/bin/port install python312 ghostscript potrace cairo
export DYLD_FALLBACK_LIBRARY_PATH="/opt/local/lib:${DYLD_FALLBACK_LIBRARY_PATH:-/usr/local/lib:/usr/lib}"
if [[ -d .venv ]]; then
  if ! .venv/bin/python -c 'import sys; assert sys.version_info[:2] == (3, 12)' 2>/dev/null; then
    echo 'Existing .venv is incompatible. Rename it yourself before retrying; it was not overwritten.' >&2
    exit 1
  fi
else
  /opt/local/bin/python3.12 -m venv .venv
fi
REQ_FILE=requirements.txt
if [[ "$MAC_MAJOR" -eq 11 ]]; then REQ_FILE=requirements-macos11.txt; fi
.venv/bin/python -m pip install --only-binary=:all: -r "$REQ_FILE"
.venv/bin/python -m pngvector.doctor
echo "Setup complete. Run: bash 'Launch PNG Vector.command'"
