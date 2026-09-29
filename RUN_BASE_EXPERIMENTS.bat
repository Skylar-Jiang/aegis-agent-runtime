@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo === Aegis Runtime Base deterministic validation ===
echo No LLM key is used by this suite.

py -3.11 -c "import sys; print(sys.version)" >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Python 3.11 is required.
  pause
  exit /b 1
)

py -3.11 -m uv --version >nul 2>&1
if errorlevel 1 py -3.11 -m pip install --user uv
if errorlevel 1 goto :failed

echo [1/3] Installing locked backend dependencies...
py -3.11 -m uv sync --project backend --group dev --locked
if errorlevel 1 goto :failed

echo [2/3] Running eleven deterministic main-chain experiments...
py -3.11 -m uv run --project backend python scripts/verify_runtime_base.py --output .runtime/base-validation.json
if errorlevel 1 goto :failed

echo [3/3] Checking and building the frontend...
pushd frontend
call corepack pnpm install --frozen-lockfile
if errorlevel 1 (
  popd
  goto :failed
)
call corepack pnpm typecheck
if errorlevel 1 (
  popd
  goto :failed
)
call corepack pnpm exec vitest run
if errorlevel 1 (
  popd
  goto :failed
)
call corepack pnpm build
if errorlevel 1 (
  popd
  goto :failed
)
popd

echo.
echo PASS - Runtime Base main chain and frontend checks completed.
echo Machine-readable report: .runtime\base-validation.json
pause
exit /b 0

:failed
echo.
echo FAIL - See the first failing command above.
if exist ".runtime\base-validation.json" echo Report: .runtime\base-validation.json
pause
exit /b 1
