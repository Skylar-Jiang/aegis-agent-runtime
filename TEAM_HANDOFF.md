# Aegis Runtime Demo — teammate handoff

1. Install Python 3.11 and Node.js 24.14.x.
2. Double-click `START_DEMO.bat`.
3. On first launch, the script creates and opens `.env`. Fill `LLM_BASE_URL`, `LLM_API_KEY`, and `PLANNER_MODEL`, save, then double-click `START_DEMO.bat` again.
4. The browser opens the workbench at `http://127.0.0.1:5173`.

The package never includes another person's `.env`, API key, local database, audit history, workspace state, cache, virtual environment, or Node modules. The launcher starts `live-agent` with the demo fixtures enabled and installs dependencies from `backend/uv.lock` and `frontend/pnpm-lock.yaml` when needed.

For the five-minute flow, follow [DEMO_GUIDE.md](DEMO_GUIDE.md). Use `Load writing demo` on the Tasks page for the normal Agent write example; it grants only the one demo file rather than broad write access.
