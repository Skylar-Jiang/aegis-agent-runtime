# Intent demo schema 0.1

位置：`docs/contracts/intent-demo-v0.1.schema.json`。
模型源：`backend/src/ra_agent/intent_demo/contracts.py`。
样例：运行 `POST /api/intent-demo/runs` 获得的 run 含六种完整对象；固定回放输入在
`fixtures/telecom/cases.json`。用户于 2026-10-02 授权定义缺失 schema。

这是一份待 1/3 号确认的联调提案，保留附件字段冻结表的全部字段名，所有字段
必填（允许 null 的字段也显式出现），拒绝额外字段。没有替换或重构 Core
TaskContractV2 / BehaviorEvent；使用方必须区分这两套结构。

| 对象 | 类型与语义 |
| --- | --- |
| IntentSpec | goal 为文本；scope/actions/criteria/source_refs 为字符串数组；version ≥ 1；confirmed_by/confirmed_at 可空，确认时间为带时区 ISO datetime |
| TaskContract | contract_version ≥ 1；intent_ref 格式 `intent_id:vN`；resources/tools/completion_conditions 为字符串数组；permissions 为资源→权限标签映射；parent_version 可空；status 为 DRAFT/CONFIRMED/SUPERSEDED |
| BehaviorEvent | step_index ≥ 1；source_type 为 user/agent/retrieval/tool/memory/system；args_digest 为规范化参数 SHA-256；timestamp 带时区；risk_flags 为字符串数组 |
| DecisionResult | decision 为 ALLOW/BLOCK/CLARIFY/REPLAN；risk_score ∈ [0,1]；dimensions/evidence 为字符串数组；policy/detector 版本显式；expires_at 带时区；未知/过期不能执行 |
| CorrectionPlan | base_contract_version ≥ 1；contaminated_refs/added_scope 为字符串数组；proposed_actions 为对象数组，仅在 demo 中是固定 tool/target；confirmation_required 为布尔；retry_budget 0–3；status 为 PROPOSED/EXECUTED/REJECTED |
| EffectCheck | before/after_digest 为 SHA-256；side_effect_ref 可空；status 为 UNCHANGED/APPLIED/FAILED/MISMATCH；本 demo 比较整个实测文件+Memory+端点快照，normalized_target 标明动作目标 |

摘要：UTF-8 JSON，键排序，`ensure_ascii=False`，无空白分隔；TaskContract digest
覆盖自身所有字段，排除 digest。状态标为 SUPERSEDED 后重算展示摘要；它不作为
签名摘要，也不替代 Core 的 immutable contract version ref。端点 payload_digest
覆盖实际参数对象，不等于报告文件的裸字节摘要。文件 hash 使用原始字节 SHA-256。
时间统一使用 UTC ISO 8601。schema 文件由 `IntentDemoSchema.model_json_schema()`
生成；contract 测试核对与源模型完全一致。

传输 envelope 是独立 demo view：包含 run_id/status、上述对象、Core 契约历史、
Core gateway decisions 和持久化 timeline，以及实际 baseline/observed/acceptance。
它的字段不被伪装成 Intent 公共模型字段。DecisionResult 的 `detector_version`
始终为 `fixture/mock:0.1`；risk_score 是脚本标签，不是检测模型输出。

API（均返回既有 `{data,error}` 格式）：

- `GET /api/intent-demo/cases`：固定六个合成 Case。
- `POST /api/intent-demo/runs {case_id}`：运行固定脚本，到完成或处置暂停点。
- `GET /api/intent-demo/runs/{run_id}`：重新读磁盘核验和完整事实视图。
- `POST /api/intent-demo/runs/{run_id}/control {action}`：confirm/replan/stop，仅当前
  状态允许的操作；confirm 是本地 demo 操作者入口，不提供企业身份认证。
- `POST /api/intent-demo/runs/{run_id}/actions/{request_id}/execute`：只允许已执行的
  ALLOW 请求进行 Core 幂等重试；暂停/未知请求 409；没有任意 tool/target/payload API。
- `POST /api/intent-demo/reset`：锁内删除带归属标记的 demo 子目录、失效全部 run。

默认 demo 开关关闭（404）；显式开启后仅部署在 loopback、单进程、单 worker。
进程重启丢失控制对象，磁盘事件仍保留；按 reset 清理后重新回放。正式检测器的
schema、确认身份、超时、不可信 Memory 隔离和纠偏计划校验由 1/3 号接入，
不得把这份演示提案宣称为已经完成团队冻结或生产接口验收。
