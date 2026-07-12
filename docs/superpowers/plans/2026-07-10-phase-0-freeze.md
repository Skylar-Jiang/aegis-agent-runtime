# Phase 0 工程冻结 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立 RA-Agent 可启动、可测试、契约和边界已冻结的 Phase 0 工程骨架。

**Architecture:** 单仓库包含 FastAPI/Pydantic 后端、Vite/React 前端、共享 YAML 策略和文档。Agent 只产出工具请求，所有副作用经 Runtime Scheduler，审计事件是 UI 和实验的统一事实来源。

**Tech Stack:** Python 3.11、uv、FastAPI、Pydantic v2、LangGraph、SQLite；当前有效基线 Node.js 24.14.0、pnpm 10.12.4、React、TypeScript、Vite、Vitest。

---

### Task 1: 仓库和环境基线

**Files:**

- Create: `.gitignore`, `.env.example`, `.python-version`, `.nvmrc`
- Create: `backend/pyproject.toml`, `frontend/package.json`

- [ ] 初始化 `main` 分支，配置指定 Git 作者和 `origin`。
- [ ] 用 `py -3.11 -m venv .venv` 创建虚拟环境，验证 `python --version` 为 3.11。
- [ ] 安装 uv 与 pnpm，分别执行 `uv lock` 和 `pnpm install --lockfile-only`；不得伪造 lock。
- [ ] 验证 `.env`、`.venv`、`.runtime`、数据库、缓存、依赖和构建产物均被忽略。

### Task 2: Contract 和状态机（TDD）

**Files:**

- Create: `backend/src/ra_agent/contracts/*.py`
- Create: `backend/src/ra_agent/runtime/state_machine.py`
- Test: `tests/contract/test_contracts.py`, `tests/unit/test_state_machine.py`

- [ ] 先写枚举、典型 Contract JSON 序列化、合法/非法状态转移测试并运行，确认因模块缺失而失败。
- [ ] 实现唯一公共枚举和 Pydantic 模型；ID 统一为字符串，时间统一为 UTC aware datetime。
- [ ] 实现最小 `transition(current, target)` 合法性校验，非法路径抛出 `InvalidStateTransition`。
- [ ] 运行 `pytest tests/contract tests/unit/test_state_machine.py -q`，预期全部通过。

### Task 3: 接口边界、Mock、API 和配置（TDD）

**Files:**

- Create: `backend/src/ra_agent/{agent,security,execution,audit,tools,core,api,database}/`
- Create: `backend/src/ra_agent/main.py`
- Test: `tests/unit/test_config.py`, `tests/integration/test_health.py`, `tests/integration/test_mock_runtime.py`

- [ ] 先写配置加载、健康检查、Mock ToolCallRequest→AuditEvent 流程测试并确认失败。
- [ ] 使用 `Protocol` 定义 LLM、风险、策略、权限、深检、执行、checkpoint、commit、rollback 和 audit sink 边界。
- [ ] Mock 名称和文档明确包含 `Mock`/`InMemory`，不得执行真实工具或外部 API。
- [ ] 创建 FastAPI 路由骨架；`/health` 返回真实状态，其余接口返回显式 Mock 数据或 501。
- [ ] 运行目标测试，预期全部通过。

### Task 4: 前端最小骨架

**Files:**

- Create: `frontend/src/*`, `frontend/index.html`, TypeScript/Vite/ESLint/Prettier/Vitest/Tailwind 配置

- [ ] 创建只展示 Phase 0 状态和后端健康入口的 React 应用，不实现完整业务页面。
- [ ] 添加一个渲染测试，运行时不依赖后端、浏览器登录态或密钥。
- [ ] 运行 `pnpm lint`、`pnpm typecheck`、`pnpm test -- --run`、`pnpm build`，预期全部通过。

### Task 5: 策略、文档、开发命令和 CI

**Files:**

- Create: `configs/*.yaml`, `docs/00-scope.md` 至 `docs/11-development-guide.md`, `docs/adr/architecture-decisions.md`
- Create: `README.md`, `Makefile`, `scripts/*.py`, `.pre-commit-config.yaml`, `.github/workflows/ci.yml`

- [ ] 将设计中的唯一风险映射、工具清单、状态流转、API 和事件 schema 写入文档与 YAML。
- [ ] 提供跨平台 Python 开发脚本及 PowerShell 快速开始命令。
- [ ] CI 只运行离线 Mock 测试，不读取真实 LLM 密钥。
- [ ] 扫描并消除冻结文档中的占位词、不受限版本和旧决策同义词。

### Task 6: 全量验证、提交和推送

**Files:**

- Modify only files required to fix verification failures.

- [ ] 在虚拟环境执行 `ruff check .`、`pyright`、`pytest --cov`。
- [ ] 启动 Uvicorn，实际请求 `/health`，随后停止进程。
- [ ] 执行全部前端检查与构建。
- [ ] 检查 `git status`、作者、remote 和 `.gitignore`，确认无密钥、venv、数据库或构建产物被跟踪。
- [ ] 提交为 Phase 0 基线并 `git push -u origin main`；用 `git ls-remote origin refs/heads/main` 确认远程分支。
