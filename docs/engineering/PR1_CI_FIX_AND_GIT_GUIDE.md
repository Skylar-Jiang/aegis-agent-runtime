# PR1 合并后 CI 修复、验证与提交指南

本文适用于已经包含 PR1 提交的本地仓库。建议使用随交付包提供的
`aegis-pr1-ci-fix.patch` 应用增量修改，这样可以保留现有 `.git` 历史，并正确删除已经失效的
旧实验测试和验证脚本。

## 1. 应用补丁

在 PowerShell 中设置路径。请把 `$Patch` 改成实际下载和解压位置：

```powershell
$Project = "F:\Github\Aegis-Runtime-Base-v0.4-Conversation"
$Patch = "$env:USERPROFILE\Downloads\Aegis-PR1-CI-Fix-Patch\aegis-pr1-ci-fix.patch"

Set-Location $Project
git status --short
git branch --show-current
git log -1 --oneline
```

只有在 `git status --short` 没有输出时继续。先检查补丁，再正式应用：

```powershell
git apply --check $Patch
git apply $Patch
git status --short
git diff --check
```

如果 `git apply --check` 报错，不要使用 `--reject` 或强制覆盖；先确认本地是否已经修改了同一批
文件，以及当前分支是否已经包含 PR1。

## 2. 后端验证

如果电脑还不能识别 `uv`，先安装：

```powershell
py -3.11 -m pip install --user uv
```

下面统一通过 `py -3.11 -m uv` 调用，不依赖 `uv.exe` 是否已经加入 PATH：

```powershell
Set-Location $Project
py -3.11 -m uv sync --project backend --group dev --frozen

py -3.11 -m uv run --project backend ruff check backend/src tests
py -3.11 -m uv run --project backend pyright --project backend/pyproject.toml
py -3.11 -m uv run --project backend pytest tests --cov=ra_agent --durations=20
```

预期结果：

- Ruff：`All checks passed!`
- Pyright：`0 errors, 0 warnings, 0 informations`
- Pytest：`948 passed, 1 skipped`

PR1 专项验证可再运行：

```powershell
py -3.11 -m uv run --project backend python scripts/verify_core_pr1.py
```

## 3. 前端验证

```powershell
Set-Location "$Project\frontend"
corepack enable
corepack pnpm install --frozen-lockfile
corepack pnpm lint
corepack pnpm typecheck
corepack pnpm exec vitest run
corepack pnpm build
Set-Location $Project
```

预期 Vitest 结果为 `9 passed`、`25 passed`，并且 Vite production build 成功。

## 4. 提交并推送到现有 PR 分支

先检查将要提交的内容：

```powershell
Set-Location $Project
git status --short
git diff --stat
git diff --check
```

确认无误后提交全部新增、修改和删除：

```powershell
git add -A
git diff --cached --stat
git diff --cached --check
git commit -m "fix(ci): stabilize checks and remove stale experiment gates"
```

查看当前分支名：

```powershell
$Branch = git branch --show-current
$Branch
```

如果输出为 `feature/core-contract-gateway-p1`，推送命令是：

```powershell
git push -u origin feature/core-contract-gateway-p1
```

如果输出是其他功能分支，则用当前分支名推送：

```powershell
git push -u origin $Branch
```

已有 Pull Request 会自动纳入这次新提交，无需重新创建 PR。新的 CI 同一分支运行开始后，旧的同分支
运行会因为 `concurrency.cancel-in-progress` 自动取消。不要直接向 `main` 强制推送。

## 5. GitHub 上最终确认

1. 打开仓库的 **Pull requests**，进入现有 PR。
2. 在 **Checks** 中确认 `backend` 与 `frontend` 均为绿色。
3. `backend` 不应再停在 43%；本次完整后端测试通常在数分钟内完成，工作流设置了 30 分钟硬超时。
4. 确认 PR 的 **Files changed** 中没有 `.env`、`.venv`、`node_modules`、`.runtime` 或 API Key。
5. CI 全绿并完成代码审查后再合并。
