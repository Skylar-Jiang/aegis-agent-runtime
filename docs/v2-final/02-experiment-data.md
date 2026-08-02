# V2 实验数据规范

每次运行产生一行 JSON（JSONL 可接受）及同字段 CSV。正式 V2 原始数据固定写入 `experiments/v2/results/raw/`，由脚本生成的聚合固定写入 `experiments/v2/results/derived/`；图表只能读取这些 raw/derived，不能手工补数。`experiments/results/**` 仅保留历史开发结果，禁止继续写入正式 V2 数据，也不得作为最终结论的数据源。

## 固定字段

| 分组 | 字段 |
| --- | --- |
| 标识 | `schema_version, run_id, case_id, repetition, mode, graph_id, task_id` |
| 可复现环境 | `started_at, finished_at, git_commit, python_version, node_version, os, environment_fingerprint, runner_command` |
| 任务 | `fixture_id, objective_class, node_count, dependency_edge_count, max_parallelism, tool_sequence` |
| 时延 | `elapsed_ms, graph_elapsed_ms, critical_path_ms, parallel_saved_ms, approval_wait_ms, rollback_elapsed_ms` |
| 安全 | `status, expected_status, safety_outcome, tool_executed_count, unsafe_tool_executed_count, blocked_count, false_block_count, risk_escalation_count, check_count, audit_event_count` |
| 人工与状态 | `approval_requested_count, approval_decision_count, manual_action_count, checkpoint_count, pending_effect_count, commit_count, rollback_count, selective_rollback_count, residual_effect_count` |
| 证据 | `audit_digest, raw_result_path, error_code, notes` |

`ExperimentResult.metrics` 是 v0.4 的冻结整数扩展位；只允许写入由 Runtime、Audit 或 runner 的 monotonic clock 推导的值。正式 Graph benchmark 必须写入：`sum_node_elapsed_ms`、`max_observed_concurrency`、`nodes_completed_during_approval`、`hidden_approval_wait_ms`、`affected_node_count`、`rolled_back_effect_count`、`preserved_node_count`、`preserved_effect_count`。未知值不得以猜测值填充；该场景不适用时填 `0` 并在 `notes` 说明原因。

`mode` 只可为 `BASELINE/FULL_GUARD/ADAPTIVE_RUNTIME`。三模式对比必须使用同一 `fixture_id`、初始 workspace、任务图、工具版本、超时和重复次数；Baseline 的绕过范围必须在 `notes` 说明。`parallel_saved_ms = max(0, sum_node_elapsed_ms - graph_elapsed_ms)`，并且两个输入都须来自 `perf_counter_ns` 的真实测量。`approval_wait_ms` 仅计算从 `APPROVAL_REQUESTED` 到实际 grant/deny 的间隔；`hidden_approval_wait_ms` 只记录等待期间仍可执行的独立节点时间；`rollback_elapsed_ms` 仅计算 `ROLLBACK_STARTED` 到 `ROLLBACK_FINISHED`。禁止用总耗时、异常是否抛出或预期结果替代这些事实。

`false_block_count` 是每条 raw `ExperimentResult` 的唯一冻结计数：仅当实际 `safety_outcome == FALSE_BLOCK` 时写 `1`，否则写 `0`。所有汇总必须累加该字段，不得另行从异常、预期状态或文本规则推断。`unsafe_request_block_rate` 保持为由 raw status/ground truth 派生的比例指标，不新增第二套存储字段。

## 必须汇总的真实指标

1. 平均/中位 `graph_elapsed_ms` 与 `parallel_saved_ms`：效率与并行收益。
2. `manual_action_count`、`approval_requested_count`：自适应审核的人力成本。
3. `rollback_success_rate`、`residual_effect_count`、`rollback_elapsed_ms`：选择性回滚效果。
4. `unsafe_request_block_rate`、`unsafe_tool_executed_count`、`false_block_count`：安全效果与可用性。
5. `check_count`、`audit_event_count`：风险自适应的检查/可追溯开销。

报告中须标注样本数、失败/跳过、运行日期、git commit 和限制；LLM 未参与的 runner 必须填 `token_usage=N/A`，不能虚构 token 或真实网络结果。
