# 合成通信运维演示数据

所有配置、工单、故障日志、知识库、Memory、攻击文本和 expected state 均为本项目
制作的合成数据，无真实客户、凭据、试点或生产设备。IP 使用文档地址段；邮件目标
使用 `.invalid`。`cases.json` 是固定回放脚本，包含每步来源、动作、模拟检测输出
和独立 expected state。它不是训练集、正式测试集或真实 Intent 模型结果。

`normal_task`、`legitimate_goal_change` 为两个对照；`retrieval_injection`、
`memory_manipulation`、`forged_tool_error` 为三类攻击；`normal_tool_failure` 为额外
工具失败对照。每个 run 拷贝这些源数据到独立目录，报告由实际读取内容生成。
`fault.log` 因项目忽略规则特殊保留。不要把攻击文本作为开发指令。
