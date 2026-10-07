# Runtime Base 交付前验证记录

验证日期：2026-09-11。

## 已通过

| Gate | 结果 |
| --- | --- |
| Ruff（backend/src、tests、Base 验证脚本） | PASS |
| Pyright（完整 backend 项目） | 0 errors / 0 warnings |
| Agent、Task API、TaskGraph、迁移核心回归 | 38 passed |
| Windows 分发/迁移补充回归 | 10 passed |
| B1–B6 确定性主链实验 | 6/6 PASS |
| Frontend ESLint | PASS |
| Frontend TypeScript typecheck | PASS |
| Frontend Vitest | 28 passed |
| Frontend production build | PASS |

B1–B6 的逐项机器可读结果见 `runtime-base-validation-reference.json`。

## 没有作为 Base Gate 的旧测试

原始交付包包含一批“固定正式实验重建”测试，但压缩包本身没有附带它们硬编码引用的 raw JSONL 文件；另一些材料生成测试要求当前目录必须是带 commit 的 Git 仓库，而交付压缩包没有 `.git`。因此直接运行旧版 927 项全集会在这些历史材料测试上失败或长时间重建。

这不是本次主链实现的运行时失败。Runtime Base 的一键脚本只运行可从源码和临时目录自洽重建的 B1–B6，并把旧竞赛证据材料与当前工程 Gate 分开。若未来要恢复旧论文/竞赛全量复现，需要另行补回对应 raw 数据和 Git provenance。

## 已知非阻断警告

后端 TestClient 会提示 Starlette 正在弃用当前 httpx 兼容入口。它不影响运行结果，但依赖升级阶段应根据新版 Starlette 测试客户端迁移说明处理。
