# DeepSeek 接入与真实对话实验（2026-09-27）

## 本轮解决的问题

连续对话此前在规划前报 `PLANNER_FAILED: live DeepSeek planning is not configured`。本轮在本地 `.env` 配置 DeepSeek 官方服务，并在浏览器连续对话页面执行真实文件操作。密钥不进入 Git、截图或实验记录。

真实调用又复现了一个原先自动化实验没有暴露的问题：写入已提交，但 Planner 收到的反馈仍包含 `output.staged=true`、`PENDING_COMMIT` artifact 和 `PENDING` 变更。一次真实修改任务重复写入了 8 次，达到轮数上限后失败。

本轮修复仅调整 Planner 的内部结果投影：当 Runtime 已明确返回 COMMITTED 且存在准备阶段的变更时，向模型提供最终 `committed_changes`，去除暂存路径和过期的待提交状态；提示模型依据已完成的操作推进后续步骤。原始 ToolExecutionResult、审计/恢复证据、任务契约、权限校验及 C1–C7 公共接口不变。未提交、失败或待审批结果不会被表示为已提交。

这不是新的防重放协议：模型以新的 request_id 提出相同操作，仍不等于同 request_id 的重试。本修复消除反馈中的状态矛盾，不承诺任意模型永不重复规划。

## 本地模型配置

仓库根目录 `.env` 的变量如下，密钥只填在本机，不填写到 GitHub 或实验报告中：

```dotenv
RUNTIME_MODE=live-agent
LLM_BASE_URL=https://api.deepseek.com
LLM_API_KEY=<仅在本机填写>
PLANNER_MODEL=deepseek-flash
CHECKER_MODEL=
```

模型名称同时经官方 `/models` 实际认证查询确认：HTTP 200，返回 deepseek-flash 和 deepseek-v4-pro。官方说明见 https://api-docs.deepseek.com/guides/harness 。本轮沿用项目现有 Chat Completions 适配器；没有把密钥接入其他服务。

两个终端分别从仓库根目录运行（需已有依赖）：

```powershell
# 终端一：同时保留本地 Core SM2/SM3 服务
& .\backend\.venv\Scripts\python.exe -X utf8 scripts/start_core.py --port 8000

# 终端二
cd frontend
pnpm dev --host 127.0.0.1 --port 5173 --strictPort
```

访问 http://127.0.0.1:5173/conversations 。修改 `.env` 后必须重启后端。

## 必须写清楚的权限配置

本轮实验保留 list_dir、read_file、create_file、write_file 权限，禁止外发，最大影响对象 100，批量审批阈值 20；写入未设置始终审批。资源范围逐行填写：

```text
reports
reports/**
```

`reports/**` 不包含目录 `reports` 本身。真实模型可能先用 list_dir 检查目标是否存在；只填通配范围时，此步骤会被正确拒绝。补充目录本身的精确授权，不需要开放整个工作区。

实验完成后已恢复本机原有安全配置，生成 v21；上述成功任务保存其执行时的 v20 契约。复现实验时按上面的两行资源范围重新保存即可。

实际文件位于仓库 `.runtime/core-demo/workspace/reports/`。安全设置页面的 `.runtime/workspace` 固定提示不适用于此启动方式，应按启动脚本输出定位。

## 真实前端实验结果

对话 ID：`conversation-f314b36e-7a33-486c-a969-0375d1ef02ad`。

| 场景 | 真实结果 |
| --- | --- |
| 仅允许 reports/**，自然语言要求创建文件 | 模型先列 reports 目录，resource_not_allowed，BLOCKED，无写入。任务 task-c1e22c00-8ce4-4b0b-89ca-bb1147a47183。 |
| 增加 reports 精确范围后创建第一版 | COMPLETED，实际文件内容正确；此次尚未修复结果反馈，模型存在冗余列目录和读取。任务 task-a476f867-2615-48f4-95d0-1f7af546ac6a。 |
| 修复前修改第二版并要求读取一次 | 实际写入第二版，但重复 write_file 8 次，最终 MAX_TURNS_EXCEEDED；该任务必须记为失败。任务 task-b0b27446-c099-47db-9970-ba7c01b907cc，114 条事件。 |
| 修复后修改第三版并读取 | **COMPLETED**，恰好 write_file → read_file 两次工具调用，25 条事件，磁盘和读取结果一致。任务 task-988019df-d2e8-4680-b896-2ac96bb737b3。 |
| 修复后使用新文件再次创建并读取 | **COMPLETED**，恰好 create_file → read_file 两次工具调用，25 条事件，磁盘和读取结果一致。任务 task-461fb58c-e832-4be4-b9b5-4e6a9833cd53。 |

修复后实际发送的两条指令：

> 请把 reports/p3-deepseek-live-0927-01.txt 的内容完整替换为“成员3真实模型修复后第三版”。用 write_file 写入，再用 read_file 核验一次，确认一致后直接回复，不要重复读取或运行命令行。

> 请创建 reports/p3-deepseek-live-0927-02.txt，内容为“成员3模型接入验收”。然后读取核对并告诉我文件内容。只使用文件工具。

两份磁盘结果分别为上述第三版文本（37 字节）和验收文本（25 字节），均为 UTF-8。自行复测新建时请换文件名，修改已有文件应使用 write_file。

## 回归验证与证据

- 先新增已提交反馈的回归用例，修复前实际观察到失败，修复后通过。
- Planner 与 Agent Runtime 定向测试：27 passed。
- 仓库根目录全量后端：**1238 passed、8 skipped**，358.03 秒；1 条既有 Starlette 弃用提示。跳过项未计为通过。
- 修改文件 Ruff 检查通过，Pyright 0 errors。
- 新增用例同时检查：最终状态一致、暂存路径不外传、敏感键仍脱敏、原始结果对象不被修改、待审批状态不会被改成成功。
- 前端真实操作已经执行，以上测试不代替前端实验。
- 本地证据位于 `.runtime/member3-validation-0927/llm-live/`：对话、五次任务、完整分页事件、步骤、Agent 状态、实际文件副本和安全配置。截图位于其上级目录的 11—14 号 PNG。该目录被 Git 忽略，不包含 `.env`。

第一次从 backend 子目录启动全量检查，因相对配置路径和 tests 包解析失败而停止收集；正确运行位置为仓库根目录。随后全量检查通过；检查进程内禁用开发者 `.env` 加载，模拟 CI 没有真实凭据的环境，避免测试调用付费模型。完整输出保存在 `.runtime/member3-validation-0927/llm-fix-backend-tests-root.log`。

## 验收边界

本次证明自然语言 → DeepSeek Planner → 原 Runtime 权限/受控文件工具 → 真实文件/读取结果可以完成。该入口与 Core 工作台的契约、网关、SM2 证据入口仍需按团队方案继续整合，不能把它自动认定为完整 Core 密码链路验收。

本次也不替代成员3的全部密码学负例、密钥泄露检查和 100/1000 次性能实验。前端“失败”原因提示尚未在本轮修改；真实失败详情保存在对应任务事件中。
