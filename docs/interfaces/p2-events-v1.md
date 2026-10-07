# P2 PR1 事件存储和界面接口

范围为 C5 和 C7 的第一轮基础交付，基于 `aegis-core` 的 `d59df71`。
本文件是 2 号提交的接口细化方案，仍需非作者评审；不是三人已确认冻结的声明。
现有 Runtime AuditEvent、P1 Gateway 与 P3 密码代码均保留其原有职责。

## 实现和生成文件

- `backend/src/ra_agent/events/models.py`：唯一 Core BehaviorEvent 模型。
- `backend/src/ra_agent/events/store.py`：JsonlEventStore，结构兼容既有 EventStore Protocol。
- `docs/interfaces/schemas/p2-events.openapi.json`：离线 OpenAPI 3.1 接口提案。
- `frontend/src/features/core/generated.ts`：从上述模型导出的 OpenAPI components 生成的类型。
- `tests/fixtures/core/behavior_events.json`：确认、重检、执行的固定事件样例。
- `frontend/src/features/core/mock/events.json`：模型校验和规范化后生成的前端样例。

生成命令：`python scripts/export_core_event_contracts.py`；加 `--check` 验证生成文件无漂移。
设置 `PYTHONPATH=backend/src` 并使用项目 Python 3.11 环境。模型源码是唯一字段定义来源。
类型导出器只处理本项目使用的 schema 结构，不支持的类型立即报错，不声称是通用 OpenAPI 生成器。

## BehaviorEvent 参数

| 字段 | 类型 | 规则 |
| --- | --- | --- |
| schema_version | `"1.0"` | 默认填充；本次提案版本 |
| event_id / task_id | string | 必填，非空且非全空白，最多 128 字符 |
| sequence | integer | 持久化后必填，从 1 开始；append 可省略，由存储分配；拒绝 bool 和字符串数字 |
| parent_event_id | string 或 null | 必须出现；父事件必须是同任务先前写入的事件 |
| type / state / actor | string | 必填，非空且非全空白，最多 128 字符；type/state 为可扩展字符串 |
| source_ref | string | 必填，来源引用，最多 512 字符；不推测或伪造来源 |
| object_digest / result_digest | 64 位小写 hex 或 null | 必须出现；null 表示未提供对象关联证据 |
| decision | Core 四类决定或 null | 必须出现；复用 P1 GatewayDecisionType |
| occurred_at | 有时区时间 | 必填；存储输出规范化 UTC ISO 8601；数字时间戳不接受 |
| request_id | string 或 null | 可选，默认 null，保留 P1 的请求关联 |
| reason_code | P1 GatewayReasonCode 或 null | 可选，默认 null |
| evidence_refs | string[] | 默认空数组，最多 100 项，每项最多 512 字符 |
| prepared_effect_id / execution_receipt_id | string 或 null | 预留可选字段，默认 null |
| effect_descriptor_digest | 64 位小写 hex 或 null | 预留可选字段，默认 null |
| result_commitment / compliance_proof_ref | string 或 null | 预留不解释语义的引用，默认 null，最多 512 字符 |
| commit_epoch | integer 或 null | 预留可选字段，非负，默认 null |

整数限制在 JCS 可精确表示范围内。所有未定义字段拒绝；schema 变更需协调并重新生成样例。
可空的计划核心字段仍要求显式传入，防止遗漏被悄悄当成“无证据”。
完整存储模型包含默认和预留字段；链计算必须对返回的完整模型进行，不得在摘要之后增删字段。
本模块只校验摘要编码；不能据此认定某个值确实由 SM3 或合法签名产生。

## EventStore 调用和异常

```python
from pathlib import Path
from ra_agent.events import JsonlEventStore
from ra_agent.gateway.interfaces import EventStore

store: EventStore = JsonlEventStore(Path(".runtime/p2/events.jsonl"))
stored = await store.append_event(event_dict)
events = await store.list_task_events(task_id)
head = await store.get_chain_head(task_id)
```

三个异步方法沿用 P1 Protocol 的 dict 输入输出。append 不修改调用者对象；返回规范化后的完整 BehaviorEvent。
list 返回按 sequence 升序的独立对象；未知任务为空数组。event_id 在单任务内唯一，重复提交（即使内容相同）报冲突。
跨任务可以复用 event_id，但不能跨任务引用 parent_event_id。显式 sequence 必须恰好等于下一序号，空值不等同于省略。

| 异常 code | 含义 |
| --- | --- |
| EVENT_INVALID | 事件模型不合法 |
| EVENT_ID_CONFLICT | 同任务 ID 重复 |
| SEQUENCE_INVALID | 序号不连续 |
| PARENT_EVENT_MISSING | 父事件尚不存在于该任务 |
| LOG_INVALID | 已有文件损坏、结构不符或链无法重建 |
| STORAGE_UNAVAILABLE | 读取或原子写入失败 |
| LOG_TOO_LARGE | 写入后超过第一轮 16 MiB 上限 |
| CHECK_UNAVAILABLE | 非空任务未配置链提供方 |

调用方读取 `EventStoreError.code`；Pydantic 模型直接使用时抛 ValidationError。
注入的链提供方在 append 时产生的自身异常向上透传，由集成层按其错误约定映射。

## 持久化与链边界

每行是 `{"storage_version":1,"event":完整事件,"chain":ChainEntry或null}`。
序号分配、校验、链重建和文件替换使用同一进程的同路径锁，文件名由应用配置，不由 task_id 拼接。
写入在同目录临时文件完成，flush/fsync 后原子替换；失败时旧快照保留。读取和写入均验证整份日志。
不自动截断损坏尾行，不静默修复重复键或跳过坏事件。

支持同一进程多个实例和异步调用者；不支持多个独立进程同时写同一文件。每次操作重读、每次 append 重写全部快照，适用于 PR1 小数据和单机演示。后续大量数据应换用事务型 Repository。
不承诺目录级断电持久性，也不把原子替换等同于防篡改。取消等待中的异步请求不保证终止已启动的文件线程；调用者遇到结果不确定时应按 event_id 查询后再决定重试。

默认未配置密码链：空任务 head 为 null；非空任务查询 head 报 CHECK_UNAVAILABLE。
绝不将 P1 `fake_chain_head` 标记为正式摘要。可注入与 P3 HashChain 同形的 `chain_factory(task_id)`：

```python
# 仅在第二轮合入并确认 P3 实现后使用。
from ra_agent.audit import HashChain
store = JsonlEventStore(Path(".runtime/p2/chained.jsonl"), chain_factory=HashChain)
```

提供方必须有同步 `append_event(event)->ChainEntry` 和 `get_chain_head()->str`。
链记录与事件同一次原子替换落盘；重启读取时由提供方重算并比对。空链值由提供方决定，P3 提案为 64 个 0。
已写无链日志不允许通过重新打开自动升级为有链日志；需要协调显式迁移，不能回填历史后声称当时已有锚定。
第一轮仅以测试替身校验这一接入机制，尚未运行 P3 实际密码模块的跨分支联调。

## 前端演示

在 frontend 下执行 `pnpm dev`，打开 `http://localhost:5173/core`。
Vite 开发服务挂载 `/__core_mock__/`，只提供固定场景和模拟确认；生产 build 不挂载 mock API。
如果只部署静态构建，此演示显示模拟服务不可用。此入口不是已接入真实 Gateway 的工作台。

场景包括 ALLOW、DENY、REQUIRE_CONFIRMATION、REQUIRE_REPLAN、空记录和服务异常。
确认成功返回固定的 CONFIRMED → GATEWAY_EVALUATED → EXECUTED 轨迹；拒绝返回 REJECTED，无执行事件。
mock HTTP 确认是无状态 fixture 重放，重复 HTTP 请求只重复模拟；不能据此宣称生产接口已经幂等。
前端不推导权限或签名结果，不会请求生产执行 API。错误、重试、按钮等待和旧响应覆盖均有测试。

## 与 1 号和 3 号的待对齐项

1. P1 目前事件缺少 actor、source_ref 等信息，执行事件包含原始 result。集成时由真实生产方提供来源和正确摘要，不能直接用该字典绕过模型校验。
2. P1 的 object_digest 当前是旧契约摘要；P3 提案要求引用完整签名对象摘要，必须协调升级。未知摘要用 null 的 fixture 不能代表真实审计关联已验证。
3. 本提案添加 schema_version、request_id、reason_code、evidence_refs 并填充可选默认字段；需三人确认最终签名输入对象。
4. 事件查询 API 的返回包装、分页，以及 export 证据包均留在 PR2 对齐；离线 OpenAPI 中的路径标记 planned-pr2，并未注册到后端。
5. 哈希链、签名、可信检查点和 AuditVerifier 由 3 号负责。本轮没有生成虚构签名或发布 core-v1.0。
