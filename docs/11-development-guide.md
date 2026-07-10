# 开发指南

## Windows PowerShell

```powershell
py -3.11 -m pip install --user uv
py -3.11 -m uv sync --project backend --group dev
.\backend\.venv\Scripts\Activate.ps1
corepack pnpm --dir frontend install --frozen-lockfile
```

启动后端：`py -3.11 -m uv run --project backend uvicorn ra_agent.main:app --reload`。启动前端：`corepack pnpm --dir frontend dev`。激活 `backend/.venv` 后运行全检：`python scripts/check.py`。

后端目录按 contracts、agent、runtime、security、execution、tools、memory、audit、database、api、core 分责；前端按 feature colocate。分支使用 `feat/`、`fix/`、`docs/` 前缀，提交使用 Conventional Commits。

公共 Contract 变更必须先更新测试与 `docs/03-contracts.md`，再由 Runtime、安全、执行、前端/审计负责人共同评审。禁止提交 `.env`、密钥、数据库、`.runtime`、venv、node_modules 或构建产物。
