# Phase 3 清理审计报告

| 路径 | 原用途 | 决定 | 判断依据/引用检查 | 替代或原因 |
| --- | --- | --- | --- | --- |
| `README.md` | 项目入口 | UPDATE | 当前开发入口直接引用 | 更新为 Phase 2 完成、Phase 3 入口 |
| `docs/00-scope.md` | Phase 1 范围 | UPDATE | 开发文档仍引用 | 改为历史范围并链接 Phase 3 |
| `docs/01-architecture.md` | 架构边界 | UPDATE | README 引用 | 修正持久化已实现描述 |
| `docs/12-module-ownership.md` | 并行所有权 | UPDATE | README 引用 | Phase 3 以 `docs/phase3/` 为执行边界 |
| `docs/SHARED-BASELINE-REPORT.md` | Phase 1 快照 | UPDATE | README 引用且含历史决策 | 明确为历史，不删除唯一基线信息 |
| `docs/FINAL-INTEGRATION-REPORT.md` | Phase 2 集成证据 | KEEP_WITH_REASON | 仍解释当前安全边界 | 仅标记为历史验证快照 |
| `docs/history/phase-0-summary.md` | Phase 0 背景 | KEEP_WITH_REASON | README 直接引用 | 原始架构决策仍有价值 |
| migrations、lock、configs、contracts、tests | 可运行基线 | KEEP | 有 import/测试/运行引用 | 用户禁止删除 |

未发现满足 DELETE 或 MERGE_DELETE 的候选：所有旧文件仍被引用或含唯一历史设计信息。代码候选中的 `placeholder`/`TODO` 搜索命中均为有效测试、迁移或 Phase 3 待实现边界，证据不足，保留。
