# RA-Agent Runtime

面向工具增强型智能体的风险自适应运行时安全架构。系统把工具调用改造为“风险分级—权限调度—受控执行—检查—提交/回滚—审计”的统一运行时链路。

> 当前仓库完成的是 **Phase 0 工程骨架**。真实风险分类、完整 Sandbox、Commit/Rollback、真实工具和 Agent 流程仍需后续开发；现有 Mock 不具备生产安全能力。

## 冻结架构

```text
Agent Planner → ToolCallRequest → Runtime Scheduler
→ Risk Classifier → Policy Engine → Permission Gate
→ Controlled Execution → Deep Check → Commit/Rollback → Audit
```

后端使用 Python 3.11、uv、FastAPI、Pydantic v2、LangGraph 和 SQLite；前端骨架使用 Node 22、pnpm、React、TypeScript 和 Vite；任务 API 使用 REST，实时审计使用 SSE。

## 目录

- `backend/src/ra_agent/`：Contract、Agent、Runtime、安全、执行、工具、Memory、审计、数据库和 API 边界。
- `frontend/`：仅用于验证工具链的最小 React 骨架。
- `configs/`：风险、权限、工具、敏感路径和运行时 YAML。
- `tests/`：Contract、单元、集成及后续安全/回滚/e2e 测试入口。
- `docs/`：P0 冻结文档、ADR、策划书和冻结报告。

## 快速开始（Windows PowerShell）

```powershell
py -3.11 -m pip install --user uv
py -3.11 -m uv sync --project backend --group dev
.\backend\.venv\Scripts\Activate.ps1
corepack pnpm --dir frontend install --frozen-lockfile
```

复制 `.env.example` 为 `.env` 后只在本地填写密钥，禁止提交真实密钥。

```powershell
py -3.11 -m uv run --project backend uvicorn ra_agent.main:app --reload
corepack pnpm --dir frontend dev
python scripts/check.py
```

后端默认 `http://127.0.0.1:8000`，健康检查为 `/health`；前端默认 `http://127.0.0.1:5173`。

## 开发规范

Contract 与枚举只有 `backend/src/ra_agent/contracts` 一套来源。Planner 不得直接调用工具，所有真实副作用必须经 Scheduler。提交采用 Conventional Commits；CI 不访问真实 LLM。详细规范见 [开发指南](docs/11-development-guide.md) 和 [Phase 0 冻结报告](docs/PHASE-0-FREEZE-REPORT.md)。

## 下一步分工

Runtime/集成、安全策略、工具与恢复、审计/前端/实验四条工作流的入口和首个验收目标记录在冻结报告中。
