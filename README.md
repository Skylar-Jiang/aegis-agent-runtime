# Aegis-Intent

## AI Agent 意图偏移检测与安全防护运行时

Aegis-Intent 是基于 Aegis Runtime 架构扩展的
面向 AI Agent 的安全增强运行时系统。

针对 Agent 在长链任务执行过程中可能出现的：
- 用户意图漂移
- 工具调用越权
- 上下文污染
- 恶意指令注入

等安全问题，提供实时检测、风险评估和动态防护能力。

---

## Core Capabilities

基于 Aegis Runtime 基础能力，Aegis-Intent 扩展实现：

- **Trusted Task Contract**
  
  建立任务可信边界，约束 Agent 执行目标。

- **Intent Drift Detection**

  对 Agent 执行过程中的任务意图变化进行检测。

- **Dynamic Defense**

  根据风险等级动态调整执行策略。

- **Security Audit**

  提供完整行为记录和安全审计能力。

- **Scenario Simulation**

  支持多种攻击场景和防护效果验证。


---

## Architecture

Aegis-Intent consists of:

- Aegis Runtime Core
- Intent Analysis Engine
- Security Policy Engine
- Audit & Verification Layer
- Visualization Dashboard


---

## Development

```bash
python scripts/start_core.py

Frontend:
cd frontend
corepack pnpm dev