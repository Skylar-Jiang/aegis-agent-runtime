# Final integration deployment configuration and demo

> 状态：本项目未执行真实 Vercel 或 Railway 部署。本文件保留已验证的本地构建、健康检查和部署配置边界，不能作为已上线或 live verified 的声明。

The frontend is a Vercel SPA (`frontend/vercel.json` keeps refresh routes on `index.html`). Build it with `corepack pnpm --dir frontend build`. Set `VITE_API_BASE_URL` only when the API is a separate public origin; it must end in `/api`. Leave it empty for a same-origin reverse proxy.

Railway runs the FastAPI service with `railway.toml`; `/health` is the health check. Use `RUNTIME_MODE=offline` for the static dashboard demo or `rules-only` with an external, non-repository SQLite volume. The committed formal experiment files are read-only under `experiments/v2/results/{raw,derived}`; never mount an output runner over them.

Required deployment checks: `/health`, `/approvals`, `/runtime`, `/audit`, `/experiments`, then browser refresh on each SPA route. Do not set `LLM_API_KEY` or any secret in tracked files; copy `.env.example` locally and configure provider secrets only in Railway/Vercel dashboards.

## 3–5 minute demo

1. Open **Tasks**, submit/select a controlled task, then use **Open selected Runtime**.
2. In **Runtime**, show graph status, dependencies, redacted Effects, checkpoint identifiers and approval link.
3. In **Approvals**, enter the reviewer identity and choose Grant or Deny directly on the pending card; reopen Runtime to show convergence.
4. Show **Audit timeline** for the same task and its Live/Polling state.
5. Open **Experiment dashboard** and identify the Safety, Graph and Rollback formal raw aggregations.

The scripted runtime scenarios are `tests/integration/test_v2_graph_runtime.py` (grant, deny, cancel/selective rollback) and `tests/e2e/test_live_runtime.py`; they use isolated test workspaces.
