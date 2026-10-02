# 后端 fixture 与模拟端点阶段验证

2026-10-02，`feature/member4-telecom-intent-demo`。

- 新 API 测试首先观察到 404，schema 测试首先观察到缺生成文件；实现后通过。
- 新增 22 项测试通过（API 13、schema 4、执行边界 5）。
- 新增与相关 Core/Memory/路径/健康 API 回归：150 passed，3 skipped。
- 全仓 Ruff：PASS；Pyright：0 errors，0 warnings。
- 前端未改变，提交前仍重跑：81 tests、TS、构建 PASS。
- fixture mock 三攻击暂停点均禁止原请求执行；Core 权限 ALLOW；配置调用和外发
  调用都为 0，配置字节未变，EffectCheck 为 UNCHANGED。
- 正常报告实际读取源文件；合法变更 v2 确认后只有一次本地模拟外发；多轮
  Memory 保存不可信候选并在终止时通过 Core 恢复可信快照；纠偏复读可信 KB
  后重新检查，原攻击请求不可重试。
- 配置端点正向单元测试确认确实改变合成文件、计数为 1、记录 target/payload
  digest/effect；因此零计数不是未实现端点造成的假阳性。
- 未新增依赖，未改 Core 检测、权限、公共契约或策略模型。唯一接入改动是
  main.py 注册 opt-in demo router。

起点全量 pytest：1228 passed，8 skipped，10 failed（195.43s）。存量失败清单：

1. `test_core_crypto_interfaces.py` 的五个 published schema 检查：缺
   SignedEnvelope、AuditCheckpoint、CheckpointRecord、AuditBundle、VerificationResult schema。
2. `test_core_event_artifacts.py::test_generated_openapi_types_and_fixtures_are_current`：
   缺 `docs/interfaces/schemas/p2-events.openapi.json`。
3. `test_core_scan_regressions.py::test_core_experiments_returns_recorded_data_and_content_hash`：
   缺已记录的 Core 实验证据（与本次 telecom fixture 无关）。
4. `test_sqlite_event_store.py::test_process_writers_do_not_lose_events`：本机一次并发
   子进程返回 Core event storage unavailable；不归因于尚未开始的本次代码。
5. `test_check_script.py::test_pnpm_version_docs_require_frontend_working_directory`：
   原 README 缺包管理器版本说明。
6. `test_check_script.py::test_authoritative_project_files_have_no_stale_node_22_requirement`：
   缺 `docs/02-tech-stack.md`。

旧 lock 的 Starlette/httpx TestClient 弃用提示以及 Pyright 新版提示均保留，未更换
依赖以隐藏警告。按用户选择记录存量问题后继续成员 4 工作。
