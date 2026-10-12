#!/usr/bin/env bash
set -euo pipefail

echo "==================================================="
echo "Starting ColdReach Outreach Suite"
echo "==================================================="

cleanup() {
    echo "Shutting down ColdReach processes..."
    kill $(jobs -p) 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "[1/3] Starting FastAPI Backend on 127.0.0.1:8000..."
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 &

echo "[2/3] Starting Worker Daemon..."
uv run python -m app.worker &

echo "[3/3] Starting React Operator Dashboard..."
(cd frontend && npm run dev) &

echo "---------------------------------------------------"
echo "ColdReach is running."
echo "- Backend API: http://127.0.0.1:8000"
echo "- Frontend UI: http://localhost:5173"
echo "Press Ctrl+C to terminate all services."
wait
