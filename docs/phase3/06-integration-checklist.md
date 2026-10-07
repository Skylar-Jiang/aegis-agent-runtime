# Phase 3 集成清单

本次 `chore(phase3): finalize parallel development baseline` readiness 提交是唯一 Phase 3 基线。提交完成后，立即在本地从该提交创建 `phase3/original-scope-completion`；其不可变 SHA 以 `git rev-parse --verify phase3/original-scope-completion` 为准，并记录在 [基线验证](BASELINE-VERIFICATION.md)。不要从 `main`、旧 `chore/phase3-foundation` 或成员功能分支开始工作。

```powershell
git switch phase3/original-scope-completion
git switch -c feat/runtime-parallel-scheduler
git switch phase3/original-scope-completion
git switch -c feat/security-pre-post-check
git switch phase3/original-scope-completion
git switch -c feat/memory-download-execution
git switch phase3/original-scope-completion
git switch -c feat/experiments-dashboard
```

四个功能分支的 PR 一律目标为 `phase3/original-scope-completion`，而不是 `main`。建议集成顺序 C → B → A → D；只有该集成分支完成 Gate 1–5 和六个演示后，才允许把它合并到 `main`。

Contract 变更必须先由组长提出迁移说明并更新 Contract tests；发生冲突时保留已冻结公共文件，以组长 rebase/resolve 为准。禁止合并：绕过 Scheduler、缺少 request_id 关联、直接 trusted 写入、真实 LLM 依赖测试、失败检查、未记录实验环境或未清理运行产物。Release 前重复执行 backend pytest/Ruff/Pyright、frontend lint/typecheck/tests/build、`scripts/check.py`，并走完六个演示。
