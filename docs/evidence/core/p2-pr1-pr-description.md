# feat(core): add P2 behavior events, JSONL store and mock workbench

目标分支：`aegis-core`；功能分支：`feature/core-event-ui-p2`。

Core 原有网关只记录松散的字典事件，尚无 P2 事件模型、持久化存储和工作台。
本 PR 提供严格 BehaviorEvent、可通过既有 EventStore Protocol 使用的 JsonlEventStore，并新增 `/core` 模拟工作台供独立演示与接口评审。

## 对应范围

C5：行为事件、任务内顺序、请求和来源关联、持久化及重启校验。
C7：契约/权限展示、事件时间线、模拟确认流程、测试与交接材料。

## 实现与接口变化

- 添加 `ra_agent.events.BehaviorEvent`，保留计划核心和六个扩展字段，复用 P1 决定/原因枚举；增加 schema_version、request_id、reason_code、evidence_refs 的细化方案供评审。
- EventStore 沿用现有三个异步方法。JSONL 支持单进程多实例串行化、连续序号、父事件校验、重复拒绝、原子快照替换及重启读取。
- 通过 chain_factory 预留 P3 HashChain 注入；默认不生成伪密码链，未配置时明确报告不可用。
- 从离线 OpenAPI components 生成前端类型和规范化样例，前端开发服务提供 mock HTTP 边界。
- `/core` 展示四类决定、等待确认、批准后重检、拒绝、空历史和服务错误；不执行真实工具。

## 验证

```powershell
.\backend\.venv\Scripts\python.exe scripts/verify_core_p2.py
# 启动前端开发服务后：
node scripts/verify_core_p2_ui.mjs
```

实际结果：47 项后端相关回归通过；39 项前端测试通过；Schema 一致性、Ruff、Pyright、前端类型、lint 和生产构建通过；桌面和手机浏览器验证通过。

## 已知限制

真实 Gateway ↔ EventStore、密码链及 AuditVerifier、生产确认流程和日志证据包导出留到第二轮。
JSONL 适用于单进程小型演示，不能用于多进程并发写入。网关当前缺少的来源字段及摘要语义需要与 1/3 号同步后接线。
本 PR 不宣称已完成密码学完整性验证或 Core v1.0 发布。

## 证据与评审资料

- `docs/interfaces/p2-events-v1.md`
- `docs/interfaces/p2-handoff-to-lead.md`
- `docs/interfaces/schemas/p2-events.openapi.json`
- `tests/fixtures/core/behavior_events.json`
- `docs/evidence/core/p2-pr1-verification.md`

公共模型和接口细化请至少一名非作者评审；字段/摘要输入与 1/3 号确认后再合并。
