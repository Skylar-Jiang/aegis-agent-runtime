# 技术栈与版本

后端 Python 3.11、FastAPI、Pydantic、SQLite 和真实 SM2 OpenSSL。使用 backend/uv.lock 安装依赖。
前端 React、TypeScript、Vite，Node.js >=24.14.0 且 <25，pnpm 10.12.4。

在 frontend 目录运行 `corepack pnpm --version`，不要从仓库根目录调用该命令来判断项目版本。

接口由 scripts/export_core_event_contracts.py 从后端模型生成，保持事件与前端类型一致。
