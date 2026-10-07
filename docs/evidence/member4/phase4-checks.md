# 自动化验收与交付阶段验证

2026-10-03（Asia/Shanghai），分支 `feature/member4-telecom-intent-demo`。

- 最新成员 4 后端回归：22 passed。独立审查提出的正常终止事件来源、重复篡改
  核验和外部恢复文件的摘要链边界，均先用失败测试复现，再修正，通过回归。
- 全前端：17 files / 84 tests passed；TypeScript、ESLint、生产构建 PASS。
- 全仓 Ruff PASS；Pyright 0 errors / 0 warnings；schema / TS 生成一致性 PASS。
- 独立服务 `8045/5175` 的 Playwright：7 passed（6 Case + 实际文件篡改），
  最新运行 1.4m。逐行检查 UI timeline、版本、DecisionResult、执行状态；独立
  读取磁盘文件 hash、Memory、端点 payload digest、调用次数和 EffectCheck 链。
- 六个 API Case replay PASS，reset PASS；暂停的 BLOCK/CLARIFY/REPLAN 请求
  execute=409 且没有产生相应执行事件/配置修改/模拟外发。合法变更确认后恰好
  一次模拟发送、sent=false；纠偏只执行重新复核的安全动作。
- 浏览器错误数组均为空；1440×1000 桌面与 390×844 移动截图已查看，移动页面
  scrollWidth=390。实际响应和截图归档于 browser/，不是静态 UI fixture。
- 最终全量 pytest：**1251 passed、8 skipped、9 failed，249.06s**。9 项失败均
  出现在基线，没有新增失败；基线一次失败的 SQLite 多进程测试本轮通过，不将
  此波动声称为本分支修复。失败标识逐项见 backend-final-summary.json。
- 9 个持续失败：5 个缺少 published crypto schema、1 个缺少 p2-events OpenAPI
  schema、1 个缺少已记录 Core 实验材料、2 个原包管理器/技术栈文档检查。
  用户授权记录这些存量问题后继续本范围，未改动或削弱旧测试。

干净环境结果见 clean-environment-summary.json：候选 index 导出新目录，不带
.env、依赖或运行状态；Python 原生 venv + uv frozen/锁定 hash pip fallback；
后端 22、前端 84、浏览器 7 及前端类型检查/构建通过。摘要同时核对干净目录
与交付代码/fixture/test/script 的规范化换行后内容一致，避免用旧版本结果作证。

安装验证暴露 uv 在本机 Windows PE trampoline 更新阶段失败，既可能发生于
venv 创建，也可能发生于包 launcher 创建。脚本已先用 Python 原生 venv，再
采用锁定导出的 pip fallback。首次验证受本机额外 NVIDIA pip 源 TLS 重试拖慢；
最终验证进程设置 `PIP_CONFIG_FILE=NUL`、官方 PyPI、清空额外源；未修改用户的
全局 pip 配置。pnpm 现有 esbuild build-script 提示保留，实际构建/浏览器通过；
Starlette/httpx 弃用、Pyright 更新及终端颜色提示保留，不据此修改依赖。

独立只读 code review 无 Critical；其事件和摘要链意见已落实并复查通过。
提交前检查 diff whitespace、分支 ancestry、只推送 feature 分支。没有创建或
合并 PR，未修改 Core 检测器、策略、权限或公共模型。
