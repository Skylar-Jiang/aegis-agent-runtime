# Phase 3 集成清单

开始前：基于 `chore/phase3-foundation`，确认 Contract v0.3、工作区干净、完整基线检查通过。推荐合并 C → B → A → D；每个 PR 提供范围、关联 Contract、测试命令/输出、风险与 rollback 证据。

Contract 变更必须先由组长提出迁移说明并更新 Contract tests；发生冲突时保留已冻结公共文件，以组长 rebase/resolve 为准。禁止合并：绕过 Scheduler、缺少 request_id 关联、直接 trusted 写入、真实 LLM 依赖测试、失败检查、未记录实验环境或未清理运行产物。Release 前重复执行 backend pytest/Ruff/Pyright、frontend lint/typecheck/tests/build、`scripts/check.py`，并走完六个演示。
