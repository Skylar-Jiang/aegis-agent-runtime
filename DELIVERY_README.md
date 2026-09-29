# Aegis Runtime Base 交付说明

这是从原始 Aegis Runtime 交付包修复出的主链基底版本。源码、配置、测试和 Windows 脚本均包含在压缩包中；真实密钥、`.env`、本地数据库、运行时产物和依赖目录不包含在内。

## 先做确定性验收

双击 `RUN_BASE_EXPERIMENTS.bat`。它不需要大模型密钥，会验证 Agent 审批恢复、持久化任务/步骤、受控写入、危险 Shell 阻断和高风险批准，并检查前端能否编译与测试。

通过后查看 `.runtime\base-validation.json`。

## 再启动真实 Agent

双击 `START_RUNTIME_BASE.bat`，按提示在 `.env` 中填写 `LLM_BASE_URL`、`LLM_API_KEY` 和 `PLANNER_MODEL`，再运行一次启动脚本。浏览器地址是 `http://127.0.0.1:5173`。

`START_DEMO.bat` 与 `start-dev.bat` 仅作为兼容入口保留，都会转到同一个 Runtime Base 启动器。

完整边界、API 和实验说明见 `README.md` 与 `docs/RUNTIME_BASE_VALIDATION.md`。
