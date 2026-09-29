@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo === Aegis Runtime Base v0.4 - Conversation Runtime ===

py -3.11 -c "import sys; print(sys.version)" >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Python 3.11 is required.
  echo Install Python 3.11 and enable the Python launcher, then retry.
  pause
  exit /b 1
)

node --version >nul 2>&1
if errorlevel 1 goto :node_version_error
for /f "tokens=1,2 delims=." %%A in ('node -p "process.versions.node"') do (
  set "NODE_MAJOR=%%A"
  set "NODE_MINOR=%%B"
)
if not "%NODE_MAJOR%"=="24" goto :node_version_error
if %NODE_MINOR% LSS 14 goto :node_version_error

if not exist ".env" (
  copy /Y ".env.example" ".env" >nul
  echo Created .env from .env.example.
)

set "MISSING_LLM_CONFIG="
for %%V in (LLM_BASE_URL LLM_API_KEY PLANNER_MODEL) do (
  findstr /R /C:"^%%V=." ".env" >nul
  if errorlevel 1 set "MISSING_LLM_CONFIG=1"
)
if defined MISSING_LLM_CONFIG (
  echo [ACTION REQUIRED] Configure LLM_BASE_URL, LLM_API_KEY and PLANNER_MODEL in .env.
  echo The deterministic validation suite does not need these values, but live Agent planning does.
  start "" notepad.exe ".env"
  pause
  exit /b 1
)

echo [1/4] Preparing locked Python environment...
py -3.11 -m uv --version >nul 2>&1
if errorlevel 1 py -3.11 -m pip install --user uv
if errorlevel 1 goto :failed
py -3.11 -m uv sync --project backend --group dev --locked
if errorlevel 1 goto :failed

echo [2/4] Preparing locked frontend environment...
pushd frontend
call corepack pnpm install --frozen-lockfile
if errorlevel 1 (
  popd
  goto :failed
)
popd

echo [3/4] Starting Runtime API at http://127.0.0.1:8000 ...
start "Aegis Runtime Base API" /D "%~dp0" cmd /k "set RUNTIME_MODE=live-agent&& set ENABLE_DEMO_FIXTURES=true&& py -3.11 -m uv run --project backend uvicorn ra_agent.main:app --app-dir backend/src --host 127.0.0.1 --port 8000"

echo [4/4] Starting Workbench at http://127.0.0.1:5173 ...
start "Aegis Runtime Base UI" /D "%~dp0frontend" cmd /k "corepack pnpm dev --host 127.0.0.1 --port 5173"

timeout /t 4 /nobreak >nul
start "" http://127.0.0.1:5173
echo Started. Configure persistent permissions in Security settings, then use Agent conversations.
echo Close the two Aegis windows to stop the system.
exit /b 0

:failed
echo [ERROR] Startup failed. Read the error above and retry START_RUNTIME_BASE.bat.
pause
exit /b 1

:node_version_error
echo [ERROR] Node.js ^>=24.14.0 ^<25 is required.
pause
exit /b 1
