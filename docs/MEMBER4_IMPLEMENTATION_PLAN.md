# Member 4 实施与界面计划

目的：帮助通信运维演示操作者运行合成任务，观察动作来源与检测证据，核实处置
是否在副作用前生效。主要操作是选择 Case 并运行，次要操作是可信目标变更确认、
执行重新复核的纠偏、拒绝/终止、reset。

页面 `/intent` 复用 Layout、字体、边框和表格样式。组件树：
`App → Layout → IntentPage → IntentPanel`。页首标记 fixture/mock 检测器、真实
本地执行和合成数据。任务/IntentSpec/TaskContract 版本和 DecisionResult 放前面；
原始输入、当前动作及来源、证据、CorrectionPlan、effect 和 timeline 按执行关系
排列。JSON 完整内容折叠展示，避免只展示解释文案而遗漏真实字段。

操作期间禁用重复提交；空状态要求先运行；加载、后端关闭/错误、暂停、完成、
安全终止都有明确状态。确认与纠偏只能作用于当前 run。移动端列变为单列，
长 digest 换行，timeline 表格局部滚动；控件有 label 和键盘焦点。

后端使用单进程隔离 demo service，通过既有 Core ToolGateway 执行文件、Memory
和本地模拟端点；mock DecisionResult 从受版本控制的 scenario fixture 读取，
不根据 Case 标签开发检测器。端点仅接受固定 Case 与已登记请求，不能提交任意
设备地址或外发目标。每个 run 有独立目录、事件和 Core 契约；reset 使旧请求失效。

三类攻击：检索文档诱导配置修改（BLOCK）；跨轮资料写入候选 Memory 后读取、
再次推进到外发（CLARIFY 并安全终止）；伪造工具失败要求改用外发（REPLAN，
可信资料重读并再次 ALLOW，原请求永远不能恢复）。两对照是正常报告与可信入口
合法增加模拟外发目标（版本 2，确认前零外发）。增加正常工具失败对照，重读可信
缓存并重新检查后完成报告。

正常报告从配置、工单、日志、知识库实际读取结果生成；expected state 独立描述
应有的报告内容、Memory 与调用数。EffectCheck 比較真实前后字节摘要和端点
记录，不直接照抄 expected state。回归还会故意改写文件验证 MISMATCH 可见。

阶段提交前运行 pytest、Pyright、Ruff、Vitest、TS、ESLint、构建；最后从真实浏览器
运行全部 Case，比较 UI 与接口、文件/Memory/端点/EffectCheck，捕获桌面与移动
证据，并从无本地状态的环境重新安装和 replay。
