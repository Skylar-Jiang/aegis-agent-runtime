# Aegis Runtime Base v0.4 交付与验收说明

交付日期：2026-09-11

## 结论

本次改造已经把原先偏向“单次 Task + 手工 TaskContract”的 Runtime，升级成可持续使用的 Conversation Runtime Base。它仍不是完整的 Aegis Core 意图偏移算法成品，但已经可以作为后续竞赛算法、Agent Adapter 或插件接入层的稳定工程底座。

最关键的变化是：前端的自然语言 Conversation 会创建真实 TaskRun；TaskRun 使用持久化 SecurityProfile 自动生成当次 TaskContract 快照；工具请求必须通过 Registry JSON Schema、安全策略和 Runtime 执行链；需要审批时会暂停，并在批准后恢复同一个请求。

## 已完成的能力

- 持久化、版本化的 SecurityProfile；普通权限跨消息、跨对话和进程重启生效。
- Allowed actions、资源范围和后台有效权限统一，不再保留用户不可见的默认 `forbidden_actions` 冲突。
- Permission、Policy 与 Approval 分离；旧 reconfirmation 字段仅为历史兼容，不再把副作用请求直接变成无法恢复的 `BLOCKED`。
- 每个生产工具由 Registry 暴露并执行同一份 JSON Schema，Planner 不再靠提示词猜参数。
- Conversation、Message、TaskRun 三层结构；后续消息获得最近原文、有界摘要和当前安全快照。
- Profile 风险规则可触发真实 `WAITING_APPROVAL`；Grant/Deny 作用于原 TaskRun 和原请求。
- 新增智能体会话首页、长期安全设置页，并保留 Tasks、Runtime、Approvals、Audit 与 Experiments 事实视图。
- SQLite 迁移保存 Profile 版本、Conversation 消息和 TaskRun 的有效权限快照。
- Windows 一键启动及不依赖 LLM Key 的一键验收脚本。

## 验收结果

| 检查 | 结果 |
| --- | --- |
| Ruff 格式与规则检查 | PASS |
| Pyright（后端源码及新增测试） | 0 errors / 0 warnings |
| 后端主链相关回归 | 440 passed |
| B1–B11 确定性 Runtime 实验 | 11 / 11 PASS |
| 前端 TypeScript typecheck | PASS |
| 前端 Vitest | 9 files / 25 tests PASS |
| 前端 production build | PASS |

参考机器可读结果位于项目内 `docs/reference/runtime-base-validation.json`。在 Windows 解压后双击 `RUN_BASE_EXPERIMENTS.bat`，会在本机重新生成 `.runtime/base-validation.json`。

原压缩包还有 9 个旧实验重建测试依赖未随交付包提供的 Git 提交元数据或历史 raw 目录；这些不属于本次主链回归，不能把它们解释成 Runtime 功能失败。Final evidence 只读材料仍保留在交付包中。

## Windows 使用

1. 解压 ZIP，双击 `START_RUNTIME_BASE.bat`。
2. 首次运行会从 `.env.example` 复制出 `.env` 并打开；填写 `LLM_BASE_URL`、`LLM_API_KEY`、`PLANNER_MODEL`。
3. 再次运行脚本，在“安全设置”保存长期权限，然后回到“智能体”进行连续对话。
4. 不接大模型时，双击 `RUN_BASE_EXPERIMENTS.bat` 验证 Runtime 基底。

## 当前边界

- 尚未实现竞赛共通线的意图偏移评分、行为序列模型、在线阈值学习和多模态检测。
- 尚未实现 MCP/浏览器插件适配器、企业身份认证、多租户、密钥托管和分布式任务队列。
- 当前单个 Conversation TaskRun 内的工具调用按顺序执行；独立 TaskGraph 入口仍负责依赖调度演示。
- 真实 Agent 的最终表现仍受所接大模型的规划质量影响；确定性验收只证明 Runtime 控制链真实有效。

详细设计、接口和实验解释见压缩包中的 `README.md`、`docs/CONVERSATION_RUNTIME_UPGRADE.md` 与 `docs/RUNTIME_BASE_VALIDATION.md`。
