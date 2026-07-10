# API 冻结

| Method | Path                                 | 说明               |
| ------ | ------------------------------------ | ------------------ |
| GET    | `/health`                            | 进程健康与工程阶段 |
| POST   | `/api/tasks`                         | 创建任务           |
| GET    | `/api/tasks/{task_id}`               | 任务快照           |
| GET    | `/api/tasks/{task_id}/steps`         | 步骤列表           |
| GET    | `/api/tasks/{task_id}/events`        | 历史审计事件       |
| GET    | `/api/tasks/{task_id}/stream`        | SSE 实时事件       |
| POST   | `/api/approvals/{approval_id}/grant` | 批准               |
| POST   | `/api/approvals/{approval_id}/deny`  | 拒绝               |
| POST   | `/api/tasks/{task_id}/cancel`        | 取消任务           |
| GET    | `/api/tasks/{task_id}/report`        | 安全报告           |

JSON 接口统一返回 `{data, error}`；失败时 `error` 使用 `{code, message, details}`。SSE event 名为 `audit`，data 是 `AuditEvent` JSON。断线恢复将使用 sequence_number，正式实现前不得依赖 Phase 0 的单事件 Mock stream。OpenAPI 由 FastAPI `/openapi.json` 自动生成，不维护重复 Swagger 文件。
