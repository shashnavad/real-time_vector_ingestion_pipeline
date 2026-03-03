#!/usr/bin/env bash
set -euo pipefail

# Run the project's tests. If a .venv exists this will use it.
if [ -f ".venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

echo "Running pytest..."
python -m pytest -q
