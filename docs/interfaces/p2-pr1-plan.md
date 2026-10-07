# 2 号第一轮 PR 开发清单与接口对照

本清单基于 2026-09-19 获取的 `origin/aegis-core`，提交 `d59df71`。工作分支为 `feature/core-event-ui-p2`，PR 目标为 `aegis-core`。组长补充的三轮 PR 安排优先于一周计划中的每日提交建议。

开发更新：第一轮模型、JSONL 存储、前端 mock 骨架和测试已经完成。以下是开工时的对照与顺序，实际接口见 `p2-events-v1.md`，可转发说明见 `p2-handoff-to-lead.md`，验收结果见 `../evidence/core/p2-pr1-verification.md`。

本文记录仓库现状与建议，不代表三人已经冻结新的字段类型或接口语义。第一轮交付 BehaviorEvent、EventStore、前端骨架及相应测试；第二轮接入真实 Gateway、密码提供方和 AuditVerifier；第三轮修复缺陷、补充回归与发布证据。

## 已有实现和建议落点

项目实际 Python 包名为 `ra_agent`，不另建计划示例中的 `aegis` 包。

| 计划模块 | 仓库现状 | 2 号建议落点及边界 |
| --- | --- | --- |
| BehaviorEvent | `backend/src/ra_agent/contracts/audit.py` 定义旧 Runtime 的 AuditEvent；尚无 Core BehaviorEvent | 建议在 `backend/src/ra_agent/events/models.py` 定义 Core 模型，记录旧模型映射；保留现有 Runtime 消费方 |
| EventStore 接口 | `backend/src/ra_agent/gateway/interfaces.py` 已定义三个异步方法 | 沿用现有 Protocol，避免另建同名但签名不同的接口；公共变更与 1 号同步 |
| EventStore 替身 | `backend/src/ra_agent/gateway/fakes.py` 中有 InMemoryEventStore | 在 `backend/src/ra_agent/events/` 实现 JsonlEventStore，与现有替身共用接口测试 |
| 旧审计持久化 | `backend/src/ra_agent/database/repositories/audit.py` 实现 SqliteAuditRepository | 其模型和方法不直接兼容 Core；暂不将其等同于新 EventStore |
| Core 页面 | 现有 AuditPage 消费旧 AuditEvent；尚无 `features/core` | 在 `frontend/src/features/core/` 建立时间线、契约权限与确认展示骨架，使用冻结 fixture 和 FakeToolGateway |
| Core 事件与导出路由 | 1 号已有 `backend/src/ra_agent/api/core_v1.py` | 事件与导出使用独立路由模块；公共路由注册与 1 号协调，避免并行修改同一文件 |
| 样例和接口资料 | 已有 `docs/interfaces/core_v1.md` 和 `tests/fixtures/core/` | 补充事件样例、接口说明和合同测试；需要改公共契约时先同步 |

## 现有 EventStore 签名

```python
async def append_event(self, event: dict[str, Any]) -> dict[str, Any]: ...
async def list_task_events(self, task_id: str) -> list[dict[str, Any]]: ...
async def get_chain_head(self, task_id: str) -> str | None: ...
```

当前替身按 task_id 分组，缺少 sequence 时从 1 分配序号。它用普通 JSON 排序和 SHA-256 产生 `fake_chain_head`，不能作为 JCS / SM3 链的实现依据。真实规范化、链计算和验证规则由 3 号负责，2 号负责按共同约定保存和提供事件。

## BehaviorEvent 字段差异

一周计划规定的核心字段为：

`event_id sequence task_id parent_event_id type actor source_ref object_digest state decision result_digest occurred_at`

预留可选字段为：

`prepared_effect_id effect_descriptor_digest result_commitment commit_epoch execution_receipt_id compliance_proof_ref`

| 旧 Runtime 字段 | Core 字段 | 对接注意事项 |
| --- | --- | --- |
| event_id / task_id / actor | 同名 | 可以映射，但需一致的校验规则 |
| sequence_number | sequence | 明确序号作用域和分配方 |
| event_type | type | 旧事件枚举不能未经核对直接复用 |
| timestamp | occurred_at | 明确带时区时间的序列化形式 |
| status | state | 需要显式映射，不能认定所有状态含义相同 |
| decision | decision | 旧 PolicyDecision 与 Core 四类 GatewayDecision 不等价 |
| request_id | 计划核心列表未列出 | 当前网关实际发送此字段，应与 1 号确认如何保留关联 |
| summary / details | 计划核心列表未列出 | 不能直接充当 source_ref 或摘要值 |

当前网关发出 `GATEWAY_EVALUATED` 和 `GATEWAY_EXECUTED` 两种字典事件，见 `gateway/gateway.py`。两者均未提供 actor、source_ref、parent_event_id、result_digest；执行事件也未提供 object_digest，且直接包含 result。评估事件的 object_digest 当前取契约摘要。

因此，严格 BehaviorEvent 模型不能在未对齐这些差异前直接替换网关事件。PR1 使用完整冻结样例独立验证；PR2 再由 1 号补齐生产方或共同确定转换层。未知的 actor、来源和摘要不得编造为真实审计事实。

## 第一轮实现顺序和验收

1. 建立 BehaviorEvent 模型草案、JSON Schema 和事件 fixture。对齐必填/可空字段、事件类型、四类决定与状态区别、request_id 和 reason_code 的表达方式。
2. 实现 JsonlEventStore，并通过现有 EventStore Protocol 使用。明确序号分配、重复 event_id、损坏记录、重启恢复和并发写入行为；准确说明支持的进程并发范围。
3. 为内存替身和 JSONL 实现建立共享接口测试。覆盖按任务隔离、顺序稳定、重启读取、输入/返回值隔离、重复与非法输入策略、写入失败。链头测试区分替身与真实密码实现。
4. 建立 FakeToolGateway 与前端 fixture，展示任务时间线、契约权限、四类决定及等待确认状态。页面展示后端决定，不自行计算权限；第一轮确认交互明确使用 mock。
5. 补充前端空状态、错误状态、事件排序及状态展示测试。记录实际验证命令和结果，PR 描述列出 C5/C7 范围以及未完成的真实联调事项。
6. 将以下接口交接材料单独发给组长，并邀请 3 号核对规范化与链输入规则。

## 发给组长的参数交接清单

- BehaviorEvent：字段名、类型、必填性、默认值、枚举、时间格式、schema_version 的承载方式及完整 JSON 示例。
- EventStore：三个方法的实际参数和返回结构，序号作用域与生成方，排序、去重/冲突、空任务、异常及持久化行为。
- Gateway 对接：缺失字段由谁提供，request_id、reason_code、evidence_refs 与契约版本如何关联，result 的摘要和脱敏边界。
- 3 号对接：规范化的精确对象、链序号作用域、前链头、空链头表达、摘要编码、哪些字段纳入签名；不得使用测试链头冒充真实链。
- 交付证据：分支、提交 SHA、文件路径、运行命令、实际测试结果、限制。

以上交接内容现已整理到 `p2-handoff-to-lead.md`，并在 PR #3 中单独评论给组长。

## 后续 PR

第二轮接入真实 Gateway ↔ EventStore、EventStore ↔ AuditVerifier、UI ↔ Gateway，完成 `GET /api/v1/tasks/{task_id}/events` 与 `POST /api/v1/audit/export`，补充确认交互、导出和端到端测试。导出包格式先与 3 号核对。

第三轮完成正常与异常路径回归、两配置验证、干净环境复现、性能原始数据与 C1 至 C7 证据索引。密码实现及独立验证器归 3 号；网关决策、契约与权限语义归 1 号。
