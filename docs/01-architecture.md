# 架构冻结

> V2 比赛最终阶段以 [V2 最终架构与接口冻结](v2-final/00-freeze.md) 为准；本文保留主干现有单步 Runtime 架构说明。

## 执行链

`User → Agent Planner → ToolCallRequest → Runtime Scheduler → Risk Classifier → Policy Engine → Permission Gate → Controlled Execution → Deep Safety Check → Commit/Rollback → Audit → Agent`

Planner 只生成计划与 `ToolCallRequest`。Scheduler 是所有副作用的唯一入口。Classifier 只产生风险证据，Policy 只映射策略，Permission Gate 按需授权，Executor 只执行已授权请求，Audit 只记录事实。

`RuntimeScheduler` 只负责幂等声明、风险分派和公共入口；FAST、SANDBOX 和审批恢复分别由专用 Flow 实现。`ServiceContainer` 注入所有 Protocol。`ToolRegistry` 同时保存可信 `ToolSpec` 与可选 `ToolHandler`，但 Scheduler 和 Agent 都不能直接调用 Handler；未来真实 Executor 才负责解析 Handler。

REQUEST_APPROVAL 是两阶段可恢复流程：第一次调度创建内存审批并返回 `WAITING_APPROVAL`，外部服务完成决定后由独立 resume 调用恢复。批准只允许进入按 ToolSpec 选择的 FAST 或 SANDBOX 路径，不可逆或禁用 Mock 工具仍然阻断。

## 依赖规则

- `contracts` 不依赖业务模块；所有模块依赖统一 Contract。
- `agent` 不得 import `tools.implementations` 或 `execution.executor`。
- `security` 不得调用工具；`execution` 不得自行改变风险等级。
- `audit` 不得向 Policy Engine 反馈决策，但历史事件可作为未来分类输入的只读事实。
- 前端只消费 REST、OpenAPI Contract 和 SSE AuditEvent，不读取 Python 内部对象。
- LangGraph 只负责 Planner/Agent 状态流转，不代替 Scheduler、安全模块或提交语义。

项目保持模块化单体。SQLite 已用于 Phase 2 审计、审批和幂等持久化；PostgreSQL 仍是可选部署目标。Phase 3 的 Pre/Post Check、受控并行和 Memory/Download 状态边界见 `docs/phase3/01-contract-v0.3.md`。
