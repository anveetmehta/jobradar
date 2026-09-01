#!/usr/bin/env bash
# One-command setup: creates the venv if it doesn't exist yet, installs
# dependencies, then launches the app. Safe to run again later — it won't
# recreate the venv or reinstall if nothing changed, so `./install.sh` also
# works as your everyday "just start jobradar" command.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found. Install Python 3.9+ first: https://www.python.org/downloads/" >&2
  exit 1
fi

if [ ! -d .venv ]; then
  echo "Creating virtual environment..."
  python3 -m venv .venv
fi

echo "Installing dependencies..."
.venv/bin/pip install -q -r requirements.txt || {
  echo "Dependency install failed — re-running verbosely so you can see why:" >&2
  .venv/bin/pip install -r requirements.txt
  exit 1
}

echo
echo "Starting jobradar — opening http://localhost:8765"
exec .venv/bin/python jobradar.py serve "$@"
