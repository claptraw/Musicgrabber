#!/bin/bash
# Run the MusicGrabber integration test suite.
# Usage:
#   ./run_tests.sh           - fast tests only (no external network calls)
#   ./run_tests.sh --slow    - all tests including slow external-service tests
#   ./run_tests.sh --all     - alias for --slow

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$SCRIPT_DIR/.venv"

if [ ! -d "$VENV" ]; then
    echo "Creating venv..."
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install -q pytest requests
fi

PYTEST="$VENV/bin/pytest"

if [[ "$1" == "--slow" || "$1" == "--all" ]]; then
    shift  # consume our flag so it doesn't reach pytest, which has no idea what --slow means
    echo "Running ALL tests (including slow external-service tests)..."
    "$PYTEST" "$SCRIPT_DIR" "$@"
else
    echo "Running fast tests only. Use --slow to include external-service tests."
    "$PYTEST" "$SCRIPT_DIR" -m "not slow" "$@"
fi
