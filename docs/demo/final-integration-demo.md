# Aegis Runtime 最终演示脚本

时长：3--5 分钟。所有演示都在本地 `live-agent` 隔离运行目录中执行，不连接真实 LLM，不部署云服务。

## 预检与备用启动

```powershell
py -3.11 -m uv sync --project backend --group dev
corepack pnpm --dir frontend install --frozen-lockfile
corepack pnpm --dir frontend exec playwright test -c ..\playwright.config.ts
```

若需要手动展示，分别启动 API 与前端：

```powershell
$env:RUNTIME_MODE = "live-agent"
$env:ENABLE_DEMO_FIXTURES = "true" # local selective-rollback demo only
py -3.11 -m uv run --project backend uvicorn ra_agent.main:app --app-dir backend/src --host 127.0.0.1 --port 8000
corepack pnpm --dir frontend exec vite --host 127.0.0.1 --port 5173
```

浏览器脚本失败时，使用 `test-results/` 中的失败截图、`trace.zip` 和 `video.webm` 复核；命令不会修改正式实验 raw/derived 数据。

## 固定流程

1. 打开工作台，说明任务只进入受控 Runtime；界面只显示审计事实，不显示密钥、原始不可信输出或隐藏推理。
2. 打开 Runtime，展示 TaskGraph、脱敏 Effect 投影和审批事实。相同节点的 speculative overlap 明确为 `NOT_SUPPORTED`。
3. 展示 `WAITING_APPROVAL`：独立节点完成，高风险删除节点暂停，后代不被误执行。
4. 在 Approval 卡片执行 Grant；刷新 Runtime，展示 Graph 恢复、依赖节点收敛与 Audit 记录。
5. 使用第二个审批卡片执行 Deny；展示目标和后代 `BLOCKED`，无危险删除提交。
6. 在本地 demo fixture 中展示 Cancel：Runtime 页先显示真实的独立 `COMMITTED` Memory Effect 与受影响 `PENDING` Memory Effect；点击 `Cancel graph` 后，真实 `SelectiveRollbackExecutor` 将受影响 Effect 变为 `ROLLED_BACK`，独立 Effect 以 `PRESERVED` 脱敏投影保留。随后 resume API 返回 409，Audit 显示 Cancel、Rollback、Preserved 与 Graph 终态。
7. 打开 Audit，展示危险 `rm -rf` 规则升档后被 Safety block，未进入受控 Shell 执行。
8. 打开 Experiment Dashboard，展示当前仓库能够重新生成的验证结果。历史 V2 固定数据只作为归档材料，不再作为 CI 的运行前置条件。

## 验收命令

```powershell
py -3.11 scripts/verify_runtime_base.py
py -3.11 scripts/verify_core_pr1.py
corepack pnpm --dir frontend exec playwright test -c ..\playwright.config.ts tests/e2e/final-runtime-scenarios.spec.ts
```

上述两个 Python 验证脚本检查当前 Runtime Base 与 Core PR1 主链。旧版
`verify_final_experiments.py` 依赖未随仓库发布的固定 V2 衍生数据，已从当前验收入口移除。

`ENABLE_DEMO_FIXTURES` 默认是 `false`；未显式启用时 `/api/demo/selective-rollback` 返回 404。该入口只使用本地隔离 Runtime 的真实 Memory、EffectStore 与 SelectiveRollbackExecutor，不写入正式实验数据。
