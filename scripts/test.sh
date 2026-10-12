#!/usr/bin/env bash
set -euo pipefail

echo "==================================================="
echo "Running ColdReach Test Suite"
echo "==================================================="

echo "[1/3] Running Python Security Linter (Bandit)..."
uv run bandit -r app -ll -q

echo "[2/3] Running Backend Tests (pytest)..."
uv run pytest -v -W ignore

echo "[3/3] Running Frontend Tests (Vitest)..."
(cd frontend && npm test -- --run)

echo "==================================================="
echo "ALL TESTS PASSED SUCCESSFULLY!"
echo "==================================================="
