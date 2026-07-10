# Phase 0 工程冻结设计

## 目标与边界

本阶段把策划书收敛为可供四人并行开发的唯一工程基线，并交付可安装、可启动、可测试的最小前后端骨架。真实 Agent 推理、完整风险分类、真实工具执行、系统级 Sandbox、可靠 Shell 回滚、完整 Commit/Rollback、生产数据库和完整页面均不在本阶段实现。

`docs/now.md` 是 Phase 0 的规范来源，`docs/人工智能安全作品赛-策划书.md` 是产品意图来源；两者冲突时采用前者，并在冻结报告记录收敛结果。

## 技术基线

- 后端：CPython 3.11、uv、FastAPI、Pydantic v2、pydantic-settings、LangGraph、asyncio、httpx、SQLAlchemy 2、Alembic、SQLite/aiosqlite、PyYAML、structlog。
- 前端：Node.js 22 LTS（`>=22.12 <23`）、pnpm 10、React 19、TypeScript 5、Vite 7、React Router 7、TanStack Query 5、Zustand 5、React Flow 12、ECharts 6、Tailwind CSS 4、Vitest、Testing Library、Playwright、ESLint、Prettier。
- 通信：任务命令与查询使用 REST；运行轨迹使用单向 SSE；OpenAPI 由 FastAPI 自动生成。
- 数据：开发期使用 SQLite；审计事件最终持久化，Phase 0 只交付内存 sink 和数据库接口骨架。
- 配置：非密钥规则使用 YAML；密钥只从 `.env`/环境变量读取，真实 `.env` 不进入 Git。

## 模块边界

调用方向固定为 `Agent Planner -> Runtime Scheduler -> Risk Classifier -> Policy Engine -> Permission Gate -> Execution -> Deep Check -> Commit/Rollback -> Audit -> Agent`。Planner 只能产生 `ToolCallRequest`；所有真实副作用必须由 Scheduler 调度。Classifier 不执行工具，Executor 不判定风险，Audit 不参与决策。LangGraph 只管理 Planner/Agent 状态，不接管安全运行时模块。

## 冲突收敛

- 风险等级统一为 `LOW/MEDIUM/HIGH/CRITICAL/FORBIDDEN`。
- 策略统一为 `FAST_EXECUTE/SANDBOX_CHECK/REQUEST_APPROVAL/BLOCK`，不再使用旧同义词。
- 映射固定为：LOW→FAST_EXECUTE，MEDIUM→SANDBOX_CHECK，HIGH→REQUEST_APPROVAL，CRITICAL/FORBIDDEN→BLOCK。`CRITICAL` 表示当前上下文中的极高风险动作；`FORBIDDEN` 表示规则上无条件禁止的能力，二者当前均不可审批绕过。
- 高风险获批只解决授权，不降低风险；获批后仍进入 checkpoint、受控执行、深度检查和 commit gate。拒绝或过期进入 `BLOCKED`。
- 中风险的受控执行与深度检查可以并发启动；`EXECUTING_SANDBOX` 表示二者并发期，`SAFETY_CHECKING` 表示执行已结束但检查仍未结束。提交必须等待执行与检查都成功。
- `memory_write` 与可修改可信状态的工具即使获批也先写 pending；`run_shell` 在 Phase 0 只有 Mock/接口，不提供任意命令执行。

## Contract 与状态机

所有 ID 为字符串，所有时间为带时区 UTC `datetime`，公共字段统一使用 `task_id/step_id/request_id/checkpoint_id/event_id`。公共枚举只定义在 `contracts/enums.py`。状态机负责合法转移；调度器另行负责风险结论、checkpoint、安全检查和幂等前置条件。

终态为 `COMMITTED/ROLLED_BACK/BLOCKED/FAILED/CANCELLED`。低风险路径为 `PLANNED→RISK_CLASSIFYING→READY→EXECUTING_FAST→COMMITTED`；中风险路径通过 checkpoint、sandbox/check 后 commit 或 rollback；高风险先进入 `WAITING_APPROVAL`，获批后走受控路径；禁止动作直接 `BLOCKED`。

## API、错误与事件

冻结 `GET /health`，任务创建/查询/步骤/事件/SSE/取消/报告，以及审批 grant/deny 路由。业务接口统一返回 `APIResponse`，错误体统一为 `APIError`。SSE 以 `AuditEvent` JSON 为事实来源；同一任务的 `sequence_number` 必须单调递增，写入 sink 前必须脱敏。

## 验证标准

- Python 3.11 虚拟环境存在且未被 Git 跟踪，后端依赖能解析并生成真实 lock。
- 后端 Ruff、Pyright、pytest 全部通过，应用可启动且 `/health` 返回成功。
- 前端依赖能解析并生成真实 lock，lint、类型检查、测试、构建全部通过。
- README、12 份 P0 文档、ADR、YAML 配置、CI 和开发命令均有实质内容。
- Git 作者为 `Skylar-Jiang <springblossom.mo.20@gmail.com>`，远程为用户指定仓库，最终提交推送至 `main`。

## 自检

本设计无 TBD/TODO；技术、状态、策略和模块边界均只有一套命名。范围限定为一个可独立验收的 Phase 0 工程骨架，不包含后续阶段实现。
