# Aegis Core

Aegis Core 是面向工具型智能体的任务边界、受控调用与密码学证据工作台。在原 Runtime Base 之上，共通线已经接入 TaskContractV2、四层权限交集、真实工具网关、行为事件及 JCS / SM3 / SM2 审计。当前为本地单操作员实验系统。

## 密码学共通线：先跑这个入口

需要 Python 3.11、uv、Node.js `>=24.14.0 <25`、pnpm 10.12.4，以及可用的 OpenSSL 3。在仓库根目录打开两个终端：

```powershell
# 终端一：真实 Core 后端，不需要大模型 API Key
uv sync --project backend --group dev --frozen --no-editable
uv run --project backend --no-editable python scripts/start_core.py
```

```powershell
# 终端二：前端
cd frontend
corepack pnpm install --frozen-lockfile
corepack pnpm dev
```

打开 <http://127.0.0.1:5173>。首页应显示 `SM2 / SM3` 和实际公钥标识。创建任务 → 审阅并确认契约 → 填工具与资源 → 评估 → 需要时批准 → 执行 → 导出证据 → 核验原件和篡改副本。首次写文件可使用 `reports/my-first-run.txt`；再次使用 `create_file` 创建同名文件会被拒绝，可换新名字。

启动器把开发密钥、数据库、证据和真实文件集中放在 `.runtime/core-demo/`，保留已有密钥。文件副作用位于 `.runtime/core-demo/workspace/`。`fake` 模式不会被当成真实 SM2 验收；启动器选用 `sm2`，缺失或不匹配的密钥会阻止启动。

完整实验步骤、实际结果、截图、接口兼容性与回退说明见 [Core 优化与验证记录](docs/CORE_HARDENING_VALIDATION.md)。公共密码参数仍以 [crypto_audit_v1.md](docs/interfaces/crypto_audit_v1.md) 为准。

## 两条入口的边界

| 入口 | 当前用途 |
| --- | --- |
| 首页 / Core，`/api/v1/*` | 已确认任务、服务端权限、受控文件等适配器、SM2 签名证据、SM3 日志链和独立核验。手动选择工具即可实验。 |
| 连续对话 / Runtime，`/api/tasks` 等旧接口 | 原有大模型规划、风险策略、检查点、提交回滚及冲突调度。使用连续对话需要另配 LLM；不能把此入口声称为已接入 Core SM2 全链路。 |
| Mock 实验室 | 固定模拟场景，用于接口展示；页面明确标注模拟数据。 |

Core 网关内的身份绑定和权限检查不等于生产用户认证；本地签名密钥不等于 HSM 隔离。证据核验说明锚定区间的签名、关联与完整性，不自动证明业务结果正确，也不证明未锚定的最新尾部完整。Core 没有新增能力令牌、持有者证明或零知识证明，继续遵循本周 C1–C7 的方向。

## 原 Runtime Base 使用说明

Aegis Runtime Base 是一个面向工具型智能体的安全执行基底。它本身不是一个追求通用能力的 Agent，而是位于 Planner/大模型与真实工具之间的“受控外骨骼”：模型负责提出下一步工具调用，Runtime 负责判断这一步能不能做、如何隔离执行、何时需要人工审批，以及失败后如何回滚和审计。

Runtime Base v0.4 完成的主链路升级包括长期安全配置、版本化权限快照、完整工具 Schema 与连续对话。以下为保留的 Runtime 操作说明；意图漂移检测算法、在线阈值学习、多模态检测、MCP/插件协议和企业级身份认证仍未实现。

## 本版已经打通的主链路

```text
连续 Conversation
  → 用户消息 + 有界上下文摘要
  → Agent Planner（可接兼容接口的大模型）
  → SecurityProfile 版本 + 自动生成的 TaskContract 快照
  → ToolCallRequest（Registry JSON Schema 校验）
  → 意图边界 / 风险 / 策略 / 权限
  → 快速执行或隔离执行
  → 审批等待与精确恢复
  → 检查 / 提交 / 回滚
  → 持久化任务、步骤、Effect 和审计事实
  → Agent 继续规划或返回最终答案
```

与原始交付包相比，Runtime Base 修复了这些会让系统看起来像“只能跑 Demo”的断点：

- 普通 Agent 的审批不再只记录决定；Grant/Deny 后会恢复原请求，并继续剩余步骤。
- 任务、TaskContract、最新 Agent 状态与最终答案写入 SQLite，刷新页面后任务历史仍在。
- `GET /api/tasks/{task_id}/steps` 返回真实计划与执行结果，不再固定返回空数组。
- 普通 Agent 任务可直接进入统一 Runtime 执行视图，不必伪装成预写 TaskGraph Demo。
- 运行中的 Agent 每次产生计划或执行结果都会更新服务端快照。
- Tasks 页面读取服务端任务历史，并正确消费 `?task_id=` 深链接。
- Windows 启动脚本明确使用 `live-agent`，不会因为默认 `offline` 而出现“按钮能点但功能不执行”。
- 新增不依赖 LLM 密钥的确定性验收脚本，验证主链路而不是验证写死的演示文案。
- 新增持久化 `SecurityProfile`：权限跨任务、跨对话和进程重启生效，修改时产生新版本。
- 普通范围内的读写不再逐次确认；真正高风险操作统一进入 `WAITING_APPROVAL`。
- 用户权限不再同时出现“允许”和隐藏 `forbidden_actions`；系统策略阻断仍独立保留并显示原因。
- 每个生产工具由 Registry 提供完整 JSON Schema，Planner 和 Runtime 使用同一参数定义。
- 新增 Conversation / Message / TaskRun 三层结构，后续指令能引用上一轮结果，同时保留逐次审计。

## 现在能做什么

- 接收自然语言目标并让配置的大模型逐步规划工具调用。
- 对每一步执行 TaskContract、意图边界、风险、策略、权限和前后置检查。
- 受控读取、列目录、新建/写入/删除文件、受限 Shell、Memory、下载隔离和邮件 dry-run。
- 对需要确认的操作暂停，等待人工批准或拒绝，再恢复同一个请求。
- 记录审批、checkpoint、pending change、commit/rollback、Effect 与审计时间线。
- 显示普通 Agent 步骤或 TaskGraph 节点的统一 Runtime 事实视图。
- 重启后保留已完成、等待审批和中断任务；重启时未完成的 RUNNING 任务会标记为 `INTERRUPTED`，不会谎报成功。
- 在“安全设置”中一次配置允许工具、Workspace 范围、外发开关和附加审批规则；新 TaskRun 自动继承当前版本。
- 在同一个 Conversation 中连续工作；每个需要工具执行的消息仍生成独立 TaskRun。

## 仍然不是这些东西

- 不是训练好的“意图漂移检测模型”。当前 Intent Boundary 是规则与任务契约边界。
- 不是可自主解决任意任务的成熟通用 Agent。能力上限仍取决于 Planner、工具和 TaskContract。
- 不是 MCP Server、浏览器插件或某个平台的一键插件；这些应作为后续 Adapter 层接入。
- 不是企业生产成品：尚缺认证授权、租户隔离、密钥托管、分布式队列、完整可观测性和压测。
- 依赖中保留了 LangGraph，但当前主链状态机与恢复语义由项目自己的 `AgentRuntime` / `RuntimeScheduler` 实现，并未把 LangGraph 当作已经实现的能力。

## Windows 快速开始

环境要求：

- Windows 10/11
- Python 3.11（必须能使用 `py -3.11`）
- Node.js `>=24.14.0 <25`
- 首次安装依赖时可访问 Python 与 npm 包源
- 运行真实 Agent 时，需要一个兼容当前 Chat Completions 调用方式的大模型接口

在项目根目录确认前端包管理器版本时，先进入前端目录再执行：

```powershell
Set-Location frontend
corepack pnpm --version
Set-Location ..
```

1. 解压压缩包。
2. 双击 `START_RUNTIME_BASE.bat`。
3. 首次运行会生成并打开 `.env`；填写：

```dotenv
LLM_BASE_URL=https://你的模型服务地址
LLM_API_KEY=你的密钥
PLANNER_MODEL=模型名称
```

4. 再次双击 `START_RUNTIME_BASE.bat`。
5. 浏览器打开 `http://127.0.0.1:5173`。
6. 首先打开“安全设置”，确认长期权限。默认允许在隔离 Workspace 中列目录、读写文件和使用 Memory；删除、Shell、下载及外发默认不授权。
7. 回到“智能体”，在同一个 Conversation 中连续输入任务。普通操作不会每一步再次询问；高风险操作会显示“批准并继续”。

不要把真实 `.env`、`.runtime/` 或 API Key 发给他人。Runtime 的真实文件操作只应在 `.runtime/workspace/` 中进行。

## 不使用大模型的主链实验

双击：

```text
RUN_BASE_EXPERIMENTS.bat
```

脚本会安装锁定依赖、运行 11 个确定性后端实验，并完成前端 typecheck、Vitest 和 production build。实验不读取 LLM Key，结果写到：

```text
.runtime/base-validation.json
```

覆盖内容：

| 编号 | 验证对象 | 通过标准 |
| --- | --- | --- |
| B1 | Agent 审批恢复 | 恢复精确请求并继续后续步骤 |
| B2 | API 主链 | 服务端可返回任务历史、步骤和 Runtime 视图 |
| B3 | 持久化 | 重新创建存储适配器后任务快照仍存在 |
| B4 | 真实受控写入 | Agent 文件写入经过 Runtime 后提交 |
| B5 | 危险 Shell | 进程启动前阻断 |
| B6 | 高风险批准 | 可逆操作批准后经过受控提交 |
| B7 | Profile 与 Conversation 持久化 | 配置、版本和对话在重启后仍存在 |
| B8 | 连续上下文 | 后续 TaskRun 获得上一轮用户消息和 Agent 回复 |
| B9 | Tool Schema | 每个生产工具暴露并执行同一 JSON Schema |
| B10 | Conversation 真实写入 | Conversation TaskRun 经过 checkpoint、deep check 和 commit 后生成文件 |
| B11 | Profile 风险审批 | Profile 规则暂停副作用，批准后恢复同一个 TaskRun |

详细说明见 `docs/RUNTIME_BASE_VALIDATION.md`。

## API 主入口

| Method | Path | 用途 |
| --- | --- | --- |
| GET | `/health` | Runtime 模式和版本阶段 |
| POST | `/api/tasks` | 创建自然语言 Agent 任务 |
| GET | `/api/tasks` | 获取持久化任务历史 |
| GET | `/api/tasks/{task_id}` | 获取任务状态与最终答案 |
| GET | `/api/tasks/{task_id}/steps` | 获取真实计划步骤和结果 |
| GET | `/api/tasks/{task_id}/graph` | 获取统一 Runtime 执行视图 |
| GET | `/api/tasks/{task_id}/events` | 获取审计事实 |
| GET | `/api/tasks/{task_id}/stream` | SSE 实时审计与最终答案 |
| GET | `/api/tasks/{task_id}/approvals` | 获取任务审批 |
| POST | `/api/approvals/{id}/grant` | 批准并触发 Agent/Graph 恢复 |
| POST | `/api/approvals/{id}/deny` | 拒绝并触发安全终止 |
| POST | `/api/tasks/{task_id}/cancel` | 取消任务 |
| GET/PUT | `/api/security-profiles/default` | 获取或创建长期安全配置的新版本 |
| GET | `/api/tools` | 获取 Registry 生成的工具说明与 JSON Schema |
| POST/GET | `/api/conversations` | 创建或列出连续对话 |
| GET | `/api/conversations/{id}` | 获取持久化消息历史 |
| POST | `/api/conversations/{id}/messages` | 在同一对话中创建新的 TaskRun |

## 源码位置

- `backend/src/ra_agent/agent/`：Planner 与 Agent 状态流。
- `backend/src/ra_agent/runtime/`：唯一工具调度入口、审批恢复、TaskGraph。
- `backend/src/ra_agent/security/`：意图边界、风险、策略和检查。
- `backend/src/ra_agent/execution/`：隔离、checkpoint、commit、rollback、Effect。
- `backend/src/ra_agent/database/`：任务、审批、幂等与审计持久化。
- `frontend/src/`：Conversation Agent、安全设置、Tasks、Approvals、Runtime、Audit 和 Experiments 工作台。
- `tests/`：单元、集成、安全、回滚和端到端测试。

## 下一阶段建议

Runtime Base 适合成为 Aegis Core 的工程底座，但不能把现有规则直接改名为“意图偏移检测”。后续共通线应新增独立的 Intent Safety Engine，把初始用户目标、允许边界、行为序列和环境反馈转成可测量的偏移分数，并在 Runtime 的工具执行前输出 `ALLOW / CLARIFY / APPROVAL / BLOCK`。这样竞赛算法层可以迭代，而当前已经打通的执行、审批、回滚和审计层无需推倒重来。
