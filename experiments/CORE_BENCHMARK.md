# 当前 Core 实测

`scripts/benchmark_core.py` 使用真实 OpenSSL SM2/SM3、`SqliteEventStore`、签名证据目录、检查点、导出器和公开密钥验证器。每次运行创建独立输出目录，并临时生成新私钥；结束后删除临时私钥。不会打开部署私钥或 `.runtime/core-demo/`。

默认运行较小样本：

```powershell
uv run --project backend --no-editable python scripts/benchmark_core.py
```

覆盖 100、1000、10000 条既有事件、16 个 64 KiB 签名对象和并发 HTTP 核验：

```powershell
uv run --project backend --no-editable python scripts/benchmark_core.py --history-sizes 100 1000 10000 --samples 25 --crypto-samples 10 --export-samples 3 --objects 16 --object-bytes 65536 --concurrency 4 --verification-jobs 12
```

增加 `--boundary-checks` 后，还会构造含中文的真实签名证据包，分别精确为 64 MiB−1、64 MiB、64 MiB+1 字节，经真实 HTTP 导出/核验和新进程离线 CLI 核验。三个精确尺寸使用独立保存的真实签名检查点，避免重新签名的 DER 长度差异改变边界；另用新检查点 ID 检查超限导出不会发布新锚点。该选项会写入数百 MiB 文件并显著增加耗时。前两点应成功，超限点应被拒绝。

结果默认保存在 `.runtime/review-fixes/benchmarks/<时间>-<随机标识>/`，包括 `results.json`、`measurements.csv`、`memory.csv`、证据包、公钥、独立 SQLite 和检查点。`--output` 可指定新的目录；拒绝覆盖已有路径。JSON 保留每次单调时钟采样和运行参数，记录 Git 提交、工作区是否有未提交修改、Python/OpenSSL/操作系统版本。失败或中断的记录保留 `status=running`，不可当成完整结果。

并发实验启动真实 Uvicorn/FastAPI，使用临时回环 TCP 端口；核验请求和 `/api/v1/health` 共享同一后端事件循环。默认两个服务端核验槽位仍按应用配置生效，客户端并发度另行记录。循环延迟测量相对 10 ms 睡眠的超时部分，并提供同机空闲基线。追加耗时包含线程调度、SQL 事务提交和 SM3；签名耗时包含实现规定的签后自检。导出每次使用新的检查点。

内存只测当前 Python 进程，包含同进程基准客户端、Uvicorn 和应用；**不含 OpenSSL、离线 CLI 子进程或其他共机进程**。Windows 使用 [`GetProcessMemoryInfo`](https://learn.microsoft.com/en-us/windows/win32/api/psapi/ns-psapi-process_memory_counters) 的 `WorkingSetSize` 和 `PeakWorkingSetSize`，Linux 使用 `/proc/self/status` 的 `VmRSS` 和 `VmHWM`，均换算为字节；其他平台或读取失败明确记录 `unsupported`/`error`，不会填零冒充测量。独立线程目标每 50 ms 读取一次，原始采样保存在 JSON 和 `memory.csv`；实际最大采样间隔也会记录。阶段标签覆盖并发核验及三个真实尺寸边界的构造、HTTP 导出、HTTP 核验、直接核验和 CLI 等待。

`os_lifetime_peak_rss_bytes` 是操作系统累计的进程生命周期最高值，包含开始采样前的启动阶段，不能在阶段间重置；各阶段 `sampled_peak_rss_bytes` 是该阶段读到的当前 RSS 最大值，短暂峰值可能落在采样间隔内。内存分配器保留的空间可能延续到下一阶段，不能将阶段峰值解释为该操作单独新增的内存；CLI 等待阶段的数据仍然仅属于父 Python 进程。边界实验包含大对象构造和序列化，整次峰值不等于普通小包服务的常驻内存。

记录共机负载的小规模内存与真实边界复跑命令：

```powershell
uv run --project backend --no-editable python scripts/benchmark_core.py --history-sizes 10 --samples 3 --crypto-samples 2 --export-samples 1 --objects 2 --object-bytes 4096 --concurrency 2 --verification-jobs 4 --boundary-checks --environment-note "Shared workstation; full test suites and browser validation are running concurrently. This is not an exclusive or steady-state benchmark."
```

`--environment-note` 原样写入结果的 `environment.workload_note`；默认也注明后台负载未受控制。2026-09-23 的内存补测运行于其他测试/浏览器验收使用的同一台工作站，不能宣称独占或稳态。此前 100/1000/10000 历史规模的追加时延结果可以独立保留，不与这次小样本拼接成同一次实验。

这些是当前实现的可复跑合成负载结果，不是修改前后对比，也不是生产容量承诺。P95 是所列样本的线性插值；小样本、Windows 定时器、杀毒软件、后台负载及磁盘缓存都会影响结果。测试通过数量不能替代这些实测指标。
