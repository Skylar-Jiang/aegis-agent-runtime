# 架构冻结

## 执行链

`User → Agent Planner → ToolCallRequest → Runtime Scheduler → Risk Classifier → Policy Engine → Permission Gate → Controlled Execution → Deep Safety Check → Commit/Rollback → Audit → Agent`

Planner 只生成计划与 `ToolCallRequest`。Scheduler 是所有副作用的唯一入口。Classifier 只产生风险证据，Policy 只映射策略，Permission Gate 按需授权，Executor 只执行已授权请求，Audit 只记录事实。

## 依赖规则

- `contracts` 不依赖业务模块；所有模块依赖统一 Contract。
- `agent` 不得 import `tools.implementations` 或 `execution.executor`。
- `security` 不得调用工具；`execution` 不得自行改变风险等级。
- `audit` 不得向 Policy Engine 反馈决策，但历史事件可作为未来分类输入的只读事实。
- 前端只消费 REST、OpenAPI Contract 和 SSE AuditEvent，不读取 Python 内部对象。
- LangGraph 只负责 Planner/Agent 状态流转，不代替 Scheduler、安全模块或提交语义。

Phase 0 是模块化单体，不拆微服务。SQLite 是开发数据库，PostgreSQL 仅是可选部署目标。
