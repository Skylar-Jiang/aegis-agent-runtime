# 本地开发

Python 3.11 环境用 uv sync --project backend --group dev --locked 安装。后端实际运行与 Intent 验收步骤见 experiments/intent/LOCAL_ACCEPTANCE.md。

前端命令必须在 frontend 目录执行：

```powershell
Set-Location frontend
corepack pnpm --version
corepack pnpm install --frozen-lockfile
corepack pnpm test --run
corepack pnpm build
corepack pnpm lint
```

前端要求 Node.js >=24.14.0 且 <25，pnpm 10.12.4。VITE_API_PROXY_TARGET 可指向隔离验收后端。
