# V2 四人任务单与集成方案

团队总数为四人，含组长；不设成员完成时间，由组长统一设置 DDL。每人从 V2 基线分支开发、提供独立测试和真实实验原始数据。

## 组长（成员 1）— Runtime / Graph / 集成

- **代码范围：** `runtime/`、`agent/` 的 Runtime 接线、`contracts/`、`core/{container,bootstrap}.py`、`main.py`、公共 router、`tests/integration/`、`docs/v2-final/`。
- **交付：** 将 v0.4 Contract 一次性落地；可序列化 TaskGraph、风险感知 ready queue、共享 target 串行化、独立节点并行、等待审批只阻断后代、选择性 RollbackPlan；最后统一 provider 注册和全量 Gate。
- **完成标准：** Graph 的无环/关联/并发上限/依赖/审批/取消/选择性回滚集成测试；Scheduler 仍是唯一副作用入口。
- **量化数据：** `graph_elapsed_ms`、`critical_path_ms`、`parallel_saved_ms`、`max_concurrency`、`blocked_descendant_count`、`selective_rollback_count`。
- **报告与素材：** 报告 2、3、7 节；架构图、Graph 执行轨迹图、并行时序图。
- **禁止修改：** 成员 2/3/4 私有实现目录的内部逻辑；不在最终接线时重构其模块。

## 成员 2 — Risk / Policy / Permission / Adaptive Approval

- **代码范围：** `security/`、`configs/risk_rules.yaml`、`configs/permissions.yaml`、`configs/sensitive_paths.yaml`、`tests/security/`；可提交 `security_provider.py`，不直接改 bootstrap/router。
- **交付：** 工具风险 floor、上下文/数据血缘/注入信号升档、Policy 映射、按需 Permission、可解释 Adaptive Approval；HIGH 审批必须关联原始 request，拒绝保持零副作用。
- **完成标准：** LOW/MEDIUM/HIGH/CRITICAL、敏感路径、越界、egress、注入、审批 grant/deny/expire 的独立测试；配置无效 fail-closed。
- **量化数据：** `approval_requested_count`、`approval_decision_count`、`manual_action_count`、`unsafe_request_block_rate`、`false_block_count`、`risk_escalation_count`。
- **报告与素材：** 报告 3、4 节；风险-策略表、审批卡片截图、安全拦截案例表。
- **禁止修改：** `contracts/`、Scheduler/TaskGraph、execution/memory/tools 内部、公共 router/bootstrap/main。

## 成员 3 — Checkpoint / Effect / Commit / Selective Rollback / Memory

- **代码范围：** `execution/`、`memory/`、`tools/`、`configs/tool_policies.yaml`、`tests/rollback/`、`tests/unit/execution/`；可提交 `execution_provider.py`。
- **交付：** 将现有 pending/checkpoint/commit/rollback 适配 `EffectRecord` 和 `RollbackPlan`；文件与 Memory 的选择性回滚、清理幂等、effect target 冲突事实；不改变 Scheduler 决策。
- **完成标准：** 多节点中一个 effect 回滚而已提交独立 effect 保留；commit/rollback/cleanup 异常 fail-closed；checkpoint/effect 关联和 Memory 状态独立测试。
- **量化数据：** `checkpoint_count`、`pending_effect_count`、`commit_count`、`rollback_count`、`rollback_success_rate`、`residual_effect_count`、`rollback_elapsed_ms`。
- **报告与素材：** 报告 5 节；pending→commit/rollback 时序、回滚前后目录/Memory 对比截图、选择性回滚表。
- **禁止修改：** `contracts/`、Risk/Policy/Permission、Scheduler/TaskGraph、公共 router/bootstrap/main；不得绕过 SafePath 或执行任意 shell。

## 成员 4 — 展示 / Audit / Experiment / Dashboard

- **代码范围：** `frontend/`、`audit/`、`experiments/`、展示专用 `api/*_provider.py`、`tests/e2e/`、`docs/report-v2/` 的汇总素材；不得直接修改公共 router。
- **交付：** Graph/节点/审批/commit/rollback/Audit 时间线可视化；统一 runner、JSON/CSV 校验、实验 Dashboard 和报告图表。只展示脱敏安全事实和 final answer，不展示 chain-of-thought 或原始不可信输出。
- **完成标准：** SSE 重连、图状态、审批卡、实验载入的前端测试；runner 对相同 fixture/mode 输出可复现记录；图表可由 raw CSV 重建。
- **量化数据：** 汇总三模式运行时间、检查数、人工次数、并行收益、回滚与安全效果；记录 `environment_fingerprint` 和 raw result 路径。
- **报告与素材：** 报告 6、7 节；工作台截图、实验柱/折线图、审计时间线、Demo 脚本素材。
- **禁止修改：** `contracts/`、Scheduler/TaskGraph、security/execution/memory/tools 内部、`main.py`、`core/bootstrap.py`。

## 最简 Git 流程

1. 组长提交本冻结文档；随后在 `codex/v2-final-integration` 落地 v0.4 Contract、空 provider 接口和 Graph/Scheduler 早期骨架，再从该 SHA 切四条工作分支。
2. 分支：`codex/v2-runtime-graph`（组长）、`codex/v2-risk-policy`（成员 2）、`codex/v2-effects-rollback`（成员 3）、`codex/v2-visualization-experiments`（成员 4）。所有成员分支只 rebase 到 V2 integration，不改 main。
3. 组长的 Runtime 基础先进入 integration，确保成员只面对稳定 Contract。最终按 **成员 2 → 成员 3 → 成员 4 → 组长少量 provider 注册/接线** 合并；每次 merge 后运行责任模块测试。
4. 不做 squash 后的大型手工适配；冲突只由组长处理公共文件。成员的 Contract 需求必须先提交 issue/变更说明，不能自行改 schema。

## 最终集成 checklist

- [ ] integration 基线含 Contract v0.4、provider Protocol 和 Contract tests。
- [ ] 成员 2 安全测试、原始安全/人工数据与报告素材齐全。
- [ ] 成员 3 effect/rollback 测试、原始回滚数据与报告素材齐全。
- [ ] 成员 4 JSON/CSV schema 校验、Dashboard/E2E、可复建图表与报告素材齐全。
- [ ] 组长完成 Graph 并行、审批等待、取消、选择性回滚和跨模块关联测试。
- [ ] Ruff、Pyright、backend pytest、frontend lint/typecheck/Vitest/build、Demo/E2E 全通过。
- [ ] 真实实验数据包含环境和命令；无 `.env`、`.runtime`、密钥、未脱敏工具输出或伪造结果。
- [ ] 只在以上项目完成后将 integration 正常合并至 main；禁止 force push。

## 第二轮最终并行（覆盖以上分支名）

所有人从本次 freeze 后的 `codex/v2-final-integration` HEAD 创建分支；禁止直接改 `main`，共享 Contract/API/公共入口只能由组长改动。

| 成员 | 分支 | 可修改范围 | 必须交付 | 禁止修改 |
| --- | --- | --- | --- | --- |
| 组长 | `codex/v2-benchmark-runtime` | `runtime/`、公共 API/router、`core/`、`contracts/`、`tests/integration/`、`experiments/v2/runners/`、`docs/v2-final/` | Graph submit/snapshot/resume/cancel、真实 Graph metric、parallel/global-pause fixture、speculative-check 能力确认、全量集成 | 成员 2/3/4 私有逻辑 |
| 成员 2 | `codex/v2-safety-evaluation` | `security/`、安全配置、`tests/security/`、`experiments/v2/{fixtures,runners,results}/` 的安全数据、报告第 3 节 | Safety/approval 三模式、注入/敏感/shell/egress/poison、人工成本 raw/derived | Scheduler、TaskGraph、Effect/rollback、公共 API/Contract |
| 成员 3 | `codex/v2-rollback-evaluation` | `execution/`、`memory/`、`tools/`、`tests/rollback/`、`experiments/v2/{fixtures,runners,results}/` 的回滚数据、报告第 4 节 | File/Memory/Download selective rollback、residual/preserved/elapsed raw data | Scheduler/TaskGraph、安全策略、公共 API/Contract |
| 成员 4 | `codex/v2-visualization-final` | `frontend/`、`audit/`、`api/*_provider.py`、`experiments/v2/results/derived/`、`tests/e2e/`、报告第 5 节与素材 | Graph/effect/audit/dashboard、approval cards、V2 experiment type/API 消费、图表与截图 | Contract、Scheduler/TaskGraph、security/execution/memory/tools、`main.py`/bootstrap/router |

成员提交必须回答：实现了什么、哪一项真实指标、raw/derived 路径、报告章节和截图/图表。截图写明 run ID、commit、日期；数字只能来自 raw/derived。

合并顺序固定为：`codex/v2-benchmark-runtime` → `codex/v2-safety-evaluation` → `codex/v2-rollback-evaluation` → `codex/v2-visualization-final` → 组长最少量 provider 注册与修复。每次 merge 后运行该模块测试、Contract tests 和 integration tests；全部完成后才运行全量 Gate、Demo、正式 benchmark 并最终合 main。
