# 第二轮并行前基线审计

检查基线：`572a2a67c758a4e2a71d9f7c30bd5ab6d0fc0c4e`。本次冻结前验证：Ruff、Pyright（0 errors / 0 warnings）、backend pytest（866 passed, 8 skipped）、Phase 3.5/live/V2 integration（112 passed）、frontend lint/typecheck/Vitest（8 tests）/build 均通过。

| 范围 | 当前事实 | 第二轮必须完成 | Owner |
| --- | --- | --- | --- |
| Controlled transaction | 单节点 checkpoint/pending/check/commit/rollback 已有；Graph cancel 可回滚 pending Memory effect | File/Memory/Download 的正式 transaction benchmark 与 residual audit | 成员 3 |
| TaskGraph | 依赖、parallel_safe、可信 File/Memory/Download target、approval fence、descendant block、cancel 已有测试 | 正式后台 Graph submit/snapshot/resume/cancel API 与图级 metric | 组长 |
| Selective rollback | RollbackPlan、EffectStore、保留 independent committed work 已有 | global-vs-selective benchmark、affected/preserved facts | 成员 3 |
| Memory lifecycle | pending/trusted/reject/rollback 与 poison 防护已有 | 正式 memory safety fixture/raw data | 成员 2 + 成员 3 |
| Security | Intent/Risk/Policy/Permission/Approval/Injection/sensitive/shell/egress 已有 | 三模式公平 safety benchmark 与人工成本 | 成员 2 |
| Observability | task Audit/SSE、Graph audit、final answer 已有 | graph/effect snapshot API、无手填 approval UI、V2 dashboard | 组长 + 成员 4 |
| Experiments | v0.4 JSON/CSV runner、security runner 已有 | 正式 Graph runner；现有单节点 runner 不得用于最终并行/rollback结论 | 组长 + 成员 4 |

### 必须但尚未实现

1. Graph execution/snapshot/resume/cancel 与 effect projection API（组长）。
2. 正式 Graph benchmark runner 和真实 graph-level timing/effect metrics（组长）。
3. Approval list/card UX，移除当前手填 approval ID 页面（成员 4，消费冻结 API）。
4. 前端 ExperimentResult 仍是旧字段集，需迁移为 v0.4 + `metrics`（成员 4）。
5. File/Memory/Download 的正式 selective rollback suite 与 raw data（成员 3）。
6. Safety/approval 的三模式正式 suite 与 raw data（成员 2）。
7. 同节点 speculative check overlap 尚未实现；只有实现、测试、数据三者齐全后可作为能力主张（组长）。

以上均有冻结 owner 与接口，不阻塞成员从下一 integration SHA 分支开始；但在最终合并前均为 P0。
