# P2 PR1 验收记录

验证日期：2026-09-19。分支：`feature/core-event-ui-p2`。基线：`d59df71`，本轮实现首次提交：`f3eb7a5`。

## 已完成范围

- C5：BehaviorEvent、离线 Schema、规范化 fixture、JsonlEventStore、共享 EventStore 接口测试。
- C7：`/core` 契约和权限展示、行为时间线、模拟确认/拒绝、六种场景、错误与重试状态。
- 接口和验收：从 OpenAPI components 生成的 TypeScript 类型、参数交接文档、可重复执行的验收脚本。

P1 的网关、契约与权限实现，以及旧 Runtime 审计存储未改动。
这不是 C1 至 C7 全项发布验收，真实跨模块联调仍属于第二轮 PR。

## 环境和命令

Windows，Python 3.11.9，Node.js 24.18.0，pnpm 10.12.4。依赖采用仓库已有 `backend/uv.lock` 和 `frontend/pnpm-lock.yaml`，未改锁文件或增加依赖。

本机后端使用 `uv sync --project backend --group dev --frozen --no-editable` 安装，以避免中文目录 editable 路径问题。

在项目根目录运行：

```powershell
$env:PYTHONUTF8 = '1'
.\backend\.venv\Scripts\python.exe scripts/verify_core_p2.py
```

脚本自行将 `backend/src` 加入子进程 PYTHONPATH，完整结果写入 `.runtime/p2-validation.json`。

| 检查 | 实际结果 |
| --- | --- |
| Schema、TypeScript、前端 fixture 一致性 | PASS |
| Ruff：backend/src、tests、新增 Python 脚本 | PASS |
| Pyright：backend 项目 | 0 errors / 0 warnings |
| P2 事件及 P1 契约/网关/API、旧审计相关回归 | 47 passed |
| 前端 TypeScript | PASS |
| 前端 ESLint | PASS，无警告 |
| 前端 Vitest 全部测试 | 11 个文件，39 passed |
| 前端生产构建 | PASS |

后端测试有一条既有依赖弃用警告：Starlette TestClient 的 httpx 使用方式；未影响结果，未为此更改团队锁文件。
此处不声称完整后端 tests 目录全部运行过，也未测试真实 LLM、P3 密码实现或多进程 EventStore 写入。

## 浏览器验证

在一个终端运行：

```powershell
cd frontend
npm.cmd run dev -- --host 127.0.0.1 --port 15175 --strictPort
```

在项目根目录另一终端运行（需要本机 Chrome）：

```powershell
node scripts/verify_core_p2_ui.mjs
```

实际结果：六种模拟场景、批准后重检与执行轨迹、拒绝无执行、桌面 1360×1000 和手机 390×844 视口通过；没有页面脚本错误，手机没有横向溢出。
截图位于 `.runtime/p2-ui/desktop.png` 和 `.runtime/p2-ui/mobile.png`，已查看截图检查页面布局。截图与测试原始报告为本机生成的证据，不提交运行缓存。

## 限制和交接

- 默认 EventStore 无密码链；非空任务 head 查询返回 CHECK_UNAVAILABLE 异常。链注入、同事务保存和重启比对以测试替身验证。
- JSONL 为单进程、小数据实现，单文件上限 16 MiB，每次追加重写快照；不承诺多进程写入或抗任意断电。
- UI 与 Vite 开发 mock 服务联通，生产构建不提供 mock API。真实 Gateway、EventStore 和 AuditVerifier 尚未接线。
- 事件字段与签名输入细化方案需要 1/3 号评审；详细问题已列入 `docs/interfaces/p2-handoff-to-lead.md`。
