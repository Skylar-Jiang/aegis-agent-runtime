# Phase 1 技术栈冻结

## 后端

CPython `>=3.11,<3.12`、uv、FastAPI、Pydantic v2、pydantic-settings、LangGraph、asyncio、httpx、SQLAlchemy 2、Alembic、SQLite/aiosqlite、PyYAML、structlog、Uvicorn。质量工具为 pytest、pytest-asyncio、pytest-cov、Ruff、Pyright standard 和 pre-commit。

## 前端

Node.js 固定开发版本为 `24.14.0`，允许范围为 `>=24.14.0 <25`；包管理器固定为 pnpm `10.12.4`，由 `frontend/package.json` 的 `packageManager` 和 Corepack 管理。前端其余技术为 React 19、TypeScript 5、Vite 7、React Router、TanStack Query、Zustand、React Flow、ECharts、Tailwind CSS 4、Vitest、Testing Library、Playwright、ESLint 和 Prettier。

pnpm 的项目版本必须在 `frontend` 目录执行 `corepack pnpm --version` 验证。仓库根目录没有 `packageManager` 字段，那里显示的 Corepack 全局默认版本不代表本项目基线；安装也必须先进入 `frontend` 再执行 `corepack pnpm install --frozen-lockfile`。

## 版本策略

`.nvmrc` 与 CI 固定 Node.js `24.14.0`，`.npmrc` 启用 `engine-strict=true`。`pyproject.toml` 是 Python 唯一依赖声明，`backend/uv.lock` 与 `frontend/pnpm-lock.yaml` 均提交并保持冻结；不得使用 `npm install`、擅自升级 pnpm 或为切换 Node 版本重建 lockfile。

Python 虚拟环境和 Node.js 版本管理相互独立。Windows 推荐 NVM for Windows；macOS/Linux 可使用读取 `.nvmrc` 的 nvm、fnm 或 asdf。

## 当前边界

Phase 1 已完成 Runtime 四策略调度骨架、幂等、权限一致性、关联校验、内存审计及 Agent 最小接线。SANDBOX_CHECK 的 Checkpoint/执行/DeepCheck/Commit/Rollback 与 REQUEST_APPROVAL 两阶段恢复均为无副作用 Mock；真实 LLM、真实安全能力、持久化审批、数据库和前端业务不在当前实现范围。
