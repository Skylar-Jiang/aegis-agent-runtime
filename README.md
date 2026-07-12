# RA-Agent Runtime

面向工具增强型智能体的风险自适应运行时安全架构。系统把工具调用改造为“风险分级—权限调度—受控执行—检查—提交/回滚—审计”的统一运行时链路。

> 当前阶段为 **Phase 1：Runtime 基础闭环**。Runtime 已实现 Mock 环境中的 FAST_EXECUTE、BLOCK、请求幂等、权限一致性校验和统一审计；SANDBOX_CHECK、REQUEST_APPROVAL、真实安全规则、真实工具、数据库和前端业务仍未实现，现有 Mock 不具备生产安全能力。

## 冻结架构

```text
Agent Planner → ToolCallRequest → Runtime Scheduler
→ Risk Classifier → Policy Engine → Permission Gate
→ Controlled Execution → Deep Check → Commit/Rollback → Audit
```

后端使用 Python 3.11、uv、FastAPI、Pydantic v2、LangGraph 和 SQLite；前端骨架使用 Node.js 24.14.0、pnpm 10.12.4、React、TypeScript 和 Vite；任务 API 使用 REST，实时审计使用 SSE。

## 目录

- `backend/src/ra_agent/`：Contract、Agent、Runtime、安全、执行、工具、Memory、审计、数据库和 API 边界。
- `frontend/`：仅用于验证工具链的最小 React 骨架。
- `configs/`：风险、权限、工具、敏感路径和运行时 YAML。
- `tests/`：Contract、单元、集成及后续安全/回滚/e2e 测试入口。
- `docs/`：P0 冻结文档、ADR、策划书和冻结报告。

## 环境安装

环境要求：Git、Python 3.11、Node.js 24.14.0（允许范围 `>=24.14.0 <25`）和 pnpm 10.12.4。后端不使用手写 `requirements.txt`，依赖声明与精确版本分别位于 `backend/pyproject.toml` 和 `backend/uv.lock`。

克隆仓库后，在项目根目录执行：

```powershell
git clone https://github.com/Skylar-Jiang/aegis-agent-runtime.git
cd aegis-agent-runtime

# 安装 uv，并按锁文件创建 backend/.venv
py -3.11 -m pip install --user uv
py -3.11 -m uv sync --project backend --group dev

# 激活虚拟环境
.\backend\.venv\Scripts\Activate.ps1

# 确认解释器与后端依赖可用
python --version
python -c "import fastapi, pydantic, langgraph; print('backend environment ready')"
```

macOS/Linux 的激活命令为 `source backend/.venv/bin/activate`。如果 PowerShell 不允许执行激活脚本，也可以直接使用 `backend\.venv\Scripts\python.exe`，不需要修改系统执行策略。

Windows 推荐使用 NVM for Windows：

```powershell
nvm install 24.14.0
nvm use 24.14.0
node --version
corepack enable
cd frontend
corepack pnpm install --frozen-lockfile
```

也可以在仓库根目录执行 `powershell -File scripts/use-node.ps1` 切换到 `.nvmrc` 指定的已安装版本。macOS/Linux 可使用 nvm、fnm、asdf 等版本管理器读取 `.nvmrc`。Python `.venv` 与 Node.js 版本管理互相独立，激活 Python 虚拟环境不会切换 Node.js。

前端只使用 Corepack 管理的 pnpm：

```powershell
corepack pnpm --dir frontend install --frozen-lockfile
```

不要运行 `npm install`，不要擅自升级 Node.js 或 pnpm，不要删除或自行重建 `pnpm-lock.yaml`；每次开发前先确认 `node --version`。

复制 `.env.example` 为 `.env` 后只在本地填写密钥，禁止提交真实密钥。

## 启动与检查

```powershell
py -3.11 -m uv run --project backend uvicorn ra_agent.main:app --reload
corepack pnpm --dir frontend dev
python scripts/check.py
```

后端默认 `http://127.0.0.1:8000`，健康检查为 `/health`；前端默认 `http://127.0.0.1:5173`。

## 开发规范

Contract 与枚举只有 `backend/src/ra_agent/contracts` 一套来源。Planner 不得直接调用工具，所有真实副作用必须经 Scheduler。同一 `request_id` 是幂等键；相同语义重试复用首个结果，不同语义冲突被拒绝。提交采用 Conventional Commits；CI 不访问真实 LLM。详细规范见 [开发指南](docs/11-development-guide.md) 和 [历史 Phase 0 冻结报告](docs/PHASE-0-FREEZE-REPORT.md)。

FAST_EXECUTE 的 LOW 结果成功后直接成为可信结果并进入 COMMITTED，不经过 Pending 或真实 CommitGate。未来 SANDBOX_CHECK 的 MEDIUM 结果必须先进入 Pending，经安全检查和 CommitGate 后才进入 COMMITTED；该路径当前尚未实现。

## 下一步分工

Runtime/集成、安全策略、工具与恢复、审计/前端/实验四条工作流的入口和首个验收目标记录在冻结报告中。
