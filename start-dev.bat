@echo off
echo === Starting RA-Agent Dev Servers ===

echo.
echo [1/2] Starting backend on http://127.0.0.1:8000 ...
start "Backend" cmd /c "cd /d %~dp0 && py -3.11 -m uv run --project backend uvicorn ra_agent.main:app --host 127.0.0.1 --port 8000 --reload"

echo [2/2] Starting frontend on http://127.0.0.1:5173 ...
start "Frontend" cmd /c "cd /d %~dp0frontend && corepack pnpm dev --host 127.0.0.1"

echo.
echo Both servers starting. Open http://127.0.0.1:5173 in your browser.
echo Close the two terminal windows to stop the servers.
pause
