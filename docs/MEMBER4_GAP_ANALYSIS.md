# Member 4 仓库审计和 gap analysis

审计日期：2026-10-02。仓库：Skylar-Jiang/aegis-agent-runtime。
审计起点：`d60f443996a6e8e11927ba8fe018eeea0813ecd3`，远端
`aegis-intent-dev`；独立 checkout `D:\aegis-intent-member4`，feature 分支
`codex/member4-telecom-demo`。本报告写作前没有修改生产代码。

## 审计方法和材料边界

对 Git 跟踪文件完整盘点，检查包清单、启动路径、路由、公共模型、网关、
安全检查、执行适配器、Memory、事件和实验目录；对关键实现与测试交叉阅读，
运行现有验证。仓库起点有 546 个跟踪文件、172 个后端 Python 文件、128 个
Python 测试文件（contract 7、e2e 2、integration 28、rollback 2、security 17、
unit 72），另有前端 Vitest 和 Playwright 文件。完整盘点写入本机
`.runtime/baseline-inventory.json`，可用 `git ls-files` 重建。

附件副本和提取文字位于 `docs/reference/`。Word 是计划与字段表，PDF 是比赛方案，
均不能作为实现证明。Word 自己说明“本文不表示代码已经完成或实验结果已经得到”。
附件中的日期、分工和技术设计不覆盖用户当前指令；场景以通信运维为准。
本次仅提取文本核查内容，不对附件版式作交付验收。官方提交、参赛资格、500M
口径不在本次开发中作合格声明；10 月 31 日是内部计划日期。

## 实际完成情况

| 模块 | 实际代码/证据 | 判断与边界 |
| --- | --- | --- |
| Runtime Base | `agent/runtime.py`, `core/bootstrap.py`, `runtime/orchestrator.py`, `database/*` | 存在 Agent/Task/Conversation、配置持久化、审批恢复和调度实现；不等同于 Intent 成品 |
| 工具入口 | `gateway/gateway.py`, `permissions/resolver.py`, `gateway/runtime_bridge.py` | Core 网关有契约版本、权限交集、执行前再检查、确认、replan 和防旁路 capability |
| 文件及副作用 | `execution/*`, `gateway/adapters.py`, `tools/implementations/*` | 文件、Memory、隔离 pending/commit/rollback、受限进程和模拟外发实现存在；不同模式的能力要分别核验 |
| TaskContract | `contracts/tasks.py`, `contracts/core_v1.py`, `contracts/core_service.py` | 旧 Runtime TaskContract 与 Core TaskContractV2 均存在；Core 使用 version/goals/allowed 等，和附件 Intent 字段表不是同一模型 |
| BehaviorEvent | `events/models.py`, `events/store.py`, `events/sqlite_store.py` | Core 事件真实存在（sequence/type/state/occurred_at）；不是附件 step_index/source_type/subgoal 等 Intent 事件结构 |
| 加密审计 | `crypto/*`, `audit/*`, `crypto/runtime.py` | SM2/SM3、证据及验证实现存在；默认 fake 签名模式没有真实签名证据，不能称为已验签 |
| 现有意图边界 | `security/intent_boundary.py`, `tests/security/test_intent_boundary.py` | 按允许动作、资源、对象数和外发权限拒绝；没有语义/序列/来源模型评分 |
| 前端 | `frontend/src/App.tsx`, `pages/*`, `features/core/*` | React 19 + TS + Vite，任务/会话/审批/审计/实验/图页面和真实 Core 工作台存在；另有明确 fake Core fixture server |
| Demo | `api/demo.py`, `DEMO_GUIDE.md`, `scripts/smoke_core.py` | 存量是报告文件、审批、依赖图、恢复演示；需要 demo 开关；无通信设备资料场景 |
| 实验 | `experiments/v2/*`, `experiments/runners/*` | 存在 safety、graph、rollback、real-agent、消融及历史 final-evidence；不能改称 Intent 序列检测实验 |
| 验证框架 | `scripts/check.py`, `.github/workflows/ci.yml`, `pytest.ini`, `frontend/package.json` | pytest/Ruff/Pyright，Vitest/Testing Library、TS/ESLint/Vite、Playwright 可复用，无需新增大型依赖 |

以上后端路径相对 `backend/src/ra_agent/`。

## 特别核对的声明

全仓搜索（生产源码、测试、脚本、实验；排除本次新增文档）结果：

| 名称 | 起点实现 | gap |
| --- | --- | --- |
| IntentSpec | 无 | 缺公共模型/schema、抽取、语义校验、可信确认和版本接口 |
| IntentPanel | 无 | 缺专用页面、检测证据、处置/纠偏和 effect 对照 |
| IntentSafetyEngine | 无 | 缺轻量语义、序列、来源检测及版本化 DecisionResult |
| intent experiments | 无 | 缺专属数据划分、标注、runner、原始结果及统计；v2 安全实验不是 Intent 实验 |
| DecisionResult / CorrectionPlan / EffectCheck | 无附件对应结构 | 有 Core GatewayDecision 和 EffectManager 等相近功能，但字段和语义不能直接替代 |
| Intent JSON Schema | 无 | 附件末尾只有字段名，没有正式类型、枚举、约束和 API 版本 |

README 使用“扩展实现”等措辞描述整体能力；它没有对应新增 Intent 代码证据。
`ACCEPTANCE.md` 明确说尚未实现竞赛意图评分和行为序列模型。
若干文档引用的 `docs/RUNTIME_BASE_VALIDATION.md` 等路径在这个起点不存在；
历史测试数和历史结果不是本次验证结果。

## Member 4 最小实现和依赖处理

用户已授权缺少 schema 时定义并注明内容和地点（2026-10-02）。新增 schema
保留附件全部字段名，放 `docs/contracts/intent-demo-v0.1.schema.json`，对应局部
demo 模型；不替换 `contracts/core_v1.py` 或 `events/models.py`。

模拟检测器只根据已登记 fixture 提供脚本化 DecisionResult，显式显示
`fixture/mock`，不开发检测算法。Core 权限允许隔离目标，再由 demo admission
按脚本决策阻止副作用，展示“权限允许但 demo 决策拒绝”的机械闭环。
这只证明展示、闸门和 effect 检查，不能证明真实 Intent 检测率、泛化或纠偏模型。

所有数据为合成配置、工单、日志、知识库、Memory 和 expected state；配置修改
只写隔离文件，外发只写本地记录。正常/合法变更和三个攻击共用同一工具环境。
reset 清理整个 demo 状态和计数，但不接触用户工作区或 Runtime 的其他历史。

## 实施顺序与成功标准

1. 审计文档和 AGENTS → 原始基线检查有可追溯结果，生产代码无修改。
2. schema、fixture、后端 adapter → pytest 检查三攻击/两对照、暂停后拒绝执行、
   真实文件/Memory/端点 effect、reset 和失效请求。
3. React Intent 页面 → 接现有后端 API；加载/错误/运行/暂停/恢复都有真实状态；
   Vitest、TS、构建通过。
4. API/浏览器回归和脚本 → 逐例交叉核对 UI、事件、hash、Memory、端点和
   EffectCheck；启动/reset/replay/干净环境可复现，形成 DEMO_4_ACCEPTANCE.md。

每阶段独立提交；提交前执行后端相关测试、前后端类型检查、前端测试与构建。
最终推送独立 feature 分支，不自行合并 PR。

## 验证结果

基线验证完成后登记于 `docs/evidence/member4/baseline-checks.md`；任何存量失败
按原始结果记录，不把旧文档的 PASS 当成本次结果。
