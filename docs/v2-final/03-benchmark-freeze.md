# V2 正式 Benchmark 冻结

基线：`572a2a67c758a4e2a71d9f7c30bd5ab6d0fc0c4e` 加本次冻结提交。本文是正式实验的唯一口径；此前 `experiments/runners/run_experiment.py` 的单节点结果和已有 raw 文件仅作开发检查，不能作为本规范任一结论的正式数据。

## 对比模式

| Mode | 保留 | 明确绕过/约束 |
| --- | --- | --- |
| `BASELINE` | 相同 fixture、ToolSpec、受控 handler、超时与输出验证 | 仅移除本 case 要消融的自适应风险、深度检查、图调度或选择性 rollback；绕过清单逐行写入 `notes`，不得直接写用户目录或真实外部系统。 |
| `FULL_GUARD` | 全部硬边界、受控执行、Audit、同一审批脚本 | 非阻断节点统一走保守检查/审批，独立节点不在审批等待期间推进。 |
| `ADAPTIVE_RUNTIME` | 全部硬边界、Risk/Policy/Permission、TaskGraph、受控执行、选择性 rollback | 仅真正独立的节点并行；等待审批只阻断依赖或冲突节点。 |

三种模式不得改变 case 的 task 语义、初始状态、重复次数、工具版本或 timeout。Baseline 是明确的受控消融，不是任意直接副作用执行。

## 正式家族与可回答的主张

| 家族 | 必测事实 | 核心创新 |
| --- | --- | --- |
| Safety | 危险 shell、敏感访问、越界、注入、memory poisoning、egress | 支撑安全机制 |
| Controlled Transaction | checkpoint → pending → check → commit/rollback | Transactional Controlled Execution |
| Parallel TaskGraph | 独立/依赖/target-conflict 节点的 wall time、临界路径和并发 | Approval-aware TaskGraph Scheduling |
| Approval Wait | independent sibling 在等待期间的完成数、人工次数与隐藏等待 | Approval-aware TaskGraph Scheduling |
| Selective Rollback | File、Memory、Download 的 affected/preserved/residual scope | Dependency-aware Selective Rollback |
| Memory Safety | Pending → validation → Trusted/Rollback 和 poison 拒绝 | 事务式受控执行的支撑证据 |
| End-to-End | 一个含 Graph、approval、effect、failure 的确定性流程 | 三项创新的联动证据 |

## 时间、事实与结果

Runner 使用 `perf_counter_ns`；UTC 时间仅用于审计排序。每次运行必须先写 JSONL raw，再由脚本产生 CSV、聚合和图表。统一模型是 `ExperimentResult`：直接字段保存通用指标，`metrics` 保存 `sum_node_elapsed_ms`、`max_observed_concurrency`、`nodes_completed_during_approval`、`hidden_approval_wait_ms`、`affected_node_count`、`rolled_back_effect_count`、`preserved_node_count`、`preserved_effect_count`。

- `graph_elapsed_ms`：Graph 提交到 terminal snapshot 的 wall-clock。
- `critical_path_ms`：由真实 node start/finish 推导的最长依赖路径。
- `parallel_saved_ms = max(0, sum_node_elapsed_ms - graph_elapsed_ms)`。
- `safety_outcome` 由 Runtime/Audit 的 block、执行和状态事实判断，绝不以 exception 是否发生判断。
- `residual_effect_count` 在 cleanup/rollback 后从 EffectStore 读取；不得手填。

## Fixture

正式目录固定为 `experiments/v2/{fixtures,runners,results/raw,results/derived}`。fixture 必须 deterministic、可重复、仅使用临时目录或 `.runtime/workspace`、不访问真实用户目录、不产生真实危险外部副作用，并为每个 case 写明 ground truth。允许使用确定性 handler 制造时延，但同时必须断言 Runtime 状态与 Audit，不能仅以 sleep 声称并行。

## 当前边界

当前 Runtime 已支持 sibling Graph 并行和 effect-conflict 串行化；尚未实现“同一 MEDIUM 节点的 isolated execution 与独立 safety check 重叠”。正式报告在该能力实现并有测试和 raw data 前只能称为**未实现**，不得与 sibling 并行混称为 speculative execution。
