# Aegis Runtime Base 验证说明

## 目的

这套验证回答的是“主链是否真的工作”，而不是“前端按钮能否展示一个预写 Demo”。所有 B1–B11 场景使用确定性 Planner、真实 Runtime 组件或隔离临时目录，不访问外部 LLM，也不操作用户工作目录。

## 一键运行

在 Windows 项目根目录双击 `RUN_BASE_EXPERIMENTS.bat`。第一次运行需要联网安装锁文件指定的依赖。脚本任何一步失败都会返回非零退出码，并停在第一个失败位置。

后端机器可读报告：`.runtime/base-validation.json`。

压缩包中的 `docs/reference/runtime-base-validation.json` 是本次交付前实际运行生成的参考结果；十一项均为 `PASS`。在你的 Windows 电脑上仍应重新运行一次，以验证本机环境。

## 场景与证据

| 场景 | 要证明的事实 | 关键断言 |
| --- | --- | --- |
| B1 Agent 审批恢复 | 普通 Agent 的批准不是空操作 | WAITING 结果被同一 request 的 COMMITTED 结果替换；后续步骤继续执行 |
| B2 API Runtime 事实 | 工作台不依赖浏览器内存伪造历史 | `/api/tasks`、`/steps`、`/graph` 返回同一 task_id 的服务端事实 |
| B3 任务持久化 | 刷新/重新构造服务后数据仍存在 | SQLite 中的状态、contract 与 Agent 快照可重新读取 |
| B4 受控写入 | Agent 写文件确实经过安全 Runtime | 文件只在 sandbox/check/commit 完成后出现在 workspace |
| B5 危险 Shell | 阻断发生在副作用之前 | `rm -rf` 类命令返回 BLOCKED，受限进程未启动 |
| B6 高风险批准 | 审批能够恢复真实执行 | delete 等可逆操作在 Grant 后带 checkpoint 进入 commit |
| B7 Profile/Conversation 持久化 | 长期设置不是浏览器临时状态 | Profile 新版本和 Conversation 在进程重启后仍可读取 |
| B8 连续上下文 | 第二句话不是一个失忆的新 Agent | 新 TaskRun 得到上一轮用户消息与 Agent 最终回复 |
| B9 Tool Schema | Planner 不再猜测工具参数 | 所有生产工具提供并执行 Registry 所属 JSON Schema |
| B10 Conversation 写入 | 连续对话入口不是只保存聊天文字 | TaskRun 经过 checkpoint、deep check、commit 后才在 Workspace 生成真实文件 |
| B11 Profile 审批恢复 | “始终审批”不是直接阻断或前端假按钮 | 文件在批准前不存在，批准后由同一个 TaskRun 完成提交 |

前端部分另外执行 TypeScript typecheck、Vitest 和 production build，避免“后端修好了但按钮/类型仍断开”。

## 如何判断结果

- `summary.status = PASS`：十一个后端场景全部通过。
- 某个 case 为 `FAIL`：查看该 case 的 `output_tail`，它包含 pytest 的最后错误信息。
- 后端通过而批处理仍失败：通常是 Node/pnpm 版本、依赖下载或前端类型/测试错误，查看批处理窗口中第一个失败命令。

## 与真实大模型实验的区别

确定性验收只证明 Runtime Base 的工程语义：Planner 提出的请求一定经过安全链、审批能恢复、危险动作能阻断、状态可查询和持久化、连续对话上下文能够传递。它不证明某个大模型能稳定规划复杂任务，也不构成“意图偏移检测算法”的效果评估。

接入真实模型后，建议再做 20–50 次重复任务测试，分别记录：任务成功率、平均规划轮数、误阻断率、审批率、执行时延、失败类型和最终副作用。模型实验必须与 B1–B6 的确定性工程验收分开报告，否则很难判断问题来自模型还是 Runtime。

## 当前已知边界

- 单个 Agent 内的工具调用仍是顺序执行；TaskGraph 的并行/依赖调度是独立入口。
- 等待审批的 Agent 可在服务重启后从 SQLite 快照恢复；进程重启瞬间正在执行的任务标记为 `INTERRUPTED`，不会自动重放副作用。
- 当前 Planner Adapter 是一个特定的兼容式 HTTP 客户端，还不是通用 Provider/MCP 插件系统。
- 身份认证、租户隔离和审批人可信身份仍需在企业化阶段实现。
- Aegis Core 所需的意图偏移评分、长期轨迹分析和在线策略学习不属于本 Base 验收范围。
