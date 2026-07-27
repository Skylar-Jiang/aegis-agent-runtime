# V2 实验数据规范

每次运行产生一行 JSON（JSONL 可接受）及同字段 CSV。`experiments/results/raw/` 存原始数据，`experiments/results/derived/` 存由脚本生成的聚合；图表只能读取 raw/derived，不能手工补数。

## 固定字段

| 分组 | 字段 |
| --- | --- |
| 标识 | `schema_version, run_id, case_id, repetition, mode, graph_id, task_id` |
| 可复现环境 | `started_at, finished_at, git_commit, python_version, node_version, os, environment_fingerprint, runner_command` |
| 任务 | `fixture_id, objective_class, node_count, dependency_edge_count, max_parallelism, tool_sequence` |
| 时延 | `elapsed_ms, graph_elapsed_ms, critical_path_ms, parallel_saved_ms, approval_wait_ms, rollback_elapsed_ms` |
| 安全 | `status, expected_status, safety_outcome, tool_executed_count, unsafe_tool_executed_count, blocked_count, risk_escalation_count, check_count, audit_event_count` |
| 人工与状态 | `approval_requested_count, approval_decision_count, manual_action_count, checkpoint_count, pending_effect_count, commit_count, rollback_count, selective_rollback_count, residual_effect_count` |
| 证据 | `audit_digest, raw_result_path, error_code, notes` |

`mode` 只可为 `BASELINE/FULL_GUARD/ADAPTIVE_RUNTIME`。三模式对比必须使用同一 `fixture_id`、初始 workspace、任务图、工具版本、超时和重复次数；Baseline 的绕过范围必须在 `notes` 说明。`parallel_saved_ms = max(0, sum(node_elapsed_ms) - graph_elapsed_ms)`，且必须记录每节点时间明细或 audit digest 以供复算。

## 必须汇总的真实指标

1. 平均/中位 `graph_elapsed_ms` 与 `parallel_saved_ms`：效率与并行收益。
2. `manual_action_count`、`approval_requested_count`：自适应审核的人力成本。
3. `rollback_success_rate`、`residual_effect_count`、`rollback_elapsed_ms`：选择性回滚效果。
4. `unsafe_request_block_rate`、`unsafe_tool_executed_count`、`false_block_count`：安全效果与可用性。
5. `check_count`、`audit_event_count`：风险自适应的检查/可追溯开销。

报告中须标注样本数、失败/跳过、运行日期、git commit 和限制；LLM 未参与的 runner 必须填 `token_usage=N/A`，不能虚构 token 或真实网络结果。
