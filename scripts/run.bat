@echo off
echo ===================================================
echo Starting ColdReach Outreach Suite
echo ===================================================

echo [1/3] Starting FastAPI Backend on 127.0.0.1:8000...
start "ColdReach API" cmd /k "uv run uvicorn app.main:app --host 127.0.0.1 --port 8000"

echo [2/3] Starting Worker Daemon...
start "ColdReach Worker" cmd /k "uv run python -m app.worker"

echo [3/3] Starting React Operator Dashboard...
start "ColdReach Frontend" cmd /k "cd frontend && npm run dev"

echo ---------------------------------------------------
echo All services launched in background consoles.
echo - Backend API:  http://127.0.0.1:8000 (Docs: /docs)
echo - Frontend UI:  http://localhost:5173
echo Close the respective console windows to stop services.
