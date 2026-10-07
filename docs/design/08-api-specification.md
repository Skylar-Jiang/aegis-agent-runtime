# API 冻结

> V2 只读 Graph、审批列表和实验结果端点的冻结定义见 [docs/v2-final/00-freeze.md](../v2-final/00-freeze.md)。

| Method | Path                                 | 说明               |
| ------ | ------------------------------------ | ------------------ |
| GET    | `/health`                            | 进程健康与工程阶段 |
| POST   | `/api/tasks`                         | 创建任务           |
| GET    | `/api/tasks`                         | 持久化任务列表     |
| GET    | `/api/tasks/{task_id}`               | 任务快照           |
| GET    | `/api/tasks/{task_id}/steps`         | 步骤列表           |
| GET    | `/api/tasks/{task_id}/events`        | 历史审计事件       |
| GET    | `/api/tasks/{task_id}/stream`        | SSE 实时事件       |
| POST   | `/api/approvals/{approval_id}/grant` | 批准               |
| POST   | `/api/approvals/{approval_id}/deny`  | 拒绝               |
| POST   | `/api/tasks/{task_id}/cancel`        | 取消任务           |
| GET    | `/api/tasks/{task_id}/report`        | 安全报告           |

JSON 接口统一返回 `{data, error}`；失败时 `error` 使用 `{code, message, details}`。SSE event 名为 `audit`，data 是 `AuditEvent` JSON。断线恢复将使用 sequence_number，正式实现前不得依赖 Phase 1 的单事件 Mock stream。OpenAPI 由 FastAPI `/openapi.json` 自动生成，不维护重复 Swagger 文件。

`POST /api/tasks` 记录 TASK_CREATED、持久化初始任务并立即返回 `RUNNING`；后台
AgentRunner 只能经 RuntimeScheduler 执行工具。运行中的计划和结果会更新持久化快照，
`/steps` 返回真实步骤。未配置 live Planner 时任务安全地返回 FAILED，不会执行工具。
`grant`/`deny` 已接入 ApprovalService，并会恢复对应的普通 Agent 或 TaskGraph；调用方
必须提供非空 `decided_by`，正式部署仍应由认证层提供可信身份。

SSE 使用 `id: sequence_number`。服务端先订阅再回放 `Last-Event-ID` 之后的历史事件，
并丢弃队列中的重复序号；浏览器重连可使用标准 EventSource 的 Last-Event-ID 机制恢复。
