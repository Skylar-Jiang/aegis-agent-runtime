# 3 号意图检测模块交接

本模块在 `aegis-intent-dev` 的 `ebf4fb5` 基础上实现，负责单步评分、行为序列与来源分析、结构化理由、离线策略候选和资源测量。代码位于 `backend/src/ra_agent/intent/detection/`，执行工具、审批、任务契约发布和实际回滚由 1 号运行时负责。

## 当前交付边界

本次检查的分支没有 `backend/src/ra_agent/intent/runtime.py`，也没有 1 号的 `IntentCheckContext` 和 `DecisionResult` 定义。因此提供可运行的独立内核及泛型异步桥接类，没有自行创建同名公共 Schema。真实冻结类型的字段适配、运行时注入及真实副作用拦截验收，须在收到 1 号所在分支或文件后补齐。

默认评分器明确标记为 `lexical-baseline-v1`。它是低置信词汇基线，不是已通过独立数据验证的语义模型。模块同时实现了中英文联合特征哈希线性分类器的离线训练、制品校验与加载；没有附带未经 2 号复核数据训练出的“正式模型”，没有编造准确率或通用防御效果。

## 文件与职责

| 文件 | 内容 |
| --- | --- |
| `provider.py` | `IntentDetector.evaluate(context, /)` 异步入口、九字段输出、结果解释、待评估请求上限 |
| `engine.py` | 硬约束优先、单步信号、序列累计、来源链、处置与可信恢复上下文 |
| `_types.py` | 仅供检测器内部使用的不可变快照和分析结果，不属于公共接口 |
| `semantic.py` | 词汇基线、稀疏线性模型、离线训练与有界缓存 |
| `policy.py` | 有界配置、验证集阈值候选、回归记录、具名批准、离线发布与回退 |
| `scripts/intent_detector.py` | 训练、前缀回放与检测组件性能测量 |

## 对接方式

`IntentDetector[IntentCheckContext, DecisionResult]` 通过两个依赖对接：`context_adapter` 将真实上下文转为内部 `DetectionInput`，`result_factory` 使用 1 号实际的 `DecisionResult` 构造器完成校验。这两处由收到的冻结 SDK 决定；3 号随后补齐具体适配代码，算法不进入 `runtime.py`。

以下为接线示意，两个名称来自实际集成代码，不是本模块捏造的公共类：

```python
from ra_agent.intent.detection import IntentDetector, IntentDetectorCore

detector = IntentDetector(
    context_adapter=map_runtime_context,
    result_factory=DecisionResult,
    core=IntentDetectorCore(),
    max_pending_evaluations=8,
)
result = await detector.evaluate(context)
```

结果只构造已约定的九个字段：`decision_id`、`decision`、`risk_score`、`trigger_dimensions`、`evidence_refs`、`reason_code`、`policy_version`、`detector_version`、`expires_at`。处置值为 `CONTINUE`、`REPLAN`、`REQUEST_CONFIRMATION`、`BLOCK`、`SAFE_TERMINATE`。详细中文理由保存在内部分析记录，可由 `explain(decision_id)` 获取，不向冻结结果添加字段。持久化审计仍由运行时完成。

适配器必须交付可信事实：

- 已确认的任务、契约版本、原始依据引用、明确动作与资源约束。`None` 表示本层没有提供该类限制，空集合表示没有获准项目，二者不能互换。
- 规范化工具动作、全部目标、参数、实际副作用类型和接收方。`additional_targets` 也参与范围检查，不能只映射首个目标。
- 当前候选之前的实际执行记录，区分成功、失败、已核验进展与未知进展。`verified_progress=None` 表示未知，不能按无进展累计。
- 来源类别、原始引用和记忆父来源。工具正文自称“用户授权”或“系统错误”均不产生权限或可信状态。
- 由运行时提供的已用纠偏次数、重复恢复状态和预算耗尽标志；检测调用次数不会被当作真实纠偏次数。

授权、支持与进展标记严格验证布尔类型。模型异常、关键输入截断、来源缺失或成环不会隐式放行。`DetectorBusyError`、适配失败和真实结果模型校验失败向运行时传播，运行时须暂停并记录错误，不能将异常当作 `CONTINUE`。返回 `None` 的结果工厂会被拒绝。

## 判定与资源控制

硬约束检查独立于软阈值和消融开关。单步层联合目标、子目标、工具、动作、目标资源与参数编码；低文字重叠不直接产生阻断。序列层只消费同任务、同契约版本的过去事件，检查持续资源扩张、影响升级、重复失败和明确无进展。最近窗口按步骤选取，与调用方容器排列无关；重复事件不重复累计，补齐早期事件时重建窗口，回放较早前缀时不会继承未来累计状态。

来源层只追踪候选动作实际引用的资料及其父来源。指令性文本属于关联线索，不能直接作为因果证明，也不会改变授权。正常报告引用攻击文本可继续执行。恢复上下文只携带可信目标、约束、已核验成果引用及需要排除的来源引用，不将污染正文重新作为 Planner 指令。

默认保留最多 64 个任务状态、每任务 12 个事件、128 条语义缓存和128条结果解释；语义缓存另有2048条硬上限。批准策略缩小语义缓存后会立即淘汰多余条目。自定义评分器须自行声明和满足资源上限。默认最多8个待评估评分请求，忙时显式拒绝；取消等待不会提前释放仍在运行的评分线程名额。线程不能通过协程取消强制终止，第三方评分器永久挂起时需要运行时监督恢复。

## 模型和离线策略

当前默认阈值属于开发配置，尚未用2号的独立验证数据校准。风险分数和线性模型置信指标都是内部软信号，不是已经校准的事件概率。

训练输入须显式声明 `split="train"`，每条记录也须属于训练集。训练字段为 `goal`、`subgoal`、`tool`、`action`、`target`、`parameters_text`，标签0表示对齐、1表示偏移。验证集和测试集记录会被训练函数拒绝。模型包含编码版本、字段长度、权重、训练规模与摘要，加载时拒绝坏索引、重复JSON键、NaN、无穷值和过大的制品。CLI另保存训练输入、源码及模型摘要的manifest。

`calibrate_policy()`完成的是单个分量分数的验证集阈值候选选择，并核验调用方提供的独立硬约束回归记录。它不等价于完整检测器的五种处置重放：语义置信条件、可信支持和累计序列均会影响最终决定。候选评估、人工批准、发布及回退均在离线`PolicyRegistry`中记录；审批绑定数据与策略摘要，更新评估会使旧批准失效，回退仅允许曾发布版本。

正式启用候选前，1/2/3号还需将模型、特征、检测代码、数据版本和分数定义绑定到完整评估凭据，使用候选策略重放独立验证轨迹并核验任务结果和副作用，再接入1号发布入口。该模块不认证真实评审人，不把用户点击继续当安全标签，也不自动运行环境回归或发布线上策略。JSON摘要用于一致性核验，真实来源认证与受保护存储由运行时提供。

## 复现命令

以下命令在仓库根目录执行，使用已创建的Python 3.11环境。pytest临时目录指定在仓库`.runtime`内，避免本机系统临时目录权限问题；重跑时选用新的临时目录名。

```powershell
& 'backend\.venv\Scripts\python.exe' -m pytest tests/unit/intent -q --basetemp '.runtime\intent-tests-local-01'
& 'backend\.venv\Scripts\python.exe' -m ruff check backend/src/ra_agent/intent/detection scripts/intent_detector.py tests/unit/intent
& 'backend\.venv\Scripts\python.exe' -m pyright --project backend/pyproject.toml backend/src/ra_agent/intent/detection scripts/intent_detector.py tests/unit/intent
& 'backend\.venv\Scripts\python.exe' scripts/intent_detector.py replay --input tests/fixtures/intent/detector_replay_smoke.json --mode full --output '.runtime\intent-replay.json'
& 'backend\.venv\Scripts\python.exe' scripts/intent_detector.py benchmark --steps 2000 --tasks 128 --workers 4 --max-tasks 32 --output '.runtime\intent-benchmark.json'
& 'backend\.venv\Scripts\python.exe' scripts/intent_detector.py train --input TRAIN.json --output MODEL.json
```

回放支持`rules`、`single`、`full`、`no-sources`，`--model MODEL.json`可指定模型。当前9条合成用例共13步，覆盖正常任务、报告引用外部指令、渐进目标扩张、伪造工具反馈、只读硬约束、正常重试、工具替换、合法目标变更和恢复预算耗尽。标签与预期值只在推理后用于离线比较，不进入在线输入。用例不是2号正式数据集，观察事实来自fixture，不表示真实执行或实际副作用拦截。

性能报告区分模型加载、冷首调用、单任务及并发请求延迟、Python分配峰值和Windows进程峰值工作集。测量包含tracemalloc插桩开销；原始结果及源码SHA-256清单保存在输出JSON中。未加载正式语义模型时仅能报告基线配置开销，不能外推正式模型或完整原型的500 MB达标结论。

## 本地验证记录

2026-10-07本地验证记录：新增模块测试与既有意图边界、公共契约、用户指定的检查脚本测试合计220项通过，Ruff和Pyright通过。四种回放模式各运行9个合成案例、13步，10项有明确预期的处置均匹配对应模式的预期。

词汇基线配置下，单任务2000步中位耗时0.781 ms、P95为0.938 ms；128个任务、4个线程共2000次调用，中位耗时2.946 ms、P95为4.362 ms。Windows进程峰值工作集32,030,720字节，Python分配峰值1,289,902字节，包含测量脚本与tracemalloc开销。保留32个任务、384个历史事件、128条语义缓存，均未超过本次配置上限。该结果不包含正式语义模型、真实工具执行链或完整原型。

原始记录为`.runtime/member3-benchmark-20261007.json`及`.runtime/member3-replay-*-20261007.json`，附实际工作树源码摘要。既有密码契约测试使用本机Git提供的OpenSSL，通过进程环境变量`AEGIS_OPENSSL`指定；未更改项目密码实现或全局系统配置。

## 仍需联调的输入

1. 1号实际`IntentCheckContext`、`DecisionResult`、相关枚举和fixture所在分支或本地目录。当前的桥接测试使用明确标注的替身，不能代替这些冻结定义。
2. 2号复核后的训练、验证、独立测试数据及模板分组。收到后训练并选择模型、校准阈值、重放对照及消融，冻结正式配置。
3. 1号真实工具执行链中的阻断、确认、重规划和安全终止结果，以及完整原型资源测量。检测模块只提供处置建议，不自行执行或撤销工具。

本次没有修改`runtime.py`或公共Schema，也没有恢复用户之前覆盖的`tests/unit/test_check_script.py`。
