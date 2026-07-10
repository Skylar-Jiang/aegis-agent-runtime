# 技术栈冻结

## 后端

CPython `>=3.11,<3.12`、uv、FastAPI、Pydantic v2、pydantic-settings、LangGraph、asyncio、httpx、SQLAlchemy 2、Alembic、SQLite/aiosqlite、PyYAML、structlog、Uvicorn。质量工具为 pytest、pytest-asyncio、pytest-cov、Ruff、Pyright standard 和 pre-commit。

## 前端

Node.js `>=22.12,<23`、pnpm 10、React 19、TypeScript 5、Vite 7、React Router、TanStack Query、Zustand、React Flow、ECharts、Tailwind CSS 4、shadcn/ui 代码分发约定、Vitest、Testing Library、Playwright、ESLint 和 Prettier。Phase 0 只建立可编译占位入口，不开发业务页面。

## 版本策略

`pyproject.toml` 是 Python 唯一依赖声明，核心依赖使用已知兼容的上下界，`uv.lock` 由 `uv lock` 生成；不维护手写 requirements。前端声明兼容范围并提交 `pnpm-lock.yaml`。本机 Node 24 不符合冻结版本，只用于验证，CI 使用 Node 22。

## 暂不采用

Celery、Redis、Kafka、Kubernetes、复杂微服务、向量数据库、复杂 RAG、多 Agent、自训练模型和真实 Docker Sandbox 均不进入第一版。
