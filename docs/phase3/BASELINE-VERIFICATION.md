# Phase 3 基线验证

验证日期：2026-07-20（Asia/Shanghai）

## 环境与基线

| 项目 | 实际值 |
| --- | --- |
| OS | Microsoft Windows NT 10.0.26200.0 |
| Python | 3.11.9 |
| uv | 0.11.28（通过 `py -3.11 -m uv --version`；`uv` 不在当前 PATH） |
| Node.js | v24.14.0 |
| pnpm | 10.12.4（Corepack） |
| Contract | v0.3 |
| 验证前父提交 | `80d1e14bb7dfb2260baa7b278021b6f858971c5f` |
| Readiness 基线提交 | 此文档所在 readiness 提交；提交后以 `git rev-parse --verify phase3/original-scope-completion` 解析其不可变 SHA |

Git 对象哈希取决于提交本身，不能在同一个待创建提交中预先写入而仍保持正确；因此 SHA 由提交后立即创建的本地 `phase3/original-scope-completion` 引用固定，并在交付报告中记录。

## 实际检查

| 检查 | 结果 |
| --- | --- |
| Phase 3 Contract focused tests | 15 passed |
| Backend pytest with coverage | 491 passed, 7 skipped, 1 warning；coverage 87% |
| Ruff | All checks passed |
| Pyright | 0 errors, 0 warnings, 0 informations |
| Frontend lint | passed |
| Frontend typecheck | passed |
| Frontend tests | 2 passed in 2 files |
| Frontend build | passed（100 modules transformed） |
| `scripts/check.py` | passed；复跑上述 backend/frontend 门禁 |

## 边界与卫生审计

- Contract exports、Pre/Post Mock 独立导入、`request_id` 关联和可变默认值均由 Contract tests 覆盖；两个 Mock 返回调用请求的相同 `request_id`。
- `agent/`、`api/`、`security/` 与前端未直接导入 tool implementation 或调用 Handler；Executor 调用仅在 Runtime flows 中命中。
- Phase 3 当前文档未保留将 PostCheck 归属 C 或将功能分支目标指向 `main` 的说明；旧 `chore/phase3-foundation` 仅作为禁止起点的历史名称出现。
- 运行时 `.runtime/`、`.env` 和常见私钥扩展名均未被 Git 跟踪；本次 worktree 未见待提交的运行时或密钥文件。
- 所有 `README.md` 与 `docs/phase3/*.md` 相对 Markdown 链接在创建本文档后均应可解析；提交前再次执行链接扫描。
- 验证时工作树只含本 readiness 变更和由 `pnpm install`/build 创建但被忽略的本地依赖与构建产物；提交后必须恢复为干净状态。

## 结论

`PARALLEL_READY: YES`

阻塞项：无。真实 Phase 3 业务实现仍待四个成员在 `phase3/original-scope-completion` 上的功能分支完成；该事实是本基线的范围，不是并行开发阻塞项。
