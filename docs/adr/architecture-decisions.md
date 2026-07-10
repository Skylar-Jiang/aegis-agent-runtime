# Architecture Decisions

## ADR-001：模块化单体与唯一调度入口

- 状态：Accepted
- 背景：四人需要并行开发，但比赛原型不需要分布式复杂度。
- 决定：采用单仓库模块化单体；所有工具副作用只经 Runtime Scheduler。
- 备选：微服务；Agent 直接调用工具。
- 理由：Contract 边界足以并行，部署与调试成本最低，且能强制安全路径。
- 后果：模块必须遵守单向依赖；Scheduler 需要重点测试，暂不水平扩展。

## ADR-002：REST + SSE

- 状态：Accepted
- 背景：命令/查询是短请求，轨迹是服务端单向实时流。
- 决定：任务 API 使用 REST，AuditEvent 使用 SSE。
- 备选：WebSocket；轮询。
- 理由：SSE 支持自动重连且协议更简单，不需要客户端向流通道发消息。
- 后果：审批仍走 REST；未来使用 sequence_number 支持恢复。

## ADR-003：风险与策略映射

- 状态：Accepted
- 背景：策划书曾存在多套未冻结的风险决策同义词。
- 决定：使用五级 RiskLevel 和四级 PolicyDecision；HIGH 审批，CRITICAL/FORBIDDEN 阻断。
- 备选：四级风险；五种策略。
- 理由：与 `docs/now.md` 的公共枚举一致，并使危险命令场景确定。
- 后果：审批不能覆盖 CRITICAL/FORBIDDEN；旧词只保留在历史策划书。
