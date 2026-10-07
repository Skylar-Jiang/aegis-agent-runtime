# Intent 前端阶段验证

2026-10-02，React/TS/Vite 现有技术栈，无新增依赖。

- 新页面测试首先观察到 `/intent` 无路由、没有标题/控件/错误态；实现后 3/3 PASS。
- 全前端 17 files / 84 tests PASS；ESLint、TypeScript、生产构建 PASS。
- 提交前后端相关回归 27 passed；Pyright 0 errors / 0 warnings，Ruff PASS。
- `scripts/export_intent_demo_schema.py --check` PASS；TS 从同一份 Python schema
  生成，复用现有 Core schema→TS 转换函数，没有手写分叉公共字段。
- 真实 Chromium 页面 `http://127.0.0.1:5174/intent`：选择 retrieval_injection →
  运行 → 后端 BLOCKED/BLOCK/NOT_EXECUTED 显示；事件和 effect 页面有实际数据。
- 1440×1000 桌面和 390×844 移动截图均人工查看；移动 document scrollWidth=390，
  viewport=390，无整页溢出，表格局部滚动；没有 pageerror 或框架错误覆盖层。
- Browser plugin/skill 未提供，使用项目现有 Playwright 实际 Chromium。证据先保存
  `.runtime/member4-evidence/`，最终回归证据和验收文档在最后阶段归档。

页面有加载/空/禁用/错误状态。操作失败保留旧 run 并显示错误，不生成静态成功
结果。版本、Evidence/DecisionResult/CorrectionPlan、文件摘要、Memory、端点记录
和完整 Core timeline 来自同一后端响应，完整 JSON 可展开核对。
