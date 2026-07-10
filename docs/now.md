你现在是本项目的首席软件架构师和工程负责人。请根据项目策划书，为项目完成正式开发前的 Phase 0 工程冻结工作。

项目名称：

RA-Agent：面向工具增强型智能体的风险自适应运行时安全架构

项目策划书位于：

docs/人工智能安全作品赛-策划书.md

---

# 一、任务目标

本次任务不是开发完整系统，也不是实现所有业务功能。

你的目标是完成项目正式编码前的工程基础冻结，包括：

1. 冻结项目范围。
2. 冻结技术栈。
3. 冻结 Python、Node.js 和核心依赖版本范围。
4. 建立规范的前后端项目结构。
5. 创建后端和前端依赖配置。
6. 创建公共数据模型和接口占位。
7. 确定 Agent、Runtime、安全模块、执行模块和审计模块之间的边界。
8. 确定运行状态机。
9. 确定风险等级、策略决策、权限类型和事件类型。
10. 确定前后端 API 与实时事件传输方案。
11. 建立测试、代码质量、Git 和环境配置规范。
12. 建立可以启动和验证的最小工程骨架。
13. 输出完整的 Phase 0 冻结报告。

不要直接实现完整 Agent、风险分类算法、复杂 Sandbox、真实 Docker 隔离或完整前端页面。

所有未实现模块可以使用接口、抽象类、占位实现、Mock 和明确的 `NotImplementedError`。

---

# 二、项目核心定位

本项目要构建的不是一个普通 Agent，也不是在 Agent 外层增加一个简单检测插件。

核心执行链为：

```text
用户任务
→ Agent Planner
→ Tool Call Request
→ Runtime Scheduler
→ Risk Classifier
→ Policy Engine
→ Permission Gate
→ 受控执行
→ Deep Safety Check
→ Commit / Rollback
→ Audit
→ 返回 Agent
```

核心创新模块包括：

```text
风险自适应运行时调度
低风险快速执行
中风险 Sandbox/Pending + 并行检测
高风险审批
禁止操作阻断
延迟授权
Checkpoint
Commit Gate
Rollback
Memory Guard
全过程审计
```

必须保证后续所有真实工具调用都只能通过 Runtime Scheduler 进入执行模块。

Agent Planner 不得直接调用真实工具。

Risk Classifier 不得直接执行工具。

Executor 不得自行决定风险等级。

Audit 模块不得参与策略决策。

---

# 三、优先进行仓库检查

开始修改前，先完成以下检查：

1. 列出当前仓库文件结构。
2. 查找已有的 Python、Node.js、FastAPI、React、LangGraph、Docker 或测试配置。
3. 检查是否已经存在：

   * `pyproject.toml`
   * `requirements.txt`
   * `package.json`
   * `docker-compose.yml`
   * `.env`
   * `.gitignore`
   * `README.md`
   * `docs/`
4. 阅读项目策划书。
5. 判断当前仓库是空仓库、半成品仓库还是已有项目。
6. 不覆盖已有有效代码。
7. 如果已有结构与目标结构冲突，优先兼容并记录迁移建议，不要盲目删除。

检查完成后，先输出一段简短的执行计划，再开始修改。

不要反复询问用户。遇到非关键歧义时，采用合理默认方案并在最终报告中记录。

---

# 四、冻结技术栈

请使用以下技术栈作为默认冻结方案。

## 4.1 后端

```text
Python：3.11
环境与依赖管理：uv
项目配置：pyproject.toml
Web 框架：FastAPI
数据校验：Pydantic v2
配置管理：pydantic-settings
Agent 工作流：LangGraph
异步运行：asyncio
HTTP 客户端：httpx
ORM：SQLAlchemy 2.x
数据库迁移：Alembic
开发数据库：SQLite
可选部署数据库：PostgreSQL
测试：pytest、pytest-asyncio、pytest-cov
代码检查：Ruff
类型检查：Pyright
Git 钩子：pre-commit
```

LangGraph 只负责 Planner 和 Agent 状态流转。

以下模块必须自行实现，不能完全交给 LangGraph：

```text
Runtime Scheduler
Risk Classifier
Policy Engine
Permission Gate
Sandbox/Pending
Checkpoint
Commit
Rollback
Audit Event
```

## 4.2 前端

```text
React
TypeScript
Vite
React Router
TanStack Query
Zustand
React Flow
Apache ECharts
Tailwind CSS
shadcn/ui
Vitest
React Testing Library
Playwright
ESLint
Prettier
pnpm
```

## 4.3 数据与通信

```text
任务操作：REST API
实时运行轨迹：SSE
数据库：开发阶段 SQLite
审计事件：数据库持久化
安全规则：YAML
环境密钥：.env
```

第一版不使用：

```text
Celery
Redis
Kafka
Kubernetes
复杂微服务
向量数据库
复杂 RAG
多 Agent 协同
自训练模型
```

---

# 五、依赖版本策略

请基于当前环境和依赖兼容性，为核心依赖选择合理版本范围。

要求：

1. 不要全部使用完全不受限的 `latest`。
2. 不要随意锁定可能不存在的版本。
3. 核心框架使用兼容版本范围。
4. 开发工具归入 dev dependency group。
5. 使用 `pyproject.toml` 作为后端唯一主要依赖声明。
6. 不再额外维护一份内容重复且可能漂移的手写 `requirements.txt`。
7. 如果需要兼容某些部署平台，可以生成一个说明如何通过 `uv export` 导出 requirements 的文档或脚本。
8. 前端使用 `package.json` 和 `pnpm-lock.yaml`。
9. 如果当前环境允许，执行依赖解析并生成锁文件。
10. 如果因为网络或环境原因无法生成锁文件，不得伪造，应在报告中明确说明。

后端至少考虑以下依赖：

```text
fastapi
uvicorn
pydantic
pydantic-settings
langgraph
httpx
sqlalchemy
alembic
aiosqlite
pyyaml
structlog 或标准 logging 的 JSON 格式方案
python-multipart（仅在确实需要时）
```

开发依赖至少考虑：

```text
pytest
pytest-asyncio
pytest-cov
ruff
pyright
pre-commit
```

不要无理由加入大量依赖。

---

# 六、建立推荐项目结构

请在兼容已有仓库的前提下，建立或调整为以下结构：

```text
RA-Agent/
├── backend/
│   ├── pyproject.toml
│   ├── uv.lock
│   ├── alembic.ini
│   ├── migrations/
│   └── src/
│       └── ra_agent/
│           ├── __init__.py
│           ├── main.py
│           │
│           ├── api/
│           │   ├── __init__.py
│           │   ├── tasks.py
│           │   ├── approvals.py
│           │   ├── reports.py
│           │   └── streams.py
│           │
│           ├── contracts/
│           │   ├── __init__.py
│           │   ├── enums.py
│           │   ├── common.py
│           │   ├── tasks.py
│           │   ├── tools.py
│           │   ├── risk.py
│           │   ├── execution.py
│           │   ├── approvals.py
│           │   └── audit.py
│           │
│           ├── agent/
│           │   ├── __init__.py
│           │   ├── graph.py
│           │   ├── planner.py
│           │   ├── state.py
│           │   └── llm_client.py
│           │
│           ├── runtime/
│           │   ├── __init__.py
│           │   ├── scheduler.py
│           │   ├── orchestrator.py
│           │   ├── state_machine.py
│           │   └── dependency_manager.py
│           │
│           ├── security/
│           │   ├── __init__.py
│           │   ├── risk_classifier.py
│           │   ├── policy_engine.py
│           │   ├── permission_gate.py
│           │   ├── deep_checker.py
│           │   └── rule_engine.py
│           │
│           ├── execution/
│           │   ├── __init__.py
│           │   ├── executor.py
│           │   ├── sandbox.py
│           │   ├── checkpoint.py
│           │   ├── pending_store.py
│           │   ├── commit_gate.py
│           │   └── rollback.py
│           │
│           ├── tools/
│           │   ├── __init__.py
│           │   ├── registry.py
│           │   ├── specs.py
│           │   └── implementations/
│           │       ├── __init__.py
│           │       ├── list_dir.py
│           │       ├── read_file.py
│           │       ├── write_file.py
│           │       ├── delete_file.py
│           │       ├── run_shell.py
│           │       ├── download_url.py
│           │       └── memory_tools.py
│           │
│           ├── memory/
│           │   ├── __init__.py
│           │   ├── guard.py
│           │   ├── models.py
│           │   └── repository.py
│           │
│           ├── audit/
│           │   ├── __init__.py
│           │   ├── logger.py
│           │   ├── event_bus.py
│           │   ├── repository.py
│           │   └── report_generator.py
│           │
│           ├── database/
│           │   ├── __init__.py
│           │   ├── session.py
│           │   ├── models.py
│           │   └── repositories/
│           │
│           └── core/
│               ├── __init__.py
│               ├── config.py
│               ├── errors.py
│               ├── ids.py
│               └── logging.py
│
├── frontend/
│   ├── package.json
│   ├── pnpm-lock.yaml
│   ├── vite.config.ts
│   ├── tsconfig.json
│   └── src/
│       ├── api/
│       ├── components/
│       ├── features/
│       │   ├── tasks/
│       │   ├── runtime-graph/
│       │   ├── approvals/
│       │   ├── audit/
│       │   └── experiments/
│       ├── pages/
│       ├── stores/
│       └── types/
│
├── configs/
│   ├── risk_rules.yaml
│   ├── permissions.yaml
│   ├── tool_policies.yaml
│   ├── sensitive_paths.yaml
│   └── runtime.yaml
│
├── tests/
│   ├── unit/
│   ├── contract/
│   ├── integration/
│   ├── security/
│   ├── rollback/
│   └── e2e/
│
├── experiments/
│   ├── cases/
│   ├── runners/
│   ├── results/
│   └── analysis/
│
├── docs/
│   ├── 00-scope.md
│   ├── 01-architecture.md
│   ├── 02-tech-stack.md
│   ├── 03-contracts.md
│   ├── 04-state-machine.md
│   ├── 05-security-policy.md
│   ├── 06-tool-specification.md
│   ├── 07-execution-semantics.md
│   ├── 08-api-specification.md
│   ├── 09-event-schema.md
│   ├── 10-test-plan.md
│   ├── 11-development-guide.md
│   └── adr/
│
├── scripts/
├── .github/
│   └── workflows/
├── .env.example
├── .gitignore
├── docker-compose.yml
├── Makefile
└── README.md
```

如果仓库已有合理结构，可以做小幅调整，但必须保持清晰的模块边界，并在报告中说明差异。

---

# 七、创建并冻结公共枚举

在 `contracts/enums.py` 中定义统一枚举。

至少包括：

## 7.1 风险等级

```text
LOW
MEDIUM
HIGH
CRITICAL
FORBIDDEN
```

## 7.2 策略决策

```text
FAST_EXECUTE
SANDBOX_CHECK
REQUEST_APPROVAL
BLOCK
```

## 7.3 权限状态

```text
GRANTED
DENIED
PENDING
NOT_REQUIRED
EXPIRED
```

## 7.4 执行状态

```text
SUCCESS
FAILED
PENDING_COMMIT
BLOCKED
ROLLED_BACK
COMMITTED
CANCELLED
TIMEOUT
```

## 7.5 步骤状态

```text
PLANNED
RISK_CLASSIFYING
WAITING_PERMISSION
READY
CHECKPOINT_CREATING
EXECUTING_FAST
EXECUTING_SANDBOX
SAFETY_CHECKING
WAITING_APPROVAL
COMMITTING
ROLLING_BACK
COMMITTED
ROLLED_BACK
BLOCKED
FAILED
CANCELLED
```

## 7.6 可恢复类型

```text
ATOMIC
BACKUP_RESTORE
COMPENSATING
NON_REVERSIBLE
```

## 7.7 权限类型

```text
FILE_LIST
FILE_READ
FILE_WRITE
FILE_DELETE
SENSITIVE_READ
SHELL_EXEC
NETWORK_DOWNLOAD
NETWORK_UPLOAD
MEMORY_READ
MEMORY_WRITE
SYSTEM_MODIFY
```

## 7.8 审计事件类型

至少包括：

```text
TASK_CREATED
PLAN_CREATED
TOOL_REQUESTED
RISK_CLASSIFIED
PERMISSION_CHECKED
APPROVAL_REQUESTED
APPROVAL_GRANTED
APPROVAL_DENIED
CHECKPOINT_CREATED
EXECUTION_STARTED
EXECUTION_FINISHED
DEEP_CHECK_STARTED
DEEP_CHECK_FINISHED
COMMIT_STARTED
COMMIT_FINISHED
ROLLBACK_STARTED
ROLLBACK_FINISHED
TOOL_BLOCKED
STEP_FAILED
TASK_FINISHED
TASK_CANCELLED
```

枚举名称一旦确定，其他模块不得自行创建同义状态。

---

# 八、创建公共 Pydantic Contract

至少创建以下模型：

```text
TaskCreateRequest
TaskResponse
TaskStep
ToolSpec
ToolCallRequest
RiskVerdict
PermissionDecision
ApprovalRequest
ApprovalDecision
CheckpointResult
ToolExecutionResult
DeepCheckResult
CommitResult
RollbackResult
AuditEvent
APIError
APIResponse
```

要求：

1. 每个模型有清晰字段说明。
2. 所有 ID 使用字符串。
3. 所有时间使用带时区的 UTC datetime。
4. 使用统一字段名：

   * `task_id`
   * `step_id`
   * `request_id`
   * `checkpoint_id`
   * `event_id`
5. 不允许同一概念在不同模型中使用不同名字。
6. 模型应支持后续序列化到 JSON。
7. 创建基础 Contract 测试，验证典型对象可以成功创建和序列化。

不要过度设计字段，但要保证四个成员可以据此并行开发。

---

# 九、创建接口与抽象边界

请定义清晰的 Protocol、ABC 或服务接口。

至少包括：

```python
class LLMClient(Protocol):
    ...

class RiskClassifier(Protocol):
    ...

class PolicyEngine(Protocol):
    ...

class PermissionGate(Protocol):
    ...

class DeepSafetyChecker(Protocol):
    ...

class ToolExecutor(Protocol):
    ...

class CheckpointManager(Protocol):
    ...

class CommitGate(Protocol):
    ...

class RollbackManager(Protocol):
    ...

class AuditEventSink(Protocol):
    ...
```

同时创建最小 Mock 实现，例如：

```text
MockLLMClient
MockRiskClassifier
MockPermissionGate
MockDeepSafetyChecker
MockToolExecutor
InMemoryAuditEventSink
```

Mock 仅用于：

```text
启动项目
Contract 测试
Runtime 骨架联通
前后端联调
```

不得将 Mock 伪装成真实安全能力。

---

# 十、冻结工具规范

第一版工具范围固定为：

```text
list_dir
read_file
write_file
delete_file
run_shell
download_url
memory_read
memory_write
```

每个工具必须拥有 `ToolSpec`，至少记录：

```text
name
description
required_permissions
base_risk
side_effect_type
reversibility
sandbox_mode
timeout_seconds
network_required
allowed_paths
supports_dry_run
```

当前阶段可以只实现安全的 Mock 或最小受限版本。

重点建立注册和调用规范：

```text
Agent 不得直接 import 工具实现
所有工具必须注册到 ToolRegistry
所有工具调用必须形成 ToolCallRequest
所有真实执行必须经过 Runtime Scheduler
```

`run_shell` 当前阶段不得实现无限制命令执行。

可以只提供占位接口或严格白名单 Mock，并在文档中声明：

> 临时目录不等于完整系统 Sandbox；任意 Shell 操作不能承诺可靠回滚。

---

# 十一、冻结运行状态机

在代码和 `docs/04-state-machine.md` 中定义合法流转。

至少描述以下路径：

## 低风险

```text
PLANNED
→ RISK_CLASSIFYING
→ READY
→ EXECUTING_FAST
→ COMMITTED
```

## 中风险通过

```text
PLANNED
→ RISK_CLASSIFYING
→ CHECKPOINT_CREATING
→ EXECUTING_SANDBOX
→ SAFETY_CHECKING
→ COMMITTING
→ COMMITTED
```

## 中风险失败

```text
PLANNED
→ RISK_CLASSIFYING
→ CHECKPOINT_CREATING
→ EXECUTING_SANDBOX
→ SAFETY_CHECKING
→ ROLLING_BACK
→ ROLLED_BACK
```

## 高风险审批

```text
PLANNED
→ RISK_CLASSIFYING
→ WAITING_APPROVAL
→ READY 或 BLOCKED
```

## 禁止操作

```text
PLANNED
→ RISK_CLASSIFYING
→ BLOCKED
```

在状态机代码中实现最小合法性校验：

1. 没有风险判断不得执行。
2. BLOCK 决策不得开始真实执行。
3. 中风险写操作没有 Checkpoint 不得执行。
4. 没有安全通过结果不得 Commit。
5. 终态不得再次执行。
6. 同一请求不能重复产生副作用。

---

# 十二、冻结前后端 API

建立 FastAPI 路由骨架和 Pydantic 响应模型。

至少包括：

```text
GET    /health

POST   /api/tasks
GET    /api/tasks/{task_id}
GET    /api/tasks/{task_id}/steps
GET    /api/tasks/{task_id}/events
GET    /api/tasks/{task_id}/stream

POST   /api/approvals/{approval_id}/grant
POST   /api/approvals/{approval_id}/deny

POST   /api/tasks/{task_id}/cancel
GET    /api/tasks/{task_id}/report
```

要求：

1. 当前可以返回 Mock 数据。
2. `/health` 必须真实可访问。
3. SSE 路由可以先建立可运行的最小事件流。
4. API 统一使用 `APIResponse`。
5. 错误统一使用 `APIError`。
6. 在 `docs/08-api-specification.md` 中记录接口。
7. 如方便，可通过 FastAPI 自动生成 OpenAPI，不需要另外维护重复的 Swagger 文件。

---

# 十三、冻结审计事件

AuditEvent 是前端、实验和系统追踪的统一事实来源。

事件模型至少包含：

```text
event_id
task_id
step_id
request_id
sequence_number
event_type
timestamp
actor
status
risk_level
decision
summary
details
```

要求：

1. 同一任务的 `sequence_number` 单调增加。
2. 所有重要状态变化必须产生事件。
3. 前端以后根据事件流展示，不读取内部 Python 对象。
4. 敏感参数进入审计日志前需要脱敏。
5. 当前创建一个内存版 Event Sink，数据库版本只建立接口和模型骨架即可。

---

# 十四、创建配置文件

创建以下配置：

```text
configs/risk_rules.yaml
configs/permissions.yaml
configs/tool_policies.yaml
configs/sensitive_paths.yaml
configs/runtime.yaml
```

只需要放入第一版默认规则和清晰注释。

示例内容包括：

```text
.env
*.pem
*.key
id_rsa
credentials
secrets
```

危险 Shell 示例包括：

```text
rm -rf
format
shutdown
reboot
mkfs
磁盘写入
注册表或系统目录修改
```

注意：

1. 规则配置与代码逻辑分离。
2. 不要仅靠关键词就宣称完成全部安全检测。
3. 文档中明确规则检测只是第一层，后续还需要上下文判断和 LLM 深度检测。

---

# 十五、创建环境和安全配置

创建 `.env.example`，只放变量名和非敏感示例：

```text
APP_ENV=development
DATABASE_URL=sqlite+aiosqlite:///./ra_agent.db

LLM_BASE_URL=
LLM_API_KEY=
PLANNER_MODEL=
CHECKER_MODEL=

WORKSPACE_ROOT=.runtime/workspace
PENDING_ROOT=.runtime/pending
CHECKPOINT_ROOT=.runtime/checkpoints
QUARANTINE_ROOT=.runtime/quarantine
```

创建完善的 `.gitignore`，至少排除：

```text
.env
.venv/
__pycache__/
.pytest_cache/
.ruff_cache/
.pyright/
coverage 文件
node_modules/
dist/
.runtime/
*.db
前端构建产物
IDE 文件
日志文件
```

不得把真实 API Key 写入任何配置、测试或文档。

---

# 十六、创建开发命令

创建 `Makefile` 或等价脚本，至少支持：

```text
make install
make backend-dev
make frontend-dev
make test
make lint
make typecheck
make format
make check
```

Windows 用户较多，因此不要只依赖 Bash 专用语法。

如果 Makefile 在 Windows 下不方便，可以同时创建：

```text
scripts/dev_backend.py
scripts/check.py
```

README 中给出 Windows PowerShell 命令。

---

# 十七、创建代码质量配置

配置：

```text
Ruff
Pyright
pytest
pre-commit
ESLint
Prettier
Vitest
```

要求：

1. Ruff 负责格式和 lint。
2. Pyright 至少使用 basic 或 standard 严格度，并在文档中说明。
3. pytest 配置 asyncio 模式。
4. 前端 TypeScript 开启 strict。
5. 建立最小 CI：

   * 后端 lint
   * 后端类型检查
   * 后端测试
   * 前端 lint
   * 前端类型检查
   * 前端构建
6. CI 不得依赖真实 LLM API Key。

---

# 十八、创建基础测试

至少建立以下测试：

```text
健康检查测试
Contract 创建与序列化测试
风险枚举和策略枚举测试
状态机合法流转测试
状态机非法流转测试
Mock ToolCallRequest 流程测试
AuditEvent 序列化测试
配置加载测试
```

暂时不需要完整安全算法测试。

测试不能调用真实外部 API。

---

# 十九、创建文档

必须创建并填写以下文档，不要只建立空文件：

## `docs/00-scope.md`

写清楚：

```text
第一版做什么
第一版不做什么
安全能力边界
Shell 回滚限制
Sandbox 原型限制
```

## `docs/01-architecture.md`

写清楚：

```text
系统总体架构
模块依赖关系
调用方向
禁止的跨模块调用
完整执行链
```

## `docs/02-tech-stack.md`

写清楚：

```text
最终技术栈
采用原因
暂不采用的技术
版本策略
```

## `docs/03-contracts.md`

列出核心 Contract、字段含义和模块间输入输出。

## `docs/04-state-machine.md`

描述状态、合法流转、终态和异常流转。

## `docs/05-security-policy.md`

描述：

```text
风险维度
风险等级
策略映射
权限类型
规则检测和 LLM 检测关系
默认保守策略
```

## `docs/06-tool-specification.md`

描述八个工具的：

```text
权限
基础风险
副作用
可逆性
Sandbox 模式
```

## `docs/07-execution-semantics.md`

写清楚：

```text
Pending
Trusted
Checkpoint
Commit
Rollback
并行检测
超时
取消
异常处理
```

## `docs/08-api-specification.md`

记录 REST 和 SSE 接口。

## `docs/09-event-schema.md`

记录 AuditEvent 与事件类型。

## `docs/10-test-plan.md`

记录：

```text
正常任务
危险命令
敏感文件读取
间接提示注入
Memory 投毒
状态污染
超时
取消
Rollback 失败
```

## `docs/11-development-guide.md`

记录：

```text
环境安装
项目启动
测试
代码检查
目录职责
分支规范
提交规范
公共 Contract 修改流程
```

---

# 二十、创建 ADR

在 `docs/adr/` 至少创建：

```text
architecture-decisions.md
```

每份 ADR 包含：

```text
状态
背景
决定
备选方案
理由
后果与限制
```

---

# 二十一、创建 README

README 应包括：

```text
项目简介
核心架构
技术栈
目录结构
快速开始
环境变量
启动后端
启动前端
运行测试
开发规范
当前完成状态
当前限制
后续开发分工入口
```

必须明确注明：

```text
当前仓库完成的是 Phase 0 工程骨架
真实风险分类、完整 Sandbox、Commit/Rollback 和 Agent 流程仍需后续开发
```

不要夸大当前完成度。

---

# 二十二、执行验证

完成文件创建后，尽可能执行：

```text
后端依赖解析
后端格式检查
后端 lint
后端类型检查
后端测试
后端启动
GET /health

前端依赖解析
前端类型检查
前端测试
前端构建
```

如果某一步因为当前机器缺少：

```text
uv
pnpm
Node.js
Docker
网络连接
```

而无法完成，请不要伪造成功结果。

记录：

```text
执行了什么
成功了什么
失败了什么
失败原因
用户下一步应执行的命令
```

---

# 二十三、不要做的事情

本次任务禁止：

1. 不要实现完整风险分类算法。
2. 不要接入真实付费模型并产生调用费用。
3. 不要实现无限制 Shell 执行。
4. 不要宣称临时目录是完整 Sandbox。
5. 不要宣称所有 Shell 操作都可以回滚。
6. 不要开发复杂微服务。
7. 不要引入 Redis、Celery、Kafka 或 Kubernetes。
8. 不要加入与核心目标无关的 RAG、向量数据库或多 Agent。
9. 不要删除未知的现有业务文件。
10. 不要把 Mock 当作真实实现。
11. 不要只写文档而不创建可运行工程骨架。
12. 不要只创建目录而不提供基本 Contract、测试和启动入口。
13. 不要创建重复的状态、风险等级或接口定义。
14. 不要把 API Key 写进仓库。

---

# 二十四、最终输出格式

完成后，请在终端回复中按以下格式总结。

## 1. 仓库原始状态

说明开始前有哪些文件和已有实现。

## 2. 已冻结的决策

列出：

```text
技术栈
目录结构
依赖管理
Agent 框架边界
通信方式
数据库
测试工具
安全边界
```

## 3. 已创建或修改的文件

按目录列出主要文件。

## 4. 核心 Contract

列出创建的数据模型和枚举。

## 5. 当前可运行能力

明确现在实际能做什么。

## 6. 尚未实现能力

明确哪些只是接口或 Mock。

## 7. 验证结果

列出每条执行命令和结果。

## 8. 风险与待确认事项

只列真正需要组长决定的问题，不要提出大量非关键问题。

## 9. 下一阶段建议

根据四人分工给出：

```text
成员 A：Runtime 与总集成
成员 B：Risk、Policy、Permission
成员 C：Tools、Sandbox、Checkpoint、Rollback
成员 D：Audit、Frontend、Experiment
```

每个人列出可以立即开始的文件和第一个验收目标。

---

现在开始：

1. 检查仓库。
2. 阅读策划书。
3. 输出简短实施计划。
4. 完成 Phase 0 工程冻结。
5. 执行验证。
6. 给出完整总结。
