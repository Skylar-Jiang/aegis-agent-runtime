# V2 架构与接口冻结（历史开发基线）

> 本文记录最终集成前的 V2 开发冻结与分工，不是当前实现的唯一规范。最终实现、验收命令和正式数据以根目录 `README.md`、`docs/final-integration-demo.md`、`docs/final-integration-assets/README.md` 及 `scripts/verify_final_experiments.py` 为准；旧 `docs/phase3/` 与本文均保留为可追溯的实施记录。

## 1. 当前状态审计

| 能力 | `main` 已实现 | V2 处理 |
| --- | --- | --- |
| Agent、异步任务、SSE、最终回答 | 已有 Live Planner、`/api/tasks`、序号恢复 SSE 和 Assistant response | 保持，不重写 Planner/UI 主链 |
| 单步风险 Runtime | Scheduler 是唯一副作用入口；Risk/Policy/Permission、Pre/Post、FAST/SANDBOX/Approval/Block 已存在 | 组长扩展为图级调度，不复制单步安全判断 |
| TaskContract 与硬边界 | SafePath、敏感资源、egress、allowlist、关联校验均已存在 | 保持 fail-closed，不把普通合法操作提前 BLOCK |
| TaskGraph | 有内存 `TaskGraphRunner`、依赖、条件和并发上限 | 缺少可序列化 Graph Contract、风险感知 ready queue、图级审计和选择性回滚 |
| Checkpoint / pending / commit | 文件、下载、Memory 的受控状态与清理已有 | 统一 effect 事实，补图级 rollback plan；不重做存储实现 |
| Approval | 两阶段 ApprovalFlow、持久化服务和 API 基础已存在 | 成员 2 完善自适应审核规则/指标；组长保留恢复接线 |
| Adaptive risk 候选 | `codex/adaptive-risk-policy` 有 risk floor、语义升档、审批恢复、taint 处理等候选改动 | 未合入 `main`；先按本文 Contract 逐项审查、测试后再择优集成 |
| 审计与展示 | AuditEvent、报告 API、SSE 和工作台已存在 | 成员 4 增加图/实验只读投影，不改变事实语义 |
| 实验 | 有三模式确定性 runner 与 JSON/CSV 雏形 | 缺少并行收益、人工次数、选择性回滚和统一环境字段 |
| 作品报告 | 有策划书和阶段报告 | 本分支新增可并行填写的报告骨架 |

V2 非目标：分布式工作流、任意 OS 进程监控、真实邮件发送、通用 Computer Agent、用 LLM 替代硬边界。

## 2. 冻结架构与数据流

```text
TaskCreate + TaskContract
  -> Planner (untrusted proposal only)
  -> TaskGraph / ready queue                         [组长]
  -> RuntimeScheduler (唯一副作用入口)               [组长]
  -> IntentBoundary -> Risk -> Policy -> Permission [成员2]
  -> FAST | PENDING+checkpoint+checks | Approval | Block
       ^              [成员3]             [成员2]
  -> Effect/Commit/Rollback                           [成员3]
  -> AuditEvent -> SSE/API -> Graph/Dashboard         [成员4]
  -> ExperimentResult JSON/CSV -> Report              [成员4 汇总]
```

规则：Planner、前端、Checker、ToolHandler 都不能直接调用工具；所有节点都经 Scheduler。只有无数据/状态依赖、权限不互斥且不共享 effect target 的节点可并行。MEDIUM 节点仅在隔离 pending 执行与独立检查之间并行；Commit 必须等待全部必需 PASS。任一检查失败、取消或监控中断均 fail-closed，并只回滚该节点及其声明依赖的 effect。

## 3. Contract v0.4（只加法，组长独占）

现有 v0.3 字段、枚举值和 `request_id` 幂等语义不变。新增模型放入 `backend/src/ra_agent/contracts/`，以 Pydantic/UTC/`extra=forbid` 为准：

| 模型 | 冻结字段与语义 |
| --- | --- |
| `TaskNode` | `task_id, graph_id, node_id, request, dependencies, parallel_safe, effect_targets`；`dependencies` 仅为 node ID，禁止 Python callback 进入公共 Contract。 |
| `TaskGraph` | `graph_id, task_id, nodes, max_parallelism`；node ID 唯一、依赖无环、所有 request 与 task_id 一致。 |
| `TaskGraphResult` | `graph_id, task_id, node_results, blocked_nodes, started_at, finished_at`；`blocked_nodes` 是 `{node_id: reason}`，不把未执行伪装为成功。 |
| `EffectRecord` | `effect_id, task_id, step_id, request_id, kind, target_ref, status, checkpoint_id, artifact_refs, created_at`；status 仅 `PENDING/COMMITTED/ROLLED_BACK/REJECTED/CLEANED`。它替代跨模块自由形状的 pending 事实，不持久化工具原始输出。 |
| `RollbackPlan` | `plan_id, task_id, trigger, request_ids, checkpoint_ids, effect_ids, reason`；范围必须显式、可验证且只包含本任务 effect。 |
| `RollbackPlanResult` | `plan_id, task_id, rolled_back_request_ids, failed_request_ids, status, reason`；部分失败必须保留失败列表并使任务失败。 |
| `ExperimentResult` | 字段见 `02-experiment-data.md`；每行表示一次真实运行，禁止由图表反推或补造。 |

现有 `CheckpointResult`、`ToolExecutionResult`、`ApprovalRequest/Decision`、`RollbackResult`、`AuditEvent` 继续是单节点事实。`ToolExecutionResult.output` 是不可信数据；若带 taint，后续 Planner 不得接收原始内容。`AuditEvent.details` 只允许脱敏摘要；图级事件统一写入 `graph_id`、`node_id`、`effect_id`、`rollback_plan_id`（不存在时省略）。

枚举冻结：保留 v0.3 全部值；仅新增 `EffectStatus` 与图级 audit event 时由组长在首个 V2 Contract 提交中一次性添加。成员不得在业务目录自定义同义 enum/model。

## 4. API 冻结

保留既有 `/api/tasks`、`/events`、`/stream`、`/report`、`/cancel`、approval grant/deny 的响应包装 `{data,error}` 和 SSE `sequence_number` 语义。V2 只新增：

| Method | Path | 返回 | 责任 |
| --- | --- | --- | --- |
| GET | `/api/tasks/{task_id}/graph` | `TaskGraphResult` 的只读快照 | 成员 4 provider，组长注册 |
| GET | `/api/tasks/{task_id}/approvals` | 当前任务审批列表 | 组长接线；前端不得从 Audit 猜 approval ID |
| GET | `/api/approvals` | pending 审批列表 | 组长接线 |
| GET | `/api/experiments/{run_id}` | 单个 `ExperimentResult` 或聚合 | 成员 4 provider，组长注册 |

实验由受控 CLI runner 创建，不开放任意 HTTP 运行接口。破坏性 API、枚举或 Contract 改动必须由组长更新 Contract tests、前端类型和本文档后才能合并。

## 5. 目录、依赖和入口边界

- `contracts/`、`core/bootstrap.py`、`core/container.py`、`main.py`、公共 router、`runtime/scheduler.py`、`runtime/task_graph.py`、状态机：组长独占。
- `security/` 和 `configs/{risk_rules,permissions,sensitive_paths}.yaml`：成员 2；只依赖 Contract/Protocol，不 import executor/tool handler。
- `execution/`、`memory/`、`tools/` 和工具策略配置：成员 3；只产生 effect/artifact，不裁决风险或审批。
- `audit/`、`frontend/`、`experiments/`、展示专用 API provider、`tests/e2e/`：成员 4；只消费 Contract、Audit/API，不能调用内部 Scheduler 方法。
- 每位成员如需 API/Container 接入，交付独立 `*_provider.py` 或 registration 函数及测试；组长在最后统一注册，不接受成员修改 `main.py`。

## 6. 必过不变量

1. 所有真实副作用经 Scheduler；相同 `request_id` 仍幂等。
2. SafePath、敏感资源、TaskContract、egress、shell allowlist 和 untrusted-output 边界不得降低。
3. HIGH 不在审批前执行；CRITICAL/FORBIDDEN 不执行；WAITING_APPROVAL/FAILED/CANCELLED 不得生成成功 final answer。
4. Commit 只在所需检查 PASS 后发生；rollback 仅处理 RollbackPlan 的 effect；Audit 不含密钥、原始敏感文件或 chain-of-thought。
