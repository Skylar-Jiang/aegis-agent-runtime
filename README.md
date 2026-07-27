# Aegis Runtime：面向工具增强型智能体的风险自适应安全运行时

面向工具增强型智能体的风险自适应运行时安全架构。系统把工具调用改造为“风险分级—权限调度—受控执行—检查—提交/回滚—审计”的统一运行时链路。

> Phase 3.5 已冻结：Live Runtime 具备 Pre/Post、受控提交/回滚、真实文件/Memory/下载/Shell 注册、TaskContract、数据外发干运行和最小任务图。验证记录见 [Phase 3.5 集成报告](docs/phase3.5/INTEGRATION-REPORT.md)。

> 比赛最终阶段的 V2 架构、共享 Contract、四人职责、实验字段和报告骨架见 [docs/v2-final](docs/v2-final/00-freeze.md)。该规范优先于历史 Phase 3 分工文档。

## 冻结架构

```text
Agent Planner → ToolCallRequest + TaskContract → Runtime Scheduler
→ IntentBoundaryGuard → Risk Classifier → Policy Engine → Permission Gate
→ PreCheck + Controlled Execution → PostCheck → Commit/Rollback → Audit
```

`live-agent` 中缺失或越界的 TaskContract 会在执行器前 Fail Closed。`send_email_dry_run` 只产生 `PENDING_EGRESS`，不会创建网络连接或发送邮件；DataEgressGuard 会按 artifact lineage 再次校验接收方。

后端使用 Python 3.11、uv、FastAPI、Pydantic v2、LangGraph 和 SQLite；前端骨架使用 Node.js 24.14.0、pnpm 10.12.4、React、TypeScript 和 Vite；任务 API 使用 REST，实时审计使用 SSE。

## 目录

- `backend/src/ra_agent/`：Contract、Agent、Runtime、安全、执行、工具、Memory、审计、数据库和 API 边界。
- `frontend/`：仅用于验证工具链的最小 React 骨架。
- `configs/`：风险、权限、工具、敏感路径和运行时 YAML。
- `tests/`：Contract、单元、集成及后续安全/回滚/e2e 测试入口。
- `docs/`：当前规范、ADR、共享基线报告及明确标注的历史参考。

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
corepack pnpm --version
corepack pnpm install --frozen-lockfile
```

也可以在仓库根目录执行 `powershell -File scripts/use-node.ps1` 切换到 `.nvmrc` 指定的已安装版本。macOS/Linux 可使用 nvm、fnm、asdf 等版本管理器读取 `.nvmrc`。Python `.venv` 与 Node.js 版本管理互相独立，激活 Python 虚拟环境不会切换 Node.js。

前端只使用 Corepack 管理的 pnpm。版本解析必须发生在 `frontend` 目录；仓库根目录显示的 Corepack 全局默认版本不代表项目基线：

```powershell
Push-Location frontend
corepack pnpm --version  # 必须为 10.12.4
corepack pnpm install --frozen-lockfile
Pop-Location
```

不要运行 `npm install`，不要擅自升级 Node.js 或 pnpm，不要删除或自行重建 `pnpm-lock.yaml`；每次开发前先确认 `node --version`，并在 `frontend` 目录确认 `corepack pnpm --version`。

复制 `.env.example` 为 `.env` 后只在本地填写密钥，禁止提交真实密钥。

## 启动与检查

```powershell
# 默认是 offline，不创建数据库或运行时目录。
py -3.11 -m uv run --project backend uvicorn ra_agent.main:app --reload

# 启用规则和持久化状态；启动期自动执行 Alembic upgrade head。
$env:RUNTIME_MODE = "rules-only"
py -3.11 -m uv run --project backend uvicorn ra_agent.main:app --reload

# 仅在受信任的本地 .runtime/workspace 中启用受控文件工具。
$env:RUNTIME_MODE = "live-agent"
py -3.11 -m uv run --project backend uvicorn ra_agent.main:app --reload

corepack pnpm --dir frontend dev
python scripts/check.py
```

后端默认 `http://127.0.0.1:8000`，健康检查为 `/health`（会返回当前 mode）；前端默认 `http://127.0.0.1:5173`。非 `offline` 模式只使用 `.runtime/` 下的 workspace、pending、checkpoint、quarantine 和 SQLite 数据库；不要将这些运行时文件或 `.env` 提交到 Git。

## 开发规范

Contract 与枚举只有 `backend/src/ra_agent/contracts` 一套来源。Planner 不得直接调用工具，所有真实副作用必须经 Scheduler。同一 `request_id` 是幂等键；相同语义重试复用首个结果，不同语义冲突被拒绝。提交采用 Conventional Commits；CI 不访问真实 LLM。详细规范见 [开发指南](docs/11-development-guide.md)；历史背景见 [Phase 0 摘要](docs/history/phase-0-summary.md)。

LOW 工具在 Pre/Post 后走 FAST_EXECUTE；受控写入、删除、下载和 Memory 写入依照风险进入隔离、检查、提交或回滚。REQUEST_APPROVAL 使用 `schedule` 创建等待结果、`resume_after_approval` 独立恢复，不长期占用 HTTP 请求。最小 TaskGraph 支持依赖、条件、受限并行和“失败/等待只阻断后代”。

## 本地安全 Demo

运行 `powershell -File scripts/run-demo-e2e.ps1` 会在 pytest 临时目录的 `.runtime/workspace` 中执行六个可重复场景：LOW `read_file`、MEDIUM `write_file` 的 pending/check/commit、HIGH 删除的 deny/approve、危险 `rm -rf` 的执行前阻断、恶意文档引出的后续越界 ToolCall 阻断，以及受控长进程取消并回收。Demo 不会操作仓库工作区或用户目录。

结果语义不可混同：

- 执行前 `BLOCKED`：风险、权限或 TaskContract 在工具启动前拒绝请求；
- `ROLLED_BACK`：工具已产生 pending 状态，但 Post/Deep/Commit 检查失败，运行时清理并恢复；
- 多步任务中的 `BLOCKED`：前序动作完成后，新的 ToolCall 仍会重新经过 Runtime 边界与风险检查；
- `EXECUTION_INTERRUPTED` / `INTERRUPTED`：正在运行的受控执行被取消或独立安全监控终止，受限进程会先 terminate、超时后 kill，已有 pending 状态沿既有 cleanup/rollback 路径处理。

当前中断能力只覆盖 Runtime 持有的受控执行（至少 `run_shell`）；它不是对任意操作系统进程进行实时语义监控。只有调用方显式提供、且确实不依赖执行结果的独立监控器，MEDIUM checkpointed pending 执行才会与该检查并行；否则维持顺序检查。

## 下一步分工

Phase 3 的真实实现仍由成员完成；当前唯一执行入口如下：

- [Phase 3 基线](docs/phase3/README.md)
- [成员 A：Runtime](docs/phase3/02-member-a-runtime.md)
- [成员 B：Security](docs/phase3/03-member-b-security.md)
- [成员 C：Execution](docs/phase3/04-member-c-execution.md)
- [成员 D：Experiments/UI](docs/phase3/05-member-d-experiments-ui.md)
- [集成清单](docs/phase3/06-integration-checklist.md)

旧 Phase 1/2 报告仅作历史资料，不是当前开发入口。
