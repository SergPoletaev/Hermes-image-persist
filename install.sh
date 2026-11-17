#!/usr/bin/env bash
# Hermes Image Persist - Unix installer wrapper.
#
#   bash install.sh             preview only
#   bash install.sh --apply     install
#
# Finds a Python 3.9+ (hermes' own venv, python3, python) and runs install.py.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY=""

pick_python() {
    local candidates=(
        "${HERMES_HOME:-$HOME/.hermes}/hermes-agent/venv/bin/python3"
        "${HERMES_HOME:-$HOME/.hermes}/hermes-agent/.venv/bin/python3"
        python3 python
    )
    for candidate in "${candidates[@]}"; do
        if command -v "$candidate" >/dev/null 2>&1; then
            if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
                PY="$candidate"
                return 0
            fi
        elif [ -x "$candidate" ]; then
            if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
                PY="$candidate"
                return 0
            fi
        fi
    done
    return 1
}

if ! pick_python; then
    echo "No Python 3.9+ found. Install Python 3.9+ (or point PATH at the Hermes venv) and retry." >&2
    exit 1
fi

exec "$PY" "$HERE/install.py" "$@"
