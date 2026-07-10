# Phase 0 冻结报告

## 原始状态

开始时目录只有 `docs/now.md` 和项目策划书，不是 Git 仓库，无代码、依赖、环境、测试或 CI。

## 最终方案

采用 `docs/now.md` 为工程规范、策划书为产品意图的方案。冻结 Python 3.11/uv/FastAPI/Pydantic/LangGraph 后端，Node 22/pnpm/React/Vite 前端骨架，SQLite 开发数据库、REST + SSE、YAML 规则与 AuditEvent 事实流。风险/策略映射、审批后受控路径和并行检查语义见 ADR-003 与状态机文档。

## 当前真实能力

公共 Contract 可创建和序列化；状态机可拒绝非法转移；Mock 请求可通过 Scheduler、Mock 分类/权限/执行并写入内存审计；FastAPI `/health` 可访问；前端占位入口可测试和构建；配置可解析。

## 明确未实现

真实 Planner、风险算法、规则求值、权限持久化、真实工具、Sandbox、checkpoint/commit/rollback、递归脱敏、数据库仓库、完整 SSE 和产品 UI 均未实现。Mock 不具备安全防护能力。

## 验证记录

- `uv sync --project backend --group dev`：成功解析并安装 71 个项目/传递包，生成 `backend/uv.lock`。
- Ruff：后端、测试和脚本检查通过。
- Pyright standard：0 errors、0 warnings。
- pytest + coverage：13 passed，Phase 0 骨架覆盖率 78%；存在一条 FastAPI/Starlette TestClient 上游弃用警告。
- Uvicorn `/health`：实际启动并返回 `{"data":{"status":"ok","phase":"phase-0"},"error":null}`。
- pnpm frozen lock、ESLint、TypeScript、Vitest、Prettier、Vite build：通过；Vitest 为 1 passed。
- Playwright：桌面和 390px 视口均加载正确，最终控制台 0 errors、0 warnings。
- 环境差异：本机 Node 24.14.0 执行前端验证时产生 engine warning；冻结和 CI 版本仍为 Node 22.12。

## P0 环境记录

uv 0.11.28 已通过可用的 PyPI 镜像解析依赖，Python 3.11.9 项目虚拟环境位于 `backend/.venv`，真实 `backend/uv.lock` 已生成。pnpm 10 已解析并生成真实 lock。本机 Node 24.14.0 高于冻结的 Node 22，只作为临时验证环境，CI 固定 Node 22。

## 下一阶段四人入口

- A（Runtime/集成）：从 `runtime/scheduler.py` 和 `state_machine.py` 开始；首验收是 LOW 与 BLOCK 两条无真实副作用路径。
- B（Risk/Policy/Permission）：从 `security/` 与 `configs/` 开始；首验收是敏感路径和危险命令产生确定 verdict。
- C（Tools/恢复）：从 `tools/registry.py` 与 `execution/` 开始；首验收是 workspace 内 write_file pending + backup/restore。
- D（Audit/Frontend/实验）：从 `audit/event_bus.py`、SSE 路由和前端 features 开始；首验收是事件持久化、重连后顺序一致。
