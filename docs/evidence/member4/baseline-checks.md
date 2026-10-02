# 2026-10-02 起点验证

源码起点 `d60f443996a6e8e11927ba8fe018eeea0813ecd3`，未修改生产代码。

| 命令 | 实测 |
| --- | --- |
| `backend/.venv/Scripts/python -m ruff check backend/src tests` | PASS |
| `backend/.venv/Scripts/python -m pyright --project backend/pyproject.toml` | 0 errors / 0 warnings；工具提示新版可用 |
| `corepack pnpm --dir frontend typecheck` | PASS |
| `corepack pnpm --dir frontend exec vitest run` | 16 files / 81 tests PASS |
| `corepack pnpm --dir frontend build` | PASS，117 modules |
| `corepack pnpm --dir frontend lint` | PASS |
| `pytest tests/unit/core/test_core_tool_gateway.py tests/unit/core/test_core_contract_service.py tests/unit/events/test_event_store.py tests/security/test_intent_boundary.py tests/unit/tools/test_path_resolver.py tests/unit/memory -q` | 124 passed / 3 skipped |
| `pytest tests/contract -q -x` | 14 passed，首次失败：缺 `docs/interfaces/schemas/SignedEnvelope.schema.json` |
| `pytest tests -q --junitxml=.runtime/baseline-pytest.xml` | 全量运行和原始日志保存在 `.runtime/baseline-pytest.log`，最终汇总在交付验收文档记录 |

用户已明确选择“记录存量问题，继续 4 号开发”。不补写 Core 已缺失的材料，
不删除、不跳过或弱化存量测试来制造全量 PASS。

环境：Windows、Python 3.11.15、Node 24.14.0、pnpm 10.12.4；按现有 lock 安装。
uv 0.12.3 两次因 Windows PE 启动器写入失败退出；使用 `python -m ensurepip`
和 `uv export --frozen --group dev --no-emit-project` 生成的带 hash requirements
经 `pip install --require-hashes` 恢复依赖，未更改 lock 文件。

参考文件 SHA-256：

- `Aegis-Intent-plan.docx`: `bf2a3392ef93dddb6aa03fbe9da8883329148a9a621ec3b6f6dfb2c07109f5fd`
- `Tianjin-2026-competition.pdf`: `74706b335c3f6aa205a75322499f68ca1917e6bcd0e66ff53fd9ffd6bf749ca3`
