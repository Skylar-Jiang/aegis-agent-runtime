# 本轮真实执行与实验材料

2026-09-23 Windows / Python 3.11 / Node 24 / OpenSSL SM2、SM3；实际 Vite 页面、FastAPI HTTP、文件、SQLite、Runtime checkpoint/Effect。

- `result.json`：浏览器18项检查，0未捕获异常；同任务两次审批/执行，事件11→20，新旧包分别按各自锚点核验。
- `bundle.json` / `bundle-after-second.json`、`public-keys.json`、`checkpoints/`：可离线核验的真实样本；`offline-verification.json` 是复制到仓库后仅使用公开材料的CLI复验结果。
- `http-controls.json`、`http-trace.json`：11类真实HTTP控制实验及请求/结果记录，包含跨工具/Skill的任务总额度、撤权、取消和变参等拒绝。
- `ablations.json` / `.csv`、`ablation-public-keys.json`：13案例机制对照，含原始签名证据；不同于浏览器端到端验收。
- `benchmark.json` / `.csv`：100、1000、10000条事件及密码操作、并发核验采样；`boundary.json` 是固定真实签名检查点后的64MiB三点复验。
- PNG均为实际界面截图。`05-tampered.png` 中的核验失败是故意篡改副本后的预期结果。

性能JSON保留运行时提交和dirty标记，代表当时工作区，不改写成事后提交测试。执行修复提交为 `6daf469`、`b04e578`、`9e97d24`，前端增量修复为 `435ad74`。最终后端1190通过/8跳过，前端70通过；详细命令和边界见 [验证记录](../../status/CORE_HARDENING_VALIDATION.md)。

公钥和锚点用于此受控实验的复现，不自动构成生产可信根。私钥、运行数据库、64MiB大文件不提交；脚本可重建独立实验。部分原始记录含本机工作区路径，只是实验来源标识。

`memory-boundary.json` / `memory.csv`：独立小规模内存补测，879次采样。含基准客户端、边界大对象构造及服务端的父Python进程生命周期RSS峰值1,519,128,576字节（约1.415GiB），不是纯服务端常驻量；不含OpenSSL/CLI子进程。小包并发阶段采样峰值约101.53MiB。三点HTTP状态200/200/413，CLI退出码0/0/1，超限未发布新锚。
