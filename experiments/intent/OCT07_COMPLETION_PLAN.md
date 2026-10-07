# 10 月 7 日必做项补齐与后续全量验收计划

编制日期：2026-10-07（北京时间）。以下保留原补齐计划供追溯。实施状态已更新至 LOCAL_ACCEPTANCE.md；人工操作请按 HUMAN_HANDOFF.md 执行，原文中的“尚未执行”不代表当前状态。

## 验收范围

当天硬门：三类攻击、两个正常对照、一次真实工具执行前拦截、一次纠偏或安全终止，以及前端事件链展示。选择真实安全终止可以满足当天的“纠偏或安全终止”，不必等待完整自动纠偏；完整纠偏仍是后续 I4 任务。

2 号当天职责：不同任务/来源轨迹、序列风险时间线、误报及正常完成分析，展示触发维度、上游引用与当前处置，保存来源和模板组。关键标签还需要真实成员复核。

正式 PPT/视频、数百条轨迹目标、策略更新、全系统内存和平台提交属于后续完整交付，不能说这些都是 10 月 7 日硬要求，也不能宣称已完成。

## 0. 固定交付分支和当前证据

负责人：2 号组织，1 号确认集成分支。建议耗时：15–30 分钟。

仓库：C:\Users\Alghy\Desktop\Aegis大创\aegis-agent-runtime。当前基底为 aegis-intent-dev；新增 experiments/intent 尚未提交。先保存当前 v2 数据、锁和 240 次结果，不覆盖旧结果。

```powershell
Set-Location 'C:\Users\Alghy\Desktop\Aegis大创\aegis-agent-runtime'
git branch --show-current
git rev-parse HEAD
git status --short
git switch -c feature/member2-intent-oct07-evidence
```

以上 feature 名为建议，执行前按团队分支约定确认。若已有同名分支，不重置，检查其用途。1/3/4 号分别提交各自模块，经审阅进入集成分支；不要互相复制未版本化文件。2 号保留当前实验包，只在锁定的新集成版本上增加端到端结果。

完成证据：基底 SHA、feature 分支、工作区变化清单、实验目录和文件摘要。

## 1. 启动真实 Core 与前端

负责人：1 号；环境安装与启动可由 Codex 执行。建议耗时：30–90 分钟，依赖下载/环境故障另计。

检查 Python 3.11、uv、Node 版本和 pnpm。frontend/package.json 要求 Node >=24.14.0 且 <25，pnpm 10.12.4。使用 backend/uv.lock 和 frontend/pnpm-lock.yaml，不临时升级依赖。

```powershell
python --version
python -m uv --version
node --version
corepack pnpm --version
python -m uv sync --project backend --group dev --locked
corepack pnpm --dir frontend install --frozen-lockfile
```

若 uv/Corepack/Node 缺失，先补齐环境，不改锁文件或跳过类型检查。把完整错误保存到启动记录，由开发执行者处理，用户无需自行猜安装包版本。

终端 A（保持运行）：

```powershell
Set-Location 'C:\Users\Alghy\Desktop\Aegis大创\aegis-agent-runtime'
python -m uv run --project backend --no-editable python scripts/start_core.py --port 8000
```

脚本会准备真实 SM2 开发密钥，启动前验证签名/验签；默认关闭 demo fixture，运行目录为 .runtime/core-demo。若提示 OpenSSL 不可用，在真实存在的 OpenSSL 3 路径上设置 AEGIS_OPENSSL 后重新启动；不能用假的签名提供者绕过。

终端 B（保持运行）：

```powershell
Set-Location 'C:\Users\Alghy\Desktop\Aegis大创\aegis-agent-runtime'
corepack pnpm --dir frontend exec vite --host 127.0.0.1 --port 5173
```

终端 C：

```powershell
Invoke-RestMethod 'http://127.0.0.1:8000/api/v1/health'
```

浏览器打开 http://127.0.0.1:5173/core，选择“Core 工作台”，不选择“Mock 实验室 · 模拟数据”。预检目标是能创建任务、确认契约、调用允许工具并看到真实事件。启动成功本身不证明 Intent 接入。

## 2. 冻结最小 Intent 接口

负责人：1 号与 3 号提供，2/4 号核对。建议耗时：30–60 分钟。

保留现有 TaskContractV2：goals、completion_criteria、allowed/denied、契约版本和确认状态。增加 Intent 检测记录时采用显式模式版本，不直接破坏既有对象和旧事件。

输入必须明确：task_id/request_id、contract_id/version、目标和完成条件、当前规范化动作/参数、有界历史、上游 source_refs、可信来源类型。只有可信用户入口能更改目标或扩大许可。

输出明确：decision、risk_score、trigger_dimensions、evidence_refs、reason_code、policy_version、detector_version，以及契约版本。现有网关决策使用 ALLOW/DENY/REQUIRE_CONFIRMATION/REQUIRE_REPLAN；为新增原因码提供明确映射，不把全部 Intent 拒绝伪装为权限越界。

在线检测不能收到人工 risk 标签、gold.required_report、首次偏移位置、未来结果或最终任务完成状态。完成条件只能来自可信契约，不从测试答案生成运行时目标。

完成证据：模式定义、正常/偏移 fixture、字段映射、参与成员的真实审阅记录。

## 3. 接入最小真实检测器

负责人：3 号。建议耗时：2–4 小时，取决于已有实现；允许使用可解释规则起步，不要求当天训练模型。

1. 硬边界仍由原网关检查；软分数不能覆盖硬拒绝。
2. 单步检查当前写入是否服务于可信目标/完成条件，覆盖权限内广告替换和失败后伪造报告。
3. 序列检查有界窗口内相同/无效动作的持续重复；加入合法重试和必要多步查询对照，避免“工具变了就拦截”。
4. 来源记录与风险维度关联，不能仅因 external_document 来源就拒绝合法资料引用。
5. 输出每步证据和版本，模型不可用/检测超时的高风险动作不得放行。
6. 当前参考规则只能作为起点；经运行时适配、异常处理、状态持久化和正常对照验证后，才可标记为真实接入规则版。

完成证据：生产模块测试、权限合法但目标偏离样例、合法重试对照、在线输入无标签泄漏。

## 4. 网关执行前阻断与真实安全终止

负责人：1 号接入，3 号协作。建议耗时：2–4 小时；这是当天验收关键路径。

代码入口：backend/src/ra_agent/gateway/gateway.py；应用装配在 backend/src/ra_agent/main.py 的 ToolGateway 构造处；实际执行经 gateway/runtime_bridge.py。需先确认所有演示调用走该网关，不只在 experiments/intent/evaluate.py 里加判断。

实施顺序：

1. 网关评估阶段调用检测器，保存决策、契约/策略/检测器版本和证据引用。
2. execute 阶段在调用真实执行桥之前重新校验当前权限、契约/策略版本和请求参数，防止旧 ALLOW 缓存继续执行。
3. 检测发现风险后，阻断待执行动作并写入事件。检测异常/超时的高风险动作同样停止。
4. 采用已有生命周期终止能力或新增受控终止状态，确保后续/依赖动作不可恢复执行。按钮变灰不等于服务器终止。
5. 保存工具执行调用次数、实际文件前后状态、Memory/模拟端点计数；首次禁止副作用发生后不可用覆盖文件掩盖。
6. 测试终止后重新请求 execute/resume，确认失败且没有新增副作用。

当天最小验收可选择安全终止。若做纠偏：从可信状态提出原契约内新计划、重新检查、限制重试和预算；扩权须经新契约确认。预录正确下一步不算 Planner 纠偏完成。

完成证据：同一 task/request 的检测、拒绝、终止、后续执行被拒绝事件，真实文件未写入且执行计数为 0。

## 5. 真实事件与风险时间线

负责人：4 号实现，2 号定义展示字段并核对证据。建议耗时：1–3 小时。

可复用 frontend/src/features/core/RealWorkbench.tsx 已有“行为时间线”“选中事件”“导出证据”区域。增加 Intent 维度展示，优先复用 backend/src/ra_agent/api/core_views.py 和 core_v1.py 的真实任务事件接口。

每条显示：步骤序号、工具/目标、来源及上游引用、契约版本、风险分数、触发维度、处置、实际执行状态。加入以步骤为横轴的风险变化图；没有风险分数的旧事件标为未提供，不画成 0 分。

点击风险点定位原事件；同一事件引用对应原始决策和副作用记录。刷新页面能重建时间线，不依赖浏览器内存或 mock JSON。阻断按钮状态只是展示，服务器必须独立阻断。

完成证据：三类攻击和两个正常任务的页面截图；刷新后事件仍在；图表与 API/文件状态一致。

## 6. 固定验收用例和真实集成重跑

负责人：2 号组织，1/3/4 号分别复核执行、检测与界面。建议耗时：1–2 小时。

| 用例 | 起始数据参考 | 必须观察 |
|---|---|---|
| 正常资料整理 | normal-0-0 | 必要查询被允许，报告满足可信完成条件 |
| 正常替换/失败回退 | normal-1-0 / normal-3-0 | 合理替换或回退不被误当攻击 |
| 检索诱导 | retrieval-0-0 | 同样允许的报告路径写无关内容被 Intent 拦截；原文件不改变 |
| 记忆渐进偏移 | memory-0-0 | 有界序列触发，展示首次偏移到检测的实际步数 |
| 失败不当替代 | failure-0-0 | 先保留真实工具失败，再阻断伪造报告 |
| 真实安全终止 | 任选一个软偏移用例 | 服务器终止；后续 execute/resume 被拒绝 |
| 合法目标修改补充对照 | normal-2-0 | 可信确认产生新版本，新目标可正常执行 |

复用 case 内容需通过运行时请求、真实 TaskContract 和可信来源产生事件，不能直接向 API 注入 gold 标签作为检测输入。

在同工具、同网关、同任务权限下重跑边界基线、真实单步、真实联合检测及各消融。基线差异只限 Intent 层，不关闭 Core 硬防护。来源消融无增益仍如实报告。

正式接入会改变代码/配置：新建集成数据版本和测试锁，另留未见任务组。本包 test 任务已被查看，不可反复用于选阈值后仍称独立正式测试。

现有可执行校验：

```powershell
python -m uv run --project backend --no-editable python -m unittest experiments.intent.test_pilot -v
python -m uv run --project backend --no-editable python scripts/verify_runtime_base.py
python -m uv run --project backend --no-editable python scripts/verify_core_pr1.py
corepack pnpm --dir frontend typecheck
corepack pnpm --dir frontend build
```

新增网关联调测试和 Intent 浏览器测试须先实现，再运行对应入口。不要将旧 Core 验证脚本通过当作新增 Intent 端到端通过。

## 7. 人工操作 A：两名成员独立标注复核

这是无法由自动测试替代的人工项。2 号联系两名真实成员，优先 1/3/4 号中的非作者。两人先分别判断再比较，不能只复制作者答案。

1. 用 Excel 打开 experiments/intent/materials/review.csv。保留 CSV 格式，编码采用 UTF-8；不要改 case_id/template_group/split。
2. 同时打开 data/pilot.jsonl，按 case_id 查找该行；从 original_request、contract、steps 和可信变更判断当前许可和目标。
3. reviewer_1/reviewer_2 填真实姓名或团队标识；verdict 填“正常”或“首次偏移=步号；理由=…”；步号从 0 开始。
4. 至少复核全部攻击关键步、两个正常对照、合法目标变更；首批只有 40 条，建议完整复核。
5. 意见不一致时把两个意见都填进 disagreement；由真实第三人写 resolution 和证据，不清空分歧。
6. evidence_ref 填 case_id 与对应事件/原始结果位置，reviewed_at 写北京时间及 +08:00 时区。
7. 单人/未裁决保持 pending_independent_review；两人完成并裁决后由数据维护者升级状态，不批量假设 reviewed。
8. 标签修改需形成新数据版本、记录原因、重跑受影响实验。不能直接修改已冻结测试集却沿用旧锁和旧成绩。

## 8. 人工操作 B：独立页面与文件验收

此步骤必须等第 4/5 步真实接入完成后执行。用户无需先在未接入的页面寻找尚不存在的 Intent 功能。

1. 启动两终端服务，浏览器打开 /core，选“Core 工作台”。
2. 创建正常任务，审阅并确认契约；记录 task_id、contract_id/version。
3. 按集成演示脚本提交允许的调用，查看报告并核对设备、证据、建议；截图契约、时间线和完成状态。
4. 新建攻击任务，提交权限内偏移动作。观察 Intent 原因和停止状态；不要通过改安全配置或切换 Mock 让演示通过。
5. 在 PowerShell 对同一报告文件检查前后摘要：

```powershell
Get-FileHash -Algorithm SHA256 -LiteralPath '.runtime\core-demo\workspace\reports\report.txt'
```

若该用例输出另一个路径，使用该用例的实际路径。文件原先不存在时，先记录 Test-Path 结果；阻断后仍应不存在，不能把 Get-FileHash 的报错算作验收失败。

6. 终止后再次执行/恢复，确认服务器拒绝；刷新网页确认终止及风险事件仍存在。
7. “导出证据”中使用新的检查点 ID；保存导出包、公钥和可信检查点。私钥留在本地运行目录，不放入交付包。
8. 对三类攻击分别截图；页面任务 ID、原始结果 task_id 和文件检查路径一致。可以用 Win+Shift+S 截图保存，录屏按团队工具完成。
9. 独立复跑者在验收表填写实际环境、版本、时间、通过/失败和缺陷；按钮截图不替代文件/服务器验证。

## 9. 人工操作 C：正式通知与协作审阅

只有团队/学校/赛事平台能提供官方信息。记录正式截止、时区、材料格式、讲解/视频时长、500M 的 MB/MiB 和组件范围；找不到时保持待核实。四名成员姓名与可用工时填真实数据。

代码完成后由非作者审阅 PR。提交本工作包前：

```powershell
git status --short
git add experiments/intent
git diff --cached --stat
git diff --cached --check
git commit -m "Add member 2 intent evaluation data and evidence"
git push -u origin feature/member2-intent-oct07-evidence
```

若分支名另行约定，使用实际名称。只提交预期源码、合成数据、报告和证据；.runtime 私钥及真实业务资料不加入。GitHub 登录或权限确认由你本人完成，随后可由 Codex 创建对应 PR。合并前核对 1/3/4 号集成改动和目标分支，保存最终 SHA。

## 10. 后续全量交付

当天门通过后，再关闭下列完整项目项；这些不应阻塞“纠偏或安全终止”中的安全终止最小交付：

| 工作 | 责任 | 验收 |
|---|---|---|
| 自动契约抽取评测 | 1 号实现、2 号标注评测 | 字段正确/遗漏/扩权分别统计；人工契约与自动抽取分开 |
| 完整有界纠偏 | 1/3 号 | 可信快照、预算、新计划重检、一次真实安全完成与一次安全终止 |
| 反馈更新与回退 | 3 号、2 号组织真实复核 | 来源、离线评估、真实批准、回归、回退证据 |
| 新独立任务和数百条开发目标 | 2 号，4 号供场景 | 人工复核、严格分组、困难正常对照，不用相似改写凑数 |
| 正式基线和消融 | 2 号组织、1/3 号核验 | 同环境、版本锁定、失败保留、按任务组报告不确定性 |
| 全系统资源 | 各模块提供、2 号汇总 | 冷加载、稳态、真实长任务、并发、进程树、组件/全系统/远端分别记录 |
| 解决方案/PPT/视频 | 2 号统稿、4 号录制 | 页面和代码同版本，实测数据与失败边界一致，按正式格式输出 |
| 干净环境复现 | 非作者成员 | 无隐含依赖，正常和三类攻击可复跑，一键重置有效 |
| 平台提交 | 有权限成员/老师 | 真实提交回执；未提交不写已接收 |

## 关闭当天验收的最终检查表

- [ ] 三类攻击通过真实网关回放，保留原始事件及副作用。
- [ ] 两个正常对照满足任务完成条件，不仅生成文件。
- [ ] 至少一个权限内目标偏移被真实 Intent 判断在副作用前阻断。
- [ ] 至少一次真实安全终止或真实纠偏；终止后执行请求被服务器拒绝。
- [ ] 真实前端显示契约、动作、来源、风险维度、处置和执行结果；刷新后可重建。
- [ ] 2 号交付风险时间线、误报/正常完成分析、样本来源和模板组。
- [ ] 关键标签真实独立复核；缺陷和分歧如实登记。
- [ ] 代码、数据、策略、页面、导出和结果引用同一集成版本。
- [ ] 实验记录和代码提交可追溯，非作者复跑并签署实际验收结论。

预计关键链路需要约一个完整开发工作日，环境/接口问题可能延长。必须按真实通过证据登记完成时间；如果当晚未赶上，记录延期与剩余项，不补写 10 月 7 日已通过。
