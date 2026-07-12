# Phase 1 开发指南

## 统一环境

- Python：3.11，项目虚拟环境位于 `backend/.venv`。
- Node.js：24.14.0，允许范围 `>=24.14.0 <25`。
- pnpm：10.12.4，由 Corepack 和 `frontend/package.json` 固定。

Python `.venv` 与 Node.js 版本管理互相独立；激活或退出 Python 虚拟环境不会切换 Node.js。

### Windows PowerShell

```powershell
nvm install 24.14.0
nvm use 24.14.0
node --version
corepack enable

py -3.11 -m pip install --user uv
py -3.11 -m uv sync --project backend --group dev
.\backend\.venv\Scripts\Activate.ps1

cd frontend
corepack pnpm --version
corepack pnpm install --frozen-lockfile
cd ..
```

已安装目标 Node 版本时，可以从仓库根目录运行 `powershell -File scripts/use-node.ps1`。脚本不会安装 Node、NVM 或修改 NVM 配置。

### macOS/Linux

使用 nvm、fnm、asdf 等版本管理器安装并切换 `.nvmrc` 中的版本，然后执行 `corepack enable`，再进入 `frontend` 目录执行 `corepack pnpm --version` 和 `corepack pnpm install --frozen-lockfile`。

## 开发规则

不使用 `npm install`，不擅自升级 Node.js 或 pnpm，不删除或自行重建 `pnpm-lock.yaml`。开始开发前先运行 `node --version`，并在 `frontend` 目录运行 `corepack pnpm --version`；预期分别为 `v24.14.0` 和 `10.12.4`。仓库根目录的 pnpm 输出可能是 Corepack 全局默认版本，不用于判断项目基线。后端启动命令为 `py -3.11 -m uv run --project backend uvicorn ra_agent.main:app --reload`，前端启动命令为 `corepack pnpm --dir frontend dev`，全检为 `python scripts/check.py`。

目录按 contracts、agent、runtime、security、execution、tools、memory、audit、database、api、core 分责；公共 Contract、枚举、状态机和依赖版本由组长统一审核。提交使用 Conventional Commits，禁止提交 `.env`、密钥、数据库、`.runtime`、venv、node_modules 或构建产物。

成员实现真实模块时只替换 `ServiceContainer` 中对应 Protocol：安全侧替换 Risk/Policy/Permission/DeepCheck，执行侧替换 ToolExecutor/Checkpoint/Commit/Rollback/ToolHandler，审计前端侧替换 ApprovalService/AuditRecorder 及 API 持久化。不得复制 Scheduler 主流程，也不得让 Agent 或路由直接调用 ToolHandler。
