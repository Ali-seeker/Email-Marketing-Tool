@echo off
echo ===================================================
echo Running ColdReach Test Suite
echo ===================================================

echo [1/3] Running Python Security Linter (Bandit)...
uv run bandit -r app -ll -q
if %ERRORLEVEL% neq 0 (
    echo Bandit scan failed!
    exit /b %ERRORLEVEL%
)

echo [2/3] Running Backend Tests (pytest)...
uv run pytest -v -W ignore
if %ERRORLEVEL% neq 0 (
    echo Backend tests failed!
    exit /b %ERRORLEVEL%
)

echo [3/3] Running Frontend Tests (Vitest)...
cd frontend
call npm test -- --run
if %ERRORLEVEL% neq 0 (
    echo Frontend tests failed!
    cd ..
    exit /b %ERRORLEVEL%
)
cd ..

echo ===================================================
echo ALL TESTS PASSED SUCCESSFULLY!
echo ===================================================
