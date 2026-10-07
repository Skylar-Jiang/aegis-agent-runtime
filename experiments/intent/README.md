# 2 号数据与实验工作包

本工作包以 `aegis-intent-dev` 的 `ebf4fb52547a2689f7ea72eb6fba54d57a9a04d1` 为起点。
交付范围为 I1/I6/I7/I9 的数据、评测、证据及材料，以及 I2/I5 的标注和复核接口。
本次仅处理 aegis-agent-runtime，另一个项目不在范围内。分工 DOCX 是需求参考；其中分支名称、内部日期和未核实指标不覆盖用户指定的起始分支，也不等于官方通知。

## 当前交付状态（2026-10-07 更新）

真实 Core 已接入报告条件检查、重复读取检测、持久化安全终止及用户确认的有界报告恢复。真实 SM2 和工具适配器的五组对照、浏览器验收、资源测量、PPT 与原始视频已生成。具体通过数和运行目录见 [本地验收报告](LOCAL_ACCEPTANCE.md)。

双人独立复核、真实反馈批准、非作者复跑、官方资源与材料口径、团队全项目验收仍待真实成员完成，操作见 [人工交接](HUMAN_HANDOFF.md)。当前不宣称 I2—I9 全部完成或通用语义模型/任意任务 Planner 已实现。

200 条新候选是已见四个任务组的参数变体，仍待复核，不构成独立未见正式测试。策略更新与回滚工具已实现且经模拟门禁测试；尚未用真实成员批准记录发布新策略。

此前已提交的离线参考试验仍保留，下面是历史工作包说明，不能将其参考恢复结果与真实网关恢复混为一谈。

## 已执行的内容

- 40 条合成完整轨迹、4 个独立任务组；开发 20、验证 10、测试 10 条。
- 现有 `RuleBasedIntentBoundaryGuard` 与 `SafePathResolver` 的实际调用。
- 6 种对照配置，各跑 40 条，共 240 次轨迹运行；原始决策和真实临时文件副作用均保存。
- 10 项评测有效性测试；标签缺失、跨集合泄漏、未来信息、合法目标变更、副作用先于阻断、恢复失败和证据篡改等检查。
- 实验进程冷启动及 200 次轨迹重复运行资源测量。
- 全流程、标注说明、实验报告、复核表和展示讲稿。

当前是合成试验包。参考单步/序列/来源规则和预录后续步骤继续执行是实验支架；真实生产检测器、Planner 纠偏、完整网关联调、双人复核、页面录屏及正式比赛验收仍需团队完成。不要把本包的脚本结果写成 I2—I9 全部实现。

## 从拉取到复现

```powershell
git clone --branch aegis-intent-dev --single-branch https://github.com/Skylar-Jiang/aegis-agent-runtime.git
Set-Location aegis-agent-runtime
git rev-parse HEAD
git status --short
python --version
python -c "import pydantic; print(pydantic.__version__)"
python -m unittest experiments.intent.test_pilot -v
python -m experiments.intent.evaluate
python -m experiments.intent.evaluate --splits test
python -m experiments.intent.resources
python -m experiments.intent.materials
```

实验最小环境为 Python 3.11 和 Pydantic 2；本包不依赖 pytest、LLM API 或后端服务启动。代码和冻结数据已随包提供，不应为复现重新生成数据或重写锁文件。完整服务环境按 backend/pyproject.toml 和 uv.lock 安装并记录，不用最小环境替代产品安装验收。

`python -m experiments.intent.dataset` 只供编辑新数据版本使用。`python -m experiments.intent.freeze` 用独占创建方式生成锁；已有锁不会覆盖。测试前检查数据/检测脚本/边界检查/文件解析器摘要和阈值。修改后应创建新版本并保留新的未见任务组；不能删除旧锁后反复调参并称为正式测试。

运行目录按 UTC 时间命名，用户日程按北京时间。每次执行建立新目录，不覆盖旧结果。默认仅开发和验证；测试需要显式 `--splits test`。`pilot-lock.json` 的阈值 3 为预设参考规则，没有“最优阈值”主张。

## 文件入口

| 文件 | 用途 |
|---|---|
| `data/pilot.jsonl` | 当前 v2 数据及每步标签、许可范围、首次偏移、完成条件 |
| `dataset.py` | 数据生成、分组/标签/可信变更校验 |
| `evaluate.py` | 逐步离线评分、独立执行记录、汇总和外接检测器 |
| `pilot-lock.json` | 当前数据、核心脚本和阈值冻结证据 |
| `results/*/raw.jsonl` | 每次运行每条轨迹的原始判断、触发、版本、文件摘要和错误 |
| `results/*/summary.json` | 按配置、集合及风险类型的分子/分母/不适用项 |
| `results/*/manifest.json` | 提交 SHA、未提交变更、环境、源文件摘要和限制 |
| `results/*/checksums.json` | 输出完整性摘要；用 evidence.py 验证，不是数字签名 |
| `resource-pilot.json` | 实验 worker 峰值内存；不代表完整产品占用 |
| `materials/` | 当前报告、复核清单、反馈表和结果索引 |
| `WORKFLOW.md` | 各阶段步骤、验收门和团队交接 |
| `ANNOTATION.md` | 标签和指标定义、独立复核要求 |
| `DEMO_AND_SOLUTION.md` | 演示脚本、解决方案及答辩稿 |

早期 `pilot-v1-superseded.jsonl`、旧锁和旧结果保留供审计。v1 任务分组不足，已废弃；v2 按任务整体隔离。`materials/experiment-report.md` 只引用当前源摘要匹配的运行，不混用旧结果。

## 外接 3 号检测器

```powershell
python -m experiments.intent.evaluate --adapter your_module:create_detector --splits dev validation
```

工厂接收配置，返回同步可调用对象 `detector(intent, current, history)`。输入为契约目标/版本/完成条件、当前动作参数及最多 8 个历史动作；不含标签、首次偏移、后续步骤和最终完成结果。输出必须包含布尔 `detected`，可带 `trigger_dimensions/evidence_refs/detector_version`。硬约束结果始终与软判断取 OR，不能被检测器抵消。

这只是离线检测接入，尚不支持真实运行时纠偏或可信契约发布。接入生产模块后，增加其源/模型/策略摘要、真实事件和网关副作用，建立独立测试锁；当前脚本禁止直接用外接适配器消费本包的测试集。I2 自动契约抽取应作为独立实验，不能用人工契约结果冒充端到端抽取效果。
